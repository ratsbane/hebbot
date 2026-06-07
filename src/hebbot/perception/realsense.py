"""RealSense camera handles for the substrate.

A `RealSenseCamera` owns a started pyrealsense2 pipeline and hands aligned
color (+ depth) frames to the `read_camera` primitive. Bootstrap lives here so
the primitive stays driver-agnostic — the primitive just calls `.capture()`.

Lab-notebook finding #101: the D405 needs `dev.hardware_reset()` plus a settle
wait *before* `pipeline.start()`, or the pipe starts "successfully" but never
delivers frames. `open_realsense_cameras()` does the reset + settle once across
all connected devices, then starts each pipeline.

Camera roles (the names that land in the `frames.camera` column):
  - D405 -> "wrist"   (gripper-mounted short-range camera)
  - D435 -> "scene"   (fixed scene camera)
  - other -> "cam_<last4 of serial>"
Override with an explicit {serial: name} map.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np


class RealSenseCamera:
    """A started RealSense pipeline plus the metadata read_camera needs."""

    def __init__(self, name, pipeline, align, depth_scale, serial, width, height):
        self.name = name
        self.pipeline = pipeline
        self.align = align
        self.depth_scale = depth_scale
        self.serial = serial
        self.width = width
        self.height = height

    def capture(self, with_depth: bool = True, timeout_ms: int = 5000) -> dict:
        """Grab one frameset. Returns color (BGR uint8) + optional depth (uint16).

        Depth is aligned to the color frame, so a (row, col) in the color image
        indexes the same point in the depth image.
        """
        frames = self.pipeline.wait_for_frames(timeout_ms)
        if self.align is not None:
            frames = self.align.process(frames)
        color = frames.get_color_frame()
        if not color:
            raise RuntimeError(f"camera {self.name!r}: no color frame in frameset")
        color_img = np.asanyarray(color.get_data())  # HxWx3 BGR (bgr8)

        depth_img = None
        if with_depth:
            depth = frames.get_depth_frame()
            depth_img = np.asanyarray(depth.get_data()) if depth else None

        h, w = color_img.shape[:2]
        return {
            "color": color_img,
            "depth": depth_img,
            "depth_scale": self.depth_scale,
            "width": w,
            "height": h,
        }

    def stop(self) -> None:
        try:
            self.pipeline.stop()
        except Exception:
            pass


def _role_for(name: str, serial: str) -> str:
    if "D405" in name:
        return "wrist"
    if "D435" in name or "D455" in name or "D415" in name:
        return "scene"
    return f"cam_{serial[-4:]}"


def open_realsense_cameras(
    name_map: Optional[dict] = None,
    *,
    width: int = 640,
    height: int = 480,
    fps: int = 30,
    settle: float = 5.0,
    warmup: int = 10,
) -> dict:
    """Open every connected Intel RealSense device and return {name: camera}.

    `name_map` optionally maps a device serial to a friendly name; serials not
    in the map fall back to `_role_for`. Does the finding-#101 hardware_reset +
    settle across all devices before starting any pipeline.
    """
    import pyrealsense2 as rs

    name_map = name_map or {}
    ctx = rs.context()
    devices = list(ctx.query_devices())
    if not devices:
        return {}

    # Reset all, then settle once (finding #101) — cheaper than per-camera waits.
    serials = []
    for dev in devices:
        serial = dev.get_info(rs.camera_info.serial_number)
        serials.append(serial)
        dev.hardware_reset()
    time.sleep(settle)

    cameras: dict[str, RealSenseCamera] = {}
    for dev, serial in zip(devices, serials):
        dev_name = dev.get_info(rs.camera_info.name)
        friendly = name_map.get(serial) or _role_for(dev_name, serial)

        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_device(serial)
        config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

        profile = pipeline.start(config)
        depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()

        # Let auto-exposure settle so the first real capture is usable.
        for _ in range(warmup):
            pipeline.wait_for_frames(timeout_ms=5000)

        cameras[friendly] = RealSenseCamera(
            name=friendly,
            pipeline=pipeline,
            align=rs.align(rs.stream.color),
            depth_scale=depth_scale,
            serial=serial,
            width=width,
            height=height,
        )

    return cameras


def close_cameras(cameras: dict) -> None:
    for cam in (cameras or {}).values():
        cam.stop()
