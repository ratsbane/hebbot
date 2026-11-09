"""Target perception that learns what it is looking for instead of being told.

Validated ab initio on hardware 2026-09-01. The fixed RGB-dominance thresholds used
elsewhere are not stable across the frame: the green block separates from blue by up to 82
counts but from red by as little as 24, depending on where it sits, which was enough to
lose it entirely. Normalised chromaticity divides intensity out, and sampling the target's
own colour removes the tuned constants altogether.

Measured on the same block across three poses: npix 729 / 765 / 762, against 3293-vs-762
from the fixed-threshold path on an identical pose.
"""

from __future__ import annotations

import numpy as np


def chromaticity(rgb: np.ndarray) -> np.ndarray:
    """Normalised (r, g) = RGB / (R+G+B). Intensity-invariant, so shadow does not matter."""
    total = rgb.sum(axis=2, keepdims=True).astype(np.float32)
    total[total < 1.0] = 1.0
    return (rgb.astype(np.float32) / total)[:, :, :2]


def reference_from(rgb: np.ndarray, u: float, v: float, half: int = 6) -> dict:
    """Learn the target's appearance from a patch known to be on it."""
    chroma = chromaticity(rgb)
    vi, ui = int(round(v)), int(round(u))
    patch = chroma[max(0, vi - half):vi + half + 1,
                   max(0, ui - half):ui + half + 1].reshape(-1, 2)
    return {"chroma": [float(np.median(patch[:, 0])), float(np.median(patch[:, 1]))],
            "sampled_at": [round(float(u), 1), round(float(v), 1)]}


def mask_by_reference(rgb: np.ndarray, reference: dict, tol: float = 0.10,
                      min_brightness: int = 35) -> np.ndarray:
    chroma = chromaticity(rgb)
    ref = np.asarray(reference["chroma"], dtype=np.float32)
    return (np.linalg.norm(chroma - ref, axis=2) <= float(tol)) \
        & (rgb.max(axis=2) >= min_brightness)


def seed_mask(rgb: np.ndarray, colour: str) -> np.ndarray:
    """Coarse first guess, used only to find a patch to learn the reference from."""
    r, g, b = (rgb[:, :, i].astype(np.int16) for i in range(3))
    saturation = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
    if colour == "green":
        mask = (g > r + 15) & (g > b + 15)
    elif colour == "red":
        mask = (r > g + 30) & (r > b + 30)
    elif colour == "blue":
        mask = (b > r + 20) & (b > g + 10)
    else:
        raise ValueError(f"unknown colour {colour!r}")
    return mask & (saturation > 25)


def components(mask: np.ndarray, min_px: int = 150) -> list[dict]:
    """Connected components by flood fill (no scipy on the robot host)."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    blobs = []
    for start_y, start_x in zip(*np.nonzero(mask)):
        if seen[start_y, start_x]:
            continue
        stack, pixels = [(start_y, start_x)], []
        seen[start_y, start_x] = True
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
            "u": round(float(cx), 1), "v": round(float(cy), 1), "npix": len(pixels),
            "angle_deg": round(float(np.degrees(np.arctan2(major[1], major[0]))), 1),
            "extent_px": [round(float(extent[1]), 1), round(float(extent[0]), 1)],
        })
    blobs.sort(key=lambda blob: -blob["npix"])
    return blobs


def locate(rgb: np.ndarray, colour: str, depth: np.ndarray | None = None,
           depth_scale: float = 1e-4, min_px: int = 150, tol: float = 0.10,
           max_px: int | None = None, use_reference: bool = True) -> dict:
    """Find the target: seed on colour, learn its chromaticity, then re-find by that.

    `max_px` is the largest area the target could physically cover at this distance. Close
    up, glare washes out the patch the reference is learned from until the table matches it
    too, or a shadow merges with the target. On 2026-11-09 that gave one blob of 200,000
    pixels for a block of about 2,300. A ratio against the seed cannot catch this, because
    a good far view can legitimately be 3-6x its seed when shadow halves the seed. If the
    blob matching the seed is larger than a target can be, the reference is thrown away and
    the seed blobs are reported instead.

    `use_reference=False` skips the learning step and reports the seed blobs. Close views
    want this: the seed centred the block in every close frame checked on 2026-11-09, while
    the learned reference there is unreliable because glare sits on the patch it learns from.
    """
    seeds = components(seed_mask(rgb, colour), min_px=min_px)
    if not seeds:
        return {"found": False, "reason": f"no {colour} seed blob", "blobs": []}
    reference = reference_from(rgb, seeds[0]["u"], seeds[0]["v"])
    blobs = seeds if not use_reference \
        else components(mask_by_reference(rgb, reference, tol=tol), min_px=min_px)
    if not blobs:
        return {"found": False, "reason": "reference matched nothing", "blobs": [],
                "reference": reference}
    reference_rejected = False
    if max_px is not None:
        seed = seeds[0]
        match = min(blobs, key=lambda b: (b["u"] - seed["u"]) ** 2 + (b["v"] - seed["v"]) ** 2)
        if match["npix"] > max_px:
            reference_rejected = True
            blobs = [blob for blob in seeds if blob["npix"] <= max_px]
        else:
            blobs = [blob for blob in blobs if blob["npix"] <= max_px]
        if not blobs:
            return {"found": False, "reason": "nothing of plausible size", "blobs": [],
                    "reference": reference, "reference_rejected": reference_rejected}
    if depth is not None:
        for blob in blobs:
            vi, ui = int(round(blob["v"])), int(round(blob["u"]))
            patch = depth[max(0, vi - 5):vi + 6, max(0, ui - 5):ui + 6]
            valid = patch[patch > 0]
            blob["depth_m"] = (float(np.median(valid)) * depth_scale
                               if valid.size else None)
    return {"found": True, "reference": reference, "blobs": blobs, "n": len(blobs),
            "reference_rejected": reference_rejected}
