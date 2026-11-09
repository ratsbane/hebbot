"""Find every block by touch alone, then stack two of them. No camera, no calibration.

The camera->hand model is unreliable outside the small region it was fitted in, so this
does not use it. The fingertip capture width (block ~28 mm + tip ~20 mm) means a 40 mm
grid is exhaustive: any block whose centre lies in the scanned area will be hit.
"""
import sys, time
import numpy as np
from hebbot.mcp_server import make_hardware_context
from hebbot.skills.primitives import (grasp_at, move_to_joints, move_to_pose,
                                      open_gripper, probe_table)

TABLE_Z, OBJ_MM = 0.0177, 10.0
HOME_Q = [0.0,-0.785,0.0,-2.356,0.0,1.571,0.785]
LIFT_Z = 0.22

def say(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def home(ctx):
    open_gripper(_context=ctx); move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)

def probe(ctx, x, y):
    r = probe_table(x=float(x), y=float(y), open_width=0.008, z_floor=0.012, _context=ctx)
    c = r.get("contact_z")
    return float(c) if c is not None else None

def main():
    ctx = make_hardware_context("192.168.0.253", no_robot=False)
    if "panda" not in ctx: say("no arm"); return 1
    home(ctx)
    xs = np.arange(0.360, 0.561, 0.040)
    ys = np.arange(-0.200, 0.081, 0.040)
    say(f"scanning {len(xs)}x{len(ys)} = {len(xs)*len(ys)} points at 40 mm")
    hits = []
    for i, x in enumerate(xs):
        row = ys if i % 2 == 0 else ys[::-1]      # boustrophedon: less travel
        for y in row:
            c = probe(ctx, x, y)
            if c is None: continue
            if c > TABLE_Z + OBJ_MM/1000.0:
                hits.append((float(x), float(y), c))
                say(f"  HIT ({x:.3f},{y:+.3f}) z={c:.4f}  h={1000*(c-TABLE_Z):.1f}mm")
    if len(hits) < 2:
        say(f"only {len(hits)} hits; cannot stack"); home(ctx); return 2

    # cluster hits that are within one block of each other
    clusters = []
    for h in hits:
        for cl in clusters:
            if any(np.hypot(h[0]-g[0], h[1]-g[1]) <= 0.050 for g in cl):
                cl.append(h); break
        else:
            clusters.append([h])
    clusters.sort(key=len, reverse=True)
    say(f"{len(clusters)} object(s): " + ", ".join(
        f"({np.mean([g[0] for g in c]):.3f},{np.mean([g[1] for g in c]):+.3f})x{len(c)}"
        for c in clusters))
    if len(clusters) < 2:
        say("need two separate objects"); home(ctx); return 3

    def refine(cl, label):
        cx = float(np.mean([g[0] for g in cl])); cy = float(np.mean([g[1] for g in cl]))
        pts = [(cx+dx, cy+dy) for dx in (-0.012,0,0.012) for dy in (-0.012,0,0.012)]
        good = []
        for x,y in pts:
            c = probe(ctx, x, y)
            if c is not None and c > TABLE_Z + OBJ_MM/1000.0:
                good.append((x,y,c))
        if not good: good = cl
        rx = float(np.mean([g[0] for g in good])); ry = float(np.mean([g[1] for g in good]))
        top = float(np.median([g[2] for g in good]))
        say(f"  {label}: centre ({rx:.4f},{ry:+.4f}) top={top:.4f} h={1000*(top-TABLE_Z):.1f}mm")
        return {"xy": (rx, ry), "top": top}

    base = refine(clusters[0], "BASE")
    move = refine(clusters[1], "MOVE")

    x, y = move["xy"]
    move_to_pose([x, y, 0.12], speed_factor=0.08, _context=ctx)
    g = grasp_at(grasp_z=TABLE_Z+0.010, width=0.024, descent_speed=0.02, _context=ctx)
    say(f"  grasp: {g.get('grasped')} width={g.get('actual_width')} high={g.get('grip_high_mm')}")
    if not g.get("grasped"):
        open_gripper(_context=ctx); home(ctx); return 4

    bx, by = base["xy"]; pz = base["top"] + 0.013
    move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([bx, by, pz], speed_factor=0.025, _context=ctx)
    open_gripper(_context=ctx)
    move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
    say(f"  placed at ({bx:.4f},{by:+.4f}) z={pz:.4f}")

    best = None
    for dx in (-0.010, 0.0, 0.010):
        for dy in (-0.010, 0.0, 0.010):
            c = probe(ctx, bx+dx, by+dy)
            if c is not None and (best is None or c > best): best = c
    h = 1000*(best-TABLE_Z) if best else None
    say(f"VERIFY pile height {h:.1f} mm (one ~27, two ~54)")
    home(ctx)
    say("STACKED" if h and h > 45 else "NOT STACKED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
