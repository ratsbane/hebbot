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

    gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)  # open so the fingers straddle
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


def _segment_blue(bgr):
    """Centroid of the navy cube in a BGR frame.

    The cube reads RGB≈(1,45,72) — near-zero red and blue-dominant — which cleanly
    separates it from the bright bluish background (high R) and the gray cast-shadow
    streak (small B−G), both of which a naive B>R blue test catches. Returns
    (u, v, npix) or None if too few pixels match.
    """
    import numpy as np
    rgb = bgr[:, :, ::-1].astype(int)    # capture() is BGR; flip to true RGB
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    m = (R < 40) & (B - G > 18) & (B > 50)
    ys, xs = np.where(m)
    if len(xs) < 80:
        return None
    return float(xs.mean()), float(ys.mean()), int(len(xs))


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
        color: block color — only "blue" is calibrated.
        camera: camera name (optional when one camera is connected).
        grasp_z, width: passed through to grasp_at.
        acquire_z: start/observation height (wide FOV). approach_z: final servo height.
        max_steps: servo iteration cap. pixel_tol: final convergence threshold (px).
        descend_tol: pixel error under which it's safe to drop a notch.
        z_step: descent increment (m) per centred step.
    """
    if color != "blue":
        return {"_source": "error", "grasped": None,
                "note": f"only 'blue' is calibrated; got {color!r}"}
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

    history: list[dict] = []
    located = False
    for step in range(max_steps):
        seg = _segment_blue(cam.capture(with_depth=False)["color"])
        if seg is None:
            return {"_source": "live", "located": located, "grasped": False,
                    "note": "block not found in frame", "servo_steps": step,
                    "history": history}
        located = True
        u, v, npix = seg
        tu, tv = _vg_grasp_pixel(z)
        du, dv = tu - u, tv - v
        err = float(np.hypot(du, dv))
        at_bottom = z <= approach_z + 1e-3
        history.append({"step": step, "z": round(z, 3), "u": round(u, 1),
                        "v": round(v, 1), "pixel_err": round(err, 1), "npix": npix})
        if at_bottom and err <= pixel_tol:
            break
        jinv = _vg_jinv(z)
        dex = jinv[0][0] * du + jinv[0][1] * dv
        dey = jinv[1][0] * du + jinv[1][1] * dv
        # Drop a notch only once we're well-centred at this altitude.
        if err <= descend_tol and not at_bottom:
            z = max(approach_z, z - z_step)
        p = panda.get_position()
        move_to_pose([float(p[0]) + dex, float(p[1]) + dey, z], _context=_context)

    g = grasp_at(grasp_z=grasp_z, width=width, _context=_context)
    return {"_source": "live", "located": located, "servo_steps": len(history),
            "final_pixel_err": history[-1]["pixel_err"] if history else None,
            "history": history, "grasped": g.get("grasped"),
            "actual_z": g.get("actual_z"), "actual_width": g.get("actual_width")}


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
                "color": {"type": "string", "default": "blue",
                          "description": "block color (only 'blue' is calibrated)"},
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
