"""SQLite store backing the substrate.

Thin wrapper over sqlite3. Holds the connection, applies the schema, and
provides primitive CRUD on skills, episodes, frames, states, decisions.

Design notes:
- JSON columns are stored as TEXT and (de)serialized at the boundary.
- All public methods are synchronous and connection-affine; callers that
  want concurrency wrap their own locks or use a separate connection.
- The schema lives in schema.sql; this module loads and applies it.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now() -> float:
    return time.time()


def _uuid() -> str:
    return str(uuid.uuid4())


def _jsonify(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value)


def _unjsonify(value: Optional[str]) -> Any:
    if value is None:
        return None
    return json.loads(value)


class Store:
    """Substrate-backing SQLite store.

    Opens (or creates) the database file at `db_path` and applies the schema
    on first run. Idempotent — calling Store(...) on an existing DB is safe.

    Example:
        store = Store("~/hebbot-data/hebbot.db")
        store.register_skill(name="get_state", body_type="classical",
                             body={"function": "get_state_impl"})
        ep_id = store.start_episode("get_state")
        store.end_episode(ep_id, success=True, outcome={"q": [...]})
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._apply_schema()

    def _apply_schema(self) -> None:
        with open(SCHEMA_PATH) as f:
            self.conn.executescript(f.read())

    def close(self) -> None:
        self.conn.close()

    # -----------------------------------------------------------------------
    # Skills
    # -----------------------------------------------------------------------

    def register_skill(
        self,
        *,
        name: str,
        body_type: str,
        body: dict,
        params_schema: Optional[dict] = None,
        expectations: Optional[dict] = None,
    ) -> None:
        """Insert or update a skill record.

        On conflict (skill exists), bumps the version and updates the body.
        """
        now = _now()
        existing = self.get_skill(name)
        version = (existing["version"] + 1) if existing else 1
        self.conn.execute(
            """
            INSERT INTO skills (name, params_schema, expectations, body_type, body,
                                version, created_at, updated_at, statistics)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                params_schema = excluded.params_schema,
                expectations = excluded.expectations,
                body_type = excluded.body_type,
                body = excluded.body,
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            (
                name,
                _jsonify(params_schema),
                _jsonify(expectations),
                body_type,
                _jsonify(body),
                version,
                existing["created_at"] if existing else now,
                now,
                None if not existing else _jsonify(existing.get("statistics")),
            ),
        )

    def get_skill(self, name: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM skills WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._skill_row_to_dict(row)

    def list_skills(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skills ORDER BY name"
        ).fetchall()
        return [self._skill_row_to_dict(r) for r in rows]

    @staticmethod
    def _skill_row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in ("params_schema", "expectations", "body", "statistics"):
            d[k] = _unjsonify(d[k])
        return d

    # -----------------------------------------------------------------------
    # Episodes
    # -----------------------------------------------------------------------

    def start_episode(
        self,
        skill_name: str,
        *,
        parent_id: Optional[str] = None,
        params: Optional[dict] = None,
        deliberate_ctx: Optional[dict] = None,
        body_type_used: Optional[str] = None,
    ) -> str:
        episode_id = _uuid()
        self.conn.execute(
            """
            INSERT INTO episodes (
                id, skill_name, parent_id, started_at, params,
                deliberate_ctx, body_type_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                skill_name,
                parent_id,
                _now(),
                _jsonify(params),
                _jsonify(deliberate_ctx),
                body_type_used,
            ),
        )
        return episode_id

    def end_episode(
        self,
        episode_id: str,
        *,
        success: bool,
        outcome: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE episodes
            SET ended_at = ?, success = ?, outcome = ?
            WHERE id = ?
            """,
            (_now(), int(success), _jsonify(outcome), episode_id),
        )

    def get_episode(self, episode_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("params", "deliberate_ctx", "outcome"):
            d[k] = _unjsonify(d[k])
        return d

    def recent_episodes(
        self, skill_name: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        if skill_name:
            rows = self.conn.execute(
                """SELECT * FROM episodes WHERE skill_name = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (skill_name, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM episodes ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("params", "deliberate_ctx", "outcome"):
                d[k] = _unjsonify(d[k])
            out.append(d)
        return out

    # -----------------------------------------------------------------------
    # States (time-series during an episode)
    # -----------------------------------------------------------------------

    def record_state(
        self,
        episode_id: str,
        *,
        t: float,
        joints: Optional[list[float]] = None,
        ee_pose: Optional[list[float]] = None,
        force: Optional[list[float]] = None,
        gripper_width: Optional[float] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO states (episode_id, t, joints, ee_pose, force, gripper_width)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                t,
                _jsonify(joints),
                _jsonify(ee_pose),
                _jsonify(force),
                gripper_width,
            ),
        )

    # -----------------------------------------------------------------------
    # Frames (image references)
    # -----------------------------------------------------------------------

    def record_frame(
        self,
        episode_id: str,
        *,
        camera: str,
        t: float,
        file_path: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> str:
        frame_id = _uuid()
        self.conn.execute(
            """
            INSERT INTO frames (id, episode_id, camera, t, file_path, width, height)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (frame_id, episode_id, camera, t, file_path, width, height),
        )
        return frame_id

    # -----------------------------------------------------------------------
    # Decisions
    # -----------------------------------------------------------------------

    def log_decision(
        self,
        *,
        skill_called: str,
        decision: str,
        episode_id: Optional[str] = None,
        params: Optional[dict] = None,
        relational_state: Optional[dict] = None,
        candidates: Optional[list[dict]] = None,
        reasoning: Optional[str] = None,
        threshold_values: Optional[dict] = None,
        outcome: Optional[str] = None,
    ) -> str:
        decision_id = _uuid()
        self.conn.execute(
            """
            INSERT INTO decisions (
                id, episode_id, timestamp, skill_called, params,
                relational_state, candidates, decision, reasoning,
                threshold_values, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                episode_id,
                _now(),
                skill_called,
                _jsonify(params),
                _jsonify(relational_state),
                _jsonify(candidates),
                decision,
                reasoning,
                _jsonify(threshold_values),
                outcome,
            ),
        )
        return decision_id
