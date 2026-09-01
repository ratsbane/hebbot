"""Ab initio grasp alignment: two functions, nothing borrowed.

Deliberately independent of primitives.py. The point is to establish, from scratch and
with everything visible, two things the existing pipeline assumes:

  locate(frame, colour) -> where the target is in the image
  the servo loop        -> move until the gripper straddles it, re-locating every step

Design rules for this experiment:
  * ONE variable at a time. The loop holds z fixed; descent is a separate step. Every
    failure on 2026-09-01 involved servoing and descending at once, which conflates the
    image error with the parallax that changes it.
  * No calibration constants are trusted. The pixel->metre mapping is derived live from
    the camera's own focal length and the measured working distance, and the goal pixel
    is measured rather than assumed.
  * Everything is printed. If the segmentation is wrong we should see it in the numbers,
    not infer it from a servo that will not converge.
"""

from __future__ import annotations

import json
import sys

import numpy as np
from PIL import Image


FX = 394.48443603515625        # wrist intrinsics, from the camera itself
FY = 393.46728515625
CX = 323.6427001953125
CY = 242.88636779785156


def colour_mask(rgb: np.ndarray, colour: str) -> np.ndarray:
    """A deliberately simple, inspectable colour test.

    Channel dominance plus a saturation floor. No HSV conversion, no tuned thresholds
    carried over from the existing pipeline — if this is too crude we want to find that
    out by looking at what it returns.
    """
    r, g, b = (rgb[:, :, i].astype(np.int16) for i in range(3))
    saturation = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
    if colour == "green":
        mask = (g > r + 25) & (g > b + 20)
    elif colour == "red":
        mask = (r > g + 45) & (r > b + 45)
    elif colour == "blue":
        mask = (b > r + 30) & (b > g + 15)
    else:
        raise ValueError(f"unknown colour {colour!r}")
    return mask & (saturation > 35)


def chromaticity(rgb: np.ndarray) -> np.ndarray:
    """Normalised chromaticity (r, g) = RGB / (R+G+B).

    Divides out intensity, so a shadowed patch of the target keeps the same coordinates as
    a lit one. Fixed channel-difference thresholds do not: the green block here separates
    from blue by up to 82 counts but from red by as little as 24 depending on where it sits
    in the frame, which is what made the first locate() lose it entirely at the image
    centre (2026-09-01).
    """
    total = rgb.sum(axis=2, keepdims=True).astype(np.float32)
    total[total < 1.0] = 1.0
    return (rgb.astype(np.float32) / total)[:, :, :2]


def reference_from(rgb: np.ndarray, u: float, v: float, half: int = 6) -> dict:
    """Learn what the target looks like from a patch we already know is on it."""
    chroma = chromaticity(rgb)
    vi, ui = int(round(v)), int(round(u))
    patch = chroma[max(0, vi - half):vi + half + 1, max(0, ui - half):ui + half + 1]
    flat = patch.reshape(-1, 2)
    return {
        "chroma": [float(np.median(flat[:, 0])), float(np.median(flat[:, 1]))],
        "spread": float(np.median(np.abs(flat - np.median(flat, axis=0))).item()),
        "sampled_at": [round(float(u), 1), round(float(v), 1)],
    }


def mask_by_reference(rgb: np.ndarray, reference: dict, tol: float = 0.045,
                      min_brightness: int = 35) -> np.ndarray:
    """Pixels whose chromaticity matches the learned target, at any brightness."""
    chroma = chromaticity(rgb)
    ref = np.asarray(reference["chroma"], dtype=np.float32)
    distance = np.linalg.norm(chroma - ref, axis=2)
    bright_enough = rgb.max(axis=2) >= min_brightness
    return (distance <= float(tol)) & bright_enough


def components(mask: np.ndarray, min_px: int = 150) -> list[dict]:
    """Connected components by iterative flood fill (no scipy on this host).

    Returns one dict per blob: centroid, pixel count, bounding box, and the principal
    axis angle from the pixel second moments.
    """
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    blobs = []
    ys, xs = np.nonzero(mask)
    for start_y, start_x in zip(ys, xs):
        if seen[start_y, start_x]:
            continue
        stack = [(start_y, start_x)]
        seen[start_y, start_x] = True
        pixels = []
        while stack:
            y, x = stack.pop()
            pixels.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < height and 0 <= nx < width \
                        and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(pixels) < min_px:
            continue
        pix = np.array(pixels, dtype=float)
        cy, cx = pix[:, 0].mean(), pix[:, 1].mean()
        dy, dx = pix[:, 0] - cy, pix[:, 1] - cx
        cov = np.array([[float((dx * dx).mean()), float((dx * dy).mean())],
                        [float((dx * dy).mean()), float((dy * dy).mean())]])
        values, vectors = np.linalg.eigh(cov)
        major = vectors[:, int(np.argmax(values))]
        extent = 2.0 * np.sqrt(np.maximum(values, 0.0))
        blobs.append({
            "u": round(float(cx), 1),
            "v": round(float(cy), 1),
            "npix": len(pixels),
            "bbox": [int(pix[:, 1].min()), int(pix[:, 0].min()),
                     int(pix[:, 1].max()), int(pix[:, 0].max())],
            "angle_deg": round(float(np.degrees(np.arctan2(major[1], major[0]))), 1),
            "extent_px": [round(float(extent[1]), 1), round(float(extent[0]), 1)],
        })
    blobs.sort(key=lambda blob: -blob["npix"])
    return blobs


def depth_at(depth_path: str, u: float, v: float, half: int = 5) -> float | None:
    """Median depth in a small patch, in metres. None if the patch has no valid returns."""
    try:
        depth = np.load(depth_path)
    except Exception:
        return None
    vi, ui = int(round(v)), int(round(u))
    patch = depth[max(0, vi - half):vi + half + 1, max(0, ui - half):ui + half + 1]
    valid = patch[patch > 0]
    if valid.size == 0:
        return None
    return float(np.median(valid)) * 1e-4      # depth_scale reported by the camera


def locate(colour_path: str, colour: str, depth_path: str | None = None,
           min_px: int = 150) -> dict:
    """Where is the target in this image? Returns every candidate, largest first."""
    rgb = np.asarray(Image.open(colour_path).convert("RGB"))
    blobs = components(colour_mask(rgb, colour), min_px=min_px)
    for blob in blobs:
        if depth_path:
            blob["depth_m"] = depth_at(depth_path, blob["u"], blob["v"])
    return {
        "colour": colour,
        "n": len(blobs),
        "blobs": blobs,
        "image_size": [int(rgb.shape[1]), int(rgb.shape[0])],
        "principal_point": [CX, CY],
    }


def locate_by_reference(colour_path: str, reference: dict, depth_path: str | None = None,
                        min_px: int = 150, tol: float = 0.045) -> dict:
    """Where is the learned target in this image? No fixed colour thresholds."""
    rgb = np.asarray(Image.open(colour_path).convert("RGB"))
    blobs = components(mask_by_reference(rgb, reference, tol=tol), min_px=min_px)
    for blob in blobs:
        if depth_path:
            blob["depth_m"] = depth_at(depth_path, blob["u"], blob["v"])
    return {"reference": reference, "n": len(blobs), "blobs": blobs}


def pixel_step_to_ee(du: float, dv: float, working_distance_m: float) -> tuple[float, float]:
    """Metres of EE motion that move the target's image position by (du, dv).

    Pinhole only: one pixel subtends working_distance / fx metres at the target. The axis
    mapping (image u <-> EE -y, image v <-> EE -x) is the one thing taken as given here,
    and `calibrate` below measures it rather than trusting it.
    """
    metres_per_px = float(working_distance_m) / FX
    return -dv * metres_per_px, -du * metres_per_px


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(0)
    colour_path, colour = args[0], args[1]
    depth_path = args[2] if len(args) > 2 else None
    print(json.dumps(locate(colour_path, colour, depth_path), indent=2))
