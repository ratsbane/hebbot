"""Verifier-gate promotion: a skill is `verified` only by fresh, context-free runs.

ASPIRE's admission rule (NVlabs, read 2026-11-12), ported: a verifier re-solves the
task from the skill record alone — registered defaults, no discovery-session context
— and only verified skills are promoted. Two things are added that ASPIRE's paper
lists as its own limitation: evidence binds to the exact behaviour that ran (the
classical body's source hash, already stamped on every episode as `policy_version`,
or a content hash of a composition body), and to a declared hardware tag. Editing
the skill or changing the hardware therefore expires the evidence silently; stale
proof never vouches for new behaviour.

What "fresh context" means here: for a classical skill the code *is* the skill, so
independence reduces to an unattended dispatcher call with only task-identity
params, judged by the skill's own declared expectations. The agent-level version
(a different orchestrator re-deriving the call) becomes meaningful when the first
LLM-authored composition comes up for promotion.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .skill import SkillNotFoundError, _policy_version, _resolve_classical, call_skill
from .store import Store


def skill_fingerprint(store: Store, name: str) -> Optional[str]:
    """The exact behaviour verification evidence binds to.

    Classical bodies hash the source file the function lives in (the same
    `policy_version` every episode is stamped with); composition bodies hash their
    canonical body dict, so editing a step expires evidence just as surely as
    editing code.
    """
    skill = store.get_skill(name)
    if skill is None:
        raise SkillNotFoundError(name)
    if skill["body_type"] == "classical":
        return _policy_version(_resolve_classical(skill["body"]["function"]))
    payload = json.dumps(skill["body"], sort_keys=True).encode()
    return hashlib.sha1(payload).hexdigest()[:10]


def verify_skill(
    store: Store,
    name: str,
    *,
    context: Any = None,
    params: Optional[dict] = None,
    attempts: int = 3,
    required: int = 2,
    hardware_tag: str = "default",
) -> dict:
    """Run the verifier: up to `attempts` fresh calls, `required` must succeed.

    Success is the dispatcher's own judgment — the skill's declared expectations
    held (`outcome["success"]`), which for the touch skills means
    `task_verified_success` was probed true, not asserted. Every run is recorded
    as evidence whether it passed or not; the gate reads only rows whose
    fingerprint still matches the skill.
    """
    params = dict(params or {})
    fingerprint = skill_fingerprint(store, name)
    runs: list[dict] = []
    successes = 0
    for attempt in range(1, int(attempts) + 1):
        try:
            outcome = call_skill(
                store, name, params=dict(params), context=context,
                deliberate_ctx={"verification": True, "hardware_tag": hardware_tag,
                                "attempt": attempt},
            )
            success = bool(outcome.get("success"))
            note = None if success else "expectations not met"
            episode_id = None  # episode rows carry their own ids; outcome has none
            version = outcome.get("policy_version") or fingerprint
        except Exception as exc:  # a crash is a failed attempt, recorded as such
            success, note, episode_id, version = False, f"{type(exc).__name__}: {exc}", None, fingerprint
        store.record_verification(
            name, episode_id=episode_id, policy_version=version,
            hardware_tag=hardware_tag, params=params, success=success, note=note,
        )
        runs.append({"attempt": attempt, "success": success, "note": note})
        successes += int(success)
        if successes >= required:
            break
        if successes + (attempts - attempt) < required:
            break  # the remaining attempts cannot reach the bar
    return {
        "skill": name,
        "verified": successes >= required,
        "successes": successes,
        "attempts_used": len(runs),
        "required": int(required),
        "fingerprint": fingerprint,
        "hardware_tag": hardware_tag,
        "runs": runs,
    }


def verification_status(
    store: Store,
    name: str,
    *,
    hardware_tag: str = "default",
    required: int = 2,
) -> dict:
    """Is this skill verified *right now*, for this code and this hardware?

    Evidence rows whose fingerprint no longer matches the skill are reported as
    stale, never counted. There is no explicit demotion call: promotion state is
    always derived, so it cannot be forgotten out of date.
    """
    fingerprint = skill_fingerprint(store, name)
    rows = store.verifications(name, hardware_tag=hardware_tag)
    valid = [r for r in rows if r["success"] and r["policy_version"] == fingerprint]
    stale = [r for r in rows if r["success"] and r["policy_version"] != fingerprint]
    return {
        "skill": name,
        "verified": len(valid) >= int(required),
        "valid_successes": len(valid),
        "stale_successes": len(stale),
        "failures_recorded": sum(1 for r in rows if not r["success"]),
        "fingerprint": fingerprint,
        "hardware_tag": hardware_tag,
        "required": int(required),
    }
