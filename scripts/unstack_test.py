"""One self-reset: run touch_unstack, report what memory did, photograph, home.

Usage: unstack_test.py <tag>   (outputs /tmp/unstack_<tag>.json and .jpg)
"""
import json, sys, time
from PIL import Image
from hebbot.mcp_server import make_hardware_context
from hebbot.substrate import Substrate
from hebbot.skills.primitives import _select_camera, move_to_joints, move_to_pose, open_gripper
from hebbot.skills.touch_stack import register_all
from hebbot.memory.scene import register_all as register_scene, scene_recall

tag = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%H%M%S")
pick_color = sys.argv[2] if len(sys.argv) > 2 else "blue"
base_color = sys.argv[3] if len(sys.argv) > 3 else "green"
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
ctx = make_hardware_context("192.168.0.253", False)
sub = Substrate("/home/doug/hebbot-data"); register_all(sub); register_scene(sub)

print("--- memory before ---", flush=True)
before = scene_recall(_store=sub.store)
print(before.get("summary") or before.get("note"), flush=True)

open_gripper(_context=ctx); move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)
t0 = time.time()
r = sub.call("touch_unstack", params={"pick_color": pick_color, "base_color": base_color}, context=ctx)
print(f"finished in {time.time()-t0:.0f}s; dispatcher success={r.get('success')}", flush=True)
json.dump(r, open(f"/tmp/unstack_{tag}.json", "w"), indent=1, default=str)
res = r.get("result", r)
keys = ("stage", "task_verified_success", "note", "n_probes", "elapsed_s", "believed",
        "pile_check_z", "dest_tried", "dest_xy", "expected_contact_z",
        "grasp", "place", "verify", "holding", "returned")
print(json.dumps({k: res.get(k) for k in keys}, default=str), flush=True)

print("--- memory after ---", flush=True)
after = scene_recall(_store=sub.store)
print(after.get("summary") or after.get("note"), flush=True)

open_gripper(_context=ctx)
move_to_pose([0.42, -0.05, 0.31], speed_factor=0.12, _context=ctx)
cam = _select_camera(ctx["cameras"], "wrist"); time.sleep(0.8)
for _ in range(6): cap = cam.capture(with_depth=True)
Image.fromarray(cap["color"][:, :, ::-1].copy()).save(f"/tmp/unstack_{tag}.jpg")
move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx); sub.close(); print("homed", flush=True)
