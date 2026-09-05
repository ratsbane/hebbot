"""Stack one block on another, locating the second RELATIVE to the first.

The absolute camera->hand model has a poorly constrained offset term: with four
correspondences the rotation and scale are decent but `b` is not, and a block near the
image centre — where the rotation/scale contribute almost nothing — is predicted almost
entirely by `b`. That is why blue kept failing while green succeeded.

A displacement between two blocks in the same image does not involve `b` at all. So: find
the base by touch, then predict the second block as base + A @ (image displacement), and
search a small radius around that. Touch still supplies the final millimetres.
"""

from __future__ import annotations

import sys
import time

import numpy as np

from hebbot.mcp_server import make_hardware_context
from hebbot.perception.target import locate
from hebbot.skills.primitives import (
    _select_camera, grasp_at, move_to_joints, move_to_pose, open_gripper, probe_table,
)

FRANKA = "192.168.0.253"
FX, CX, CY = 353.6, 323.6427001953125, 242.88636779785156
A = np.array([[0.7668, -0.2911], [0.1157, 0.8530]])
B = np.array([-0.0388, -0.0702])
TABLE_Z = 0.0177
OBJECT_MM = 10.0
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
VIEW = (0.45, -0.05, 0.40)
LIFT_Z = 0.22


def say(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def spiral(step, radius):
    pts, ring = [(0.0, 0.0)], 1
    while ring * step <= radius:
        r = ring * step
        c = [i * step for i in range(-ring, ring + 1)]
        for p in ([(x, -r) for x in c] + [(r, y) for y in c]
                  + [(x, r) for x in reversed(c)] + [(-r, y) for y in reversed(c)]):
            if p not in pts:
                pts.append(p)
        ring += 1
    return pts


def home(ctx):
    open_gripper(_context=ctx)
    move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)


def survey(ctx, colours):
    """One image, every block located in it, so displacements are directly comparable."""
    move_to_pose(list(VIEW), speed_factor=0.12, _context=ctx)
    cam = _select_camera(ctx["cameras"], None)
    cap = cam.capture(with_depth=True)
    rgb = cap["color"][:, :, ::-1]
    seen = {}
    for colour in colours:
        out = locate(rgb, colour, depth=cap.get("depth"))
        blobs = [b for b in out.get("blobs", [])
                 if b["v"] > 120 and b["npix"] >= 400 and b.get("depth_m")]
        if blobs:
            seen[colour] = blobs[0]
            say(f"  saw {colour} at u={blobs[0]['u']:.0f} v={blobs[0]['v']:.0f} "
                f"depth={blobs[0]['depth_m']:.3f}")
        else:
            say(f"  {colour} not visible")
    return seen, [float(v) for v in ctx["panda"].get_position()]


def image_to_world(blob, observed_from):
    mpp = float(blob["depth_m"]) / FX
    raw = np.array([(blob["v"] - CY) * mpp, (blob["u"] - CX) * mpp])
    return np.asarray(observed_from[:2]) + A @ raw + B


def relative_to(anchor_blob, anchor_world, blob):
    """Where `blob` is, given a block whose true position is already known by touch."""
    mpp = float(blob["depth_m"]) / FX
    delta = np.array([(blob["v"] - anchor_blob["v"]) * mpp,
                      (blob["u"] - anchor_blob["u"]) * mpp])
    return np.asarray(anchor_world) + A @ delta


def feel(ctx, centre, coarse=0.022, radius=0.075, cap=70, label=""):
    probes = [0]

    def probe(x, y):
        if not (0.30 <= x <= 0.60 and -0.30 <= y <= 0.30):
            return None
        r = probe_table(x=x, y=y, open_width=0.008, z_floor=0.012, _context=ctx)
        probes[0] += 1
        c = r.get("contact_z")
        if c is None:
            return None
        return float(c) if float(c) > TABLE_Z + OBJECT_MM / 1000.0 else False

    first = None
    for dx, dy in spiral(coarse, radius):
        if probes[0] >= cap:
            break
        hit = probe(centre[0] + dx, centre[1] + dy)
        if hit:
            first = (centre[0] + dx, centre[1] + dy, hit)
            break
    if first is None:
        say(f"  {label}: not felt in {probes[0]} probes")
        return None
    hits = [first]
    for dx, dy in spiral(0.011, 0.033):
        if probes[0] >= cap + 30:
            break
        if dx or dy:
            hit = probe(first[0] + dx, first[1] + dy)
            if hit:
                hits.append((first[0] + dx, first[1] + dy, hit))
    cx = float(np.mean([h[0] for h in hits]))
    cy = float(np.mean([h[1] for h in hits]))
    top = float(np.median([h[2] for h in hits]))
    say(f"  {label}: felt ({cx:.4f}, {cy:.4f}) top={top:.4f} "
        f"h={1000*(top-TABLE_Z):.1f}mm  {probes[0]} probes, {len(hits)} hits")
    return {"xy": (cx, cy), "top": top, "hits": len(hits)}


def main():
    base_col, move_col = (sys.argv[1:3] + ["green", "blue"])[:2]
    ctx = make_hardware_context(FRANKA, no_robot=False)
    if "panda" not in ctx:
        say("no arm"); return 1
    home(ctx)

    seen, observed_from = survey(ctx, [base_col, move_col])
    if base_col not in seen or move_col not in seen:
        say("need both blocks visible in one image"); home(ctx); return 2

    base = feel(ctx, image_to_world(seen[base_col], observed_from), label=base_col)
    if base is None:
        home(ctx); return 3

    predicted = relative_to(seen[base_col], base["xy"], seen[move_col])
    say(f"{move_col} predicted relative to {base_col}: "
        f"({predicted[0]:.4f}, {predicted[1]:.4f})")
    target = feel(ctx, predicted, radius=0.055, label=move_col)
    if target is None:
        home(ctx); return 4

    x, y = target["xy"]
    move_to_pose([x, y, 0.12], speed_factor=0.08, _context=ctx)
    grasp = grasp_at(grasp_z=TABLE_Z + 0.010, width=0.024, descent_speed=0.02,
                     _context=ctx)
    say(f"  grasp {move_col}: grasped={grasp.get('grasped')} "
        f"width={grasp.get('actual_width')} high={grasp.get('grip_high_mm')}")
    if not grasp.get("grasped"):
        open_gripper(_context=ctx); home(ctx); return 5

    bx, by = base["xy"]
    place_z = base["top"] + 0.010 + 0.003
    move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([bx, by, place_z], speed_factor=0.025, _context=ctx)
    open_gripper(_context=ctx)
    move_to_pose([bx, by, LIFT_Z], speed_factor=0.06, _context=ctx)
    say(f"  placed {move_col} on {base_col} at ({bx:.4f}, {by:.4f}) z={place_z:.4f}")

    best = None
    for dx, dy in spiral(0.010, 0.020):
        r = probe_table(x=bx + dx, y=by + dy, open_width=0.008, z_floor=0.012,
                        _context=ctx)
        c = r.get("contact_z")
        if c is not None and (best is None or c > best):
            best = float(c)
    height = 1000 * (best - TABLE_Z) if best is not None else None
    say(f"VERIFY: pile height {height:.1f} mm (one block ~27, two ~54)")
    home(ctx)
    say("STACKED" if height and height > 45 else "NOT STACKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
