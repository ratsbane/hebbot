"""Demo: small motion + gripper test through the substrate, live.

Procedure:
  1. Read current state.
  2. Move base joint (q[0]) by +0.10 rad (~5.7°) and back. Very slow.
  3. Move gripper through open → close → fully open. No object expected.
  4. Confirm episodes recorded.

Safety:
  - speed_factor = 0.05 throughout
  - Joint deltas tiny (0.10 rad on a single joint)
  - Keep your hand near the e-stop.

Run:
    cd ~/hebbot && .venv/bin/python scripts/demo_live_motion.py
"""

from __future__ import annotations

import os
import sys
import time

from hebbot import Substrate
from hebbot.skills.primitives import register_all


FRANKA_HOST = os.environ.get("HEBBOT_FRANKA", "192.168.0.253")
DATA_DIR = os.environ.get("HEBBOT_DATA", "~/hebbot-data")


def main() -> int:
    s = Substrate(DATA_DIR)
    register_all(s)

    try:
        import panda_py
    except ImportError:
        print("panda-python not installed.")
        return 1

    try:
        panda = panda_py.Panda(FRANKA_HOST)
        gripper = panda_py.libfranka.Gripper(FRANKA_HOST)
    except Exception as e:
        print(f"connect failed: {e}")
        return 2

    ctx = {"panda": panda, "gripper": gripper}

    # --- 1. Read current state ---
    print("== current state ==")
    out = s.call("get_state", context=ctx)
    assert out["success"]
    q_start = list(out["result"]["joints"])
    print(f"  q_start: {[f'{q:.3f}' for q in q_start]}")
    print(f"  gripper_width: {out['result']['gripper_width']}")

    # --- 2. Small joint move on q[0] ---
    q_offset = list(q_start)
    q_offset[0] += 0.10  # rotate base by ~5.7°
    print(f"\n== move_to_joints (q[0] +0.10) ==")
    out = s.call("move_to_joints",
                 params={"q": q_offset, "speed_factor": 0.05},
                 context=ctx)
    print(f"  success: {out['success']}")
    if out["success"]:
        print(f"  max_error: {out['result']['max_error']:.5f} rad")

    time.sleep(0.5)

    print(f"\n== move_to_joints (back to start) ==")
    out = s.call("move_to_joints",
                 params={"q": q_start, "speed_factor": 0.05},
                 context=ctx)
    print(f"  success: {out['success']}")
    if out["success"]:
        print(f"  max_error: {out['result']['max_error']:.5f} rad")

    # --- 3. Gripper open / close / fully open ---
    print("\n== open_gripper(width=0.04) ==")
    out = s.call("open_gripper",
                 params={"width": 0.04, "speed": 0.05},
                 context=ctx)
    print(f"  success: {out['success']}, actual_width: {out['result'].get('actual_width')}")

    time.sleep(0.5)

    print("\n== close_gripper(width=0.0, no object) ==")
    out = s.call("close_gripper",
                 params={"width": 0.0, "speed": 0.05, "force": 10.0},
                 context=ctx)
    print(f"  success: {out['success']}, actual_width: {out['result'].get('actual_width')}, grasped: {out['result'].get('grasped')}")

    time.sleep(0.5)

    print("\n== open_gripper(width=0.08, fully open) ==")
    out = s.call("open_gripper",
                 params={"width": 0.08, "speed": 0.05},
                 context=ctx)
    print(f"  success: {out['success']}, actual_width: {out['result'].get('actual_width')}")

    # --- 4. Episodes ---
    recent = s.store.recent_episodes(limit=8)
    print(f"\n== recent episodes ({len(recent)}) ==")
    for ep in recent:
        success = "OK  " if ep["success"] else "FAIL"
        print(f"  {ep['id'][:8]}  skill={ep['skill_name']:<18} {success}")

    s.close()
    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
