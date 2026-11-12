"""Measure the finger-line lateral offset as a function of wrist yaw.

Usage: measure_yaw.py <tag>   (outputs /tmp/yaw_<tag>.json)

The campaign's verdict: 6/6 grasp failures at wrist_quaternion arguments 74-86 deg,
~37/37 successes near 0, camera exonerated to ~5 mm by a probe line. Hypothesis: the
fingers' closing line sits laterally off the commanded EE axis, the camera map's b
absorbed it at fit orientation, and rotating the wrist rotates it back out.

Method: bracket blue's edges along world x and world y with guarded descents
(_lower_until_contact — the same contact logic every place uses), with the hand at
wrist_quaternion(0), (-75) [the failure configuration], and (90). The chord midpoint
is invariant to the probe footprint (dilation extends both ends equally) and to the
block's own pose, so midpoint shift between yaw settings = the finger-line offset
projection. Yaw 0 is measured twice, first and last: light blocks can turn when
probed near edges (#160), and the repeat is the drift control.
"""
import json
import math
import sys

from hebbot.mcp_server import make_hardware_context
from hebbot.substrate import Substrate
from hebbot.skills.primitives import _extract, move_to_joints, open_gripper, probe_table
from hebbot.skills.touch_stack import (
    DEFAULT_TABLE_Z, PROBE_FORCE_OFF_N, PROBE_OPEN_WIDTH, PROBE_Z_FLOOR,
    wrist_quaternion,
)
from hebbot.memory.scene import scene_recall

tag = sys.argv[1] if len(sys.argv) > 1 else "y1"
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
HOVER = 0.09
ON_BLOCK_Z = DEFAULT_TABLE_Z + 0.010
YAWS = [("a0", 0.0), ("b-75", -75.0), ("c90", 90.0), ("d0_again", 0.0)]

ctx = make_hardware_context("192.168.0.253", False)
panda, gripper = _extract(ctx, "panda"), _extract(ctx, "gripper")
sub = Substrate("/home/doug/hebbot-data")

print("--- memory before ---", flush=True)
before = scene_recall(_store=sub.store)
print(before.get("summary"), flush=True)
blue = next(b for b in before["beliefs"] if b["name"] == "blue")
bx, by = blue["xy"]
print(f"blue believed at ({bx:.4f}, {by:.4f}) "
      f"±{(blue.get('precision_m') or 0) * 1000:.0f} mm", flush=True)

open_gripper(_context=ctx)
move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)
probes = []


def probe(x, y, quat):
    # The proven probe, rotated: probe_table takes an orientation, and the touch
    # skills' own settings (force trigger off at 30 N; position-lag detection).
    # The first draft rebuilt this from the placement detector and its 4 N force
    # trigger false-tripped in free air over the long descent (Doug's eyes, not
    # the harness, caught it).
    r = probe_table(x=x, y=y, orientation=list(quat), open_width=PROBE_OPEN_WIDTH,
                    z_floor=PROBE_Z_FLOOR, force_thresh=PROBE_FORCE_OFF_N,
                    approach_z=HOVER, retreat_z=HOVER, _context=ctx)
    z = None if r.get("trip") == "timeout" else r.get("contact_z")
    probes.append({"x": round(x, 4), "y": round(y, 4), "trip": r.get("trip"),
                   "contact_z": None if z is None else round(float(z), 4)})
    return z


def on_block(x, y, quat):
    z = probe(x, y, quat)
    if z is None:            # a timeout: one retry, then believe the retry
        z = probe(x, y, quat)
    return z is not None and z > ON_BLOCK_Z


def edge(quat, ux, uy):
    """Walk outward from the centre until off the block, then bisect: edge radius."""
    last_in, first_out, r = 0.0, None, 0.015
    while r <= 0.065:
        if on_block(cx + ux * r, cy + uy * r, quat):
            last_in, r = r, r + 0.015
        else:
            first_out = r
            break
    if first_out is None:
        return None
    for _ in range(3):
        mid = (last_in + first_out) / 2
        if on_block(cx + ux * mid, cy + uy * mid, quat):
            last_in = mid
        else:
            first_out = mid
    return (last_in + first_out) / 2


# The belief is ±40 mm after y2's herding (the block is taped now), so find the
# block before measuring: a cross search for first contact, then rough extents
# at yaw 0 to estimate the true centre every leg will measure from.
def rough_on(x, y):
    z = probe(x, y, wrist_quaternion(0.0))
    return z is not None and z > ON_BLOCK_Z


hit = None
for dx, dy in ((0, 0), (0.015, 0), (-0.015, 0), (0, 0.015), (0, -0.015),
               (0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03),
               (0.03, 0.03), (-0.03, -0.03), (0.03, -0.03), (-0.03, 0.03)):
    if rough_on(bx + dx, by + dy):
        hit = (bx + dx, by + dy)
        break
if hit is None:
    open_gripper(_context=ctx)
    move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)
    sub.close()
    sys.exit("no block found near the belief; stopping for a human")
ext = {}
for lbl, (ux, uy) in (("px", (1, 0)), ("mx", (-1, 0)), ("py", (0, 1)), ("my", (0, -1))):
    r = 0.0
    while r < 0.075 and rough_on(hit[0] + ux * (r + 0.015), hit[1] + uy * (r + 0.015)):
        r += 0.015
    ext[lbl] = r
cx = hit[0] + (ext["px"] - ext["mx"]) / 2
cy = hit[1] + (ext["py"] - ext["my"]) / 2
print(f"measuring from centre ({cx:.4f}, {cy:.4f}), "
      f"{len(probes)} probes spent finding it", flush=True)

results = {}
for label, yaw in YAWS:
    quat = wrist_quaternion(yaw)
    n0 = len(probes)
    if not on_block(cx, cy, quat):
        print(f"{label}: centre probe found no block; stopping for a human.", flush=True)
        break
    axes = {}
    for axis, (ux, uy) in (("x", (1.0, 0.0)), ("y", (0.0, 1.0))):
        plus, minus = edge(quat, ux, uy), edge(quat, -ux, -uy)
        axes[axis] = {
            "edge_plus": plus, "edge_minus": minus,
            "midpoint_offset_mm": None if None in (plus, minus)
            else round((plus - minus) / 2 * 1000, 2),
            "chord_mm": None if None in (plus, minus)
            else round((plus + minus) * 1000, 1),
        }
    results[label] = {"yaw_arg": yaw, "axes": axes, "n_probes": len(probes) - n0}
    print(json.dumps({label: results[label]}, default=str), flush=True)

summary = {}
base = results.get("a0", {}).get("axes", {})
for label, r in results.items():
    summary[label] = {
        axis: None if (base.get(axis, {}).get("midpoint_offset_mm") is None
                       or r["axes"][axis]["midpoint_offset_mm"] is None)
        else round(r["axes"][axis]["midpoint_offset_mm"]
                   - base[axis]["midpoint_offset_mm"], 2)
        for axis in ("x", "y")
    }
print("--- offsets relative to first yaw-0 (mm) ---", flush=True)
print(json.dumps(summary, default=str), flush=True)

json.dump({"blue_xy": [cx, cy], "results": results, "offsets_mm": summary,
           "probes": probes}, open(f"/tmp/yaw_{tag}.json", "w"), indent=1)
open_gripper(_context=ctx)
move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)
sub.close()
print(f"{len(probes)} probes; homed; /tmp/yaw_{tag}.json", flush=True)
