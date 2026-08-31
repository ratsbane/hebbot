from __future__ import annotations

from hebbot.learning.benchmark import classify_arm, compare, summarize, wilson_interval


def _row(arm_params, version, success, at=1.0):
    return {"arm": classify_arm(arm_params), "policy_version": version,
            "success": success, "started_at": at, "episode_id": "e"}


def test_shadow_warm_start_is_not_counted_as_a_policy_arm():
    assert classify_arm({"warm_start_mode": "shadow"}) == "substrate"
    assert classify_arm({"warm_start_mode": "apply"}) == "policy"
    assert classify_arm({"experience_mode": "replay"}) == "memory"
    assert classify_arm({"feasibility_mode": "policy"}) == "policy"
    assert classify_arm({"experience_mode": "record"}) == "substrate"
    assert classify_arm(None) == "substrate"


def test_policy_takes_precedence_over_memory_when_both_are_enabled():
    assert classify_arm({"experience_mode": "replay",
                         "feasibility_mode": "policy"}) == "policy"


def test_arms_are_only_compared_within_one_policy_version():
    rows = [_row({}, "aaa", True), _row({}, "aaa", False),
            _row({"experience_mode": "replay"}, "bbb", True)]
    result = compare(summarize(rows))
    assert result["comparisons"] == []
    assert not result["attribution_possible"]
    reasons = {row["policy_version"]: row["reason"] for row in result["unrunnable"]}
    assert "only the baseline arm ran" in reasons["aaa"]
    assert "no substrate arm" in reasons["bbb"]


def test_within_version_delta_is_reported_with_overlap_honesty():
    rows = ([_row({}, "aaa", False) for _ in range(8)]
            + [_row({"experience_mode": "replay"}, "aaa", True) for _ in range(8)])
    result = compare(summarize(rows))
    assert len(result["comparisons"]) == 1
    comparison = result["comparisons"][0]
    assert comparison["arm"] == "memory"
    assert comparison["delta"] == 1.0
    assert comparison["intervals_overlap"] is False
    assert result["attribution_possible"]


def test_small_samples_report_overlapping_intervals():
    rows = [_row({}, "aaa", False), _row({"experience_mode": "replay"}, "aaa", True)]
    comparison = compare(summarize(rows))["comparisons"][0]
    assert comparison["delta"] == 1.0
    assert comparison["intervals_overlap"] is True     # n=1 per arm proves nothing


def test_wilson_interval_widens_for_small_n():
    narrow = wilson_interval(50, 100)
    wide = wilson_interval(1, 2)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])
    assert wilson_interval(0, 0) is None


def test_unstamped_episodes_are_never_compared():
    rows = ([_row({}, None, False) for _ in range(10)]
            + [_row({"experience_mode": "replay"}, None, True) for _ in range(10)])
    result = compare(summarize(rows))
    assert result["comparisons"] == []
    assert not result["attribution_possible"]
    assert "predate policy-version stamping" in result["unrunnable"][0]["reason"]
