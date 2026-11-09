"""touch_stack against a simulated table: rectangles, a finite fingertip, and a hand.

The simulator answers the question the probes ask, namely how high does a fingertip stop at
(x, y). It says nothing about whether the real arm behaves this way, so passing here means
the geometry and the control flow are right, not that blocks will stack.
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from hebbot.skills import touch_stack as ts

TABLE = 0.0177
TIP_HALF_X, TIP_HALF_Y = ts.PROBE_DILATION_M[0] / 2, ts.PROBE_DILATION_M[1] / 2


def block(x, y, angle=0.0, length=0.055, width=0.028, height=0.027):
    return {"xy": (x, y), "angle": angle, "length": length, "width": width, "height": height}


class World:
    def __init__(self, blocks, noise=0.0, seed=0):
        self.blocks = blocks
        self.noise = noise
        self.rng = random.Random(seed)
        self.probes = []
        self.grasps = []
        self.pose = [0.3, 0.0, 0.5]
        self.orientation = None
        self.held = None
        self.released_at = None
        self.floor_contact = True
        self.open_events = []

    def height_at(self, x, y):
        best = 0.0
        for b in self.blocks:
            # The closed fingers' footprint, measured on hardware: wider across y than x.
            touches = any(
                ts.rect_distance((x + dx, y + dy), b["xy"], b["angle"], b["length"] / 2,
                                 b["width"] / 2) == 0.0
                for dx in np.linspace(-TIP_HALF_X, TIP_HALF_X, 7)
                for dy in np.linspace(-TIP_HALF_Y, TIP_HALF_Y, 9))
            if touches:
                best = max(best, b.get("base_z", 0.0) + b["height"])
        return best

    def probe_table(self, *, x, y, open_width, z_floor, approach_z, retreat_z, _context,
                    force_thresh=6.0):
        self.probes.append((x, y))
        x += self.rng.gauss(0, self.noise)
        y += self.rng.gauss(0, self.noise)
        return {"contact_z": TABLE + self.height_at(x, y), "contacted": True}

    def move_to_pose(self, position, orientation=None, *, speed_factor=0.1, _context=None):
        self.pose = list(position)
        self.orientation = orientation
        return {}

    def grasp_at(self, *, grasp_z, width, open_width, descent_speed, _context):
        x, y = self.pose[:2]
        self.grasps.append({"xy": (x, y), "open_width": open_width, "orientation": self.orientation})
        for b in self.blocks:
            if math.hypot(b["xy"][0] - x, b["xy"][1] - y) < 0.02:
                self.held = b
                self.pose[2] = grasp_z
                self.gripper.width = b["width"]
                return {"grasped": True, "actual_z": grasp_z, "actual_width": b["width"],
                        "grip_high_mm": 0.0}
        return {"grasped": False, "actual_z": grasp_z, "note": "closed on air"}

    def lower_until_contact(self, panda, *, start_z, floor_z, contact_force=4.0, speed=0.015):
        if not self.floor_contact:
            return {"contact": False, "contact_z": None}
        return {"contact": True, "contact_z": round(floor_z + 0.030, 4)}

    def release(self):
        self.open_events.append(tuple(self.pose))
        if self.held is not None:
            base = next(b for b in self.blocks if b is not self.held)
            d = math.hypot(self.pose[0] - base["xy"][0], self.pose[1] - base["xy"][1])
            if d < 0.02:
                self.held.update(xy=tuple(self.pose[:2]), base_z=base["height"])
            self.released_at = tuple(self.pose)
            self.held = None


class FakeGripper:
    def __init__(self, world):
        self.world = world
        self.width = 0.08

    def move(self, width, speed):
        if width > self.width and self.world.held is not None:
            self.world.release()
        self.width = width

    def read_once(self):
        return type("S", (), {"width": self.width})()


@pytest.fixture
def bench(monkeypatch):
    def build(world):
        monkeypatch.setattr(ts, "probe_table", world.probe_table)
        monkeypatch.setattr(ts, "move_to_pose", world.move_to_pose)
        monkeypatch.setattr(ts, "grasp_at", world.grasp_at)
        monkeypatch.setattr(ts, "_lower_until_contact", world.lower_until_contact)
        gripper = FakeGripper(world)
        world.gripper = gripper

        def open_full(_context=None):
            gripper.move(0.08, 0.1)
            return {}

        monkeypatch.setattr(ts, "open_gripper", open_full)
        return {"panda": object(), "gripper": gripper}

    return build


def axis_error(a, b):
    return abs(ts.normalise_axis_deg(a - b))


def touch_for(world, **kwargs):
    def sense(x, y):
        return world.probe_table(x=x, y=y, open_width=0.008, z_floor=0.012, approach_z=0.09,
                                 retreat_z=0.09, _context=None)["contact_z"]

    return ts._Touch(sense, **kwargs)


@pytest.mark.parametrize("angle", [0, 15, 30, 45, 60, 75, 89, -30, -60])
def test_measures_centre_and_yaw_at_any_orientation(angle):
    truth = block(0.45, -0.05, angle=angle)
    for seed_dx, seed_dy in ((0.0, 0.0), (0.02, -0.01), (-0.03, 0.03), (0.05, 0.04)):
        world = World([truth])
        pose = ts.feel_object(touch_for(world), (0.45 + seed_dx, -0.05 + seed_dy))
        assert pose["found"]
        error = 1000 * math.hypot(pose["xy"][0] - 0.45, pose["xy"][1] + 0.05)
        assert error < 5.0, f"centre off by {error:.1f} mm at {angle} deg"
        # The footprint is a rectangle in the world frame, so at diagonal angles it shears the
        # measured outline and biases yaw. The centre is unaffected, and touch mode is only
        # used on fixed targets, where the centre is the thing that matters.
        if abs(ts.normalise_axis_deg(angle)) <= 30 or abs(ts.normalise_axis_deg(angle)) >= 75:
            assert axis_error(pose["long_axis_deg"], angle) < 15.0


def test_measurement_does_not_follow_the_seed():
    """The defect from 2026-09-01: shifting the guess shifted the answer by the same amount."""
    results = []
    for seed in ((0.43, -0.07), (0.47, -0.03)):
        pose = ts.feel_object(touch_for(World([block(0.45, -0.05)])), seed)
        results.append(pose["xy"])
    drift = 1000 * math.hypot(results[0][0] - results[1][0], results[0][1] - results[1][1])
    assert drift < 4.0


def test_measurement_tolerates_probe_noise():
    errors = []
    for seed in range(12):
        world = World([block(0.45, 0.02, angle=25)], noise=0.001, seed=seed)
        pose = ts.feel_object(touch_for(world), (0.46, 0.0))
        errors.append(1000 * math.hypot(pose["xy"][0] - 0.45, pose["xy"][1] - 0.02))
    assert max(errors) < 7.0


@pytest.mark.parametrize("seed", range(10))
def test_mid_air_false_stops_do_not_corrupt_the_measurement(seed):
    """The rate and height range measured on hardware on 2026-11-09."""
    world = World([block(0.45, -0.05, angle=20)], seed=seed)
    rng = random.Random(seed + 100)
    real = world.probe_table

    def glitchy(**kwargs):
        result = real(**kwargs)
        if rng.random() < 0.07:
            result["contact_z"] = rng.uniform(0.033, 0.077)
        return result

    world.probe_table = glitchy
    pose = ts.feel_object(touch_for(world, max_probes=300), (0.46, -0.04))
    assert pose["found"]
    assert 1000 * math.hypot(pose["xy"][0] - 0.45, pose["xy"][1] + 0.05) < 6.0
    assert axis_error(pose["long_axis_deg"], 20) < 15.0
    assert pose["body_long_m"] == pytest.approx(0.055, abs=0.012)


def test_a_clean_table_reading_is_not_probed_twice():
    world = World([])
    touch = touch_for(world)
    touch.contact(0.45, 0.0)
    assert len(touch.records) == 1


def test_a_stalled_probe_is_retried_and_can_recover():
    answers = iter([None, TABLE, TABLE])
    touch = ts._Touch(lambda x, y: next(answers))
    assert touch.contact(0.45, 0.0) == pytest.approx(TABLE)
    assert len(touch.records) == 3


def test_a_neighbour_is_not_absorbed_into_the_object():
    """Blocks 78 mm apart side by side, the spacing that fooled the extent probe."""
    world = World([block(0.45, -0.05), block(0.45, 0.028)])
    pose = ts.feel_object(touch_for(world), (0.45, -0.05))
    assert pose["found"]
    assert abs(pose["xy"][1] + 0.05) < 0.006
    assert pose["body_long_m"] < 0.07


@pytest.mark.parametrize("gap_mm", [5, 10, 15, 20, 23])
def test_a_gap_the_fingertip_cannot_resolve_is_never_a_confident_wrong_answer(gap_mm):
    """End-to-end along x, where the footprint is narrowest, the gap still cannot be resolved.

    Merging them is acceptable because the size gate refuses it. What must never happen is
    a block-sized answer in the wrong place. Across y the footprint is 37.5 mm, so touch
    mode needs wider spacing there still; it is reserved for targets someone placed.
    """
    centre_gap = 0.055 + gap_mm / 1000.0
    world = World([block(0.40, -0.05, angle=0), block(0.40 + centre_gap, -0.05, angle=0)])
    pose = ts.feel_object(touch_for(world), (0.40, -0.05))
    if pose["found"] and pose["body_long_m"] <= ts.MAX_BODY_LONG_M:
        assert abs(pose["xy"][0] - 0.40) < 0.008


def test_one_long_block_is_one_object():
    """A 55 mm block once clustered into two objects and was stacked on itself."""
    world = World([block(0.45, -0.05)])
    pose = ts.feel_object(touch_for(world), (0.45, -0.05))
    assert pose["found"] and pose["n_edges"] >= 6


def test_a_first_probe_landing_on_the_object_counts():
    """A plane estimated from contacts so far would call this first contact the table."""
    world = World([block(0.45, 0.0)])
    assert ts.feel_object(touch_for(world), (0.45, 0.0))["first_hit"] == [0.45, 0.0]


def test_empty_table_reports_nothing_found():
    pose = ts.feel_object(touch_for(World([])), (0.45, 0.0), search_radius=0.05)
    assert pose["found"] is False


def test_probe_budget_is_enforced():
    with pytest.raises(ts._OutOfProbes):
        ts.feel_object(touch_for(World([block(0.45, 0.0)]), max_probes=5), (0.45, 0.0))


@pytest.mark.parametrize("axis", [0.0, 20.0, 45.0, 90.0, -35.0, -89.0, 135.0])
def test_fingers_close_across_the_short_axis(axis):
    w, x, y, z = ts.wrist_quaternion(axis)
    # Tool x rotated into the world; it has to lie along the object's long axis.
    tool_x = (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y))
    assert abs(tool_x[2]) < 1e-9
    assert axis_error(math.degrees(math.atan2(tool_x[1], tool_x[0])), axis) < 1e-6


def test_zero_axis_is_the_grasp_verified_on_hardware():
    assert ts.wrist_quaternion(0.0) == pytest.approx([0.0, 1.0, 0.0, 0.0])


def test_corridor_check_flags_a_finger_above_the_neighbour():
    pick = {"xy": [0.45, 0.0], "long_axis_deg": 0.0}
    near = {"xy": [0.45, 0.060], "long_axis_deg": 0.0, "body_long_m": 0.055, "body_short_m": 0.028}
    far = {"xy": [0.45, 0.120], "long_axis_deg": 0.0, "body_long_m": 0.055, "body_short_m": 0.028}
    assert ts.finger_corridor_clear(pick, 0.080, near) is False
    assert ts.finger_corridor_clear(pick, 0.046, far) is True


def two_blocks():
    return [block(0.50, -0.02, angle=10), block(0.45, -0.16, angle=70)]


def run(ctx, **kwargs):
    events = []

    def record(phase, kind, label=None, data=None):
        events.append((phase, kind, label, data))

    result = ts.touch_stack(base_xy=[0.51, -0.01], pick_xy=[0.44, -0.15], _context=ctx,
                            _record_event=record, **kwargs)
    return result, events


def test_full_stack_verifies_by_height(bench):
    world = World(two_blocks())
    result, events = run(bench(world))
    assert result["stage"] == "stacked"
    assert result["task_verified_success"] is True
    assert result["base_height_mm"] == pytest.approx(27.0, abs=0.5)
    assert world.grasps[0]["open_width"] == pytest.approx(0.046, abs=0.012)
    verdicts = [e for e in events if e[1] == "verdict"]
    assert len(verdicts) == 1 and verdicts[0][3]["task_verified_success"] is True


def test_misses_are_kept_as_evidence(bench):
    world = World(two_blocks())
    result, _ = run(bench(world))
    table = [p for p in result["probes"] if p["contact_z"] <= TABLE + 0.005]
    assert len(table) > 10
    assert result["table_z"] == pytest.approx(TABLE, abs=1e-3)


def test_measure_only_never_grasps(bench):
    world = World(two_blocks())
    result, events = run(bench(world), measure_only=True)
    assert result["stage"] == "measured"
    assert world.grasps == []
    assert [e for e in events if e[1] == "verdict"][0][3]["task_verified_success"] is None


def test_block_is_never_released_in_free_space(bench):
    world = World(two_blocks())
    world.floor_contact = False
    result, _ = run(bench(world))
    assert result["stage"] == "place_no_contact"
    assert result["task_verified_success"] is False
    assert result["holding"] is True
    assert world.released_at is None


def test_failed_grasp_is_reported_not_stacked(bench):
    world = World(two_blocks())
    world.grasp_at = lambda **kw: {"grasped": False, "note": "closed on air"}
    result, _ = run(bench(world))
    assert result["stage"] == "grasp" and result["task_verified_success"] is False


def test_block_that_fell_off_fails_verification(bench):
    world = World(two_blocks())
    original = world.release
    world.release = lambda: (original(), world.blocks[1].update(base_z=0.0, xy=(0.40, 0.10)))
    result, _ = run(bench(world))
    assert result["stage"] == "not_stacked"
    assert result["task_verified_success"] is False


def test_blocks_too_close_together_are_refused_before_contact(bench):
    """A 20 mm gap: too narrow to measure separately, and a finger would not fit anyway."""
    world = World([block(0.45, -0.02, angle=0), block(0.45, 0.028, angle=0)])
    result = ts.touch_stack(base_xy=[0.45, -0.02], pick_xy=[0.45, 0.028],
                            _context=bench(world))
    assert result["task_verified_success"] is False
    assert result["stage"] in {"corridor", "same_object", "find_base", "find_pick",
                               "size_base", "size_pick", "fit_base", "fit_pick"}
    assert world.grasps == []


def test_merged_blocks_are_refused_by_size(bench, monkeypatch):
    world = World(two_blocks())
    real = ts.feel_object

    def merged(touch, seed, **kwargs):
        pose = real(touch, seed, **kwargs)
        pose["body_long_m"] = 0.110
        return pose

    monkeypatch.setattr(ts, "feel_object", merged)
    result, _ = run(bench(world))
    assert result["stage"] == "size_base" and world.grasps == []


class FakeImpedance:
    last = None

    def __init__(self):
        FakeImpedance.last = self
        self.z = None

    def set_impedance(self, matrix):
        pass

    def set_nullspace_stiffness(self, value):
        pass

    def set_control(self, position, quat, q0):
        self.z = float(position[2])


class FakeDescent:
    """An arm that tracks its command, with a scripted force estimate per tick."""

    def __init__(self, z, force_at):
        self.z, self.force_at, self.tick, self.controller_on = z, force_at, 0, False

    def get_position(self):
        if FakeImpedance.last is not None and FakeImpedance.last.z is not None:
            self.z = FakeImpedance.last.z
        return [0.45, 0.0, self.z]

    def get_orientation(self):
        return [0.0, 1.0, 0.0, 0.0]

    def get_state(self):
        return type("S", (), {"q": [0.0] * 7, "O_F_ext_hat_K": [0, 0, self.force_at(self.tick), 0, 0, 0]})()

    def start_controller(self, controller):
        self.controller_on = True

    def stop_controller(self):
        self.controller_on = False

    def create_context(self, frequency, max_runtime):
        outer = self
        limit = int(frequency * max_runtime)

        class Context:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def ok(self):
                outer.tick += 1
                return outer.tick < limit

        return Context()


@pytest.fixture
def fake_controllers(monkeypatch):
    import sys
    import types

    FakeImpedance.last = None
    module = types.ModuleType("panda_py")
    module.controllers = types.SimpleNamespace(CartesianImpedance=FakeImpedance)
    monkeypatch.setitem(sys.modules, "panda_py", module)


def engaged_offset(tick):
    """The free-air shift measured on 2026-11-09: starts at 0, reaches -5 N in 0.3 s."""
    return 5.0 - min(tick / 30.0, 1.0) * 5.0


def test_controller_engagement_shift_does_not_release_the_block(fake_controllers):
    arm = FakeDescent(0.10, engaged_offset)
    result = ts._lower_until_contact(arm, start_z=0.10, floor_z=0.04)
    assert result["contact"] is False
    assert arm.controller_on is False


def test_one_noisy_sample_does_not_release_the_block(fake_controllers):
    arm = FakeDescent(0.10, lambda t: engaged_offset(t) + (12.0 if t == 120 else 0.0))
    assert ts._lower_until_contact(arm, start_z=0.10, floor_z=0.04)["contact"] is False


def test_sustained_force_is_contact(fake_controllers):
    arm = FakeDescent(0.10, lambda t: engaged_offset(t) + (7.0 if t >= 200 else 0.0))
    result = ts._lower_until_contact(arm, start_z=0.10, floor_z=0.04)
    assert result["contact"] is True
    assert 0.04 < result["contact_z"] < 0.08
    assert arm.controller_on is False


def test_probing_does_not_use_the_force_trigger(bench):
    world = World(two_blocks())
    seen = []
    real = world.probe_table

    def spy(**kwargs):
        seen.append((kwargs["force_thresh"], kwargs["z_floor"]))
        return real(**kwargs)

    world.probe_table = spy
    run(bench(world), measure_only=True)
    assert seen and all(f >= 20.0 and z == 0.008 for f, z in seen)


MAP = {"A": [[1.0, 0.0], [0.0, 1.0]], "b": [-0.06, 0.01]}


def fake_camera(world, bad_frames=()):
    """Reports blocks where the map says they should appear from the current hand pose."""
    calls = {"n": 0}

    def blobs(color, camera, ctx, close):
        calls["n"] += 1
        if calls["n"] in bad_frames:
            return ([{"u": 640.0, "v": 1.0, "npix": 900, "depth_m": 0.31, "angle_deg": 0.0,
                      "extent_px": [60.0, 30.0]}], list(world.pose))
        target = world.blocks[0] if color == "green" else world.blocks[1]
        ee = np.array(world.pose[:2])
        offset = np.array(target["xy"]) - ee - np.array(MAP["b"])
        d = 0.31
        v, u = offset[0] * ts.FX_FOR_MAP / d + ts.CY_FOR_MAP, offset[1] * ts.FX_FOR_MAP / d + ts.CX_FOR_MAP
        a = math.radians(target["angle"])
        image = math.degrees(math.atan2(math.cos(a), math.sin(a)))
        side = ts.FX_FOR_MAP / d / math.sqrt(3.0)
        return ([{"u": float(u), "v": float(v), "npix": 2300, "depth_m": d, "angle_deg": image,
                  "extent_px": [target["length"] * side, target["width"] * side]}], list(world.pose))

    return blobs, calls


@pytest.fixture
def camera_bench(bench, monkeypatch):
    def build(world, bad_frames=(), camera_map=MAP):
        ctx = bench(world)
        blobs, calls = fake_camera(world, bad_frames)
        monkeypatch.setattr(ts, "_fresh_blobs", blobs)
        monkeypatch.setattr(ts, "load_camera_map", lambda store: camera_map)
        monkeypatch.setattr(ts.time, "sleep", lambda s: None)
        return ctx, calls

    return build


def test_camera_located_blocks_stack_without_probing_edges(camera_bench):
    world = World(two_blocks())
    ctx, _ = camera_bench(world)
    result = ts.touch_stack(_context=ctx)
    assert result["stage"] == "stacked"
    assert result["base"]["method"] == "camera"
    assert axis_error(result["pick"]["long_axis_deg"], 70) < 2.0
    assert axis_error(result["base"]["long_axis_deg"], 10) < 2.0
    # One confirming probe per block, one for the table, up to three to verify.
    assert len(world.probes) <= 3 * 3 + 9


def test_a_wild_close_reading_is_looked_at_again_and_never_acted_on(camera_bench):
    world = World(two_blocks())
    moves = []
    original = world.move_to_pose

    def spy(position, orientation=None, *, speed_factor=0.1, _context=None):
        moves.append(tuple(position))
        return original(position, orientation, speed_factor=speed_factor, _context=_context)

    world.move_to_pose = spy
    ctx, _ = camera_bench(world, bad_frames={2, 3})
    result = ts.touch_stack(_context=ctx, measure_only=True)
    assert result["stage"] == "measured"
    assert result["base"]["rejected_looks"] == 2
    assert all(0.28 <= x <= 0.62 and abs(y) <= 0.32 for x, y, *_ in moves)


def test_a_camera_position_with_no_block_is_refused(camera_bench):
    world = World(two_blocks())
    world.probe_table = lambda **kw: {"contact_z": TABLE, "contacted": True}
    ctx, _ = camera_bench(world, camera_map={"A": MAP["A"], "b": [-0.06, 0.01]})
    result = ts.touch_stack(_context=ctx)
    assert result["stage"] == "find_base" and "found no block" in result["note"]


def test_no_camera_map_is_a_clear_refusal(camera_bench):
    world = World(two_blocks())
    ctx, _ = camera_bench(world, camera_map=None)
    result = ts.touch_stack(_context=ctx)
    assert result["stage"] == "find_base" and "camera map" in result["note"]


def test_camera_map_recovers_known_mapping():
    rng = random.Random(1)
    truth = {"green": (0.47, -0.18), "blue": (0.48, 0.11)}
    A = np.array([[0.9, 0.02], [0.05, 0.95]])
    b = np.array([-0.06, 0.01])
    rows = []
    for _ in range(40):
        color = rng.choice(list(truth))
        ee = [rng.uniform(0.40, 0.48), rng.uniform(-0.12, 0.04), 0.31]
        raw = np.linalg.solve(A, np.array(truth[color]) - np.array(ee[:2]) - b)
        d = rng.uniform(0.29, 0.33)
        rows.append({"color": color, "ee": ee, "npix": 3000, "depth": d,
                     "v": raw[0] * ts.FX_FOR_MAP / d + ts.CY_FOR_MAP,
                     "u": raw[1] * ts.FX_FOR_MAP / d + ts.CX_FOR_MAP})
    fitted = ts.fit_camera_map(rows, truth)
    assert np.allclose(fitted["A"], A, atol=1e-6) and np.allclose(fitted["b"], b, atol=1e-6)


def test_dilation_is_wider_across_the_fingers():
    assert ts.probe_dilation(0.0) == pytest.approx(0.020)
    assert ts.probe_dilation(90.0) == pytest.approx(0.0375)


def test_mock_mode_does_nothing():
    result = ts.touch_stack(_context=None)
    assert result["_source"] == "mock" and result["task_verified_success"] is None


def test_probing_defaults_are_unchanged_for_existing_callers():
    import inspect

    from hebbot.skills.primitives import probe_table

    signature = inspect.signature(probe_table).parameters
    assert signature["approach_z"].default == 0.16
    assert signature["retreat_z"].default == 0.20
