"""Skill registry, dispatcher, and body-type runners.

Body types implemented here:
  - `classical`:    importlib-resolved Python function
  - `composition`:  ordered list of sub-skill calls

Body types still stubbed (raise UnsupportedBodyTypeError):
  - `script`, `trajectory`, `learned_policy`, `vlm_grounded`
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Optional

from .store import Store


# Module-level registry of classical-body callables.
_CLASSICAL_REGISTRY: dict[str, Callable[..., Any]] = {}


def _resolve_classical(spec: str) -> Callable[..., Any]:
    """Resolve a 'module.path:function_name' string to a callable."""
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


# ---------------------------------------------------------------------------
# Body-type runners
# ---------------------------------------------------------------------------

def _run_classical(
    store: Store,
    skill: dict,
    params: dict,
    context: Any,
    episode_id: str,
) -> dict:
    body = skill["body"] or {}
    spec = body.get("function")
    if not spec:
        raise ValueError(f"skill {skill['name']!r} classical body missing 'function'")
    func = _resolve_classical(spec)
    extras = _injected_kwargs(func, context=context, store=store, episode_id=episode_id)
    result = func(**params, **extras)
    return {"success": True, "result": result}


def _run_composition(
    store: Store,
    skill: dict,
    params: dict,
    context: Any,
    episode_id: str,
) -> dict:
    """Execute a composition body: an ordered list of sub-skill calls.

    Body format:
        {
            "steps": [
                {"skill": "move_to_joints", "args": {"q": [...]}, "arg_map": {}},
                {"skill": "open_gripper", "args": {"width": 0.08}},
                ...
            ]
        }

    `arg_map` (optional) lets a step pull values from the parent's params:
        {"skill": "pick_at", "args": {"speed_factor": 0.1}, "arg_map": {"x": "x", "y": "y"}}
    pulls parent params["x"] → step args["x"], etc.

    On any sub-step failure, the composition halts and returns
    {success: False, error: "...", completed_steps: N}.
    """
    body = skill["body"] or {}
    steps = body.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError(f"composition body 'steps' must be a list, got {type(steps).__name__}")

    sub_results: list[dict] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "skill" not in step:
            raise ValueError(f"step {i} missing required 'skill' key: {step!r}")
        sub_name = step["skill"]
        sub_args = dict(step.get("args") or {})

        # Apply arg_map: pull values from parent params into sub args
        for sub_key, parent_key in (step.get("arg_map") or {}).items():
            if parent_key in params:
                sub_args[sub_key] = params[parent_key]

        # Recursively call sub-skill, with this episode_id as parent
        sub_outcome = call_skill(
            store, sub_name,
            params=sub_args,
            context=context,
            parent_id=episode_id,
        )
        sub_results.append({
            "step": i,
            "skill": sub_name,
            "outcome": sub_outcome,
        })
        if not sub_outcome.get("success"):
            return {
                "success": False,
                "error": f"composition halted at step {i} ({sub_name}): {sub_outcome.get('error') or sub_outcome.get('result')}",
                "completed_steps": i,
                "sub_results": sub_results,
            }

    return {
        "success": True,
        "completed_steps": len(steps),
        "sub_results": sub_results,
    }


_BODY_RUNNERS: dict[str, Callable[..., dict]] = {
    "classical": _run_classical,
    "composition": _run_composition,
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def call_skill(
    store: Store,
    name: str,
    *,
    params: Optional[dict] = None,
    context: Optional[Any] = None,
    deliberate_ctx: Optional[dict] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Invoke a skill by name. Returns the outcome dict."""
    params = params or {}
    skill = store.get_skill(name)
    if skill is None:
        raise SkillNotFoundError(name)

    body_type = skill["body_type"]
    episode_id = store.start_episode(
        skill_name=name,
        parent_id=parent_id,
        params=params,
        deliberate_ctx=deliberate_ctx,
        body_type_used=body_type,
    )

    try:
        runner = _BODY_RUNNERS.get(body_type)
        if runner is None:
            raise UnsupportedBodyTypeError(
                f"body type {body_type!r} not yet supported by the dispatcher"
            )
        outcome = runner(store, skill, params, context, episode_id)
        store.end_episode(episode_id, success=bool(outcome.get("success")), outcome=outcome)
        return outcome

    except Exception as e:
        outcome = {"success": False, "error": f"{type(e).__name__}: {e}"}
        store.end_episode(episode_id, success=False, outcome=outcome)
        raise


def _injected_kwargs(
    func: Callable,
    *,
    context: Any,
    store: Store,
    episode_id: str,
) -> dict:
    """Substrate-supplied kwargs a primitive may opt into by declaring them.

    A primitive declares any of `_context` (hardware handles), `_store` (the
    SQLite store), or `_episode_id` (the current episode) in its signature and
    receives them; primitives that declare none are called with just their
    params. This is how perception/logging primitives reach the store and the
    episode they belong to — e.g. `read_camera` writes `frames` rows for the
    episode it ran in — without threading those through every call.
    """
    import inspect
    declared = inspect.signature(func).parameters
    available = {"_context": context, "_store": store, "_episode_id": episode_id}
    return {k: v for k, v in available.items() if k in declared}


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------


def register(
    store: Store,
    name: str,
    *,
    function_spec: Optional[str] = None,
    body_type: str = "classical",
    body: Optional[dict] = None,
    params_schema: Optional[dict] = None,
    expectations: Optional[dict] = None,
) -> None:
    """Register a skill by writing its record to the store.

    For `classical` body type: pass `function_spec` ("module.path:function_name").
    For other body types: pass the full `body` dict (the type-specific payload).
    """
    if body_type == "classical":
        if not function_spec:
            raise ValueError("classical skills need function_spec='module:function'")
        body = {"function": function_spec}
    else:
        if body is None:
            raise ValueError(f"body type {body_type!r} requires explicit body dict")

    store.register_skill(
        name=name,
        body_type=body_type,
        body=body,
        params_schema=params_schema,
        expectations=expectations,
    )
