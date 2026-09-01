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


def touch_find(
    *,
    color: str = "green",
    camera: Optional[str] = None,
    observe_z: float = 0.30,
    step: float = 0.018,
    search_radius: float = 0.10,
    max_probes: int = 60,
    min_object_mm: float = 8.0,
    speed_factor: float = 0.08,
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
    seed = (observed_from[0] + dx, observed_from[1] + dy)
    if _record_event is not None:
        _record_event("approach", "observation", label="touch_find_seen",
                      data={"blob": blob, "seed_xy": list(seed),
                            "observed_from": observed_from})

    probes, contacts, hits = [], [], []
    table_z = None
    for index, (offset_x, offset_y) in enumerate(_spiral(step, search_radius)):
        if index >= max_probes:
            break
        x, y = seed[0] + offset_x, seed[1] + offset_y
        if not (0.28 <= x <= 0.62 and -0.32 <= y <= 0.32):
            continue                      # stay inside the reachable table envelope
        result = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH,
                             z_floor=PROBE_Z_FLOOR, _context=_context)
        contact_z = result.get("contact_z")
        probes.append({"x": round(x, 4), "y": round(y, 4),
                       "contact_z": contact_z, "contacted": result.get("contacted")})
        if contact_z is None:
            continue
        contacts.append(float(contact_z))
        # The table is whatever most probes agree on; objects stand above it.
        table_z = float(np.median(contacts))
        if float(contact_z) > table_z + min_object_mm / 1000.0:
            hits.append((x, y, float(contact_z)))
            if len(hits) >= 5:            # enough to centre on
                break

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
        "probes": probes,
    }
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


PRIMITIVE_SPECS = [
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
                         "default": 0.018, "description": "probe spacing (m)"},
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
