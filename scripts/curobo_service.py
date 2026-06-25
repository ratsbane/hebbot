"""cuRobo planning service — runs in curobo-venv, loads cuRobo ONCE and stays warm.

The MCP substrate (hebbot/.venv: panda_py, no torch) calls this over localhost
HTTP to get collision-aware IK configs, then executes them itself via panda_py.
Keeping cuRobo resident avoids paying its multi-second warmup on every move, and
keeps torch/cuRobo out of the substrate venv.

    ~/curobo-venv/bin/python scripts/curobo_service.py [--port 8211]

Endpoints:
    GET  /health           -> {ok, tool}
    POST /ik   {position:[x,y,z], quaternion:[w,x,y,z]} -> {ok, q:[7], pos_err_mm}

Quaternion is [w,x,y,z] (scalar-first, cuRobo convention); gripper-down = [0,1,0,0].
Returns only the 7 arm joints (cuRobo also reports 2 finger joints).
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
from curobo.inverse_kinematics import InverseKinematics, InverseKinematicsCfg
from curobo.types import GoalToolPose, JointState, Pose

# use_cuda_graph=False so the problem shape can vary per call (we seed with the
# caller's current_state for continuity, which changes the goal buffer shape).
_cfg = InverseKinematicsCfg.create(robot="franka.yml", num_seeds=64,
                                   self_collision_check=True, use_cuda_graph=False)
_IK = InverseKinematics(_cfg)
_TOOL = _IK.tool_frames[0]
_JN = getattr(_IK, "joint_names", None) or _IK.kinematics.joint_names


def solve_ik(position, quat_wxyz, start_q=None):
    """Collision-aware IK for `panda_hand` at the given pose. When start_q (7 arm
    joints) is given, seed toward it and return the nearest collision-free
    solution (continuity), instead of the global optimum which can be a big swing.
    """
    goal = Pose(
        position=torch.tensor([position], device="cuda", dtype=torch.float32),
        quaternion=torch.tensor([quat_wxyz], device="cuda", dtype=torch.float32),
    )
    gt = GoalToolPose.from_poses({_TOOL: goal}, num_goalset=1)
    if start_q is not None:
        cs = JointState.from_position(
            torch.tensor([start_q], device="cuda", dtype=torch.float32), joint_names=_JN)
        res = _IK.solve_pose(gt, current_state=cs, return_seeds=8)
    else:
        res = _IK.solve_pose(gt)

    P = res.js_solution.position.detach().reshape(-1, len(_JN) + 2).cpu().numpy()[:, :7]
    S = res.success.detach().reshape(-1).cpu().numpy().astype(bool)
    if not S.any():
        return {"ok": False}
    idx_ok = np.where(S)[0]
    if start_q is not None:
        d = np.linalg.norm(P[idx_ok] - np.array(start_q), axis=1)
        best = int(idx_ok[int(np.argmin(d))])
    else:
        best = int(idx_ok[0])
    perr = res.position_error.detach().reshape(-1).cpu().numpy()
    return {
        "ok": True,
        "q": [float(v) for v in P[best]],
        "pos_err_mm": float(perr[best]) * 1000.0 if best < len(perr) else None,
        "dist_to_start": (float(np.linalg.norm(P[best] - np.array(start_q)))
                          if start_q is not None else None),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "tool": str(_TOOL)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": f"bad json: {e}"})
        if self.path == "/ik":
            try:
                pos = [float(v) for v in body["position"]]
                quat = [float(v) for v in body.get("quaternion", [0.0, 1.0, 0.0, 0.0])]
                sq = body.get("start_q")
                sq = [float(v) for v in sq] if sq else None
                self._send(200, solve_ik(pos, quat, sq))
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):  # keep the console quiet
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8211)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    solve_ik([0.45, 0.0, 0.30], [0.0, 1.0, 0.0, 0.0])  # warm the CUDA graph
    print(f"cuRobo service ready on {args.host}:{args.port} (tool={_TOOL})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
