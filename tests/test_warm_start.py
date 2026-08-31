from __future__ import annotations

import tempfile

import pytest

from hebbot import Substrate
from hebbot.learning.warm_start import propose, train_candidate
from hebbot.skills.primitives import register_all


def _examples(n=8):
    out = []
    for i in range(n):
        u = -0.5 + i / max(n - 1, 1)
        v = -0.3 + 0.6 * ((i * 3) % n) / max(n - 1, 1)
        x = [u, v, 0.0, 0.308, 0.0]
        out.append({
            "episode_id": f"ep-{i}", "started_at": float(i), "x": x,
            "y": [0.42 + 0.10 * u, 0.02 + 0.10 * v],
            # The start pose the features already encode (x[3], x[4]); the usefulness
            # gate measures travel from here to the achieved pre-close xy.
            "start_xyz": [0.308, 0.0, 0.45],
            "servo_steps": 7,
        })
    return out


def test_candidate_refuses_too_few_near_duplicate_examples():
    result = train_candidate(_examples(2), min_samples=6)
    assert not result.promoted
    assert any("minimum 6" in reason for reason in result.reasons)


def test_candidate_promotes_diverse_low_error_batch_and_gates_domain():
    result = train_candidate(_examples(), min_samples=6, max_loo_error_m=0.02)
    assert result.promoted, result.reasons
    inside = propose(result.artifact, _examples()[3]["x"])
    assert inside["accepted"]
    outside = propose(result.artifact, [20.0, 20.0, 0.0, 0.308, 0.0])
    assert not outside["accepted"]


def test_store_exposes_episode_states_and_frames():
    with tempfile.TemporaryDirectory() as tmp:
        substrate = Substrate(tmp)
        register_all(substrate)
        ep = substrate.store.start_episode("vision_grasp", body_type_used="classical")
        substrate.store.record_state(ep, t=1.0, ee_pose=list(range(16)))
        substrate.store.record_frame(
            ep, camera="wrist", t=1.0, file_path="frames/example.jpg",
        )
        assert substrate.store.episode_states(ep)[0]["ee_pose"] == list(range(16))
        assert substrate.store.episode_frames(ep)[0]["file_path"] == "frames/example.jpg"
        substrate.close()


def _useful_examples(n=8):
    """Predictions that land far closer to the achieved pose than the arm started."""
    examples = []
    for i in range(n):
        u = 200.0 + 30.0 * i
        target = [0.36 + 0.02 * i, -0.10 + 0.025 * i]
        examples.append({
            "episode_id": f"ep-{i}",
            "started_at": float(i),
            "x": [(u - 320.0) / 320.0, 0.1, 0.0, 0.45, 0.0],
            "y": target,
            "start_xyz": [0.45, 0.0, 0.45],
            "acquire_z": 0.45,
            "servo_steps": 6,
        })
    return examples


def test_accurate_but_useless_warm_start_is_refused():
    from hebbot.learning.warm_start import train_candidate

    examples = _useful_examples()
    # Start each episode already at its achieved pose: the prediction can be perfect and
    # still remove no travel, which is a policy worth nothing.
    for example in examples:
        example["start_xyz"] = [example["y"][0], example["y"][1], 0.45]
    result = train_candidate(examples)
    assert not result.promoted
    assert any("not useful" in reason for reason in result.reasons)


def test_useful_warm_start_records_its_travel_reduction():
    from hebbot.learning.warm_start import train_candidate

    result = train_candidate(_useful_examples())
    assert result.artifact["median_travel_reduction_m"] > 0.02
    assert result.artifact["improved_fraction"] == 1.0
    assert result.artifact["validity"]["acquire_z"] == 0.45


def test_feature_vector_matches_the_training_featuriser():
    from hebbot.learning.warm_start import FEATURE_NAMES, feature_vector

    features = feature_vector({"u": 320.0, "v": 240.0, "depth_height_m": 0.03},
                              [0.45, 0.1, 0.45])
    assert len(features) == len(FEATURE_NAMES)
    assert features[:3] == [0.0, 0.0, 0.0]
    assert features[3:] == [0.45, 0.1]
