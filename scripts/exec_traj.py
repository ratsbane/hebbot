"""Stream a cuRobo trajectory to the arm via the JointPosition controller.
Usage: exec_traj.py X Y Z [speed]   (speed 1.0 = planned dt; <1 = slower)
EE-frame target, gripper-down. Needs FCI (stop the MCP server first)."""
import json, os, sys, urllib.request
import numpy as np
from panda_py import controllers
from hebbot.mcp_server import make_hardware_context

URL = os.environ.get("HEBBOT_CUROBO_URL", "http://127.0.0.1:8211")
X, Y, Z = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
SPEED = float(sys.argv[4]) if len(sys.argv) > 4 else 0.4

ctx = make_hardware_context(os.environ.get("HEBBOT_FRANKA", "192.168.0.253"),
                            no_robot=False, no_camera=True)
panda = ctx["panda"]
cur = list(panda.get_state().q)
hand = [X, Y, Z + 0.103]  # EE -> panda_hand offset, gripper-down
body = json.dumps({"position": hand, "quaternion": [0.0, 1.0, 0.0, 0.0], "start_q": cur}).encode()
res = json.loads(urllib.request.urlopen(
    urllib.request.Request(URL + "/plan", data=body,
                           headers={"Content-Type": "application/json"}), timeout=15).read())
if not res.get("ok"):
    print("PLAN FAILED"); sys.exit(1)
wps = [np.asarray(q, dtype=float) for q in res["waypoints"]]
dt = res["dt"] / SPEED
print(f"streaming {len(wps)} waypoints at dt={dt:.4f}s (~{len(wps)*dt:.1f}s, speed {SPEED})", flush=True)

ctrl = controllers.JointPosition()
panda.start_controller(ctrl)
max_tilt = 0.0
try:
    with panda.create_context(frequency=1.0 / dt, max_runtime=len(wps) * dt + 3.0) as cx:
        i = 0
        while cx.ok() and i < len(wps):
            ctrl.set_control(wps[i])
            z2 = float(np.array(panda.get_state().O_T_EE).reshape(4, 4).T[2, 2])  # EE z-axis .z
            max_tilt = max(max_tilt, np.degrees(np.arccos(np.clip(-z2, -1, 1))))
            i += 1
finally:
    panda.stop_controller()
T = np.array(panda.get_state().O_T_EE).reshape(4, 4).T
err = np.linalg.norm(T[:3, 3] - [X, Y, Z]) * 1000
print(f"DONE. final EE xyz={np.round(T[:3,3],4)}  err={err:.1f}mm  max_tilt_during={max_tilt:.2f}deg",
      flush=True)
