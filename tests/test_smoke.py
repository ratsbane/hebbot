"""Smoke tests for the substrate, primitives, and composition body type."""

from __future__ import annotations

import tempfile

import pytest

from hebbot import Substrate
from hebbot.skills.primitives import register_all as register_primitives
from hebbot.skills.composed import register_all as register_composed


@pytest.fixture
def substrate():
    with tempfile.TemporaryDirectory() as tmp:
        s = Substrate(tmp)
        register_primitives(s)
        register_composed(s)
        yield s
        s.close()


# ---------------------------------------------------------------------------
# Primitive registration + dispatch
# ---------------------------------------------------------------------------

def test_all_skills_registered(substrate):
    names = {sk["name"] for sk in substrate.list_skills()}
    assert "get_state" in names
    assert "move_to_joints" in names
    assert "move_to_pose" in names
    assert "open_gripper" in names
    assert "close_gripper" in names
    assert "read_camera" in names
    assert "compliant_hold" in names
    assert "teleop_xbox" in names
    assert "home" in names


def test_teleop_xbox_mock(substrate):
    # No panda in context -> mock mode, no hardware/gamepad touched.
    out = substrate.call("teleop_xbox")
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_get_state_mock(substrate):
    out = substrate.call("get_state")
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_compliant_hold_mock(substrate):
    out = substrate.call("compliant_hold", params={"duration_s": 0.25})
    assert out["success"]
    assert out["result"]["completed"] is True
    assert out["result"]["max_position_drift_m"] == 0.0


def test_descent_runtime_scales_with_distance_and_speed():
    from hebbot.skills.primitives import _descent_context_runtime

    assert _descent_context_runtime(0.20, 0.026, 0.02) == pytest.approx(10.1)
    assert _descent_context_runtime(0.04, 0.026, 0.02) == pytest.approx(2.1)
    assert _descent_context_runtime(0.026, 0.026, 0.02) == pytest.approx(2.0)


def test_persistent_high_contact_requires_air_close_above_target():
    from hebbot.skills.primitives import _persistent_high_contact

    assert _persistent_high_contact(0.0437, 0.026, False, 0.006)
    assert not _persistent_high_contact(0.030, 0.026, False, 0.006)
    assert not _persistent_high_contact(0.0437, 0.026, True, 0.006)


def test_alignment_depth_update_preserves_grasp_axis_geometry():
    from hebbot.skills.primitives import _update_alignment_geometry

    original = {
        "u": 365.0, "v": 357.0, "height_m": 0.029,
        "length_m": 0.057, "width_m": 0.028, "ang": 103.0,
    }
    updated = _update_alignment_geometry(
        original, (350.0, 336.0, 5200, 0.027),
    )
    assert (updated["u"], updated["v"], updated["height_m"]) == (350.0, 336.0, 0.027)
    assert updated["length_m"] == original["length_m"]
    assert updated["width_m"] == original["width_m"]
    assert updated["ang"] == original["ang"]


def test_final_pose_tolerance_is_derived_from_per_side_jaw_clearance():
    from hebbot.skills.primitives import _final_pose_alignment_tolerance_px

    # The 2026-07-25 failed trial: 6 mm physical clearance on each side at
    # 303.5 mm depth is 7.8 px; reserve 2 px for calibration/segmentation.
    tol = _final_pose_alignment_tolerance_px(
        open_width=0.0433,
        object_width=0.0313,
        object_depth=0.3035,
        requested_tol=10.0,
    )
    assert tol == pytest.approx(5.8, abs=0.1)
    assert _final_pose_alignment_tolerance_px(
        0.06, 0.02, 0.30, requested_tol=5.0,
    ) == pytest.approx(5.0)
    assert _final_pose_alignment_tolerance_px(
        0.031, 0.030, 0.30, requested_tol=10.0,
    ) == pytest.approx(3.0)


def test_yawed_recenter_rotates_reference_planar_delta():
    import math
    from hebbot.skills.primitives import _rotate_planar_delta

    assert _rotate_planar_delta(0.01, 0.0, 0.0) == pytest.approx((0.01, 0.0))
    assert _rotate_planar_delta(0.01, 0.0, math.pi / 2) == pytest.approx(
        (0.0, 0.01), abs=1e-9,
    )
    assert _rotate_planar_delta(0.0, 0.01, -math.pi / 2) == pytest.approx(
        (0.01, 0.0), abs=1e-9,
    )


def test_servo_prediction_uses_clamped_cartesian_command():
    from hebbot.skills.primitives import _predict_servo_pixel

    # 1 mm/px diagonal inverse Jacobian: a desired 200 px correction clamped to
    # 60 mm must predict 60 px of image motion, not the unreachable target pixel.
    predicted = _predict_servo_pixel(
        140, 330, [[0.001, 0.0], [0.0, 0.001]], 0.060, -0.020,
    )
    assert predicted == pytest.approx([200, 310])


def test_workspace_saturation_requires_meaningful_severe_clipping():
    from hebbot.skills.primitives import _workspace_saturated

    assert _workspace_saturated(0.0, 0.020, 0.0, 0.003)
    assert not _workspace_saturated(0.0, 0.020, 0.0, 0.006)
    assert not _workspace_saturated(0.0, 0.001, 0.0, 0.0)
    assert not _workspace_saturated(0.020, 0.0, 0.020, 0.0)
    # Retaining the smaller Y request does not help when dominant X is pinned.
    assert _workspace_saturated(-0.030, 0.009, 0.0005, 0.009)


def test_vision_grasp_probe_forces_recorded_no_descent_episode(monkeypatch):
    import hebbot.skills.primitives as primitives

    called = {}

    def fake_vision_grasp(**kwargs):
        called.update(kwargs)
        return {"dry_run": kwargs["dry_run"], "safe_to_descend": True}

    monkeypatch.setattr(primitives, "vision_grasp", fake_vision_grasp)
    out = primitives.vision_grasp_probe(
        color="red", target=[320, 330], speed_factor=0.2,
    )
    assert out["dry_run"]
    assert called["experience_mode"] == "record"
    assert called["max_retry"] == 0
    assert called["speed_factor"] == pytest.approx(0.05)

    monkeypatch.setattr(
        primitives, "vision_grasp",
        lambda **kwargs: {"located": False, "note": "coarse servo failed"},
    )
    failed = primitives.vision_grasp_probe(color="red")
    assert failed["dry_run"]
    assert not failed["safe_to_descend"]

    invalid = primitives.vision_grasp_probe(color="red", instance="middle")
    assert not invalid["safe_to_descend"]

    invalid_memory = primitives.vision_grasp_probe(
        color="red", feasibility_mode="invented",
    )
    assert not invalid_memory["safe_to_descend"]


def test_saturation_memory_recall_is_scoped_by_color_and_acquisition_pose(
    substrate, monkeypatch,
):
    import hebbot.skills.primitives as primitives
    from hebbot.skills.primitives import _recall_saturation_memories

    def add(color, acquire_z, target, note):
        ep = substrate.store.start_episode(
            "vision_grasp_probe", params={"color": color, "acquire_z": acquire_z},
        )
        substrate.store.end_episode(ep, success=False, outcome={
            "success": False,
            "result": {
                "note": note,
                "instance_selection": {"target": target},
            },
        })
        return ep

    match = add("green", 0.45, [501, 266], "workspace saturation: test")
    add("red", 0.45, [501, 266], "workspace saturation: wrong color")
    add("green", 0.34, [501, 266], "workspace saturation: wrong pose")
    add("green", 0.45, [300, 300], "coarse servo did not converge")

    recalled = _recall_saturation_memories(substrate.store, "green", 0.45)
    assert [m["episode_id"] for m in recalled] == [match]
    assert recalled[0]["target"] == [501.0, 266.0]

    class Panda:
        def get_position(self):
            return [0.4, 0.0, 0.45]

    monkeypatch.setattr(primitives, "move_to_pose", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        primitives, "find_blocks",
        lambda **kwargs: {"blocks": [{"u": 505, "v": 270, "npix": 2000}]},
    )
    out = primitives.vision_grasp_probe(
        color="green", instance="leftmost", feasibility_mode="avoid_saturation",
        acquire_z=0.45, _context={"panda": Panda()}, _store=substrate.store,
    )
    assert out["note"].startswith("deliberate abstention")
    assert out["feasibility_memory"]["retrieved_episode_ids"] == [match]
    assert not out["safe_to_descend"]


def test_feasibility_recovery_probe_replays_recorded_fallback(monkeypatch):
    import hebbot.skills.primitives as primitives

    calls = []

    def fake_probe(**kwargs):
        calls.append((kwargs["color"], kwargs["instance"]))
        if kwargs["color"] == "green":
            return {
                "safe_to_descend": False,
                "note": "deliberate abstention: remembered saturation",
                "feasibility_memory": {"retrieved_episode_ids": ["sat-1"]},
            }
        return {"safe_to_descend": True, "note": "probe passed"}

    monkeypatch.setattr(primitives, "vision_grasp_probe", fake_probe)
    monkeypatch.setattr(
        primitives, "_recall_probe_recoveries",
        lambda *args, **kwargs: [{
            "episode_id": "recovery-1",
            "fallback": {"color": "red", "instance": "rightmost"},
            "trigger_episode_ids": ["sat-1"],
        }],
    )

    class Decision:
        def record(self, decision, **kwargs):
            self.decision = decision
            self.kwargs = kwargs

    decision = Decision()
    out = primitives.feasibility_recovery_probe(
        requested_color="green", requested_instance="leftmost",
        recovery_mode="replay", _log_decision=decision.record,
    )
    assert calls == [("green", "leftmost"), ("red", "rightmost")]
    assert out["safe_to_descend"]
    assert out["recovery"]["source_episode_id"] == "recovery-1"
    assert out["recovery"]["trigger_episode_ids"] == ["sat-1"]
    assert decision.decision == "replay"


def test_feasibility_recovery_record_requires_deliberate_fallback(monkeypatch):
    import hebbot.skills.primitives as primitives

    monkeypatch.setattr(
        primitives, "vision_grasp_probe",
        lambda **kwargs: {
            "safe_to_descend": False,
            "note": "deliberate abstention: remembered saturation",
            "feasibility_memory": {"retrieved_episode_ids": ["sat-1"]},
        },
    )
    out = primitives.feasibility_recovery_probe(
        requested_color="green", recovery_mode="record",
    )
    assert not out["safe_to_descend"]
    assert out["note"] == "record mode requires fallback_color"


def test_read_camera_mock(substrate):
    # No camera in context -> mock mode, no file written.
    out = substrate.call("read_camera")
    assert out["success"]
    assert out["result"]["_source"] == "mock"
    assert out["result"]["color_path"] is None


def test_read_camera_writes_frame_row(substrate):
    # A fake camera lets us exercise the save + frames-table path without
    # hardware. Requires PIL for the JPEG write.
    pytest.importorskip("PIL")
    import numpy as np

    class FakeCamera:
        name = "wrist"

        def capture(self, with_depth=True):
            return {
                "color": np.zeros((8, 8, 3), dtype=np.uint8),
                "depth": (np.ones((8, 8), dtype=np.uint16) * 500) if with_depth else None,
                "depth_scale": 0.001,
                "width": 8,
                "height": 8,
            }

    ctx = {"cameras": {"wrist": FakeCamera()}}
    out = substrate.call("read_camera", context=ctx)
    assert out["success"]
    res = out["result"]
    assert res["_source"] == "live"
    assert res["camera"] == "wrist"
    assert res["color_path"] and res["depth_path"]
    assert res["center_depth_m"] == pytest.approx(0.5)  # 500 * 0.001

    # A color frame row + a depth frame row, both tied to a real episode.
    rows = substrate.store.conn.execute(
        "SELECT camera, episode_id, file_path FROM frames ORDER BY camera"
    ).fetchall()
    cams = {r["camera"] for r in rows}
    assert cams == {"wrist", "wrist:depth"}
    for r in rows:
        ep = substrate.store.get_episode(r["episode_id"])
        assert ep is not None and ep["skill_name"] == "read_camera"


def test_read_camera_internal_tags_preserve_multiple_checkpoints(substrate):
    pytest.importorskip("PIL")
    import numpy as np
    from hebbot.skills.primitives import read_camera

    class FakeCamera:
        name = "wrist"

        def capture(self, with_depth=True):
            return {
                "color": np.zeros((8, 8, 3), dtype=np.uint8),
                "depth": np.ones((8, 8), dtype=np.uint16) if with_depth else None,
                "depth_scale": 0.001,
                "width": 8,
                "height": 8,
            }

    ep = substrate.store.start_episode("vision_grasp", body_type_used="classical")
    ctx = {"cameras": {"wrist": FakeCamera()}}
    read_camera(_context=ctx, _store=substrate.store, _episode_id=ep,
                _frame_tag="before", _t=1.0)
    read_camera(_context=ctx, _store=substrate.store, _episode_id=ep,
                _frame_tag="after", _t=2.0)
    rows = substrate.store.conn.execute(
        "SELECT camera, t, file_path FROM frames WHERE episode_id=? ORDER BY t, camera", (ep,)
    ).fetchall()
    assert len(rows) == 4
    assert {r["t"] for r in rows} == {1.0, 2.0}
    assert any("before" in r["file_path"] for r in rows)
    assert any("after" in r["file_path"] for r in rows)


def test_read_camera_can_persist_exact_injected_capture(substrate):
    pytest.importorskip("PIL")
    import numpy as np
    from hebbot.skills.primitives import read_camera

    class NoCaptureCamera:
        name = "wrist"

        def capture(self, with_depth=True):
            raise AssertionError("read_camera recaptured instead of persisting injected evidence")

    cap = {
        "color": np.zeros((8, 8, 3), dtype=np.uint8),
        "depth": np.full((8, 8), 777, dtype=np.uint16),
        "depth_scale": 0.001,
        "width": 8,
        "height": 8,
    }
    ep = substrate.store.start_episode("vision_grasp", body_type_used="classical")
    out = read_camera(
        _context={"cameras": {"wrist": NoCaptureCamera()}},
        _store=substrate.store, _episode_id=ep, _frame_tag="verified",
        _capture=cap,
    )
    assert out["center_depth_m"] == pytest.approx(0.777)
    assert "verified" in out["color_path"]
    assert "verified" in out["depth_path"]


def test_relational_grasp_recall_only_uses_task_verified_episodes(substrate):
    from hebbot.skills.primitives import (
        _grasp_relational_state, _grasp_state_similarity, _recall_grasp_experiences,
    )

    geom = {
        "u": 350.0, "v": 340.0, "length_m": 0.065, "width_m": 0.031,
        "height_m": 0.029, "ang": 80.0,
    }
    state = _grasp_relational_state("green", geom, [0.45, 0.0, 0.30])
    nearby = _grasp_relational_state(
        "green", {**geom, "u": 354.0, "ang": 84.0}, [0.46, 0.01, 0.30]
    )
    wrong_color = _grasp_relational_state("red", geom, [0.45, 0.0, 0.30])
    assert _grasp_state_similarity(state, nearby) > 0.8
    assert _grasp_state_similarity(state, wrong_color) == 0.0

    def add_episode(verified, rel_state, grip_z):
        ep = substrate.store.start_episode(
            "vision_grasp", params={"experience_mode": "record"},
            body_type_used="classical",
        )
        substrate.store.end_episode(ep, success=True, outcome={
            "success": True,
            "result": {
                "task_verified_success": verified,
                "verification_method": "test",
                "experience": {
                    "relational_state": rel_state,
                    "action": {
                        "grip_z": grip_z, "grip_width": 0.027,
                        "open_width": 0.043, "grip_yaw_deg": 10,
                    },
                },
            },
        })
        return ep

    rejected = add_episode(False, state, 0.099)
    accepted = add_episode(True, nearby, 0.026)
    recalled = _recall_grasp_experiences(substrate.store, state)
    assert [r["episode_id"] for r in recalled] == [accepted]
    assert rejected not in [r["episode_id"] for r in recalled]
    assert recalled[0]["action"]["grip_z"] == 0.026


def test_aligned_depth_gate_rejects_flat_color_shadow():
    import numpy as np
    from hebbot.skills.primitives import _nearest_raised_instance

    # BGR frame with two red components. The closer component to `track` is a flat red
    # artifact at table depth; the farther component is a physical block 30 mm high.
    color = np.zeros((100, 100, 3), dtype=np.uint8)
    color[15:30, 15:30] = [0, 0, 200]       # flat shadow/reflection
    color[55:75, 60:80] = [0, 0, 200]       # real raised block
    depth = np.full((100, 100), 1000, dtype=np.uint16)
    depth[55:75, 60:80] = 970
    cap = {"color": color, "depth": depth, "depth_scale": 0.001}

    found = _nearest_raised_instance(
        cap, "red", track=[20, 20], min_px=100, min_height_m=0.008,
    )
    assert found is not None
    u, v, npix, height = found
    assert (u, v) == pytest.approx((69.5, 64.5))
    assert npix == 400
    assert height == pytest.approx(0.03)


def test_aligned_depth_gate_rejects_missing_depth():
    import numpy as np
    from hebbot.skills.primitives import _nearest_raised_instance

    color = np.zeros((40, 40, 3), dtype=np.uint8)
    color[10:30, 10:30] = [0, 0, 200]
    cap = {
        "color": color,
        "depth": np.zeros((40, 40), dtype=np.uint16),
        "depth_scale": 0.001,
    }
    assert _nearest_raised_instance(cap, "red", [20, 20], min_px=100) is None


def test_depth_identity_gate_rejects_height_change_and_large_track_jump():
    import numpy as np
    from hebbot.skills.primitives import _nearest_raised_instance

    color = np.zeros((160, 180, 3), dtype=np.uint8)
    color[55:85, 110:140] = [200, 80, 20]
    depth = np.full((160, 180), 1000, dtype=np.uint16)
    depth[55:85, 110:140] = 930  # robot-like blue structure, 70 mm relief
    cap = {"color": color, "depth": depth, "depth_scale": 0.001}

    assert _nearest_raised_instance(
        cap, "blue", [125, 70], min_px=600, expected_height_m=0.028,
    ) is None
    assert _nearest_raised_instance(
        cap, "blue", [20, 20], min_px=600, max_track_distance_px=80,
    ) is None


def test_canonical_wrist_self_mask_is_narrow_and_includes_observed_housing():
    from hebbot.skills.primitives import _is_canonical_wrist_self_candidate

    assert _is_canonical_wrist_self_candidate(620, 110)
    assert _is_canonical_wrist_self_candidate(320, 40)
    assert not _is_canonical_wrist_self_candidate(510, 270)
    assert not _is_canonical_wrist_self_candidate(120, 350)


def test_acquisition_rejects_edge_clipped_candidate_centroids():
    from hebbot.skills.primitives import _is_frame_edge_candidate

    assert _is_frame_edge_candidate(120, 467)
    assert _is_frame_edge_candidate(8, 240)
    assert not _is_frame_edge_candidate(120, 450)
    assert not _is_frame_edge_candidate(320, 240)


def test_aligned_depth_table_ring_ignores_nearby_colored_object():
    import numpy as np
    from hebbot.skills.primitives import _nearest_raised_instance

    color = np.zeros((140, 140, 3), dtype=np.uint8)
    color[55:85, 55:85] = [0, 0, 200]       # red target
    # A large green neighbor fills much of the target's old local-table ring.
    color[35:105, 88:125] = [35, 95, 65]
    depth = np.full((140, 140), 1000, dtype=np.uint16)
    depth[55:85, 55:85] = 970
    depth[35:105, 88:125] = 965
    cap = {"color": color, "depth": depth, "depth_scale": 0.001}

    found = _nearest_raised_instance(cap, "red", [70, 70], min_px=300)
    assert found is not None
    assert found[3] == pytest.approx(0.03)


def test_held_red_verifier_accepts_dim_clipped_near_gripper_region():
    import numpy as np
    from hebbot.skills.primitives import _held_color_evidence

    color = np.zeros((100, 120, 3), dtype=np.uint8)
    color[82:100, 45:75] = [28, 36, 99]  # BGR for dim held RGB=(99,36,28)
    found = _held_color_evidence(color, "red", min_px=200)
    assert found is not None
    u, v, npix, cue = found
    assert (u, v) == pytest.approx((59.5, 90.5))
    assert npix == 18 * 30
    assert cue == "held_dim_red"


def test_held_red_verifier_keeps_relaxed_cue_out_of_upper_scene():
    import numpy as np
    from hebbot.skills.primitives import _held_color_evidence

    color = np.zeros((100, 120, 3), dtype=np.uint8)
    color[10:35, 45:75] = [28, 36, 99]
    assert _held_color_evidence(color, "red", min_px=200) is None


def test_post_lift_verifier_accepts_vacant_source_when_held_is_clipped():
    import numpy as np
    from hebbot.skills.primitives import _post_lift_visual_evidence

    blank = np.zeros((100, 120, 3), dtype=np.uint8)
    evidence = _post_lift_visual_evidence(blank, "red", [60, 75], min_px=200)
    assert evidence["present"]
    assert evidence["cue"] == "source_vacant"


def test_post_lift_verifier_rejects_target_remaining_at_source():
    import numpy as np
    from hebbot.skills.primitives import _post_lift_visual_evidence

    color = np.zeros((100, 120, 3), dtype=np.uint8)
    color[60:80, 50:70] = [60, 70, 180]
    evidence = _post_lift_visual_evidence(color, "red", [60, 70], min_px=200)
    assert not evidence["present"]
    assert evidence["cue"] == "target_remains_at_source"


def test_aligned_depth_gate_unifies_fragmented_color_on_one_object():
    import numpy as np
    from hebbot.skills.primitives import _nearest_raised_instance

    color = np.zeros((140, 160, 3), dtype=np.uint8)
    # One physical rectangle, but illumination leaves two disconnected red patches.
    color[48:68, 62:82] = [0, 0, 200]
    color[72:92, 88:108] = [0, 0, 200]
    # A flat red artifact is closer to the old tracked pixel.
    color[15:30, 15:30] = [0, 0, 200]
    depth = np.full((140, 160), 1000, dtype=np.uint16)
    depth[45:95, 58:112] = 970
    cap = {"color": color, "depth": depth, "depth_scale": 0.001}

    found = _nearest_raised_instance(
        cap, "red", track=[20, 20], min_px=100, min_height_m=0.008,
    )
    assert found is not None
    u, v, npix, height = found
    assert (u, v) == pytest.approx((84.5, 69.5))
    assert npix == 50 * 54
    assert height == pytest.approx(0.03)


def test_depth_object_threshold_applies_after_small_color_seeds_are_unified():
    import numpy as np
    from hebbot.skills.primitives import _nearest_raised_instance

    color = np.zeros((140, 160, 3), dtype=np.uint8)
    color[45:65, 55:75] = [0, 0, 200]   # 400 px seed, below final 600 px gate
    color[70:90, 85:105] = [0, 0, 200]  # another 400 px seed
    depth = np.full((140, 160), 1000, dtype=np.uint16)
    depth[42:94, 50:110] = 970           # unified physical body: 3120 px
    cap = {"color": color, "depth": depth, "depth_scale": 0.001}

    found = _nearest_raised_instance(cap, "red", [65, 55], min_px=600)
    assert found is not None
    assert found[2] == 52 * 60


def test_depth_object_reacquires_after_transient_dropout():
    import numpy as np
    from hebbot.skills.primitives import _reacquire_raised_instance

    invalid = {
        "color": np.zeros((100, 100, 3), dtype=np.uint8),
        "depth": np.zeros((100, 100), dtype=np.uint16),
        "depth_scale": 0.001,
    }
    color = np.zeros((100, 100, 3), dtype=np.uint8)
    color[35:65, 35:65] = [0, 0, 200]
    depth = np.full((100, 100), 1000, dtype=np.uint16)
    depth[35:65, 35:65] = 970
    valid = {"color": color, "depth": depth, "depth_scale": 0.001}

    class SequenceCamera:
        def __init__(self, frames):
            self.frames = iter(frames)
            self.calls = 0

        def capture(self, with_depth=True):
            self.calls += 1
            return next(self.frames)

    cam = SequenceCamera([invalid, invalid, valid])
    found, rejected = _reacquire_raised_instance(
        cam, "red", [50, 50], min_px=100, max_retries=2,
    )
    assert found is not None
    assert rejected == 2
    assert cam.calls == 3


def test_depth_object_reacquisition_is_bounded():
    import numpy as np
    from hebbot.skills.primitives import _reacquire_raised_instance

    invalid = {
        "color": np.zeros((40, 40, 3), dtype=np.uint8),
        "depth": np.zeros((40, 40), dtype=np.uint16),
        "depth_scale": 0.001,
    }

    class CountingCamera:
        calls = 0

        def capture(self, with_depth=True):
            self.calls += 1
            return invalid

    cam = CountingCamera()
    found, rejected = _reacquire_raised_instance(
        cam, "red", [20, 20], min_px=100, max_retries=2,
    )
    assert found is None
    assert rejected == 3
    assert cam.calls == 3


def test_move_to_joints_mock(substrate):
    out = substrate.call("move_to_joints", params={"q": [0.0]*7, "speed_factor": 0.1})
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_unknown_skill_raises(substrate):
    from hebbot.substrate import SkillNotFoundError
    with pytest.raises(SkillNotFoundError):
        substrate.call("does_not_exist")


# ---------------------------------------------------------------------------
# Composition body type
# ---------------------------------------------------------------------------

def test_home_composition_runs(substrate):
    out = substrate.call("home")
    assert out["success"]
    assert out["completed_steps"] == 2
    sub = out["sub_results"]
    assert sub[0]["skill"] == "open_gripper"
    assert sub[1]["skill"] == "move_to_joints"
    # Each sub-step was a success too
    for s in sub:
        assert s["outcome"]["success"]


def test_home_records_parent_and_child_episodes(substrate):
    substrate.call("home")
    recent = substrate.store.recent_episodes(limit=20)
    skills = [e["skill_name"] for e in recent]
    assert "home" in skills
    assert "open_gripper" in skills
    assert "move_to_joints" in skills

    # The home episode should have no parent, the sub-skill episodes should
    # have home as parent
    home_ep = next(e for e in recent if e["skill_name"] == "home")
    assert home_ep["parent_id"] is None
    children = [e for e in recent if e["parent_id"] == home_ep["id"]]
    assert len(children) == 2


def test_composition_speed_factor_passes_through(substrate):
    out = substrate.call("home", params={"speed_factor": 0.05})
    assert out["success"]
    # The move_to_joints sub-step should have received our overridden speed
    move_step = out["sub_results"][1]
    assert move_step["outcome"]["result"]["speed_factor"] == 0.05


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------

def test_top_level_call_logs_orchestrator_decision(substrate):
    substrate.call("get_state")
    decs = substrate.store.recent_decisions()
    assert len(decs) == 1
    d = decs[0]
    assert d["skill_called"] == "get_state"
    assert d["decision"] == "orchestrator"
    assert d["outcome"] == "success"
    # Linked to the episode it ran in.
    ep = substrate.store.get_episode(d["episode_id"])
    assert ep is not None and ep["skill_name"] == "get_state"


def test_composition_logs_one_decision_for_parent_only(substrate):
    substrate.call("home")
    decs = substrate.store.recent_decisions()
    # One decision for the top-level `home` call; the open_gripper /
    # move_to_joints children are part of its execution, not separately routed.
    assert [d["skill_called"] for d in decs] == ["home"]
    assert decs[0]["decision"] == "orchestrator"


def test_failed_call_logs_decision_with_fail_outcome(substrate):
    with pytest.raises(ValueError):
        substrate.call("move_to_joints", params={"q": [0.0, 0.0]})  # bad q length
    decs = substrate.store.recent_decisions(skill_called="move_to_joints")
    assert len(decs) == 1
    assert decs[0]["outcome"] == "fail"


def test_log_decision_seam_records_routing_choice(substrate, monkeypatch):
    # A body that routes via the injected _log_decision seam. The dispatcher
    # uses the body's choice instead of the default orchestrator baseline, and
    # stamps the outcome after execution.
    from hebbot.skills import primitives

    def _routing_probe(*, _log_decision=None):
        _log_decision(
            "replay",
            reasoning="best candidate 0.91 > threshold 0.85",
            candidates=[{"episode_id": "abc", "relational_similarity": 0.91}],
            threshold_values={"replay": 0.85},
        )
        return {"_source": "test", "ok": True}

    monkeypatch.setattr(primitives, "_routing_probe", _routing_probe, raising=False)
    substrate.register("routing_probe",
                       function_spec="hebbot.skills.primitives:_routing_probe")

    out = substrate.call("routing_probe")
    assert out["success"]

    decs = substrate.store.recent_decisions(skill_called="routing_probe")
    assert len(decs) == 1
    d = decs[0]
    assert d["decision"] == "replay"
    assert d["candidates"][0]["relational_similarity"] == 0.91
    assert d["threshold_values"] == {"replay": 0.85}
    assert d["outcome"] == "success"  # stamped by dispatcher post-execution


# ---------------------------------------------------------------------------
# Expectation model / surprise detection (commitment #1, slice 1)
# ---------------------------------------------------------------------------

def test_evaluate_expectations_pass_and_fail():
    from hebbot.substrate.expectations import evaluate
    exp = {"checks": [
        {"name": "err", "field": "max_error", "op": "le", "value": 0.05},
        {"name": "grasped", "field": "grasped", "op": "eq", "value": True},
        {"name": "in_range", "field": "x", "op": "between", "min": 0.2, "max": 0.6},
    ]}
    # all satisfied -> no events
    assert evaluate(exp, {"max_error": 0.01, "grasped": True, "x": 0.4}) == []
    # one violated -> one event naming the field
    events = evaluate(exp, {"max_error": 0.5, "grasped": True, "x": 0.4})
    assert len(events) == 1 and events[0]["field"] == "max_error"
    # multiple violated -> multiple events
    assert len(evaluate(exp, {"max_error": 0.5, "grasped": False, "x": 0.9})) == 3


def test_evaluate_skips_missing_fields():
    # A check on a field the result lacks (e.g. mock results) is skipped, not
    # treated as a violation.
    from hebbot.substrate.expectations import evaluate
    exp = {"checks": [{"name": "err", "field": "max_error", "op": "le", "value": 0.05}]}
    assert evaluate(exp, {"_source": "mock", "note": "no max_error here"}) == []


def test_move_to_joints_mock_not_surprised(substrate):
    # move_to_joints has an expectation, but the mock result omits max_error,
    # so a mock call is a clean success, not a surprise.
    out = substrate.call("move_to_joints", params={"q": [0.0] * 7})
    assert out["success"] and not out.get("surprise")


def test_primitives_carry_expectations(substrate):
    for name in ("move_to_joints", "move_to_pose", "close_gripper"):
        sk = substrate.store.get_skill(name)
        assert sk["expectations"]["checks"], f"{name} should declare expectations"


def test_expectation_violation_flags_surprise(substrate, monkeypatch):
    from hebbot.skills import primitives

    def _exp_probe(*, val=0.0):
        return {"max_error": val}

    monkeypatch.setattr(primitives, "_exp_probe", _exp_probe, raising=False)
    substrate.register(
        "exp_probe",
        function_spec="hebbot.skills.primitives:_exp_probe",
        expectations={"checks": [
            {"name": "reached", "field": "max_error", "op": "le", "value": 0.01},
        ]},
    )

    # Violating call -> surprise.
    out = substrate.call("exp_probe", params={"val": 0.5})
    assert out["success"] is False
    assert out["surprise"] is True
    assert out["surprise_events"][0]["field"] == "max_error"
    decs = substrate.store.recent_decisions(skill_called="exp_probe")
    assert decs[0]["outcome"] == "surprise"

    # Satisfying call -> clean success.
    out2 = substrate.call("exp_probe", params={"val": 0.005})
    assert out2["success"] is True and not out2.get("surprise")
    decs2 = substrate.store.recent_decisions(skill_called="exp_probe")
    assert decs2[0]["outcome"] == "success"


def test_composition_halts_on_surprise(substrate, monkeypatch):
    from hebbot.skills import primitives

    def _exp_probe(*, val=0.0):
        return {"max_error": val}

    monkeypatch.setattr(primitives, "_exp_probe", _exp_probe, raising=False)
    substrate.register(
        "exp_probe",
        function_spec="hebbot.skills.primitives:_exp_probe",
        expectations={"checks": [
            {"name": "reached", "field": "max_error", "op": "le", "value": 0.01},
        ]},
    )
    # First step surprises -> composition halts before the second step.
    substrate.register_composition("surprise_comp", steps=[
        {"skill": "exp_probe", "args": {"val": 0.5}},
        {"skill": "open_gripper", "args": {"width": 0.08}},
    ])
    out = substrate.call("surprise_comp")
    assert out["success"] is False
    assert out["completed_steps"] == 0
    # The halt carries the surprising sub-outcome forward for diagnosis.
    assert out["sub_results"][0]["outcome"]["surprise"] is True


def test_composition_halts_on_subskill_failure(substrate):
    # Register a deliberately broken composition (bad q length on first step)
    substrate.register_composition(
        "broken",
        steps=[
            {"skill": "move_to_joints", "args": {"q": [0.0, 0.0]}},  # too few joints
            {"skill": "open_gripper", "args": {"width": 0.08}},
        ],
    )
    with pytest.raises(ValueError):
        substrate.call("broken")
    # First sub-step's failure propagates up; second sub-step never runs
    recent = substrate.store.recent_episodes(skill_name="open_gripper", limit=5)
    # We registered fresh substrate, only home's open_gripper exists; broken's didn't run
    # (we'd need to check timing to be precise; this asserts no NEW open_gripper episode
    #  from broken — easiest by confirming exactly one open_gripper was called)
    open_count = sum(1 for e in recent)
    assert open_count == 0  # broken never reached the open_gripper step
