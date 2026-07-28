from hebbot.learning.outcomes import probe_outcome_axes


def test_authorized_probe_populates_only_supported_positive_evidence():
    axes = probe_outcome_axes({"safe_to_descend": True, "history": [{}]})
    assert axes["live_pose_reached"] is True
    assert axes["controller_envelope_limited"] is False
    assert axes["coarse_visual_servo_converged"] is True
    assert axes["depth_alignment_converged"] is True
    assert axes["visual_servo_converged"] is True
    assert axes["final_pose_authorized"] is True
    assert axes["geometric_reachable"] is None
    assert axes["planned_path_found"] is None


def test_saturation_is_controller_evidence_not_geometric_failure():
    axes = probe_outcome_axes({
        "safe_to_descend": False,
        "note": "workspace saturation: legacy episode",
        "history": [{"workspace_saturated": True, "workspace_saturated_count": 3}],
    })
    assert axes["controller_envelope_clipped"] is True
    assert axes["controller_envelope_limited"] is True
    assert axes["coarse_visual_servo_converged"] is False
    assert axes["visual_servo_converged"] is False
    assert axes["geometric_reachable"] is None
    assert axes["planned_path_found"] is None
    assert axes["live_pose_reached"] is None


def test_ambiguous_failure_stays_unknown_on_unmeasured_axes():
    axes = probe_outcome_axes({
        "safe_to_descend": False,
        "note": "target lost or rejected by aligned-depth gate",
        "history": [{"event": "depth_reacquire_failed"}],
    })
    assert axes["controller_envelope_limited"] is None
    assert axes["visual_servo_converged"] is None
    assert axes["live_pose_reached"] is None


def test_depth_alignment_failure_preserves_coarse_servo_success():
    axes = probe_outcome_axes({
        "safe_to_descend": False,
        "note": "depth top-face alignment did not converge before descent",
        "history": [{"z": 0.2, "pixel_err": 3.5}],
        "geom": {"height_m": 0.03},
        "geometry_alignment_history": [
            {"pixel_error": 4.8, "pixel_tolerance": 4.0},
        ],
    })
    assert axes["coarse_visual_servo_converged"] is True
    assert axes["depth_alignment_converged"] is False
    assert axes["final_pose_alignment_converged"] is None
    assert axes["visual_servo_converged"] is False
