"""Skill registry and dispatcher.

The substrate's runtime side of commitment #10: skills are records, not code.
This module is the bridge between the records (in the Store) and the live
Python callables that implement `classical` body-type skills.

For `classical` body-type skills, the body's JSON looks like:
    {"function": "module.path:function_name"}

The dispatcher resolves that to a callable via importlib at call time, and
caches the lookup. For other body types (script, composition, learned_policy,
etc.), the dispatcher delegates to type-specific runners — placeholders for
now, to be implemented as they become relevant.

Skill registration is also exposed as a decorator for convenience:

    from hebbot.substrate import register, Substrate

    @register("get_state", body_type="classical")
    def get_state(panda) -> dict:
        ...

The decorator stashes metadata on the function and writes a skill row.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Optional

from .store import Store


# Module-level registry of classical-body callables.
# Maps fully-qualified "module.path:function_name" → callable.
_CLASSICAL_REGISTRY: dict[str, Callable[..., Any]] = {}


def _resolve_classical(spec: str) -> Callable[..., Any]:
    """Resolve a 'module.path:function_name' string to a callable.

    Caches successful lookups in _CLASSICAL_REGISTRY.
    """
    if spec in _CLASSICAL_REGISTRY:
        return _CLASSICAL_REGISTRY[spec]
    if ":" not in spec:
        raise ValueError(
            f"classical body 'function' must be 'module.path:function_name', got {spec!r}"
        )
    module_path, func_name = spec.rsplit(":", 1)
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    _CLASSICAL_REGISTRY[spec] = func
    return func


class SkillNotFoundError(KeyError):
    pass


class UnsupportedBodyTypeError(NotImplementedError):
    pass


def call_skill(
    store: Store,
    name: str,
    *,
    params: Optional[dict] = None,
    context: Optional[Any] = None,
    deliberate_ctx: Optional[dict] = None,
) -> dict:
    """Invoke a skill by name.

    Looks up the skill record, dispatches based on body_type, records an
    episode (start + end with outcome).

    Returns the outcome dict, which has at least:
        {"success": bool, "result": <whatever the body returned>}
    """
    params = params or {}
    skill = store.get_skill(name)
    if skill is None:
        raise SkillNotFoundError(name)

    body_type = skill["body_type"]
    body = skill["body"] or {}

    episode_id = store.start_episode(
        skill_name=name,
        params=params,
        deliberate_ctx=deliberate_ctx,
        body_type_used=body_type,
    )

    try:
        if body_type == "classical":
            spec = body.get("function")
            if not spec:
                raise ValueError(f"skill {name!r} classical body missing 'function'")
            func = _resolve_classical(spec)
            # The skill author's function signature gets **params unpacked.
            # If they want raw access, they can declare params: dict explicitly.
            result = func(**params, _context=context) if _accepts_context(func) else func(**params)
            outcome = {"success": True, "result": result}
            store.end_episode(episode_id, success=True, outcome=outcome)
            return outcome

        elif body_type in ("script", "composition", "trajectory",
                           "learned_policy", "vlm_grounded"):
            # Placeholders — to be implemented as each becomes relevant.
            raise UnsupportedBodyTypeError(
                f"body type {body_type!r} not yet supported by the dispatcher"
            )

        else:
            raise UnsupportedBodyTypeError(f"unknown body type {body_type!r}")

    except Exception as e:
        outcome = {"success": False, "error": f"{type(e).__name__}: {e}"}
        store.end_episode(episode_id, success=False, outcome=outcome)
        raise


def _accepts_context(func: Callable) -> bool:
    """Return True if `func` declares a `_context` keyword parameter."""
    import inspect
    sig = inspect.signature(func)
    return "_context" in sig.parameters


# ---------------------------------------------------------------------------
# Registration helpers — convenience for writing classical primitives.
# ---------------------------------------------------------------------------


def register(
    store: Store,
    name: str,
    *,
    function_spec: str,
    body_type: str = "classical",
    params_schema: Optional[dict] = None,
    expectations: Optional[dict] = None,
) -> None:
    """Register a skill by writing its record to the store.

    For classical skills, `function_spec` is "module.path:function_name".
    """
    if body_type != "classical":
        # Future body types will accept different `body` payloads.
        raise NotImplementedError(
            f"register() currently only handles classical body type, got {body_type!r}"
        )

    body = {"function": function_spec}
    store.register_skill(
        name=name,
        body_type=body_type,
        body=body,
        params_schema=params_schema,
        expectations=expectations,
    )
