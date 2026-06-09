"""Autonomous block localization: Moondream + hand-eye calibration -> robot coords.

For a saved survey frame at a known EE pose T, for each object:
  pixel = Moondream.point(obj)  ->  c = deproject(pixel, depth, intrinsics)
  base_point = T @ X @ [c; 1]      (X = camera->EE extrinsic from calibration)
Prints the base-frame (x, y, z) for each object.
"""
import json
import sys

import numpy as np
from PIL import Image
from transformers import AutoModelForCausalLM

# ---- this survey capture (from get_state + read_camera) ----
DATA = "/home/doug/hebbot-data"
COLOR = sys.argv[1]   # color_path (relative to DATA)
DEPTH = sys.argv[2]   # depth_path
T = np.array([float(x) for x in sys.argv[3].split(",")]).reshape(4, 4)  # EE pose, row-major
INTR = {"fx": 394.48443603515625, "fy": 393.46728515625,
        "cx": 323.6427001953125, "cy": 242.88636779785156}
DEPTH_SCALE = 9.999999747378752e-05
OBJECTS = ["green block", "blue block", "pink block", "tan block"]

X = np.array(json.load(open("/home/doug/hebbot/scripts/calib_result_2026-06-07.json"))["X"])

model = AutoModelForCausalLM.from_pretrained(
    "vikhyatk/moondream2", revision="2025-06-21",
    trust_remote_code=True, device_map={"": "cuda"})

img = Image.open(f"{DATA}/{COLOR}").convert("RGB")
W, H = img.size
depth = np.load(f"{DATA}/{DEPTH}")


def deproject(u, v):
    ui, vi = int(round(u)), int(round(v))
    patch = depth[max(0, vi - 3):vi + 4, max(0, ui - 3):ui + 4]
    vals = patch[patch > 0]
    if vals.size == 0:
        return None
    z = float(np.median(vals)) * DEPTH_SCALE
    return np.array([(u - INTR["cx"]) * z / INTR["fx"],
                     (v - INTR["cy"]) * z / INTR["fy"], z])


out = {}
for obj in OBJECTS:
    pts = model.point(img, obj)["points"]
    if not pts:
        print(f"{obj:14s} not found"); continue
    u, v = pts[0]["x"] * W, pts[0]["y"] * H
    c = deproject(u, v)
    if c is None:
        print(f"{obj:14s} pixel ({u:.0f},{v:.0f}) no depth"); continue
    base = (T @ X @ np.append(c, 1.0))[:3]
    out[obj] = base.tolist()
    print(f"{obj:14s} pixel=({u:.0f},{v:.0f})  base=({base[0]:.3f}, {base[1]:.3f}, {base[2]:.3f})")

json.dump(out, open("/tmp/block_positions.json", "w"))
print("saved /tmp/block_positions.json")
