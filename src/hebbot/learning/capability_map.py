"""Offline geometric capability mapping through Hebbot's cuRobo service."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np


HAND_EE_OFFSET_M = 0.103
GRIPPER_DOWN_WXYZ = [0.0, 1.0, 0.0, 0.0]


def _quat_to_mat(wxyz: list[float]) -> np.ndarray:
    w, x, y, z = (float(v) for v in wxyz)
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
        [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
        [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)],
    ])


def ee_to_hand(position: list[float], quaternion: list[float]) -> list[float]:
    """Convert Hebbot's panda_py EE target to cuRobo's panda_hand target."""
    tool_z = _quat_to_mat(quaternion) @ np.array([0.0, 0.0, 1.0])
    return (np.asarray(position, dtype=float) - HAND_EE_OFFSET_M * tool_z).tolist()


def http_request(url: str, endpoint: str, body: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + endpoint,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def sample_grid(
    *, xs: list[float], ys: list[float], ee_z: float, start_q: list[float],
    url: str = "http://127.0.0.1:8211",
    quaternion: list[float] | None = None,
    request: Callable[[str, str, dict], dict] = http_request,
    plan_all: bool = True,
) -> dict:
    """Sample collision-aware IK and paths without commanding robot motion."""
    quaternion = quaternion or GRIPPER_DOWN_WXYZ
    samples = []
    for x in xs:
        for y in ys:
            ee_position = [float(x), float(y), float(ee_z)]
            hand_position = ee_to_hand(ee_position, quaternion)
            base = {
                "position": hand_position,
                "quaternion": quaternion,
                "start_q": [float(v) for v in start_q],
            }
            try:
                ik = request(url, "/ik", base)
            except Exception as exc:
                ik = {"ok": False, "error": str(exc)}
            plan = None
            if plan_all and ik.get("ok"):
                try:
                    plan = request(url, "/plan", base)
                except Exception as exc:
                    plan = {"ok": False, "error": str(exc)}
            samples.append({
                "ee_position": ee_position,
                "hand_position": hand_position,
                "ik_reachable": bool(ik.get("ok")),
                "ik_pos_err_mm": ik.get("pos_err_mm"),
                "ik_dist_to_start": ik.get("dist_to_start"),
                "ik_q": ik.get("q"),
                "path_found": (bool(plan.get("ok")) if plan is not None else None),
                "path_waypoints": (len(plan.get("waypoints") or []) if plan else None),
                "error": ik.get("error") or (plan or {}).get("error"),
            })
    return {
        "kind": "geometric_capability_map_v1",
        "created_at": time.time(),
        "evidence": "curobo_self_collision_and_joint_limits_no_live_motion",
        "world_collision_model": "robot_only_no_table_or_blocks",
        "ee_z": float(ee_z),
        "orientation_wxyz": quaternion,
        "start_q": [float(v) for v in start_q],
        "grid": {"xs": xs, "ys": ys},
        "samples": samples,
    }


def save_map(capability_map: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(capability_map, indent=2, sort_keys=True) + "\n")
    return path


def boundary_candidates(capability_map: dict) -> list[dict]:
    """Reachable cells adjacent to an IK-infeasible four-connected grid cell."""
    samples = capability_map["samples"]
    by_xy = {(round(s["ee_position"][0], 6), round(s["ee_position"][1], 6)): s
             for s in samples}
    xs = capability_map["grid"]["xs"]
    ys = capability_map["grid"]["ys"]
    dx = min(np.diff(sorted(xs))) if len(xs) > 1 else 0.0
    dy = min(np.diff(sorted(ys))) if len(ys) > 1 else 0.0
    result = []
    for sample in samples:
        if not sample["ik_reachable"] or sample.get("path_found") is False:
            continue
        x, y, _ = sample["ee_position"]
        neighbours = [(x + dx, y), (x - dx, y), (x, y + dy), (x, y - dy)]
        if any((n := by_xy.get((round(nx, 6), round(ny, 6)))) is not None
               and not n["ik_reachable"] for nx, ny in neighbours):
            result.append(sample)
    return result
