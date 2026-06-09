"""Bootstrap primitives — the hand-written `classical`-body-type skills.

Per foundations.md commitment #10, primitives are the only thing that ships
as code; everything else is data records the agent creates at runtime.

Each primitive:
  - Accepts a `_context` kwarg if it needs hardware handles.
  - Falls back to mock behavior when the relevant handle isn't supplied.
  - Returns a plain dict so the substrate can serialize it as an outcome.

Context keys this module recognizes:
  - "panda":   a panda_py.Panda instance for arm control / state reads
  - "gripper": a panda_py.libfranka.Gripper instance (the gripper, not the arm)
"""

from __future__ import annotations

import time
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Mock-mode defaults that match panda-py's get_state() shape closely enough
# to exercise downstream code without a robot.
# ---------------------------------------------------------------------------

_MOCK_STATE = {
    "joints": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785],
    "ee_pose": [
        1.0, 0.0, 0.0, 0.3,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.5,
        0.0, 0.0, 0.0, 1.0,
    ],
    "force": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "gripper_width": 0.04,
    "_source": "mock",
}

# Reasonable defaults for the Franka working envelope
_DEFAULT_SPEED_FACTOR = 0.1
_MIN_SPEED_FACTOR = 0.01
_MAX_SPEED_FACTOR = 0.5

# Gripper hard limits (Franka Hand)
_GRIPPER_MAX_WIDTH = 0.08
_GRIPPER_MIN_WIDTH = 0.0
_DEFAULT_GRIPPER_SPEED = 0.1
_DEFAULT_GRIP_FORCE = 20.0  # N


def _extract(context: Optional[Any], attr: str) -> Optional[Any]:
    """Pull `attr` out of `context` if it's a dict or has the attribute."""
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get(attr)
    return getattr(context, attr, None)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# State read
# ---------------------------------------------------------------------------

def get_state(_context: Optional[Any] = None) -> dict:
    """Read the current robot state.

    Returns: {joints, ee_pose, force, gripper_width, _source}
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")

    if panda is None:
        return dict(_MOCK_STATE)

    import numpy as np
    state = panda.get_state()
    ee = np.array(state.O_T_EE).reshape(4, 4).T.flatten().tolist()
    width = None
    if gripper is not None:
        try:
            width = float(gripper.read_once().width)
        except Exception:
            width = None
    return {
        "joints": [float(q) for q in state.q],
        "ee_pose": ee,
        "force": [
            float(state.O_F_ext_hat_K[i]) if i < 6 else 0.0
            for i in range(6)
        ],
        "gripper_width": width,
        "_source": "live",
    }


# ---------------------------------------------------------------------------
# Joint-space move
# ---------------------------------------------------------------------------

def move_to_joints(
    q: list[float],
    *,
    speed_factor: float = _DEFAULT_SPEED_FACTOR,
    _context: Optional[Any] = None,
) -> dict:
    """Move the arm to the specified 7-DOF joint configuration.

    Args:
        q: target joint angles (7 floats, radians)
        speed_factor: 0.01-0.5, default 0.1 (slow + safe)
    """
    if len(q) != 7:
        raise ValueError(f"q must have 7 elements, got {len(q)}")
    speed_factor = _clamp(float(speed_factor), _MIN_SPEED_FACTOR, _MAX_SPEED_FACTOR)
    q = [float(v) for v in q]

    panda = _extract(_context, "panda")
    if panda is None:
        time.sleep(0.05)
        return {
            "_source": "mock",
            "target_q": q,
            "speed_factor": speed_factor,
            "note": "mock — no actual motion",
        }

    panda.move_to_joint_position(q, speed_factor=speed_factor)
    state = panda.get_state()
    return {
        "_source": "live",
        "target_q": q,
        "final_q": [float(qi) for qi in state.q],
        "max_error": max(abs(a - b) for a, b in zip(q, state.q)),
        "speed_factor": speed_factor,
    }


# ---------------------------------------------------------------------------
# Cartesian-space move (via IK)
# ---------------------------------------------------------------------------

def move_to_pose(
    position: list[float],
    orientation: Optional[list[float]] = None,
    *,
    speed_factor: float = _DEFAULT_SPEED_FACTOR,
    _context: Optional[Any] = None,
) -> dict:
    """Move the end-effector to a Cartesian pose (uses ik_full + joint move).

    Args:
        position: [x, y, z] in meters, robot base frame
        orientation: quaternion [w, x, y, z]; defaults to gripper pointing
                     straight down ([0, 1, 0, 0])
        speed_factor: 0.01-0.5
    """
    if len(position) != 3:
        raise ValueError(f"position must have 3 elements, got {len(position)}")
    if orientation is None:
        orientation = [0.0, 1.0, 0.0, 0.0]  # gripper points -Z (down) in EE frame
    if len(orientation) != 4:
        raise ValueError(f"orientation must have 4 elements (w,x,y,z), got {len(orientation)}")
    speed_factor = _clamp(float(speed_factor), _MIN_SPEED_FACTOR, _MAX_SPEED_FACTOR)

    panda = _extract(_context, "panda")
    if panda is None:
        time.sleep(0.05)
        return {
            "_source": "mock",
            "target_position": list(position),
            "target_orientation": list(orientation),
            "speed_factor": speed_factor,
            "note": "mock — no actual motion",
        }

    import numpy as np
    import panda_py

    current_q = panda.get_state().q
    pos = np.array(position, dtype=float)
    # API convention is scalar-first [w, x, y, z]; panda_py / Eigen expect
    # scalar-last [x, y, z, w]. Reorder before handing it to ik_full.
    w, x, y, z = (float(v) for v in orientation)
    quat = np.array([x, y, z, w], dtype=float)

    # ik_full returns up to 4 branches; pick the one closest to current joints
    # (matches commitment #11's preference for relative-state continuity)
    best_q = None
    best_dist = float("inf")
    for q7 in [current_q[6], current_q[6] + 0.5, current_q[6] - 0.5, 0.785, -0.785, 0.0]:
        try:
            sols = panda_py.ik_full(pos, quat, np.array(current_q), float(q7))
        except Exception:
            continue
        if sols is None:
            continue
        sols = np.array(sols)
        if sols.ndim == 1:
            sols = sols.reshape(1, -1)
        for sol in sols:
            sol = np.array(sol).flatten()
            if sol.shape != (7,) or np.any(np.isnan(sol)):
                continue
            dist = float(np.linalg.norm(sol - np.array(current_q)))
            if dist < best_dist:
                best_dist = dist
                best_q = sol

    if best_q is None:
        raise RuntimeError(f"no IK solution for position={position} orientation={orientation}")

    panda.move_to_joint_position(best_q.tolist(), speed_factor=speed_factor)
    final_state = panda.get_state()
    final_ee = np.array(final_state.O_T_EE).reshape(4, 4).T
    final_pos = final_ee[:3, 3].tolist()
    return {
        "_source": "live",
        "target_position": list(position),
        "target_orientation": list(orientation),
        "ik_solution_q": [float(v) for v in best_q],
        "final_position": final_pos,
        "position_error": float(np.linalg.norm(np.array(final_pos) - pos)),
        "speed_factor": speed_factor,
    }


# ---------------------------------------------------------------------------
# Teleop (Xbox controller -> Cartesian impedance jog)
# ---------------------------------------------------------------------------

# Conservative workspace box (robot base frame, metres). Clamps the jog target
# so the gripper can't be driven outside a safe envelope.
_TELEOP_BOX = {"x": (0.20, 0.65), "y": (-0.40, 0.40), "z": (-0.02, 0.60)}


def _quat_to_mat(q):
    """Quaternion [x, y, z, w] -> 3x3 rotation matrix (numpy)."""
    import numpy as np
    x, y, z, w = q
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w),     2*(x*z + y*w)],
        [2*(x*y + z*w),     1 - 2*(x*x + z*z), 2*(y*z - x*w)],
        [2*(x*z - y*w),     2*(y*z + x*w),     1 - 2*(x*x + y*y)],
    ])


def _mat_to_quat(R):
    """3x3 rotation matrix -> quaternion [x, y, z, w] (numpy)."""
    import numpy as np
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        S = np.sqrt(tr + 1.0) * 2
        w = 0.25 * S
        x = (R[2, 1] - R[1, 2]) / S
        y = (R[0, 2] - R[2, 0]) / S
        z = (R[1, 0] - R[0, 1]) / S
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        S = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / S; x = 0.25 * S
        y = (R[0, 1] + R[1, 0]) / S; z = (R[0, 2] + R[2, 0]) / S
    elif R[1, 1] > R[2, 2]:
        S = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / S; x = (R[0, 1] + R[1, 0]) / S
        y = 0.25 * S; z = (R[1, 2] + R[2, 1]) / S
    else:
        S = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / S; x = (R[0, 2] + R[2, 0]) / S
        y = (R[1, 2] + R[2, 1]) / S; z = 0.25 * S
    return np.array([x, y, z, w])


def _level_gripper_down(q_xyzw):
    """Force a truly-vertical gripper-down orientation, preserving yaw.

    Takes the current orientation and rebuilds it with the tool z-axis pointing
    straight down (0,0,-1) and the tool x-axis = the horizontal projection of the
    current x-axis. Removes any few-degree tilt that would bias a touch-off.
    """
    import numpy as np
    R = _quat_to_mat(q_xyzw)
    x_axis = R[:, 0].copy()
    x_axis[2] = 0.0  # project onto the table plane
    n = np.linalg.norm(x_axis)
    x_h = (x_axis / n) if n > 1e-6 else np.array([1.0, 0.0, 0.0])
    z_new = np.array([0.0, 0.0, -1.0])
    y_new = np.cross(z_new, x_h)
    R_new = np.column_stack([x_h, y_new, z_new])
    return _mat_to_quat(R_new)


def _find_gamepad():
    """Return an evdev InputDevice for the first gamepad, or None."""
    import evdev
    from evdev import ecodes
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        caps = dev.capabilities()
        keys = caps.get(ecodes.EV_KEY, [])
        # A gamepad reports an A/South button and absolute axes.
        if ecodes.EV_ABS in caps and (ecodes.BTN_A in keys or ecodes.BTN_SOUTH in keys):
            return dev
        dev.close()
    return None


def teleop_xbox(
    speed: float = 0.06,
    max_runtime: float = 60.0,
    frequency: float = 100.0,
    *,
    _context: Optional[Any] = None,
) -> dict:
    """Jog the end-effector with an Xbox controller via Cartesian impedance.

    Compliant control: driving the gripper into a surface stops at contact with
    bounded force (no hard collision reflex), so this doubles as a safe touch-off
    — jog down until the fingertips touch, release, read the returned z.

    Controls (gripper-down orientation held fixed):
      - Left stick      : X/Y in the table plane (up = +x forward)
      - Right stick up/dn: Z (raise / lower)
      - A button        : close gripper   B button: open gripper
      - START button    : finish and return

    Args:
        speed: max jog velocity at full stick deflection (m/s).
        max_runtime: hard cap on the session (s); returns when it elapses.
        frequency: control-loop rate (Hz).

    Returns:
        {final_position, final_z, min_z_reached, duration_s, exited_via, _source}
        min_z_reached is the lowest the EE got — the touch-off reading.
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    if panda is None:
        time.sleep(0.02)
        return {"_source": "mock", "note": "mock — no panda in context",
                "final_position": None, "min_z_reached": None}

    import numpy as np
    import threading
    from evdev import ecodes
    from panda_py import controllers

    dev = _find_gamepad()
    if dev is None:
        raise RuntimeError("no gamepad found in /dev/input (is the controller plugged in?)")

    speed = max(0.005, min(0.2, float(speed)))
    dt = 1.0 / float(frequency)
    deadzone = 0.15

    def norm(v, lo=-32768.0, hi=32767.0):
        n = (2.0 * (v - lo) / (hi - lo)) - 1.0  # -> [-1, 1]
        return 0.0 if abs(n) < deadzone else n

    axes = {ecodes.ABS_X: 0.0, ecodes.ABS_Y: 0.0, ecodes.ABS_RY: 0.0}
    state = {"exit": False, "grip": None}  # grip: "open"/"close" request

    def handle_gripper():
        # Run blocking gripper moves off the RT loop so they don't stall control.
        if gripper is None:
            return
        req, state["grip"] = state["grip"], None
        if req == "close":
            try: gripper.grasp(width=0.0, speed=0.1, force=20.0,
                               epsilon_inner=0.05, epsilon_outer=0.05)
            except Exception: pass
        elif req == "open":
            try: gripper.move(width=0.08, speed=0.1)
            except Exception: pass

    target = panda.get_position().copy()
    # Hold a TRUE gripper-down orientation (z-axis straight down), preserving the
    # start yaw. Holding the raw start orientation baked in a few-degree tilt that
    # biased touch-offs by a few mm at the fingertip.
    quat = _level_gripper_down(panda.get_orientation())  # scalar-last [x,y,z,w]
    q0 = np.array(panda.get_state().q)  # bias the nullspace toward the start config
    min_z = float(target[2])
    min_z_q = q0.copy()  # joint config at the lowest z reached (descent diagnostic)

    ctrl = controllers.CartesianImpedance()
    # Firm orientation, compliant translation. The default impedance let the
    # gripper drift ~25deg and the elbow wander into contorted configs over a
    # session; high rotational stiffness (entries 3-5) locks gripper-down while
    # moderate translational stiffness keeps contact (touch-off) soft. Nullspace
    # softened 20->10: at 20 the bias toward the (high) start config resisted
    # descending to a low touch-off, stalling the down-jog near the table.
    ctrl.set_impedance(np.diag([200.0, 200.0, 200.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(10.0)
    panda.start_controller(ctrl)
    start = time.time()
    exited_via = "timeout"
    try:
        with panda.create_context(frequency=frequency, max_runtime=max_runtime) as ctx:
            while ctx.ok():
                # Drain pending controller events (non-blocking).
                while True:
                    ev = dev.read_one()
                    if ev is None:
                        break
                    if ev.type == ecodes.EV_ABS and ev.code in axes:
                        axes[ev.code] = norm(ev.value)
                    elif ev.type == ecodes.EV_KEY and ev.value == 1:
                        if ev.code in (ecodes.BTN_START,):
                            state["exit"] = True
                        elif ev.code in (ecodes.BTN_A, ecodes.BTN_SOUTH):
                            state["grip"] = "close"
                        elif ev.code in (ecodes.BTN_B, ecodes.BTN_EAST):
                            state["grip"] = "open"
                if state["exit"]:
                    exited_via = "button"
                    break
                if state["grip"]:
                    threading.Thread(target=handle_gripper, daemon=True).start()

                # Stick -> velocity. Up on a stick reads negative, so negate.
                dx = -axes[ecodes.ABS_Y] * speed * dt   # forward/back
                dy = -axes[ecodes.ABS_X] * speed * dt    # left/right
                dz = -axes[ecodes.ABS_RY] * speed * dt   # up/down
                target[0] = float(np.clip(target[0] + dx, *_TELEOP_BOX["x"]))
                target[1] = float(np.clip(target[1] + dy, *_TELEOP_BOX["y"]))
                target[2] = float(np.clip(target[2] + dz, *_TELEOP_BOX["z"]))
                ctrl.set_control(target, quat, q0)
                z_now = float(panda.get_position()[2])
                if z_now < min_z:
                    min_z = z_now
                    min_z_q = np.array(panda.get_state().q)
    finally:
        panda.stop_controller()
        dev.close()

    final = panda.get_position()
    return {
        "_source": "live",
        "final_position": [float(v) for v in final],
        "final_z": float(final[2]),
        "min_z_reached": float(min_z),
        "joints_at_min_z": [float(v) for v in min_z_q],
        "duration_s": round(time.time() - start, 2),
        "exited_via": exited_via,
    }


# ---------------------------------------------------------------------------
# Camera read
# ---------------------------------------------------------------------------

def _select_camera(cameras: dict, name: Optional[str]):
    """Pick a camera from the context's camera map.

    - explicit name -> that camera (or None if absent)
    - no name + exactly one camera -> that camera
    - no name + several cameras -> error (ambiguous)
    """
    if not cameras:
        return None
    if name is not None:
        return cameras.get(name)
    if len(cameras) == 1:
        return next(iter(cameras.values()))
    raise ValueError(
        f"multiple cameras available ({sorted(cameras)}); specify camera=<name>"
    )


def read_camera(
    camera: Optional[str] = None,
    with_depth: bool = True,
    save: bool = True,
    *,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
) -> dict:
    """Capture a frame from a camera and (optionally) record it to the episode.

    Reads aligned color (+ depth) from a camera handle supplied in the context
    under the "cameras" key. When `save` is set and the substrate provided a
    store + episode id, the color frame (and raw depth) are written under the
    data root's frames/ tree and a row is inserted into the `frames` table,
    linking the image to this episode (foundations commitment #9).

    Args:
        camera: which camera to read ("wrist", "scene", ...). Optional when
                exactly one camera is connected.
        with_depth: also capture + save the aligned depth frame.
        save: persist the frame(s) to disk + the frames table.

    Returns:
        {camera, width, height, color_path, depth_path, center_depth_m, _source}
        where *_path are relative to the data root (None when not saved/mock).
    """
    cameras = _extract(_context, "cameras") or {}
    cam = _select_camera(cameras, camera)

    if cam is None:
        # Mock mode — no camera available. Used for dev/CI without hardware.
        time.sleep(0.02)
        return {
            "_source": "mock",
            "camera": camera or "mock",
            "width": 640,
            "height": 480,
            "color_path": None,
            "depth_path": None,
            "note": "mock — no camera in context",
        }

    import numpy as np

    cap = cam.capture(with_depth=with_depth)
    w, h = cap["width"], cap["height"]
    result: dict[str, Any] = {
        "_source": "live",
        "camera": cam.name,
        "width": w,
        "height": h,
        "depth_scale": cap["depth_scale"],
        "intrinsics": getattr(cam, "intrinsics", None),
        "color_path": None,
        "depth_path": None,
    }
    if with_depth and cap["depth"] is not None:
        center = cap["depth"][h // 2, w // 2] * cap["depth_scale"]
        result["center_depth_m"] = float(center)
        result["depth_coverage"] = float((cap["depth"] > 0).mean())

    if save and _store is not None and _episode_id is not None:
        from pathlib import Path
        from PIL import Image

        data_root = Path(_store.db_path).parent
        d = time.gmtime()
        rel_dir = (
            f"frames/{d.tm_year:04d}/{d.tm_mon:02d}/{d.tm_mday:02d}/{_episode_id}"
        )
        (data_root / rel_dir).mkdir(parents=True, exist_ok=True)

        color_rel = f"{rel_dir}/{cam.name}_000.jpg"
        # capture() returns BGR; flip to RGB so the saved JPEG has true colors.
        Image.fromarray(cap["color"][:, :, ::-1]).save(data_root / color_rel, quality=92)
        _store.record_frame(_episode_id, camera=cam.name, t=0.0,
                            file_path=color_rel, width=w, height=h)
        result["color_path"] = color_rel

        if with_depth and cap["depth"] is not None:
            depth_rel = f"{rel_dir}/{cam.name}_000_depth.npy"
            np.save(data_root / depth_rel, cap["depth"])  # lossless uint16
            _store.record_frame(_episode_id, camera=f"{cam.name}:depth", t=0.0,
                                file_path=depth_rel, width=w, height=h)
            result["depth_path"] = depth_rel

    return result


# ---------------------------------------------------------------------------
# Gripper
# ---------------------------------------------------------------------------

def open_gripper(
    *,
    width: float = _GRIPPER_MAX_WIDTH,
    speed: float = _DEFAULT_GRIPPER_SPEED,
    _context: Optional[Any] = None,
) -> dict:
    """Open the gripper to the given width (default: fully open).

    Args:
        width: target width in meters (0.0 - 0.08)
        speed: opening speed, m/s
    """
    width = _clamp(float(width), _GRIPPER_MIN_WIDTH, _GRIPPER_MAX_WIDTH)
    speed = max(0.01, float(speed))

    gripper = _extract(_context, "gripper")
    if gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "target_width": width, "speed": speed,
                "note": "mock — no actual gripper move"}

    ok = gripper.move(width=width, speed=speed)
    state = gripper.read_once()
    return {
        "_source": "live",
        "target_width": width,
        "actual_width": float(state.width),
        "speed": speed,
        "success": bool(ok),
    }


def close_gripper(
    *,
    width: float = 0.0,
    speed: float = _DEFAULT_GRIPPER_SPEED,
    force: float = _DEFAULT_GRIP_FORCE,
    epsilon_inner: float = 0.005,
    epsilon_outer: float = 0.005,
    _context: Optional[Any] = None,
) -> dict:
    """Close the gripper around an object with a target width and force.

    Uses libfranka's `grasp` semantics — the grasp succeeds if the gripper
    width falls within [width - epsilon_inner, width + epsilon_outer] and the
    force can be maintained.

    Args:
        width: expected object width (m). Use 0.0 for "close all the way."
        speed: closing speed (m/s)
        force: grip force (N)
        epsilon_inner, epsilon_outer: width tolerance for grasp-success check
    """
    width = _clamp(float(width), _GRIPPER_MIN_WIDTH, _GRIPPER_MAX_WIDTH)
    speed = max(0.01, float(speed))
    force = max(1.0, float(force))

    gripper = _extract(_context, "gripper")
    if gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "target_width": width, "speed": speed,
                "force": force, "grasped": True, "note": "mock — no actual gripper move"}

    note = None
    try:
        grasped = gripper.grasp(
            width=width, speed=speed, force=force,
            epsilon_inner=epsilon_inner, epsilon_outer=epsilon_outer,
        )
    except Exception:
        # libfranka rejects a re-issued grasp on an already-held object. If the
        # current width is within the acceptance band and an object is present,
        # treat it as already grasped rather than erroring (idempotent close).
        w = float(gripper.read_once().width)
        if w > 0.003 and (width - epsilon_inner - 1e-3) <= w <= (width + epsilon_outer + 1e-3):
            grasped = True
            note = f"already holding at {w:.4f} m; grasp re-issue skipped"
        else:
            raise
    state = gripper.read_once()
    result = {
        "_source": "live",
        "target_width": width,
        "actual_width": float(state.width),
        "speed": speed,
        "force": force,
        "grasped": bool(grasped),
    }
    if note:
        result["note"] = note
    return result


# ---------------------------------------------------------------------------
# Bulk registration
# ---------------------------------------------------------------------------

PRIMITIVE_SPECS = [
    {
        "name": "get_state",
        "function_spec": "hebbot.skills.primitives:get_state",
        "params_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "move_to_joints",
        "function_spec": "hebbot.skills.primitives:move_to_joints",
        # Predict the arm actually reaches the commanded configuration. Live
        # settle error at speed_factor 0.1 is ~2-4 mrad; >50 mrad means it
        # stalled / hit something (a surprise). Mock results omit max_error, so
        # this check is skipped in mock mode.
        "expectations": {
            "checks": [
                {"name": "reached_target", "field": "max_error", "op": "le", "value": 0.05},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 7, "maxItems": 7,
                    "description": "7-DOF joint angles in radians",
                },
                "speed_factor": {
                    "type": "number",
                    "minimum": 0.01, "maximum": 0.5, "default": 0.1,
                },
            },
            "required": ["q"],
            "additionalProperties": False,
        },
    },
    {
        "name": "move_to_pose",
        "function_spec": "hebbot.skills.primitives:move_to_pose",
        # Predict the EE lands near the commanded position. Live error is ~3 mm;
        # >20 mm means it reached a pose far from target (a surprise). No IK
        # solution raises instead, taking the hard-fail path. Mock omits
        # position_error -> skipped.
        "expectations": {
            "checks": [
                {"name": "reached_pose", "field": "position_error", "op": "le", "value": 0.02},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3, "maxItems": 3,
                    "description": "[x, y, z] in meters, robot base frame",
                },
                "orientation": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4, "maxItems": 4,
                    "description": "quaternion [w, x, y, z]; default: gripper-down",
                },
                "speed_factor": {
                    "type": "number",
                    "minimum": 0.01, "maximum": 0.5, "default": 0.1,
                },
            },
            "required": ["position"],
            "additionalProperties": False,
        },
    },
    {
        "name": "teleop_xbox",
        "function_spec": "hebbot.skills.primitives:teleop_xbox",
        "params_schema": {
            "type": "object",
            "properties": {
                "speed": {"type": "number", "minimum": 0.005, "maximum": 0.2,
                          "default": 0.06,
                          "description": "max jog velocity at full stick (m/s)"},
                "max_runtime": {"type": "number", "minimum": 1, "maximum": 300,
                                "default": 60,
                                "description": "hard cap on the session (s)"},
                "frequency": {"type": "number", "minimum": 10, "maximum": 1000,
                              "default": 100, "description": "control loop rate (Hz)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_camera",
        "function_spec": "hebbot.skills.primitives:read_camera",
        "params_schema": {
            "type": "object",
            "properties": {
                "camera": {
                    "type": "string",
                    "description": "camera name (e.g. 'wrist', 'scene'); "
                                   "optional when only one camera is connected",
                },
                "with_depth": {"type": "boolean", "default": True,
                               "description": "also capture + save aligned depth"},
                "save": {"type": "boolean", "default": True,
                         "description": "persist frame(s) to disk + frames table"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "open_gripper",
        "function_spec": "hebbot.skills.primitives:open_gripper",
        "params_schema": {
            "type": "object",
            "properties": {
                "width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.08},
                "speed": {"type": "number", "minimum": 0.01, "default": 0.1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "close_gripper",
        "function_spec": "hebbot.skills.primitives:close_gripper",
        # Predict the grasp holds. grasped=False means the fingers closed but
        # didn't settle on an object at the expected width — the canonical
        # manipulation surprise (closed on air, object slipped, wrong width).
        "expectations": {
            "checks": [
                {"name": "grasped", "field": "grasped", "op": "eq", "value": True},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.0,
                          "description": "expected object width (m); 0 for full close"},
                "speed": {"type": "number", "minimum": 0.01, "default": 0.1},
                "force": {"type": "number", "minimum": 1.0, "default": 20.0,
                          "description": "grip force in N"},
                "epsilon_inner": {"type": "number", "minimum": 0.0, "default": 0.005},
                "epsilon_outer": {"type": "number", "minimum": 0.0, "default": 0.005},
            },
            "additionalProperties": False,
        },
    },
]


def register_all(substrate) -> None:
    """Register all primitives in this module into the given substrate."""
    for spec in PRIMITIVE_SPECS:
        substrate.register(
            spec["name"],
            function_spec=spec["function_spec"],
            params_schema=spec.get("params_schema"),
            expectations=spec.get("expectations"),
        )
