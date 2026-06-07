"""Demo: read live Franka state through the substrate.

Run:
    cd ~/hebbot && .venv/bin/python scripts/demo_live_state.py
optionally:
    HEBBOT_DATA=/tmp/hebbot-demo .venv/bin/python scripts/demo_live_state.py

Prerequisites:
    - panda-python installed (already in our venv via `uv pip install -e .[robot]`
      if not, run that)
    - Franka reachable at 192.168.0.253 (i.e. the franka-link Ethernet is up
      and FCI is active)

What this proves:
    - The same `s.call("get_state")` call returns mock data without a panda
      handle and live data with one.
    - The episode written to SQLite distinguishes them via outcome["result"]["_source"].
"""

from __future__ import annotations

import os
import sys

from hebbot import Substrate
from hebbot.skills.primitives import register_all


FRANKA_HOST = os.environ.get("HEBBOT_FRANKA", "192.168.0.253")
DATA_DIR = os.environ.get("HEBBOT_DATA", "~/hebbot-data")


def main() -> int:
    s = Substrate(DATA_DIR)
    register_all(s)

    print("== mock call (no panda in context) ==")
    outcome = s.call("get_state")
    print(f"  source: {outcome['result']['_source']}")
    print(f"  joints[0]: {outcome['result']['joints'][0]:.4f}")

    print(f"\n== connecting to Franka at {FRANKA_HOST} ==")
    try:
        import panda_py
    except ImportError:
        print("  panda-python not installed; install with: uv pip install -e .[robot]")
        return 1

    try:
        panda = panda_py.Panda(FRANKA_HOST)
        print(f"  connected.")
    except Exception as e:
        print(f"  FAILED to connect: {e}")
        return 2

    print("\n== live call (panda in context) ==")
    outcome = s.call("get_state", context={"panda": panda})
    if not outcome["success"]:
        print(f"  call failed: {outcome}")
        return 3
    result = outcome["result"]
    print(f"  source: {result['_source']}")
    print(f"  joints: {[f'{q:.3f}' for q in result['joints']]}")
    print(f"  ee position: {result['ee_pose'][3]:.3f}, {result['ee_pose'][7]:.3f}, {result['ee_pose'][11]:.3f}")

    recent = s.store.recent_episodes(skill_name="get_state", limit=5)
    print(f"\n== recent episodes ({len(recent)}) ==")
    for ep in recent[:5]:
        source = (ep.get("outcome") or {}).get("result", {}).get("_source", "?")
        success = "OK" if ep["success"] else "FAIL"
        print(f"  {ep['id'][:8]}  skill={ep['skill_name']:<15} {success:<4} source={source}")

    s.close()
    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
