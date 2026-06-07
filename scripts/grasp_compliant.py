"""Compliant-descent grasp at the calibrated block position (substrate venv).

The stiff position-control descent tripped a cartesian_reflex when a fingertip
grazed the block top, aborting high and closing on air. Here the descent uses
Cartesian impedance with SOFT vertical stiffness (firm xy + orientation), so a
light graze deflects instead of aborting and the fingers reach straddle height.
"""
import json
import os
import sys

import numpy as np

from hebbot.substrate import Substrate
from hebbot.skills.primitives import register_all as reg_prim
from hebbot.skills.composed import register_all as reg_comp
from hebbot.mcp_server import make_hardware_context
from panda_py import controllers

p = json.load(open("/tmp/calib_result.json"))["p"]
X, Y = p[0], p[1]
GRASP_Z = 0.038      # EE z target (~where the teleop grab held the block)
DESCENT_SPEED = 0.04  # m/s


def main():
    s = Substrate(os.environ.get("HEBBOT_DATA", "~/hebbot-data"))
    reg_prim(s); reg_comp(s)
    ctx = make_hardware_context(os.environ.get("HEBBOT_FRANKA", "192.168.0.253"),
                                no_robot=False, no_camera=False)
    panda = ctx.get("panda")
    if panda is None:
        print("no panda"); return 1

    def call(n, **p_): return s.call(n, params=p_, context=ctx)

    print(f"target ({X:.3f},{Y:.3f}), grasp_z={GRASP_Z}", flush=True)
    call("open_gripper", width=0.08)
    call("move_to_pose", position=[X, Y, 0.15], speed_factor=0.06)  # hover (stiff, safe)

    # --- compliant descent ---
    quat = panda.get_orientation()          # gripper-down (scalar-last)
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    # firm in all translation (reach the target reliably) + firm orientation;
    # nullspace ~0 so it doesn't hold the arm up and fight the descent. Still
    # impedance, so contact stays force-limited (won't hard-reflex like position
    # control did).
    ctrl.set_impedance(np.diag([400.0, 400.0, 300.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    tz = float(panda.get_position()[2])
    settle = 0
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            while cx.ok():
                tz = max(GRASP_Z, tz - DESCENT_SPEED * dt)
                ctrl.set_control(np.array([X, Y, tz]), quat, q0)
                if tz <= GRASP_Z + 1e-4:
                    settle += 1
                    if settle > 60:   # ~0.6 s to settle at target
                        break
    finally:
        panda.stop_controller()
    actual_z = float(panda.get_position()[2])
    print(f"descended to EE z={actual_z:.4f} (target {GRASP_Z})", flush=True)
    if actual_z > 0.055:
        print(f"STALLED HIGH at {actual_z:.4f} — not closing (would grab air). "
              f"Lifting.", flush=True)
        call("move_to_pose", position=[X, Y, 0.20], speed_factor=0.06)
        s.close()
        return 2

    g = call("close_gripper", width=0.024, epsilon_inner=0.018, epsilon_outer=0.018, force=20)
    print(f"grasp: success={g['success']} grasped={g['result'].get('grasped')} "
          f"width={g['result'].get('actual_width'):.4f}", flush=True)

    call("move_to_pose", position=[X, Y, 0.20], speed_factor=0.06)
    w = call("get_state")["result"]["gripper_width"]
    print(f"after lift: gripper_width={w:.4f}  -> {'HOLDING' if w > 0.005 else 'EMPTY'}", flush=True)
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
