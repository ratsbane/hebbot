"""Capture a frame from a connected Intel RealSense camera (D405 wrist cam).

First vision bring-up for Hebbot. Follows lab-notebook finding #101: the D405
needs `dev.hardware_reset()` + a settling wait before `pipeline.start()`, or
the pipe starts "successfully" but never delivers frames.

Saves a color JPEG and a colorized-depth JPEG to --out-dir, and prints basic
stats (resolution, depth at image center). Not yet a substrate primitive —
this is the standalone proof that the camera path works end to end.

Usage:
    uv run --project . python scripts/capture_realsense.py --out-dir /tmp/rs
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pyrealsense2 as rs


def list_devices() -> list[rs.device]:
    ctx = rs.context()
    devs = list(ctx.query_devices())
    for d in devs:
        print(f"  - {d.get_info(rs.camera_info.name)} "
              f"(serial {d.get_info(rs.camera_info.serial_number)}, "
              f"fw {d.get_info(rs.camera_info.firmware_version)})")
    return devs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/tmp/rs")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--settle", type=float, default=5.0,
                    help="seconds to wait after hardware_reset (finding #101)")
    ap.add_argument("--warmup", type=int, default=15,
                    help="frames to discard for auto-exposure to settle")
    args = ap.parse_args()

    import os
    os.makedirs(args.out_dir, exist_ok=True)

    print("Enumerating RealSense devices...")
    devs = list_devices()
    if not devs:
        print("No RealSense devices found.", file=sys.stderr)
        return 1

    # Finding #101: hardware_reset + settle before start, or frames never arrive.
    print("hardware_reset() on first device, then settling...")
    devs[0].hardware_reset()
    time.sleep(args.settle)

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)

    print(f"Starting pipeline at {args.width}x{args.height}@{args.fps}...")
    profile = pipeline.start(config)
    depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
    print(f"  depth scale: {depth_scale} m/unit")

    try:
        align = rs.align(rs.stream.color)
        for i in range(args.warmup):
            pipeline.wait_for_frames(timeout_ms=5000)

        frames = align.process(pipeline.wait_for_frames(timeout_ms=5000))
        color = frames.get_color_frame()
        depth = frames.get_depth_frame()
        if not color or not depth:
            print("Got frameset but missing color/depth frame.", file=sys.stderr)
            return 2

        color_img = np.asanyarray(color.get_data())          # HxWx3 BGR
        depth_img = np.asanyarray(depth.get_data())           # HxW uint16

        h, w = depth_img.shape
        cx, cy = w // 2, h // 2
        center_m = depth_img[cy, cx] * depth_scale
        print(f"  color: {color_img.shape}  depth: {depth_img.shape}")
        print(f"  center-pixel depth: {center_m:.3f} m")
        print(f"  depth coverage: {(depth_img > 0).mean()*100:.1f}% of pixels have a return")

        # Colorize depth for a human-viewable JPEG (no cv2 dependency).
        colorizer = rs.colorizer()
        depth_color = np.asanyarray(colorizer.colorize(depth).get_data())

        # Save without cv2: use PIL.
        from PIL import Image
        # color is BGR -> flip to RGB for correct colors in the saved file
        Image.fromarray(color_img[:, :, ::-1]).save(f"{args.out_dir}/color.jpg", quality=92)
        Image.fromarray(depth_color[:, :, ::-1]).save(f"{args.out_dir}/depth.jpg", quality=92)
        print(f"Saved {args.out_dir}/color.jpg and {args.out_dir}/depth.jpg")
    finally:
        pipeline.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
