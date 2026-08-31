"""Tiny controller-envelope classifier distilled from no-contact probe episodes."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hebbot.learning.outcomes import probe_outcome_axes


FEATURE_NAMES = ("u_norm", "v_norm")


def _label(result: dict) -> tuple[int | None, str]:
    axes = result.get("outcome_axes") or probe_outcome_axes(result)
    if axes["controller_envelope_limited"] is True:
        return 1, "controller_envelope_limited"
    if axes["final_pose_authorized"] is True:
        return 0, "authorized"
    # Two consecutive clips are retained as a precursor label for the original
    # experiment, but are explicitly about the controller envelope, not geometry.
    history = result.get("history") or []
    if max((int(h.get("workspace_saturated_count") or 0) for h in history), default=0) >= 2:
        return 1, "controller_envelope_precursor"
    return None, "excluded_ambiguous_or_other_failure"


def _spatial_groups(examples: list[dict], radius_px: float = 35.0) -> list[int]:
    """Greedy first-match clustering, made independent of episode ordering.

    Group assignment decides the held-out split and therefore the promotion decision, so
    it must not depend on the order episodes came back from the store (lab notebook #146).
    Clustering runs over a canonical pixel-sorted order and the resulting labels are
    renumbered by that same order, so the same examples always produce the same groups.
    """
    order = sorted(
        range(len(examples)),
        key=lambda i: (examples[i]["target_pixel"][0], examples[i]["target_pixel"][1],
                       str(examples[i].get("episode_id") or "")),
    )
    centers: list[list[float]] = []
    groups = [0] * len(examples)
    for index in order:
        pixel = examples[index]["target_pixel"]
        match = next((i for i, center in enumerate(centers)
                      if math.dist(pixel, center) <= radius_px), None)
        if match is None:
            match = len(centers)
            centers.append(list(pixel))
        groups[index] = match
    return groups


def extract_examples(store, limit: int = 500, acquire_z: float = 0.45) -> list[dict]:
    """Extract defensible labels for the current visual-servo execution envelope."""
    examples = []
    for episode in reversed(store.recent_episodes(
        skill_name="vision_grasp_probe", limit=limit,
    )):
        params = episode.get("params") or {}
        if abs(float(params.get("acquire_z", 0.34)) - float(acquire_z)) > 0.02:
            continue
        result = ((episode.get("outcome") or {}).get("result") or {})
        target = (result.get("instance_selection") or {}).get("target")
        if not target or len(target) != 2:
            continue
        y, label_source = _label(result)
        if y is None:
            continue
        examples.append({
            "episode_id": episode["id"],
            "started_at": float(episode["started_at"]),
            "target_pixel": [float(target[0]), float(target[1])],
            "x": [(float(target[0]) - 320.0) / 320.0,
                  (float(target[1]) - 240.0) / 240.0],
            "y": int(y),
            "color": params.get("color"),
            "instance": params.get("instance"),
            "label_source": label_source,
            "acquire_z": float(params.get("acquire_z", acquire_z)),
        })
    groups = _spatial_groups(examples)
    for example, group in zip(examples, groups):
        example["spatial_group"] = group
    return examples


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def _fit_logistic(x: np.ndarray, y: np.ndarray, ridge: float = 1.0) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    weights = np.zeros(design.shape[1], dtype=float)
    penalty = np.eye(design.shape[1]) * float(ridge)
    penalty[0, 0] = 0.0
    for _ in range(50):
        p = _sigmoid(design @ weights)
        grad = design.T @ (p - y) + penalty @ weights
        curvature = np.clip(p * (1.0 - p), 1e-5, None)
        hessian = design.T @ (design * curvature[:, None]) + penalty
        step = np.linalg.solve(hessian, grad)
        weights -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return weights


def _predict(weights: np.ndarray, x: np.ndarray) -> np.ndarray:
    return _sigmoid(np.column_stack([np.ones(len(x)), x]) @ weights)


@dataclass
class TrainingResult:
    artifact: dict
    promoted: bool
    reasons: list[str]


def train_candidate(
    examples: list[dict], *, min_samples: int = 8,
    min_positive_groups: int = 2, min_negative_groups: int = 2,
    max_group_loo_brier: float = 0.25, ridge: float = 1.0,
) -> TrainingResult:
    reasons = []
    artifact = {"kind": "controller_envelope_logistic_v1", "n": len(examples)}
    if len(examples) < 2 or len({e["y"] for e in examples}) < 2:
        reasons.append("need at least two classes and two examples")
        artifact.update({"promoted": False, "rejection_reasons": reasons})
        return TrainingResult(artifact, False, reasons)
    x = np.asarray([e["x"] for e in examples], dtype=float)
    y = np.asarray([e["y"] for e in examples], dtype=float)
    groups = np.asarray([e["spatial_group"] for e in examples], dtype=int)
    weights = _fit_logistic(x, y, ridge)
    positive_groups = len(set(groups[y == 1]))
    negative_groups = len(set(groups[y == 0]))
    held_predictions = []
    for group in sorted(set(groups)):
        train = groups != group
        held = groups == group
        if len(set(y[train])) < 2:
            continue
        pred = _predict(_fit_logistic(x[train], y[train], ridge), x[held])
        held_predictions.extend(zip(np.where(held)[0].tolist(), pred.tolist()))
    held_predictions.sort()
    loo_y = np.asarray([y[i] for i, _ in held_predictions], dtype=float)
    loo_p = np.asarray([p for _, p in held_predictions], dtype=float)
    brier = float(np.mean((loo_p - loo_y) ** 2)) if len(loo_p) else None
    artifact.update({
        "created_at": time.time(), "feature_names": list(FEATURE_NAMES),
        "source_episode_ids": [e["episode_id"] for e in examples],
        "weights": weights.tolist(), "ridge": float(ridge),
        "class_counts": {"authorized": int((y == 0).sum()),
                         "controller_envelope_limited": int((y == 1).sum())},
        "spatial_group_counts": {"authorized": negative_groups,
                                 "controller_envelope_limited": positive_groups},
        # The features are image pixels, so the artifact is only meaningful at the
        # acquisition pose it was fitted at. Recording it lets the runtime registry refuse
        # an artifact that has outlived its observe pose instead of silently misreading a
        # different view (lab notebook #146).
        "validity": {
            "acquire_z": float(np.median([float(e.get("acquire_z", 0.0))
                                          for e in examples])),
            "tolerances": {"acquire_z": 0.02},
        },
        "group_loo_n": len(loo_p), "group_loo_brier": brier,
        "group_loo_predictions": [
            {"episode_id": examples[i]["episode_id"], "y": int(y[i]), "p": p}
            for i, p in held_predictions
        ],
    })
    if len(examples) < min_samples:
        reasons.append(f"{len(examples)} examples < minimum {min_samples}")
    if positive_groups < min_positive_groups:
        reasons.append(
            f"{positive_groups} controller-limited spatial groups < minimum {min_positive_groups}"
        )
    if negative_groups < min_negative_groups:
        reasons.append(f"{negative_groups} authorized spatial groups < minimum {min_negative_groups}")
    if brier is None or brier > max_group_loo_brier:
        reasons.append(f"group-LOO Brier {brier} exceeds {max_group_loo_brier}")
    artifact["promotion_thresholds"] = {
        "min_samples": min_samples, "min_positive_groups": min_positive_groups,
        "min_negative_groups": min_negative_groups,
        "max_group_loo_brier": max_group_loo_brier,
    }
    artifact["promoted"] = not reasons
    artifact["rejection_reasons"] = reasons
    return TrainingResult(artifact, not reasons, reasons)


def save_artifact(result: TrainingResult, policies_dir: str | Path) -> Path:
    policies_dir = Path(policies_dir)
    policies_dir.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(result.artifact, sort_keys=True).encode()
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    path = policies_dir / f"controller_envelope_{digest}.json"
    path.write_text(json.dumps(result.artifact, indent=2, sort_keys=True) + "\n")
    return path


def predict(artifact: dict, target_pixel: list[float]) -> float:
    x = np.asarray([[(float(target_pixel[0]) - 320.0) / 320.0,
                     (float(target_pixel[1]) - 240.0) / 240.0]])
    return float(_predict(np.asarray(artifact["weights"], dtype=float), x)[0])
