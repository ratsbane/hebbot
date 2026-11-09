"""Measure the block's long axis BY TOUCH, yaw the wrist across it, then stack.

grip_high 17.7 mm with a 45 mm pre-open means the fingers are landing on the block, not
beside it: at +/-22.5 mm they sit inside a 55 mm-long footprint. So the wrist must close
across the short axis, and the orientation can be felt rather than seen - probe a cross and
compare the extent in each direction.
"""
import math, time
import numpy as np
from hebbot.mcp_server import make_hardware_context
from hebbot.skills.primitives import (_qmul, grasp_at, move_to_joints, move_to_pose,
                                      open_gripper, probe_table)

TABLE_Z, OBJ_MM = 0.0177, 10.0
HOME_Q = [0.0,-0.785,0.0,-2.356,0.0,1.571,0.785]
LIFT_Z = 0.22
BASE = (0.5000, -0.0200)
MOVE = (0.5273, -0.0933)

def say(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def probe(ctx, x, y):
    r = probe_table(x=float(x), y=float(y), open_width=0.008, z_floor=0.012, _context=ctx)
    c = r.get("contact_z")
    return float(c) if c is not None else None

def on_object(ctx, x, y):
    c = probe(ctx, x, y)
    return c is not None and c > TABLE_Z + OBJ_MM/1000.0, c

def extent(ctx, centre, axis):
    """How far the object reaches from centre along +/- one axis (metres)."""
    reach = 0.0
    for step in (0.010, 0.020, 0.030):
        for sign in (1, -1):
            dx, dy = ((step*sign, 0.0) if axis == "x" else (0.0, step*sign))
            hit, _ = on_object(ctx, centre[0]+dx, centre[1]+dy)
            if hit:
                reach = max(reach, step)
    return reach

ctx = make_hardware_context("192.168.0.253", no_robot=False)
open_gripper(_context=ctx); move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)

say("measuring the block's long axis by touch")
ex = extent(ctx, MOVE, "x")
ey = extent(ctx, MOVE, "y")
say(f"  reach along x = {ex*1000:.0f} mm, along y = {ey*1000:.0f} mm")

# At zero yaw the fingers separate along world Y (verified by the successful grasp of
# 2026-09-01, whose block lay with its long axis along world X).
if ex > ey:
    yaw_deg = 0.0; say("  long axis is X -> fingers close along Y -> yaw 0")
else:
    yaw_deg = 90.0; say("  long axis is Y -> fingers close along X -> yaw 90")

yaw = math.radians(yaw_deg)
quat = _qmul([0.0,1.0,0.0,0.0], [math.cos(yaw/2), 0.0, 0.0, math.sin(yaw/2)])
say(f"  orientation {[round(v,4) for v in quat]}")

x, y = MOVE
move_to_pose([x, y, 0.12], orientation=list(quat), speed_factor=0.06, _context=ctx)
g = grasp_at(grasp_z=TABLE_Z+0.010, width=0.024, open_width=0.048,
             descent_speed=0.02, _context=ctx)
say(f"  grasp: {g.get('grasped')} width={g.get('actual_width')} high={g.get('grip_high_mm')}")

if g.get("grasped"):
    bx, by = BASE
    top = 0.0450
    pz = top + 0.013
    move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([bx, by, pz], speed_factor=0.02, _context=ctx)
    open_gripper(_context=ctx)
    move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
    say(f"  placed on base at ({bx:.4f},{by:+.4f}) z={pz:.4f}")
    best = None
    for dx in (-0.010, 0.0, 0.010):
        for dy in (-0.010, 0.0, 0.010):
            c = probe(ctx, bx+dx, by+dy)
            if c is not None and (best is None or c > best): best = c
    h = 1000*(best-TABLE_Z) if best else 0
    say(f"VERIFY pile height {h:.1f} mm (one ~27, two ~54)")
    say("*** STACKED ***" if h > 45 else "not stacked")
else:
    open_gripper(_context=ctx)

open_gripper(_context=ctx); move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)
say("done")
