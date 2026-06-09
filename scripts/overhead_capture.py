"""Move straight above a coarse (x,y), capture an overhead frame, emit T + paths.

Used for coarse->fine localization: a near-overhead close view has the least
top-face parallax, so re-localizing green from here should tighten the ~2 cm
open-loop residual. Keeps current (gripper-down) orientation. Substrate venv.
"""
import json
import os
import sys

from hebbot.substrate import Substrate
from hebbot.skills.primitives import register_all as reg_prim
from hebbot.skills.composed import register_all as reg_comp
from hebbot.mcp_server import make_hardware_context

X, Y = float(sys.argv[1]), float(sys.argv[2])
Z = float(sys.argv[3]) if len(sys.argv) > 3 else 0.22


def main():
    s = Substrate(os.environ.get("HEBBOT_DATA", "~/hebbot-data"))
    reg_prim(s); reg_comp(s)
    ctx = make_hardware_context(os.environ.get("HEBBOT_FRANKA", "192.168.0.253"),
                                no_robot=False, no_camera=False)

    def call(n, **p_): return s.call(n, params=p_, context=ctx)

    call("move_to_pose", position=[X, Y, Z], speed_factor=0.08)  # keep gripper-down
    st = call("get_state")["result"]
    cap = call("read_camera", with_depth=True)["result"]
    out = {"T": st["ee_pose"], "color": cap["color_path"], "depth": cap["depth_path"],
           "ee": [st["ee_pose"][3], st["ee_pose"][7], st["ee_pose"][11]]}
    print("CAPTURE " + json.dumps(out), flush=True)
    s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
