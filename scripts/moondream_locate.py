"""Locate an object in a saved RealSense frame and deproject to a camera-frame 3D point.

Runs Moondream `point` on the color image, samples the aligned depth at that
pixel, and deprojects (simple pinhole) to a point in the camera optical frame.
Used by the hand-eye calibration. Loads the model once and can process several
frames in one invocation (pass --batch a JSON list) to amortize model load.

Single:  moondream_locate.py --color C --depth D --fx .. --object "green block"
Batch:   moondream_locate.py --batch frames.json  (list of {color,depth,object,label})
         where color/depth are paths relative to --data-root.
"""
import argparse
import json
import sys

import numpy as np
from PIL import Image
from transformers import AutoModelForCausalLM

DATA_ROOT = "/home/doug/hebbot-data"


def deproject(u, v, depth_img, depth_scale, intr, win=7):
    h, w = depth_img.shape
    u_i, v_i = int(round(u)), int(round(v))
    r = win // 2
    patch = depth_img[max(0, v_i - r):v_i + r + 1, max(0, u_i - r):u_i + r + 1]
    vals = patch[patch > 0]
    if vals.size == 0:
        return None, None
    z = float(np.median(vals)) * depth_scale
    x = (u - intr["cx"]) * z / intr["fx"]
    y = (v - intr["cy"]) * z / intr["fy"]
    return [x, y, z], z


def locate(model, color_path, depth_path, obj, intr, depth_scale):
    img = Image.open(color_path).convert("RGB")
    W, H = img.size
    pts = model.point(img, obj)["points"]
    if not pts:
        return {"object": obj, "found": False}
    # take the most confident / first point
    px = pts[0]
    u, v = px["x"] * W, px["y"] * H
    depth_img = np.load(depth_path)
    cam_point, z = deproject(u, v, depth_img, depth_scale, intr)
    return {
        "object": obj, "found": True,
        "pixel": [u, v], "depth_m": z, "cam_point": cam_point,
        "n_points": len(pts),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch")
    ap.add_argument("--color"); ap.add_argument("--depth"); ap.add_argument("--object")
    ap.add_argument("--fx", type=float); ap.add_argument("--fy", type=float)
    ap.add_argument("--cx", type=float); ap.add_argument("--cy", type=float)
    ap.add_argument("--depth-scale", type=float, default=9.999999747378752e-05)
    ap.add_argument("--data-root", default=DATA_ROOT)
    args = ap.parse_args()

    model = AutoModelForCausalLM.from_pretrained(
        "vikhyatk/moondream2", revision="2025-06-21",
        trust_remote_code=True, device_map={"": "cuda"})

    if args.batch:
        spec = json.load(open(args.batch))
        out = []
        for item in spec["frames"]:
            intr = spec["intrinsics"]
            ds = spec.get("depth_scale", args.depth_scale)
            r = locate(model, f"{args.data_root}/{item['color']}",
                       f"{args.data_root}/{item['depth']}", item["object"], intr, ds)
            r["label"] = item.get("label")
            out.append(r)
        print(json.dumps(out))
    else:
        intr = {"fx": args.fx, "fy": args.fy, "cx": args.cx, "cy": args.cy}
        r = locate(model, f"{args.data_root}/{args.color}",
                   f"{args.data_root}/{args.depth}", args.object, intr, args.depth_scale)
        print(json.dumps(r))


if __name__ == "__main__":
    main()
