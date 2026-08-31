"""Batch-distilled warm-start policy for vision_grasp.

The first reflex is intentionally small: learn where the successful pre-close EE xy
landed from the acquisition-frame block observation. It may only propose a bounded
warm start; aligned-depth confirmation and deliberate fallback remain mandatory.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np


FEATURE_NAMES = ("u_norm", "v_norm", "height_norm", "start_x", "start_y")


def _xyz(ee_pose: list[float]) -> list[float]:
    return [float(ee_pose[3]), float(ee_pose[7]), float(ee_pose[11])]


def _features(step0: dict, start_xyz: list[float]) -> np.ndarray:
    return np.array([
        (float(step0["u"]) - 320.0) / 320.0,
        (float(step0["v"]) - 240.0) / 240.0,
        (float(step0.get("depth_height_m") or 0.03) - 0.03) / 0.03,
        float(start_xyz[0]),
        float(start_xyz[1]),
    ], dtype=float)


def feature_vector(step0: dict, start_xyz: list[float]) -> list[float]:
    """Public feature builder so a runtime caller cannot drift from training."""
    return _features(step0, start_xyz).tolist()


def _preclose_from_events(store, episode_id) -> Optional[dict]:
    """The pre-close state, taken from the episode's own record of it.

    An instrumented episode names the state its pre-close capture was taken at, so this is
    a lookup. The legacy path below has to guess — nearest timestamp to a frame identified
    by a substring of its filename — and that guessing is why only a handful of grasps ever
    yielded a training example (lab notebook #146).
    """
    try:
        event = store.event_by_label(episode_id, "before_close")
    except Exception:
        return None
    return (event or {}).get("state")


def _preclose_by_nearest_timestamp(store, episode_id, states) -> Optional[dict]:
    """Legacy reconstruction for episodes recorded before the events table existed."""
    before_t = next(
        (f["t"] for f in store.episode_frames(episode_id)
         if "before_close" in f["file_path"] and f["camera"] == "wrist"),
        None,
    )
    if before_t is None:
        return None
    return min(states, key=lambda s: abs(float(s["t"]) - float(before_t)))


def extract_verified_examples(store, limit: int = 500) -> list[dict]:
    """Extract acquisition observation -> successful pre-close EE xy pairs."""
    examples = []
    for episode in store.recent_episodes(skill_name="vision_grasp", limit=limit):
        outcome = episode.get("outcome") or {}
        result = outcome.get("result") or {}
        if result.get("task_verified_success") is not True:
            continue
        history = result.get("history") or []
        states = store.episode_states(episode["id"])
        if not history or len(states) < 2:
            continue
        preclose = _preclose_from_events(store, episode["id"])
        source = "events"
        if preclose is None:
            preclose = _preclose_by_nearest_timestamp(store, episode["id"], states)
            source = "nearest_timestamp"
        if preclose is None:
            continue
        if not states[0].get("ee_pose") or not preclose.get("ee_pose"):
            continue
        start_xyz = _xyz(states[0]["ee_pose"])
        target_xyz = _xyz(preclose["ee_pose"])
        examples.append({
            "episode_id": episode["id"],
            "started_at": float(episode["started_at"]),
            "x": _features(history[0], start_xyz).tolist(),
            "y": target_xyz[:2],
            "start_xyz": start_xyz,
            "acquire_z": float((episode.get("params") or {}).get("acquire_z", 0.45)),
            "preclose_source": source,
            "initial_observation": {
                key: history[0].get(key)
                for key in ("u", "v", "z", "depth_height_m", "npix")
            },
            "servo_steps": int(result.get("servo_steps") or len(history)),
        })
    examples.sort(key=lambda e: e["started_at"])
    return examples


def _fit(x: np.ndarray, y: np.ndarray, ridge: float) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    reg = np.eye(design.shape[1]) * float(ridge)
    reg[0, 0] = 0.0
    return np.linalg.solve(design.T @ design + reg, design.T @ y)


def _predict(weights: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(x)), x]) @ weights


@dataclass
class TrainingResult:
    artifact: dict
    promoted: bool
    reasons: list[str]


def train_candidate(
    examples: list[dict],
    *,
    min_samples: int = 6,
    min_xy_span_m: float = 0.06,
    max_loo_error_m: float = 0.015,
    min_travel_reduction_m: float = 0.02,
    min_improved_fraction: float = 0.75,
    ridge: float = 1e-3,
) -> TrainingResult:
    """Fit and gate a candidate on leave-one-out error, spatial diversity, and usefulness.

    Accuracy alone is not a reason to promote a warm start: the point of one is to shorten
    the deliberate servo, so a candidate that predicts well but lands no closer than the
    arm already was buys nothing (lab notebook #146). The usefulness gate measures, on
    held-out episodes, how much of the start-to-preclose travel the prediction removes.
    """
    reasons = []
    if len(examples) < 2:
        reasons.append("need at least two examples to fit a candidate")
        return TrainingResult({"kind": "warm_start_ridge_v1", "n": len(examples)}, False, reasons)
    x = np.asarray([e["x"] for e in examples], dtype=float)
    y = np.asarray([e["y"] for e in examples], dtype=float)
    weights = _fit(x, y, ridge)
    loo_errors = []
    travel_reductions = []
    if len(examples) >= 3:
        for held in range(len(examples)):
            keep = np.arange(len(examples)) != held
            pred = _predict(_fit(x[keep], y[keep], ridge), x[[held]])[0]
            loo_errors.append(float(np.linalg.norm(pred - y[held])))
            # What the deliberate servo had to cover unaided, minus what it would still
            # have to cover from the warm start. Positive means the reflex earned its keep.
            start = examples[held].get("start_xyz")
            if start is None:
                continue          # unmeasurable, not evidence of usefulness either way
            baseline = float(np.linalg.norm(
                np.asarray(start[:2], dtype=float) - y[held]
            ))
            travel_reductions.append(baseline - loo_errors[-1])
    xy_span = np.ptp(y, axis=0)
    std = x.std(axis=0)
    safe_std = np.where(std < 1e-6, 1.0, std)
    z = (x - x.mean(axis=0)) / safe_std
    artifact = {
        "kind": "warm_start_ridge_v1",
        "created_at": time.time(),
        "feature_names": list(FEATURE_NAMES),
        "n": len(examples),
        "source_episode_ids": [e["episode_id"] for e in examples],
        "weights": weights.tolist(),
        "feature_mean": x.mean(axis=0).tolist(),
        "feature_std": safe_std.tolist(),
        "max_train_distance": float(np.linalg.norm(z, axis=1).max()),
        "training_xy_bounds": {
            "min": y.min(axis=0).tolist(), "max": y.max(axis=0).tolist(),
        },
        "xy_span_m": xy_span.tolist(),
        "loo_errors_m": loo_errors,
        "loo_rmse_m": (
            float(np.sqrt(np.mean(np.square(loo_errors)))) if loo_errors else None
        ),
        "loo_travel_reduction_m": travel_reductions,
        "median_travel_reduction_m": (
            float(np.median(travel_reductions)) if travel_reductions else None
        ),
        "improved_fraction": (
            float(np.mean([r > 0.0 for r in travel_reductions]))
            if travel_reductions else None
        ),
        "validity": {
            "acquire_z": float(np.median([float(e.get("acquire_z", 0.45))
                                          for e in examples])),
            "tolerances": {"acquire_z": 0.02},
        },
        "mean_deliberate_servo_steps": float(
            np.mean([e["servo_steps"] for e in examples])
        ),
        "ridge": float(ridge),
    }
    if len(examples) < min_samples:
        reasons.append(f"{len(examples)} verified examples < minimum {min_samples}")
    if float(np.linalg.norm(xy_span)) < min_xy_span_m:
        reasons.append(
            f"pre-close xy span {float(np.linalg.norm(xy_span)):.4f}m "
            f"< minimum {min_xy_span_m:.4f}m"
        )
    if artifact["loo_rmse_m"] is None or artifact["loo_rmse_m"] > max_loo_error_m:
        reasons.append(
            f"leave-one-out RMSE {artifact['loo_rmse_m']} exceeds {max_loo_error_m:.4f}m"
        )
    median_reduction = artifact["median_travel_reduction_m"]
    improved_fraction = artifact["improved_fraction"]
    if median_reduction is None or median_reduction < min_travel_reduction_m:
        reasons.append(
            f"median held-out travel reduction {median_reduction} "
            f"below minimum {min_travel_reduction_m:.4f}m — accurate but not useful"
        )
    if improved_fraction is None or improved_fraction < min_improved_fraction:
        reasons.append(
            f"warm start improved only {improved_fraction} of held-out episodes "
            f"< minimum {min_improved_fraction}"
        )
    artifact["promotion_thresholds"] = {
        "min_samples": min_samples,
        "min_xy_span_m": min_xy_span_m,
        "max_loo_error_m": max_loo_error_m,
        "min_travel_reduction_m": min_travel_reduction_m,
        "min_improved_fraction": min_improved_fraction,
    }
    artifact["promoted"] = not reasons
    artifact["rejection_reasons"] = reasons
    return TrainingResult(artifact, not reasons, reasons)


def save_artifact(result: TrainingResult, policies_dir: str | Path) -> Path:
    policies_dir = Path(policies_dir)
    policies_dir.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(result.artifact, sort_keys=True).encode()
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    path = policies_dir / f"vision_grasp_warm_start_{digest}.json"
    path.write_text(json.dumps(result.artifact, indent=2, sort_keys=True) + "\n")
    return path


def propose(artifact: dict, feature_vector: list[float], max_distance: float = 3.0) -> dict:
    """Return a bounded warm-start proposal or an explicit deliberate fallback."""
    x = np.asarray(feature_vector, dtype=float)
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    std = np.asarray(artifact["feature_std"], dtype=float)
    distance = float(np.linalg.norm((x - mean) / std))
    pred = _predict(np.asarray(artifact["weights"], dtype=float), x[None, :])[0]
    workspace_ok = 0.30 <= pred[0] <= 0.60 and -0.28 <= pred[1] <= 0.28
    accepted = bool(artifact.get("promoted") and distance <= max_distance and workspace_ok)
    return {
        "accepted": accepted,
        "decision": "policy" if accepted else "orchestrator",
        "predicted_xy": pred.tolist(),
        "domain_distance": distance,
        "workspace_ok": workspace_ok,
        "reason": (
            "promoted policy within training domain and workspace"
            if accepted else "candidate unavailable, out of domain, or outside workspace"
        ),
    }
