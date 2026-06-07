"""Demo: invoke the `read_camera` primitive against a live RealSense camera.

Exercises the full substrate path — open camera -> substrate.call('read_camera')
-> save frame(s) under the data root -> write rows to the frames table linked
to the episode. Prints the outcome and the frame rows it produced.

    HEBBOT_DATA=/tmp/hebbot-camtest uv run --project . python scripts/demo_read_camera.py
"""

from __future__ import annotations

import os
import sys

from hebbot import Substrate
from hebbot.skills.primitives import register_all as register_primitives
from hebbot.skills.composed import register_all as register_composed


def main() -> int:
    s = Substrate(os.environ.get("HEBBOT_DATA", "~/hebbot-data"))
    register_primitives(s)
    register_composed(s)

    try:
        from hebbot.perception.realsense import open_realsense_cameras, close_cameras
    except ImportError as e:
        print(f"pyrealsense2 not available: {e}")
        return 1

    print("opening RealSense cameras (hardware_reset + settle)...")
    cameras = open_realsense_cameras()
    if not cameras:
        print("no cameras found.")
        return 2
    print(f"  cameras: {sorted(cameras)}")
    ctx = {"cameras": cameras}

    try:
        out = s.call("read_camera", context=ctx)
        print(f"\nread_camera success: {out['success']}")
        res = out["result"]
        for k in ("_source", "camera", "width", "height",
                  "center_depth_m", "depth_coverage", "color_path", "depth_path"):
            if k in res:
                print(f"  {k}: {res[k]}")

        # Show the frame rows this call produced.
        rows = s.store.conn.execute(
            "SELECT camera, t, file_path, width, height FROM frames "
            "ORDER BY rowid DESC LIMIT 4"
        ).fetchall()
        print("\nframes table (most recent):")
        for r in rows:
            print(f"  {r['camera']:<14} t={r['t']}  {r['file_path']}  ({r['width']}x{r['height']})")
    finally:
        close_cameras(cameras)
        s.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
