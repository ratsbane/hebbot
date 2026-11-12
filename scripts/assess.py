"""Assess the bench after f3: one look, extra probes across blue, and a photo.

Usage: assess.py <tag>   (outputs /tmp/assess_<tag>.json and .jpg)

f3 left blue probing 64.3 mm at its centre: taller than flat, shorter than on-end,
resting on nothing memory knows. Before any more grasps, measure the shape of that
mystery: the look refreshes both beliefs, then a short probe line across blue's short
axis says whether it is flat, tilted, or propped, and the photo shows against what.
"""
import json
import sys
import time

from PIL import Image

from hebbot.mcp_server import make_hardware_context
from hebbot.substrate import Substrate
from hebbot.skills.primitives import _select_camera, move_to_joints, move_to_pose, open_gripper, probe_table
from hebbot.skills.touch_stack import register_all, PROBE_OPEN_WIDTH, PROBE_Z_FLOOR, PROBE_FORCE_OFF_N
from hebbot.memory.scene import register_all as register_scene, scene_recall

import math

tag = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%H%M%S")
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
ctx = make_hardware_context("192.168.0.253", False)
sub = Substrate("/home/doug/hebbot-data")
register_all(sub)
register_scene(sub)

print("--- memory before ---", flush=True)
print(scene_recall(_store=sub.store).get("summary"), flush=True)

open_gripper(_context=ctx)
move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)

r = sub.call("touch_look", params={"colors": ["blue", "green"]}, context=ctx)
res = r.get("result", r)
print(json.dumps({k: res.get(k) for k in ("stage", "task_verified_success", "found",
                                          "table_z", "n_probes", "elapsed_s")},
                 default=str), flush=True)

# A probe line across blue's believed short axis: a flat block answers one height on
# the body and table off it; a tilted one answers a ramp.
blue = (scene_recall(object="blue", _store=sub.store).get("belief") or {})
line = []
if blue.get("xy"):
    bx, by = blue["xy"]
    yaw = float(blue.get("yaw_deg") or 0.0)
    a = math.radians(yaw + 90.0)   # across the short axis
    for off in (-0.030, -0.015, 0.0, 0.015, 0.030):
        x, y = bx + off * math.cos(a), by + off * math.sin(a)
        p = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH, z_floor=PROBE_Z_FLOOR,
                        force_thresh=PROBE_FORCE_OFF_N, approach_z=0.12, retreat_z=0.12,
                        _context=ctx)
        line.append({"off_mm": off * 1000, "xy": [round(x, 4), round(y, 4)],
                     "contact_z": p.get("contact_z"), "trip": p.get("trip")})
        print(json.dumps(line[-1], default=str), flush=True)

json.dump({"look": res, "line": line}, open(f"/tmp/assess_{tag}.json", "w"),
          indent=1, default=str)

# Photo from above the corner, framing blue with green in view.
open_gripper(_context=ctx)
move_to_pose([0.41, -0.19, 0.31], speed_factor=0.12, _context=ctx)
cam = _select_camera(ctx["cameras"], "wrist")
time.sleep(0.8)
for _ in range(6):
    cap = cam.capture(with_depth=True)
Image.fromarray(cap["color"][:, :, ::-1].copy()).save(f"/tmp/assess_{tag}.jpg")

print("--- memory after ---", flush=True)
print(scene_recall(_store=sub.store).get("summary"), flush=True)
move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)
sub.close()
print("homed", flush=True)
