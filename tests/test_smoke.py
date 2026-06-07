"""Smoke tests for the substrate + all primitives (mock mode)."""

from __future__ import annotations

import tempfile

import pytest

from hebbot import Substrate
from hebbot.skills.primitives import register_all


@pytest.fixture
def substrate():
    with tempfile.TemporaryDirectory() as tmp:
        s = Substrate(tmp)
        register_all(s)
        yield s
        s.close()


def test_all_primitives_registered(substrate):
    names = {sk["name"] for sk in substrate.list_skills()}
    expected = {"get_state", "move_to_joints", "move_to_pose",
                "open_gripper", "close_gripper"}
    assert names == expected


def test_get_state_mock(substrate):
    out = substrate.call("get_state")
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_move_to_joints_mock(substrate):
    q = [0.0, -0.5, 0.0, -2.0, 0.0, 1.5, 0.5]
    out = substrate.call("move_to_joints", params={"q": q, "speed_factor": 0.1})
    assert out["success"]
    assert out["result"]["_source"] == "mock"
    assert out["result"]["target_q"] == q


def test_move_to_joints_validates_q_length(substrate):
    with pytest.raises(ValueError, match="q must have 7"):
        substrate.call("move_to_joints", params={"q": [0.0, 0.0]})


def test_move_to_joints_clamps_speed(substrate):
    out = substrate.call("move_to_joints", params={"q": [0.0]*7, "speed_factor": 100.0})
    assert out["success"]
    # 100 > max (0.5), should be clamped to 0.5
    assert out["result"]["speed_factor"] == 0.5


def test_move_to_pose_mock_with_default_orientation(substrate):
    out = substrate.call("move_to_pose", params={"position": [0.4, 0.0, 0.3]})
    assert out["success"]
    assert out["result"]["_source"] == "mock"
    assert out["result"]["target_position"] == [0.4, 0.0, 0.3]
    assert out["result"]["target_orientation"] == [0.0, 1.0, 0.0, 0.0]


def test_open_gripper_mock(substrate):
    out = substrate.call("open_gripper", params={"width": 0.06})
    assert out["success"]
    assert out["result"]["_source"] == "mock"
    assert out["result"]["target_width"] == 0.06


def test_open_gripper_clamps_width(substrate):
    out = substrate.call("open_gripper", params={"width": 0.5})  # > max 0.08
    assert out["success"]
    assert out["result"]["target_width"] == 0.08


def test_close_gripper_mock(substrate):
    out = substrate.call("close_gripper", params={
        "width": 0.03, "force": 15.0
    })
    assert out["success"]
    assert out["result"]["_source"] == "mock"
    assert out["result"]["grasped"] is True
    assert out["result"]["target_width"] == 0.03
    assert out["result"]["force"] == 15.0


def test_episodes_recorded_for_all_skills(substrate):
    # Call each
    substrate.call("get_state")
    substrate.call("move_to_joints", params={"q": [0.0]*7})
    substrate.call("move_to_pose", params={"position": [0.4, 0.0, 0.3]})
    substrate.call("open_gripper")
    substrate.call("close_gripper")

    recent = substrate.store.recent_episodes(limit=20)
    skill_names = [e["skill_name"] for e in recent]
    for name in ["get_state", "move_to_joints", "move_to_pose",
                 "open_gripper", "close_gripper"]:
        assert name in skill_names
    for ep in recent:
        assert ep["success"] == 1
        assert ep["ended_at"] is not None


def test_unknown_skill_raises(substrate):
    from hebbot.substrate import SkillNotFoundError
    with pytest.raises(SkillNotFoundError):
        substrate.call("does_not_exist")


def test_mcp_server_imports():
    """Just confirm the module can be imported and main() exists."""
    from hebbot import mcp_server
    assert callable(mcp_server.main)
