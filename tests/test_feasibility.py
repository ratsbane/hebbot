from __future__ import annotations

from hebbot.learning.feasibility import predict, train_candidate


def _examples():
    points = [
        (100, 350, 0, 0), (140, 360, 0, 1),
        (430, 370, 0, 2), (460, 380, 0, 3),
        (500, 260, 1, 4), (515, 275, 1, 5),
        (530, 220, 1, 6), (545, 235, 1, 7),
    ]
    return [{
        "episode_id": f"ep-{i}", "target_pixel": [u, v],
        "x": [(u - 320) / 320, (v - 240) / 240],
        "y": y, "spatial_group": group,
    } for i, (u, v, y, group) in enumerate(points)]


def test_controller_envelope_candidate_promotes_spatially_diverse_batch():
    result = train_candidate(_examples(), max_group_loo_brier=0.5)
    assert result.promoted, result.reasons
    assert predict(result.artifact, [530, 240]) > predict(result.artifact, [120, 350])


def test_controller_envelope_candidate_refuses_one_positive_region():
    examples = _examples()
    for e in examples:
        if e["y"] == 1:
            e["spatial_group"] = 9
    result = train_candidate(examples, max_group_loo_brier=1.0)
    assert not result.promoted
    assert any("controller-limited spatial groups" in reason for reason in result.reasons)
