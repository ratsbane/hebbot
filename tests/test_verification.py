"""The verifier gate: fresh runs are the only promotion evidence, and it expires.

ASPIRE's admission rule on the Hebbot substrate: a skill is `verified` only when
context-free dispatcher runs meet its own declared expectations, and the evidence
is bound to the exact behaviour that ran — so editing the skill, or moving to
different hardware, silently expires it.
"""

from __future__ import annotations

import pytest

from hebbot.substrate import Substrate

CALLS = {"n": 0}


def well_behaved(**kwargs):
    return {"ok": True}


def flaky(**kwargs):
    CALLS["n"] += 1
    return {"ok": CALLS["n"] % 3 == 0}   # succeeds every third call


def broken(**kwargs):
    raise RuntimeError("the arm is on fire")


OK = {"checks": [{"name": "ok", "field": "ok", "op": "eq", "value": True}]}


@pytest.fixture
def sub(tmp_path):
    s = Substrate(tmp_path)
    yield s
    s.close()


def test_a_skill_that_reproduces_is_verified(sub):
    sub.register("steady", function_spec="test_verification:well_behaved",
                 expectations=OK)
    report = sub.verify("steady")
    assert report["verified"] is True
    assert report["successes"] == 2 and report["attempts_used"] == 2
    status = sub.verification_status("steady")
    assert status["verified"] is True and status["valid_successes"] == 2


def test_a_flaky_skill_does_not_clear_the_bar(sub):
    CALLS["n"] = 0
    sub.register("flaky", function_spec="test_verification:flaky", expectations=OK)
    report = sub.verify("flaky", attempts=3, required=2)
    assert report["verified"] is False
    assert report["successes"] <= 1
    status = sub.verification_status("flaky")
    assert status["verified"] is False
    assert status["failures_recorded"] >= 2


def test_a_crash_is_a_recorded_failure_not_an_exception(sub):
    sub.register("broken", function_spec="test_verification:broken", expectations=OK)
    report = sub.verify("broken", attempts=2, required=1)
    assert report["verified"] is False
    assert "RuntimeError" in report["runs"][0]["note"]
    assert sub.verification_status("broken")["failures_recorded"] == 2


def test_editing_a_composition_expires_its_evidence(sub):
    """The ASPIRE limitation we closed: stale proof never vouches for new behaviour."""
    sub.register("steady", function_spec="test_verification:well_behaved",
                 expectations=OK)
    sub.register_composition("routine", steps=[{"skill": "steady"}])
    assert sub.verify("routine")["verified"] is True
    assert sub.verification_status("routine")["verified"] is True
    # Re-register with a changed body: same name, different behaviour.
    sub.register_composition("routine", steps=[{"skill": "steady"},
                                               {"skill": "steady"}])
    status = sub.verification_status("routine")
    assert status["verified"] is False
    assert status["valid_successes"] == 0 and status["stale_successes"] == 2
    # Fresh verification under the new body re-earns it.
    assert sub.verify("routine")["verified"] is True
    assert sub.verification_status("routine")["verified"] is True


def test_evidence_does_not_cross_hardware_tags(sub):
    sub.register("steady", function_spec="test_verification:well_behaved",
                 expectations=OK)
    assert sub.verify("steady", hardware_tag="bracket-A")["verified"] is True
    assert sub.verification_status("steady", hardware_tag="bracket-A")["verified"] is True
    after_rebuild = sub.verification_status("steady", hardware_tag="bracket-B")
    assert after_rebuild["verified"] is False and after_rebuild["valid_successes"] == 0


def test_a_mock_run_cannot_mint_evidence(sub):
    """touch_stack without hardware returns a mock; its expectations cannot hold,
    so an armless run can never promote the skill."""
    from hebbot.skills.touch_stack import register_all

    register_all(sub)
    report = sub.verify("touch_stack", attempts=1, required=1)
    assert report["verified"] is False
    assert sub.verification_status("touch_stack")["verified"] is False
