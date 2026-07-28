from __future__ import annotations

import numpy as np

from hebbot.learning.capability_map import boundary_candidates, ee_to_hand, sample_grid


def test_gripper_down_ee_to_hand_offset_is_positive_z():
    hand = ee_to_hand([0.45, 0.0, 0.20], [0.0, 1.0, 0.0, 0.0])
    assert np.allclose(hand, [0.45, 0.0, 0.303])


def test_map_keeps_ik_and_path_evidence_separate():
    def request(_url, endpoint, body):
        x, y, _ = body["position"]
        if endpoint == "/ik":
            return {"ok": x <= 0.5, "q": [0.0] * 7, "pos_err_mm": 0.1}
        return {"ok": y <= 0.0, "waypoints": [[0.0] * 7] * 3}

    result = sample_grid(
        xs=[0.45, 0.55], ys=[-0.05, 0.05], ee_z=0.2,
        start_q=[0.0] * 7, request=request,
    )
    assert [s["ik_reachable"] for s in result["samples"]] == [True, True, False, False]
    assert [s["path_found"] for s in result["samples"]] == [True, False, None, None]
    candidates = boundary_candidates(result)
    assert len(candidates) == 1
    assert {s["ee_position"][0] for s in candidates} == {0.45}
    assert candidates[0]["ee_position"][1] == -0.05
