"""Expectation evaluation — the first slice of commitment #1 (surprise detection).

A skill record may carry an `expectations` payload describing forward-prediction
bounds on its result. After a classical body runs *without raising*, the
dispatcher evaluates the result against those bounds. A violated bound is a
*surprise*: the action completed but didn't do what the substrate predicted —
the signal the deliberate layer needs to re-plan, re-parameterize, demote, or
escalate (foundations commitment #1, the bidirectional-feedback feature).

Expectation payload shape (stored in skills.expectations as JSON):

    {
      "checks": [
        {"name": "reached_target", "field": "max_error", "op": "le", "value": 0.05},
        {"name": "object_grasped", "field": "grasped",   "op": "eq", "value": true},
        {"name": "in_range",       "field": "x",          "op": "between",
                                    "min": 0.2, "max": 0.6}
      ]
    }

Each check names a `field` (a dotted path into the body's result dict), an `op`,
and the bound. A check whose field is absent from the result is *skipped* (not a
violation): this keeps mock-mode results, which omit live-only fields like
`max_error`, from spuriously surprising. A check that errors while comparing
(bad op, non-numeric field) yields a flagged event rather than crashing dispatch
— surfacing a malformed expectation instead of hiding it (the retrospective's
"build observability for succeeded-but-bad" lesson).

`evaluate` returns a list of surprise events (empty when all checks pass). The
dispatcher turns a non-empty list into success=False + surprise=True.

Ops: le, lt, ge, gt, eq, ne, abs_le (|field| <= value), between (min..max incl).
"""

from __future__ import annotations

from typing import Any

_MISSING = object()


def _resolve_path(result: dict, path: str) -> Any:
    """Walk a dotted path into a nested dict; return _MISSING if absent."""
    cur: Any = result
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _expected_repr(chk: dict) -> str:
    if chk.get("op") == "between":
        return f"in [{chk.get('min')}, {chk.get('max')}]"
    return f"{chk.get('op')} {chk.get('value')}"


def _passes(op: str, actual: Any, chk: dict) -> bool:
    v = chk.get("value")
    if op == "le":
        return actual <= v
    if op == "lt":
        return actual < v
    if op == "ge":
        return actual >= v
    if op == "gt":
        return actual > v
    if op == "eq":
        return actual == v
    if op == "ne":
        return actual != v
    if op == "abs_le":
        return abs(actual) <= v
    if op == "between":
        return chk.get("min") <= actual <= chk.get("max")
    raise ValueError(f"unknown expectation op {op!r}")


def evaluate(expectations: Any, result: Any) -> list[dict]:
    """Evaluate a skill's expectations against a body result.

    Returns a list of surprise events; empty means every check passed (or there
    was nothing to check).
    """
    if not expectations or not isinstance(result, dict):
        return []
    checks = expectations.get("checks") or []
    events: list[dict] = []
    for chk in checks:
        field = chk.get("field")
        op = chk.get("op")
        name = chk.get("name") or field
        actual = _resolve_path(result, field) if field else _MISSING
        if actual is _MISSING:
            # Field not present (e.g. a mock result lacking a live-only field) —
            # can't evaluate, so don't treat it as a violation.
            continue
        try:
            ok = _passes(op, actual, chk)
        except Exception as e:
            events.append({
                "check": name, "field": field, "op": op, "actual": actual,
                "error": True,
                "message": f"expectation {name!r} could not be evaluated: "
                           f"{type(e).__name__}: {e}",
            })
            continue
        if not ok:
            events.append({
                "check": name, "field": field, "op": op,
                "expected": _expected_repr(chk), "actual": actual,
                "message": f"{name}: expected {field} {_expected_repr(chk)}, got {actual}",
            })
    return events
