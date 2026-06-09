"""Localize one object in one frame using a given extrinsic. Perception venv.

argv: color_path depth_path T(16 csv) object calib_json
Prints: LOCALIZE {"x":..,"y":..,"z":..,"pixel":[u,v]}
"""
import json
import sys

import numpy as np
from PIL import Image
from transformers import AutoModelForCausalLM

DATA = "/home/doug/hebbot-data"
color, depth_p, T_csv, obj, calib = sys.argv[1:6]
T = np.array([float(x) for x in T_csv.split(",")]).reshape(4, 4)
X = np.array(json.load(open(calib))["X"])
INTR = {"fx": 394.48443603515625, "fy": 393.46728515625,
        "cx": 323.6427001953125, "cy": 242.88636779785156}
DS = 9.999999747378752e-05

model = AutoModelForCausalLM.from_pretrained(
    "vikhyatk/moondream2", revision="2025-06-21",
    trust_remote_code=True, device_map={"": "cuda"})

img = Image.open(f"{DATA}/{color}").convert("RGB"); W, H = img.size
depth = np.load(f"{DATA}/{depth_p}")
pts = model.point(img, obj)["points"]
if not pts:
    print("LOCALIZE null"); raise SystemExit(0)
u, v = pts[0]["x"]*W, pts[0]["y"]*H
ui, vi = int(round(u)), int(round(v))
patch = depth[max(0, vi-3):vi+4, max(0, ui-3):ui+4]
vals = patch[patch > 0]
z = float(np.median(vals))*DS
c = np.array([(u-INTR["cx"])*z/INTR["fx"], (v-INTR["cy"])*z/INTR["fy"], z])
base = (T @ X @ np.append(c, 1.0))[:3]
print("LOCALIZE " + json.dumps({"x": round(float(base[0]), 4), "y": round(float(base[1]), 4),
                                "z": round(float(base[2]), 4), "pixel": [round(u), round(v)]}))
