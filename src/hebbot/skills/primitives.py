"""Bootstrap primitives — the hand-written `classical`-body-type skills.

Per foundations.md commitment #10, primitives are the only thing that ships
as code; everything else is data records the agent creates at runtime.

Each primitive:
  - Accepts a `_context` kwarg if it needs hardware handles.
  - Falls back to mock behavior when the relevant handle isn't supplied.
  - Returns a plain dict so the substrate can serialize it as an outcome.

Context keys this module recognizes:
  - "panda":   a panda_py.Panda instance for arm control / state reads
  - "gripper": a panda_py.libfranka.Gripper instance (the gripper, not the arm)
"""

from __future__ import annotations

import time
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Mock-mode defaults that match panda-py's get_state() shape closely enough
# to exercise downstream code without a robot.
# ---------------------------------------------------------------------------

_MOCK_STATE = {
    "joints": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785],
    "ee_pose": [
        1.0, 0.0, 0.0, 0.3,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.5,
        0.0, 0.0, 0.0, 1.0,
    ],
    "force": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "gripper_width": 0.04,
    "_source": "mock",
}

# Reasonable defaults for the Franka working envelope
_DEFAULT_SPEED_FACTOR = 0.1
_MIN_SPEED_FACTOR = 0.01
_MAX_SPEED_FACTOR = 0.5

# Gripper hard limits (Franka Hand)
_GRIPPER_MAX_WIDTH = 0.08
_GRIPPER_MIN_WIDTH = 0.0
_DEFAULT_GRIPPER_SPEED = 0.1
_DEFAULT_GRIP_FORCE = 20.0  # N


def _extract(context: Optional[Any], attr: str) -> Optional[Any]:
    """Pull `attr` out of `context` if it's a dict or has the attribute."""
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get(attr)
    return getattr(context, attr, None)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# State read
# ---------------------------------------------------------------------------

def get_state(_context: Optional[Any] = None) -> dict:
    """Read the current robot state.

    Returns: {joints, ee_pose, force, gripper_width, _source}
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")

    if panda is None:
        return dict(_MOCK_STATE)

    import numpy as np
    state = panda.get_state()
    ee = np.array(state.O_T_EE).reshape(4, 4).T.flatten().tolist()
    width = None
    if gripper is not None:
        try:
            width = float(gripper.read_once().width)
        except Exception:
            width = None
    return {
        "joints": [float(q) for q in state.q],
        "ee_pose": ee,
        "force": [
            float(state.O_F_ext_hat_K[i]) if i < 6 else 0.0
            for i in range(6)
        ],
        "gripper_width": width,
        "_source": "live",
    }


# ---------------------------------------------------------------------------
# Joint-space move
# ---------------------------------------------------------------------------

def move_to_joints(
    q: list[float],
    *,
    speed_factor: float = _DEFAULT_SPEED_FACTOR,
    _context: Optional[Any] = None,
) -> dict:
    """Move the arm to the specified 7-DOF joint configuration.

    Args:
        q: target joint angles (7 floats, radians)
        speed_factor: 0.01-0.5, default 0.1 (slow + safe)
    """
    if len(q) != 7:
        raise ValueError(f"q must have 7 elements, got {len(q)}")
    speed_factor = _clamp(float(speed_factor), _MIN_SPEED_FACTOR, _MAX_SPEED_FACTOR)
    q = [float(v) for v in q]

    panda = _extract(_context, "panda")
    if panda is None:
        time.sleep(0.05)
        return {
            "_source": "mock",
            "target_q": q,
            "speed_factor": speed_factor,
            "note": "mock — no actual motion",
        }

    panda.move_to_joint_position(q, speed_factor=speed_factor)
    state = panda.get_state()
    return {
        "_source": "live",
        "target_q": q,
        "final_q": [float(qi) for qi in state.q],
        "max_error": max(abs(a - b) for a, b in zip(q, state.q)),
        "speed_factor": speed_factor,
    }


# ---------------------------------------------------------------------------
# Cartesian-space move (via IK)
# ---------------------------------------------------------------------------

def move_to_pose(
    position: list[float],
    orientation: Optional[list[float]] = None,
    *,
    speed_factor: float = _DEFAULT_SPEED_FACTOR,
    _context: Optional[Any] = None,
) -> dict:
    """Move the end-effector to a Cartesian pose (uses ik_full + joint move).

    Args:
        position: [x, y, z] in meters, robot base frame
        orientation: quaternion [w, x, y, z]; defaults to gripper pointing
                     straight down ([0, 1, 0, 0])
        speed_factor: 0.01-0.5
    """
    if len(position) != 3:
        raise ValueError(f"position must have 3 elements, got {len(position)}")
    if orientation is None:
        orientation = [0.0, 1.0, 0.0, 0.0]  # gripper points -Z (down) in EE frame
    if len(orientation) != 4:
        raise ValueError(f"orientation must have 4 elements (w,x,y,z), got {len(orientation)}")
    speed_factor = _clamp(float(speed_factor), _MIN_SPEED_FACTOR, _MAX_SPEED_FACTOR)

    panda = _extract(_context, "panda")
    if panda is None:
        time.sleep(0.05)
        return {
            "_source": "mock",
            "target_position": list(position),
            "target_orientation": list(orientation),
            "speed_factor": speed_factor,
            "note": "mock — no actual motion",
        }

    import numpy as np
    import panda_py

    current_q = panda.get_state().q
    pos = np.array(position, dtype=float)
    # API convention is scalar-first [w, x, y, z]; panda_py / Eigen expect
    # scalar-last [x, y, z, w]. Reorder before handing it to ik_full.
    w, x, y, z = (float(v) for v in orientation)
    quat = np.array([x, y, z, w], dtype=float)

    # ik_full returns up to 4 branches; pick the one closest to current joints
    # (matches commitment #11's preference for relative-state continuity)
    best_q = None
    best_dist = float("inf")
    for q7 in [current_q[6], current_q[6] + 0.5, current_q[6] - 0.5, 0.785, -0.785, 0.0]:
        try:
            sols = panda_py.ik_full(pos, quat, np.array(current_q), float(q7))
        except Exception:
            continue
        if sols is None:
            continue
        sols = np.array(sols)
        if sols.ndim == 1:
            sols = sols.reshape(1, -1)
        for sol in sols:
            sol = np.array(sol).flatten()
            if sol.shape != (7,) or np.any(np.isnan(sol)):
                continue
            dist = float(np.linalg.norm(sol - np.array(current_q)))
            if dist < best_dist:
                best_dist = dist
                best_q = sol

    if best_q is None:
        raise RuntimeError(f"no IK solution for position={position} orientation={orientation}")

    panda.move_to_joint_position(best_q.tolist(), speed_factor=speed_factor)
    final_state = panda.get_state()
    final_ee = np.array(final_state.O_T_EE).reshape(4, 4).T
    final_pos = final_ee[:3, 3].tolist()
    return {
        "_source": "live",
        "target_position": list(position),
        "target_orientation": list(orientation),
        "ik_solution_q": [float(v) for v in best_q],
        "final_position": final_pos,
        "position_error": float(np.linalg.norm(np.array(final_pos) - pos)),
        "speed_factor": speed_factor,
    }


# ---------------------------------------------------------------------------
# Gripper
# ---------------------------------------------------------------------------

def open_gripper(
    *,
    width: float = _GRIPPER_MAX_WIDTH,
    speed: float = _DEFAULT_GRIPPER_SPEED,
    _context: Optional[Any] = None,
) -> dict:
    """Open the gripper to the given width (default: fully open).

    Args:
        width: target width in meters (0.0 - 0.08)
        speed: opening speed, m/s
    """
    width = _clamp(float(width), _GRIPPER_MIN_WIDTH, _GRIPPER_MAX_WIDTH)
    speed = max(0.01, float(speed))

    gripper = _extract(_context, "gripper")
    if gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "target_width": width, "speed": speed,
                "note": "mock — no actual gripper move"}

    ok = gripper.move(width=width, speed=speed)
    state = gripper.read_once()
    return {
        "_source": "live",
        "target_width": width,
        "actual_width": float(state.width),
        "speed": speed,
        "success": bool(ok),
    }


def close_gripper(
    *,
    width: float = 0.0,
    speed: float = _DEFAULT_GRIPPER_SPEED,
    force: float = _DEFAULT_GRIP_FORCE,
    epsilon_inner: float = 0.005,
    epsilon_outer: float = 0.005,
    _context: Optional[Any] = None,
) -> dict:
    """Close the gripper around an object with a target width and force.

    Uses libfranka's `grasp` semantics — the grasp succeeds if the gripper
    width falls within [width - epsilon_inner, width + epsilon_outer] and the
    force can be maintained.

    Args:
        width: expected object width (m). Use 0.0 for "close all the way."
        speed: closing speed (m/s)
        force: grip force (N)
        epsilon_inner, epsilon_outer: width tolerance for grasp-success check
    """
    width = _clamp(float(width), _GRIPPER_MIN_WIDTH, _GRIPPER_MAX_WIDTH)
    speed = max(0.01, float(speed))
    force = max(1.0, float(force))

    gripper = _extract(_context, "gripper")
    if gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "target_width": width, "speed": speed,
                "force": force, "grasped": True, "note": "mock — no actual gripper move"}

    grasped = gripper.grasp(
        width=width, speed=speed, force=force,
        epsilon_inner=epsilon_inner, epsilon_outer=epsilon_outer,
    )
    state = gripper.read_once()
    return {
        "_source": "live",
        "target_width": width,
        "actual_width": float(state.width),
        "speed": speed,
        "force": force,
        "grasped": bool(grasped),
    }


# ---------------------------------------------------------------------------
# Bulk registration
# ---------------------------------------------------------------------------

PRIMITIVE_SPECS = [
    {
        "name": "get_state",
        "function_spec": "hebbot.skills.primitives:get_state",
        "params_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "move_to_joints",
        "function_spec": "hebbot.skills.primitives:move_to_joints",
        "params_schema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 7, "maxItems": 7,
                    "description": "7-DOF joint angles in radians",
                },
                "speed_factor": {
                    "type": "number",
                    "minimum": 0.01, "maximum": 0.5, "default": 0.1,
                },
            },
            "required": ["q"],
            "additionalProperties": False,
        },
    },
    {
        "name": "move_to_pose",
        "function_spec": "hebbot.skills.primitives:move_to_pose",
        "params_schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3, "maxItems": 3,
                    "description": "[x, y, z] in meters, robot base frame",
                },
                "orientation": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4, "maxItems": 4,
                    "description": "quaternion [w, x, y, z]; default: gripper-down",
                },
                "speed_factor": {
                    "type": "number",
                    "minimum": 0.01, "maximum": 0.5, "default": 0.1,
                },
            },
            "required": ["position"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_gripper",
        "function_spec": "hebbot.skills.primitives:open_gripper",
        "params_schema": {
            "type": "object",
            "properties": {
                "width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.08},
                "speed": {"type": "number", "minimum": 0.01, "default": 0.1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "close_gripper",
        "function_spec": "hebbot.skills.primitives:close_gripper",
        "params_schema": {
            "type": "object",
            "properties": {
                "width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.0,
                          "description": "expected object width (m); 0 for full close"},
                "speed": {"type": "number", "minimum": 0.01, "default": 0.1},
                "force": {"type": "number", "minimum": 1.0, "default": 20.0,
                          "description": "grip force in N"},
                "epsilon_inner": {"type": "number", "minimum": 0.0, "default": 0.005},
                "epsilon_outer": {"type": "number", "minimum": 0.0, "default": 0.005},
            },
            "additionalProperties": False,
        },
    },
]


def register_all(substrate) -> None:
    """Register all primitives in this module into the given substrate."""
    for spec in PRIMITIVE_SPECS:
        substrate.register(
            spec["name"],
            function_spec=spec["function_spec"],
            params_schema=spec.get("params_schema"),
            expectations=spec.get("expectations"),
        )
