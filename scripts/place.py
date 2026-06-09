"""Place the held block back on the table near (x,y), gripper-down. Substrate venv."""
import os, sys
from hebbot.substrate import Substrate
from hebbot.skills.primitives import register_all as reg_prim
from hebbot.skills.composed import register_all as reg_comp
from hebbot.mcp_server import make_hardware_context

X, Y = float(sys.argv[1]), float(sys.argv[2])
s = Substrate(os.environ.get("HEBBOT_DATA", "~/hebbot-data"))
reg_prim(s); reg_comp(s)
ctx = make_hardware_context(os.environ.get("HEBBOT_FRANKA", "192.168.0.253"),
                            no_robot=False, no_camera=True)
def call(n, **p): return s.call(n, params=p, context=ctx)
call("move_to_pose", position=[X, Y, 0.06], orientation=[0.0, 1.0, 0.0, 0.0], speed_factor=0.06)
call("open_gripper", width=0.08)
call("move_to_pose", position=[X, Y, 0.20], speed_factor=0.06)
w = call("get_state")["result"]["gripper_width"]
print(f"placed, lifted; gripper_width={w:.4f}", flush=True)
s.close()
