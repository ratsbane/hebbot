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
