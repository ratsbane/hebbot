from __future__ import annotations

import pytest

from hebbot.substrate.skill import _Rollout
from hebbot.substrate.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "hebbot.db")
    yield store
    store.close()


def _episode(store):
    store.register_skill(name="vision_grasp", body_type="classical",
                         body={"function": "x:y"})
    return store.start_episode(skill_name="vision_grasp", params={})


def test_schema_v2_is_applied_to_an_existing_database(tmp_path):
    first = Store(tmp_path / "hebbot.db")
    first.close()
    reopened = Store(tmp_path / "hebbot.db")
    try:
        version = reopened.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert version == "2"
        assert reopened.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone() is not None
    finally:
        reopened.close()


def test_rollout_resolves_state_and_frames_without_guessing(store):
    episode_id = _episode(store)
    store.record_state(episode_id, t=0.0, ee_pose=[1, 0, 0, 0.30] + [0] * 12,
                       gripper_width=0.08)
    store.record_state(episode_id, t=1.5, ee_pose=[1, 0, 0, 0.42] + [0] * 12,
                       gripper_width=0.03)
    # A decoy frame at almost the same instant: nearest-timestamp matching would be a
    # coin flip here, while the event names exactly which one it was taken with.
    decoy = store.record_frame(episode_id, camera="wrist", t=1.49,
                               file_path="frames/decoy.jpg")
    frame_id = store.record_frame(episode_id, camera="wrist", t=1.5,
                                  file_path="frames/before_close_0.jpg")
    store.record_event(episode_id, seq=1, t=1.5, phase="align", kind="observation",
                       label="before_close", data={"attempt": 0},
                       state_t=1.5, frame_ids=[frame_id])

    rollout = store.rollout(episode_id)
    assert rollout["instrumented"] is True
    event = rollout["events"][0]
    assert event["state"]["gripper_width"] == 0.03
    assert event["state"]["ee_pose"][3] == 0.42
    assert [frame["file_path"] for frame in event["frames"]] == \
        ["frames/before_close_0.jpg"]
    assert decoy not in [frame["id"] for frame in event["frames"]]


def test_event_by_label_returns_none_for_an_uninstrumented_episode(store):
    episode_id = _episode(store)
    assert store.event_by_label(episode_id, "before_close") is None
    assert store.rollout(episode_id)["instrumented"] is False


def test_recorder_numbers_events_and_rejects_undeclared_phases(store):
    episode_id = _episode(store)
    rollout = _Rollout(store, episode_id=episode_id, started_at=0.0)
    rollout.record("approach", "observation", label="initial")
    rollout.record("verify", "verdict", label="task_verified",
                   data={"task_verified_success": True})
    assert [event["seq"] for event in store.episode_events(episode_id)] == [1, 2]
    with pytest.raises(ValueError):
        rollout.record("wandering", "observation")
    with pytest.raises(ValueError):
        rollout.record("approach", "vibing")


def test_a_failing_store_loses_the_record_not_the_grasp(store):
    """Instrumentation must never be able to abort a skill mid-motion."""
    class Broken:
        def record_event(self, *args, **kwargs):
            raise RuntimeError("disk full")

    rollout = _Rollout(Broken(), episode_id="e", started_at=0.0)
    assert rollout.record("grasp", "closure", label="closed") is None


def test_warm_start_prefers_the_event_anchor_over_nearest_timestamp(store):
    from hebbot.learning.warm_start import extract_verified_examples

    def pose(x, y, z):
        """Flattened 4x4 with translation at indices 3, 7, 11."""
        flat = [0.0] * 16
        flat[3], flat[7], flat[11] = x, y, z
        return flat

    episode_id = _episode(store)
    store.record_state(episode_id, t=0.0, ee_pose=pose(0.30, 0.0, 0.45))
    # Two candidate pre-close states; only the event says which one it was.
    store.record_state(episode_id, t=1.0, ee_pose=pose(0.99, 0.99, 0.20))
    store.record_state(episode_id, t=1.2, ee_pose=pose(0.42, 0.05, 0.20))
    store.record_frame(episode_id, camera="wrist", t=1.0,
                       file_path="frames/before_close_0.jpg")
    store.record_event(episode_id, seq=1, t=1.2, phase="align", kind="observation",
                       label="before_close", state_t=1.2)
    store.end_episode(episode_id, success=True, outcome={"success": True, "result": {
        "task_verified_success": True,
        "history": [{"u": 300.0, "v": 250.0, "depth_height_m": 0.03}],
        "servo_steps": 5,
    }})

    examples = extract_verified_examples(store)
    assert len(examples) == 1
    assert examples[0]["preclose_source"] == "events"
    # The filename-matched frame points at t=1.0; the event points at t=1.2, and the
    # event wins.
    assert examples[0]["y"] == [0.42, 0.05]
