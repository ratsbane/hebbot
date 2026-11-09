"""Target perception on synthetic images."""

from __future__ import annotations

import numpy as np

from hebbot.perception.target import locate


def scene(block, background=(150, 160, 140)):
    image = np.empty((240, 320, 3), dtype=np.uint8)
    image[:] = background
    image[100:140, 140:180] = block
    return image


# A block whose learned colour lies within tolerance of the table.
WASHED_OUT = (120, 160, 110)
VIVID = (40, 200, 40)


def test_without_a_limit_a_washed_out_reference_matches_the_table():
    assert locate(scene(WASHED_OUT), "green")["blobs"][0]["npix"] > 50000


def test_a_washed_out_reference_falls_back_to_the_seed():
    found = locate(scene(WASHED_OUT), "green", max_px=3000)
    assert found["reference_rejected"] is True
    assert found["blobs"][0]["npix"] == 1600
    assert abs(found["blobs"][0]["u"] - 159.5) < 1 and abs(found["blobs"][0]["v"] - 119.5) < 1


def test_a_good_reference_is_kept():
    found = locate(scene(VIVID), "green", max_px=3000)
    assert found["reference_rejected"] is False
    assert found["blobs"][0]["npix"] == 1600


def test_a_good_reference_may_exceed_a_shadowed_seed():
    """Half the block in deep shade: the seed misses it, the learned colour does not."""
    image = scene(VIVID)
    image[100:140, 160:180] = (16, 80, 26)
    found = locate(image, "green", max_px=3000)
    assert found["reference_rejected"] is False


def test_oversized_blobs_are_not_reported():
    image = scene(VIVID)
    image[10:70, 10:110] = VIVID
    found = locate(image, "green", max_px=3000)
    assert all(blob["npix"] <= 3000 for blob in found["blobs"])


def test_nothing_plausible_is_reported_as_not_found():
    image = scene(VIVID)
    image[:] = VIVID
    assert locate(image, "green", max_px=3000)["found"] is False
