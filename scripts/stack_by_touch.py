"""Stack blocks by touch. Vision to get approximately right, contact to get exactly right.

Runs the arm directly (no MCP server) so it can be driven while the tool connection is down.

The camera model here is the similarity fitted from the practice loop's own touch
correspondences on 2026-09-01 (#155): scale 0.83, rotation ~14 deg, which leaves ~17 mm rms
against ~46 mm for the translation model `touch_find` was using. 17 mm is not good enough to
grasp from, but it is good enough to start a search at, and touch resolves the rest.

Sequence: find the base by touch, find the block to move by touch, grasp it at the felt
position, place it on the base at the felt height, and confirm the stack by feeling how tall
the pile became. No step depends on the hand-eye calibration being right.
"""

from __future__ import annotations

import json
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

# Similarity fitted from touch correspondences (lab notebook #155).
A = np.array([[0.7668, -0.2911], [0.1157, 0.8530]])
B = np.array([-0.0388, -0.0702])   # b refined by the green correspondence of
                                   # 2026-09-01 18:31: predicted (0.4395,-0.0297),
                                   # felt (0.4535,-0.0437) -> b += (+0.014,-0.014)

# Measured repeatedly today: probe_table reads 0.0164-0.0193 across the workspace, and
# #144's plane origin still reads 0.0164 against June's 0.0165.
TABLE_Z = 0.0177
OBJECT_MM = 10.0                 # a contact this far above the table is something standing on it
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
OBSERVE = (0.42, -0.05, 0.31)
LIFT_Z = 0.22

log = []


def say(msg):
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)
    log.append(f"{stamp} {msg}")


def spiral(step, radius):
    points, ring = [(0.0, 0.0)], 1
    while ring * step <= radius:
        r = ring * step
        coords = [i * step for i in range(-ring, ring + 1)]
        for point in ([(x, -r) for x in coords] + [(r, y) for y in coords]
                      + [(x, r) for x in reversed(coords)]
                      + [(-r, y) for y in reversed(coords)]):
            if point not in points:
                points.append(point)
        ring += 1
    return points


def home(ctx):
    open_gripper(_context=ctx)
    move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)


def observe(ctx, colour, at=OBSERVE):
    move_to_pose(list(at), speed_factor=0.12, _context=ctx)
    cam = _select_camera(ctx["cameras"], None)
    capture = cam.capture(with_depth=True)
    found = locate(capture["color"][:, :, ::-1], colour, depth=capture.get("depth"))
    blobs = [b for b in found.get("blobs", [])
             if b["v"] > 130 and b["npix"] >= 400 and b.get("depth_m")]
    return blobs, [float(v) for v in ctx["panda"].get_position()]


def predict_world(blob, observed_from):
    """Where the camera thinks the block is, corrected by the fitted similarity."""
    mpp = float(blob["depth_m"]) / FX
    raw = np.array([(blob["v"] - CY) * mpp, (blob["u"] - CX) * mpp])
    world = np.asarray(observed_from[:2]) + A @ raw + B
    # The similarity was fitted on four points and extrapolates badly beyond them: a block
    # at the frame edge once projected to y=-0.73, off the table. Clamp to the reachable
    # tabletop so a bad prediction still searches somewhere real.
    return np.array([float(np.clip(world[0], 0.33, 0.57)),
                     float(np.clip(world[1], -0.26, 0.18))])


def touch_locate(ctx, centre, coarse=0.030, radius=0.135, max_probes=90, label=""):
    """Feel for the object: coarse search to hit it, then a symmetric raster to centre on it.

    Taking the mean of however many hits the coarse spiral landed first biases the centre
    toward wherever the search entered the object — measured on 2026-09-01, where shifting
    the prediction by 14 mm shifted the "measured" position by the same 14 mm. The centre
    must come from sampling that is symmetric about the object, not about the guess.
    """
    probes = 0

    def probe(x, y):
        nonlocal probes
        if not (0.30 <= x <= 0.60 and -0.30 <= y <= 0.30):
            return None
        result = probe_table(x=x, y=y, open_width=0.008, z_floor=0.012, _context=ctx)
        probes += 1
        contact = result.get("contact_z")
        if contact is None:
            return None
        return float(contact) if float(contact) > TABLE_Z + OBJECT_MM / 1000.0 else False

    first = None
    for dx, dy in spiral(coarse, radius):
        if probes >= max_probes:
            break
        hit = probe(float(centre[0] + dx), float(centre[1] + dy))
        if hit:
            first = (float(centre[0] + dx), float(centre[1] + dy), hit)
            break
    if first is None:
        say(f"  {label}: no object within {radius*1000:.0f} mm after {probes} probes")
        return None

    # Symmetric raster about the coarse hit, so the centroid is the object's, not the seed's.
    fine = [(first[0], first[1], first[2])]
    for dx, dy in spiral(0.012, 0.036):
        if probes >= max_probes + 30:
            break
        if dx == 0 and dy == 0:
            continue
        hit = probe(first[0] + dx, first[1] + dy)
        if hit:
            fine.append((first[0] + dx, first[1] + dy, hit))
    cx = float(np.mean([h[0] for h in fine]))
    cy = float(np.mean([h[1] for h in fine]))
    top = float(np.median([h[2] for h in fine]))
    say(f"  {label}: felt at ({cx:.4f}, {cy:.4f}) top={top:.4f} "
        f"height={1000*(top-TABLE_Z):.1f}mm in {probes} probes ({len(fine)} hits)")
    return {"xy": (cx, cy), "top": top, "probes": probes, "hits": len(fine)}


SURVEY = [(0.42, -0.05, 0.31), (0.42, -0.19, 0.31), (0.50, 0.06, 0.31),
          (0.36, 0.04, 0.31), (0.52, -0.16, 0.31)]


def find(ctx, colour):
    blobs, observed_from = [], None
    for pose in SURVEY:
        blobs, observed_from = observe(ctx, colour, at=pose)
        if blobs:
            break
        say(f"  {colour}: not in view from {pose[:2]}")
    if not blobs:
        say(f"{colour}: not seen from any survey pose")
        return None
    seed = predict_world(blobs[0], observed_from)
    say(f"{colour}: seen at u={blobs[0]['u']:.0f} v={blobs[0]['v']:.0f} "
        f"-> predicted ({seed[0]:.4f}, {seed[1]:.4f})")
    return touch_locate(ctx, seed, label=colour)


def pick(ctx, target):
    x, y = target["xy"]
    move_to_pose([x, y, 0.12], speed_factor=0.08, _context=ctx)
    grasp = grasp_at(grasp_z=TABLE_Z + 0.010, width=0.024, descent_speed=0.02,
                     _context=ctx)
    say(f"  grasp: grasped={grasp.get('grasped')} width={grasp.get('actual_width')} "
        f"high={grasp.get('grip_high_mm')}mm")
    if not grasp.get("grasped"):
        open_gripper(_context=ctx)
        return False
    move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
    return True


def place_on(ctx, base, drop_clearance=0.003):
    """Release so the carried block's underside lands on the base's top face.

    The block is grasped at TABLE_Z + 0.010 with its underside on the table, so the
    underside always sits 10 mm below the EE. Putting it on a face at `top` therefore means
    EE = top + 0.010, and `drop_clearance` is the release gap ON TOP of that. It was 14 mm
    on the first stacking run, which dropped the block from 14 mm onto a 28 mm-wide target
    and bounced it off: the pile verified at 28.3 mm instead of 54 mm.
    """
    x, y = base["xy"]
    place_z = base["top"] + 0.010 + drop_clearance
    move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
    move_to_pose([x, y, place_z], speed_factor=0.03, _context=ctx)
    open_gripper(_context=ctx)
    move_to_pose([x, y, LIFT_Z], speed_factor=0.06, _context=ctx)
    say(f"  placed at ({x:.4f}, {y:.4f}) release_z={place_z:.4f}")
    return place_z


def verify_stack(ctx, base, expect_mm):
    """Confirm by feel: the pile should now be one block taller."""
    x, y = base["xy"]
    best = None
    for dx, dy in spiral(0.012, 0.024):
        result = probe_table(x=x + dx, y=y + dy, open_width=0.008, z_floor=0.012,
                             _context=ctx)
        contact = result.get("contact_z")
        if contact is not None and (best is None or contact > best):
            best = float(contact)
        if best is not None and best > TABLE_Z + (expect_mm - 8) / 1000.0:
            break
    height = 1000 * (best - TABLE_Z) if best is not None else None
    say(f"  VERIFY: tallest contact {best} -> pile height "
        f"{height:.1f} mm (expected ~{expect_mm:.0f} mm)")
    return height


def main():
    ctx = make_hardware_context(FRANKA, no_robot=False)
    if "panda" not in ctx:
        say("no arm"); return 1
    say("connected; homing")
    home(ctx)

    order = sys.argv[1:] or ["red", "green", "blue"]
    say(f"stack order (base first): {order}")

    base = find(ctx, order[0])
    if base is None:
        say("base not found; aborting"); home(ctx); return 2
    say(f"BASE = {order[0]} at {base['xy']}")

    results = {"base": order[0], "base_xy": base["xy"], "placed": []}
    pile_top = base["top"]
    for colour in order[1:]:
        home(ctx)
        target = find(ctx, colour)
        if target is None:
            say(f"{colour}: not found, skipping"); continue
        if not pick(ctx, target):
            say(f"{colour}: grasp failed, skipping"); continue
        place_on(ctx, {"xy": base["xy"], "top": pile_top})
        expect = 1000 * (pile_top - TABLE_Z) + 27.0
        height = verify_stack(ctx, base, expect)
        if height is not None:
            pile_top = TABLE_Z + height / 1000.0
        results["placed"].append({"colour": colour, "pile_height_mm": height})
        say(f"{colour}: stacked. pile now {height} mm")

    home(ctx)
    say("done")
    print(json.dumps(results, indent=2))
    with open("/home/doug/hebbot-data/stack_log.txt", "w") as handle:
        handle.write("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
