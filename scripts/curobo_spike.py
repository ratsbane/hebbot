"""cuRobo vs panda_py IK/planning spike — GPU-only, NO ARM NEEDED (no FCI).

Purpose: prove cuRobo gives collision-free, well-conditioned configs where panda_py's
single analytical IK returns the contorted / self-colliding configs that have caused our
reflexes and branch flips. Run this on Omen (needs the GPU) BEFORE wiring cuRobo into the
substrate; the Panda does not need to be powered on.

    /home/doug/<venv-with-curobo>/bin/python scripts/curobo_spike.py

⚠️ UNTESTED — written while Omen was offline. cuRobo's API shifts between releases; if an
import or method name is off, check it against the installed version's docs
(https://curobo.org) and adjust. The structure and the comparison logic are the point.

Quaternion conventions (easy to get wrong):
  - cuRobo Pose:   [w, x, y, z]  (scalar FIRST).  gripper-down = [0, 1, 0, 0]
  - panda_py.ik:   [x, y, z, w]  (scalar LAST).   gripper-down = [1, 0, 0, 0]
"""
import sys

import numpy as np

# Gripper-down orientation in each library's convention.
QUAT_CUROBO = [0.0, 1.0, 0.0, 0.0]   # w, x, y, z
QUAT_PANDAPY = np.array([1.0, 0.0, 0.0, 0.0])  # x, y, z, w
HOME_Q = np.array([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785])

# Test poses (base frame, metres). "hard" is the near-base spot where panda_py contorted
# the elbow to J4 ~ -3.0 and tripped a self-collision reflex (the blue touch-off).
TEST_POSES = [
    ("normal  (0.45, 0.00, 0.30)", [0.45, 0.0, 0.30]),
    ("hard    (0.30, 0.00, 0.06)", [0.30, 0.0, 0.06]),
]
TEST_POSES_DICT = {label: np.array(pos) for label, pos in TEST_POSES}


def run_panda_py():
    """Return {label: q(7,)} from panda_py's analytical IK, or None if unavailable."""
    try:
        import panda_py
    except Exception as e:
        print(f"[panda_py] not importable: {e}")
        return {}
    out = {}
    for label, pos in TEST_POSES:
        try:
            q = panda_py.ik(np.array(pos), QUAT_PANDAPY, HOME_Q, 0.785)
            if q is None or np.any(np.isnan(q)):
                out[label] = None
            else:
                reached = panda_py.fk(np.asarray(q))[:3, 3]
                out[label] = (np.asarray(q), np.asarray(reached))
        except Exception as e:
            print(f"[panda_py] {label}: raised {type(e).__name__}: {e}")
            out[label] = None
    return out


def run_curobo():
    """Return {label: (q, pos_err_mm, ok)} from cuRobo's collision-aware IK + a plan check."""
    try:
        import torch
        from curobo.types.base import TensorDeviceType
        from curobo.types.math import Pose
        from curobo.types.state import JointState
        from curobo.geom.types import WorldConfig, Cuboid
        from curobo.util_file import get_robot_configs_path, join_path, load_yaml
        from curobo.wrap.reacher.ik_solver import IKSolver, IKSolverConfig
        from curobo.wrap.reacher.motion_gen import (
            MotionGen, MotionGenConfig, MotionGenPlanConfig,
        )
    except Exception as e:
        print(f"[cuRobo] not importable: {e}\n"
              f"         install per https://curobo.org/get_started/1_install_instructions.html")
        return {}

    tensor_args = TensorDeviceType()
    robot_cfg = load_yaml(join_path(get_robot_configs_path(), "franka.yml"))["robot_cfg"]

    # World: a table slab with its top surface at z = 0 (the arm must not plan through it).
    world_cfg = WorldConfig(cuboid=[
        Cuboid(name="table", pose=[0.45, 0.0, -0.05, 1.0, 0.0, 0.0, 0.0], dims=[1.2, 1.2, 0.1]),
    ])

    # --- collision-aware IK ---
    ik_cfg = IKSolverConfig.load_from_robot_config(
        robot_cfg, world_cfg,
        position_threshold=0.005, rotation_threshold=0.05,
        num_seeds=30, self_collision_check=True, self_collision_opt=True,
        tensor_args=tensor_args, use_cuda_graph=True,
    )
    ik = IKSolver(ik_cfg)

    # --- motion planner (to show a full collision-free trajectory succeeds/fails) ---
    mg_cfg = MotionGenConfig.load_from_robot_config(
        robot_cfg, world_cfg, tensor_args, interpolation_dt=0.02,
    )
    motion_gen = MotionGen(mg_cfg)
    print("[cuRobo] warming up (CUDA graph capture)...")
    motion_gen.warmup()
    joint_names = motion_gen.kinematics.joint_names
    start = JointState.from_position(
        tensor_args.to_device(HOME_Q.reshape(1, -1)), joint_names=joint_names)

    out = {}
    for label, pos in TEST_POSES:
        goal = Pose.from_list(pos + QUAT_CUROBO)  # [x,y,z, w,x,y,z]
        ik_res = ik.solve_single(goal)
        ok = bool(ik_res.success.item())
        q = ik_res.solution.view(-1).cpu().numpy() if ok else None
        pos_err = float(ik_res.position_error.item()) * 1000.0 if ok else float("nan")

        plan = motion_gen.plan_single(start, goal, MotionGenPlanConfig(max_attempts=5))
        plan_ok = bool(plan.success.item())
        n_wp = plan.get_interpolated_plan().position.shape[0] if plan_ok else 0
        out[label] = dict(ok=ok, q=q, pos_err_mm=pos_err, plan_ok=plan_ok, n_waypoints=n_wp)
    return out


def main():
    print("=" * 78)
    print("cuRobo vs panda_py — IK / planning spike (no arm, GPU only)")
    print("=" * 78)
    pp = run_panda_py()
    cr = run_curobo()

    for label, _ in TEST_POSES:
        print(f"\n### {label}")
        p = pp.get(label)
        if p is None:
            print("  panda_py : NO SOLUTION / NaN")
        elif p:
            q, reached = p
            print(f"  panda_py : J4={q[3]:+.3f}  q={np.round(q, 3)}")
            print(f"             reached xyz={np.round(reached, 3)} "
                  f"(err {np.linalg.norm(reached - TEST_POSES_DICT[label]) * 1000:.1f} mm)")
        c = cr.get(label)
        if not c:
            print("  cuRobo   : (not run)")
        elif not c["ok"]:
            print("  cuRobo   : IK reported INFEASIBLE (likely collision/limits) — "
                  "i.e. it refuses the config panda_py would have forced")
        else:
            print(f"  cuRobo   : J4={c['q'][3]:+.3f}  q={np.round(c['q'], 3)}  "
                  f"(pos err {c['pos_err_mm']:.1f} mm)")
            print(f"             collision-free plan: {c['plan_ok']} "
                  f"({c['n_waypoints']} waypoints)")
        if p and c and c.get("ok"):
            dj4 = abs(p[0][3] - c["q"][3])
            print(f"  -> J4 differs by {dj4:.3f} rad "
                  f"({'cuRobo less contorted' if abs(c['q'][3]) < abs(p[0][3]) else 'similar'})")

    print("\nTakeaway: where panda_py drives the elbow to its limit (or self-collides),")
    print("cuRobo either returns a cleaner collision-free config or cleanly refuses —")
    print("exactly the behavior that removes the reflexes and branch flips.")


if __name__ == "__main__":
    sys.exit(main())
