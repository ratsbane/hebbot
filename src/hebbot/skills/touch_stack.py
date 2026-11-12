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

from hebbot.memory.scene import load_scene_memory
from hebbot.perception.target import locate as locate_target
from hebbot.skills.primitives import (
    _extract, _qmul, _select_camera, grasp_at, move_to_pose, open_gripper, probe_table,
)
from hebbot.skills.selfcal import _observe, _spiral, image_to_world_offset

# Used only to tell "on the table" from "on something". #144 and 2026-09-01 measured the
# table at 0.0164-0.0193 across the workspace, well inside the 10 mm classification margin.
# Heights are taken from each run's own table contacts wherever it has any.
DEFAULT_TABLE_Z = 0.0177
MIN_OBJECT_M = 0.010
TOP_TOLERANCE_M = 0.006          # a contact this far from the object's top is another surface
CONFIRM_BAND_M = 0.0025          # a reading further than this from the top is re-probed

FINGERTIP_M = 0.020              # finger width along its pad: world x when probing
# A probe of finite width measures the object dilated by its footprint. Closed to 8 mm the
# pair is wider across the fingers than along them: two taped 55 x 28 mm blocks measured
# 76 and 75 long along x and 66 wide along y on 2026-11-09, so 20 mm in x and 37.5 in y.
PROBE_DILATION_M = (0.020, 0.0375)
PROBE_OPEN_WIDTH = 0.008
# Commanded floor. At 0.012 the 5-7 mm of free-space tracking lag lands the hand at about
# 0.0175 whether or not a table is there, so the "reading" was floor plus lag. At 0.008 the
# hand can only stop at that height if something physical stopped it (2026-11-09).
PROBE_Z_FLOOR = 0.008
# Effectively removes the force trigger. The force zero is taken before the controller
# engages and the estimate then shifts by up to 5 N in free air, so a 6 N trigger rode at
# its threshold and fired in mid-air. Position lag is physical: at most 7.4 mm in free air
# against the 10 mm trigger, measured over 60 probes.
PROBE_FORCE_OFF_N = 30.0
HOVER_Z = 0.09                   # clears a block lying on the table, saves ~4 s per probe
GRASP_HEIGHT_M = 0.010           # EE this far above the table when closing on a lying block
LIFT_Z = 0.22
BASELINE_START_TICKS = 30        # at 100 Hz: skip the engagement transient, then
BASELINE_END_TICKS = 50          # average the force estimate over the next 0.2 s
CONTACT_SUSTAIN_TICKS = 5
# Geometric contact is a rate, not a level. The lag between hand and command drifts
# with arm configuration as the descent changes the pose — in the near-base reach it
# grew past any fixed threshold mid-descent (places released 12-23 mm early at
# x <= 0.39, 2026-11-12) — but drift never *stops* the hand: the worst measured ate
# about half the command rate. Real contact stops it. So the trigger is the hand's
# own descent rate collapsing below this fraction of the commanded speed, which a
# drifting gravity model cannot fake and a surface cannot avoid.
CONTACT_VELOCITY_FRACTION = 0.25
CONTACT_VELOCITY_WINDOW_S = 0.10
MIN_SEPARATION_M = 0.035         # two "objects" closer than this are one object
MIN_BLOCK_HEIGHT_M = 0.015       # a remembered pile implying a block outside these is not
MAX_BLOCK_HEIGHT_M = 0.045       # a pile; and a probed top under the max is "on the table"
FAILED_GRASP_RETRACT_M = 0.050   # f1 (2026-11-11): a failed grasp shoved a block 45 mm and
                                 # turned it 90 deg, so a belief it touched is retracted to
                                 # this error bar rather than merely aged
# Largest bodies on this bench are about 67 x 37 mm. A ray can step across a narrow gap, so
# two nearby blocks can measure as one big one; anything beyond these is refused, not fitted.
MAX_BODY_LONG_M = 0.080
MAX_BODY_SHORT_M = 0.050

WORKSPACE = ((0.30, 0.60), (-0.30, 0.30))
WORKSPACE_FOR_TARGETS = ((0.28, 0.62), (-0.32, 0.32))
# The camera finds the block, touch confirms it. Blocks this light turn aside if probed
# near their edges (#160), so the search for edges only runs on fixed targets.
FX_FOR_MAP, CX_FOR_MAP, CY_FOR_MAP = 353.6, 323.6427, 242.8864
LOOK_Z = 0.31
MAX_LOOK_JUMP_M = 0.060          # a new estimate further than this from the last is rejected
MAX_LOOKS = 4
LOOKS_WANTED = 2
EE_LIMITS = ((0.33, 0.60), (-0.28, 0.28))    # no camera-derived command outside these
MAX_BLOB_PX = 5500               # largest a block can cover from LOOK_Z, side faces included
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
        self.top: Optional[float] = None   # the surface being measured, once one is known
        self.describe: Optional[Callable[[], dict]] = None

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
        readings = [self._read(x, y)]
        if self._doubtful(readings[0]):
            readings += [self._read(x, y), self._read(x, y)]
        heard = [float(r) for r in readings if r is not None]
        # One reading is taken at its word. Three are settled by the median, so a single
        # false stop cannot decide. With fewer than two answers there is nothing to settle.
        if len(readings) == 1:
            z = readings[0]
        else:
            z = float(np.median(heard)) if len(heard) >= 2 else None
        self.cache[key] = z
        return z

    def _read(self, x: float, y: float) -> Optional[float]:
        if len(self.records) >= self.max_probes:
            raise _OutOfProbes(f"probe budget of {self.max_probes} exhausted")
        z = self.sense(x, y)
        record = {"x": round(x, 4), "y": round(y, 4),
                  "contact_z": None if z is None else round(float(z), 4)}
        if self.describe is not None:
            record.update(self.describe())
        self.records.append(record)
        return z

    def _doubtful(self, z: Optional[float]) -> bool:
        """Is this a reading worth checking before believing it?

        On 2026-11-09 about 7% of probes stopped in mid-air, at heights that are neither
        table nor block top, and some landed within a few millimetres of the top. So only
        a reading that matches the surface being measured, or the table, passes unchecked.
        """
        if z is None:
            return True
        if float(z) <= self.table_ref + self.min_object:
            return False
        return self.top is None or abs(float(z) - self.top) > CONFIRM_BAND_M

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
    touch.top = None
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
    touch.top = top

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
            "body_long_m": max(fit["long_m"] - probe_dilation(fit["long_axis_deg"]), 0.0),
            "body_short_m": max(fit["short_m"] - probe_dilation(fit["long_axis_deg"] + 90.0), 0.0),
            "edges": [[round(x, 4), round(y, 4)] for x, y in edges]}


def probe_dilation(axis_deg: float) -> float:
    """How much the probe's footprint widens an extent measured along this axis."""
    a = math.radians(axis_deg)
    return PROBE_DILATION_M[0] * abs(math.cos(a)) + PROBE_DILATION_M[1] * abs(math.sin(a))


def fit_camera_map(rows: list, truth: dict) -> dict:
    """Fit target_xy - ee_xy = A @ raw + b from photos of targets whose position is known.

    `rows` are {color, ee, u, v, depth, npix}; `truth` maps color to touch-measured (x, y).
    """
    used = [r for r in rows if r["npix"] < 20000 and r.get("depth") and r["color"] in truth]
    raw = np.array([_raw_image_offset(r["u"], r["v"], r["depth"]) for r in used])
    target = np.array([np.asarray(truth[r["color"]]) - np.asarray(r["ee"][:2]) for r in used])
    solution, *_ = np.linalg.lstsq(np.c_[raw, np.ones(len(used))], target, rcond=None)
    A, b = solution[:2].T, solution[2]
    residual = np.linalg.norm(target - (raw @ A.T + b), axis=1) * 1000
    return {"A": A.tolist(), "b": b.tolist(), "n": len(used),
            "median_residual_mm": round(float(np.median(residual)), 2),
            "max_residual_mm": round(float(residual.max()), 2), "look_z": LOOK_Z}


def _raw_image_offset(u: float, v: float, depth: float) -> np.ndarray:
    metres_per_px = float(depth) / FX_FOR_MAP
    return np.array([(v - CY_FOR_MAP) * metres_per_px, (u - CX_FOR_MAP) * metres_per_px])


def predict_world_xy(camera_map: dict, ee_xy, u: float, v: float, depth: float) -> np.ndarray:
    return np.asarray(ee_xy, dtype=float)[:2] \
        + np.asarray(camera_map["A"]) @ _raw_image_offset(u, v, depth) + np.asarray(camera_map["b"])


def image_axis_to_world_deg(camera_map: dict, angle_deg: float) -> float:
    """World angle of an axis measured in the image; image v follows world x, u follows y."""
    a = math.radians(angle_deg)
    world = np.asarray(camera_map["A"]) @ np.array([math.sin(a), math.cos(a)])
    return normalise_axis_deg(math.degrees(math.atan2(world[1], world[0])))


def _camera_map_path(store):
    from pathlib import Path
    if store is None or not getattr(store, "db_path", None):
        return None
    return Path(store.db_path).parent / "camera_map.json"


def load_camera_map(store) -> Optional[dict]:
    import json
    path = _camera_map_path(store)
    try:
        with open(path) as handle:
            return json.load(handle)
    except Exception:
        return None


def _fresh_blobs(color: str, camera: Optional[str], ctx, *, close: bool):
    """One look with frames that post-date the last motion, and the pose it was taken from."""
    cam = _select_camera(_extract(ctx, "cameras"), camera or "wrist")
    time.sleep(0.8)
    capture = None
    for _ in range(6):
        capture = cam.capture(with_depth=True)
    rgb = capture["color"][:, :, ::-1]
    found = locate_target(rgb, color, depth=capture.get("depth"), max_px=MAX_BLOB_PX,
                          use_reference=not close)
    blobs = [b for b in found.get("blobs", []) or [] if b["npix"] >= 400 and b.get("depth_m")]
    return blobs, [float(v) for v in _extract(ctx, "panda").get_position()]


def _within(point, limits) -> bool:
    return limits[0][0] <= point[0] <= limits[0][1] and limits[1][0] <= point[1] <= limits[1][1]


def look_twice(color: str, camera: Optional[str], camera_map: dict, ctx,
               speed_factor: float, hint_xy: Optional[list] = None) -> dict:
    """Far look to get close, then re-look from directly above the block.

    Measured on 2026-11-09 with the map fit on the other block: 8-14 mm from afar, about
    6 mm from above. A reading that jumps is discarded and looked at again; nothing the
    camera says moves the arm unless it stays on the table and inside the limits.

    A hint from the scene memory is tried before the survey poses: one look straight at
    where the block last was. The hint never supplies the pose, only the viewpoint, so a
    stale memory costs one wasted look and cannot shift the answer. A block seen from the
    hint pose skips the survey sweep entirely.
    """
    b = np.asarray(camera_map["b"])
    survey = [(p[0], p[1]) for p in SURVEY_POSES]
    if hint_xy is not None:
        viewpoint = np.asarray(hint_xy, dtype=float)[:2] - b
        if _within(viewpoint, EE_LIMITS):
            survey.insert(0, (float(viewpoint[0]), float(viewpoint[1])))
    far, hinted = None, False
    for index, pose in enumerate(survey):
        move_to_pose([pose[0], pose[1], LOOK_Z], speed_factor=speed_factor, _context=ctx)
        blobs, ee = _fresh_blobs(color, camera, ctx, close=False)
        if blobs:
            blob = max(blobs, key=lambda q: q["npix"])
            far = predict_world_xy(camera_map, ee, blob["u"], blob["v"], blob["depth_m"])
            hinted = hint_xy is not None and len(survey) > len(SURVEY_POSES) and index == 0
            break
    if far is None:
        return {"found": False, "reason": f"{color} not seen from any survey pose"}
    if not _within(far, WORKSPACE_FOR_TARGETS):
        return {"found": False, "reason": f"{color} predicted off the table at {far.tolist()}"}

    current, accepted, rejected, blob = far, 0, 0, None
    for _ in range(MAX_LOOKS):
        command = current - b
        if not _within(command, EE_LIMITS):
            return {"found": False,
                    "reason": f"{color}: refused to move to {np.round(command, 3).tolist()}"}
        move_to_pose([float(command[0]), float(command[1]), LOOK_Z],
                     speed_factor=speed_factor, _context=ctx)
        blobs, ee = _fresh_blobs(color, camera, ctx, close=True)
        if not blobs:
            rejected += 1
            continue
        nearest = min(blobs, key=lambda q: math.hypot(q["u"] - CX_FOR_MAP, q["v"] - CY_FOR_MAP))
        estimate = predict_world_xy(camera_map, ee, nearest["u"], nearest["v"], nearest["depth_m"])
        if not _within(estimate, WORKSPACE_FOR_TARGETS) \
                or np.linalg.norm(estimate - current) > MAX_LOOK_JUMP_M:
            rejected += 1
            continue
        current, blob = estimate, nearest
        accepted += 1
        if accepted >= LOOKS_WANTED:
            break
    if accepted == 0:
        return {"found": False, "reason": f"{color}: no close look was believable ({rejected} rejected)"}

    metres_per_px = float(blob["depth_m"]) / FX_FOR_MAP
    # extent_px is 2 sigma of the blob; a filled rectangle's side is sqrt(12) sigma.
    long_m, short_m = (math.sqrt(3.0) * float(e) * metres_per_px for e in blob["extent_px"])
    return {"found": True, "xy": [float(current[0]), float(current[1])],
            "long_axis_deg": image_axis_to_world_deg(camera_map, blob["angle_deg"]),
            "long_m": long_m, "short_m": short_m, "body_long_m": long_m, "body_short_m": short_m,
            "fit_rms_mm": 0.0, "far_xy": [float(far[0]), float(far[1])],
            "accepted_looks": accepted, "rejected_looks": rejected, "method": "camera",
            "npix": blob["npix"], "hinted": hinted}


# The fingers' closing line is not the commanded wrist axis: probing a taped block's
# chord midpoints and +x edges with the wrist at 0, 75 and 90 deg (y3, 2026-11-12)
# measured a constant hand-frame offset. The camera map's b absorbed its yaw-0
# projection — the whole world frame is "commanded at yaw 0" — so a rotated wrist
# needs only the rotation residual [R(a) - R(0)] d: zero at axis 0 (the 37/37
# regime), ~5.2 mm at 90 deg, over half the grasp's 9 mm per-side clearance and the
# systematic share of the campaign's 0/6 cliff. The hand-frame decomposition is a
# weighted fit (both measured yaws rotate the same way, 15 deg apart, so d's
# direction is soft), but the residual against every direct measurement is
# under 0.7 mm across the 0-90 arc, which is what a grasp consumes.
FINGER_OFFSET_TOOL_M = (0.00253, -0.00261)   # (along pads, along finger separation)


def grasp_axis_correction(axis_deg: float) -> tuple:
    """World xy to subtract from a rotated-wrist command so the fingers land on target."""
    a = math.radians(normalise_axis_deg(axis_deg))
    dx, dy = FINGER_OFFSET_TOOL_M
    return (dx * (math.cos(a) - 1.0) + dy * math.sin(a),
            dx * math.sin(a) - dy * (math.cos(a) - 1.0))


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

    The force zero is averaged while already descending, after the estimate has settled:
    measured on 2026-11-09 it takes about 0.3 s to shift by up to 5 N once the controller
    engages, so a zero taken earlier leaves the trigger riding close to its threshold.
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
    guard_z = start_z - 0.018
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
    contact_z, settle, unload_ticks, above = None, 0, 0, 0
    baseline: list[float] = []
    # The hand's recent heights: contact is their descent rate collapsing while the
    # command keeps sinking. (This replaced lag-above-baseline, which replaced the
    # absolute lag, each version's predecessor broken by droop the next one wasn't:
    # u2/r9b's constant droop, then f5/vgate's configuration drift at low x.)
    window = max(2, int(CONTACT_VELOCITY_WINDOW_S / dt))
    recent: list[float] = []
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
                if settle < BASELINE_END_TICKS:
                    settle += 1
                    if settle > BASELINE_START_TICKS:
                        baseline.append(fz)
                    continue
                if baseline:
                    fz0, baseline = float(np.mean(baseline)), []
                # A release is irreversible, so one noisy sample must not cause it. Contact
                # is force OR geometry: u1 (2026-11-10) pressed a block to the command floor
                # at (0.37, -0.20) without the force estimate ever reaching 4 N — the
                # estimate under-reads in exactly the configurations where the gravity
                # model drifts, so geometry must not lean on a drifting level either. The
                # hand still descending (however far above its command it rides) is not
                # contact; the hand no longer descending while its command sinks is.
                recent.append(actual)
                if len(recent) > window:
                    recent.pop(0)
                descending = (recent[0] - actual) / (dt * (len(recent) - 1)) \
                    if len(recent) == window else speed
                stalled = descending < CONTACT_VELOCITY_FRACTION * speed
                above = above + 1 if stalled or abs(fz - fz0) > contact_force else 0
                if tz < guard_z and above >= CONTACT_SUSTAIN_TICKS:
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
    locate_by: str = "camera",
    camera: Optional[str] = None,
    coarse_step: float = 0.040,
    search_radius: float = 0.135,
    max_probes: int = 220,
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
    last: dict = {}

    def sense(x, y):
        result = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH, z_floor=PROBE_Z_FLOOR,
                             force_thresh=PROBE_FORCE_OFF_N, approach_z=hover["z"],
                             retreat_z=hover["z"], _context=_context)
        last.clear()
        last.update({k: result.get(k) for k in ("trip", "lag_mm", "Fz", "dFz", "contacted")})
        # `contacted` compares against the same unreliable force zero, so it is logged and
        # not believed: it called 7 of 30 good table readings non-contact.
        if result.get("trip") == "timeout":
            return None
        return result.get("contact_z")

    touch = _Touch(sense, max_probes=int(max_probes))
    touch.describe = lambda: dict(last)
    out: dict = {"_source": "live", "task_verified_success": False, "stage": "start",
                 "base_color": base_color, "pick_color": pick_color}

    # The scene memory outlives this attempt: what is learned here seeds the next run.
    memory = load_scene_memory(_store)

    def commit_pose(color, pose):
        """A located block becomes a belief: pose, error bar, and body size if touched."""
        if memory is None:
            return
        by_camera = pose.get("method") == "camera"
        memory.observe(color, sensor="camera+touch" if by_camera else "touch",
                       xy=pose["xy"], top_z=pose.get("top"), yaw_deg=pose["long_axis_deg"],
                       precision_m=0.006 if by_camera else 0.003,
                       data={"fit_rms_mm": pose.get("fit_rms_mm"), "hinted": pose.get("hinted")})
        if not by_camera:
            # Camera sizes come from blob extents and are rough; only touch fits are kept.
            memory.note(color, body_long_m=round(pose["body_long_m"], 4),
                        body_short_m=round(pose["body_short_m"], 4))

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
        if memory is not None:
            memory.close()
        return out

    camera_map = load_camera_map(_store) if locate_by == "camera" else None

    def locate_by_camera(label, color):
        """The camera supplies the pose; touch confirms it from well inside the block."""
        hint = None
        if memory is not None:
            believed = memory.belief(color)
            if believed and believed.get("xy") and not believed["properties"].get("in_gripper"):
                hint = believed["xy"]
        pose = look_twice(color, camera, camera_map, _context, speed_factor, hint_xy=hint)
        if not pose.get("found"):
            return None, f"{label} ({color}): {pose.get('reason')}"
        gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
        touch.top = None
        top = touch.contact(*pose["xy"])
        if top is None or top <= touch.table_ref + touch.min_object:
            return None, (f"{label} ({color}): the camera put it at "
                          f"{[round(v, 4) for v in pose['xy']]} but a probe there found no block")
        pose["top"] = float(top)
        emit("approach", "contact", f"felt_{label}",
             {k: pose.get(k) for k in ("xy", "far_xy", "top", "long_axis_deg", "long_m", "short_m",
                                       "accepted_looks", "rejected_looks", "method", "npix",
                                       "hinted")})
        return pose, None

    def locate(label, color, given):
        if given is None and locate_by == "camera":
            if camera_map is None:
                return None, ("no camera map in the data directory: fit one, or pass "
                              "base_xy and pick_xy to search by touch")
            return locate_by_camera(label, color)
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
        commit_pose(base_color, base)
        touch.excluded.append({"xy": base["xy"], "long_axis_deg": base["long_axis_deg"],
                               "half_long": base["long_m"] / 2,
                               "half_short": base["short_m"] / 2, "margin": 0.006})
        pick, problem = locate("pick", pick_color, pick_xy)
        if pick is None:
            return finish("find_pick", note=problem)
        out["pick"] = _public(pick)
        commit_pose(pick_color, pick)
    except _OutOfProbes as exc:
        return finish("touch_budget", note=str(exc))

    # Camera-located blocks produce no table contacts. Take one, midway between them, only
    # if that spot is well clear of both.
    between = [(base["xy"][0] + pick["xy"][0]) / 2, (base["xy"][1] + pick["xy"][1]) / 2]
    if base.get("method") == "camera" and pick.get("method") == "camera" \
            and min(math.hypot(between[0] - o["xy"][0], between[1] - o["xy"][1])
                    for o in (base, pick)) >= 0.09:
        touch.top = None
        try:
            touch.contact(*between)
        except _OutOfProbes:
            pass
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
    # Where to command the hand so the *fingers* land on the target at each yaw.
    pcx, pcy = grasp_axis_correction(pick["long_axis_deg"])
    bcx, bcy = grasp_axis_correction(base["long_axis_deg"])
    gpx, gpy = px - pcx, py - pcy
    gbx, gby = bx - bcx, by - bcy
    open_gripper(_context=_context)
    move_to_pose([gpx, gpy, 0.12], orientation=pick_quat, speed_factor=speed_factor,
                 _context=_context)
    grasp = grasp_at(grasp_z=table_z + GRASP_HEIGHT_M,
                     width=float(np.clip(pick["body_short_m"], 0.020, 0.045)),
                     open_width=open_width, descent_speed=0.02, _context=_context)
    out["grasp"] = {k: grasp.get(k) for k in ("grasped", "actual_z", "actual_width",
                                              "grip_high_mm", "note")}
    emit("grasp", "closure", "touch_grasp", out["grasp"])
    if not grasp.get("grasped"):
        if memory is not None:
            # A failed grasp is an uncontrolled shove, not a nudge: f1 (2026-11-11) moved
            # a block 45 mm. The position is retracted, and "resting_on" goes with it.
            memory.act("grasp_failed", obj=pick_color, xy=pick["xy"])
            memory.observe(pick_color, sensor="inference", xy=pick["xy"],
                           precision_m=FAILED_GRASP_RETRACT_M,
                           data={"retracted": "grasp_failed"})
            memory.note(pick_color, resting_on=None)
        open_gripper(_context=_context)
        move_to_pose([gpx, gpy, lift_z], speed_factor=speed_factor, _context=_context)
        return finish("grasp", note=grasp.get("note") or "grasp failed")
    if memory is not None:
        memory.act("grasp", obj=pick_color, xy=pick["xy"])
        memory.note(pick_color, in_gripper=True)

    # How far the block's underside hangs below the fingertips, from where the jaws actually
    # closed rather than where they were told to.
    hold_offset = float(grasp.get("actual_z") or table_z + GRASP_HEIGHT_M) - table_z
    expected_contact = base["top"] + hold_offset
    start_z = expected_contact + 0.035
    out["expected_contact_z"] = round(expected_contact, 4)

    move_to_pose([gpx, gpy, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                 _context=_context)
    emit("lift", "motion", "touch_lift", {"lift_z": lift_z})
    move_to_pose([gbx, gby, lift_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    move_to_pose([gbx, gby, start_z], orientation=place_quat, speed_factor=speed_factor,
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
        move_to_pose([gbx, gby, lift_z], orientation=place_quat, speed_factor=speed_factor,
                     _context=_context)
        move_to_pose([gpx, gpy, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        move_to_pose([gpx, gpy, back_start], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        returned = _lower_until_contact(panda, start_z=back_start,
                                        floor_z=table_z + hold_offset - 0.030,
                                        contact_force=contact_force)
        out["returned"] = returned
        if returned["contact"]:
            gripper.move(width=min(0.08, held_width + 0.012), speed=0.02)
        move_to_pose([gpx, gpy, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        out["holding"] = not returned["contact"]
        if memory is not None:
            memory.act("return", obj=pick_color, xy=[px, py])
            if returned["contact"]:
                memory.note(pick_color, in_gripper=None)
                memory.observe(pick_color, sensor="placement", xy=[px, py],
                               precision_m=0.004, data={"returned": True})
            else:
                memory.note(pick_color, in_gripper=True)
        return finish("place_no_contact", note="no contact at the pile; block taken back")

    # A narrow, slow opening: the fingers still straddle only the block just placed.
    gripper.move(width=min(0.08, held_width + 0.012), speed=0.02)
    move_to_pose([gbx, gby, lift_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    open_gripper(_context=_context)
    if memory is not None:
        memory.act("place", obj=pick_color, xy=[bx, by])
        memory.note(pick_color, in_gripper=None)
        memory.observe(pick_color, sensor="placement", xy=[bx, by],
                       yaw_deg=base["long_axis_deg"], precision_m=0.004)

    # The reading that settles it, and the only one that can: how tall is the pile now?
    # A wrist image cannot resolve height (#134).
    expected_top = base["top"] + pick_height
    hover["z"] = max(float(hover_z), expected_top + 0.03)
    gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
    a = math.radians(base["long_axis_deg"])
    readings, success = [], False
    touch.excluded.clear()
    touch.cache.clear()       # the scene has changed; an earlier reading here means nothing now
    touch.max_probes = len(touch.records) + 9
    touch.top = None
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
    if memory is not None:
        if success:
            heard = next(z for z in reversed(readings) if z is not None)
            memory.observe(pick_color, sensor="touch", xy=[bx, by], top_z=float(heard),
                           precision_m=0.004, data={"verified": True})
            memory.note(pick_color, resting_on=base_color)
        else:
            # The block left the gripper near here, but the pile did not verify: a vague
            # memory is the honest one. The deleted resting_on says "whereabouts unsure".
            memory.observe(pick_color, sensor="inference", xy=[bx, by], precision_m=0.030,
                           data={"verified": False})
            memory.note(pick_color, resting_on=None)
    return finish("stacked" if success else "not_stacked", success=success,
                  note=None if success else "pile is not one block taller")


# Reset destinations keep the next run's close look inside EE_LIMITS: the close-look
# viewpoint sits ~6 cm beyond the target in x, and r7 showed a block at x=0.53 already
# pushes it to the boundary.
RESET_X = (0.36, 0.52)
RESET_Y = (-0.24, 0.24)
RESET_CLEARANCE_M = 0.10         # from any believed object, plus that belief's error bar


def touch_unstack(
    *,
    pick_color: str = "blue",
    base_color: str = "green",
    dest_xy: Optional[list] = None,
    dest_yaw_deg: Optional[float] = None,
    hover_z: float = HOVER_Z,
    lift_z: float = LIFT_Z,
    contact_force: float = 4.0,
    max_probes: int = 40,
    speed_factor: float = 0.06,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
    _log_decision: Optional[Any] = None,
    _record_event: Optional[Any] = None,
) -> dict:
    """Take the pick block off the pile and set it on a clear patch of table.

    touch_stack's inverse, and the first routine that acts from memory alone: no camera.
    The remembered pile supplies the grasp, one probe confirms the pile is as remembered
    before anything moves, and the destination is sampled away from every believed object
    and probed as empty table before the block is committed to it. The wrist turns the
    held block to `dest_yaw_deg` on the way — sampled across the whole axis range when
    not given, because the reset is where yaw diversity enters a campaign: placing at
    the pile's own yaw would hand every following grasp the same angle forever.
    `task_verified_success` is True only when a fresh probe finds the block at the
    destination at its remembered height.
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
    last: dict = {}

    def sense(x, y):
        result = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH, z_floor=PROBE_Z_FLOOR,
                             force_thresh=PROBE_FORCE_OFF_N, approach_z=hover["z"],
                             retreat_z=hover["z"], _context=_context)
        last.clear()
        last.update({k: result.get(k) for k in ("trip", "lag_mm", "Fz", "dFz", "contacted")})
        if result.get("trip") == "timeout":
            return None
        return result.get("contact_z")

    touch = _Touch(sense, max_probes=int(max_probes))
    touch.describe = lambda: dict(last)
    out: dict = {"_source": "live", "task_verified_success": False, "stage": "start",
                 "pick_color": pick_color, "base_color": base_color}
    memory = load_scene_memory(_store)

    def finish(stage, *, success=False, note=None, refusal=False):
        out.update({"stage": stage, "task_verified_success": success, "note": note,
                    "n_probes": len(touch.records), "elapsed_s": round(time.time() - started, 1),
                    "probes": touch.records})
        if refusal:
            emit("abstain", "refusal", "touch_unstack_refused", {"stage": stage, "note": note})
        emit("verify", "verdict", "task_verified",
             {"task_verified_success": success, "stage": stage, "note": note,
              "n_probes": len(touch.records), "elapsed_s": out["elapsed_s"]})
        if _log_decision is not None:
            _log_decision("orchestrator", reasoning=(
                f"touch_unstack {pick_color} off {base_color}: {stage}, "
                f"{len(touch.records)} probes" + (f"; {note}" if note else "")))
        if memory is not None:
            memory.close()
        return out

    if memory is None:
        return finish("no_memory", refusal=True,
                      note="touch_unstack acts from memory; none in the data directory")

    pick = memory.belief(pick_color)
    base = memory.belief(base_color)
    if pick is None or pick.get("xy") is None or pick.get("top_z") is None:
        return finish("no_belief", refusal=True,
                      note=f"no remembered position and height for {pick_color}")
    if pick["properties"].get("in_gripper"):
        return finish("in_gripper", refusal=True,
                      note=f"{pick_color} is remembered as still in the gripper")
    if pick["properties"].get("resting_on") != base_color:
        return finish("not_on_pile", refusal=True,
                      note=f"{pick_color} is not remembered as resting on {base_color}")
    if base is None or base.get("top_z") is None:
        return finish("no_base_belief", refusal=True,
                      note=f"no remembered height for {base_color}")

    px, py = (float(v) for v in pick["xy"])
    pile_top = float(pick["top_z"])
    base_top = float(base["top_z"])
    height = pile_top - base_top
    if not MIN_BLOCK_HEIGHT_M <= height <= MAX_BLOCK_HEIGHT_M:
        return finish("height", refusal=True,
                      note=f"the remembered pile implies a {height * 1000:.0f} mm block")
    yaw = float(pick.get("yaw_deg") or 0.0)
    body_short = float(pick["properties"].get("body_short_m") or 0.032)
    open_width = float(np.clip(body_short + 0.018, 0.040, 0.080))
    out["believed"] = {"xy": [round(px, 4), round(py, 4)], "pile_top": round(pile_top, 4),
                       "yaw_deg": round(yaw, 1), "age_s": pick.get("age_s"),
                       "disturbances": len(pick.get("disturbances") or [])}

    # One probe settles whether the world still matches the memory before anything moves.
    hover["z"] = max(float(hover_z), pile_top + 0.03)
    gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
    heard = touch.contact(px, py)
    if heard is None or abs(heard - pile_top) > 0.008:
        found = "nothing" if heard is None else f"a surface at {heard:.4f}"
        return finish("pile_changed", refusal=True,
                      note=f"memory put the pile top at {pile_top:.4f}; the probe found {found}")
    out["pile_check_z"] = round(float(heard), 4)

    # The destination: given, or sampled away from everything the memory believes in, then
    # probed. Only a spot that answers "table" takes the block.
    beliefs = memory.beliefs()
    rng = np.random.default_rng()

    def too_close(x, y):
        for b in beliefs:
            if b["name"] == pick_color or b.get("xy") is None:
                continue
            margin = RESET_CLEARANCE_M + float(b.get("precision_m") or 0.0)
            if math.hypot(x - b["xy"][0], y - b["xy"][1]) < margin:
                return b["name"]
        if math.hypot(x - px, y - py) < RESET_CLEARANCE_M:
            return "the pile"
        return None

    candidates = [list(dest_xy)] if dest_xy is not None else [
        [float(rng.uniform(*RESET_X)), float(rng.uniform(*RESET_Y))] for _ in range(40)]
    dest, dest_table, tried = None, None, []
    for x, y in candidates:
        blocker = too_close(x, y)
        if blocker is not None:
            if dest_xy is not None:
                return finish("dest_blocked", refusal=True,
                              note=f"the destination is within clearance of {blocker}")
            continue
        z = touch.contact(x, y)
        tried.append({"xy": [round(x, 4), round(y, 4)],
                      "contact_z": None if z is None else round(float(z), 4)})
        # Stricter than the 10 mm object margin: u1's probe stalled 6 mm proud of the
        # table at a spot the arm then could not press into. A destination must answer
        # "table" to within 5 mm, or the block goes somewhere that does.
        if z is not None and z <= touch.table_ref + 0.005:
            dest, dest_table = [x, y], float(z)
            break
        if len(tried) >= 4:
            break
    out["dest_tried"] = tried
    if dest is None:
        return finish("no_destination", refusal=True,
                      note="no sampled destination probed as clear table")
    dx, dy = dest
    out["dest_xy"] = [round(dx, 4), round(dy, 4)]

    dest_yaw = normalise_axis_deg(float(dest_yaw_deg)) if dest_yaw_deg is not None \
        else float(rng.uniform(-90.0, 90.0))
    out["dest_yaw_deg"] = round(dest_yaw, 1)
    pick_quat, place_quat = wrist_quaternion(yaw), wrist_quaternion(dest_yaw)
    # Command the hand where the fingers need to be at each yaw (y3, 2026-11-12).
    ucx, ucy = grasp_axis_correction(yaw)
    dcx, dcy = grasp_axis_correction(dest_yaw)
    gpx, gpy = px - ucx, py - ucy
    gdx, gdy = dx - dcx, dy - dcy
    open_gripper(_context=_context)
    move_to_pose([gpx, gpy, max(0.13, pile_top + 0.05)], orientation=pick_quat,
                 speed_factor=speed_factor, _context=_context)
    grasp = grasp_at(grasp_z=base_top + GRASP_HEIGHT_M,
                     width=float(np.clip(body_short, 0.020, 0.045)),
                     open_width=open_width, descent_speed=0.02, _context=_context)
    out["grasp"] = {k: grasp.get(k) for k in ("grasped", "actual_z", "actual_width",
                                              "grip_high_mm", "note")}
    emit("grasp", "closure", "unstack_grasp", out["grasp"])
    if not grasp.get("grasped"):
        # The same retraction as touch_stack's: the fingers were just in contact with a
        # block they failed to hold, so where it is now is not where it was remembered.
        memory.act("grasp_failed", obj=pick_color, xy=[px, py])
        memory.observe(pick_color, sensor="inference", xy=[px, py],
                       precision_m=FAILED_GRASP_RETRACT_M,
                       data={"retracted": "grasp_failed"})
        memory.note(pick_color, resting_on=None)
        open_gripper(_context=_context)
        move_to_pose([gpx, gpy, lift_z], speed_factor=speed_factor, _context=_context)
        return finish("grasp", note=grasp.get("note") or "grasp failed")
    memory.act("grasp", obj=pick_color, xy=[px, py])
    memory.note(pick_color, in_gripper=True)

    hold_offset = float(grasp.get("actual_z") or base_top + GRASP_HEIGHT_M) - base_top
    expected_contact = dest_table + hold_offset
    out["expected_contact_z"] = round(expected_contact, 4)

    move_to_pose([gpx, gpy, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                 _context=_context)
    move_to_pose([gdx, gdy, lift_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    move_to_pose([gdx, gdy, expected_contact + 0.035], orientation=place_quat,
                 speed_factor=speed_factor, _context=_context)
    placed = _lower_until_contact(panda, start_z=expected_contact + 0.035,
                                  floor_z=expected_contact - 0.030,
                                  contact_force=contact_force)
    out["place"] = placed
    if placed["contact"]:
        out["place"]["z_error_mm"] = round((placed["contact_z"] - expected_contact) * 1000, 1)
    emit("verify", "contact", "unstack_place", out["place"])

    held_width = float(gripper.read_once().width)
    if not placed["contact"]:
        # The same rule as touch_stack: never open in free space; take it back to the pile.
        back_start = base_top + hold_offset + 0.035
        move_to_pose([gdx, gdy, lift_z], orientation=place_quat, speed_factor=speed_factor,
                     _context=_context)
        move_to_pose([gpx, gpy, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        move_to_pose([gpx, gpy, back_start], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        returned = _lower_until_contact(panda, start_z=back_start,
                                        floor_z=base_top + hold_offset - 0.030,
                                        contact_force=contact_force)
        out["returned"] = returned
        if returned["contact"]:
            gripper.move(width=min(0.08, held_width + 0.012), speed=0.02)
        move_to_pose([gpx, gpy, lift_z], orientation=pick_quat, speed_factor=speed_factor,
                     _context=_context)
        out["holding"] = not returned["contact"]
        memory.act("return", obj=pick_color, xy=[px, py])
        if returned["contact"]:
            memory.note(pick_color, in_gripper=None)
            memory.observe(pick_color, sensor="placement", xy=[px, py],
                           precision_m=0.004, data={"returned": True})
        else:
            memory.note(pick_color, in_gripper=True)
        return finish("place_no_contact", note="no contact at the destination; block taken back")

    gripper.move(width=min(0.08, held_width + 0.012), speed=0.02)
    move_to_pose([gdx, gdy, lift_z], orientation=place_quat, speed_factor=speed_factor,
                 _context=_context)
    open_gripper(_context=_context)
    memory.act("place", obj=pick_color, xy=[dx, dy])
    memory.note(pick_color, in_gripper=None, resting_on="table")
    memory.observe(pick_color, sensor="placement", xy=[dx, dy], yaw_deg=dest_yaw,
                   precision_m=0.004)

    expected_top = dest_table + height
    hover["z"] = max(float(hover_z), expected_top + 0.03, pile_top + 0.03)
    gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
    touch.cache.clear()       # the scene has changed; the table reading here means nothing now
    touch.top = None
    touch.max_probes = len(touch.records) + 6
    readings, success = [], False
    a = math.radians(dest_yaw)
    try:
        for offset in (0.0, 0.010, -0.010):
            z = touch.contact(dx + offset * math.cos(a), dy + offset * math.sin(a))
            readings.append(z)
            if z is not None and abs(z - expected_top) <= 0.008:
                success = True
                break
    except _OutOfProbes:
        pass
    out["verify"] = {"expected_top": round(expected_top, 4),
                     "readings": [None if z is None else round(float(z), 4) for z in readings]}
    move_to_pose([dx, dy, max(float(lift_z), expected_top + 0.06)],
                 speed_factor=speed_factor, _context=_context)
    open_gripper(_context=_context)
    if success:
        heard = next(z for z in reversed(readings) if z is not None)
        memory.observe(pick_color, sensor="touch", xy=[dx, dy], top_z=float(heard),
                       precision_m=0.004, data={"verified": True})
    else:
        memory.observe(pick_color, sensor="inference", xy=[dx, dy], precision_m=0.030,
                       data={"verified": False})
        memory.note(pick_color, resting_on=None)
    return finish("reset" if success else "not_reset", success=success,
                  note=None if success else "the block did not verify at the destination")


def touch_look(
    *,
    colors: Optional[list] = None,
    camera: Optional[str] = None,
    speed_factor: float = 0.06,
    max_probes: int = 24,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
    _log_decision: Optional[Any] = None,
    _record_event: Optional[Any] = None,
) -> dict:
    """Re-observe the scene: the camera finds each block, one probe confirms its height.

    The investigation action. When an observation disagrees with memory the next step is
    to look, not to stop: f1 (2026-11-11) halted a campaign over a shoved block that one
    overhead capture would have found. Each block seen gets a fresh camera+touch belief,
    and resting_on is re-derived from the probed height: a top within one block of the
    table is "table", a taller top rests on the believed object beneath it. Looking only
    moves the camera, so the scene epoch does not advance.

    `task_verified_success` is True when every requested color is accounted for: either
    seen and height-confirmed, or serving as the support a confirmed block rests on (a
    base block fully hidden under a pile cannot be seen, and does not need to be).
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    if panda is None or gripper is None:
        return {"_source": "mock", "task_verified_success": None, "stage": "mock",
                "note": "mock — no arm/gripper"}

    started = time.time()
    colors = [str(c) for c in colors] if colors else ["blue", "green"]

    def emit(phase, kind, label, data):
        if _record_event is not None:
            _record_event(phase, kind, label=label, data=data)

    hover = {"z": float(HOVER_Z)}
    last: dict = {}

    def sense(x, y):
        result = probe_table(x=x, y=y, open_width=PROBE_OPEN_WIDTH, z_floor=PROBE_Z_FLOOR,
                             force_thresh=PROBE_FORCE_OFF_N, approach_z=hover["z"],
                             retreat_z=hover["z"], _context=_context)
        last.clear()
        last.update({k: result.get(k) for k in ("trip", "lag_mm", "Fz", "dFz", "contacted")})
        if result.get("trip") == "timeout":
            return None
        return result.get("contact_z")

    touch = _Touch(sense, max_probes=int(max_probes))
    touch.describe = lambda: dict(last)
    out: dict = {"_source": "live", "task_verified_success": False, "stage": "start",
                 "colors": list(colors)}
    memory = load_scene_memory(_store)

    def finish(stage, *, success=False, note=None, refusal=False):
        out.update({"stage": stage, "task_verified_success": success, "note": note,
                    "n_probes": len(touch.records), "elapsed_s": round(time.time() - started, 1),
                    "probes": touch.records})
        if refusal:
            emit("abstain", "refusal", "touch_look_refused", {"stage": stage, "note": note})
        emit("verify", "verdict", "task_verified",
             {"task_verified_success": success, "stage": stage, "note": note,
              "n_probes": len(touch.records), "elapsed_s": out["elapsed_s"]})
        if _log_decision is not None:
            _log_decision("orchestrator", reasoning=(
                f"touch_look {', '.join(colors)}: {stage}, "
                f"{len(touch.records)} probes" + (f"; {note}" if note else "")))
        if memory is not None:
            memory.close()
        return out

    if memory is None:
        return finish("no_memory", refusal=True,
                      note="touch_look exists to right the memory; none in the data directory")
    camera_map = load_camera_map(_store)
    if camera_map is None:
        return finish("no_camera_map", refusal=True,
                      note="no camera map in the data directory")

    tops = [b["top_z"] for b in memory.beliefs() if b.get("top_z") is not None]
    hover["z"] = max([float(HOVER_Z)] + [t + 0.03 for t in tops])

    found: dict = {}
    try:
        for color in colors:
            hint = None
            believed = memory.belief(color)
            if believed and believed.get("xy") and not believed["properties"].get("in_gripper"):
                hint = believed["xy"]
            pose = look_twice(color, camera, camera_map, _context, speed_factor, hint_xy=hint)
            if not pose.get("found"):
                found[color] = {"found": False, "reason": pose.get("reason")}
                continue
            gripper.move(width=PROBE_OPEN_WIDTH, speed=0.1)
            touch.top = None
            top = touch.contact(*pose["xy"])
            entry = {"found": True, "xy": [round(float(v), 4) for v in pose["xy"]],
                     "yaw_deg": round(pose["long_axis_deg"], 1), "hinted": pose.get("hinted")}
            if top is None or top <= touch.table_ref + touch.min_object:
                # The camera saw it, the finger found table: keep the sighting, with an
                # error bar wide enough to admit that the two sensors disagree.
                entry["confirmed"] = False
                memory.observe(color, sensor="camera", xy=pose["xy"],
                               yaw_deg=pose["long_axis_deg"], precision_m=0.010,
                               data={"look": True, "confirmed": False})
            else:
                entry.update(confirmed=True, top_z=round(float(top), 4))
                memory.observe(color, sensor="camera+touch", xy=pose["xy"],
                               top_z=float(top), yaw_deg=pose["long_axis_deg"],
                               precision_m=0.006, data={"look": True})
                memory.note(color, in_gripper=None)
            found[color] = entry
            emit("approach", "contact", "looked_at", {"color": color, **entry})
    except _OutOfProbes as exc:
        out["found"] = found
        return finish("touch_budget", note=str(exc))
    out["found"] = found

    table_z = touch.table_estimate()
    out["table_z"] = round(table_z, 4)

    def support_of(color, entry):
        """What a tall block rests on: the believed object beneath it, if one is clear."""
        for b in memory.beliefs():
            if b["name"] == color or b.get("xy") is None:
                continue
            if math.hypot(entry["xy"][0] - b["xy"][0],
                          entry["xy"][1] - b["xy"][1]) > MIN_SEPARATION_M:
                continue
            other = found.get(b["name"]) or {}
            if other.get("confirmed") and other.get("top_z", 0.0) >= entry["top_z"] - 0.010:
                continue   # just as tall as this one: side by side, not a support
            return b["name"]
        return None

    for color, entry in found.items():
        if not entry.get("confirmed"):
            continue
        if entry["top_z"] - table_z <= MAX_BLOCK_HEIGHT_M:
            rest = "table"
        else:
            rest = support_of(color, entry)
        memory.note(color, resting_on=rest)
        entry["resting_on"] = rest

    accounted = {c for c, e in found.items() if e.get("confirmed")}
    accounted |= {e["resting_on"] for e in found.values() if e.get("resting_on")}
    success = all(c in accounted for c in colors)
    return finish("looked" if success else "look_incomplete", success=success,
                  note=None if success else "not every block could be seen or accounted for")


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
                "locate_by": {"type": "string", "enum": ["camera", "touch"], "default": "camera",
                              "description": "camera for loose blocks; touch only for fixed "
                                             "targets, since edge probing turns light blocks"},
                "camera": {"type": "string"},
                "coarse_step": {"type": "number", "minimum": 0.015, "maximum": 0.048,
                                "default": 0.040},
                "search_radius": {"type": "number", "minimum": 0.02, "maximum": 0.20,
                                  "default": 0.135},
                "max_probes": {"type": "integer", "minimum": 10, "maximum": 300, "default": 220},
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
    {
        "name": "touch_unstack",
        "function_spec": "hebbot.skills.touch_stack:touch_unstack",
        "expectations": {"checks": [
            {"name": "reset", "field": "task_verified_success", "op": "eq", "value": True},
        ]},
        "params_schema": {
            "type": "object",
            "properties": {
                "pick_color": {"type": "string", "enum": ["blue", "green", "red"],
                               "default": "blue"},
                "base_color": {"type": "string", "enum": ["blue", "green", "red"],
                               "default": "green"},
                "dest_xy": {"type": "array", "items": {"type": "number"},
                            "minItems": 2, "maxItems": 2,
                            "description": "where to put the block; omit to sample a clear spot"},
                "dest_yaw_deg": {"type": "number", "minimum": -180, "maximum": 180,
                                 "description": "long-axis angle to set the block down at; "
                                                "omit to sample the whole axis range"},
                "hover_z": {"type": "number", "minimum": 0.07, "maximum": 0.16, "default": 0.09},
                "lift_z": {"type": "number", "minimum": 0.15, "maximum": 0.30, "default": 0.22},
                "contact_force": {"type": "number", "minimum": 3.0, "maximum": 10.0,
                                  "default": 4.0},
                "max_probes": {"type": "integer", "minimum": 6, "maximum": 80, "default": 40},
                "speed_factor": {"type": "number", "minimum": 0.01, "maximum": 0.2,
                                 "default": 0.06},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "touch_look",
        "function_spec": "hebbot.skills.touch_stack:touch_look",
        "expectations": {"checks": [
            {"name": "looked", "field": "task_verified_success", "op": "eq", "value": True},
        ]},
        "params_schema": {
            "type": "object",
            "properties": {
                "colors": {"type": "array",
                           "items": {"type": "string", "enum": ["blue", "green", "red"]},
                           "description": "blocks to re-observe; omit for blue and green"},
                "camera": {"type": "string"},
                "max_probes": {"type": "integer", "minimum": 4, "maximum": 60, "default": 24},
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
