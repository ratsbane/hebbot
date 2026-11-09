"""Tight refine + narrow pre-open. Both previous failures were finger placement, not location."""
import math, time
import numpy as np
from hebbot.mcp_server import make_hardware_context
from hebbot.skills.primitives import (_qmul, grasp_at, move_to_joints, move_to_pose,
                                      open_gripper, probe_table)
TABLE_Z, OBJ = 0.0177, 0.010
HOME_Q=[0.0,-0.785,0.0,-2.356,0.0,1.571,0.785]; LIFT=0.22
BASE=(0.5000,-0.0200); MOVE=(0.5273,-0.0933)
def say(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)
def probe(ctx,x,y):
    r=probe_table(x=float(x),y=float(y),open_width=0.008,z_floor=0.012,_context=ctx)
    c=r.get("contact_z"); return float(c) if c is not None else None

ctx=make_hardware_context("192.168.0.253",no_robot=False)
open_gripper(_context=ctx); move_to_joints(q=HOME_Q,speed_factor=0.15,_context=ctx)

# refine inside +/-12 mm only: the neighbour is 78 mm away and must not contaminate this
hits=[]
for dx in (-0.012,-0.006,0.0,0.006,0.012):
    for dy in (-0.012,0.0,0.012):
        c=probe(ctx,MOVE[0]+dx,MOVE[1]+dy)
        if c is not None and c>TABLE_Z+OBJ: hits.append((MOVE[0]+dx,MOVE[1]+dy,c))
if not hits:
    say("nothing felt"); raise SystemExit(1)
cx=float(np.mean([h[0] for h in hits])); cy=float(np.mean([h[1] for h in hits]))
top=float(np.median([h[2] for h in hits]))
say(f"MOVE refined ({cx:.4f},{cy:+.4f}) top={top:.4f} h={1000*(top-TABLE_Z):.1f}mm {len(hits)}/15 hits")

yaw=math.radians(90.0)
quat=_qmul([0.0,1.0,0.0,0.0],[math.cos(yaw/2),0.0,0.0,math.sin(yaw/2)])
for ow in (0.040, 0.036):
    move_to_pose([cx,cy,0.12],orientation=list(quat),speed_factor=0.06,_context=ctx)
    g=grasp_at(grasp_z=TABLE_Z+0.010,width=0.023,open_width=ow,descent_speed=0.015,_context=ctx)
    say(f"  open={ow*1000:.0f}mm -> grasped={g.get('grasped')} w={g.get('actual_width')} high={g.get('grip_high_mm')}")
    if g.get("grasped"): break
    open_gripper(_context=ctx)

if g.get("grasped"):
    bx,by=BASE; pz=0.0450+0.013
    move_to_pose([cx,cy,LIFT],speed_factor=0.06,_context=ctx)
    move_to_pose([bx,by,LIFT],speed_factor=0.06,_context=ctx)
    move_to_pose([bx,by,pz],speed_factor=0.02,_context=ctx)
    open_gripper(_context=ctx)
    move_to_pose([bx,by,LIFT],speed_factor=0.06,_context=ctx)
    say(f"  placed at ({bx:.4f},{by:+.4f}) z={pz:.4f}")
    best=None
    for dx in (-0.010,0.0,0.010):
        for dy in (-0.010,0.0,0.010):
            c=probe(ctx,bx+dx,by+dy)
            if c is not None and (best is None or c>best): best=c
    h=1000*(best-TABLE_Z) if best else 0
    say(f"VERIFY pile {h:.1f} mm"); say("*** STACKED ***" if h>45 else "not stacked")
open_gripper(_context=ctx); move_to_joints(q=HOME_Q,speed_factor=0.15,_context=ctx); say("done")
