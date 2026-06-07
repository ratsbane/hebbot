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
# Decision logging
# ---------------------------------------------------------------------------

# Valid routing decisions, per the schema's CHECK constraint on decisions.decision.
VALID_DECISIONS = ("replay", "orchestrator", "policy")


class _Decision:
    """Records the substrate's routing decision for one skill invocation.

    A skill body may call the injected `_log_decision(...)` (this object's
    `record`) to declare how it routed the call — `replay` a past trajectory,
    defer to the `orchestrator`, or run a learned `policy` — along with the
    candidates / relational-state / reasoning behind the choice. The dispatcher
    commits the row after execution, stamping the real outcome (so a body
    decides *before* running and the outcome is filled in *after*).

    If no body records a decision, the dispatcher logs a default `orchestrator`
    decision for top-level calls: the orchestrator chose to invoke this skill
    and the substrate executed it as registered, without rerouting. Sub-skill
    calls inside a composition are not baseline-logged — they're part of the
    parent's execution, not independent routing decisions. A composition child
    that *does* route itself (future replay logic) still logs via `record`.
    """

    def __init__(self, store: Store, *, skill_called: str, episode_id: str, params: dict):
        self._store = store
        self._skill_called = skill_called
        self._episode_id = episode_id
        self._params = params
        self._chosen: Optional[dict] = None

    def record(
        self,
        decision: str,
        *,
        reasoning: Optional[str] = None,
        relational_state: Optional[dict] = None,
        candidates: Optional[list] = None,
        threshold_values: Optional[dict] = None,
    ) -> None:
        """Declare the routing decision for this call (body-facing API).

        `decision` must be one of VALID_DECISIONS. The outcome is stamped by the
        dispatcher after execution, so callers don't pass it here.
        """
        if decision not in VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {VALID_DECISIONS}, got {decision!r}"
            )
        self._chosen = {
            "decision": decision,
            "reasoning": reasoning,
            "relational_state": relational_state,
            "candidates": candidates,
            "threshold_values": threshold_values,
        }

    def commit(self, *, default_decision: str, outcome: str, top_level: bool) -> None:
        """Write the decision row. Called once by the dispatcher post-execution."""
        if self._chosen is not None:
            self._store.log_decision(
                skill_called=self._skill_called,
                episode_id=self._episode_id,
                params=self._params,
                outcome=outcome,
                **self._chosen,
            )
        elif top_level:
            self._store.log_decision(
                skill_called=self._skill_called,
                episode_id=self._episode_id,
                params=self._params,
                decision=default_decision,
                outcome=outcome,
                reasoning="orchestrator-driven call; substrate executed as registered (no rerouting)",
            )


# ---------------------------------------------------------------------------
# Body-type runners
# ---------------------------------------------------------------------------

def _run_classical(
    store: Store,
    skill: dict,
    params: dict,
    context: Any,
    episode_id: str,
    decision: "_Decision",
) -> dict:
    body = skill["body"] or {}
    spec = body.get("function")
    if not spec:
        raise ValueError(f"skill {skill['name']!r} classical body missing 'function'")
    func = _resolve_classical(spec)
    extras = _injected_kwargs(
        func, context=context, store=store, episode_id=episode_id, decision=decision,
    )
    result = func(**params, **extras)
    return {"success": True, "result": result}


def _run_composition(
    store: Store,
    skill: dict,
    params: dict,
    context: Any,
    episode_id: str,
    decision: "_Decision",
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
    decision = _Decision(store, skill_called=name, episode_id=episode_id, params=params)
    top_level = parent_id is None

    try:
        runner = _BODY_RUNNERS.get(body_type)
        if runner is None:
            raise UnsupportedBodyTypeError(
                f"body type {body_type!r} not yet supported by the dispatcher"
            )
        outcome = runner(store, skill, params, context, episode_id, decision)
        success = bool(outcome.get("success"))
        store.end_episode(episode_id, success=success, outcome=outcome)
        decision.commit(
            default_decision="orchestrator",
            outcome="success" if success else "fail",
            top_level=top_level,
        )
        return outcome

    except Exception as e:
        outcome = {"success": False, "error": f"{type(e).__name__}: {e}"}
        store.end_episode(episode_id, success=False, outcome=outcome)
        decision.commit(default_decision="orchestrator", outcome="fail", top_level=top_level)
        raise


def _injected_kwargs(
    func: Callable,
    *,
    context: Any,
    store: Store,
    episode_id: str,
    decision: "_Decision",
) -> dict:
    """Substrate-supplied kwargs a primitive may opt into by declaring them.

    A primitive declares any of these in its signature and receives them;
    primitives that declare none are called with just their params:

      - `_context`       hardware handles (panda, gripper, cameras)
      - `_store`         the SQLite store
      - `_episode_id`    the current episode id
      - `_log_decision`  records the substrate's routing decision for this call
                         (see `_Decision.record`) — candidates, relational
                         state, reasoning. The dispatcher stamps the outcome
                         after execution.

    This is the single seam through which perception/logging/routing primitives
    reach the substrate — `read_camera` writes `frames` rows, a future replay
    body calls `_log_decision("replay", ...)` — without threading store/episode
    through every call.
    """
    import inspect
    declared = inspect.signature(func).parameters
    available = {
        "_context": context,
        "_store": store,
        "_episode_id": episode_id,
        "_log_decision": decision.record if decision is not None else None,
    }
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
