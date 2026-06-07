"""Demo: invoke the `home` composition skill on the live Franka."""

from __future__ import annotations

import os
import sys

from hebbot import Substrate
from hebbot.skills.primitives import register_all as register_primitives
from hebbot.skills.composed import register_all as register_composed


def main() -> int:
    s = Substrate(os.environ.get("HEBBOT_DATA", "~/hebbot-data"))
    register_primitives(s)
    register_composed(s)

    try:
        import panda_py
    except ImportError:
        print("panda-python not installed.")
        return 1

    host = os.environ.get("HEBBOT_FRANKA", "192.168.0.253")
    try:
        panda = panda_py.Panda(host)
        gripper = panda_py.libfranka.Gripper(host)
    except Exception as e:
        print(f"connect failed: {e}")
        return 2

    ctx = {"panda": panda, "gripper": gripper}

    print("calling home composition...")
    out = s.call("home", context=ctx)

    print(f"  success: {out['success']}")
    print(f"  completed_steps: {out['completed_steps']}")
    for sr in out["sub_results"]:
        sub = sr["outcome"]
        step_n = sr["step"]
        skill = sr["skill"]
        print(f"    step {step_n}: {skill:<18} success={sub['success']}")

    print()
    recent = s.store.recent_episodes(limit=8)
    print("recent episodes:")
    for ep in recent:
        parent = ""
        if ep["parent_id"]:
            parent = f"(parent={ep['parent_id'][:8]})"
        ok = "OK  " if ep["success"] else "FAIL"
        print(f"  {ep['id'][:8]}  {ep['skill_name']:<18} {ok} {parent}")

    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
