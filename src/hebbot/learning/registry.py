"""Runtime lookup of promoted policy artifacts.

This is the wire between the offline training scripts and live execution. Before it
existed, `train_*.py` wrote gated artifacts into `data_dir/policies/` that nothing read,
so a promoted policy could not have changed behaviour even in principle (lab notebook
#146). The contract here is deliberately narrow:

  * only artifacts with `promoted: true` are ever returned — the promotion gate stays the
    single barrier between the corpus and a reflex;
  * validity preconditions recorded on the artifact (e.g. the acquisition pose it was
    fitted at) are checked at load, so an artifact cannot silently outlive the setup that
    produced it;
  * every load returns its reason, so the caller can record *why* it fell back to the
    deliberate layer rather than reporting a bare absence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


POLICY_SUBDIR = "policies"


def policies_dir(store: Optional[Any] = None) -> Path:
    """The artifact directory beside the episode store, matching scene/metrics siting."""
    db_path = getattr(store, "db_path", None) if store is not None else None
    if db_path:
        return Path(db_path).parent / POLICY_SUBDIR
    return Path("/tmp/hebbot_policies")


def _preconditions_ok(artifact: dict, preconditions: dict) -> tuple[bool, Optional[str]]:
    """Reject an artifact whose recorded validity envelope excludes the current call."""
    validity = artifact.get("validity") or {}
    for key, value in preconditions.items():
        expected = validity.get(key)
        if expected is None:
            continue
        tolerance = (validity.get("tolerances") or {}).get(key)
        if tolerance is None:
            if expected != value:
                return False, f"{key}={value!r} != artifact validity {expected!r}"
        elif abs(float(value) - float(expected)) > float(tolerance):
            return False, (
                f"{key}={float(value):.4f} outside artifact validity "
                f"{float(expected):.4f} +/- {float(tolerance):.4f}"
            )
    return True, None


def load_promoted(
    kind: str,
    *,
    store: Optional[Any] = None,
    directory: Optional[str | Path] = None,
    preconditions: Optional[dict] = None,
) -> dict:
    """Return the newest promoted artifact of `kind`, or an explicit reason it is absent.

    Never raises on a malformed or unreadable artifact: an unusable policy directory must
    degrade to deliberate control, not take the arm down with it.
    """
    directory = Path(directory) if directory is not None else policies_dir(store)
    trace: dict = {
        "kind": kind,
        "directory": str(directory),
        "artifact": None,
        "path": None,
        "available": False,
        "considered": 0,
        "rejected": [],
    }
    if not directory.is_dir():
        trace["reason"] = "no policy directory"
        return trace

    candidates = []
    for path in sorted(directory.glob("*.json")):
        try:
            artifact = json.loads(path.read_text())
        except Exception as exc:                      # unreadable artifact is not fatal
            trace["rejected"].append({"path": path.name, "reason": f"unreadable: {exc}"})
            continue
        if not isinstance(artifact, dict) or artifact.get("kind") != kind:
            continue
        trace["considered"] += 1
        if artifact.get("promoted") is not True:
            trace["rejected"].append({
                "path": path.name,
                "reason": "not promoted",
                "rejection_reasons": artifact.get("rejection_reasons") or [],
            })
            continue
        ok, why = _preconditions_ok(artifact, preconditions or {})
        if not ok:
            trace["rejected"].append({"path": path.name, "reason": why})
            continue
        candidates.append((float(artifact.get("created_at") or 0.0), path, artifact))

    if not candidates:
        trace["reason"] = (
            "no promoted artifact of this kind"
            if trace["considered"] else "no artifact of this kind"
        )
        return trace

    _, path, artifact = max(candidates, key=lambda item: item[0])
    trace.update({
        "artifact": artifact,
        "path": str(path),
        "available": True,
        "reason": "promoted artifact loaded",
    })
    return trace


def summarize(trace: dict) -> dict:
    """The part of a load trace worth persisting in an episode outcome."""
    return {
        "kind": trace.get("kind"),
        "available": bool(trace.get("available")),
        "path": Path(trace["path"]).name if trace.get("path") else None,
        "reason": trace.get("reason"),
        "rejected": trace.get("rejected") or [],
    }
