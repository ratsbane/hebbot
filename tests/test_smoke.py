"""Smoke tests for the substrate, primitives, and composition body type."""

from __future__ import annotations

import tempfile

import pytest

from hebbot import Substrate
from hebbot.skills.primitives import register_all as register_primitives
from hebbot.skills.composed import register_all as register_composed


@pytest.fixture
def substrate():
    with tempfile.TemporaryDirectory() as tmp:
        s = Substrate(tmp)
        register_primitives(s)
        register_composed(s)
        yield s
        s.close()


# ---------------------------------------------------------------------------
# Primitive registration + dispatch
# ---------------------------------------------------------------------------

def test_all_skills_registered(substrate):
    names = {sk["name"] for sk in substrate.list_skills()}
    assert "get_state" in names
    assert "move_to_joints" in names
    assert "move_to_pose" in names
    assert "open_gripper" in names
    assert "close_gripper" in names
    assert "home" in names


def test_get_state_mock(substrate):
    out = substrate.call("get_state")
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_move_to_joints_mock(substrate):
    out = substrate.call("move_to_joints", params={"q": [0.0]*7, "speed_factor": 0.1})
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_unknown_skill_raises(substrate):
    from hebbot.substrate import SkillNotFoundError
    with pytest.raises(SkillNotFoundError):
        substrate.call("does_not_exist")


# ---------------------------------------------------------------------------
# Composition body type
# ---------------------------------------------------------------------------

def test_home_composition_runs(substrate):
    out = substrate.call("home")
    assert out["success"]
    assert out["completed_steps"] == 2
    sub = out["sub_results"]
    assert sub[0]["skill"] == "open_gripper"
    assert sub[1]["skill"] == "move_to_joints"
    # Each sub-step was a success too
    for s in sub:
        assert s["outcome"]["success"]


def test_home_records_parent_and_child_episodes(substrate):
    substrate.call("home")
    recent = substrate.store.recent_episodes(limit=20)
    skills = [e["skill_name"] for e in recent]
    assert "home" in skills
    assert "open_gripper" in skills
    assert "move_to_joints" in skills

    # The home episode should have no parent, the sub-skill episodes should
    # have home as parent
    home_ep = next(e for e in recent if e["skill_name"] == "home")
    assert home_ep["parent_id"] is None
    children = [e for e in recent if e["parent_id"] == home_ep["id"]]
    assert len(children) == 2


def test_composition_speed_factor_passes_through(substrate):
    out = substrate.call("home", params={"speed_factor": 0.05})
    assert out["success"]
    # The move_to_joints sub-step should have received our overridden speed
    move_step = out["sub_results"][1]
    assert move_step["outcome"]["result"]["speed_factor"] == 0.05


def test_composition_halts_on_subskill_failure(substrate):
    # Register a deliberately broken composition (bad q length on first step)
    substrate.register_composition(
        "broken",
        steps=[
            {"skill": "move_to_joints", "args": {"q": [0.0, 0.0]}},  # too few joints
            {"skill": "open_gripper", "args": {"width": 0.08}},
        ],
    )
    with pytest.raises(ValueError):
        substrate.call("broken")
    # First sub-step's failure propagates up; second sub-step never runs
    recent = substrate.store.recent_episodes(skill_name="open_gripper", limit=5)
    # We registered fresh substrate, only home's open_gripper exists; broken's didn't run
    # (we'd need to check timing to be precise; this asserts no NEW open_gripper episode
    #  from broken — easiest by confirming exactly one open_gripper was called)
    open_count = sum(1 for e in recent)
    assert open_count == 0  # broken never reached the open_gripper step
