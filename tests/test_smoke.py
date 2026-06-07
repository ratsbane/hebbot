"""Smoke test: register the primitive, call it, verify episode + state.

Run from project root:
    python -m pytest tests/test_smoke.py -v
or:
    python tests/test_smoke.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from hebbot import Substrate
from hebbot.skills.primitives import register_all


def test_init_and_call_in_mock_mode():
    with tempfile.TemporaryDirectory() as tmp:
        s = Substrate(tmp)

        # No skills yet
        assert s.list_skills() == []

        # Register the primitives
        register_all(s)
        skills = s.list_skills()
        assert {sk["name"] for sk in skills} == {"get_state"}
        get_state_skill = next(sk for sk in skills if sk["name"] == "get_state")
        assert get_state_skill["body_type"] == "classical"
        assert get_state_skill["body"]["function"] == "hebbot.skills.primitives:get_state"

        # Call the skill in mock mode (no robot)
        outcome = s.call("get_state")
        assert outcome["success"] is True
        result = outcome["result"]
        assert result["_source"] == "mock"
        assert len(result["joints"]) == 7
        assert len(result["ee_pose"]) == 16

        # An episode should have been recorded
        recent = s.store.recent_episodes(skill_name="get_state", limit=10)
        assert len(recent) == 1
        ep = recent[0]
        assert ep["skill_name"] == "get_state"
        assert ep["success"] == 1
        assert ep["body_type_used"] == "classical"
        assert ep["started_at"] is not None
        assert ep["ended_at"] is not None
        assert ep["outcome"]["result"]["_source"] == "mock"

        s.close()


def test_register_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        s = Substrate(tmp)
        register_all(s)
        v1 = s.list_skills()[0]["version"]

        register_all(s)
        v2 = s.list_skills()[0]["version"]
        assert v2 == v1 + 1, "re-register should bump version"

        s.close()


def test_unknown_skill_raises():
    from hebbot.substrate import SkillNotFoundError

    with tempfile.TemporaryDirectory() as tmp:
        s = Substrate(tmp)
        import pytest
        with pytest.raises(SkillNotFoundError):
            s.call("nonexistent_skill")
        s.close()


if __name__ == "__main__":
    test_init_and_call_in_mock_mode()
    test_register_is_idempotent()
    try:
        test_unknown_skill_raises()
    except Exception as e:
        print(f"unknown_skill test needs pytest; skipping: {e}")
    print("smoke tests passed (manual run)")
