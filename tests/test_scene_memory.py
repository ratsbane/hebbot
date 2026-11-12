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
from test_touch_stack import TABLE, World, bench, camera_bench, two_blocks  # noqa: F401

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


# ---------------------------------------------------------------------------
# touch_unstack: the self-reset routine, acting from memory alone
# ---------------------------------------------------------------------------


class RoundTripWorld(World):
    """A world whose grasp knows height and whose release works anywhere.

    The base World closes on the first block near the jaws' xy — blind to z, which is
    fine when blocks never share a footprint — and its release only lands on the other
    block. Unstacking grasps from a pile, where only the closing height tells the two
    blocks apart, and releases onto bare table.
    """

    def grasp_at(self, *, grasp_z, width, open_width, descent_speed, _context):
        x, y = self.pose[:2]
        self.grasps.append({"xy": (x, y), "open_width": open_width,
                            "orientation": self.orientation})
        for b in self.blocks:
            bottom = TABLE + b.get("base_z", 0.0)
            if math.hypot(b["xy"][0] - x, b["xy"][1] - y) < 0.02 \
                    and bottom <= grasp_z <= bottom + b["height"]:
                self.held = b
                self.pose[2] = grasp_z
                self.gripper.width = b["width"]
                return {"grasped": True, "actual_z": grasp_z, "actual_width": b["width"],
                        "grip_high_mm": 0.0}
        return {"grasped": False, "actual_z": grasp_z, "note": "closed on air"}

    def release(self):
        self.open_events.append(tuple(self.pose))
        if self.held is None:
            return
        near = next((b for b in self.blocks if b is not self.held
                     and math.hypot(self.pose[0] - b["xy"][0],
                                    self.pose[1] - b["xy"][1]) < 0.02), None)
        base_z = near.get("base_z", 0.0) + near["height"] if near is not None else 0.0
        self.held.update(xy=tuple(self.pose[:2]), base_z=base_z)
        self.released_at = tuple(self.pose)
        self.held = None


def stacked_world(bench, store):
    world = RoundTripWorld(two_blocks())
    ctx = bench(world)
    result = ts.touch_stack(base_xy=[0.51, -0.01], pick_xy=[0.44, -0.15],
                            _context=ctx, _store=store)
    assert result["stage"] == "stacked"
    return world, ctx


def test_unstack_round_trips_the_block_to_a_chosen_spot(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    result = ts.touch_unstack(dest_xy=[0.38, 0.15], _context=ctx, _store=store)
    assert result["stage"] == "reset" and result["task_verified_success"] is True
    # The sim hand has no finger-line offset, so the landed position is the command:
    # the target minus the correction at whatever yaw the reset sampled. The reported
    # yaw is rounded to 0.1 deg, which moves the recomputed correction a few microns.
    cx, cy = ts.grasp_axis_correction(result["dest_yaw_deg"])
    assert world.blocks[1]["xy"] == pytest.approx((0.38 - cx, 0.15 - cy), abs=1e-5)
    assert world.blocks[1]["base_z"] == 0.0
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    blue = memory.belief("blue")
    assert blue["properties"]["resting_on"] == "table"
    assert "in_gripper" not in blue["properties"]
    assert blue["xy"] == pytest.approx([0.38, 0.15], abs=0.001)
    assert memory.epoch() == 4   # stack's grasp+place, then unstack's
    memory.close()


def test_unstack_then_stack_uses_the_reset_position(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    assert ts.touch_unstack(dest_xy=[0.38, 0.15], _context=ctx,
                            _store=store)["task_verified_success"] is True
    again = ts.touch_stack(base_xy=[0.51, -0.01], pick_xy=[0.38, 0.15],
                           _context=ctx, _store=store)
    assert again["stage"] == "stacked" and again["task_verified_success"] is True


def test_unstack_samples_its_own_clear_destination(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    result = ts.touch_unstack(_context=ctx, _store=store)
    assert result["stage"] == "reset" and result["task_verified_success"] is True
    dx, dy = result["dest_xy"]
    green = world.blocks[0]["xy"]
    assert math.hypot(dx - green[0], dy - green[1]) >= ts.RESET_CLEARANCE_M
    assert ts.RESET_X[0] <= dx <= ts.RESET_X[1] and ts.RESET_Y[0] <= dy <= ts.RESET_Y[1]


def test_unstack_turns_the_block_to_a_commanded_yaw(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    result = ts.touch_unstack(dest_xy=[0.38, 0.15], dest_yaw_deg=60.0,
                              _context=ctx, _store=store)
    assert result["stage"] == "reset" and result["dest_yaw_deg"] == 60.0
    # The command is offset by the finger-line correction at the DESTINATION yaw, not
    # the pile's, and the sim hand has no physical offset, so the block lands exactly
    # at the offset command.
    cx, cy = ts.grasp_axis_correction(60.0)
    assert world.blocks[1]["xy"] == pytest.approx((0.38 - cx, 0.15 - cy), abs=1e-6)
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    assert memory.belief("blue")["yaw_deg"] == pytest.approx(60.0)
    memory.close()


def test_unstack_samples_a_yaw_when_none_is_given(bench, tmp_path):
    """The reset is where yaw diversity enters a campaign: left to itself it must
    place at an angle of its own choosing, not hand back the pile's forever."""
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    result = ts.touch_unstack(_context=ctx, _store=store)
    assert result["stage"] == "reset"
    assert -90.0 <= result["dest_yaw_deg"] < 90.0
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    assert memory.belief("blue")["yaw_deg"] == pytest.approx(result["dest_yaw_deg"],
                                                            abs=0.05)
    memory.close()


def test_unstack_normalises_the_commanded_yaw_as_an_axis(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    result = ts.touch_unstack(dest_xy=[0.38, 0.15], dest_yaw_deg=120.0,
                              _context=ctx, _store=store)
    assert result["stage"] == "reset" and result["dest_yaw_deg"] == -60.0


def test_unstack_refuses_a_destination_near_a_believed_object(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    result = ts.touch_unstack(dest_xy=[0.50, -0.02], _context=ctx, _store=store)
    assert result["stage"] == "dest_blocked" and len(world.grasps) == 1


def test_unstack_never_places_on_a_surprise(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    # Something nobody told the memory about is sitting at the destination.
    world.blocks.append({"xy": (0.38, 0.15), "angle": 0, "length": 0.055,
                         "width": 0.028, "height": 0.027})
    result = ts.touch_unstack(dest_xy=[0.38, 0.15], _context=ctx, _store=store)
    assert result["stage"] == "no_destination" and len(world.grasps) == 1


def test_unstack_refuses_when_the_pile_is_not_as_remembered(bench, tmp_path):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    # The user took blue off the pile; the memory does not know.
    world.blocks[1].update(xy=(0.40, 0.20), base_z=0.0)
    result = ts.touch_unstack(_context=ctx, _store=store)
    assert result["stage"] == "pile_changed" and len(world.grasps) == 1


def test_unstack_refuses_without_a_remembered_pile(bench, tmp_path):
    world = RoundTripWorld(two_blocks())
    ctx = bench(world)
    result = ts.touch_unstack(_context=ctx, _store=Store(tmp_path))
    assert result["stage"] == "no_belief" and world.grasps == []


# ---------------------------------------------------------------------------
# Retraction and investigation: a failed grasp admits ignorance, a look repairs it
# ---------------------------------------------------------------------------


def test_a_failed_unstack_grasp_retracts_the_belief(bench, tmp_path, monkeypatch):
    store = Store(tmp_path)
    world, ctx = stacked_world(bench, store)
    monkeypatch.setattr(ts, "grasp_at",
                        lambda **kw: {"grasped": False, "note": "stalled on the top edge"})
    result = ts.touch_unstack(_context=ctx, _store=store)
    assert result["stage"] == "grasp"
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    blue = memory.belief("blue")
    assert blue["precision_m"] >= ts.FAILED_GRASP_RETRACT_M
    assert "resting_on" not in blue["properties"]
    memory.close()


def test_a_failed_stack_grasp_retracts_the_belief(camera_bench, tmp_path, monkeypatch):
    world = World(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    monkeypatch.setattr(ts, "grasp_at",
                        lambda **kw: {"grasped": False, "note": "closed on air"})
    result = ts.touch_stack(_context=ctx, _store=store)
    assert result["stage"] == "grasp"
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    blue = memory.belief("blue")
    assert blue["precision_m"] >= ts.FAILED_GRASP_RETRACT_M
    assert "resting_on" not in blue["properties"]
    memory.close()


def test_look_rights_a_memory_the_world_has_left_behind(camera_bench, tmp_path):
    """f1 (2026-11-11): blue left the pile; memory still said it was on green."""
    world = RoundTripWorld(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    assert ts.touch_stack(_context=ctx, _store=store)["stage"] == "stacked"
    world.blocks[1].update(xy=(0.45, -0.20), base_z=0.0, angle=80)
    result = ts.touch_look(_context=ctx, _store=store)
    assert result["stage"] == "looked" and result["task_verified_success"] is True
    assert result["found"]["blue"]["resting_on"] == "table"
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    blue, green = memory.belief("blue"), memory.belief("green")
    assert blue["properties"]["resting_on"] == "table"
    assert blue["xy"] == pytest.approx([0.45, -0.20], abs=0.01)
    assert blue["top_z"] == pytest.approx(TABLE + 0.027, abs=0.004)
    assert green["properties"]["resting_on"] == "table"
    memory.close()


def test_look_accounts_for_a_base_hidden_under_the_pile(camera_bench, tmp_path):
    """Only the top block of an aligned pile is visible; the base is vouched for, not seen."""
    world = RoundTripWorld(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    assert ts.touch_stack(_context=ctx, _store=store)["stage"] == "stacked"
    result = ts.touch_look(_context=ctx, _store=store)
    assert result["task_verified_success"] is True
    assert result["found"]["blue"]["resting_on"] == "green"
    assert result["found"]["green"]["found"] is False
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    assert memory.belief("blue")["properties"]["resting_on"] == "green"
    memory.close()


def test_a_block_the_camera_cannot_find_is_not_invented(camera_bench, tmp_path, monkeypatch):
    world = RoundTripWorld(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    assert ts.touch_stack(_context=ctx, _store=store)["stage"] == "stacked"
    monkeypatch.setattr(ts, "_fresh_blobs",
                        lambda color, camera, c, close: ([], [0.42, -0.05, 0.31]))
    result = ts.touch_look(_context=ctx, _store=store)
    assert result["task_verified_success"] is False
    assert result["stage"] == "look_incomplete"
    memory = sm.SceneMemory(tmp_path / "scene.sqlite")
    assert memory.belief("blue")["properties"]["resting_on"] == "green"   # untouched
    memory.close()


def test_a_shove_then_a_look_then_the_campaign_goes_on(camera_bench, tmp_path, monkeypatch):
    """The whole f1 recovery, end to end: retraction, investigation, and back to work."""
    world = RoundTripWorld(two_blocks())
    ctx, _ = camera_bench(world)
    store = Store(tmp_path)
    assert ts.touch_stack(_context=ctx, _store=store)["stage"] == "stacked"

    def shove(**kwargs):
        world.blocks[1].update(xy=(0.44, -0.19), base_z=0.0, angle=80)
        return {"grasped": False, "note": "air-close after persistent high contact"}

    monkeypatch.setattr(ts, "grasp_at", shove)
    failed = ts.touch_unstack(_context=ctx, _store=store)
    assert failed["stage"] == "grasp"
    monkeypatch.setattr(ts, "grasp_at", world.grasp_at)
    look = ts.touch_look(_context=ctx, _store=store)
    assert look["task_verified_success"] is True
    assert look["found"]["blue"]["resting_on"] == "table"
    again = ts.touch_stack(_context=ctx, _store=store)
    assert again["stage"] == "stacked" and again["task_verified_success"] is True
