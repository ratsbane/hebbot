"""Ablation reporting over stored episodes: did the system learn, or did we write better code?

Lab notebook #147: between 2026-06-27 and 2026-07-30 every measurable gain came from
hand-written primitives, and no measurement existed that could have told the difference.
A success-rate curve alone cannot: it moves when the substrate improves, when a policy
improves, and when the blocks happen to sit somewhere easier.

This module makes the comparison the project has been unable to run, and it needs no new
bookkeeping to do it. An episode already records the parameters it ran with, so its
experimental *arm* is derivable after the fact; the dispatcher now stamps the content hash
of the primitives that executed, so arms can be compared only within one policy version.
Comparing across versions is the exact confound CaP-X warns about, so `compare` refuses to
do it silently and reports the mismatch instead.
"""

from __future__ import annotations

import math
from typing import Any, Optional


# An arm is a claim about which machinery was allowed to act, in increasing order of
# assistance. `substrate` is the honest baseline: hand-written primitives alone.
ARMS = ("substrate", "memory", "policy")

_MEMORY_PARAMS = {
    "experience_mode": ("replay",),
    "feasibility_mode": ("avoid_saturation",),
    "recovery_mode": ("replay",),
}
_POLICY_PARAMS = {
    "feasibility_mode": ("policy",),
    "warm_start_mode": ("apply",),
}


def classify_arm(params: Optional[dict]) -> str:
    """Which experimental arm an episode belongs to, read off the params it ran with.

    `warm_start_mode="shadow"` is deliberately NOT a policy arm: a shadow proposal is
    recorded and never commanded, so the episode is still a substrate (or memory) run and
    counting it as policy would manufacture an effect that did not happen.
    """
    params = params or {}
    policy = any(str(params.get(key)) in values for key, values in _POLICY_PARAMS.items())
    memory = any(str(params.get(key)) in values for key, values in _MEMORY_PARAMS.items())
    if policy:
        return "policy"
    if memory:
        return "memory"
    return "substrate"


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Optional[list[float]]:
    """Wilson score interval — honest about small n, where the normal interval is not."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [max(0.0, center - half), min(1.0, center + half)]


def _success(episode: dict, success_field: str) -> Optional[bool]:
    result = ((episode.get("outcome") or {}).get("result") or {})
    value = result.get(success_field)
    return bool(value) if value is not None else None


def collect(
    store: Any,
    *,
    skill_name: str = "vision_grasp",
    success_field: str = "task_verified_success",
    limit: int = 1000,
) -> list[dict]:
    """One row per episode that carries a usable verdict, tagged with arm and version."""
    rows = []
    for episode in store.recent_episodes(skill_name=skill_name, limit=limit):
        verdict = _success(episode, success_field)
        if verdict is None:
            continue                     # no verdict is not a failure; exclude it
        rows.append({
            "episode_id": episode["id"],
            "started_at": float(episode["started_at"]),
            "arm": classify_arm(episode.get("params")),
            "policy_version": (episode.get("outcome") or {}).get("policy_version"),
            "success": bool(verdict),
        })
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    """Success rate per (policy_version, arm), newest policy version first."""
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        buckets.setdefault((row["policy_version"], row["arm"]), []).append(row)
    summary = []
    for (version, arm), bucket in buckets.items():
        successes = sum(1 for row in bucket if row["success"])
        summary.append({
            "policy_version": version,
            "arm": arm,
            "n": len(bucket),
            "successes": successes,
            "success_rate": successes / len(bucket),
            "wilson_95": wilson_interval(successes, len(bucket)),
            "last_at": max(row["started_at"] for row in bucket),
        })
    summary.sort(key=lambda row: (-row["last_at"], ARMS.index(row["arm"])
                                  if row["arm"] in ARMS else len(ARMS)))
    return summary


def compare(summary: list[dict], *, baseline: str = "substrate") -> dict:
    """Within-version arm deltas, plus an explicit refusal to compare across versions.

    A delta is only evidence of learning if both arms ran the same primitives. Where a
    policy version holds just one arm, that is reported as an unrunnable comparison rather
    than quietly compared against a different version's baseline — the confound that makes
    #147 unfalsifiable.
    """
    by_version: dict[Optional[str], dict[str, dict]] = {}
    for row in summary:
        by_version.setdefault(row["policy_version"], {})[row["arm"]] = row

    comparisons, unrunnable = [], []
    for version, arms in by_version.items():
        if version is None:
            # Episodes recorded before the dispatcher stamped a policy version cannot be
            # attributed to the code that ran them. Comparing arms inside this bucket is
            # the very confound the ablation exists to remove: the "substrate" rows here
            # span two months of primitive edits (lab notebook #147).
            unrunnable.append({
                "policy_version": None,
                "arms_present": sorted(arms),
                "reason": ("episodes predate policy-version stamping; an arm delta here "
                           "would also contain every primitive edit between them"),
            })
            continue
        base = arms.get(baseline)
        others = [arm for arm in arms if arm != baseline]
        if base is None or not others:
            unrunnable.append({
                "policy_version": version,
                "arms_present": sorted(arms),
                "reason": (
                    f"no {baseline} arm at this policy version"
                    if base is None else "only the baseline arm ran at this policy version"
                ),
            })
            continue
        for arm in others:
            row = arms[arm]
            comparisons.append({
                "policy_version": version,
                "arm": arm,
                "baseline": baseline,
                "n_arm": row["n"],
                "n_baseline": base["n"],
                "success_rate_arm": row["success_rate"],
                "success_rate_baseline": base["success_rate"],
                "delta": row["success_rate"] - base["success_rate"],
                "intervals_overlap": _overlap(row["wilson_95"], base["wilson_95"]),
            })
    return {
        "comparisons": comparisons,
        "unrunnable": unrunnable,
        "policy_versions": sorted(str(version) for version in by_version),
        "attribution_possible": bool(comparisons),
        "note": (
            "arm deltas are only evidence of learning within one policy_version; "
            "across versions they also contain every primitive edit"
        ),
    }


def _overlap(a: Optional[list[float]], b: Optional[list[float]]) -> Optional[bool]:
    """Overlapping 95% intervals mean the delta is not yet distinguishable from noise."""
    if not a or not b:
        return None
    return not (a[1] < b[0] or b[1] < a[0])


def report(
    store: Any,
    *,
    skill_name: str = "vision_grasp",
    success_field: str = "task_verified_success",
    limit: int = 1000,
) -> dict:
    rows = collect(store, skill_name=skill_name, success_field=success_field, limit=limit)
    summary = summarize(rows)
    return {
        "skill_name": skill_name,
        "success_field": success_field,
        "n_episodes": len(rows),
        "summary": summary,
        "comparison": compare(summary),
    }
