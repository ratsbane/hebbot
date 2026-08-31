from __future__ import annotations

import json

from hebbot.learning import registry


def _write(directory, name, artifact):
    path = directory / name
    path.write_text(json.dumps(artifact))
    return path


def _artifact(**overrides):
    base = {
        "kind": "feasibility_logistic_v1",
        "promoted": True,
        "created_at": 100.0,
        "weights": [0.0, 1.0, 0.0],
        "validity": {"acquire_z": 0.45, "tolerances": {"acquire_z": 0.02}},
    }
    base.update(overrides)
    return base


def test_unpromoted_artifacts_are_never_returned(tmp_path):
    _write(tmp_path, "a.json", _artifact(promoted=False,
                                         rejection_reasons=["too few groups"]))
    load = registry.load_promoted("feasibility_logistic_v1", directory=tmp_path)
    assert not load["available"]
    assert load["reason"] == "no promoted artifact of this kind"
    assert load["rejected"][0]["rejection_reasons"] == ["too few groups"]


def test_newest_promoted_artifact_wins(tmp_path):
    _write(tmp_path, "old.json", _artifact(created_at=10.0))
    _write(tmp_path, "new.json", _artifact(created_at=99.0))
    load = registry.load_promoted("feasibility_logistic_v1", directory=tmp_path)
    assert load["available"]
    assert load["artifact"]["created_at"] == 99.0


def test_artifact_is_refused_outside_its_recorded_validity_envelope(tmp_path):
    _write(tmp_path, "a.json", _artifact())
    ok = registry.load_promoted("feasibility_logistic_v1", directory=tmp_path,
                                preconditions={"acquire_z": 0.455})
    assert ok["available"]
    stale = registry.load_promoted("feasibility_logistic_v1", directory=tmp_path,
                                   preconditions={"acquire_z": 0.34})
    assert not stale["available"]
    assert "outside artifact validity" in stale["rejected"][0]["reason"]


def test_unreadable_artifact_degrades_to_deliberate_rather_than_raising(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    load = registry.load_promoted("feasibility_logistic_v1", directory=tmp_path)
    assert not load["available"]
    assert "unreadable" in load["rejected"][0]["reason"]


def test_missing_policy_directory_is_a_reason_not_an_error(tmp_path):
    load = registry.load_promoted("warm_start_ridge_v1", directory=tmp_path / "absent")
    assert not load["available"]
    assert load["reason"] == "no policy directory"
