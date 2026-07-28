"""Orthogonal evidence axes for manipulation and no-contact probe outcomes."""

from __future__ import annotations


SERVO_ENVELOPE_M = {"x": [0.30, 0.60], "y": [-0.28, 0.28]}


def probe_outcome_axes(result: dict) -> dict:
    """Normalize probe evidence without converting absence of evidence into failure."""
    history = result.get("history") or []
    note = str(result.get("note") or "")
    safe = result.get("safe_to_descend")
    saturation = (
        result.get("controller_envelope_saturation")
        or result.get("workspace_saturation")
    )
    clipped_any = any(bool(step.get("controller_envelope_clipped",
                                    step.get("workspace_saturated"))) for step in history)
    max_clipped = max((int(step.get("controller_envelope_clipped_count",
                                    step.get("workspace_saturated_count", 0)) or 0)
                       for step in history), default=0)
    limited = bool(
        saturation or max_clipped >= 3 or note.startswith("workspace saturation")
        or note.startswith("controller envelope saturation")
    )
    if limited:
        envelope_limited = True
    elif safe is True:
        envelope_limited = False
    else:
        envelope_limited = None

    geometry_history = result.get("geometry_alignment_history") or []
    final_alignment = result.get("final_pose_alignment")
    reached_geometry_phase = bool(result.get("geom") is not None or geometry_history)
    if safe is True or reached_geometry_phase:
        coarse_servo_converged = True
    elif limited or note.startswith("coarse servo did not converge"):
        coarse_servo_converged = False
    else:
        coarse_servo_converged = None

    if geometry_history:
        depth_alignment_converged = any(
            float(item.get("pixel_error", float("inf")))
            <= float(item.get("pixel_tolerance", -1.0))
            for item in geometry_history
        )
    elif safe is True:
        depth_alignment_converged = True
    else:
        depth_alignment_converged = None
    final_alignment_converged = (
        bool(final_alignment.get("passed")) if final_alignment is not None else None
    )
    if safe is True:
        servo_converged = True
    elif (coarse_servo_converged is False
          or depth_alignment_converged is False
          or final_alignment_converged is False):
        servo_converged = False
    else:
        servo_converged = None

    explicit_geometry = result.get("geometric_evidence") or {}
    geometric_reachable = explicit_geometry.get("ik_reachable")
    planned_path_found = explicit_geometry.get("path_found")
    live_pose_reached = explicit_geometry.get("live_pose_reached")
    if live_pose_reached is None and safe is True:
        live_pose_reached = True

    return {
        "geometric_reachable": geometric_reachable,
        "planned_path_found": planned_path_found,
        "live_pose_reached": live_pose_reached,
        "controller_envelope_clipped": (clipped_any if history else None),
        "controller_envelope_limited": envelope_limited,
        "coarse_visual_servo_converged": coarse_servo_converged,
        "depth_alignment_converged": depth_alignment_converged,
        "final_pose_alignment_converged": final_alignment_converged,
        "visual_servo_converged": servo_converged,
        "final_pose_authorized": (bool(safe) if safe is not None else None),
        "servo_envelope_m": SERVO_ENVELOPE_M,
    }
