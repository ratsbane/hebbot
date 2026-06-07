"""Composed skills — `composition`-body-type entries built from primitives.

These are written as Python code here only so we have a convenient way to
register them. The substrate stores them as composition body records; the
agent could create equivalent records at runtime without any code change.
"""

from __future__ import annotations


# A reasonable home pose for the Franka — gripper points down, EE around
# (0.30, 0.0, 0.49). Slightly different from panda-py's `move_to_start` so
# we can distinguish them in episode logs.
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]


COMPOSED_SKILLS = [
    {
        "name": "home",
        "params_schema": {
            "type": "object",
            "properties": {
                "speed_factor": {"type": "number", "minimum": 0.01, "maximum": 0.5,
                                 "default": 0.1},
            },
            "additionalProperties": False,
        },
        "steps": [
            {
                "skill": "open_gripper",
                "args": {"width": 0.08, "speed": 0.1},
            },
            {
                "skill": "move_to_joints",
                "args": {"q": HOME_Q, "speed_factor": 0.1},
                "arg_map": {"speed_factor": "speed_factor"},
            },
        ],
    },
]


def register_all(substrate) -> None:
    for spec in COMPOSED_SKILLS:
        substrate.register_composition(
            spec["name"],
            steps=spec["steps"],
            params_schema=spec.get("params_schema"),
            expectations=spec.get("expectations"),
        )
