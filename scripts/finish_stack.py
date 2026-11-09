"""Stack the two blocks already located by touch, with a narrow pre-open.

The last attempt failed with grip_high 17.0 mm - the fingers landed on the block top rather
than beside it. The two blocks are 78 mm apart and grasp_at's default pre-open is the full
80 mm, so a finger comes down on the neighbour. #141 already prescribed the fix ("a narrow
symmetric pre-open, just wider than the block, not the full 80 mm"); it was never reachable
through MCP because open_width is missing from the tool schema.
"""
import time
import numpy as np
from hebbot.mcp_server import make_hardware_context
from hebbot.skills.primitives import (grasp_at, move_to_joints, move_to_pose,
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

def refine(ctx, centre, label):
    good = []
    for dx in (-0.011, 0.0, 0.011):
        for dy in (-0.011, 0.0, 0.011):
            c = probe(ctx, centre[0]+dx, centre[1]+dy)
            if c is not None and c > TABLE_Z + OBJ_MM/1000.0:
                good.append((centre[0]+dx, centre[1]+dy, c))
    if not good:
        say(f"  {label}: nothing felt"); return None
    x = float(np.mean([g[0] for g in good])); y = float(np.mean([g[1] for g in good]))
    top = float(np.median([g[2] for g in good]))
    say(f"  {label}: ({x:.4f},{y:+.4f}) top={top:.4f} h={1000*(top-TABLE_Z):.1f}mm  {len(good)} hits")
    return {"xy": (x, y), "top": top}

ctx = make_hardware_context("192.168.0.253", no_robot=False)
open_gripper(_context=ctx); move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)

base = refine(ctx, BASE, "BASE")
move = refine(ctx, MOVE, "MOVE")
if base and move:
    sep = 1000*np.hypot(base["xy"][0]-move["xy"][0], base["xy"][1]-move["xy"][1])
    say(f"  separation {sep:.0f} mm -> pre-open 45 mm so no finger reaches the neighbour")
    x, y = move["xy"]
    move_to_pose([x, y, 0.12], speed_factor=0.08, _context=ctx)
    g = grasp_at(grasp_z=TABLE_Z+0.010, width=0.024, open_width=0.045,
                 descent_speed=0.02, _context=ctx)
    say(f"  grasp: {g.get('grasped')} width={g.get('actual_width')} high={g.get('grip_high_mm')}")
    if g.get("grasped"):
        bx, by = base["xy"]; pz = base["top"] + 0.013
        move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
        move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
        move_to_pose([bx, by, pz], speed_factor=0.02, _context=ctx)
        open_gripper(_context=ctx)
        move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
        say(f"  placed at ({bx:.4f},{by:+.4f}) z={pz:.4f}")
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
