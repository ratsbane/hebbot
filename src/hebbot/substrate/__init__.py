"""Substrate: the runtime layer that owns the skill store and dispatches calls.

Public API:

    from hebbot.substrate import Substrate
    s = Substrate("~/hebbot-data")
    s.register("get_state", function_spec="hebbot.skills.primitives:get_state")
    s.register_composition("home", steps=[
        {"skill": "open_gripper", "args": {"width": 0.08}},
        {"skill": "move_to_joints", "args": {"q": HOME_Q, "speed_factor": 0.1}},
    ])
    result = s.call("home")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .skill import (
    SkillNotFoundError,
    UnsupportedBodyTypeError,
    call_skill,
    register,
)
from .store import Store
from .verification import verification_status, verify_skill


class Substrate:
    """Top-level substrate object — store + dispatcher in one handle."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "hebbot.db")

    def register(
        self,
        name: str,
        *,
        function_spec: str,
        body_type: str = "classical",
        params_schema: Optional[dict] = None,
        expectations: Optional[dict] = None,
    ) -> None:
        """Register a classical-body skill."""
        register(
            self.store,
            name,
            function_spec=function_spec,
            body_type=body_type,
            params_schema=params_schema,
            expectations=expectations,
        )

    def register_composition(
        self,
        name: str,
        *,
        steps: list[dict],
        params_schema: Optional[dict] = None,
        expectations: Optional[dict] = None,
    ) -> None:
        """Register a composition-body skill.

        `steps` is an ordered list of:
            {"skill": "sub_skill_name",
             "args": {"key": value, ...},     # static args
             "arg_map": {"sub_arg": "parent_arg"}}  # optional: map parent params
        """
        register(
            self.store,
            name,
            body_type="composition",
            body={"steps": steps},
            params_schema=params_schema,
            expectations=expectations,
        )

    def call(
        self,
        name: str,
        *,
        params: Optional[dict] = None,
        context: Optional[Any] = None,
        deliberate_ctx: Optional[dict] = None,
    ) -> dict:
        """Invoke a registered skill by name. Returns the outcome dict."""
        return call_skill(
            self.store,
            name,
            params=params,
            context=context,
            deliberate_ctx=deliberate_ctx,
        )

    def list_skills(self) -> list[dict]:
        return self.store.list_skills()

    def verify(self, name: str, **kwargs: Any) -> dict:
        """Run the verifier gate on a skill (see substrate/verification.py)."""
        return verify_skill(self.store, name, **kwargs)

    def verification_status(self, name: str, **kwargs: Any) -> dict:
        return verification_status(self.store, name, **kwargs)

    def close(self) -> None:
        self.store.close()


__all__ = [
    "Substrate",
    "SkillNotFoundError",
    "UnsupportedBodyTypeError",
]
