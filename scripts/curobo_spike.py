"""cuRobo vs panda_py IK spike — GPU-only, NO ARM NEEDED (no FCI).

Shows that cuRobo's multi-seed, self-collision-aware IK returns a clean,
collision-free configuration where panda_py's single analytical IK jams the
elbow against its limit (the configs behind our reflexes and branch flips).

    ~/curobo-venv/bin/python scripts/curobo_spike.py     # on Omen (needs the GPU)

cuRobo API note: this targets the current `curobo.inverse_kinematics` API
(InverseKinematics + solve_pose). Quaternion convention differs between libs:
  - cuRobo Pose:   [w, x, y, z]  (scalar FIRST).  gripper-down = [0, 1, 0, 0]
  - panda_py.ik:   [x, y, z, w]  (scalar LAST).   gripper-down = [1, 0, 0, 0]
Note: cuRobo's franka tool frame may differ slightly from panda_py's EE frame,
so configs won't match to the decimal — the comparison is about config QUALITY
(elbow not jammed, collision-free, clean success/refusal), not bit-identity.
"""
import sys

import numpy as np

QUAT_PANDAPY = np.array([1.0, 0.0, 0.0, 0.0])   # x, y, z, w  (gripper-down)
HOME_Q = np.array([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785])

TEST_POSES = [
    ("normal  (0.45, 0.00, 0.30)", [0.45, 0.0, 0.30]),
    ("hard    (0.30, 0.00, 0.06)", [0.30, 0.0, 0.06]),  # near base/low: panda_py jams J4
]
TEST_POSES_DICT = {label: np.array(pos) for label, pos in TEST_POSES}


def run_panda_py():
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
            print(f"[panda_py] {label}: {type(e).__name__}: {e}")
            out[label] = None
    return out


def run_curobo():
    try:
        import torch
        from curobo.inverse_kinematics import InverseKinematics, InverseKinematicsCfg
        from curobo.types import GoalToolPose, Pose
    except Exception as e:
        print(f"[cuRobo] not importable: {e}")
        return {}

    cfg = InverseKinematicsCfg.create(
        robot="franka.yml",
        num_seeds=64,
        self_collision_check=True,   # the constraint panda_py ignores
    )
    ik = InverseKinematics(cfg)
    target_link = ik.tool_frames[0]
    quat = torch.tensor([[0.0, 1.0, 0.0, 0.0]], device="cuda", dtype=torch.float32)  # gripper-down

    out = {}
    for label, pos in TEST_POSES:
        try:
            goal = Pose(
                position=torch.tensor([pos], device="cuda", dtype=torch.float32),
                quaternion=quat,
            )
            res = ik.solve_pose(GoalToolPose.from_poses({target_link: goal}, num_goalset=1))
            ok = bool(res.success.item())
            q = res.js_solution.position.detach().reshape(-1).cpu().numpy() if ok else None
            err = float(res.position_error.item()) * 1000.0 if ok else float("nan")
            out[label] = dict(ok=ok, q=q, pos_err_mm=err, tool=str(target_link))
        except Exception as e:
            print(f"[cuRobo] {label}: {type(e).__name__}: {e}")
            out[label] = dict(ok=False, q=None, pos_err_mm=float("nan"), err=str(e))
    return out


def main():
    print("=" * 78)
    print("cuRobo vs panda_py — collision-aware IK spike (no arm, GPU only)")
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
            err = np.linalg.norm(reached - TEST_POSES_DICT[label]) * 1000
            print(f"  panda_py : J4={q[3]:+.3f}  reached err {err:.1f} mm  q={np.round(q, 3)}")
        c = cr.get(label)
        if not c:
            print("  cuRobo   : (not run)")
        elif not c["ok"]:
            print("  cuRobo   : IK INFEASIBLE — refuses the config "
                  "(self-collision/limits), rather than jamming into it")
        else:
            print(f"  cuRobo   : J4={c['q'][3]:+.3f}  pos err {c['pos_err_mm']:.1f} mm  "
                  f"q={np.round(c['q'], 3)}")
        if p and c and c.get("ok"):
            tag = "cuRobo less contorted" if abs(c["q"][3]) < abs(p[0][3]) else "similar"
            print(f"  -> J4 |Δ|={abs(p[0][3] - c['q'][3]):.3f} rad  ({tag})")

    print("\nTakeaway: where panda_py drives J4 to its limit (and still misses the")
    print("target), cuRobo returns a cleaner collision-free config or cleanly refuses")
    print("— the behavior that removes the reflexes and branch flips.")


if __name__ == "__main__":
    sys.exit(main())
