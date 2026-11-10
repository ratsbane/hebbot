"""Scene memory: beliefs with error bars, epochs that age them, and touch_stack writing it.

The integration tests run the same simulated bench as test_touch_stack, with a real SQLite
file in tmp_path standing in for the data directory, so what is asserted is the actual
read-modify-write path a hardware run would take.
"""

from __future__ import annotations

import math

import pytest

from hebbot.memory import scene as sm
from hebbot.skills import touch_stack as ts
from test_touch_stack import World, bench, camera_bench, two_blocks  # noqa: F401

# ---------------------------------------------------------------------------
# The store on its own
# ---------------------------------------------------------------------------


@pytest.fixture
def memory(tmp_path):
    m = sm.SceneMemory(tmp_path / "scene.sqlite")
    yield m
    m.close()


class Store:
    """What touch_stack needs of a substrate store: a db_path to put files beside."""

    def __init__(self, directory):
        self.db_path = str(directory / "hebbot.db")


def test_belief_takes_the_latest_word_on_each_aspect(memory):
    memory.observe("green", sensor="camera+touch", xy=[0.45, -0.1], precision_m=0.006, t=100.0)
    memory.observe("green", sensor="touch", top_z=0.0457, precision_m=0.002, t=101.0)
    b = memory.belief("green", now=161.0)
    assert b["xy"] == [0.45, -0.1]
    assert b["sensor"] == "camera+touch"   # provenance follows the position, not the top
    assert b["top_z"] == pytest.approx(0.0457)
    assert b["age_s"] == pytest.approx(61.0)
    assert b["epochs_behind"] == 0


def test_acting_ages_beliefs_and_observing_does_not(memory):
    memory.observe("green", sensor="touch", xy=[0.45, -0.1], precision_m=0.003, t=100.0)
    memory.observe("blue", sensor="touch", xy=[0.50, 0.1], precision_m=0.003, t=101.0)
    assert memory.epoch() == 0
    memory.act("grasp", obj="blue", xy=[0.50, 0.1], t=102.0)
    assert memory.epoch() == 1
    assert memory.belief("green")["epochs_behind"] == 1


def test_only_actions_on_or_near_an_object_are_disturbances(memory):
    memory.observe("green", sensor="touch", xy=[0.45, -0.1], precision_m=0.003, t=100.0)
    memory.act("probe", xy=[0.46, -0.11], t=101.0)            # 14 mm away: near
    memory.act("grasp", obj="green", t=102.0)                 # named, no position
    memory.act("place", obj="blue", xy=[0.45, 0.2], t=103.0)  # 30 cm away: not
    b = memory.belief("green")
    assert [d["kind"] for d in b["disturbances"]] == ["probe", "grasp"]
    assert b["epochs_behind"] == 3


def test_notes_merge_and_none_deletes(memory):
    memory.note("blue", resting_on="green", body_long_m=0.055)
    memory.note("blue", resting_on=None)
    assert memory.properties("blue") == {"body_long_m": 0.055}


def test_memory_survives_reopening(tmp_path):
    path = tmp_path / "scene.sqlite"
    m = sm.SceneMemory(path)
    m.observe("blue", sensor="touch", xy=[0.5, 0.1], precision_m=0.003)
    m.act("grasp", obj="blue")
    m.close()
    again = sm.SceneMemory(path)
    assert again.belief("blue")["xy"] == [0.5, 0.1]
    assert again.epoch() == 1
    again.close()


def test_vagueness_is_an_error_bar_not_a_shrug(memory):
    """"Over there to the right" is a coordinate with an honest 3 cm error bar."""
    memory.observe("red", sensor="inference", xy=[0.5, 0.15], precision_m=0.030, t=100.0)
    assert "±30 mm" in memory.summary(now=160.0)


def test_summary_reads_as_one_line_per_object(memory):
    memory.observe("green", sensor="touch", xy=[0.45, -0.1], top_z=0.0457,
                   precision_m=0.003, t=100.0)
    memory.note("green", movable=False)
    memory.note("blue", resting_on="green")
    text = memory.summary(now=160.0)
    assert "green: at (0.450, -0.100)" in text and "movable=False" in text
    assert "blue: position unknown; resting_on=green" in text


def test_unknown_object_has_no_belief(memory):
    assert memory.belief("red") is None


def test_no_store_means_no_memory():
    assert sm.load_scene_memory(None) is None
    assert ts.touch_stack(_context=None, _store=None)["_source"] == "mock"


# ---------------------------------------------------------------------------
# touch_stack writing and reading it, on the simulated bench
# ---------------------------------------------------------------------------


def test_touch_stack_writes_what_it_learned(camera_bench, tmp_path):
    world = World(two_blocks())
    ctx, _ = camera_bench(world)
    result = ts.touch_stack(_context=ctx, _store=Store(tmp_path))
    assert result["stage"] == "stacked"
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    blue = memory.belief("blue")
    assert blue["properties"]["resting_on"] == "green"
    assert "in_gripper" not in blue["properties"]
    base_xy = world.blocks[0]["xy"]
    assert math.hypot(blue["xy"][0] - base_xy[0], blue["xy"][1] - base_xy[1]) < 0.01
    assert blue["top_z"] == pytest.approx(0.0177 + 2 * 0.027, abs=0.004)
    assert memory.belief("green")["xy"] is not None
    assert memory.epoch() == 2   # one grasp, one place
    memory.close()


def test_a_second_run_looks_where_it_remembers(camera_bench, tmp_path):
    world = World(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    first = ts.touch_stack(_context=ctx, _store=store, measure_only=True)
    assert first["stage"] == "measured"
    assert first["base"]["hinted"] is False
    second = ts.touch_stack(_context=ctx, _store=store, measure_only=True)
    assert second["stage"] == "measured"
    assert second["base"]["hinted"] is True
    assert second["pick"]["hinted"] is True


def test_a_stale_memory_costs_one_look_and_cannot_shift_the_answer(camera_bench, tmp_path,
                                                                   monkeypatch):
    world = World(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    planted = sm.SceneMemory(tmp_path / "scene.sqlite")
    planted.observe("green", sensor="camera+touch", xy=[0.36, 0.20], precision_m=0.006)
    planted.close()

    real = ts._fresh_blobs
    looks = []

    def nothing_at_the_remembered_spot(color, camera, c, close):
        blobs, ee = real(color, camera, c, close)
        looks.append((color, close, round(ee[0], 3), round(ee[1], 3)))
        if color == "green" and not close and len(looks) == 1:
            return [], ee
        return blobs, ee

    monkeypatch.setattr(ts, "_fresh_blobs", nothing_at_the_remembered_spot)
    result = ts.touch_stack(_context=ctx, _store=store, measure_only=True)
    assert result["stage"] == "measured"
    assert result["base"]["hinted"] is False
    # The first look went to the remembered (wrong) viewpoint, then the survey took over.
    assert looks[0][2:] == (round(0.36 + 0.06, 3), round(0.20 - 0.01, 3))
    assert abs(result["base"]["xy"][0] - world.blocks[0]["xy"][0]) < 0.005


def test_a_block_still_held_is_remembered_as_held(camera_bench, tmp_path):
    world = World(two_blocks())
    world.floor_contact = False
    ctx, _ = camera_bench(world)
    result = ts.touch_stack(_context=ctx, _store=Store(tmp_path))
    assert result["stage"] == "place_no_contact" and result["holding"] is True
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    assert memory.belief("blue")["properties"]["in_gripper"] is True
    memory.close()


def test_scene_recall_renders_the_scene_for_the_orchestrator(camera_bench, tmp_path):
    world = World(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    assert ts.touch_stack(_context=ctx, _store=store)["stage"] == "stacked"
    recalled = sm.scene_recall(_store=store)
    assert "blue" in recalled["summary"] and "resting_on=green" in recalled["summary"]
    one = sm.scene_recall(object="blue", _store=store)
    assert one["belief"]["properties"]["resting_on"] == "green"
    assert sm.scene_recall(object="red", _store=store)["belief"] is None


def test_scene_note_records_what_only_the_user_knows(tmp_path):
    store = Store(tmp_path)
    noted = sm.scene_note(object="green", properties={"movable": False}, _store=store)
    assert noted["properties"]["movable"] is False
    assert "movable=False" in sm.scene_recall(_store=store)["summary"]
