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
    assert "read_camera" in names
    assert "teleop_xbox" in names
    assert "home" in names


def test_teleop_xbox_mock(substrate):
    # No panda in context -> mock mode, no hardware/gamepad touched.
    out = substrate.call("teleop_xbox")
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_get_state_mock(substrate):
    out = substrate.call("get_state")
    assert out["success"]
    assert out["result"]["_source"] == "mock"


def test_read_camera_mock(substrate):
    # No camera in context -> mock mode, no file written.
    out = substrate.call("read_camera")
    assert out["success"]
    assert out["result"]["_source"] == "mock"
    assert out["result"]["color_path"] is None


def test_read_camera_writes_frame_row(substrate):
    # A fake camera lets us exercise the save + frames-table path without
    # hardware. Requires PIL for the JPEG write.
    pytest.importorskip("PIL")
    import numpy as np

    class FakeCamera:
        name = "wrist"

        def capture(self, with_depth=True):
            return {
                "color": np.zeros((8, 8, 3), dtype=np.uint8),
                "depth": (np.ones((8, 8), dtype=np.uint16) * 500) if with_depth else None,
                "depth_scale": 0.001,
                "width": 8,
                "height": 8,
            }

    ctx = {"cameras": {"wrist": FakeCamera()}}
    out = substrate.call("read_camera", context=ctx)
    assert out["success"]
    res = out["result"]
    assert res["_source"] == "live"
    assert res["camera"] == "wrist"
    assert res["color_path"] and res["depth_path"]
    assert res["center_depth_m"] == pytest.approx(0.5)  # 500 * 0.001

    # A color frame row + a depth frame row, both tied to a real episode.
    rows = substrate.store.conn.execute(
        "SELECT camera, episode_id, file_path FROM frames ORDER BY camera"
    ).fetchall()
    cams = {r["camera"] for r in rows}
    assert cams == {"wrist", "wrist:depth"}
    for r in rows:
        ep = substrate.store.get_episode(r["episode_id"])
        assert ep is not None and ep["skill_name"] == "read_camera"


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


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------

def test_top_level_call_logs_orchestrator_decision(substrate):
    substrate.call("get_state")
    decs = substrate.store.recent_decisions()
    assert len(decs) == 1
    d = decs[0]
    assert d["skill_called"] == "get_state"
    assert d["decision"] == "orchestrator"
    assert d["outcome"] == "success"
    # Linked to the episode it ran in.
    ep = substrate.store.get_episode(d["episode_id"])
    assert ep is not None and ep["skill_name"] == "get_state"


def test_composition_logs_one_decision_for_parent_only(substrate):
    substrate.call("home")
    decs = substrate.store.recent_decisions()
    # One decision for the top-level `home` call; the open_gripper /
    # move_to_joints children are part of its execution, not separately routed.
    assert [d["skill_called"] for d in decs] == ["home"]
    assert decs[0]["decision"] == "orchestrator"


def test_failed_call_logs_decision_with_fail_outcome(substrate):
    with pytest.raises(ValueError):
        substrate.call("move_to_joints", params={"q": [0.0, 0.0]})  # bad q length
    decs = substrate.store.recent_decisions(skill_called="move_to_joints")
    assert len(decs) == 1
    assert decs[0]["outcome"] == "fail"


def test_log_decision_seam_records_routing_choice(substrate, monkeypatch):
    # A body that routes via the injected _log_decision seam. The dispatcher
    # uses the body's choice instead of the default orchestrator baseline, and
    # stamps the outcome after execution.
    from hebbot.skills import primitives

    def _routing_probe(*, _log_decision=None):
        _log_decision(
            "replay",
            reasoning="best candidate 0.91 > threshold 0.85",
            candidates=[{"episode_id": "abc", "relational_similarity": 0.91}],
            threshold_values={"replay": 0.85},
        )
        return {"_source": "test", "ok": True}

    monkeypatch.setattr(primitives, "_routing_probe", _routing_probe, raising=False)
    substrate.register("routing_probe",
                       function_spec="hebbot.skills.primitives:_routing_probe")

    out = substrate.call("routing_probe")
    assert out["success"]

    decs = substrate.store.recent_decisions(skill_called="routing_probe")
    assert len(decs) == 1
    d = decs[0]
    assert d["decision"] == "replay"
    assert d["candidates"][0]["relational_similarity"] == 0.91
    assert d["threshold_values"] == {"replay": 0.85}
    assert d["outcome"] == "success"  # stamped by dispatcher post-execution


# ---------------------------------------------------------------------------
# Expectation model / surprise detection (commitment #1, slice 1)
# ---------------------------------------------------------------------------

def test_evaluate_expectations_pass_and_fail():
    from hebbot.substrate.expectations import evaluate
    exp = {"checks": [
        {"name": "err", "field": "max_error", "op": "le", "value": 0.05},
        {"name": "grasped", "field": "grasped", "op": "eq", "value": True},
        {"name": "in_range", "field": "x", "op": "between", "min": 0.2, "max": 0.6},
    ]}
    # all satisfied -> no events
    assert evaluate(exp, {"max_error": 0.01, "grasped": True, "x": 0.4}) == []
    # one violated -> one event naming the field
    events = evaluate(exp, {"max_error": 0.5, "grasped": True, "x": 0.4})
    assert len(events) == 1 and events[0]["field"] == "max_error"
    # multiple violated -> multiple events
    assert len(evaluate(exp, {"max_error": 0.5, "grasped": False, "x": 0.9})) == 3


def test_evaluate_skips_missing_fields():
    # A check on a field the result lacks (e.g. mock results) is skipped, not
    # treated as a violation.
    from hebbot.substrate.expectations import evaluate
    exp = {"checks": [{"name": "err", "field": "max_error", "op": "le", "value": 0.05}]}
    assert evaluate(exp, {"_source": "mock", "note": "no max_error here"}) == []


def test_move_to_joints_mock_not_surprised(substrate):
    # move_to_joints has an expectation, but the mock result omits max_error,
    # so a mock call is a clean success, not a surprise.
    out = substrate.call("move_to_joints", params={"q": [0.0] * 7})
    assert out["success"] and not out.get("surprise")


def test_primitives_carry_expectations(substrate):
    for name in ("move_to_joints", "move_to_pose", "close_gripper"):
        sk = substrate.store.get_skill(name)
        assert sk["expectations"]["checks"], f"{name} should declare expectations"


def test_expectation_violation_flags_surprise(substrate, monkeypatch):
    from hebbot.skills import primitives

    def _exp_probe(*, val=0.0):
        return {"max_error": val}

    monkeypatch.setattr(primitives, "_exp_probe", _exp_probe, raising=False)
    substrate.register(
        "exp_probe",
        function_spec="hebbot.skills.primitives:_exp_probe",
        expectations={"checks": [
            {"name": "reached", "field": "max_error", "op": "le", "value": 0.01},
        ]},
    )

    # Violating call -> surprise.
    out = substrate.call("exp_probe", params={"val": 0.5})
    assert out["success"] is False
    assert out["surprise"] is True
    assert out["surprise_events"][0]["field"] == "max_error"
    decs = substrate.store.recent_decisions(skill_called="exp_probe")
    assert decs[0]["outcome"] == "surprise"

    # Satisfying call -> clean success.
    out2 = substrate.call("exp_probe", params={"val": 0.005})
    assert out2["success"] is True and not out2.get("surprise")
    decs2 = substrate.store.recent_decisions(skill_called="exp_probe")
    assert decs2[0]["outcome"] == "success"


def test_composition_halts_on_surprise(substrate, monkeypatch):
    from hebbot.skills import primitives

    def _exp_probe(*, val=0.0):
        return {"max_error": val}

    monkeypatch.setattr(primitives, "_exp_probe", _exp_probe, raising=False)
    substrate.register(
        "exp_probe",
        function_spec="hebbot.skills.primitives:_exp_probe",
        expectations={"checks": [
            {"name": "reached", "field": "max_error", "op": "le", "value": 0.01},
        ]},
    )
    # First step surprises -> composition halts before the second step.
    substrate.register_composition("surprise_comp", steps=[
        {"skill": "exp_probe", "args": {"val": 0.5}},
        {"skill": "open_gripper", "args": {"width": 0.08}},
    ])
    out = substrate.call("surprise_comp")
    assert out["success"] is False
    assert out["completed_steps"] == 0
    # The halt carries the surprising sub-outcome forward for diagnosis.
    assert out["sub_results"][0]["outcome"]["surprise"] is True


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
