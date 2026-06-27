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

# panda_py's configured EE sits ~10.3 cm along the tool +z axis from cuRobo's
# `panda_hand` frame (measured by FK-checking cuRobo configs through panda_py).
# We offset the target from EE frame to panda_hand frame before asking cuRobo.
_HAND_EE_OFFSET = 0.103


def _curobo_ik(ee_position, orientation_wxyz, start_q, url, timeout=3.0):
    """Collision-aware IK via the cuRobo planning service.

    ee_position / orientation are in panda_py's EE frame (the substrate
    convention); we translate to cuRobo's panda_hand frame, seed with start_q for
    continuity, and return a 7-vector config (np.ndarray) or None on any failure
    (service down, no solution) so the caller can fall back to analytical IK.
    """
    import json
    import urllib.request

    import numpy as np

    w, x, y, z = (float(v) for v in orientation_wxyz)
    tool_z = _quat_to_mat([x, y, z, w]) @ np.array([0.0, 0.0, 1.0])
    hand_pos = np.array(ee_position, dtype=float) - _HAND_EE_OFFSET * tool_z
    body = json.dumps({
        "position": [float(v) for v in hand_pos],
        "quaternion": [float(v) for v in orientation_wxyz],
        "start_q": [float(v) for v in start_q],
    }).encode()
    try:
        req = urllib.request.Request(url.rstrip("/") + "/ik", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except Exception:
        return None
    if not res.get("ok"):
        return None
    return np.array(res["q"], dtype=float)


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

    import os

    ik_source = "analytical"
    best_q = None
    # Prefer cuRobo collision-aware IK when the planning service is configured
    # (HEBBOT_CUROBO_URL). FK-verify the returned config against the intended pose
    # and reject large joint swings; on any miss fall back to analytical ik_full.
    # Default to the local cuRobo planning service; set HEBBOT_CUROBO_URL="" to disable.
    curobo_url = os.environ.get("HEBBOT_CUROBO_URL", "http://127.0.0.1:8211")
    if curobo_url:
        cand = _curobo_ik(position, orientation, list(current_q), curobo_url)
        if cand is not None and cand.shape == (7,):
            reached = panda_py.fk(cand)[:3, 3]
            swing = float(np.max(np.abs(cand - np.array(current_q))))
            if np.linalg.norm(reached - pos) < 0.01 and swing < 2.5:
                best_q = cand
                ik_source = "curobo"

    # Fallback: analytical ik_full returns up to 4 branches; pick the one closest
    # to current joints (commitment #11's preference for relative-state continuity).
    if best_q is None:
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
        "ik_source": ik_source,
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
    deadzone = 0.30  # raised from 0.15 to swallow a drifting stick (2026-06-25)

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
    # Firm orientation, compliant translation. Rotational stiffness 30 held
    # gripper-down at rest but drifted ~9.5deg under translation jogging over a
    # session (the leveled start orientation didn't stay level) -> raised to 100
    # so the gripper holds vertical while moving. Nullspace softened 20->10: at
    # 20 the bias toward the (high) start config resisted descending to a low
    # touch-off, stalling the down-jog near the table (now reaches contact freely).
    ctrl.set_impedance(np.diag([200.0, 200.0, 200.0, 100.0, 100.0, 100.0]))
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


def grasp_at(
    *,
    grasp_z: float = 0.038,
    width: float = 0.024,
    force: float = _DEFAULT_GRIP_FORCE,
    descent_speed: float = 0.04,
    stall_guard: float = 0.068,
    open_width: Optional[float] = None,
    _context: Optional[Any] = None,
) -> dict:
    """Compliant straight-down descent from the current (x, y) to grasp_z, then close.

    Reflex-safe: Cartesian impedance with firm xy + orientation and softer vertical
    stiffness, so a finger graze deflects rather than tripping a cartesian_reflex
    (which would lock the robot). Assumes the gripper is already positioned over the
    target (e.g. via the vision servo). If a fingertip lands on top of the object the
    descent stalls above stall_guard and refuses to close (would grab air).

    Args:
        grasp_z: EE z target for the fingertips-around-object height (m).
        width: expected object width (m) for the grasp-success band.
        force: grip force (N). descent_speed: m/s. stall_guard: refuse-to-close EE z.
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    if panda is None or gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "grasped": None, "note": "mock — no arm/gripper"}

    import numpy as np
    from panda_py import controllers

    # Open so the fingers straddle. A narrower pre-open (just wider than the object) makes
    # the close a short, symmetric squeeze — a long light block can't be cammed/squirted out
    # the way a full-80mm asymmetric close does (the elongated-block shove, lab 2026-06-27).
    ow = _GRIPPER_MAX_WIDTH if open_width is None else float(np.clip(open_width, 0.02, _GRIPPER_MAX_WIDTH))
    gripper.move(width=ow, speed=0.1)
    pos0 = panda.get_position()
    X, Y = float(pos0[0]), float(pos0[1])
    quat = panda.get_orientation()      # hold current (gripper-down) orientation
    q0 = np.array(panda.get_state().q)

    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 300.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    tz = float(panda.get_position()[2])
    settle = 0
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            while cx.ok():
                tz = max(grasp_z, tz - descent_speed * dt)
                ctrl.set_control(np.array([X, Y, tz]), quat, q0)
                if tz <= grasp_z + 1e-4:
                    settle += 1
                    if settle > 60:        # ~0.6 s settle at target
                        break
    finally:
        panda.stop_controller()

    actual_z = float(panda.get_position()[2])
    if actual_z > stall_guard:
        return {"_source": "live", "grasped": False, "stalled_high": True,
                "actual_z": actual_z,
                "note": f"stalled at {actual_z:.4f} m (fingertip on object top) — not closing"}

    try:
        grasped = gripper.grasp(width=width, speed=0.1, force=force,
                                epsilon_inner=0.018, epsilon_outer=0.018)
    except Exception:
        grasped = float(gripper.read_once().width) > 0.003   # already holding
    return {"_source": "live", "grasped": bool(grasped), "actual_z": actual_z,
            "actual_width": float(gripper.read_once().width)}


# ---------------------------------------------------------------------------
# Vision grasp — calibration-free top-down pick via image-Jacobian servo
# ---------------------------------------------------------------------------
# Calibration captured 2026-06-25 (lab notebook #130/#132). Valid only for a
# dead-vertical gripper-down pick at the servo height; the grasp-pixel and the
# image Jacobian are both height- and orientation-specific.
_VG_APPROACH_Z = 0.20                    # final servo/grasp-approach height (m), EE frame
_VG_ACQUIRE_Z = 0.34                     # start height — wide FOV finds the block anywhere
# grasp-pixel(z): where a table point directly under the gripper appears, as a function
# of camera height. The camera is tilted+offset from the gripper axis, so this point's
# image slides as z changes — a perspective law pixel = a + b/z (fit sub-pixel over
# 0.20–0.36, 2026-06-25). The old single-height grasp-pixel (357.5,372.7) is this at z=0.20.
_VG_GP_U = (328.9, 5.94)                 # u(z) = a + b/z
_VG_GP_V = (235.6, 27.4)                 # v(z) = a + b/z
# image-Jacobian inverse at z=0.20 (metres EE per pixel). The gain is ~f/z, so at any
# other height Jinv scales by z/0.20 (verified to ~7% at z=0.36 — well inside servo tol).
_VG_JINV0 = ((-1.168537e-05, -4.959711e-04),
             (-4.821881e-04,  1.770091e-05))


def _vg_grasp_pixel(z):
    """Pixel where a table point directly under the gripper appears at EE height z."""
    return (_VG_GP_U[0] + _VG_GP_U[1] / z, _VG_GP_V[0] + _VG_GP_V[1] / z)


def _vg_jinv(z):
    """Image-Jacobian inverse at height z (Jinv0 scaled by z/0.20; gain ~ f/z)."""
    s = z / _VG_APPROACH_Z
    return ((_VG_JINV0[0][0] * s, _VG_JINV0[0][1] * s),
            (_VG_JINV0[1][0] * s, _VG_JINV0[1][1] * s))


_SEGMENT_COLORS = ("blue", "green", "red")


def _color_mask(R, G, B, color):
    """Boolean cube mask from integer RGB channel arrays. Calibrated per color:

    - **blue** — the navy cube reads RGB≈(1,45,72): near-zero red, blue-dominant, and
      *dark*. The G<95 ceiling rejects bright cyan ceiling-light glare (G≈120,B≈250),
      which is otherwise also low-red + blue-dominant.
    - **green** — RGB≈(70,90,38): green-dominant with low blue. The `G−B>25` test is the
      decisive one — it rejects the *blue* cube (G−B≈−36) and the neutral table/glare/
      shadow (G−B≈+5), so blue and green never get confused.
    - **red** — RGB≈(160,80,75): strongly red-dominant and *bright*. The R>115 floor
      rejects the cube's own reddish cast shadow (R≈101, same hue but dark).
    """
    if color == "blue":
        return (R < 40) & (B - G > 18) & (B > 50) & (G < 95)
    if color == "green":
        return (G - B > 25) & (G - R > 12) & (G > 45)
    if color == "red":
        return (R - G > 40) & (R - B > 40) & (R > 115)
    raise ValueError(f"uncalibrated color {color!r}; have {_SEGMENT_COLORS}")


def _segment(bgr, color="blue"):
    """Centroid (u, v, npix) of the `color` cube in a BGR frame, or None if too few px."""
    import numpy as np
    rgb = bgr[:, :, ::-1].astype(int)    # capture() is BGR; flip to true RGB
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    m = _color_mask(R, G, B, color)
    ys, xs = np.where(m)
    if len(xs) < 80:
        return None
    return float(xs.mean()), float(ys.mean()), int(len(xs))


def _label_cc(mask, max_iter=120):
    """Pure-numpy 4-connected components (no scipy): min-label propagation to
    convergence. Returns an int label array (0 = background). Compact blobs
    converge in ~radius iterations; the substrate venv has no cv2/scipy."""
    import numpy as np
    h, w = mask.shape
    lab = np.where(mask, np.arange(1, h * w + 1).reshape(h, w), 0).astype(np.int64)
    BIG = h * w + 1
    for _ in range(max_iter):
        prev = lab
        nb = np.stack([lab,
                       np.pad(lab[:-1], ((1, 0), (0, 0))),
                       np.pad(lab[1:], ((0, 1), (0, 0))),
                       np.pad(lab[:, :-1], ((0, 0), (1, 0))),
                       np.pad(lab[:, 1:], ((0, 0), (0, 1)))])
        nb[nb == 0] = BIG
        new = nb.min(0)
        new[~mask] = 0
        lab = new
        if np.array_equal(lab, prev):
            break
    return lab


def find_blocks(
    *,
    camera: Optional[str] = None,
    colors: Optional[list] = None,
    min_px: int = 120,
    _context: Optional[Any] = None,
) -> dict:
    """Per-instance block detection — the shape-aware perception (lab notebook #137).

    Unlike `_segment` (one centroid per colour), this labels connected components so
    *multiple same-colour blocks are separated*, and reports each instance's geometry:
    centroid pixel, principal-axis angle, pixel extent (L×W), aspect ratio, and a
    shape tag (`rect` if aspect ≥ 1.5 else `cube`). A rectangular block lying down shows
    its long face (rect); standing on its end it shows a near-square top face (cube-like)
    — so aspect also hints at orientation. Calibrated colours: blue, green, red.

    Returns {blocks: [{color, shape, u, v, ang, L, W, aspect, npix}, ...]} sorted by size.
    """
    cameras = _extract(_context, "cameras") or {}
    if not cameras:
        time.sleep(0.02)
        return {"_source": "mock", "blocks": []}
    import numpy as np
    cam = _select_camera(cameras, camera)
    if cam is None:
        return {"_source": "error", "blocks": [], "note": "no camera"}
    rgb = cam.capture(with_depth=False)["color"][:, :, ::-1].astype(int)  # BGR->RGB
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    want = colors or list(_SEGMENT_COLORS)
    blocks = []
    for col in want:
        if col not in _SEGMENT_COLORS:
            continue
        lab = _label_cc(_color_mask(R, G, B, col))
        for L in np.unique(lab):
            if L == 0:
                continue
            ys, xs = np.where(lab == L)
            if len(xs) < min_px:
                continue
            pts = np.stack([xs, ys]).astype(float)
            c = pts.mean(1)
            p = pts - c[:, None]
            evals, evecs = np.linalg.eigh(p @ p.T / p.shape[1])
            ang = float(np.degrees(np.arctan2(evecs[1, -1], evecs[0, -1])))
            Lp, Wp = 4 * np.sqrt(evals[-1]), 4 * np.sqrt(max(evals[0], 1e-6))
            asp = float(Lp / max(Wp, 1.0))
            blocks.append({"color": col, "shape": "rect" if asp >= 1.5 else "cube",
                           "u": round(float(c[0]), 1), "v": round(float(c[1]), 1),
                           "ang": round(ang), "L": round(float(Lp)), "W": round(float(Wp)),
                           "aspect": round(asp, 2), "npix": int(len(xs))})
    blocks.sort(key=lambda b: -b["npix"])
    return {"_source": "live", "blocks": blocks, "n": len(blocks)}


def _nearest_instance(bgr, color, track, min_px=80):
    """Centroid (u,v,npix) of the `color` connected-component nearest pixel `track` —
    lets the servo lock onto ONE instance in a cluttered / multi-same-colour scene
    instead of _segment's merged centroid. None if no plausible instance."""
    import numpy as np
    rgb = bgr[:, :, ::-1].astype(int)
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lab = _label_cc(_color_mask(R, G, B, color))
    best, bestd = None, 1e18
    for L in np.unique(lab):
        if L == 0:
            continue
        ys, xs = np.where(lab == L)
        if len(xs) < min_px:
            continue
        cu, cv = float(xs.mean()), float(ys.mean())
        d = (cu - track[0]) ** 2 + (cv - track[1]) ** 2
        if d < bestd:
            bestd, best = d, (cu, cv, int(len(xs)))
    return best


# EE z at which the open fingertips touch the table (calibrated from the blue-cube grasp,
# 2026-06-27: it bottomed out + gripped at actual_z 0.047). Lets us turn a depth-measured
# block height (above the table) into a grasp_z: grasp_z = _TABLE_EE_Z + (fingertip height).
_TABLE_EE_Z = 0.047
_CAM_FX = 394.484   # wrist intrinsics fx (px) — metric extent = px * depth / fx


def _perceive_pose_3d(cam, color, track, _context=None, min_px=120):
    """Depth-derived 3-D pose of the tracked block — the geometry-true grasp perception.

    The colour silhouette is a poor grasp cue: its principal axis is noisy (a small,
    lit, partly-shadowed blob) and for a *standing* block its centroid is pulled toward
    the lit side face. Depth is lighting-immune, so we segment by colour to find the
    instance but read its pose from the depth points: isolate the TOP face (highest
    points), take its centroid (the real grip target), its principal axis (the stable
    long-edge direction → fingers go across the short axis), its metric short/long
    extent, and its height above the table (→ grasp_z, and standing-vs-lying).

    Returns {u, v (top-face centroid px), cu, cv (colour centroid px), ang (deg),
    width_m, length_m, height_m, coverage, block_depth, table_depth} or None.
    """
    import numpy as np
    cap = cam.capture(with_depth=True)
    bgr, depth = cap["color"], cap.get("depth")
    if depth is None:
        return None
    ds = cap.get("depth_scale") or 1e-4
    rgb = bgr[:, :, ::-1].astype(int)
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lab = _label_cc(_color_mask(R, G, B, color))
    best, bestd = None, 1e18
    for Ll in np.unique(lab):
        if Ll == 0:
            continue
        ys, xs = np.where(lab == Ll)
        if len(xs) < min_px:
            continue
        cu, cv = float(xs.mean()), float(ys.mean())
        d = (cu - track[0]) ** 2 + (cv - track[1]) ** 2
        if d < bestd:
            bestd, best = d, (xs, ys, cu, cv)
    if best is None:
        return None
    xs, ys, cu, cv = best
    dm = depth.astype(np.float32) * ds
    dvals = dm[ys, xs]
    ok = dvals > 0.05
    cov = float(ok.mean())
    if int(ok.sum()) < 30:
        return None
    gx, gy, gd = xs[ok].astype(float), ys[ok].astype(float), dvals[ok]
    # table depth: a ring of non-this-colour pixels around the instance
    yy, xx = np.mgrid[0:dm.shape[0], 0:dm.shape[1]]
    r = np.hypot(xx - cu, yy - cv)
    rad = float(np.sqrt(len(xs) / np.pi))
    ring = (r > rad * 1.6) & (r < rad * 3.4) & (lab == 0) & (dm > 0.05)
    if int(ring.sum()) < 50:
        ring = (r > rad * 1.6) & (lab == 0) & (dm > 0.05)
    table_depth = float(np.median(dm[ring])) if int(ring.sum()) else float(np.percentile(gd, 90))
    heights = table_depth - gd                      # m above the table (top is closest → tallest)
    Hmax = float(np.percentile(heights, 92))
    height_m = max(0.0, Hmax)
    topf = heights >= (Hmax - 0.012)                # the top plate (within 12 mm of the top)
    if int(topf.sum()) < 12:
        topf = heights >= (Hmax - 0.020)
    tx, ty = gx[topf], gy[topf]
    u, v = float(tx.mean()), float(ty.mean())
    block_depth = float(np.median(gd[topf])) if int(topf.sum()) else float(np.median(gd))
    P = np.stack([tx - u, ty - v])
    evals, evecs = np.linalg.eigh(P @ P.T / max(P.shape[1], 1))
    ang = float(np.degrees(np.arctan2(evecs[1, -1], evecs[0, -1])))
    Lp, Wp = 4 * np.sqrt(max(evals[-1], 1e-9)), 4 * np.sqrt(max(evals[0], 1e-9))
    return {"u": u, "v": v, "cu": cu, "cv": cv, "ang": ang,
            "length_m": Lp * block_depth / _CAM_FX, "width_m": Wp * block_depth / _CAM_FX,
            "height_m": height_m, "coverage": cov,
            "block_depth": block_depth, "table_depth": table_depth}


def vision_grasp(
    *,
    color: str = "blue",
    camera: Optional[str] = None,
    grasp_z: float = 0.038,
    width: float = 0.024,
    acquire_z: float = _VG_ACQUIRE_Z,
    approach_z: float = _VG_APPROACH_Z,
    max_steps: int = 10,
    pixel_tol: float = 4.0,
    descend_tol: float = 22.0,
    z_step: float = 0.045,
    pick_top: float = 0.0,
    target: Optional[list] = None,
    max_retry: int = 2,
    geometry: bool = True,
    _context: Optional[Any] = None,
) -> dict:
    """Locate a block from the wrist camera and grasp it — no hand-eye calibration.

    Coarse-to-fine image servo: starts high (acquire_z, wide FOV → the block can be
    anywhere on the table), and at each step segments the block, drives its image to
    the height-dependent grasp-pixel with the 1/z-scaled image Jacobian
    (Δee = Jinv(z)·(grasp_pixel(z) − block_pixel)), and descends a notch *once it is
    centred enough at the current height* (pixel error < descend_tol). When it reaches
    approach_z centred to within pixel_tol it runs the compliant `grasp_at`. Centring
    before committing lower keeps the block inside the narrowing FOV and away from the
    grasp-pixel's z-sensitivity. Expects a dead-vertical gripper-down pose.
    (lab notebook #130/#132/#133)

    Args:
        color: block color to grasp — calibrated: "blue", "green".
        camera: camera name (optional when one camera is connected).
        grasp_z, width: passed through to grasp_at.
        acquire_z: start/observation height (wide FOV). approach_z: final servo height.
        max_steps: servo iteration cap. pixel_tol: final convergence threshold (px).
        descend_tol: pixel error under which it's safe to drop a notch.
        z_step: descent increment (m) per centred step.
    """
    if color not in _SEGMENT_COLORS:
        return {"_source": "error", "grasped": None,
                "note": f"uncalibrated color {color!r}; have {_SEGMENT_COLORS}"}
    panda = _extract(_context, "panda")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "grasped": None, "note": "mock — no arm/camera"}

    import numpy as np

    cam = _select_camera(cameras, camera)
    if cam is None:
        return {"_source": "error", "grasped": None, "note": "named camera not found"}

    # Rise to the acquisition height (preserving xy) for the widest catch basin.
    pos = panda.get_position()
    z = float(np.clip(acquire_z, approach_z, 0.45))
    move_to_pose([float(pos[0]), float(pos[1]), z], _context=_context)

    import math
    history: list[dict] = []
    located = False
    track = list(target) if target else None   # lock onto one instance when given

    def _coarse_servo():
        """Colour-centroid image-Jacobian servo from acquire_z down to approach_z over the
        tracked instance — a coarse centre. Returns the last (u, v) pixel, or None if lost."""
        nonlocal track, located
        z = float(np.clip(acquire_z, approach_z, 0.45))
        p0 = panda.get_position()
        move_to_pose([float(p0[0]), float(p0[1]), z], _context=_context)
        last = None
        for step in range(max_steps):
            frame = cam.capture(with_depth=False)["color"]
            seg = _nearest_instance(frame, color, track) if track else _segment(frame, color)
            if seg is None:
                return None
            u, v, npix = seg
            if track is not None:
                track = [u, v]
            d = z - pick_top                   # parallax: a point pick_top above the table
            if track is None:                  # plausibility gate (merged-colour path only)
                expected = 5600.0 * (_VG_APPROACH_Z / d) ** 2
                if not (0.2 * expected <= npix <= 4.0 * expected):
                    return None
            located = True
            tu, tv = _vg_grasp_pixel(d)
            du, dv = tu - u, tv - v
            err = float(np.hypot(du, dv))
            at_bottom = z <= approach_z + 1e-3
            history.append({"step": step, "z": round(z, 3), "u": round(u, 1),
                            "v": round(v, 1), "pixel_err": round(err, 1), "npix": npix})
            last = (u, v)
            if at_bottom and err <= pixel_tol:
                break
            jinv = _vg_jinv(d)
            dex = jinv[0][0] * du + jinv[0][1] * dv
            dey = jinv[1][0] * du + jinv[1][1] * dv
            clamp = 0.06 if track is not None else 0.15
            dex = float(np.clip(dex, -clamp, clamp))
            dey = float(np.clip(dey, -clamp, clamp))
            if err <= descend_tol and not at_bottom:
                z = max(approach_z, z - z_step)
            p = panda.get_position()
            tx = float(np.clip(float(p[0]) + dex, 0.30, 0.60))
            ty = float(np.clip(float(p[1]) + dey, -0.28, 0.28))
            move_to_pose([tx, ty, z], _context=_context)
        return last

    def _recenter(u, v, tu, tv, d):
        """Translate (gripper-down → image Jacobian valid) so the depth top-face centroid
        (u, v) lands on the target grasp-pixel (tu, tv). Run before yawing. `d` is the top
        face's camera distance so the Jacobian gain matches its height (parallax)."""
        nonlocal track
        jinv = _vg_jinv(d)
        du, dv = tu - u, tv - v
        dex = float(np.clip(jinv[0][0] * du + jinv[0][1] * dv, -0.05, 0.05))
        dey = float(np.clip(jinv[1][0] * du + jinv[1][1] * dv, -0.05, 0.05))
        p = panda.get_position()
        # Hold the perceive height (pz, ~0.30) — NOT approach_z. Dropping to 0.20 here makes
        # the re-perceive read close-range depth, which is unreliable (it inflated a cube to
        # 60 mm and broke the grasp). pz resolves from the enclosing scope at call time.
        move_to_pose([float(p[0]) + dex, float(p[1]) + dey, pz], _context=_context)
        track = [u, v]

    def _tilt_classify():
        """Piece 3 — 'back off and view from another angle': a 35° side glance that resolves
        the standing-vs-lying ambiguity a single top-down view cannot (it fooled me 3× in one
        session). Returns 'standing'|'lying'|None and leaves the wrist back over the block."""
        p = panda.get_position(); bx, by, bz = float(p[0]), float(p[1]), float(p[2])
        cls = None
        try:
            move_to_pose([bx + 0.16, by, 0.28], orientation=[0.0, 0.954, 0.0, -0.301], _context=_context)
            rgb = cam.capture(with_depth=False)["color"][:, :, ::-1].astype(int)
            m = _color_mask(rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2], color)
            ys, xs = np.where(m)
            if len(xs) > 60:                   # side silhouette: standing → taller than wide
                vh, uw = (ys.max() - ys.min()), (xs.max() - xs.min())
                cls = "standing" if vh >= 1.15 * uw else "lying"
        except Exception:
            cls = None
        move_to_pose([bx, by, bz], _context=_context)   # back over the block, same height
        return cls

    result = None
    lower_bias = 0.0          # each missed grasp biases the next one lower (Doug: a miss is
    for attempt in range(max_retry + 1):   # usually 'gripped too high' → open + go down + retry)
        if _coarse_servo() is None:
            result = {"_source": "live", "located": located, "grasped": False,
                      "note": "block not found in frame", "servo_steps": len(history),
                      "history": history, "attempt": attempt}
            break

        grip_yaw, geom = None, None
        g_z, grip_w, s_guard = grasp_z + pick_top, width, 0.068 + pick_top
        pz = approach_z
        if geometry and track is not None:
            # Perceive from 0.30 m, not the 0.20 m servo floor: close-range RealSense depth
            # drops out below ~0.2 m, and metric pose (axis/width/height) is z-invariant.
            pz = max(approach_z, 0.30)
            p = panda.get_position()
            move_to_pose([float(p[0]), float(p[1]), pz], _context=_context)
            geom = _perceive_pose_3d(cam, color, track, _context=_context)
        if geom is not None:
            H = geom["height_m"]
            # Re-centre on the TRUE top-face centroid. The grasp-pixel is parallax-corrected
            # to the top face's height (d = pz − H), like an elevated/stacked pick.
            d_face = max(0.05, pz - H)
            tu, tv = _vg_grasp_pixel(d_face)
            if float(np.hypot(geom["u"] - tu, geom["v"] - tv)) > pixel_tol:
                _recenter(geom["u"], geom["v"], tu, tv, d_face)
                geom = _perceive_pose_3d(cam, color, track, _context=_context) or geom
                H = geom["height_m"]

            # Piece 3: a tilt-glance only when the top-down height is ambiguous / sparse —
            # it corrects H so a standing block isn't mistaken for a low one (and vice versa).
            if 0.034 <= H <= 0.052 or geom["coverage"] < 0.40:
                cls = _tilt_classify()
                geom = _perceive_pose_3d(cam, color, track, _context=_context) or geom
                H = geom["height_m"]
                if cls == "standing" and H < 0.045:
                    H = 0.058
                elif cls == "lying" and H > 0.035:
                    H = 0.029

            grip_w = float(np.clip(geom["width_m"] - 0.004, 0.010, 0.05))
            open_w = float(np.clip(grip_w + 0.028, 0.045, _GRIPPER_MAX_WIDTH))
            # Grip height. A block that can reach the table is gripped at TABLE level — the
            # proven cube/rect grip: descend until the fingertips stall on the table beside it
            # (mid-height gripping fails — Doug saw it grip 'a little too high'). Only a clearly
            # tall standing block (can't reach the table) is gripped low on its body. Retries
            # bias lower, since a miss is usually 'too high'.
            if H >= 0.050:
                g_z = _TABLE_EE_Z + float(np.clip(0.40 * H, 0.010, 0.040)) - lower_bias
                s_guard = g_z + 0.035
            else:
                g_z = grasp_z - lower_bias       # ~0.038 → descend & stall at the table
                s_guard = 0.072
            g_z = max(0.026, g_z)

            # Piece 2: fingers parallel to the true long edges (across the short axis), using
            # the STABLE depth principal axis. Skip the twist for near-square tops (cubes).
            if geom["length_m"] / max(geom["width_m"], 1e-3) >= 1.25:
                phi = math.atan2(-math.cos(math.radians(geom["ang"])),
                                 -math.sin(math.radians(geom["ang"])))
                phi = ((phi + math.pi / 2) % math.pi) - math.pi / 2   # ±90°: gripper is symmetric
                q = _qmul([math.cos(phi / 2), 0, 0, math.sin(phi / 2)], [0.0, 1.0, 0.0, 0.0])
                p = panda.get_position()
                try:
                    move_to_pose([float(p[0]), float(p[1]), pz], orientation=q, _context=_context)
                    grip_yaw = round(math.degrees(phi))
                except Exception:
                    grip_yaw = None

        g = grasp_at(grasp_z=g_z, width=grip_w, stall_guard=s_guard,
                     open_width=(open_w if geom is not None else None), _context=_context)
        result = {"_source": "live", "located": located, "servo_steps": len(history),
                  "final_pixel_err": history[-1]["pixel_err"] if history else None,
                  "grip_yaw_deg": grip_yaw, "attempt": attempt,
                  "geom": ({k: round(float(val), 4) for k, val in geom.items()} if geom else None),
                  "grip_z": round(g_z, 4), "grip_width": round(grip_w, 4),
                  "history": history, "grasped": g.get("grasped"),
                  "actual_z": g.get("actual_z"), "actual_width": g.get("actual_width")}

        # Piece 4: verify + retry. A failed grasp usually nudged the block, so lift, re-acquire
        # near the last sighting, and try again.
        if g.get("grasped"):
            break
        if attempt < max_retry:
            lower_bias += 0.012          # open + go a notch lower on the next try
            p = panda.get_position()
            move_to_pose([float(p[0]), float(p[1]), acquire_z], _context=_context)
            if track is not None:
                seg = _nearest_instance(cam.capture(with_depth=False)["color"], color, track)
                if seg is not None:
                    track = [seg[0], seg[1]]
    return result


def place_block(
    *,
    target_xy: Optional[list[float]] = None,
    target_pixel: Optional[list[float]] = None,
    camera: Optional[str] = None,
    place_z: float = 0.05,
    approach_z: float = _VG_APPROACH_Z,
    lift_z: float = 0.22,
    descent_speed: float = 0.04,
    _context: Optional[Any] = None,
) -> dict:
    """Set a held block down at a target, then lift clear. The inverse of vision_grasp.

    The target may be given as a world xy ([x, y] in base frame — what auto-reset uses,
    a fixed home), or as a target_pixel ([u, v] in the wrist frame), which is deprojected
    to the EE xy that puts the gripper over that image point using the same grasp-pixel /
    image-Jacobian map as the grasp (Δee = Jinv(z)·(grasp_pixel(z) − target_pixel)). With
    neither, it places straight down at the current xy. Descent is compliant (soft z) so a
    slightly-off table height sets the block down rather than jamming. (lab notebook #133)

    Args:
        target_xy: [x, y] world place location (m). Takes precedence over target_pixel.
        target_pixel: [u, v] wrist-frame place location (deprojected via the grasp map).
        place_z: EE z at release (m). approach_z: travel height. lift_z: retreat height.
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    if panda is None or gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "placed": None, "note": "mock — no arm/gripper"}

    import numpy as np
    from panda_py import controllers

    # 1) Get over the target xy at travel height.
    if target_xy is not None:
        X, Y = float(target_xy[0]), float(target_xy[1])
        move_to_pose([X, Y, approach_z], _context=_context)
    elif target_pixel is not None:
        pos = panda.get_position()
        move_to_pose([float(pos[0]), float(pos[1]), approach_z], _context=_context)
        tu, tv = _vg_grasp_pixel(approach_z)
        ji = _vg_jinv(approach_z)
        du, dv = tu - float(target_pixel[0]), tv - float(target_pixel[1])
        p = panda.get_position()
        X = float(p[0]) + ji[0][0] * du + ji[0][1] * dv
        Y = float(p[1]) + ji[1][0] * du + ji[1][1] * dv
        move_to_pose([X, Y, approach_z], _context=_context)
    else:
        p = panda.get_position()
        X, Y = float(p[0]), float(p[1])
        move_to_pose([X, Y, approach_z], _context=_context)

    # 2) Compliant straight-down descent to the release height.
    quat = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 300.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    tz = float(panda.get_position()[2])
    settle = 0
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            while cx.ok():
                tz = max(place_z, tz - descent_speed * dt)
                ctrl.set_control(np.array([X, Y, tz]), quat, q0)
                if tz <= place_z + 1e-4:
                    settle += 1
                    if settle > 40:
                        break
    finally:
        panda.stop_controller()
    actual_z = float(panda.get_position()[2])

    # 3) Release and lift clear.
    gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), lift_z], _context=_context)
    return {"_source": "live", "placed": True, "place_xy": [round(X, 4), round(Y, 4)],
            "actual_z": actual_z, "released_width": float(gripper.read_once().width)}


_CUBE_WIDTH = {"blue": 0.024, "green": 0.029, "red": 0.029}   # measured cube widths (m)
_OBSERVE_POSE = [0.43, 0.0, 0.44]   # wide overhead vantage — sees the whole working area


def _observe(colors, _context, camera=None):
    """Move to the wide overhead pose, then find_blocks — robust re-location so a block
    shifted by a previous (failed) grasp is still found, instead of vanishing from a
    fixed narrower view and collapsing the run (lab notebook #140)."""
    move_to_pose(list(_OBSERVE_POSE), _context=_context)
    return find_blocks(colors=colors, camera=camera, _context=_context)


def stack_on(
    *,
    pick: str = "green",
    onto: str = "blue",
    onto_top: float = 0.0,
    camera: Optional[str] = None,
    acquire_z: float = _VG_ACQUIRE_Z,
    approach_z: float = _VG_APPROACH_Z,
    max_steps: int = 10,
    pixel_tol: float = 4.0,
    descend_tol: float = 22.0,
    z_step: float = 0.045,
    contact_force: float = 4.0,
    contact_gap: float = 0.008,
    z_floor: float = 0.06,
    _context: Optional[Any] = None,
) -> dict:
    """Pick the `pick` cube and stack it on top of the `onto` cube.

    `vision_grasp`s the pick cube, lifts, image-servos the gripper over the onto cube
    (same coarse-to-fine loop, onto's centroid as the target), then descends the held
    cube straight down until its bottom **contacts** onto's top — detected by the
    commanded z diverging from the actual z (the soft-z impedance can't push through the
    contact) — and releases at contact. The contact-detect (vs a guessed release height)
    is the generalization that makes this work at *any* stack height: a 2nd cube, a 3rd,
    uneven cubes — it finds the top each time instead of needing place_z per level. The
    manual proof-of-concept (servo-over-onto + place_block to a fixed height) already
    stacked green on blue (lab notebook #134); this productizes it as one call. `stacked`
    means contact was reached and the cube released — verify the seat by re-imaging.

    Args:
        pick, onto: cube colors (calibrated: blue, green, red). pick != onto.
        onto_top: height (m) of onto's top surface above the table — the stack it sits on
            (0 for a cube on the table, ≈0.053 for stacking onto a 2-high tower). Cancels
            the parallax of centring over an elevated target; raise z_floor to match.
        contact_force: external z-force rise (N) that signals contact (primary, slip-immune).
        contact_gap: commanded−actual z gap (m) — secondary contact trigger.
        z_floor: abort if no contact by this EE z (onto cube missing / mis-located).
    """
    if pick not in _SEGMENT_COLORS or onto not in _SEGMENT_COLORS or pick == onto:
        return {"_source": "error", "stacked": None,
                "note": f"need two distinct calibrated colors; got pick={pick!r} onto={onto!r}"}
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or gripper is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "stacked": None, "note": "mock — no arm/gripper/camera"}

    import numpy as np
    from panda_py import controllers

    cam = _select_camera(cameras, camera)

    # Locate the pick INSTANCE from the wide overhead pose (per-instance — works on rect
    # blocks / clutter; the wide vantage re-finds blocks shifted by a prior grasp, #139/#140).
    fb = _observe([pick], _context)
    picks = [b for b in fb.get("blocks", []) if b["color"] == pick]
    if not picks:
        return {"_source": "live", "stacked": False, "stage": "locate", "note": f"no {pick} instance"}
    pick_inst = max(picks, key=lambda b: b["npix"])

    # 1) Grab the pick instance (shape-aware: targeted + yawed across its short axis).
    g = vision_grasp(color=pick, width=_CUBE_WIDTH.get(pick, 0.029),
                     target=[pick_inst["u"], pick_inst["v"]], max_steps=16,
                     acquire_z=acquire_z, approach_z=approach_z, _context=_context)
    if not g.get("grasped"):
        return {"_source": "live", "stacked": False, "stage": "grasp_pick", "grasp": g}

    # 2) Re-acquire the onto INSTANCE from the wide overhead pose (robust to the pick grasp
    #    having shifted things; #140), then image-servo over it from there.
    fb2 = _observe([onto], _context)
    ontos = [b for b in fb2.get("blocks", []) if b["color"] == onto]
    if not ontos:
        return {"_source": "live", "stacked": False, "stage": "locate_onto",
                "note": f"no {onto} instance found", "grasp": g}
    onto_track = [max(ontos, key=lambda b: b["npix"])["u"], max(ontos, key=lambda b: b["npix"])["v"]]
    z = float(_OBSERVE_POSE[2])   # start the onto-servo from the observe height
    servo: list[dict] = []
    for step in range(max_steps):
        seg = _nearest_instance(cam.capture(with_depth=False)["color"], onto, onto_track)
        if seg is None:
            return {"_source": "live", "stacked": False, "stage": "locate_onto",
                    "note": f"lost {onto} instance", "servo": servo, "grasp": g}
        u, v, npix = seg
        onto_track = [u, v]   # follow the onto instance
        # onto is elevated by onto_top (height of the stack we're placing on), so it sits
        # at camera distance d = z − onto_top — cancel that parallax.
        d = z - onto_top
        tu, tv = _vg_grasp_pixel(d)
        du, dv = tu - u, tv - v
        err = float(np.hypot(du, dv))
        at_bottom = z <= approach_z + 1e-3
        servo.append({"step": step, "z": round(z, 3), "pixel_err": round(err, 1)})
        if at_bottom and err <= pixel_tol:
            break
        jinv = _vg_jinv(d)
        dex = float(np.clip(jinv[0][0] * du + jinv[0][1] * dv, -0.06, 0.06))
        dey = float(np.clip(jinv[1][0] * du + jinv[1][1] * dv, -0.06, 0.06))
        if err <= descend_tol and not at_bottom:
            z = max(approach_z, z - z_step)
        p = panda.get_position()
        tx = float(np.clip(float(p[0]) + dex, 0.30, 0.60))
        ty = float(np.clip(float(p[1]) + dey, -0.28, 0.28))
        move_to_pose([tx, ty, z], _context=_context)

    # 3) Compliant descend to contact, then release at contact (no press).
    X, Y = float(panda.get_position()[0]), float(panda.get_position()[1])
    quat = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 200.0, 30.0, 30.0, 30.0]))  # softer z for a gentle seat
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    tz = float(panda.get_position()[2])
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])   # baseline external z-force (free space)
    contact_z = None
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            while cx.ok():
                tz = max(z_floor, tz - 0.02 * dt)              # slow descent
                ctrl.set_control(np.array([X, Y, tz]), quat, q0)
                az = float(panda.get_position()[2])
                fz = float(panda.get_state().O_F_ext_hat_K[2])
                # Contact = the held cube's bottom meets the surface → external z-force
                # rises. This is SLIP-IMMUNE: when the cube slides up in the gripper the
                # commanded−actual z gap never grows (lab #136), but the force still does.
                # Keep the gap as a secondary trigger.
                if abs(fz - fz0) > contact_force or (tz - az) > contact_gap:
                    contact_z = az
                    break
                if tz <= z_floor + 1e-4:                        # reached floor with no contact
                    break
    finally:
        gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)       # release (at contact, not pressing)
        panda.stop_controller()

    if contact_z is None:
        # no contact by the floor — onto cube wasn't under us; lift, report miss
        p = panda.get_position()
        move_to_pose([float(p[0]), float(p[1]), 0.25], _context=_context)
        return {"_source": "live", "stacked": False, "stage": "no_contact",
                "note": f"descended to floor {z_floor} without contact", "servo": servo}

    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), 0.25], _context=_context)
    return {"_source": "live", "stacked": True, "contact_z": round(contact_z, 4),
            "final_pixel_err": servo[-1]["pixel_err"] if servo else None, "servo": servo}


def _qmul(a, b):
    """Hamilton product of [w,x,y,z] quaternions."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw]


def stand_on_end(
    *,
    color: str = "red",
    camera: Optional[str] = None,
    grip_width: float = 0.03,
    grip_z: float = 0.037,   # a LYING block presents a low (~one-cube) profile — grip low,
                             # not at the mid-height of a standing block (lab notebook #138)
    lift_z: float = 0.30,
    block_half: float = 0.03,
    pitch_sign: float = 1.0,
    _context: Optional[Any] = None,
) -> dict:
    """Stand a lying rectangular block up on its end — the first non-top-down maneuver.

    Finds the (largest) rect block of `color` with `find_blocks`, centres the gripper
    over it, yaws so the fingers close across its *narrow* axis (computed from its image
    angle via the camera rotation), grips it flat, lifts, **pitches the wrist 90° about
    the finger axis** so the block goes vertical, lowers the bottom end to a force-sensed
    contact with the table, and releases. (lab notebook #137; Moravec → reflex.)

    Self-checks: if the grip closes too wide it gripped the long axis (yaw 90° off) and
    retries flipped. pitch_sign ∈ {+1,-1} picks which way it tips up — flip if it tips
    the wrong way. block_half = half the block's length (m), sets the place height.
    """
    import numpy as np, math
    from panda_py import controllers

    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or gripper is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "stood": None, "note": "mock — no arm/gripper/camera"}
    cam = _select_camera(cameras, camera)

    # 1) locate the target rect block
    fb = find_blocks(colors=[color], _context=_context)
    rects = [b for b in fb.get("blocks", []) if b["shape"] == "rect"]
    if not rects:
        return {"_source": "live", "stood": False, "note": f"no lying {color} block found"}
    tgt = rects[0]

    # 2) centre the gripper (down) over the block — servo on the INSTANCE centroid from
    #    find_blocks (not _segment, which merges all same-colour blobs and aims off-centre).
    for _ in range(6):
        fb = find_blocks(colors=[color], _context=_context)
        rs = [b for b in fb.get("blocks", []) if b["shape"] == "rect"]
        if not rs:
            break
        tgt = max(rs, key=lambda b: b["npix"])
        u, v = tgt["u"], tgt["v"]
        z = float(panda.get_position()[2])
        tu, tv = _vg_grasp_pixel(z)
        du, dv = tu - u, tv - v
        if math.hypot(du, dv) <= 5:
            break
        ji = _vg_jinv(z)
        dex = float(np.clip(ji[0][0] * du + ji[0][1] * dv, -0.12, 0.12))
        dey = float(np.clip(ji[1][0] * du + ji[1][1] * dv, -0.12, 0.12))
        p = panda.get_position()
        move_to_pose([float(p[0]) + dex, float(p[1]) + dey, max(0.20, z)], _context=_context)

    # 3) yaw so the fingers (EE-y, world-Y at gripper-down) close across the block's SHORT
    #    axis. Block long-axis image angle θ → world long-axis φ ∝ (−sinθ,−cosθ); the
    #    needed yaw about world-Z is ψ = φ.
    th = math.radians(tgt["ang"])
    phi = math.atan2(-math.cos(th), -math.sin(th))
    bx, by = float(panda.get_position()[0]), float(panda.get_position()[1])

    def grip_with_yaw(psi):
        q = _qmul([math.cos(psi / 2), 0, 0, math.sin(psi / 2)], [0.0, 1.0, 0.0, 0.0])
        move_to_pose([bx, by, 0.20], orientation=q, _context=_context)
        g = grasp_at(grasp_z=grip_z, width=grip_width, stall_guard=0.085, _context=_context)
        return q, g

    q_grip, g = grip_with_yaw(phi)
    w = float(gripper.read_once().width)
    if w > grip_width + 0.012:          # gripped the long face → yaw was 90° off
        gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
        q_grip, g = grip_with_yaw(phi + math.pi / 2)
        w = float(gripper.read_once().width)
    if not g.get("grasped") and w < 0.004:
        return {"_source": "live", "stood": False, "stage": "grip", "note": "grip closed empty", "grip_w": w}

    # 4) lift, then pitch 90° about the local finger (Y) axis → block vertical
    move_to_pose([bx, by, lift_z], orientation=q_grip, _context=_context)
    a = pitch_sign * math.pi / 4
    q_stand = _qmul(q_grip, [math.cos(a), 0, math.sin(a), 0])
    move_to_pose([bx, by, lift_z], orientation=q_stand, _context=_context)

    # 5) lower the block's lower end to a force-sensed contact with the table, release
    quat_xyzw = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 200.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    tz = float(panda.get_position()[2])
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
    floor = 0.015
    contact_z = None
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            while cx.ok():
                tz = max(floor, tz - 0.02 * dt)
                ctrl.set_control(np.array([bx, by, tz]), quat_xyzw, q0)
                if abs(float(panda.get_state().O_F_ext_hat_K[2]) - fz0) > 4.0:
                    contact_z = float(panda.get_position()[2])
                    break
                if tz <= floor + 1e-4:
                    break
    finally:
        gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)   # release the standing block
        panda.stop_controller()

    # 6) retreat up + away without knocking it
    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), lift_z], _context=_context)
    return {"_source": "live", "stood": contact_z is not None, "contact_z": contact_z,
            "grip_w": round(w, 4), "yaw_deg": round(math.degrees(phi))}


def practice(
    *,
    pick: str = "green",
    onto: str = "blue",
    trials: int = 3,
    onto_height: float = 0.024,
    reset_xy: Optional[list] = None,
    camera: Optional[str] = None,
    _context: Optional[Any] = None,
) -> dict:
    """The self-improvement loop: repeatedly {attempt → verify → reset}, logging each
    trial (lab notebook #137–139). `attempt` = stack_on(pick,onto); `verify` = find_blocks
    "onto occluded under pick" (a stacked onto vanishes top-down — coincidence detector);
    `reset` = lift pick off and place it aside so the next trial starts fresh. Returns the
    trial log; the high layer (agent) reviews it and refines the primitives — that review
    is the self-improvement. Runs unattended on the cuRobo motion layer.
    """
    panda = _extract(_context, "panda")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "trials": []}
    rx = reset_xy or [0.45, 0.12]
    log = []
    fails = 0
    for t in range(trials):
        s = stack_on(pick=pick, onto=onto, onto_top=0.0, max_steps=16, _context=_context)
        # verify from the wide overhead pose (robust re-location; #140).
        fb = _observe([pick, onto], _context)
        pk = [b for b in fb.get("blocks", []) if b["color"] == pick]
        on = [b for b in fb.get("blocks", []) if b["color"] == onto]
        # Stacked ⇒ onto's centroid coincides with pick's (within a cube) OR onto is fully
        # occluded under pick. Separate ⇒ centroids far apart. (#139: "onto absent" was too
        # strict — a sliver of the lower block peeks out at close range.)
        pc = max(pk, key=lambda b: b["npix"]) if pk else None
        oc = max(on, key=lambda b: b["npix"]) if on else None
        verified = pc is not None and (oc is None or
                   ((pc["u"] - oc["u"]) ** 2 + (pc["v"] - oc["v"]) ** 2) < 60 ** 2)
        trial = {"trial": t, "attempt": s.get("stacked"), "stage": s.get("stage"),
                 "contact_z": s.get("contact_z"), "verified": verified, "reset": None}
        # reset: lift pick off (elevated by onto_height when stacked) and set it aside; the
        # release height tracks the grasp height (an elevated grasp holds the block higher).
        if pc is not None:
            pt = onto_height if verified else 0.0
            g = vision_grasp(color=pick, target=[pc["u"], pc["v"]], pick_top=pt,
                             width=_CUBE_WIDTH.get(pick, 0.029), max_steps=16, _context=_context)
            if g.get("grasped"):
                place_block(target_xy=rx, place_z=0.04 + pt, lift_z=0.30, _context=_context)
                trial["reset"] = "ok"
            else:
                trial["reset"] = "grasp_failed"
        # cascade-stop: a good trial stacked, verified, AND reset cleanly. Two failures in a
        # row → stop rather than thrash and scatter the scene (the failure mode of #140).
        ok = bool(trial["verified"]) and trial["reset"] == "ok"
        fails = 0 if ok else fails + 1
        log.append(trial)
        if fails >= 2:
            trial["stopped"] = "two consecutive failures"
            break
    return {"_source": "live", "trials": log, "n": len(log),
            "verified_count": sum(1 for l in log if l.get("verified")),
            "reset_failures": sum(1 for l in log if l.get("reset") == "grasp_failed")}


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
    {
        "name": "grasp_at",
        "function_spec": "hebbot.skills.primitives:grasp_at",
        # Predict the compliant descent ends holding the object. grasped=False
        # (incl. the stalled-high refusal) is the surprise — fingertip on top, xy
        # off, or closed on air.
        "expectations": {
            "checks": [
                {"name": "grasped", "field": "grasped", "op": "eq", "value": True},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "grasp_z": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.038,
                            "description": "EE z for fingertips-around-object height (m)"},
                "width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.024,
                          "description": "expected object width (m)"},
                "force": {"type": "number", "minimum": 1.0, "default": 20.0},
                "descent_speed": {"type": "number", "minimum": 0.005, "maximum": 0.1, "default": 0.04},
                "stall_guard": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.068,
                                "description": "refuse to close if the descent stalls above this EE z"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "vision_grasp",
        "function_spec": "hebbot.skills.primitives:vision_grasp",
        # Predict the full locate→servo→grasp loop ends holding the block.
        # grasped=False (block not found, servo diverged, or grasp_at refused)
        # is the surprise.
        "expectations": {
            "checks": [
                {"name": "grasped", "field": "grasped", "op": "eq", "value": True},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["blue", "green", "red"], "default": "blue",
                          "description": "block color to grasp (calibrated: blue, green, red)"},
                "camera": {"type": "string",
                           "description": "camera name; optional when one camera is connected"},
                "grasp_z": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.038},
                "width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.024},
                "acquire_z": {"type": "number", "minimum": 0.2, "maximum": 0.45, "default": 0.34,
                              "description": "start/observation height (m) — higher = wider FOV"},
                "approach_z": {"type": "number", "minimum": 0.1, "maximum": 0.3, "default": 0.20,
                               "description": "final servo height (m) before grasp"},
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10,
                              "description": "image-servo iteration cap"},
                "pixel_tol": {"type": "number", "minimum": 0.5, "default": 4.0,
                              "description": "final servo convergence threshold (px)"},
                "descend_tol": {"type": "number", "minimum": 1.0, "default": 22.0,
                                "description": "pixel error under which it drops a height notch"},
                "z_step": {"type": "number", "minimum": 0.01, "maximum": 0.1, "default": 0.045,
                           "description": "descent increment (m) per centred step"},
                "pick_top": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.0,
                             "description": "height (m) of the cube's base above the table when it's "
                                            "already stacked; parallax + raises the grasp height "
                                            "(≈0.053 to grasp the top of a 2-high tower)"},
                "target": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                           "description": "[u,v] pixel of a specific instance (from find_blocks) to "
                                          "lock onto in a multi-block scene; default grabs the merged colour"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "place_block",
        "function_spec": "hebbot.skills.primitives:place_block",
        # Predict the held block is set down and released. placed=False would be a
        # descent/controller failure.
        "expectations": {
            "checks": [
                {"name": "placed", "field": "placed", "op": "eq", "value": True},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "target_xy": {"type": "array", "items": {"type": "number"},
                              "minItems": 2, "maxItems": 2,
                              "description": "[x, y] world place location (m); preferred"},
                "target_pixel": {"type": "array", "items": {"type": "number"},
                                 "minItems": 2, "maxItems": 2,
                                 "description": "[u, v] wrist-frame place location (deprojected)"},
                "camera": {"type": "string"},
                "place_z": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.05,
                            "description": "EE z at release (m)"},
                "approach_z": {"type": "number", "minimum": 0.1, "maximum": 0.3, "default": 0.20},
                "lift_z": {"type": "number", "minimum": 0.1, "maximum": 0.4, "default": 0.22},
                "descent_speed": {"type": "number", "minimum": 0.005, "maximum": 0.1, "default": 0.04},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "stack_on",
        "function_spec": "hebbot.skills.primitives:stack_on",
        # Predict the pick cube ends grasped, carried, and released on contact with
        # onto's top. stacked=False = grasp miss, onto not located, or no contact.
        "expectations": {
            "checks": [
                {"name": "stacked", "field": "stacked", "op": "eq", "value": True},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "pick": {"type": "string", "enum": ["blue", "green", "red"], "default": "green",
                         "description": "cube to pick up"},
                "onto": {"type": "string", "enum": ["blue", "green", "red"], "default": "blue",
                         "description": "cube to stack onto"},
                "onto_top": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.0,
                             "description": "height (m) of onto's top surface (the stack it sits on); "
                                            "≈0.053 to stack onto a 2-high tower"},
                "camera": {"type": "string"},
                "acquire_z": {"type": "number", "minimum": 0.2, "maximum": 0.45, "default": 0.34},
                "approach_z": {"type": "number", "minimum": 0.1, "maximum": 0.3, "default": 0.20},
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                "pixel_tol": {"type": "number", "minimum": 0.5, "default": 4.0},
                "descend_tol": {"type": "number", "minimum": 1.0, "default": 22.0},
                "z_step": {"type": "number", "minimum": 0.01, "maximum": 0.1, "default": 0.045},
                "contact_force": {"type": "number", "minimum": 1.0, "maximum": 20.0, "default": 4.0,
                                  "description": "external z-force rise (N) signalling contact (primary, slip-immune)"},
                "contact_gap": {"type": "number", "minimum": 0.002, "maximum": 0.03, "default": 0.008,
                                "description": "commanded−actual z gap (m); secondary contact trigger"},
                "z_floor": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.06,
                            "description": "abort if no contact by this EE z"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "find_blocks",
        "function_spec": "hebbot.skills.primitives:find_blocks",
        "params_schema": {
            "type": "object",
            "properties": {
                "camera": {"type": "string"},
                "colors": {"type": "array", "items": {"type": "string", "enum": ["blue", "green", "red"]},
                           "description": "colours to detect (default all calibrated)"},
                "min_px": {"type": "integer", "minimum": 20, "default": 120,
                           "description": "min pixels for an instance"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "stand_on_end",
        "function_spec": "hebbot.skills.primitives:stand_on_end",
        "expectations": {
            "checks": [
                {"name": "stood", "field": "stood", "op": "eq", "value": True},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["blue", "green", "red"], "default": "red"},
                "camera": {"type": "string"},
                "grip_width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.03},
                "grip_z": {"type": "number", "minimum": 0.0, "maximum": 0.2, "default": 0.045},
                "lift_z": {"type": "number", "minimum": 0.1, "maximum": 0.4, "default": 0.30},
                "block_half": {"type": "number", "minimum": 0.0, "maximum": 0.1, "default": 0.03,
                               "description": "half the block's length (m); sets place height"},
                "pitch_sign": {"type": "number", "enum": [1, -1], "default": 1,
                               "description": "which way to tip the block up"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "practice",
        "function_spec": "hebbot.skills.primitives:practice",
        "params_schema": {
            "type": "object",
            "properties": {
                "pick": {"type": "string", "enum": ["blue", "green", "red"], "default": "green"},
                "onto": {"type": "string", "enum": ["blue", "green", "red"], "default": "blue"},
                "trials": {"type": "integer", "minimum": 1, "maximum": 50, "default": 3},
                "onto_height": {"type": "number", "minimum": 0.0, "maximum": 0.1, "default": 0.024,
                                "description": "height (m) of the onto block (for the un-stack grasp)"},
                "reset_xy": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                             "description": "[x,y] where the pick block is set aside between trials"},
                "camera": {"type": "string"},
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
