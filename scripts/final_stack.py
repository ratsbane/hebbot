"""Camera for orientation, touch for position - the split the whole day argued for.

The camera's relative geometry (blob angles, displacements between blocks) is sound; only
its absolute offset is broken. Touch gives absolute position but cannot measure a block's
orientation without the neighbour contaminating the probe. So: scan by touch to get true
positions, photograph to get angles, then match the two point sets to pair them up - which
also recovers the offset for free.
"""
import itertools, math, time
import numpy as np
from hebbot.mcp_server import make_hardware_context
from hebbot.perception.target import locate
from hebbot.skills.primitives import (_grasp_axis_yaw, _qmul, _select_camera, grasp_at,
                                      move_to_joints, move_to_pose, open_gripper, probe_table)

TABLE_Z, OBJ = 0.0177, 0.010
FX, CX, CY = 353.6, 323.6427, 242.8864
A = np.array([[0.7668,-0.2911],[0.1157,0.8530]])
HOME_Q=[0.0,-0.785,0.0,-2.356,0.0,1.571,0.785]; LIFT=0.22
VIEW=(0.46,-0.05,0.35)

def say(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)
def probe(ctx,x,y):
    r=probe_table(x=float(x),y=float(y),open_width=0.008,z_floor=0.012,_context=ctx)
    c=r.get("contact_z"); return float(c) if c is not None else None

ctx=make_hardware_context("192.168.0.253",no_robot=False)
open_gripper(_context=ctx); move_to_joints(q=HOME_Q,speed_factor=0.15,_context=ctx)

# ---- camera: angles and relative layout ----
move_to_pose(list(VIEW),speed_factor=0.12,_context=ctx)
cap=_select_camera(ctx["cameras"],None).capture(with_depth=True)
rgb=cap["color"][:,:,::-1]
blobs={}
for c in ("green","blue","red"):
    o=locate(rgb,c,depth=cap.get("depth"))
    bs=[b for b in o.get("blobs",[]) if b["v"]>120 and b["npix"]>=400 and b.get("depth_m")]
    if bs:
        blobs[c]=bs[0]
        say(f"  saw {c} u={bs[0]['u']:.0f} v={bs[0]['v']:.0f} angle={bs[0]['angle_deg']:.0f}")
obs=np.array([float(v) for v in ctx["panda"].get_position()][:2])
pred={c:obs+A@np.array([(b["v"]-CY)*b["depth_m"]/FX,(b["u"]-CX)*b["depth_m"]/FX])
      for c,b in blobs.items()}

# ---- touch: true positions ----
say("touch scan")
xs=np.arange(0.380,0.581,0.040); ys=np.arange(-0.180,0.061,0.040)
hits=[]
for i,x in enumerate(xs):
    for y in (ys if i%2==0 else ys[::-1]):
        c=probe(ctx,x,y)
        if c is not None and c>TABLE_Z+OBJ:
            hits.append((float(x),float(y),c)); say(f"  HIT ({x:.3f},{y:+.3f}) h={1000*(c-TABLE_Z):.0f}mm")
clusters=[]
for h in hits:
    for cl in clusters:
        if any(np.hypot(h[0]-g[0],h[1]-g[1])<=0.045 for g in cl): cl.append(h); break
    else: clusters.append([h])
objs=[{"xy":np.array([np.mean([g[0] for g in cl]),np.mean([g[1] for g in cl])]),
       "top":float(np.median([g[2] for g in cl])),"n":len(cl)} for cl in clusters]
objs.sort(key=lambda o:-o["n"])
say(f"{len(objs)} objects: "+", ".join(f"({o['xy'][0]:.3f},{o['xy'][1]:+.3f})h{1000*(o['top']-TABLE_Z):.0f}" for o in objs))
if len(objs)<2: say("need 2 objects"); raise SystemExit(2)

# ---- match camera blobs to touched objects (recovers the offset too) ----
names=list(pred); best=None
for perm in itertools.permutations(range(len(objs)), min(len(names),len(objs))):
    P=np.array([pred[names[i]] for i in range(len(perm))])
    T=np.array([objs[j]["xy"] for j in perm])
    off=(T-P).mean(axis=0); res=float(np.linalg.norm(T-P-off,axis=1).mean())
    if best is None or res<best[0]: best=(res,perm,off)
res,perm,off=best
say(f"matched with mean residual {1000*res:.1f} mm, recovered offset ({off[0]:+.4f},{off[1]:+.4f})")
angle={objs[j]["n"] and j:names[i] for i,j in enumerate(perm)}
pairing={j:names[i] for i,j in enumerate(perm)}
for j,c in pairing.items(): say(f"  object {j} at ({objs[j]['xy'][0]:.3f},{objs[j]['xy'][1]:+.3f}) is {c}")

# pick the pair with the LARGEST separation so no finger reaches the neighbour
pairs=sorted(itertools.combinations(range(len(objs)),2),
             key=lambda p:-np.linalg.norm(objs[p[0]]["xy"]-objs[p[1]]["xy"]))
mi,bi=pairs[0]
sep=1000*np.linalg.norm(objs[mi]["xy"]-objs[bi]["xy"])
say(f"stacking object {mi} onto object {bi}, separation {sep:.0f} mm")

mv,bs=objs[mi],objs[bi]
yaw=_grasp_axis_yaw(blobs[pairing[mi]]["angle_deg"],0.055,0.028) if mi in pairing else 0.0
say(f"  yaw {math.degrees(yaw):.0f} deg from image angle")
quat=_qmul([0.0,1.0,0.0,0.0],[math.cos(yaw/2),0.0,0.0,math.sin(yaw/2)])
x,y=float(mv["xy"][0]),float(mv["xy"][1])
move_to_pose([x,y,0.12],orientation=list(quat),speed_factor=0.06,_context=ctx)
g=grasp_at(grasp_z=TABLE_Z+0.010,width=0.023,open_width=0.044,descent_speed=0.015,_context=ctx)
say(f"  grasp {g.get('grasped')} w={g.get('actual_width')} high={g.get('grip_high_mm')}")
if g.get("grasped"):
    bx,by=float(bs["xy"][0]),float(bs["xy"][1]); pz=bs["top"]+0.013
    move_to_pose([x,y,LIFT],speed_factor=0.06,_context=ctx)
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
else: open_gripper(_context=ctx)
open_gripper(_context=ctx); move_to_joints(q=HOME_Q,speed_factor=0.15,_context=ctx); say("done")
