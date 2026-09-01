"""Self-calibration by touch: the arm works out where its own hand is.

Nobody calibrates a toddler. It reaches, misses, feels where the thing actually was, and
its next reach is better. This module gives the arm the same loop, using the only two
senses it has: it can *see* where a block is, and it can *feel* where its fingers are.

On 2026-09-01 the image-space servo converged to 0.8 mm while the grasp still closed on
air, and eight touch probes at every position implied by `_vg_grasp_pixel`, by the stored
2026-06-09 hand-eye file, and by the camera axis itself all hit bare table. Kinematics were
verified sound the same day (`probe_table` 0.0164 against June's 0.0165). So the fault is
the map between the camera and the hand — and rather than ask a human to re-run a
calibration procedure, the arm can measure it: find a seen block by feel, and the
disagreement between where it looked and where it turned out to be IS the correction.

Bench time is the cheap resource here, so the search is deliberately brute: a blind spiral
of dozens of probes is fine if it removes a calibration constant nobody trusts.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import numpy as np

from hebbot.perception.target import locate
from hebbot.skills.primitives import (
    _extract, _select_camera, move_to_pose, probe_table,
)


# Effective focal length MEASURED from robot motion on 2026-09-01, not taken from the
# camera's intrinsics. Two 40 mm test moves gave 683 and 679 px/m at a working distance of
# 0.5193 m, i.e. 681 * 0.5193 = 353.6 px — 11% below the 394.5 px the intrinsics report.
# Using the intrinsic value makes every image-space correction overshoot by that much.
EFFECTIVE_FX = 353.6
CX, CY = 323.6427001953125, 242.88636779785156

# Image axes against robot axes, also measured: EE +x moves the target image -v, EE +y
# moves it -u, with 3-4% cross-coupling. So world +x <-> image -v and world +y <-> image -u.
PROBE_OPEN_WIDTH = 0.008          # fingers nearly shut: the pair acts as a point probe
PROBE_Z_FLOOR = 0.012


def _spiral(step: float, radius: float) -> list[tuple[float, float]]:
    """Square spiral outward from the seed, so the nearest guesses are tried first."""
    points = [(0.0, 0.0)]
    ring = 1
    while ring * step <= radius:
        r = ring * step
        coords = [i * step for i in range(-ring, ring + 1)]
        edge = ([(x, -r) for x in coords] + [(r, y) for y in coords]
                + [(x, r) for x in reversed(coords)]
                + [(-r, y) for y in reversed(coords)])
        for point in edge:
            if point not in points:
                points.append(point)
        ring += 1
    return points


def _observe(color, camera, _context) -> dict:
    cameras = _extract(_context, "cameras")
    if not cameras:
        return {"found": False, "reason": "no camera"}
    cam = _select_camera(cameras, camera)
    capture = cam.capture(with_depth=True)
    rgb = capture["color"][:, :, ::-1]
    return locate(rgb, color, depth=capture.get("depth"))


def image_to_world_offset(u: float, v: float, depth_m: float) -> tuple[float, float]:
    """Where a target sits relative to the camera axis, using the measured scale."""
    metres_per_px = float(depth_m) / EFFECTIVE_FX
    # Signs verified against the servo that worked on 2026-09-01: bringing a block at
    # dv=+120 to the image centre required moving the EE +0.178 m in x, and du=-74
    # required -0.116 m in y. Both map positively.
    return (v - CY) * metres_per_px, (u - CX) * metres_per_px


def _correction_path(store) -> str:
    from pathlib import Path
    if store is not None and getattr(store, "db_path", None):
        return str(Path(store.db_path).parent / "touch_correction.json")
    return "/tmp/hebbot_touch_correction.json"


def load_correction(store) -> dict:
    """The running camera-to-hand correction the arm has taught itself so far."""
    import json
    try:
        with open(_correction_path(store)) as handle:
            return json.load(handle)
    except Exception:
        return {"dx": 0.0, "dy": 0.0, "n": 0, "residuals": []}


def update_correction(store, residual_xy) -> dict:
    """Fold one (see, feel) pair into the running correction.

    A plain running mean, deliberately. With one correspondence there is nothing to fit
    beyond a translation, and claiming a rotation or scale from a handful of samples is the
    kind of overreach this notebook keeps catching. The residual spread is recorded so a
    later fit can tell whether a translation was ever the right model.
    """
    import json
    state = load_correction(store)
    state["residuals"] = (state.get("residuals") or [])[-49:] + [
        [round(float(residual_xy[0]), 5), round(float(residual_xy[1]), 5)]
    ]
    arr = np.asarray(state["residuals"], dtype=float)
    state["dx"], state["dy"] = float(arr[:, 0].mean()), float(arr[:, 1].mean())
    state["n"] = len(arr)
    state["spread_mm"] = round(float(np.hypot(*arr.std(axis=0)) * 1000), 1)
    try:
        with open(_correction_path(store), "w") as handle:
            json.dump(state, handle, indent=2)
    except Exception:
        pass
    return state


def touch_find(
    *,
    color: str = "green",
    camera: Optional[str] = None,
    observe_z: float = 0.30,
    step: float = 0.012,
    coarse_step: float = 0.045,
    search_radius: float = 0.10,
    max_probes: int = 60,
    min_object_mm: float = 8.0,
    speed_factor: float = 0.08,
    use_correction: bool = True,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
    _log_decision: Optional[Any] = None,
    _record_event: Optional[Any] = None,
) -> dict:
    """See a block, then find it by feel. Returns the see/feel disagreement.

    The table plane is measured from this run's own low contacts rather than a stored
    constant, so a drifted table cannot corrupt the result. A probe counts as touching the
    object when it stops `min_object_mm` above that measured plane.
    """
    panda = _extract(_context, "panda")
    if panda is None:
        return {"_source": "mock", "found": False, "note": "mock — no arm"}

    pos = panda.get_position()
    move_to_pose([float(pos[0]), float(pos[1]), float(observe_z)],
                 speed_factor=speed_factor, _context=_context)
    seen = _observe(color, camera, _context)
    if not seen.get("found") or not seen["blobs"]:
        return {"_source": "live", "found": False,
                "note": f"target not seen: {seen.get('reason')}", "seen": seen}
    blob = seen["blobs"][0]
    if not blob.get("depth_m"):
        return {"_source": "live", "found": False,
                "note": "no depth on target", "seen": blob}

    observed_from = [float(v) for v in panda.get_position()]
    dx, dy = image_to_world_offset(blob["u"], blob["v"], blob["depth_m"])
    correction = load_correction(_store) if use_correction else {"dx": 0.0, "dy": 0.0, "n": 0}
    seed = (observed_from[0] + dx + float(correction.get("dx", 0.0)),
            observed_from[1] + dy + float(correction.get("dy", 0.0)))
    if _record_event is not None:
        _record_event("approach", "observation", label="touch_find_seen",
                      data={"blob": blob, "seed_xy": list(seed),
                            "observed_from": observed_from})

    probes, contacts, hits = [], [], []
    table_z = None

    def probe(x, y):
        """One probe; returns True if it landed on something standing on the table."""
        nonlocal table_z
        if not (0.28 <= x <= 0.62 and -0.32 <= y <= 0.32):
            return None                   # outside the reachable table envelope
        if any(abs(p["x"] - x) < 1e-4 and abs(p["y"] - y) < 1e-4 for p in probes):
            return None                   # already sampled
        result = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH,
                             z_floor=PROBE_Z_FLOOR, _context=_context)
        contact_z = result.get("contact_z")
        probes.append({"x": round(x, 4), "y": round(y, 4),
                       "contact_z": contact_z, "contacted": result.get("contacted")})
        if contact_z is None:
            return None
        contacts.append(float(contact_z))
        # The table is whatever most probes agree on; objects stand above it.
        table_z = float(np.median(contacts))
        if float(contact_z) > table_z + min_object_mm / 1000.0:
            hits.append((x, y, float(contact_z)))
            return True
        return False

    # Coarse pass. A probe within (object width + fingertip width) of centre hits, so the
    # step can be far larger than the fine spacing and still be exhaustive — sweeping a
    # 90 mm radius at 18 mm costs 5x what 45 mm does for the same guarantee.
    coarse = max(float(step), float(coarse_step))
    found_at = None
    for offset_x, offset_y in _spiral(coarse, search_radius):
        if len(probes) >= max_probes:
            break
        if probe(seed[0] + offset_x, seed[1] + offset_y):
            found_at = (seed[0] + offset_x, seed[1] + offset_y)
            break

    # Fine pass: bracket the object around the coarse hit to place its centre.
    if found_at is not None:
        for offset_x, offset_y in _spiral(float(step), coarse):
            if len(probes) >= max_probes or len(hits) >= 6:
                break
            probe(found_at[0] + offset_x, found_at[1] + offset_y)

    if not hits:
        return {"_source": "live", "found": False,
                "note": f"no object found in {len(probes)} probes "
                        f"within {search_radius * 1000:.0f} mm of where it was seen",
                "seen": blob, "seed_xy": list(seed), "table_z": table_z,
                "probes": probes}

    touched = (float(np.mean([h[0] for h in hits])), float(np.mean([h[1] for h in hits])))
    top_z = float(np.mean([h[2] for h in hits]))
    residual = (touched[0] - seed[0], touched[1] - seed[1])
    out = {
        "_source": "live", "found": True,
        "seen": {"u": blob["u"], "v": blob["v"], "depth_m": blob["depth_m"],
                 "npix": blob["npix"], "angle_deg": blob["angle_deg"]},
        "observed_from": observed_from,
        "seed_xy": [round(v, 4) for v in seed],
        "touched_xy": [round(v, 4) for v in touched],
        "residual_xy": [round(v, 4) for v in residual],
        "residual_mm": round(float(np.hypot(*residual)) * 1000, 1),
        "object_top_z": round(top_z, 4),
        "table_z": round(table_z, 4) if table_z else None,
        "object_height_mm": round((top_z - table_z) * 1000, 1) if table_z else None,
        "n_probes": len(probes), "n_hits": len(hits),
        "correction_used": {"dx": round(float(correction.get("dx", 0.0)), 5),
                            "dy": round(float(correction.get("dy", 0.0)), 5),
                            "n": correction.get("n", 0)},
        "probes": probes,
    }
    if use_correction:
        # The find just produced a fresh (see, feel) pair. Fold it in, so the next seed is
        # better and the next search is shorter: probes-per-find is the learning curve.
        out["correction_after"] = update_correction(
            _store, (touched[0] - seed[0] + float(correction.get("dx", 0.0)),
                     touched[1] - seed[1] + float(correction.get("dy", 0.0))))
    if _record_event is not None:
        _record_event("verify", "contact", label="touch_find_result",
                      data={k: out[k] for k in
                            ("seed_xy", "touched_xy", "residual_mm",
                             "object_height_mm", "n_probes")})
    if _log_decision is not None:
        _log_decision("orchestrator",
                      reasoning=(f"touch-found {color} {out['residual_mm']} mm from where "
                                 f"it was seen, in {len(probes)} probes"))
    return out


def practice_touch(
    *,
    cycles: int = 10,
    colors: Optional[list] = None,
    camera: Optional[str] = None,
    observe_z: float = 0.30,
    lift_z: float = 0.18,
    grip_width: float = 0.024,
    place_margin_m: float = 0.09,
    max_probes: int = 60,
    speed_factor: float = 0.08,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
    _log_decision: Optional[Any] = None,
    _record_event: Optional[Any] = None,
) -> dict:
    """Pick each block up, put it down somewhere else, repeat — and get faster at it.

    The loop is deliberately the whole thesis in miniature. Every cycle produces a fresh
    (see, feel) correspondence, which sharpens the visual seed, which shortens the next
    search. **Probes-per-find is the learning curve**: the first find on 2026-09-01 needed
    26 probes because the seed was 76 mm out, and a converged correction should bring that
    to the first or second probe. Deliberate search compressing into a prior, measured.

    Placement is randomised on purpose. Both distilled artifacts in this project are
    blocked on spatial diversity (#146), and a loop that returns every block to the same
    spot generates volume without variety.
    """
    import random

    from hebbot.skills.primitives import grasp_at, open_gripper

    panda = _extract(_context, "panda")
    if panda is None:
        return {"_source": "mock", "note": "mock — no arm"}
    colors = list(colors or ["green", "red", "blue"])
    trials, placed = [], {}

    for cycle in range(int(cycles)):
        for color in colors:
            trial = {"cycle": cycle, "color": color, "t": time.time()}
            found = touch_find(
                color=color, camera=camera, observe_z=observe_z,
                max_probes=max_probes, speed_factor=speed_factor,
                _context=_context, _store=_store, _record_event=_record_event,
            )
            trial["n_probes"] = found.get("n_probes")
            trial["correction_used"] = found.get("correction_used")
            trial["residual_mm"] = found.get("residual_mm")
            if not found.get("found"):
                trial.update({"stage": "find", "ok": False, "note": found.get("note")})
                trials.append(trial)
                continue

            x, y = found["touched_xy"]
            table_z = float(found["table_z"])
            trial["touched_xy"] = [x, y]
            trial["object_height_mm"] = found.get("object_height_mm")

            move_to_pose([x, y, 0.12], speed_factor=speed_factor, _context=_context)
            grasp = grasp_at(grasp_z=table_z + 0.010, width=grip_width,
                             descent_speed=0.02, _context=_context)
            trial["grasp"] = {k: grasp.get(k) for k in
                              ("grasped", "actual_width", "grip_high_mm", "note")}
            if not grasp.get("grasped"):
                trial.update({"stage": "grasp", "ok": False})
                trials.append(trial)
                open_gripper(_context=_context)
                continue

            move_to_pose([x, y, lift_z], speed_factor=speed_factor, _context=_context)
            held = float(panda.get_gripper_width()) if hasattr(panda, "get_gripper_width") \
                else float(grasp.get("actual_width") or 0.0)
            trial["held_width"] = round(held, 4)

            # Somewhere else, and not on top of anything we know about.
            target = None
            for _ in range(40):
                cand = (random.uniform(0.40, 0.53), random.uniform(-0.22, 0.06))
                if all(np.hypot(cand[0] - p[0], cand[1] - p[1]) >= place_margin_m
                       for c, p in placed.items() if c != color):
                    target = cand
                    break
            if target is None:
                target = (0.46, -0.05)
            move_to_pose([target[0], target[1], lift_z], speed_factor=speed_factor,
                         _context=_context)
            move_to_pose([target[0], target[1], table_z + 0.012],
                         speed_factor=0.04, _context=_context)
            open_gripper(_context=_context)
            move_to_pose([target[0], target[1], lift_z], speed_factor=speed_factor,
                         _context=_context)
            placed[color] = target
            trial.update({"stage": "place", "ok": True,
                          "placed_xy": [round(v, 4) for v in target]})
            trials.append(trial)
            if _record_event is not None:
                _record_event("reset", "closure", label="place",
                              data={k: trial[k] for k in
                                    ("cycle", "color", "n_probes", "placed_xy")})

    done = [t for t in trials if t.get("ok")]
    probes = [t["n_probes"] for t in trials if t.get("n_probes")]
    half = max(1, len(probes) // 2)
    summary = {
        "cycles": cycles, "attempts": len(trials), "successes": len(done),
        "success_rate": round(len(done) / max(len(trials), 1), 3),
        "probes_first_half": round(float(np.mean(probes[:half])), 1) if probes else None,
        "probes_last_half": round(float(np.mean(probes[half:])), 1)
        if len(probes) > half else None,
        "correction_final": load_correction(_store),
        "by_color": {c: {
            "attempts": sum(1 for t in trials if t["color"] == c),
            "successes": sum(1 for t in trials if t["color"] == c and t.get("ok")),
        } for c in colors},
    }
    if _log_decision is not None:
        _log_decision("orchestrator",
                      reasoning=(f"practice_touch: {summary['successes']}/"
                                 f"{summary['attempts']} placed; probes/find "
                                 f"{summary['probes_first_half']} -> "
                                 f"{summary['probes_last_half']}"))
    return {"_source": "live", **summary, "trials": trials}


PRIMITIVE_SPECS = [
    {
        "name": "practice_touch",
        "function_spec": "hebbot.skills.selfcal:practice_touch",
        "params_schema": {
            "type": "object",
            "properties": {
                "cycles": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "colors": {"type": "array", "items": {"enum": ["blue", "green", "red"]},
                           "description": "blocks to cycle through, in order"},
                "camera": {"type": "string"},
                "observe_z": {"type": "number", "minimum": 0.15, "maximum": 0.45,
                              "default": 0.30},
                "lift_z": {"type": "number", "minimum": 0.10, "maximum": 0.35,
                           "default": 0.18},
                "grip_width": {"type": "number", "minimum": 0.0, "maximum": 0.08,
                               "default": 0.024},
                "place_margin_m": {"type": "number", "minimum": 0.03, "maximum": 0.2,
                                   "default": 0.09},
                "max_probes": {"type": "integer", "minimum": 1, "maximum": 200,
                               "default": 60},
                "speed_factor": {"type": "number", "minimum": 0.01, "maximum": 0.2,
                                 "default": 0.08},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "touch_find",
        "function_spec": "hebbot.skills.selfcal:touch_find",
        "expectations": {"checks": [
            {"name": "found", "field": "found", "op": "eq", "value": True},
        ]},
        "params_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["blue", "green", "red"],
                          "default": "green"},
                "camera": {"type": "string"},
                "observe_z": {"type": "number", "minimum": 0.15, "maximum": 0.45,
                              "default": 0.30,
                              "description": "height to look from; both colour and depth "
                                             "measure better close (2026-09-01)"},
                "step": {"type": "number", "minimum": 0.005, "maximum": 0.05,
                         "default": 0.012,
                         "description": "fine probe spacing once the object is hit (m)"},
                "coarse_step": {"type": "number", "minimum": 0.01, "maximum": 0.08,
                                "default": 0.045,
                                "description": "search spacing; safe up to the object "
                                               "width plus the fingertip width"},
                "search_radius": {"type": "number", "minimum": 0.02, "maximum": 0.20,
                                  "default": 0.10},
                "max_probes": {"type": "integer", "minimum": 1, "maximum": 200,
                               "default": 60},
                "min_object_mm": {"type": "number", "minimum": 3, "maximum": 40,
                                  "default": 8.0,
                                  "description": "height above the measured table plane "
                                                 "that counts as touching the object"},
                "speed_factor": {"type": "number", "minimum": 0.01, "maximum": 0.2,
                                 "default": 0.08},
                "use_correction": {"type": "boolean", "default": True,
                                   "description": "apply and update the camera-to-hand "
                                                  "correction the arm has taught itself"},
            },
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
            expectations=spec.get("expectations"),
        )
