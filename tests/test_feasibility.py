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


def test_spatial_grouping_is_independent_of_episode_order():
    from hebbot.learning.feasibility import _spatial_groups

    examples = [
        {"episode_id": "a", "target_pixel": [100.0, 350.0]},
        {"episode_id": "b", "target_pixel": [110.0, 355.0]},
        {"episode_id": "c", "target_pixel": [500.0, 260.0]},
        {"episode_id": "d", "target_pixel": [505.0, 262.0]},
    ]
    forward = _spatial_groups(examples)
    reversed_groups = _spatial_groups(list(reversed(examples)))[::-1]
    assert forward == reversed_groups
    assert forward[0] == forward[1] and forward[2] == forward[3]
    assert forward[0] != forward[2]


def test_artifact_records_the_acquisition_pose_it_is_valid_at():
    examples = _examples()
    for example in examples:
        example["acquire_z"] = 0.45
    result = train_candidate(examples, max_group_loo_brier=0.5)
    assert result.artifact["validity"]["acquire_z"] == 0.45
    assert result.artifact["validity"]["tolerances"]["acquire_z"] == 0.02
