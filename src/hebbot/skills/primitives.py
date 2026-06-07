"""Bootstrap primitives — the only hand-written skills.

Per foundations.md commitment #10, primitives are `classical` body-type skills
that ship as code. Everything else is data.

Currently provides:
    - get_state: reads current robot joint positions, EE pose, and gripper state.

The implementation prefers a real Franka via panda-py if `_context` carries a
connected `panda` handle; otherwise it returns mock data so the substrate is
testable without hardware in the loop.

Adding a new primitive: write the function here, then call
    substrate.register("name", function_spec="hebbot.skills.primitives:func")
or run hebbot.skills.primitives.register_all(substrate) which registers all
primitives in this module with sensible defaults.
"""

from __future__ import annotations

from typing import Any, Optional

# Mock-mode defaults that match panda-py's get_state() shape closely enough
# to exercise downstream code without a robot.
_MOCK_STATE = {
    "joints": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785],   # close to home pose
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


def get_state(_context: Optional[Any] = None) -> dict:
    """Read the current robot state.

    Returns a dict with keys: joints, ee_pose, force, gripper_width.
    If `_context` carries a `panda` handle, reads live; otherwise mock data.
    """
    panda = _extract(_context, "panda")
    if panda is None:
        return dict(_MOCK_STATE)

    state = panda.get_state()
    # Flatten EE pose row-major from numpy 4x4 → list of 16 floats
    import numpy as np
    ee = np.array(state.O_T_EE).reshape(4, 4).T.flatten().tolist()
    return {
        "joints": [float(q) for q in state.q],
        "ee_pose": ee,
        "force": [
            float(state.O_F_ext_hat_K[i]) if i < 6 else 0.0
            for i in range(6)
        ],
        "gripper_width": None,  # gripper width comes from the gripper handle, not panda
        "_source": "live",
    }


def _extract(context: Optional[Any], attr: str) -> Optional[Any]:
    """Pull `attr` out of `context` if it's a dict or has the attribute."""
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get(attr)
    return getattr(context, attr, None)


# ---------------------------------------------------------------------------
# Bulk registration
# ---------------------------------------------------------------------------

PRIMITIVE_SPECS = [
    {
        "name": "get_state",
        "function_spec": "hebbot.skills.primitives:get_state",
        "params_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "expectations": None,
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
