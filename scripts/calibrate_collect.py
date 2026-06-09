"""Hand-eye calibration — collection phase (runs in the substrate venv).

Uses the hebbot library DIRECTLY (no MCP) — Substrate + a hardware context — to
drive the arm to a set of look-at poses around the green block and capture a
wrist frame at each. Writes a manifest (per-pose EE pose T_i + frame paths +
intrinsics) for the solver. This doubles as the foundations litmus-test #6
demo: a custom script using the substrate as a plain library.

    HEBBOT_DATA=~/hebbot-data /home/doug/hebbot/.venv/bin/python calibrate_collect.py
"""
import json
import os
import sys

import numpy as np

from hebbot.substrate import Substrate
from hebbot.skills.primitives import register_all as reg_prim
from hebbot.skills.composed import register_all as reg_comp
from hebbot.mcp_server import make_hardware_context

TARGET = np.array([0.45, -0.17, 0.03])   # green-block location (base frame, 2026-06-09 scene)
DIST = 0.30                              # EE distance from target along view ray
OBJECT = "green block"
FRANKA = os.environ.get("HEBBOT_FRANKA", "192.168.0.253")


def mat_to_quat_wxyz(R):
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        S = np.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * S, (R[2, 1]-R[1, 2])/S, (R[0, 2]-R[2, 0])/S, (R[1, 0]-R[0, 1])/S
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        S = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, x, y, z = (R[2, 1]-R[1, 2])/S, 0.25*S, (R[0, 1]+R[1, 0])/S, (R[0, 2]+R[2, 0])/S
    elif R[1, 1] > R[2, 2]:
        S = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, x, y, z = (R[0, 2]-R[2, 0])/S, (R[0, 1]+R[1, 0])/S, 0.25*S, (R[1, 2]+R[2, 1])/S
    else:
        S = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, x, y, z = (R[1, 0]-R[0, 1])/S, (R[0, 2]+R[2, 0])/S, (R[1, 2]+R[2, 1])/S, 0.25*S
    return np.array([w, x, y, z])


def look_at(e, target):
    """EE rotation whose z-axis (camera view dir) points from e toward target."""
    z = target - e
    z = z / np.linalg.norm(z)
    ref = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(ref, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def gen_poses():
    # Vary DISTANCE as well as angle this time (the 2026-06-07 calib used a fixed
    # 0.30 m, which may be why localization didn't generalize across depths).
    poses = []
    for el_deg, dist in ((88, 0.38), (74, 0.30), (62, 0.24)):  # overhead-far -> tilted-close
        for az_deg in (-45, -15, 15, 45):                       # azimuth around the target
            el, az = np.radians(el_deg), np.radians(az_deg)
            offset = dist * np.array([np.cos(el)*np.cos(az),
                                      np.cos(el)*np.sin(az), np.sin(el)])
            e = TARGET + offset
            R = look_at(e, TARGET)
            poses.append((e.tolist(), mat_to_quat_wxyz(R).tolist(), f"el{el_deg}_d{dist}_az{az_deg}"))
    return poses


def main():
    s = Substrate(os.environ.get("HEBBOT_DATA", "~/hebbot-data"))
    reg_prim(s); reg_comp(s)
    ctx = make_hardware_context(FRANKA, no_robot=False, no_camera=False)
    if "panda" not in ctx:
        print("no panda in context"); return 1
    if "cameras" not in ctx:
        print("no cameras in context"); return 1

    intr = depth_scale = None
    frames = []
    for e, quat, label in gen_poses():
        try:
            s.call("move_to_pose",
                   params={"position": e, "orientation": quat, "speed_factor": 0.1},
                   context=ctx)
        except Exception as ex:
            print(f"skip {label}: move failed ({type(ex).__name__}: {ex})", flush=True)
            continue
        # Use the ACTUAL achieved pose (move may be a 'surprise' if a few mm off).
        st = s.call("get_state", context=ctx)["result"]
        cap = s.call("read_camera", context=ctx)["result"]
        if intr is None:
            intr, depth_scale = cap["intrinsics"], cap["depth_scale"]
        frames.append({"label": label, "T": st["ee_pose"],
                       "color": cap["color_path"], "depth": cap["depth_path"]})
        p = st["ee_pose"]
        print(f"ok {label}: EE=({p[3]:.3f},{p[7]:.3f},{p[11]:.3f})", flush=True)

    manifest = {"intrinsics": intr, "depth_scale": depth_scale, "object": OBJECT,
                "target": TARGET.tolist(), "frames": frames}
    json.dump(manifest, open("/tmp/calib_manifest.json", "w"), indent=2)
    print(f"\ncollected {len(frames)} frames -> /tmp/calib_manifest.json", flush=True)

    print("homing...", flush=True)
    try:
        s.call("home", context=ctx)
    except Exception as ex:
        print(f"home failed: {ex}")
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
