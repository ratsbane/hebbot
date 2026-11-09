"""Stack one block on another by touch: vision to get close, contact for everything else.

This replaces a run of one-off bench scripts from 2026-09-01/02. Each of those fixed one
defect in a copy of the last one, so no single script had them all: yaw lived in one, the
narrow pre-open in another, and none placed by force. This module carries the lessons in
one place, and runs through the dispatcher so that an attempt is an episode.

**How an object is measured.** The older scripts averaged whatever hits a raster produced,
which biases the centre toward where the search happened to enter the object, and split or
merged objects by a clustering radius (45 mm against a 55 mm block produced "two objects"
that were one). Here the probe walks outward along rays from inside the object until it
falls off, brackets each edge, and fits the smallest rectangle around those edge points.
The result depends on where the edges are rather than on where the search started. A ray
stops at its first miss, so a neighbouring block is never reached and no clustering exists.

**What is deliberately absent.** No hand-eye transform and no stored camera correction: the
similarity from #155 and `touch_correction.json` both predate the bracket being straightened
(#156). The camera only seeds a search wide enough not to care.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Optional

import numpy as np

from hebbot.skills.primitives import (
    _extract, _qmul, grasp_at, move_to_pose, open_gripper, probe_table,
)
from hebbot.skills.selfcal import _observe, _spiral, image_to_world_offset

# Used only to tell "on the table" from "on something". #144 and 2026-09-01 measured the
# table at 0.0164-0.0193 across the workspace, well inside the 10 mm classification margin.
# Heights are taken from each run's own table contacts wherever it has any.
DEFAULT_TABLE_Z = 0.0177
MIN_OBJECT_M = 0.010
TOP_TOLERANCE_M = 0.006          # a contact this far from the object's top is another surface

FINGERTIP_M = 0.020              # a probe of finite width measures the object dilated by this
PROBE_OPEN_WIDTH = 0.008
PROBE_Z_FLOOR = 0.012
HOVER_Z = 0.09                   # clears a block lying on the table, saves ~4 s per probe
GRASP_HEIGHT_M = 0.010           # EE this far above the table when closing on a lying block
LIFT_Z = 0.22
MIN_SEPARATION_M = 0.035         # two "objects" closer than this are one object
# Largest bodies on this bench are about 67 x 37 mm. A ray can step across a narrow gap, so
# two nearby blocks can measure as one big one; anything beyond these is refused, not fitted.
MAX_BODY_LONG_M = 0.080
MAX_BODY_SHORT_M = 0.050

WORKSPACE = ((0.30, 0.60), (-0.30, 0.30))
SURVEY_POSES = ((0.42, -0.05, 0.31), (0.42, -0.19, 0.31), (0.50, 0.06, 0.31),
                (0.36, 0.04, 0.31), (0.52, -0.16, 0.31))


class _OutOfProbes(Exception):
    """The probe budget ran out; better to report that than to keep pressing on."""


class _Touch:
    """One attempt's contact measurements, misses included.

    A miss is evidence, not waste: the contact-search literature finds that free-space
    observations give the largest single gain, and every one here is kept for the record.
    """

    def __init__(self, sense: Callable[[float, float], Optional[float]], *,
                 table_ref: float = DEFAULT_TABLE_Z, min_object: float = MIN_OBJECT_M,
                 top_tolerance: float = TOP_TOLERANCE_M, max_probes: int = 180):
        self.sense = sense
        self.table_ref = table_ref
        self.min_object = min_object
        self.top_tolerance = top_tolerance
        self.max_probes = max_probes
        self.records: list[dict] = []
        self.cache: dict[tuple[int, int], Optional[float]] = {}
        self.excluded: list[dict] = []

    def _is_excluded(self, x: float, y: float) -> bool:
        return any(rect_distance((x, y), r["xy"], r["long_axis_deg"], r["half_long"],
                                 r["half_short"]) <= r["margin"] for r in self.excluded)

    def contact(self, x: float, y: float) -> Optional[float]:
        """Contact height at (x, y), or None where nothing can be said."""
        (x_lo, x_hi), (y_lo, y_hi) = WORKSPACE
        if not (x_lo <= x <= x_hi and y_lo <= y <= y_hi) or self._is_excluded(x, y):
            return None
        key = (round(x * 1e4), round(y * 1e4))
        if key in self.cache:
            return self.cache[key]
        if len(self.records) >= self.max_probes:
            raise _OutOfProbes(f"probe budget of {self.max_probes} exhausted")
        z = self.sense(x, y)
        self.cache[key] = z
        self.records.append({"x": round(x, 4), "y": round(y, 4),
                             "contact_z": None if z is None else round(float(z), 4)})
        return z

    def inside(self, x: float, y: float, top: Optional[float] = None) -> Optional[bool]:
        """True on the object, False off it, None if the probe could not tell."""
        z = self.contact(x, y)
        if z is None:
            return None
        if z <= self.table_ref + self.min_object:
            return False
        return top is None or abs(z - top) <= self.top_tolerance

    def table_estimate(self) -> float:
        low = [r["contact_z"] for r in self.records if r["contact_z"] is not None
               and r["contact_z"] <= self.table_ref + self.min_object]
        return float(np.median(low)) if low else self.table_ref


def rect_distance(point, centre, long_axis_deg, half_long, half_short) -> float:
    """Distance from a point to a filled rectangle (0 inside)."""
    a = math.radians(long_axis_deg)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    along = dx * math.cos(a) + dy * math.sin(a)
    across = -dx * math.sin(a) + dy * math.cos(a)
    return float(math.hypot(max(abs(along) - half_long, 0.0),
                            max(abs(across) - half_short, 0.0)))


def normalise_axis_deg(angle: float) -> float:
    """An axis has no direction: map it into [-90, 90)."""
    return ((float(angle) + 90.0) % 180.0) - 90.0


def fit_rectangle(points) -> dict:
    """Smallest-area rectangle around edge points: centre, long-axis angle and extents."""
    pts = np.asarray(points, dtype=float)
    best = None
    for deg in range(180):
        a = math.radians(deg)
        u = pts[:, 0] * math.cos(a) + pts[:, 1] * math.sin(a)
        v = -pts[:, 0] * math.sin(a) + pts[:, 1] * math.cos(a)
        extent_u, extent_v = float(u.max() - u.min()), float(v.max() - v.min())
        area = extent_u * extent_v
        if best is None or area < best[0] - 1e-12:
            best = (area, deg, extent_u, extent_v, float((u.max() + u.min()) / 2),
                    float((v.max() + v.min()) / 2))
    _, deg, extent_u, extent_v, mid_u, mid_v = best
    a = math.radians(deg)
    centre = (mid_u * math.cos(a) - mid_v * math.sin(a),
              mid_u * math.sin(a) + mid_v * math.cos(a))
    long_m, short_m, axis = (extent_u, extent_v, float(deg)) if extent_u >= extent_v \
        else (extent_v, extent_u, deg + 90.0)
    axis = normalise_axis_deg(axis)
    a = math.radians(axis)
    residuals = []
    for x, y in pts:
        dx, dy = x - centre[0], y - centre[1]
        along = abs(dx * math.cos(a) + dy * math.sin(a))
        across = abs(-dx * math.sin(a) + dy * math.cos(a))
        residuals.append(min(abs(long_m / 2 - along), abs(short_m / 2 - across)))
    return {"xy": [float(centre[0]), float(centre[1])], "long_axis_deg": axis,
            "long_m": long_m, "short_m": short_m,
            "fit_rms_mm": float(np.sqrt(np.mean(np.square(residuals))) * 1000)}


def _edge_along(touch: _Touch, origin, angle_deg: float, top: float, *, step: float,
                refine: int, max_reach: float) -> tuple[str, Optional[float]]:
    """Walk outward until the probe falls off; returns (status, radius of the edge)."""
    ux, uy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    at = lambda r: touch.inside(origin[0] + ux * r, origin[1] + uy * r, top=top)
    last_in, first_out, r = 0.0, None, step
    while r <= max_reach + 1e-9:
        state = at(r)
        if state is None:
            return "unknown", None
        if not state:
            first_out = r
            break
        last_in, r = r, r + step
    if first_out is None:
        return "open", last_in
    for _ in range(refine):
        mid = (last_in + first_out) / 2
        state = at(mid)
        if state is None:
            return "unknown", None
        if state:
            last_in = mid
        else:
            first_out = mid
    return "edge", (last_in + first_out) / 2


def feel_object(touch: _Touch, seed, *, coarse_step: float = 0.040,
                search_radius: float = 0.135, n_rays: int = 8) -> dict:
    """Find the object nearest the seed and measure it by its edges.

    The coarse spacing is safe up to object width plus fingertip width: 28 + 20 mm for the
    narrowest block here, so 40 mm cannot step over one.
    """
    first = None
    for dx, dy in _spiral(coarse_step, search_radius):
        point = (float(seed[0] + dx), float(seed[1] + dy))
        if touch.inside(*point):
            first = point
            break
    if first is None:
        return {"found": False,
                "reason": f"nothing felt within {search_radius * 1000:.0f} mm of the seed"}
    top = float(touch.contact(*first))

    # Rough centre, so the measuring rays start well inside and see every side.
    ends = {}
    for angle in (0, 90, 180, 270):
        # A first hit near one end of a long block can see more than 90 mm of it. A ray that
        # runs out of reach still says the object extends at least that far.
        status, radius = _edge_along(touch, first, angle, top, step=0.012, refine=0,
                                     max_reach=0.11)
        ends[angle] = radius if status in ("edge", "open") else 0.0
    centre = (first[0] + (ends[0] - ends[180]) / 2, first[1] + (ends[90] - ends[270]) / 2)
    if touch.inside(*centre, top=top) is not True:
        centre = first

    edges, open_rays = [], 0
    for i in range(int(n_rays)):
        angle = 22.5 + i * 360.0 / int(n_rays)
        status, radius = _edge_along(touch, centre, angle, top, step=0.015, refine=2,
                                     max_reach=0.075)
        if status == "open":
            open_rays += 1
        elif status == "edge":
            edges.append((centre[0] + radius * math.cos(math.radians(angle)),
                          centre[1] + radius * math.sin(math.radians(angle))))
    if open_rays:
        return {"found": False, "first_hit": list(first),
                "reason": f"{open_rays} ray(s) never left the surface: larger than any "
                          "block here, or two objects touching"}
    if len(edges) < 6:
        return {"found": False, "first_hit": list(first),
                "reason": f"only {len(edges)} edges measured; too few to fit"}

    fit = fit_rectangle(edges)
    return {"found": True, **fit, "top": top, "first_hit": list(first),
            "n_edges": len(edges), "elongation": fit["long_m"] / max(fit["short_m"], 1e-3),
            "body_long_m": max(fit["long_m"] - FINGERTIP_M, 0.0),
            "body_short_m": max(fit["short_m"] - FINGERTIP_M, 0.0),
            "edges": [[round(x, 4), round(y, 4)] for x, y in edges]}


def wrist_quaternion(long_axis_deg: float) -> list[float]:
    """Gripper-down orientation whose fingers close across an object's short axis.

    With the hand pointing down, tool z is world -z, so a positive wrist yaw turns the hand
    clockwise seen from above. The fingers separate along tool y; the long axis has to lie
    along tool x, which is at world angle -yaw. Hence yaw = -axis.
    """
    half = math.radians(-normalise_axis_deg(long_axis_deg)) / 2.0
    return list(_qmul([0.0, 1.0, 0.0, 0.0], [math.cos(half), 0.0, 0.0, math.sin(half)]))


def finger_corridor_clear(obj: dict, open_width: float, other: dict,
                          margin: float = 0.005) -> bool:
    """Whether the open fingers come down clear of a neighbouring object (#150, #156)."""
    a = math.radians(obj["long_axis_deg"] + 90.0)
    half_tip = FINGERTIP_M / 2.0
    for sign in (1, -1):
        finger = (obj["xy"][0] + sign * (open_width / 2) * math.cos(a),
                  obj["xy"][1] + sign * (open_width / 2) * math.sin(a))
        if rect_distance(finger, other["xy"], other["long_axis_deg"],
                         other["body_long_m"] / 2,
                         other["body_short_m"] / 2) <= half_tip + margin:
            return False
    return True


def _lower_until_contact(panda, *, start_z: float, floor_z: float, contact_force: float = 4.0,
                         speed: float = 0.015) -> dict:
    """Lower the held block until the table or pile pushes back, then unload the spring.

    Contact is an external-force rise, which still works when a held block slips upward in
    the jaws (#136). 4 N is load-bearing: 2.5 N broke detection (#142). The force only
    reaches the threshold once the commanded height has sunk well below the actual one, at
    300 N/m about 13 mm, so the floor must sit far below the expected contact; it is the
    stop for a miss, not the target. The first 0.2 s is ignored because the controller's
    engagement transient once false-tripped and dropped a block from 19 cm (2026-06-28).
    """
    from panda_py import controllers

    position = panda.get_position()
    x, y = float(position[0]), float(position[1])
    quat = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 300.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    dt = 0.01
    tz = float(position[2])
    guard_z = start_z - 0.012
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
    contact_z, settle, unload_ticks = None, 0, 0
    panda.start_controller(ctrl)
    try:
        runtime = (start_z - floor_z) / speed + 2.0
        with panda.create_context(frequency=100, max_runtime=runtime) as cx:
            while cx.ok():
                actual = float(panda.get_position()[2])
                if contact_z is not None:
                    # Ease the command back up to where the block actually is, so opening
                    # the jaws does not let the stored spring drive the hand into the pile.
                    tz = min(contact_z, tz + (contact_z - tz) / max(30 - unload_ticks, 1))
                    ctrl.set_control(np.array([x, y, tz]), quat, q0)
                    unload_ticks += 1
                    if unload_ticks >= 30:
                        break
                    continue
                tz = max(floor_z, tz - speed * dt)
                ctrl.set_control(np.array([x, y, tz]), quat, q0)
                fz = float(panda.get_state().O_F_ext_hat_K[2])
                if settle < 20:
                    settle += 1
                    fz0 = fz
                    continue
                if tz < guard_z and abs(fz - fz0) > contact_force:
                    contact_z = actual
                    continue
                if tz <= floor_z + 1e-4:
                    break
    finally:
        panda.stop_controller()
    return {"contact": contact_z is not None,
            "contact_z": None if contact_z is None else round(contact_z, 4)}


def _seed_from_camera(color: str, camera: Optional[str], speed_factor: float, ctx) -> Optional[list]:
    """Roughly where the camera thinks a block is; touch does the rest."""
    panda = _extract(ctx, "panda")
    for pose in SURVEY_POSES:
        move_to_pose(list(pose), speed_factor=speed_factor, _context=ctx)
        seen = _observe(color, camera, ctx)
        blobs = [b for b in seen.get("blobs", []) or []
                 if b.get("v", 0) > 130 and b.get("npix", 0) >= 400 and b.get("depth_m")]
        if not blobs:
            continue
        here = [float(v) for v in panda.get_position()]
        dx, dy = image_to_world_offset(blobs[0]["u"], blobs[0]["v"], blobs[0]["depth_m"])
        (x_lo, x_hi), (y_lo, y_hi) = WORKSPACE
        return [float(np.clip(here[0] + dx, x_lo, x_hi)), float(np.clip(here[1] + dy, y_lo, y_hi))]
    return None


def _public(pose: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in pose.items()}


def touch_stack(
    *,
    base_color: str = "green",
    pick_color: str = "blue",
    base_xy: Optional[list] = None,
    pick_xy: Optional[list] = None,
    measure_only: bool = False,
    camera: Optional[str] = None,
    coarse_step: float = 0.040,
    search_radius: float = 0.135,
    max_probes: int = 180,
    hover_z: float = HOVER_Z,
    lift_z: float = LIFT_Z,
    contact_force: float = 4.0,
    max_fit_rms_mm: float = 8.0,
    speed_factor: float = 0.06,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
    _log_decision: Optional[Any] = None,
    _record_event: Optional[Any] = None,
) -> dict:
    """Find two blocks by touch, pick one up, set it on the other and feel the result.

    With `measure_only` the arm stops after measuring both blocks and never grasps, which
    is the safe way to check the measurements on hardware. `task_verified_success` is True
    only when a probe finds the pile one block taller.
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    if panda is None or gripper is None:
        return {"_source": "mock", "task_verified_success": None, "stage": "mock",
                "note": "mock — no arm/gripper"}

    started = time.time()

    def emit(phase, kind, label, data):
        if _record_event is not None:
            _record_event(phase, kind, label=label, data=data)

    hover = {"z": float(hover_z)}

    def sense(x, y):
        result = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH, z_floor=PROBE_Z_FLOOR,
                             approach_z=hover["z"], retreat_z=hover["z"], _context=_context)
        if result.get("contacted") is False:
            return None                       # stalled in mid-air: no statement about height
        return result.get("contact_z")

    touch = _Touch(sense, max_probes=int(max_probes))
    out: dict = {"_source": "live", "task_verified_success": False, "stage": "start",
                 "base_color": base_color, "pick_color": pick_color}

    def finish(stage, *, success=False, note=None, refusal=False):
        out.update({"stage": stage, "task_verified_success": success, "note": note,
                    "n_probes": len(touch.records), "elapsed_s": round(time.time() - started, 1),
                    "probes": touch.records})
        if refusal:
            emit("abstain", "refusal", "touch_stack_refused", {"stage": stage, "note": note})
        emit("verify", "verdict", "task_verified",
             {"task_verified_success": success if not measure_only else None,
              "stage": stage, "note": note, "n_probes": len(touch.records),
              "elapsed_s": out["elapsed_s"]})
        if _log_decision is not None:
            _log_decision("orchestrator", reasoning=(
                f"touch_stack {pick_color} on {base_color}: {stage}, "
                f"{len(touch.records)} probes" + (f"; {note}" if note else "")))
        return out

    def locate(label, color, given):
        seed = list(given) if given is not None \
            else _seed_from_camera(color, camera, speed_factor, _context)
        if seed is None:
            return None, f"{color} not seen from any survey pose"
        gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
        pose = feel_object(touch, seed, coarse_step=coarse_step, search_radius=search_radius)
        if not pose.get("found"):
            return None, f"{label} ({color}): {pose.get('reason')}"
        pose["seed_xy"] = [round(float(v), 4) for v in seed]
        # See-versus-feel residual: the time series #156 said was never examined.
        pose["see_feel_residual_mm"] = round(
            1000 * float(math.hypot(pose["xy"][0] - seed[0], pose["xy"][1] - seed[1])), 1)
        pose["seeded_by"] = "given" if given is not None else "camera"
        emit("approach", "contact", f"felt_{label}",
             {k: pose.get(k) for k in ("xy", "top", "long_axis_deg", "long_m", "short_m",
                                       "fit_rms_mm", "n_edges", "seed_xy", "seeded_by",
                                       "see_feel_residual_mm", "edges")})
        return pose, None

    try:
        base, problem = locate("base", base_color, base_xy)
        if base is None:
            return finish("find_base", note=problem)
        out["base"] = _public(base)
        touch.excluded.append({"xy": base["xy"], "long_axis_deg": base["long_axis_deg"],
                               "half_long": base["long_m"] / 2,
                               "half_short": base["short_m"] / 2, "margin": 0.006})
        pick, problem = locate("pick", pick_color, pick_xy)
        if pick is None:
            return finish("find_pick", note=problem)
        out["pick"] = _public(pick)
    except _OutOfProbes as exc:
        return finish("touch_budget", note=str(exc))

    table_z = touch.table_estimate()
    out["table_z"] = round(table_z, 4)
    base_height, pick_height = base["top"] - table_z, pick["top"] - table_z
    out.update(base_height_mm=round(base_height * 1000, 1),
               pick_height_mm=round(pick_height * 1000, 1))

    separation = math.hypot(base["xy"][0] - pick["xy"][0], base["xy"][1] - pick["xy"][1])
    open_width = float(np.clip(pick["body_short_m"] + 0.018, 0.040, 0.080))
    out["open_width"] = round(open_width, 4)
    for name, pose in (("base", base), ("pick", pick)):
        if pose["fit_rms_mm"] > max_fit_rms_mm:
            return finish(f"fit_{name}", refusal=True,
                          note=f"{name} edges fit a rectangle to {pose['fit_rms_mm']:.1f} mm rms")
        if pose["body_long_m"] > MAX_BODY_LONG_M or pose["body_short_m"] > MAX_BODY_SHORT_M:
            return finish(f"size_{name}", refusal=True,
                          note=(f"{name} measured {pose['body_long_m'] * 1000:.0f} x "
                                f"{pose['body_short_m'] * 1000:.0f} mm: larger than any block "
                                "here, probably two objects close together"))
    if separation < MIN_SEPARATION_M:
        return finish("same_object", refusal=True,
                      note=f"base and pick are {separation * 1000:.0f} mm apart: one object")
    if not finger_corridor_clear(pick, open_width, base):
        return finish("corridor", refusal=True,
                      note="a finger would come down on the neighbouring block")

    if measure_only:
        return finish("measured", note="measure_only: no grasp attempted")

    pick_quat, place_quat = wrist_quaternion(pick["long_axis_deg"]), \
        wrist_quaternion(base["long_axis_deg"])
    px, py = pick["xy"]
    bx, by = base["xy"]
    open_gripper(_context=_context)
    move_to_pose([px, py, 0.12], orientation=pick_quat, speed_factor=speed_factor,
                 _context=_context)
    grasp = grasp_at(grasp_z=table_z + GRASP_HEIGHT_M,
                     width=float(np.clip(pick["body_short_m"], 0.020, 0.045)),
                     open_width=open_width, descent_speed=0.02, _context=_context)
    out["grasp"] = {k: grasp.get(k) for k in ("grasped", "actual_z", "actual_width",
                                              "grip_high_mm", "note")}
    emit("grasp", "closure", "touch_grasp", out["grasp"])
    if not grasp.get("grasped"):
        open_gripper(_context=_context)
        move_to_pose([px, py, lift_z], speed_factor=speed_factor, _context=_context)
        return finish("grasp", note=grasp.get("note") or "grasp failed")

    # How far the block's underside hangs below the fingertips, from where the jaws actually
    # closed rather than where they were told to.
    hold_offset = float(grasp.get("actual_z") or table_z + GRASP_HEIGHT_M) - table_z
    expected_contact = base["top"] + hold_offset
    start_z = expected_contact + 0.035
    out["expected_contact_z"] = round(expected_contact, 4)

    move_to_pose([px, py, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                 _context=_context)
    emit("lift", "motion", "touch_lift", {"lift_z": lift_z})
    move_to_pose([bx, by, lift_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    move_to_pose([bx, by, start_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    placed = _lower_until_contact(panda, start_z=start_z, floor_z=expected_contact - 0.030,
                                  contact_force=contact_force)
    out["place"] = placed
    if placed["contact"]:
        out["place"]["z_error_mm"] = round((placed["contact_z"] - expected_contact) * 1000, 1)
    emit("verify", "contact", "touch_place", out["place"])

    held_width = float(gripper.read_once().width)
    if not placed["contact"]:
        # Never open the jaws in free space. Take the block back to where it came from and
        # set it down there, so one miss does not leave the scene unknown (#140).
        back_start = table_z + hold_offset + 0.035
        move_to_pose([bx, by, lift_z], orientation=place_quat, speed_factor=speed_factor,
                     _context=_context)
        move_to_pose([px, py, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        move_to_pose([px, py, back_start], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        returned = _lower_until_contact(panda, start_z=back_start,
                                        floor_z=table_z + hold_offset - 0.030,
                                        contact_force=contact_force)
        out["returned"] = returned
        if returned["contact"]:
            gripper.move(width=min(0.08, held_width + 0.012), speed=0.02)
        move_to_pose([px, py, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        out["holding"] = not returned["contact"]
        return finish("place_no_contact", note="no contact at the pile; block taken back")

    # A narrow, slow opening: the fingers still straddle only the block just placed.
    gripper.move(width=min(0.08, held_width + 0.012), speed=0.02)
    move_to_pose([bx, by, lift_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    open_gripper(_context=_context)

    # The reading that settles it, and the only one that can: how tall is the pile now?
    # A wrist image cannot resolve height (#134).
    expected_top = base["top"] + pick_height
    hover["z"] = max(float(hover_z), expected_top + 0.03)
    gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
    a = math.radians(base["long_axis_deg"])
    readings, success = [], False
    touch.excluded.clear()
    touch.max_probes = len(touch.records) + 3
    try:
        for offset in (0.0, 0.010, -0.010):
            z = touch.contact(bx + offset * math.cos(a), by + offset * math.sin(a))
            readings.append(z)
            if z is not None and abs(z - expected_top) <= 0.008:
                success = True
                break
    except _OutOfProbes:
        pass
    out["verify"] = {"expected_top": round(expected_top, 4),
                     "readings": [None if z is None else round(float(z), 4) for z in readings]}
    gripper_home_z = max(float(lift_z), expected_top + 0.06)
    move_to_pose([bx, by, gripper_home_z], speed_factor=speed_factor, _context=_context)
    open_gripper(_context=_context)
    return finish("stacked" if success else "not_stacked", success=success,
                  note=None if success else "pile is not one block taller")


PRIMITIVE_SPECS = [
    {
        "name": "touch_stack",
        "function_spec": "hebbot.skills.touch_stack:touch_stack",
        "expectations": {"checks": [
            {"name": "stacked", "field": "task_verified_success", "op": "eq", "value": True},
        ]},
        "params_schema": {
            "type": "object",
            "properties": {
                "base_color": {"type": "string", "enum": ["blue", "green", "red"],
                               "default": "green"},
                "pick_color": {"type": "string", "enum": ["blue", "green", "red"],
                               "default": "blue"},
                "base_xy": {"type": "array", "items": {"type": "number"},
                            "minItems": 2, "maxItems": 2,
                            "description": "search seed for the base; omit to seed from the camera"},
                "pick_xy": {"type": "array", "items": {"type": "number"},
                            "minItems": 2, "maxItems": 2,
                            "description": "search seed for the block to move"},
                "measure_only": {"type": "boolean", "default": False,
                                 "description": "measure both blocks and stop before grasping"},
                "camera": {"type": "string"},
                "coarse_step": {"type": "number", "minimum": 0.015, "maximum": 0.048,
                                "default": 0.040},
                "search_radius": {"type": "number", "minimum": 0.02, "maximum": 0.20,
                                  "default": 0.135},
                "max_probes": {"type": "integer", "minimum": 10, "maximum": 300, "default": 180},
                "hover_z": {"type": "number", "minimum": 0.07, "maximum": 0.16, "default": 0.09},
                "lift_z": {"type": "number", "minimum": 0.15, "maximum": 0.30, "default": 0.22},
                "contact_force": {"type": "number", "minimum": 3.0, "maximum": 10.0,
                                  "default": 4.0},
                "max_fit_rms_mm": {"type": "number", "minimum": 1.0, "maximum": 30.0,
                                   "default": 8.0},
                "speed_factor": {"type": "number", "minimum": 0.01, "maximum": 0.2,
                                 "default": 0.06},
            },
            "additionalProperties": False,
        },
    },
]


def register_all(substrate) -> None:
    for spec in PRIMITIVE_SPECS:
        substrate.register(
            spec["name"],
            function_spec=spec["function_spec"],
            params_schema=spec.get("params_schema"),
            expectations=spec.get("expectations"),
        )
