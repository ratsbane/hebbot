"""Hand-eye calibration — solve phase (runs in the perception venv).

Reads the manifest from calibrate_collect.py. For each pose: Moondream-`point`
the object in the color frame, deproject (pixel + aligned depth) to a
camera-frame point c_i. Then solve the camera->EE extrinsic X (6 DoF) and the
object's base position p by least squares on  T_i * X * c_i = p  (for a fixed X,
the optimal p is the mean of T_i X c_i, so we minimize the spread of the
back-projected object position across poses).
"""
import json

import numpy as np
from PIL import Image
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from transformers import AutoModelForCausalLM

DATA = "/home/doug/hebbot-data"
man = json.load(open("/tmp/calib_manifest.json"))
intr, ds, obj = man["intrinsics"], man["depth_scale"], man["object"]

model = AutoModelForCausalLM.from_pretrained(
    "vikhyatk/moondream2", revision="2025-06-21",
    trust_remote_code=True, device_map={"": "cuda"})


def deproject(u, v, depth, win=7):
    h, w = depth.shape
    ui, vi = int(round(u)), int(round(v))
    r = win // 2
    patch = depth[max(0, vi-r):vi+r+1, max(0, ui-r):ui+r+1]
    vals = patch[patch > 0]
    if vals.size == 0:
        return None
    z = float(np.median(vals)) * ds
    return np.array([(u-intr["cx"])*z/intr["fx"], (v-intr["cy"])*z/intr["fy"], z])


data = []
for f in man["frames"]:
    img = Image.open(f"{DATA}/{f['color']}").convert("RGB")
    W, H = img.size
    pts = model.point(img, obj)["points"]
    if not pts:
        print(f"  {f['label']}: not found"); continue
    u, v = pts[0]["x"]*W, pts[0]["y"]*H
    c = deproject(u, v, np.load(f"{DATA}/{f['depth']}"))
    if c is None:
        print(f"  {f['label']}: no depth"); continue
    T = np.array(f["T"]).reshape(4, 4)
    data.append((T, c, f["label"]))
    print(f"  {f['label']}: pixel=({u:.0f},{v:.0f}) z={c[2]:.3f} c={np.round(c,3)}")

print(f"\nusing {len(data)} poses")
if len(data) < 4:
    print("too few poses to solve"); raise SystemExit(1)


def backproject(params):
    X = np.eye(4)
    X[:3, :3] = Rotation.from_rotvec(params[:3]).as_matrix()
    X[:3, 3] = params[3:6]
    qs = np.array([(T @ X @ np.append(c, 1.0))[:3] for T, c, _ in data])
    return qs


def residuals(params):
    qs = backproject(params)
    return (qs - qs.mean(0)).ravel()


res = least_squares(residuals, np.zeros(6), method="lm")
X = np.eye(4)
X[:3, :3] = Rotation.from_rotvec(res.x[:3]).as_matrix()
X[:3, 3] = res.x[3:6]
qs = backproject(res.x)
p = qs.mean(0)
resid = qs - p
rms = np.sqrt((resid**2).sum(1)).mean()

np.set_printoptions(precision=4, suppress=True)
print("\n=== camera->EE extrinsic X ===")
print(X)
print("translation (m):", np.round(X[:3, 3], 4),
      " rotation (deg, xyz):", np.round(Rotation.from_matrix(X[:3, :3]).as_euler("xyz", degrees=True), 2))
print("\nobject position p (base frame):", np.round(p, 4))
print(f"per-pose back-projection spread: RMS {rms*1000:.1f} mm,  max {np.sqrt((resid**2).sum(1)).max()*1000:.1f} mm")
print("per-pose error (mm):", np.round(np.sqrt((resid**2).sum(1))*1000, 1))

json.dump({"X": X.tolist(), "p": p.tolist(), "rms_mm": rms*1000,
           "n_poses": len(data)}, open("/tmp/calib_result.json", "w"), indent=2)
print("\nsaved -> /tmp/calib_result.json")
