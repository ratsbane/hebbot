"""Substrate: the runtime layer that owns the skill store and dispatches calls.

Public API for the most common path:

    from hebbot.substrate import Substrate
    s = Substrate("~/hebbot-data")
    s.register("get_state", function_spec="hebbot.skills.primitives:get_state")
    result = s.call("get_state")
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
        """Register a skill in the store."""
        register(
            self.store,
            name,
            function_spec=function_spec,
            body_type=body_type,
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

    def close(self) -> None:
        self.store.close()


__all__ = [
    "Substrate",
    "SkillNotFoundError",
    "UnsupportedBodyTypeError",
]
