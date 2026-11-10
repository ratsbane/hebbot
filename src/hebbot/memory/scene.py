"""World-frame scene memory: a belief store, not a map.

A fact about code stays true until someone edits the file, and the edit is observable. A
fact about the table decays silently, at a rate set by what kind of thing it is about. So
where a coding agent's memory can be append-mostly text, this store records *beliefs*:
every observation carries its sensor, an error bar, a timestamp and the scene epoch it was
taken in, and reading one back tells you how much of the world has happened since.

Three tables. `observations` are curated conclusions about objects ("blue is at (x, y)
within 6 mm, camera+touch"), not raw probes; raw probes stay in episode rollouts.
`actions` are things the arm did that could have changed the world, and each one advances
the scene epoch — the lesson of the 2026-11-09 stale-cache defect, where a verification
read a pre-placement value, generalised: a percept from an older epoch is not deleted, it
is demoted to "probably still there". `objects` hold slow, learned properties ("taped
down", "55 x 28 mm", "turns when probed off-centre", #160) that do not decay with the
scene.

An earlier scene store exists (`primitives.scene_sync`, July 2026): image-frame at one
observe pose, camera-only, with good predict-then-look change detection. It predates the
camera map and all touch sensing, so it could not hold world coordinates or fuse sensors.
This store is world-frame and multi-sensor; porting scene_sync's association and
moved/appeared/disappeared logic onto it is future work, and until then the two coexist.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects (
    name TEXT PRIMARY KEY,
    properties TEXT NOT NULL DEFAULT '{}',
    updated_t REAL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    t REAL NOT NULL,
    object TEXT NOT NULL,
    sensor TEXT NOT NULL,
    x REAL, y REAL,
    top_z REAL,
    yaw_deg REAL,
    precision_m REAL NOT NULL,
    epoch INTEGER NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_obs_object ON observations (object, id DESC);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    t REAL NOT NULL,
    kind TEXT NOT NULL,
    object TEXT,
    x REAL, y REAL,
    data TEXT NOT NULL DEFAULT '{}',
    epoch_after INTEGER NOT NULL
);
"""


class SceneMemory:
    """Beliefs about objects on the table, persisted in SQLite beside hebbot.db."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def epoch(self) -> int:
        row = self._db.execute("SELECT value FROM meta WHERE key='scene_epoch'").fetchone()
        return int(row["value"]) if row else 0

    def observe(self, obj: str, *, sensor: str, precision_m: float,
                xy: Optional[list] = None, top_z: Optional[float] = None,
                yaw_deg: Optional[float] = None, data: Optional[dict] = None,
                t: Optional[float] = None) -> int:
        """Record a conclusion about an object. Observing never bumps the epoch."""
        t = time.time() if t is None else float(t)
        x, y = (float(xy[0]), float(xy[1])) if xy is not None else (None, None)
        with self._db:
            self._touch_object(obj, t)
            self._db.execute("UPDATE objects SET updated_t=? WHERE name=?", (t, obj))
            cur = self._db.execute(
                "INSERT INTO observations (t, object, sensor, x, y, top_z, yaw_deg,"
                " precision_m, epoch, data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t, obj, sensor, x, y,
                 None if top_z is None else float(top_z),
                 None if yaw_deg is None else float(yaw_deg),
                 float(precision_m), self.epoch(), json.dumps(data or {})))
        return int(cur.lastrowid)

    def act(self, kind: str, *, obj: Optional[str] = None, xy: Optional[list] = None,
            data: Optional[dict] = None, t: Optional[float] = None) -> int:
        """Record a world-changing action and advance the scene epoch. Returns the new epoch."""
        t = time.time() if t is None else float(t)
        x, y = (float(xy[0]), float(xy[1])) if xy is not None else (None, None)
        with self._db:
            epoch = self.epoch() + 1
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('scene_epoch', ?)",
                (str(epoch),))
            self._db.execute(
                "INSERT INTO actions (t, kind, object, x, y, data, epoch_after)"
                " VALUES (?,?,?,?,?,?,?)",
                (t, kind, obj, x, y, json.dumps(data or {}), epoch))
        return epoch

    def note(self, obj: str, **properties: Any) -> dict:
        """Merge slow, learned properties into an object; a None value deletes its key."""
        with self._db:
            current = self.properties(obj)
            for key, value in properties.items():
                if value is None:
                    current.pop(key, None)
                else:
                    current[key] = value
            self._touch_object(obj, time.time())
            self._db.execute("UPDATE objects SET properties=?, updated_t=? WHERE name=?",
                             (json.dumps(current), time.time(), obj))
        return current

    def properties(self, obj: str) -> dict:
        row = self._db.execute("SELECT properties FROM objects WHERE name=?", (obj,)).fetchone()
        return json.loads(row["properties"]) if row else {}

    def belief(self, obj: str, *, near_m: float = 0.12,
               now: Optional[float] = None) -> Optional[dict]:
        """The latest word on each aspect of an object, with its staleness laid bare.

        Position, top height and yaw each come from the newest observation that has them,
        so a touch top can refine a camera position without erasing it. Staleness is
        counted from the position observation: its age, how many epochs the scene has
        advanced since, and which recorded actions since then were on or near this object.
        """
        rows = self._db.execute(
            "SELECT * FROM observations WHERE object=? ORDER BY id DESC LIMIT 100",
            (obj,)).fetchall()
        props = self.properties(obj)
        if not rows and not props:
            return None
        now = time.time() if now is None else float(now)
        belief: dict = {"name": obj, "properties": props, "xy": None, "top_z": None,
                        "yaw_deg": None}
        pose = next((r for r in rows if r["x"] is not None), None)
        top = next((r for r in rows if r["top_z"] is not None), None)
        yaw = next((r for r in rows if r["yaw_deg"] is not None), None)
        if top is not None:
            belief["top_z"] = top["top_z"]
        if yaw is not None:
            belief["yaw_deg"] = yaw["yaw_deg"]
        if pose is not None:
            belief.update(xy=[pose["x"], pose["y"]], precision_m=pose["precision_m"],
                          sensor=pose["sensor"], t=pose["t"],
                          age_s=round(now - pose["t"], 1),
                          epochs_behind=self.epoch() - pose["epoch"])
            since = self._db.execute("SELECT * FROM actions WHERE t>? ORDER BY id",
                                     (pose["t"],)).fetchall()
            belief["disturbances"] = [
                {"kind": a["kind"], "t": a["t"]} for a in since
                if a["object"] == obj or (
                    a["x"] is not None
                    and math.hypot(a["x"] - pose["x"], a["y"] - pose["y"]) <= near_m)]
        return belief

    def beliefs(self, *, now: Optional[float] = None) -> list[dict]:
        names = [r["name"] for r in self._db.execute(
            "SELECT name FROM objects ORDER BY updated_t DESC")]
        return [b for b in (self.belief(n, now=now) for n in names) if b is not None]

    def summary(self, *, now: Optional[float] = None) -> str:
        """One line per object, for an orchestrator that reads prose better than rows."""
        now = time.time() if now is None else float(now)
        lines = [f"scene epoch {self.epoch()}"]
        for b in self.beliefs(now=now):
            parts = []
            if b["xy"] is not None:
                parts.append(f"at ({b['xy'][0]:.3f}, {b['xy'][1]:.3f}) "
                             f"±{b['precision_m'] * 1000:.0f} mm "
                             f"({b['sensor']}, {_age(b['age_s'])})")
                if b["epochs_behind"]:
                    kinds = ", ".join(d["kind"] for d in b["disturbances"]) or "none near it"
                    parts.append(f"{b['epochs_behind']} action(s) since: {kinds}")
            else:
                parts.append("position unknown")
            if b["top_z"] is not None:
                parts.append(f"top {b['top_z'] * 1000:.1f} mm")
            if b["yaw_deg"] is not None:
                parts.append(f"axis {b['yaw_deg']:.0f} deg")
            parts += [f"{k}={v}" for k, v in b["properties"].items()]
            lines.append(f"{b['name']}: " + "; ".join(parts))
        if len(lines) == 1:
            lines.append("no objects known")
        return "\n".join(lines)

    def _touch_object(self, obj: str, t: float) -> None:
        self._db.execute("INSERT OR IGNORE INTO objects (name, properties, updated_t)"
                         " VALUES (?, '{}', ?)", (obj, t))


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 129600:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds / 86400:.1f} d ago"


def load_scene_memory(store) -> Optional[SceneMemory]:
    """The scene store beside hebbot.db, or None when there is no data directory."""
    if store is None or not getattr(store, "db_path", None):
        return None
    try:
        return SceneMemory(Path(store.db_path).parent / "scene.sqlite")
    except Exception:
        return None


def scene_recall(
    *,
    object: Optional[str] = None,
    _store: Optional[Any] = None,
    _context: Optional[Any] = None,
) -> dict:
    """What the scene memory believes, as prose plus the rows behind it."""
    memory = load_scene_memory(_store)
    if memory is None:
        return {"_source": "mock", "note": "no data directory, so no scene memory"}
    try:
        if object is not None:
            found = memory.belief(object)
            return {"_source": "live", "belief": found,
                    "note": None if found else f"nothing known about {object!r}"}
        return {"_source": "live", "summary": memory.summary(), "beliefs": memory.beliefs()}
    finally:
        memory.close()


def scene_note(
    *,
    object: str,
    properties: dict,
    _store: Optional[Any] = None,
    _context: Optional[Any] = None,
) -> dict:
    """Record what someone knows and the sensors cannot see: "green is taped down"."""
    memory = load_scene_memory(_store)
    if memory is None:
        return {"_source": "mock", "note": "no data directory, so no scene memory"}
    try:
        merged = memory.note(object, **properties)
        return {"_source": "live", "object": object, "properties": merged}
    finally:
        memory.close()


PRIMITIVE_SPECS = [
    {
        "name": "scene_recall",
        "function_spec": "hebbot.memory.scene:scene_recall",
        "params_schema": {
            "type": "object",
            "properties": {
                "object": {"type": "string",
                           "description": "one object's belief; omit for the whole scene"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "scene_note",
        "function_spec": "hebbot.memory.scene:scene_note",
        "params_schema": {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "properties": {"type": "object",
                               "description": "merged into the object; null deletes a key"},
            },
            "required": ["object", "properties"],
            "additionalProperties": False,
        },
    },
]


def register_all(substrate) -> None:
    for spec in PRIMITIVE_SPECS:
        substrate.register(
            spec["name"],
            function_spec=spec["function_spec"],
            params_schema=spec.get("params_schema"),
        )
