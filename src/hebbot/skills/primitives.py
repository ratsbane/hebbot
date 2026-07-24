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
    _frame_tag: str = "000",
    _t: float = 0.0,
    _capture: Optional[Any] = None,
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

    # Internal experiment callers can persist and classify the exact same capture. Without this,
    # saving one frame and immediately capturing another for verification can produce a label that
    # disagrees with the retained evidence.
    cap = _capture if _capture is not None else cam.capture(with_depth=with_depth)
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

        # Internal callers may save several checkpoints into one episode. Keep the public
        # read_camera contract simple; `_frame_tag` and `_t` are substrate-internal kwargs.
        safe_tag = "".join(c for c in str(_frame_tag) if c.isalnum() or c in ("-", "_")) or "000"
        color_rel = f"{rel_dir}/{cam.name}_{safe_tag}.jpg"
        # capture() returns BGR; flip to RGB so the saved JPEG has true colors.
        Image.fromarray(cap["color"][:, :, ::-1]).save(data_root / color_rel, quality=92)
        _store.record_frame(_episode_id, camera=cam.name, t=float(_t),
                            file_path=color_rel, width=w, height=h)
        result["color_path"] = color_rel

        if with_depth and cap["depth"] is not None:
            depth_rel = f"{rel_dir}/{cam.name}_{safe_tag}_depth.npy"
            np.save(data_root / depth_rel, cap["depth"])  # lossless uint16
            _store.record_frame(_episode_id, camera=f"{cam.name}:depth", t=float(_t),
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


def compliant_hold(
    *,
    duration_s: float = 2.0,
    frequency: float = 100.0,
    _context: Optional[Any] = None,
) -> dict:
    """Hold the current Cartesian pose with the grasp controller, without translating.

    Diagnostic for the Franka FCI/controller boundary. It deliberately excludes perception,
    gripper commands, descent, and contact so a failure localizes to controller startup or the
    real-time command loop. The target is the measured start pose throughout.
    """
    panda = _extract(_context, "panda")
    if panda is None:
        time.sleep(min(float(duration_s), 0.05))
        return {
            "_source": "mock", "completed": True, "cycles": 0,
            "duration_s": float(duration_s), "max_position_drift_m": 0.0,
        }

    import numpy as np
    from panda_py import controllers

    duration_s = float(np.clip(duration_s, 0.25, 5.0))
    frequency = float(np.clip(frequency, 50.0, 250.0))
    target = np.array(panda.get_position(), dtype=float)
    quat = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 300.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    cycles = 0
    max_drift = 0.0
    started = time.monotonic()
    try:
        with panda.create_context(
            frequency=frequency, max_runtime=duration_s + 1.0,
        ) as cx:
            while cx.ok() and time.monotonic() - started < duration_s:
                ctrl.set_control(target, quat, q0)
                cycles += 1
                drift = float(np.linalg.norm(np.array(panda.get_position()) - target))
                max_drift = max(max_drift, drift)
    finally:
        panda.stop_controller()
    elapsed = time.monotonic() - started
    return {
        "_source": "live", "completed": elapsed >= 0.9 * duration_s,
        "cycles": cycles, "duration_s": round(elapsed, 3),
        "frequency_hz": frequency, "max_position_drift_m": max_drift,
        "final_position": [float(v) for v in panda.get_position()],
    }


def _descent_context_runtime(start_z, target_z, descent_speed, settle_s=0.4):
    """Runtime budget for a commanded descent, including settle and scheduling margin."""
    travel_s = max(0.0, float(start_z) - float(target_z)) / max(float(descent_speed), 1e-4)
    return max(2.0, travel_s + float(settle_s) + 1.0)


def grasp_at(
    *,
    grasp_z: float = 0.038,
    width: float = 0.024,
    force: float = _DEFAULT_GRIP_FORCE,
    descent_speed: float = 0.04,
    stall_guard: float = 0.068,
    open_width: Optional[float] = None,
    grip_depth_tol: float = 0.006,
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
    # Hold the current orientation, with the calibrated finger-leveling offset applied (in the
    # gripper's local frame, so it's correct at any yaw) — both fingertips meet the block level
    # instead of one tapping first and nudging/stalling it high (Doug, 2026-06-27).
    quat = _level_quat(panda.get_orientation())
    q0 = np.array(panda.get_state().q)

    dt = 0.01

    def _descend_to(z_target):
        """Compliant descent of the EE straight down to z_target (or until it stalls there)."""
        tz = float(panda.get_position()[2])
        settle = 0
        ctrl = controllers.CartesianImpedance()
        ctrl.set_impedance(np.diag([400.0, 400.0, 300.0, 30.0, 30.0, 30.0]))
        ctrl.set_nullspace_stiffness(1.0)
        runtime = _descent_context_runtime(tz, z_target, descent_speed)
        panda.start_controller(ctrl)
        try:
            with panda.create_context(frequency=100, max_runtime=runtime) as cx:
                while cx.ok():
                    tz = max(z_target, tz - descent_speed * dt)
                    ctrl.set_control(np.array([X, Y, tz]), quat, q0)
                    if tz <= z_target + 1e-4:
                        settle += 1
                        if settle > 40:        # ~0.4 s settle at target
                            break
        finally:
            # Never leave an arm controller active across the blocking Franka Hand command
            # below. Each local inch-down retry starts a fresh, fully bounded context.
            panda.stop_controller()

    # Descend and close; if the jaws meet on AIR (no block sensed — the grip was a touch too
    # high), don't lift and re-servo: just REOPEN and inch the EE down a few mm right here, then
    # close again in place (Doug, 2026-06-28). A few local steps before deferring to the caller's
    # full re-acquire. `grasp()` returns False on an air-close (final width ~0, not near `width`).
    grasped = False
    actual_z = float(panda.get_position()[2])
    z_target = grasp_z
    air_retries, depth_retries = 0, 0     # telemetry: in-place inch-downs by cause
    for _local in range(4):
        _descend_to(z_target)
        actual_z = float(panda.get_position()[2])
        if actual_z > stall_guard:        # fingertip resting ON the top — never reached close
            break                         # depth; let the caller lift + re-perceive (mis-centred)
        try:
            grasped = gripper.grasp(width=width, speed=0.1, force=force,
                                    epsilon_inner=0.018, epsilon_outer=0.018)
        except Exception:
            grasped = float(gripper.read_once().width) > 0.003   # already holding
        caught = bool(grasped) or float(gripper.read_once().width) > 0.006
        if caught:
            # GRIP-DEPTH GATE (lab #147): a CAUGHT grip is only good enough if it seated low on
            # the body. One that stalled high engages just the top few mm of the side — it
            # holds but almost slips (Doug, 2026-06-28). If it closed more than grip_depth_tol
            # above the intended height, reopen and inch down for a deeper grip; accept on the
            # last local step regardless (a marginal grip beats dropping the block).
            if actual_z <= grasp_z + grip_depth_tol or _local == 3:
                grasped = True
                break
            depth_retries += 1
            gripper.move(width=ow, speed=0.1)
            z_target = max(0.012, z_target - 0.007)
            continue
        # closed on air → reopen and step ~7 mm lower, then retry the close in place
        air_retries += 1
        gripper.move(width=ow, speed=0.1)
        z_target = max(0.012, z_target - 0.007)

    if not grasped and actual_z > stall_guard:
        return {"_source": "live", "grasped": False, "stalled_high": True,
                "actual_z": actual_z, "air_retries": air_retries, "depth_retries": depth_retries,
                "note": f"stalled at {actual_z:.4f} m (fingertip on object top) — not closing"}
    return {"_source": "live", "grasped": bool(grasped), "actual_z": actual_z,
            "actual_width": float(gripper.read_once().width),
            "air_retries": air_retries, "depth_retries": depth_retries,
            "grip_high_mm": round(max(0.0, actual_z - grasp_z) * 1000, 1)}


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


def _held_color_evidence(bgr, color, min_px=200):
    """Color evidence in the wrist's near-gripper region after a lift.

    A held object is very close to the D405, clipped by the lower image edge, and substantially
    darker than the same object on the illuminated table. Keep this verifier-specific adaptation
    out of the motion segmentation masks. Returns (u, v, npix, cue), or None.
    """
    import numpy as np

    h, w = bgr.shape[:2]
    track = [w * 0.5, h * 0.65]
    normal = _nearest_instance(bgr, color, track, min_px=min_px)
    if normal is not None:
        return (*normal, "table_calibrated_color")
    if color != "red":
        return None

    rgb = bgr[:, :, ::-1].astype(int)
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # The successful held red rectangle read around RGB=(99,36,28), versus R>115 on-table.
    # Restrict the relaxed cue to the central/lower gripper region so it cannot steer motion or
    # accept reddish table shadows elsewhere in the scene.
    yy, xx = np.mgrid[0:h, 0:w]
    roi = ((yy >= int(0.55 * h)) & (xx >= int(0.15 * w)) & (xx < int(0.85 * w)))
    mask = roi & (R > 70) & (R - G > 25) & (R - B > 25)
    lab = _label_cc(mask)
    best = None
    for label in np.unique(lab):
        if label == 0:
            continue
        ys, xs = np.where(lab == label)
        if len(xs) < min_px:
            continue
        candidate = (float(xs.mean()), float(ys.mean()), int(len(xs)), "held_dim_red")
        if best is None or candidate[2] > best[2]:
            best = candidate
    return best


def _nearest_raised_instance(cap, color, track, min_px=80, min_height_m=0.008):
    """Nearest color-seeded, connected depth object above its local table.

    RGB shadows/reflections can satisfy the calibrated color masks, but their depth is the
    surrounding table depth. Color therefore only identifies a raised seed. Around that seed,
    connected aligned-depth foreground supplies the complete object and its highest (top-face)
    centroid. This avoids hopping between disconnected color patches on the same physical block.
    Returns (u, v, npix, height_m), or None when depth cannot validate a stable object.
    """
    import numpy as np

    bgr, depth = cap.get("color"), cap.get("depth")
    scale = float(cap.get("depth_scale") or 0.0)
    if bgr is None or depth is None or scale <= 0.0:
        return None
    rgb = bgr[:, :, ::-1].astype(int)
    lab = _label_cc(_color_mask(rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2], color))
    dm = depth.astype(np.float32) * scale
    yy, xx = np.mgrid[0:dm.shape[0], 0:dm.shape[1]]
    seeds = []
    for label in np.unique(lab):
        if label == 0:
            continue
        ys, xs = np.where(lab == label)
        if len(xs) < min_px:
            continue
        vals = dm[ys, xs]
        valid = vals > 0.05
        if int(valid.sum()) < max(20, int(0.25 * len(xs))):
            continue
        cu, cv = float(xs.mean()), float(ys.mean())
        rad = max(4.0, float(np.sqrt(len(xs) / np.pi)))
        rr = np.hypot(xx - cu, yy - cv)
        ring = ((rr > 1.4 * rad) & (rr < 3.2 * rad)
                & (lab == 0) & (dm > 0.05))
        if int(ring.sum()) < 40:
            continue
        object_depth = float(np.median(vals[valid]))
        table_depth = float(np.median(dm[ring]))
        height = table_depth - object_depth
        if not (min_height_m <= height <= 0.10):
            continue
        d = (cu - track[0]) ** 2 + (cv - track[1]) ** 2
        seeds.append({
            "d": d, "xs": xs, "ys": ys, "cu": cu, "cv": cv,
            "table_depth": table_depth,
        })
    if not seeds:
        return None

    seed = min(seeds, key=lambda s: s["d"])
    xs, ys = seed["xs"], seed["ys"]
    table_depth = float(seed["table_depth"])

    # Work in a bounded ROI: large enough to include the whole nearby block when the color
    # threshold sees only its top or side, but not other blocks distributed across the table.
    span = max(int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
    pad = int(np.clip(2 * span, 45, 120))
    x0, x1 = max(0, int(xs.min()) - pad), min(dm.shape[1], int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(dm.shape[0], int(ys.max()) + pad + 1)
    roi_depth = dm[y0:y1, x0:x1]
    # A block lies 8--80 mm above its local table. Excluding taller foreground keeps the
    # wrist/gripper out of the object mask during close views.
    heights = table_depth - roi_depth
    raised_mask = ((roi_depth > 0.05)
                   & (heights >= min_height_m)
                   & (heights <= 0.080))
    depth_lab = _label_cc(raised_mask)

    seed_mask = np.zeros_like(raised_mask, dtype=bool)
    seed_mask[ys - y0, xs - x0] = True
    best_label, best_overlap = None, 0
    for depth_label in np.unique(depth_lab):
        if depth_label == 0:
            continue
        overlap = int(np.count_nonzero((depth_lab == depth_label) & seed_mask))
        if overlap > best_overlap:
            best_label, best_overlap = depth_label, overlap
    if best_label is None or best_overlap < 10:
        return None

    oy, ox = np.where(depth_lab == best_label)
    if len(ox) < min_px:
        return None
    object_heights = heights[oy, ox]
    top_height = float(np.percentile(object_heights, 90))
    top = object_heights >= top_height - 0.010
    if int(top.sum()) < 20:
        return None
    u = float((ox[top] + x0).mean())
    v = float((oy[top] + y0).mean())
    return (u, v, int(len(ox)), top_height)


def _reacquire_raised_instance(
    cam, color, track, min_px=80, min_height_m=0.008, max_retries=2,
):
    """Capture a raised object, holding still across a bounded transient dropout.

    The caller performs no motion while this helper captures the initial frame plus at most
    `max_retries` replacements. Each frame must independently satisfy the depth-object gate.
    Returns (observation_or_none, rejected_frame_count).
    """
    rejected = 0
    for _ in range(max_retries + 1):
        raised = _nearest_raised_instance(
            cam.capture(with_depth=True), color, track,
            min_px=min_px, min_height_m=min_height_m,
        )
        if raised is not None:
            return raised, rejected
        rejected += 1
    return None, rejected


# EE z at which the fingertips touch the table — the base for turning a height above the table into
# a grasp_z: grasp_z = _TABLE_EE_Z + (desired fingertip height). DIRECTLY MEASURED by probe_table
# (the open fingers pressed to a bare table → contact_z ≈ 0.016, repeatably). The old 0.047 was a
# grasp-derived guess and ~31 mm too high, which made every height built ON this constant aim 3 cm
# high — the standing-block top-edge grip, the unstack elevated-grip knock, and the topple sailing
# over the top (Doug confirmed each, 2026-06-29/30). Normal lying grasps don't use this (they use
# grasp_z 0.026 directly ≈ 0.016 + 10 mm, already correct), which is why only the tall/elevated/
# topple paths were off.
_TABLE_EE_Z = 0.016
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


def _grasp_relational_state(color, geom, ee_position, pick_top=0.0):
    """Compact geometry + gripper/object relation for the first retrieval experiment."""
    if geom is None:
        return None
    ee = [float(x) for x in ee_position[:3]]
    d = max(0.05, ee[2] - float(geom["height_m"]) - float(pick_top))
    gu, gv = _vg_grasp_pixel(d)
    return {
        "color": str(color),
        "object": {
            "length_m": round(float(geom["length_m"]), 5),
            "width_m": round(float(geom["width_m"]), 5),
            "height_m": round(float(geom["height_m"]), 5),
            "angle_deg": round(float(geom["ang"]), 2),
        },
        "gripper": {"ee_xyz": [round(v, 5) for v in ee]},
        "relative": {
            "du_px": round(float(geom["u"]) - gu, 2),
            "dv_px": round(float(geom["v"]) - gv, 2),
            "pick_top_m": round(float(pick_top), 5),
        },
    }


def _grasp_state_similarity(a, b):
    """Similarity in [0,1] using scales for the narrow rectangular-block experiment."""
    if not a or not b or a.get("color") != b.get("color"):
        return 0.0
    import math

    ao, bo = a["object"], b["object"]
    ar, br = a["relative"], b["relative"]
    ae, be = a["gripper"]["ee_xyz"], b["gripper"]["ee_xyz"]
    angle = abs(float(ao["angle_deg"]) - float(bo["angle_deg"])) % 180.0
    angle = min(angle, 180.0 - angle)
    terms = [
        (float(ao["length_m"]) - float(bo["length_m"])) / 0.020,
        (float(ao["width_m"]) - float(bo["width_m"])) / 0.015,
        (float(ao["height_m"]) - float(bo["height_m"])) / 0.020,
        angle / 45.0,
        (float(ar["du_px"]) - float(br["du_px"])) / 35.0,
        (float(ar["dv_px"]) - float(br["dv_px"])) / 35.0,
        (float(ar["pick_top_m"]) - float(br["pick_top_m"])) / 0.030,
        (float(ae[0]) - float(be[0])) / 0.150,
        (float(ae[1]) - float(be[1])) / 0.150,
    ]
    rms = math.sqrt(sum(t * t for t in terms) / len(terms))
    return float(math.exp(-rms))


def _recall_grasp_experiences(store, state, limit=5):
    """Rank task-verified vision_grasp episodes by relational-state similarity."""
    if store is None or state is None:
        return []
    ranked = []
    for ep in store.recent_episodes(skill_name="vision_grasp", limit=200):
        outcome = ep.get("outcome") or {}
        result = outcome.get("result") or {}
        exp = result.get("experience") or {}
        if result.get("task_verified_success") is not True:
            continue
        prior_state = exp.get("relational_state")
        action = exp.get("action")
        if not prior_state or not action:
            continue
        ranked.append({
            "episode_id": ep["id"],
            "relational_similarity": round(_grasp_state_similarity(state, prior_state), 4),
            "action": action,
            "verification_method": result.get("verification_method"),
        })
    ranked.sort(key=lambda x: x["relational_similarity"], reverse=True)
    return ranked[:limit]


def _record_state_snapshot(store, episode_id, context, t):
    if store is None or episode_id is None:
        return None
    state = get_state(context)
    if state.get("_source") != "live":
        return state
    store.record_state(
        episode_id,
        t=float(t),
        joints=state.get("joints"),
        ee_pose=state.get("ee_pose"),
        force=state.get("force"),
        gripper_width=state.get("gripper_width"),
    )
    return state


def _center_over(cam, color, u0, v0, _context, tol=6.0, steps=6):
    """Image-Jacobian servo to bring the wrist over the block at image (u0, v0), holding the
    current height. Re-finds the instance each step (small footprint when standing). Used by the
    scene survey to position for a tilt-glance."""
    import numpy as np, math
    panda = _extract(_context, "panda")
    track = [u0, v0]
    for _ in range(steps):
        seg = _nearest_instance(cam.capture(with_depth=False)["color"], color, track)
        if seg is None:
            break
        u, v, _npx = seg
        track = [u, v]
        z = float(panda.get_position()[2])
        tu, tv = _vg_grasp_pixel(z)
        du, dv = tu - u, tv - v
        if math.hypot(du, dv) <= tol:
            break
        ji = _vg_jinv(z)
        dex = float(np.clip(ji[0][0] * du + ji[0][1] * dv, -0.12, 0.12))
        dey = float(np.clip(ji[1][0] * du + ji[1][1] * dv, -0.12, 0.12))
        p = panda.get_position()
        move_to_pose([float(p[0]) + dex, float(p[1]) + dey, z], _context=_context)


def _pose_by_tilt(cam, color, bx, by, _context, base_z=0.30):
    """Side-glance pose classification (the disambiguator promoted from vision_grasp's
    _tilt_classify, lab #138): from a 35° tilt beside the block, a STANDING block's colour
    silhouette is taller than it is wide. Assumes the wrist is roughly over the block (bx, by).
    Returns 'standing'|'lying'|None and leaves the wrist back over the block."""
    import numpy as np
    cls = None
    try:
        move_to_pose([bx + 0.16, by, 0.28], orientation=[0.0, 0.954, 0.0, -0.301], _context=_context)
        rgb = cam.capture(with_depth=False)["color"][:, :, ::-1].astype(int)
        m = _color_mask(rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2], color)
        ys, xs = np.where(m)
        if len(xs) > 60:                       # side silhouette: standing → taller than wide
            vh, uw = (ys.max() - ys.min()), (xs.max() - xs.min())
            cls = "standing" if vh >= 1.15 * uw else "lying"
    except Exception:
        cls = None
    move_to_pose([bx, by, base_z], _context=_context)   # back over the block
    return cls


def survey_scene(
    *,
    colors: Optional[list] = None,
    camera: Optional[str] = None,
    tilt_confirm: bool = True,
    _context: Optional[Any] = None,
) -> dict:
    """Scene-level side-view perception (docs/planning.md gap #1): classify every block as LYING
    or STANDING. From the overhead observe pose, measure each block's depth height above the table
    (lying ≈ one thickness ~28 mm, standing ≈ one length ~65 mm); for heights in the AMBIGUOUS band
    — or where the depth is low-coverage (dark blue reads poorly) — drop a tilt-glance side view to
    resolve it (the call a single top-down view cannot make, lab #138). This promotes the
    grasp-time tilt-glance to a first-class scene primitive the planner can consult before acting.

    Returns {blocks: [{color, u, v, height_m, pose, method}], n}, pose ∈ lying|standing|unknown.

    MULTI-INSTANCE (2026-07-04): returns EVERY real block instance (not one per colour), so a scene
    with several same-colour blocks — e.g. Elek's 8-on-end — is fully enumerated. Pose per instance
    by footprint+depth (the tilt-glance was unreliable on dark/edge blocks, #150). Limitation:
    same-colour blocks that TOUCH merge into one connected blob and undercount.
    """
    panda = _extract(_context, "panda")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "blocks": [], "n": 0}
    cam = _select_camera(cameras, camera)
    colors = colors or list(_SEGMENT_COLORS)

    move_to_pose(list(_OBSERVE_POSE), _context=_context)
    fb = find_blocks(colors=colors, _context=_context)
    # MULTI-INSTANCE: keep EVERY real block blob, not just the largest per colour — the scene may
    # hold several blocks of one colour (only 3 colours, but e.g. Elek's 8-on-end, 2026-07-04). Drop
    # tiny blobs + frame-edge slivers (find_blocks artifacts), then dedupe near-coincident detections
    # of the same instance. (Limitation: same-colour blocks that TOUCH merge into one connected blob
    # → undercount; separating them needs depth/marker splitting, a later refinement.)
    cand = [b for b in fb.get("blocks", [])
            if b["npix"] >= 300 and 12 < b["u"] < 628 and 12 < b["v"] < 468]
    cand.sort(key=lambda b: -b["npix"])
    insts: list = []
    for b in cand:
        if any(o["color"] == b["color"] and (o["u"] - b["u"]) ** 2 + (o["v"] - b["v"]) ** 2 < 35 ** 2
               for o in insts):
            continue                                  # duplicate detection of an instance already kept
        insts.append(b)

    # Pose from FOOTPRINT + DEPTH together — the tilt-glance silhouette proved unreliable on the
    # dark blue block and edge slivers (2026-06-30). Two robust signals:
    #  • A big top area can only be a block lying on its broad face (standing shows just the small
    #    end face), so npix ≥ LYING_NPIX ⇒ lying, regardless of how elevated it is (a block lying ON
    #    another is still lying — the case that fooled depth-only).
    #  • For a SMALL footprint (standing, or an edge/occluded/partial block), an elevated top face
    #    (depth height ≥ STAND_H, clearly above one lying block's ~28 mm) means standing; otherwise
    #    it's a low sliver → lying. (Depth under-reads standing blocks — blue read 43 mm for a ~65 mm
    #    upright — so STAND_H sits low, between a lying block and an under-read upright.)
    LYING_NPIX = 1100
    STAND_H = 0.036
    out = []
    for b in insts:
        color, npix = b["color"], b["npix"]
        geom = _perceive_pose_3d(cam, color, [b["u"], b["v"]], _context=_context)
        h = round(geom["height_m"], 4) if geom else None
        if npix >= LYING_NPIX:
            pose, method = "lying", "footprint"
        elif h is not None:
            pose, method = ("standing" if h >= STAND_H else "lying"), "footprint+depth"
        else:
            pose, method = "lying", "footprint_small"          # tiny unmeasurable blob → occluded lying
        out.append({"color": color, "u": round(b["u"], 1), "v": round(b["v"], 1),
                    "ang": b.get("ang"), "aspect": b.get("aspect"),
                    "length_m": round(geom["length_m"], 4) if geom else None,
                    "width_m": round(geom["width_m"], 4) if geom else None,
                    "npix": npix, "height_m": h, "pose": pose, "method": method})

    move_to_pose(list(_OBSERVE_POSE), _context=_context)
    return {"_source": "live", "blocks": out, "n": len(out)}


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
    experience_mode: str = "off",
    replay_threshold: float = 0.82,
    speed_factor: float = 0.1,
    _context: Optional[Any] = None,
    _store: Optional[Any] = None,
    _episode_id: Optional[str] = None,
    _log_decision: Optional[Any] = None,
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
        experience_mode: "off" (legacy behavior), "record" (save a verified example), or
            "replay" (retrieve and reuse parameters from a similar verified example).
        speed_factor: point-to-point motion speed; experimental calls are capped at 0.05.
    """
    if experience_mode not in ("off", "record", "replay"):
        return {"_source": "error", "grasped": None,
                "note": f"experience_mode must be off|record|replay, got {experience_mode!r}"}
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

    experiment = experience_mode != "off"
    motion_speed = min(float(speed_factor), 0.05) if experiment else float(speed_factor)
    started = time.monotonic()
    initial_capture = None
    if experiment:
        _record_state_snapshot(_store, _episode_id, _context, 0.0)
        try:
            initial_capture = read_camera(
                camera=camera, with_depth=True, save=True, _context=_context,
                _store=_store, _episode_id=_episode_id, _frame_tag="initial", _t=0.0,
            )
        except Exception:
            initial_capture = None

    # Rise to the acquisition height (preserving xy) for the widest catch basin.
    pos = panda.get_position()
    z = float(np.clip(acquire_z, approach_z, 0.45))
    move_to_pose([float(pos[0]), float(pos[1]), z], speed_factor=motion_speed,
                 _context=_context)

    import math
    history: list[dict] = []
    located = False
    track = list(target) if target else None   # lock onto one instance when given
    servo_stop_reason = None

    def _coarse_servo():
        """Colour-centroid image-Jacobian servo from acquire_z down to approach_z over the
        tracked instance — a coarse centre. Returns the last (u, v) pixel, or None if lost."""
        nonlocal track, located, servo_stop_reason
        z = float(np.clip(acquire_z, approach_z, 0.45))
        p0 = panda.get_position()
        move_to_pose([float(p0[0]), float(p0[1]), z], speed_factor=motion_speed,
                     _context=_context)
        last = None
        for step in range(max_steps):
            if track:
                # Hold position through a single-frame RealSense dropout. The bounded two-frame
                # reacquisition is perception-only: no command is sent until a fresh observation
                # independently passes the same raised-object gate.
                raised, depth_rejections = _reacquire_raised_instance(
                    cam, color, track, min_px=600, max_retries=2,
                )
                seg = raised[:3] if raised is not None else None
                depth_height = raised[3] if raised is not None else None
            else:
                cap = cam.capture(with_depth=False)
                seg = _segment(cap["color"], color)
                depth_height = None
                depth_rejections = 0
            if seg is None:
                history.append({
                    "step": step, "z": round(z, 3), "event": "depth_reacquire_failed",
                    "rejected_frames": depth_rejections,
                })
                servo_stop_reason = (
                    "target lost or rejected by aligned-depth gate after "
                    f"{depth_rejections} stationary frames"
                )
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
                            "v": round(v, 1), "pixel_err": round(err, 1), "npix": npix,
                            "depth_height_m": (
                                round(float(depth_height), 4)
                                if depth_height is not None else None
                            ),
                            "depth_rejected_frames": depth_rejections})
            last = (u, v)
            if at_bottom and err <= pixel_tol:
                servo_stop_reason = "converged"
                return last
            jinv = _vg_jinv(d)
            # The calibrated full-step Jacobian is well behaved in the overview view, but at
            # z<=0.25 m the D405 perspective + controller settle caused alternating overshoot
            # across the target (2026-07-24 depth-gated baseline). Dampen only the close loop.
            servo_gain = 1.0 if z >= 0.29 else 0.35
            history[-1]["servo_gain"] = servo_gain
            dex = servo_gain * (jinv[0][0] * du + jinv[0][1] * dv)
            dey = servo_gain * (jinv[1][0] * du + jinv[1][1] * dv)
            clamp = 0.06 if track is not None else 0.15
            dex = float(np.clip(dex, -clamp, clamp))
            dey = float(np.clip(dey, -clamp, clamp))
            if err <= descend_tol and not at_bottom:
                z = max(approach_z, z - z_step)
            p = panda.get_position()
            tx = float(np.clip(float(p[0]) + dex, 0.30, 0.60))
            ty = float(np.clip(float(p[1]) + dey, -0.28, 0.28))
            move_to_pose([tx, ty, z], speed_factor=motion_speed, _context=_context)
        servo_stop_reason = (
            f"coarse servo did not converge: final error {err:.1f}px at z={z:.3f}m"
        )
        return None

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
        tx = float(np.clip(float(p[0]) + dex, 0.30, 0.60))
        ty = float(np.clip(float(p[1]) + dey, -0.28, 0.28))
        move_to_pose([tx, ty, pz],
                     speed_factor=motion_speed, _context=_context)
        track = [u, v]

    def _tilt_classify():
        """Piece 3 — 'back off and view from another angle': a 35° side glance that resolves
        the standing-vs-lying ambiguity a single top-down view cannot (it fooled me 3× in one
        session). Returns 'standing'|'lying'|None and leaves the wrist back over the block."""
        p = panda.get_position(); bx, by, bz = float(p[0]), float(p[1]), float(p[2])
        cls = None
        try:
            move_to_pose([bx + 0.16, by, 0.28], orientation=[0.0, 0.954, 0.0, -0.301],
                         speed_factor=motion_speed, _context=_context)
            rgb = cam.capture(with_depth=False)["color"][:, :, ::-1].astype(int)
            m = _color_mask(rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2], color)
            ys, xs = np.where(m)
            if len(xs) > 60:                   # side silhouette: standing → taller than wide
                vh, uw = (ys.max() - ys.min()), (xs.max() - xs.min())
                cls = "standing" if vh >= 1.15 * uw else "lying"
        except Exception:
            cls = None
        move_to_pose([bx, by, bz], speed_factor=motion_speed,
                     _context=_context)   # back over the block, same height
        return cls

    result = None
    lower_bias = 0.0          # each missed grasp biases the next one lower (Doug: a miss is
    experience_state = None
    experience_candidates = []
    selected_experience = None
    decision_logged = False
    before_capture = None
    for attempt in range(max_retry + 1):   # usually 'gripped too high' → open + go down + retry)
        if _coarse_servo() is None:
            result = {"_source": "live", "located": located, "grasped": False,
                      "note": servo_stop_reason or "block not found in frame",
                      "servo_steps": len(history),
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
            move_to_pose([float(p[0]), float(p[1]), pz], speed_factor=motion_speed,
                         _context=_context)
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
            # Open just enough to clear the target's top (a few mm margin), NOT fully — a full
            # open reaches into an ADJACENT block and a tip gets blocked by the neighbour (Doug,
            # 2026-06-28, blue touching green). The too-high cure was the lower grip start below,
            # not the open width. Truly touching blocks still need a clutter-aware grip axis.
            open_w = float(np.clip(grip_w + 0.016, 0.035, 0.065))
            # Grip height. grasp_z=0.038 lands the fingertips ~22 mm up — near the TOP of a ~29 mm
            # block, not its body — so the first attempt grips 'just above the block' and the
            # −12 mm lower-bias retry rescues it (Doug's chronic 'grasps too high', 2026-06-28).
            # Start 12 mm lower so the FIRST attempt grips the body. A clearly tall standing block
            # (can't reach the table) is still gripped low on its body.
            if H >= 0.050:
                g_z = _TABLE_EE_Z + float(np.clip(0.40 * H, 0.010, 0.040)) - lower_bias
                s_guard = g_z + 0.035
            else:
                g_z = grasp_z - 0.012 - lower_bias
                s_guard = 0.072
            g_z = max(0.018, g_z)
            if pick_top > 0.0:
                # ELEVATED pick (unstacking a tower): the block sits on a surface `pick_top` above
                # the table, so the depth height reads it relative to the block BELOW it (~one
                # block = 'lying'), which picks a table-level grip and drives the fingers DOWN into
                # the lower block — knocking the stack over (Doug, 2026-06-28: "knocked green off
                # red while picking it up"). Floor the grip to the block's known elevated body and
                # keep the descent ABOVE the block below.
                g_z = max(g_z, _TABLE_EE_Z + pick_top + 0.014)   # mid-body (≈ +half-thickness)
                s_guard = max(s_guard, _TABLE_EE_Z + pick_top + 0.05)

            # First Hebbian-compression experiment: record the current relational state and, in
            # replay mode, let the nearest TASK-VERIFIED episode choose the grasp parameters.
            # The existing geometry heuristic remains the baseline and safe fallback.
            replay_yaw = None
            if experiment:
                experience_state = _grasp_relational_state(
                    color, geom, panda.get_position(), pick_top=pick_top,
                )
                experience_candidates = _recall_grasp_experiences(
                    _store, experience_state, limit=5,
                )
                if (experience_mode == "replay" and experience_candidates
                        and experience_candidates[0]["relational_similarity"] >= replay_threshold):
                    selected_experience = experience_candidates[0]
                    action = selected_experience["action"]
                    # Hard safety bounds: replay may specialize within the existing primitive's
                    # envelope, never expand it.
                    g_z = float(np.clip(action["grip_z"], 0.018, 0.12))
                    grip_w = float(np.clip(action["grip_width"], 0.010, 0.05))
                    open_w = float(np.clip(action.get("open_width", grip_w + 0.016),
                                           grip_w + 0.004, 0.065))
                    if action.get("grip_yaw_deg") is not None:
                        replay_yaw = math.radians(float(action["grip_yaw_deg"]))
                    s_guard = max(s_guard, g_z + 0.025)
                if not decision_logged and _log_decision is not None:
                    if selected_experience is not None:
                        _log_decision(
                            "replay",
                            reasoning=(
                                f"nearest verified grasp "
                                f"{selected_experience['relational_similarity']:.3f} "
                                f">= threshold {replay_threshold:.3f}"
                            ),
                            relational_state=experience_state,
                            candidates=experience_candidates,
                            threshold_values={"replay": replay_threshold},
                        )
                    else:
                        why = ("record-only baseline" if experience_mode == "record"
                               else "no verified candidate exceeded replay threshold")
                        _log_decision(
                            "orchestrator",
                            reasoning=why,
                            relational_state=experience_state,
                            candidates=experience_candidates,
                            threshold_values={"replay": replay_threshold},
                        )
                    decision_logged = True
                if before_capture is None:
                    _record_state_snapshot(
                        _store, _episode_id, _context, time.monotonic() - started,
                    )
                    try:
                        before_capture = read_camera(
                            camera=camera, with_depth=True, save=True, _context=_context,
                            _store=_store, _episode_id=_episode_id,
                            _frame_tag=f"before_close_{attempt}",
                            _t=time.monotonic() - started,
                        )
                    except Exception:
                        before_capture = None

            # Piece 2: fingers parallel to the true long edges (across the short axis), using
            # the STABLE depth principal axis. Skip the twist for near-square tops (cubes).
            if geom["length_m"] / max(geom["width_m"], 1e-3) >= 1.25:
                if replay_yaw is not None:
                    phi = replay_yaw
                else:
                    phi = math.atan2(-math.cos(math.radians(geom["ang"])),
                                     -math.sin(math.radians(geom["ang"])))
                    phi = ((phi + math.pi / 2) % math.pi) - math.pi / 2   # gripper symmetry
                p = panda.get_position()

                def _apply_yaw(yaw):
                    qq = _qmul([math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)], [0.0, 1.0, 0.0, 0.0])
                    r = move_to_pose([float(p[0]), float(p[1]), pz], orientation=qq,
                                     speed_factor=motion_speed,
                                     _context=_context)
                    return (r.get("ik_solution_q") or [0.0] * 7)[6]   # wrist joint 7

                try:
                    q7 = _apply_yaw(phi)
                    grip_yaw = round(math.degrees(phi))
                    # Exploit the gripper's 180° symmetry: yaw φ and φ±π are the IDENTICAL grasp, but
                    # at the workspace edge the arm's azimuth forces a big counter-rotation, driving
                    # wrist joint 7 near its ±2.9 rad limit — an awkward over-twist (Doug, 2026-06-28,
                    # green at the far right). The symmetric grasp lands joint 7 ~π away; take it when
                    # it's more comfortable (smaller |q7|), reverting if it isn't.
                    if abs(q7) > 2.5:
                        alt = phi - math.copysign(math.pi, phi)
                        if abs(_apply_yaw(alt)) >= abs(q7):
                            _apply_yaw(phi)              # original was better — revert
                except Exception:
                    grip_yaw = None

        # Open just past the target's width (open_w) — enough clearance to descend without a tip
        # clipping the block top, but not so wide it reaches into an adjacent block. The lower grip
        # start (above) is what cured 'grasps too high'; the Franka jaws close symmetrically so the
        # pre-open never affected squirt (the parallel+levelled+centred grip handles that).
        g = grasp_at(grasp_z=g_z, width=grip_w, stall_guard=s_guard,
                     descent_speed=(0.02 if experiment else 0.04),
                     open_width=(open_w if geom is not None else None), _context=_context)
        result = {"_source": "live", "located": located, "servo_steps": len(history),
                  "final_pixel_err": history[-1]["pixel_err"] if history else None,
                  "grip_yaw_deg": grip_yaw, "attempt": attempt,
                  "geom": ({k: round(float(val), 4) for k, val in geom.items()} if geom else None),
                  "grip_z": round(g_z, 4), "grip_width": round(grip_w, 4),
                  "open_width": round(open_w, 4) if geom is not None else None,
                  "history": history, "grasped": g.get("grasped"),
                  "actual_z": g.get("actual_z"), "actual_width": g.get("actual_width"),
                  "air_retries": g.get("air_retries"), "depth_retries": g.get("depth_retries"),
                  "grip_high_mm": g.get("grip_high_mm")}

        # Piece 4: verify + retry. A failed grasp usually nudged the block, so lift, re-acquire
        # near the last sighting, and try again.
        if g.get("grasped"):
            break
        if attempt < max_retry:
            lower_bias += 0.012          # open + go a notch lower on the next try
            p = panda.get_position()
            move_to_pose([float(p[0]), float(p[1]), acquire_z],
                         speed_factor=motion_speed, _context=_context)
            if track is not None:
                raised = _nearest_raised_instance(
                    cam.capture(with_depth=True), color, track, min_px=600,
                )
                if raised is not None:
                    track = [raised[0], raised[1]]

    if experiment and result is not None:
        result["mechanical_success"] = bool(result.get("grasped"))
        width_ok = bool((result.get("actual_width") or 0.0) > 0.006)
        result["proprioceptive_success"] = bool(result.get("grasped") and width_ok)
        visual = None
        after_capture = None
        if result.get("grasped"):
            # Lift before visual verification. This makes "still holding after lift" a stronger
            # label than gripper.grasp() alone while keeping the motion vertical and slow.
            p = panda.get_position()
            lift_z = float(np.clip(float(p[2]) + 0.12, 0.16, min(acquire_z, 0.36)))
            move_to_pose([float(p[0]), float(p[1]), lift_z], speed_factor=motion_speed,
                         _context=_context)
            _record_state_snapshot(
                _store, _episode_id, _context, time.monotonic() - started,
            )
            verification_cap = None
            try:
                verification_cap = cam.capture(with_depth=True)
                after_capture = read_camera(
                    camera=camera, with_depth=True, save=True, _context=_context,
                    _store=_store, _episode_id=_episode_id, _frame_tag="after_lift",
                    _t=time.monotonic() - started,
                    _capture=verification_cap,
                )
            except Exception:
                after_capture = None
            # Perceptual check: a substantial target-colour component must remain near the
            # gripper-centred region after the lift. For this first experiment the target is
            # deliberately isolated; saved RGB-D remains available for deliberate review.
            frame = (
                verification_cap["color"] if verification_cap is not None
                else cam.capture(with_depth=False)["color"]
            )
            h, w = frame.shape[:2]
            seg = _held_color_evidence(frame, color, min_px=200)
            if seg is not None:
                u, v, npix, cue = seg
                distance = float(np.hypot(u - w * 0.5, v - h * 0.65))
                visual = {
                    "present": bool(distance <= 190.0 and npix >= 200),
                    "u": round(float(u), 1), "v": round(float(v), 1),
                    "npix": int(npix), "roi_distance_px": round(distance, 1),
                    "cue": cue,
                }
            else:
                visual = {"present": False, "note": "target colour absent after lift"}

        task_ok = bool(result.get("proprioceptive_success")
                       and visual is not None and visual.get("present"))
        result["task_verified_success"] = task_ok
        result["verification_method"] = "post_lift_wrist_color+gripper_width"
        result["verification"] = visual
        result["experience"] = {
            "mode": experience_mode,
            "relational_state": experience_state,
            "candidates": experience_candidates,
            "selected_episode_id": (
                selected_experience["episode_id"] if selected_experience else None
            ),
            "action": {
                "grip_z": result.get("grip_z"),
                "grip_width": result.get("grip_width"),
                "open_width": result.get("open_width"),
                "grip_yaw_deg": result.get("grip_yaw_deg"),
            },
            "captures": {
                "initial": initial_capture,
                "before_close": before_capture,
                "after_lift": after_capture,
            },
        }
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
    pick_target: Optional[list] = None,
    onto_target: Optional[list] = None,
    align_ang: Optional[float] = None,
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
    # pick/onto may be the SAME colour when both instances are pinned by pixel (multi-instance
    # stacking, e.g. red-on-red in a duplicate-colour scene); otherwise they must differ.
    if pick not in _SEGMENT_COLORS or onto not in _SEGMENT_COLORS or \
            (pick == onto and not (pick_target and onto_target)):
        return {"_source": "error", "stacked": None,
                "note": f"need two calibrated colors (same colour ok only with both targets); "
                        f"got pick={pick!r} onto={onto!r}"}
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or gripper is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "stacked": None, "note": "mock — no arm/gripper/camera"}

    import numpy as np
    import math
    from panda_py import controllers

    cam = _select_camera(cameras, camera)

    # Locate the pick INSTANCE from the wide overhead pose (per-instance — works on rect
    # blocks / clutter; the wide vantage re-finds blocks shifted by a prior grasp, #139/#140).
    fb = _observe([pick], _context)
    picks = [b for b in fb.get("blocks", []) if b["color"] == pick]
    if not picks:
        return {"_source": "live", "stacked": False, "stage": "locate", "note": f"no {pick} instance"}
    # pick a SPECIFIC instance nearest pick_target (multi-instance / duplicate colours); else largest
    pick_inst = (min(picks, key=lambda b: (b["u"] - pick_target[0]) ** 2 + (b["v"] - pick_target[1]) ** 2)
                 if pick_target else max(picks, key=lambda b: b["npix"]))

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
    # the onto INSTANCE nearest onto_target (the tower top, disambiguating a same-colour duplicate
    # elsewhere on the table); else the largest.
    onto_pt = (min(ontos, key=lambda b: (b["u"] - onto_target[0]) ** 2 + (b["v"] - onto_target[1]) ** 2)
               if onto_target else max(ontos, key=lambda b: b["npix"]))
    onto_track = [onto_pt["u"], onto_pt["v"]]
    # ALIGNMENT (2026-07-05): rotate the held block so its long axis matches align_ang (the base
    # block's orientation), so rectangular footprints stack OVERLAPPING instead of crossing — the
    # fix for tipping when stacking rectangles. Without it the onto-servo re-levels to the fixed
    # default-down yaw, which aligns the placed block only to a base that happens to lie on that
    # axis; a base at any other angle gets blocks laid across it → tiny overlap → topple.
    place_quat = None
    if align_ang is not None:
        phi = math.atan2(-math.cos(math.radians(align_ang)), -math.sin(math.radians(align_ang)))
        phi = ((phi + math.pi / 2) % math.pi) - math.pi / 2   # ±90°: gripper is symmetric
        place_quat = _qmul([math.cos(phi / 2), 0.0, 0.0, math.sin(phi / 2)], [0.0, 1.0, 0.0, 0.0])
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
        if place_quat is not None:
            move_to_pose([tx, ty, z], orientation=place_quat, _context=_context)
        else:
            move_to_pose([tx, ty, z], _context=_context)

    # 3) Compliant descend to contact, then release at contact (no press).
    X, Y = float(panda.get_position()[0]), float(panda.get_position()[1])
    quat = place_quat if place_quat is not None else panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 200.0, 30.0, 30.0, 30.0]))  # softer z for a gentle seat
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    start_z = float(panda.get_position()[2])
    tz = start_z
    # GUARD the contact test against the controller-engagement transient: when the impedance
    # controller first takes over (right after the servo move), the commanded−actual z gap and the
    # force estimate spike for ~0.2 s, which false-triggers "contact" at the START of the descent →
    # the block is released ~0.19 m up and DROPPED (Doug, 2026-06-28: blue dropped from ~19 cm; the
    # 0.19 false-trip from lab #142). So: settle + re-baseline the force for ~0.2 s, and ignore any
    # contact until we've actually descended past the transient (real contact is far lower anyway).
    guard_z = start_z - 0.02
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
    contact_z = None
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            settle = 0
            while cx.ok():
                tz = max(z_floor, tz - 0.02 * dt)              # slow descent
                ctrl.set_control(np.array([X, Y, tz]), quat, q0)
                az = float(panda.get_position()[2])
                fz = float(panda.get_state().O_F_ext_hat_K[2])
                if settle < 20:                                 # ~0.2 s: let it settle, re-baseline
                    settle += 1
                    fz0 = fz
                    continue
                # Contact = the held cube's bottom meets the surface → external z-force rises.
                # SLIP-IMMUNE: when the cube slides up in the gripper the commanded−actual z gap
                # never grows (lab #136), but the force still does. Gap is the secondary trigger.
                # Only honoured once we're past guard_z (transient skipped).
                if tz < guard_z and (abs(fz - fz0) > contact_force or (tz - az) > contact_gap):
                    contact_z = az
                    break
                if tz <= z_floor + 1e-4:                        # reached floor with no contact
                    break
    finally:
        # Release ONLY on a real contact — never open the jaws in free space, which would
        # DROP the block from the floor height (Doug, 2026-06-28: "dropped from quite a high
        # position"). A no-contact miss keeps holding so the caller can recover gently.
        if contact_z is not None:
            gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)   # release at contact, not pressing
        panda.stop_controller()

    if contact_z is None:
        well_centered = bool(servo and servo[-1]["pixel_err"] <= max(10.0, 2 * pixel_tol))
        if well_centered:
            # PERFECTLY positioned over the onto but no force spike — the held block slipped up in
            # the jaws as we descended (slip case, lab #136), so its bottom is already resting on
            # the onto top at the floor. RELEASE here (a clean seat) — do NOT carry it off (Doug,
            # 2026-06-28: "you had it perfectly positioned" then raised it and set it aside). The
            # no-drop hold is only for a GENUINE miss (servo never converged over the onto).
            gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
            p = panda.get_position()
            move_to_pose([float(p[0]), float(p[1]), 0.25], _context=_context)
            return {"_source": "live", "stacked": True, "contact_z": round(z_floor, 4),
                    "seated_by": "centered_floor_release",
                    "final_pixel_err": servo[-1]["pixel_err"],
                    "pick_grip_z": g.get("actual_z"), "pick_grip_target": g.get("grip_z"),
                    "pick_grip_w": g.get("actual_width"), "servo": servo}
        # genuinely not over the onto (servo never converged) — keep the block HELD (never drop
        # it), lift, and report the miss + that we're holding so the caller recovers gently.
        p = panda.get_position()
        move_to_pose([float(p[0]), float(p[1]), 0.25], _context=_context)
        return {"_source": "live", "stacked": False, "stage": "no_contact", "holding": True,
                "note": f"descended to floor {z_floor} without contact", "servo": servo}

    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), 0.25], _context=_context)
    return {"_source": "live", "stacked": True, "contact_z": round(contact_z, 4),
            # Surface the PICK grasp depth (where the jaws actually closed) so a full run exposes
            # grip quality per layer — the data to calibrate the grip-depth gate (lab #147).
            "pick_grip_z": g.get("actual_z"), "pick_grip_target": g.get("grip_z"),
            "pick_grip_w": g.get("actual_width"),
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


def reorient(
    *,
    color: str = "red",
    to: str = "lying",
    target: Optional[list] = None,
    camera: Optional[str] = None,
    grip_width: float = 0.03,
    lift_z: float = 0.30,
    block_len: float = 0.065,
    pitch_sign: float = 1.0,
    _context: Optional[Any] = None,
) -> dict:
    """Reorient a block between LYING and STANDING — the pose-fixing maneuver the planner needs
    (docs/planning.md gap #3; the demo exposed it when unstacking tipped the base upright).

    * to="standing" — stand a lying block on its end. A pick-and-place (delegates to `stand_on_end`).
    * to="lying"    — lay a STANDING block down flat by GRAB-AND-TURN: `vision_grasp` it mid-body,
      carry it to a central station, pitch the wrist 90° to horizontal in STAGES (45°+45°), and
      lower it to a force-sensed table contact. Viable now that the calibration fix (#151) grips a
      standing block solidly mid-body — a topple can't beat the low-friction table (a light block
      slides at any sub-top push, even a fast jab; 2026-06-30/07-04). `pitch_sign` flips the
      rotation direction; the staged pitch + a central station avoid the IK-sparse / self-collision
      zones the one-shot 90° flip hit at tabletop edges.
    """
    import numpy as np, math
    from panda_py import controllers

    if to == "standing":
        # the lying→standing direction already exists; expose it through the unified verb
        return stand_on_end(color=color, camera=camera, grip_width=grip_width,
                            lift_z=lift_z, block_half=block_len / 2.0, pitch_sign=pitch_sign,
                            _context=_context)
    if to != "lying":
        return {"_source": "error", "reoriented": None,
                "note": f"to must be 'lying' or 'standing', got {to!r}"}

    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or gripper is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "reoriented": None, "note": "mock — no arm/gripper/camera"}
    _select_camera(cameras, camera)

    # 1) GRASP the standing block mid-body. vision_grasp perceives it (H≥50 mm → tall branch),
    #    centres, yaws across the narrow axis, and — post-calibration (#151) — grips solidly at
    #    mid-body (verified: fingertip ~28 mm on a 60 mm block, width 0.029), the grip that used to
    #    catch the top edge and slip when rotated.
    fb = find_blocks(colors=[color], _context=_context)
    insts = [b for b in fb.get("blocks", []) if b["npix"] >= 120]
    if not insts:
        return {"_source": "live", "reoriented": False, "note": f"no standing {color} block found"}
    # target a SPECIFIC instance (nearest the given pixel) for multi-instance scenes; else the
    # largest blob of the colour.
    tgt = (min(insts, key=lambda b: (b["u"] - target[0]) ** 2 + (b["v"] - target[1]) ** 2)
           if target else max(insts, key=lambda b: b["npix"]))
    g = vision_grasp(color=color, target=[tgt["u"], tgt["v"]], _context=_context)
    if not g.get("grasped"):
        return {"_source": "live", "reoriented": False, "stage": "grip", "grasp": g}

    # 2) lift the upright block clear, then carry it (still vertical) to a central REORIENT STATION
    #    where the horizontal-gripper pose is reachable/collision-free — the grasp spot may sit at an
    #    edge where the 90° pitch had no IK / self-collided (2026-06-30).
    q_grip = list(panda.get_orientation())
    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), lift_z], orientation=q_grip, _context=_context)
    sx, sy = 0.48, 0.0
    move_to_pose([sx, sy, lift_z], orientation=q_grip, _context=_context)

    # 3) pitch the wrist 90° to horizontal in STAGES (45° then 90°). The one-shot flip swept through
    #    IK-sparse / self-colliding configs; a staged rotation stays in-envelope. Try both
    #    directions; on failure restore vertical and try the other way.
    def _staged_pitch(psign):
        for frac in (0.5, 1.0):                              # 45°, then the full 90°
            a = psign * frac * (math.pi / 4)
            q = _qmul(q_grip, [math.cos(a), 0, math.sin(a), 0])
            move_to_pose([sx, sy, lift_z], orientation=q, _context=_context)

    pitched = False
    for psign in (pitch_sign, -pitch_sign):
        try:
            _staged_pitch(psign)
            pitched = True
            break
        except Exception:
            try:
                move_to_pose([sx, sy, lift_z], orientation=q_grip, _context=_context)  # restore vertical
            except Exception:
                pass
    if not pitched:
        # couldn't rotate either way — set the still-vertical block back down and release (no drop)
        try:
            move_to_pose([sx, sy, 0.10], orientation=q_grip, _context=_context)
        except Exception:
            pass
        gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
        move_to_pose(list(_OBSERVE_POSE), _context=_context)
        return {"_source": "live", "reoriented": False, "stage": "pitch",
                "note": "no IK for the staged 90° rotation in either direction"}

    # 4) lower the now-horizontal block to a force-sensed table contact, release, retreat. Guarded
    #    against the controller-engagement transient so it never releases high (lab #148/reorient).
    quat_xyzw = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    ctrl.set_impedance(np.diag([400.0, 400.0, 200.0, 30.0, 30.0, 30.0]))
    ctrl.set_nullspace_stiffness(1.0)
    panda.start_controller(ctrl)
    dt = 0.01
    start_z = float(panda.get_position()[2])
    tz = start_z
    guard_z = start_z - 0.02
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
    floor = 0.03
    contact_z = None
    try:
        with panda.create_context(frequency=100, max_runtime=8.0) as cx:
            settle = 0
            while cx.ok():
                tz = max(floor, tz - 0.02 * dt)
                ctrl.set_control(np.array([sx, sy, tz]), quat_xyzw, q0)
                if settle < 20:
                    settle += 1
                    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
                    continue
                if tz < guard_z and abs(float(panda.get_state().O_F_ext_hat_K[2]) - fz0) > 4.0:
                    contact_z = float(panda.get_position()[2])
                    break
                if tz <= floor + 1e-4:
                    break
    finally:
        gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
        panda.stop_controller()
    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), lift_z], _context=_context)

    # 5) verify a lying footprint from overhead.
    fb2 = _observe([color], _context)
    lying = [b for b in fb2.get("blocks", []) if b["color"] == color and b["npix"] >= 1100]
    return {"_source": "live", "reoriented": bool(lying) or (contact_z is not None), "to": "lying",
            "method": "grab_and_turn", "contact_z": contact_z,
            "lying_npix": (max(lying, key=lambda b: b["npix"])["npix"] if lying else None)}


# Gripper-leveling offset (rad): small wrist roll/pitch added to every grasp orientation so
# the FINGERS' closing plane is level, not just the flange. The flange comes down vertical
# (~0.1°), but the fingers sit with a tiny mechanical tilt, so one fingertip lands first —
# nudging the block and stalling the grasp a few mm high (Doug, watching, 2026-06-27).
# Calibrated 2026-06-27 by lowering the open fingers to the table under teleop and leveling by
# eye (the force estimate was too noisy at fingertip contact): the left/+Y tip sat ~3 mm low,
# nulled by a −1° roll about the gripper's local x-axis (both tips then met the table together).
_GRIP_LEVEL_ROLL = -0.01745   # rad (−1°)
_GRIP_LEVEL_PITCH = 0.0

# Tabletop surface in EE-z (where the open fingertips press the flat table), mapped with
# probe_table 2026-06-27. The tabletop is PHYSICALLY FLAT (Doug), so the ~±1.4 mm variation
# across the workspace is the robot's forward-kinematics z-error — this plane (origin 0.46,0)
# encodes "what EE-z really puts the fingertip on the surface here", so referencing it per-xy
# compensates the FK error for grasp/place floors. Fit from 5 pressed points (|ΔFz| > 1.5 N).
_TABLE_PLANE = (0.0165, 0.01313, 0.00885, 0.46, 0.0)   # z0, dz/dx, dz/dy, x0, y0


def table_z(x, y):
    """FK-compensated tabletop EE-z at world (x, y) — the height the open fingertips touch the
    flat table, accounting for the robot's position-dependent kinematic error."""
    z0, gx, gy, x0, y0 = _TABLE_PLANE
    return z0 + gx * (float(x) - x0) + gy * (float(y) - y0)


def _level_quat(q):
    """Apply the calibrated gripper-leveling roll/pitch offset to a grasp orientation `q`
    ([w,x,y,z]). Rotates about the gripper's own x (roll) then y (pitch) axes."""
    import math
    if _GRIP_LEVEL_ROLL == 0.0 and _GRIP_LEVEL_PITCH == 0.0:
        return q
    r, p = _GRIP_LEVEL_ROLL / 2.0, _GRIP_LEVEL_PITCH / 2.0
    qr = [math.cos(r), math.sin(r), 0.0, 0.0]
    qp = [math.cos(p), 0.0, math.sin(p), 0.0]
    return _qmul(q, _qmul(qr, qp))


def probe_table(
    *,
    x: float = 0.45,
    y: float = 0.0,
    orientation: Optional[list] = None,
    open_width: float = 0.045,
    descent_speed: float = 0.02,
    z_floor: float = 0.008,
    force_thresh: float = 6.0,
    _context: Optional[Any] = None,
) -> dict:
    """Compliant downward probe to calibrate gripper leveling. Opens the fingers, descends
    over a bare patch of table until the vertical force rises, and reports the contact height
    and external wrench — Fz plus the horizontal torques Mx, My (in the EE frame). A level
    finger pair loads evenly (Mx, My ≈ 0); a mechanical finger tilt shows up as a horizontal
    torque. Iterate `orientation` (small roll/pitch) until the torque nulls → that's level.
    Read-only characterisation: it does NOT grasp. Use a bare table spot (no block under it).
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    if panda is None or gripper is None:
        time.sleep(0.05)
        return {"_source": "mock", "note": "mock — no arm/gripper"}
    import numpy as np
    from panda_py import controllers
    q = orientation or [0.0, 1.0, 0.0, 0.0]
    move_to_pose([x, y, 0.16], orientation=q, _context=_context)
    gripper.move(width=open_width, speed=0.1)
    quat = panda.get_orientation()
    q0 = np.array(panda.get_state().q)
    ctrl = controllers.CartesianImpedance()
    # Stiff in Z so it presses firmly into the table (clean contact signal), and ZERO nullspace
    # stiffness so the arm can REconfigure its joints to reach low — the 1.0 nullspace pull back
    # toward the high starting config was what stalled the descent ~4 mm above the table.
    ctrl.set_impedance(np.diag([600.0, 600.0, 600.0, 40.0, 40.0, 40.0]))
    ctrl.set_nullspace_stiffness(0.0)
    panda.start_controller(ctrl)
    dt = 0.01
    z_start = float(panda.get_position()[2])
    tz = z_start
    fz0 = float(panda.get_state().O_F_ext_hat_K[2])
    contact_z, wrench = None, None
    try:
        with panda.create_context(frequency=100, max_runtime=14.0) as cx:
            while cx.ok():
                tz = max(z_floor, tz - descent_speed * dt)
                ctrl.set_control(np.array([x, y, tz]), quat, q0)
                az = float(panda.get_position()[2])
                fz = float(panda.get_state().O_F_ext_hat_K[2])
                # Now that the arm actually REACHES the table and presses (nullspace freed, stiff
                # Z), contact is clean: the actual z stalls on the surface while the commanded z
                # keeps sinking below it, so (actual − commanded) opens up past the moving lag,
                # AND the press force rises. Gate on real downward progress (10 mm) first.
                if (z_start - az) > 0.010 and ((az - tz) > 0.010 or abs(fz - fz0) > force_thresh):
                    contact_z = az
                    wrench = [float(v) for v in panda.get_state().O_F_ext_hat_K]
                    break
                if tz <= z_floor + 1e-4:
                    contact_z = az
                    wrench = [float(v) for v in panda.get_state().O_F_ext_hat_K]
                    break
    finally:
        panda.stop_controller()
    if wrench is None:
        wrench = [float(v) for v in panda.get_state().O_F_ext_hat_K]
        contact_z = float(panda.get_position()[2])
    p = panda.get_position()
    move_to_pose([float(p[0]), float(p[1]), 0.20], orientation=q, _context=_context)
    # `contacted`: did it actually PRESS the table (real contact) vs stall in mid-air? At hard
    # corners the descent can't reach the surface and stalls with ~0 force — only a real press
    # (|ΔFz| ≳ 1.5 N) certifies the contact_z is the true surface (lab notebook 2026-06-27).
    contacted = bool(abs(wrench[2] - fz0) > 1.5)
    return {"_source": "live", "contact_z": round(contact_z, 4), "contacted": contacted,
            "Fz": round(wrench[2], 2), "dFz": round(wrench[2] - fz0, 2),
            "Mx": round(wrench[3], 3), "My": round(wrench[4], 3), "Mz": round(wrench[5], 3),
            "orientation": [round(float(v), 4) for v in q]}


def shove(
    *,
    color: str = "blue",
    target: Optional[list] = None,
    push_uv: Optional[list] = None,
    distance: float = 0.05,
    z_push: float = 0.022,   # push at the block's BASE (~6 mm up) so it slides flat instead of
                             # tipping/rotating and dragging a touching neighbour (surgical shove)
    camera: Optional[str] = None,
    _context: Optional[Any] = None,
) -> dict:
    """Push a block sideways to separate it from a touching neighbour so it can be grasped.

    Centres the CLOSED gripper over the target block (calibration-free image servo, like
    vision_grasp but no grasp), then lowers it beside the block and drives it through along
    the EE direction that matches the image push vector `push_uv` — e.g. (target_pixel −
    neighbour_pixel), to push the two apart. Learnt because a grasp on a clustered block gets
    a fingertip blocked by the neighbour (Doug, 2026-06-28). `distance` = push length (m).
    """
    import numpy as np
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or gripper is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "note": "mock — no arm/gripper/camera"}
    cam = _select_camera(cameras, camera)
    if cam is None or target is None or push_uv is None:
        return {"_source": "error", "note": "need target [u,v] + push_uv [du,dv]"}

    gripper.move(width=0.0, speed=0.1)          # closed — the gripper is the pusher
    approach_z, acquire_z = _VG_APPROACH_Z, _VG_ACQUIRE_Z
    track = list(target)
    pos = panda.get_position()
    z = float(np.clip(acquire_z, approach_z, 0.45))
    move_to_pose([float(pos[0]), float(pos[1]), z], _context=_context)
    for _ in range(14):                         # servo the closed gripper over the target
        seg = _nearest_instance(cam.capture(with_depth=False)["color"], color, track)
        if seg is None:
            break
        u, v, _npx = seg
        track = [u, v]
        tu, tv = _vg_grasp_pixel(z)
        du, dv = tu - u, tv - v
        err = float(np.hypot(du, dv))
        at_bottom = z <= approach_z + 1e-3
        if at_bottom and err <= 4.0:
            break
        ji = _vg_jinv(z)
        dex = float(np.clip(ji[0][0] * du + ji[0][1] * dv, -0.06, 0.06))
        dey = float(np.clip(ji[1][0] * du + ji[1][1] * dv, -0.06, 0.06))
        if err <= 22.0 and not at_bottom:
            z = max(approach_z, z - 0.045)
        p = panda.get_position()
        move_to_pose([float(np.clip(p[0] + dex, 0.30, 0.60)),
                      float(np.clip(p[1] + dey, -0.28, 0.28)), z], _context=_context)

    # Image push vector → EE-xy unit direction. The image Jacobian centres a block by moving the
    # CAMERA (block appears to move toward target); a physical push moves the block WITH the
    # gripper, which projects the opposite way — so negate to make the block actually travel along
    # push_uv (verified 2026-06-28: push_uv +v had moved the block −v before this flip).
    ji = _vg_jinv(approach_z)
    ex = -(ji[0][0] * push_uv[0] + ji[0][1] * push_uv[1])
    ey = -(ji[1][0] * push_uv[0] + ji[1][1] * push_uv[1])
    n = float(np.hypot(ex, ey)) or 1.0
    ux, uy = ex / n, ey / n
    p = panda.get_position()
    bx, by = float(p[0]), float(p[1])
    off = 0.034                                  # start just beside the block, on the origin side
    sx, sy = bx - ux * off, by - uy * off
    tx, ty = bx + ux * distance, by + uy * distance
    cl = lambda x, lo, hi: float(np.clip(x, lo, hi))
    move_to_pose([cl(sx, 0.30, 0.60), cl(sy, -0.28, 0.28), approach_z], _context=_context)
    move_to_pose([cl(sx, 0.30, 0.60), cl(sy, -0.28, 0.28), z_push], _context=_context)
    move_to_pose([cl(tx, 0.30, 0.60), cl(ty, -0.28, 0.28), z_push], _context=_context)  # push
    move_to_pose([cl(tx, 0.30, 0.60), cl(ty, -0.28, 0.28), approach_z], _context=_context)
    return {"_source": "live", "pushed": True,
            "ee_dir": [round(ux, 3), round(uy, 3)], "distance": distance,
            "from_xy": [round(sx, 4), round(sy, 4)], "to_xy": [round(tx, 4), round(ty, 4)]}


def _relevel_base(onto, canon_uv, canon_xy, _context, drift_tol=22.0):
    """Re-home the base block to its canonical lying pose if it has drifted past drift_tol px
    (measured at the overhead observe pose). A lying base placed back stays lying, so catching
    drift EARLY — every cycle — is what stops it accumulating into a tip, the loop's #142
    frontier; no lay-flat reorient needed. Returns a status string."""
    import math
    fb = _observe([onto], _context)
    cand = [b for b in fb.get("blocks", []) if b["color"] == onto]
    if not cand:
        return "base_lost"
    b = max(cand, key=lambda x: x["npix"])
    if math.hypot(b["u"] - canon_uv[0], b["v"] - canon_uv[1]) <= drift_tol:
        return "in_place"
    g = vision_grasp(color=onto, target=[b["u"], b["v"]], _context=_context)
    if not g.get("grasped"):
        return "regrasp_failed"
    place_block(target_xy=list(canon_xy), place_z=0.05, lift_z=0.30, _context=_context)
    return "releveled"


# ---------------------------------------------------------------------------
# Persistent scene store — the arm's world model + predict-then-look change detection
# ---------------------------------------------------------------------------
# A small persistent map of the world: the table plane (table_z) is the immovable substrate;
# blocks are movable objects, each tracked by identity + pose. v1 lives in the OBSERVE-pose
# image frame (calibration-free, and directly actionable — grasps target pixels), enriched
# with the block's pixel geometry. `scene_sync` re-perceives and compares to the stored model:
# a block whose pixel has shifted past `moved_px` since the last sync — with no action of mine
# in between — was moved by an EXTERNAL force (a hand, gravity, a knocked neighbour). The
# prediction error IS the alarm that the world changed without me (lab notebook: world-model).

def _scene_path(_store):
    from pathlib import Path
    if _store is not None and getattr(_store, "db_path", None):
        return str(Path(_store.db_path).parent / "scene.json")
    return "/tmp/hebbot_scene.json"


def _load_scene(path):
    import json, os
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {"frame": "wrist@observe", "ts": 0, "next_id": 1, "objects": []}


def _save_scene(path, scene):
    import json
    with open(path, "w") as f:
        json.dump(scene, f, indent=2)


def scene_sync(
    *,
    moved_px: float = 9.0,
    gate_px: float = 200.0,
    min_npix: int = 400,
    _store: Optional[Any] = None,
    _context: Optional[Any] = None,
) -> dict:
    """Predict-then-look world-model update + external-move detector. Goes to the overhead
    observe pose, re-perceives every block, associates each to the stored model (same colour,
    nearest within `gate_px`), and classifies it: stable / moved (pixel shift ≥ moved_px) /
    appeared / disappeared. Persists the refreshed model to data_dir/scene.json. Called when
    no action of mine has touched the scene, a `moved` result means an EXTERNAL force shifted
    that block — the prediction error is the alarm. (moved_px ≈ 9 px ≈ 1 cm at the observe
    height — above the ~1–2 mm perception noise and the ±1.4 mm FK error.)
    """
    cameras = _extract(_context, "cameras") or {}
    panda = _extract(_context, "panda")
    if panda is None or not cameras:
        time.sleep(0.02)
        return {"_source": "mock", "note": "no arm/camera"}
    move_to_pose(list(_OBSERVE_POSE), _context=_context)
    cam = _select_camera(cameras, None)
    fresh = [b for b in find_blocks(_context=_context).get("blocks", []) if b["npix"] >= min_npix]
    # Enrich each detection with POSE + metric size from depth — an ORIENTED BOX, not just a point
    # (position u,v + orientation ang were already here; pose/height tell us a rectangle is on its
    # END, which is what makes stacking tip, Doug 2026-07-05). Footprint is the primary pose cue
    # (a big top area can only be lying); depth height resolves the small footprints.
    for b in fresh:
        geom = _perceive_pose_3d(cam, b["color"], [b["u"], b["v"]], _context=_context)
        h = geom["height_m"] if geom else None
        b["height_m"] = round(h, 4) if h is not None else None
        b["length_m"] = round(geom["length_m"], 4) if geom else None
        b["width_m"] = round(geom["width_m"], 4) if geom else None
        b["pose"] = ("lying" if b["npix"] >= 1100
                     else ("standing" if (h is not None and h >= 0.036) else "lying"))
    path = _scene_path(_store)
    prior = _load_scene(path)
    prior_objs = prior.get("objects", [])
    next_id = prior.get("next_id", 1)
    ts = prior.get("ts", 0) + 1

    used = [False] * len(fresh)
    stable, moved, disappeared = [], [], []
    new_objs = []
    for o in prior_objs:
        best, bestd = -1, gate_px + 1.0
        for i, b in enumerate(fresh):
            if used[i] or b["color"] != o["color"]:
                continue
            d = ((b["u"] - o["u"]) ** 2 + (b["v"] - o["v"]) ** 2) ** 0.5
            if d < bestd:
                bestd, best = d, i
        if best >= 0:
            used[best] = True
            b = fresh[best]
            new_objs.append({"id": o["id"], "color": b["color"], "u": b["u"], "v": b["v"],
                             "ang": b["ang"], "L": b["L"], "W": b["W"], "aspect": b["aspect"],
                             "pose": b.get("pose"), "height_m": b.get("height_m"),
                             "length_m": b.get("length_m"), "width_m": b.get("width_m"),
                             "npix": b["npix"], "shape": b["shape"], "ts": ts, "missing": 0})
            if bestd >= moved_px:
                moved.append({"id": o["id"], "color": o["color"], "residual_px": round(bestd, 1),
                              "from": [o["u"], o["v"]], "to": [b["u"], b["v"]]})
            else:
                stable.append({"id": o["id"], "color": o["color"], "residual_px": round(bestd, 1)})
        else:
            miss = int(o.get("missing", 0)) + 1
            disappeared.append({"id": o["id"], "color": o["color"], "last": [o["u"], o["v"]],
                                "missing": miss})
            if miss <= 3:                 # keep an occluded/removed object for a few syncs
                new_objs.append({**o, "missing": miss, "ts": ts})

    appeared = []
    for i, b in enumerate(fresh):
        if used[i]:
            continue
        oid = f"{b['color']}-{next_id}"
        next_id += 1
        new_objs.append({"id": oid, "color": b["color"], "u": b["u"], "v": b["v"], "ang": b["ang"],
                         "L": b["L"], "W": b["W"], "aspect": b["aspect"],
                         "pose": b.get("pose"), "height_m": b.get("height_m"),
                         "length_m": b.get("length_m"), "width_m": b.get("width_m"),
                         "npix": b["npix"], "shape": b["shape"], "ts": ts, "missing": 0})
        appeared.append({"id": oid, "color": b["color"], "at": [b["u"], b["v"]]})

    _save_scene(path, {"frame": "wrist@observe", "ts": ts, "next_id": next_id, "objects": new_objs})
    return {"_source": "live", "scene_ts": ts, "n_objects": len([o for o in new_objs if not o.get("missing")]),
            "moved": moved, "appeared": appeared, "disappeared": disappeared, "stable": stable}


def scene_show(*, _store: Optional[Any] = None, _context: Optional[Any] = None) -> dict:
    """Return the current persistent world model (the stored scene) without re-perceiving."""
    return {"_source": "live", **_load_scene(_scene_path(_store))}


# ---------------------------------------------------------------------------
# Self-improvement metrics — the rising success-rate curve (ENPIRE-style pass@k evidence)
# ---------------------------------------------------------------------------
# Every `practice` run appends a record to data_dir/practice_metrics.jsonl: success rate,
# failure breakdown, and a content hash of THIS primitives.py (the policy version that ran).
# Plotted over runs, that's the self-improvement curve — and each step is tied to the exact
# policy code that produced it, the longitudinal evidence ENPIRE reports as pass@k.

def _code_version():
    """Short content hash of the running primitives.py = the policy version (more accurate than
    git HEAD, which lags on the deployed copy). Ties each metrics row to the code that ran."""
    try:
        import hashlib, os
        return hashlib.sha1(open(os.path.abspath(__file__), "rb").read()).hexdigest()[:10]
    except Exception:
        return None


def _metrics_path(_store):
    from pathlib import Path
    if _store is not None and getattr(_store, "db_path", None):
        return str(Path(_store.db_path).parent / "practice_metrics.jsonl")
    return "/tmp/hebbot_practice_metrics.jsonl"


def _append_metrics(_store, rec):
    import json
    try:
        with open(_metrics_path(_store), "a") as f:
            f.write(json.dumps(rec) + "\n")
        return True
    except Exception:
        return False


def practice_metrics(*, _store: Optional[Any] = None, _context: Optional[Any] = None) -> dict:
    """Read the accumulated self-improvement curve: every `practice` run's success rate over
    time, each tagged with the policy-code version that produced it. The trend IS the evidence
    that the loop measurably improves (the ENPIRE-style pass@k curve)."""
    import json, os
    path = _metrics_path(_store)
    runs = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        runs.append(json.loads(line))
                    except Exception:
                        pass
    return {"_source": "live", "n_runs": len(runs),
            "trend": [r.get("success_rate") for r in runs],
            "versions": [r.get("code") for r in runs],
            "runs": runs[-20:]}


def practice(
    *,
    pick: str = "green",
    onto: str = "blue",
    trials: int = 3,
    onto_height: float = 0.024,
    reset_xy: Optional[list] = None,
    contact_force: float = 4.0,
    relevel: bool = True,
    camera: Optional[str] = None,
    _store: Optional[Any] = None,
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
    # Establish the base's canonical lying pose: grasp it, record its world xy, place it back,
    # and record its overhead pixel. Each cycle then re-homes the base to this pose if it has
    # drifted, so drift can't accumulate into a tip (#142).
    canon_uv = canon_xy = None
    if relevel:
        fb0 = _observe([onto], _context)
        ob0 = [b for b in fb0.get("blocks", []) if b["color"] == onto]
        if ob0:
            bc0 = max(ob0, key=lambda x: x["npix"])
            g0 = vision_grasp(color=onto, target=[bc0["u"], bc0["v"]], _context=_context)
            if g0.get("grasped"):
                p0 = panda.get_position()
                canon_xy = [float(p0[0]), float(p0[1])]
                place_block(target_xy=canon_xy, place_z=0.05, lift_z=0.30, _context=_context)
                fb1 = _observe([onto], _context)
                ob1 = [b for b in fb1.get("blocks", []) if b["color"] == onto]
                if ob1:
                    canon_uv = [max(ob1, key=lambda x: x["npix"])["u"],
                                max(ob1, key=lambda x: x["npix"])["v"]]
    for t in range(trials):
        # Re-home the base to its canonical lying pose before stacking, so drift can't pile up
        # into a tip (#142). No-op when it hasn't moved past the drift tolerance.
        relevel_status = _relevel_base(onto, canon_uv, canon_xy, _context) \
            if (relevel and canon_uv is not None) else None
        # NB 2026-06-27: lowering contact_force to 2.5 N to be gentle on the base BROKE contact
        # detection (most stacks aborted no_contact, two false-tripped at 0.19 m) — 4 N is
        # load-bearing for reliable slip-immune detection; re-leveling (above) is the right
        # lever for base drift, not a softer press.
        # z_floor 0.045 (was 0.06): blue is held a touch high in the gripper, so onto a 29 mm
        # base it first-contacts right AT the old 0.06 floor and often aborted no_contact a few
        # mm above the block (Doug, watching: "slightly above the green block"). A lower floor
        # lets the descent reach the base — the grasp's "go lower" lesson, applied to the stack.
        s = stack_on(pick=pick, onto=onto, onto_top=0.0, max_steps=16,
                     contact_force=contact_force, z_floor=0.045, _context=_context)
        # verify from the wide overhead pose (robust re-location; #140).
        fb = _observe([pick, onto], _context)
        pk = [b for b in fb.get("blocks", []) if b["color"] == pick]
        on = [b for b in fb.get("blocks", []) if b["color"] == onto]
        # Stacked ⇒ onto's centroid coincides with pick's (within a cube) OR onto is fully
        # occluded under pick. Separate ⇒ centroids far apart. (#139: "onto absent" was too
        # strict — a sliver of the lower block peeks out at close range.)
        pc = max(pk, key=lambda b: b["npix"]) if pk else None
        oc = max(on, key=lambda b: b["npix"]) if on else None
        # Verify requires the stack to have ACTUALLY happened (stack_on reported contact) AND
        # the centroids to coincide. Without the stacked() gate, a no_contact abort still
        # passed on centroid-coincidence — the loop reporting success it never achieved
        # (2026-06-27: 10/12 'verified' were no-contact non-stacks). Honest failure detection
        # is the whole point of a self-improvement loop.
        verified = bool(s.get("stacked")) and pc is not None and (oc is None or
                   ((pc["u"] - oc["u"]) ** 2 + (pc["v"] - oc["v"]) ** 2) < 60 ** 2)
        trial = {"trial": t, "attempt": s.get("stacked"), "stage": s.get("stage"),
                 "contact_z": s.get("contact_z"), "verified": verified, "reset": None,
                 "relevel": relevel_status}
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
    # Self-improvement metric: log this run's success rate + failure breakdown, tagged with the
    # policy-code version, so the curve over runs is the ENPIRE-style pass@k evidence.
    n = len(log)
    verified = sum(1 for l in log if l.get("verified"))
    reset_fail = sum(1 for l in log if l.get("reset") == "grasp_failed")
    stages = {}
    for l in log:
        s = l.get("stage")
        if s:
            stages[s] = stages.get(s, 0) + 1
    rec = {"ts": time.time(), "pick": pick, "onto": onto, "requested": trials, "ran": n,
           "verified": verified, "success_rate": round(verified / n, 3) if n else None,
           "reset_failures": reset_fail, "fail_stages": stages,
           "relevels": sum(1 for l in log if l.get("relevel") == "releveled"),
           "stopped_early": any(l.get("stopped") for l in log), "code": _code_version()}
    _append_metrics(_store, rec)
    return {"_source": "live", "trials": log, "n": n, "verified_count": verified,
            "success_rate": rec["success_rate"], "reset_failures": reset_fail,
            "code_version": rec["code"]}


def _declutter(color, target_uv, _context, clutter_px=52.0, distance=0.05):
    """The grasp_in_clutter de-clutter step: if a different block sits within `clutter_px` of the
    target, shove the target perpendicular to that neighbour (approaching from the clear +v side)
    to open grasp clearance. Returns the target's pixel after the shove (or the original)."""
    import math
    fb = _observe([color], _context)
    same = [b for b in fb.get("blocks", []) if b["npix"] >= 400]
    if not same:
        return target_uv
    tgt = min(same, key=lambda b: (b["u"] - target_uv[0]) ** 2 + (b["v"] - target_uv[1]) ** 2)
    others = [b for b in find_blocks(_context=_context).get("blocks", []) if b["npix"] >= 400
              and abs(b["u"] - tgt["u"]) + abs(b["v"] - tgt["v"]) > 4]
    if not others:
        return [tgt["u"], tgt["v"]]
    nb = min(others, key=lambda o: (o["u"] - tgt["u"]) ** 2 + (o["v"] - tgt["v"]) ** 2)
    if math.hypot(nb["u"] - tgt["u"], nb["v"] - tgt["v"]) >= clutter_px:
        return [tgt["u"], tgt["v"]]
    ax, ay = nb["u"] - tgt["u"], nb["v"] - tgt["v"]       # toward the neighbour
    perp = [-ay, ax]
    if perp[1] < 0:                                        # prefer +v: clear approach from above
        perp = [-perp[0], -perp[1]]
    shove(color=color, target=[tgt["u"], tgt["v"]], push_uv=perp, distance=distance, _context=_context)
    cand = [b for b in _observe([color], _context).get("blocks", []) if b["npix"] >= 400]
    if cand:
        moved = min(cand, key=lambda b: (b["u"] - tgt["u"] - perp[0]) ** 2 + (b["v"] - tgt["v"] - perp[1]) ** 2)
        return [moved["u"], moved["v"]]
    return [tgt["u"], tgt["v"]]


def stack_all(
    *,
    order: Optional[list] = None,
    block_h: float = 0.028,
    declutter: bool = True,
    pose_fix: bool = False,
    _context: Optional[Any] = None,
) -> dict:
    """Goal-directed PLAN: stack the given blocks into a tower, base-first (see docs/planning.md).

    v1 composes the validated pieces — perceive → de-clutter (`shove`) → `stack_on` with a
    growing `onto_top` → predict-then-look verify — over distinct calibrated colours (one
    instance each). Runs failure-driven: remaining gaps (same-colour instances, reorientation,
    side-view perception, tower stability) surface as failed steps in the returned log, exactly
    the pattern that grows the next round of primitives.

    pose_fix (DRAFT, off by default): before stacking, run `survey_scene` to detect any STANDING
    block among `order` and `reorient` it flat, so every pick is a normal lying grasp — closing
    planning.md gaps #1 (side-view perception) + #3 (reorientation). Composes the two UNTESTED
    drafts, so it stays OFF until both are tuned on hardware; the verified stacking path below is
    unchanged when pose_fix is False.
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "tower_height": 0, "log": []}
    order = order or ["red", "blue", "green"]
    base = order[0]
    log = [{"step": "base", "color": base}]
    # Pose-fix pass (gated): lay any standing block among `order` flat before stacking, so the
    # tower is built only from lying blocks. Best-effort — a failed reorient is logged, not fatal.
    if pose_fix:
        try:
            survey = survey_scene(colors=list(order), tilt_confirm=True, _context=_context)
            for binfo in survey.get("blocks", []):
                if binfo.get("pose") == "standing":
                    r = reorient(color=binfo["color"], to="lying", _context=_context)
                    log.append({"step": "pose_fix", "color": binfo["color"], "was": "standing",
                                "reoriented": r.get("reoriented")})
        except Exception as e:
            log.append({"step": "pose_fix", "aborted": type(e).__name__, "note": str(e)[:90]})
    height = 1                                            # tower height in blocks (base = 1)
    top = base                                            # the CURRENT tower-top to stack ONTO
    for pick in order[1:]:
        if declutter:
            fb = _observe([pick], _context)
            pk = [b for b in fb.get("blocks", []) if b["npix"] >= 400]
            if pk:
                p0 = max(pk, key=lambda b: b["npix"])
                _declutter(pick, [p0["u"], p0["v"]], _context)
        # onto_top = elevation of the surface the onto SITS ON (0 for the base cube on the table,
        # block_h once it's on the 1st block, ...). NOT the onto's own top — that off-by-one put a
        # 28 mm phantom elevation into the parallax and mis-centred the pick off the base. z_floor
        # rises with the tower so a taller stack's higher contact is still reached.
        ot = (height - 1) * block_h
        # z_floor sits ~12 mm below the onto's top surface (ot + block_h): enough press-room for the
        # held block to trip the force/gap contact, but NOT so low it drives a slipping block to the
        # table and shoves the stack. The earlier '-0.006' (≈ table level for layer 1) over-pressed
        # a slipped block and knocked it off-centre; the real cure for "not sensing" was the
        # contact-detector startup-transient guard (false-trip fix), not a lower floor. On a genuine
        # slip (no force), the centered-release in stack_on seats it at this floor instead.
        # Stack onto the CURRENT TOP block (`top`), not the base: once a block is placed, the base
        # is occluded under the tower, so servoing over it mis-centres the pick and drops it beside
        # the stack (Doug, 2026-06-28). The visible top is always the right thing to centre on.
        try:
            s = stack_on(pick=pick, onto=top, onto_top=ot, z_floor=ot + block_h + 0.012,
                         max_steps=16, _context=_context)
        except Exception as e:
            # A motion abort (e.g. cartesian_reflex from a reachy pose / hard contact) must not
            # crash the plan mid-motion: recover the arm (release + lift to the observe pose) and
            # stop cleanly with the failure logged. The plan surfaced this on a 3-high attempt.
            try:
                if gripper is not None:
                    gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
                move_to_pose(list(_OBSERVE_POSE), _context=_context)
            except Exception:
                pass
            log.append({"step": "stack", "pick": pick, "aborted": type(e).__name__,
                        "note": str(e)[:90], "recovered": True})
            break
        fb2 = _observe([pick, top], _context)
        pk = [b for b in fb2.get("blocks", []) if b["color"] == pick and b["npix"] >= 400]
        on = [b for b in fb2.get("blocks", []) if b["color"] == top and b["npix"] >= 400]
        pc = max(pk, key=lambda b: b["npix"]) if pk else None
        oc = max(on, key=lambda b: b["npix"]) if on else None
        verified = bool(s.get("stacked")) and pc is not None and (oc is None or
                   ((pc["u"] - oc["u"]) ** 2 + (pc["v"] - oc["v"]) ** 2) < 60 ** 2)
        log.append({"step": "stack", "pick": pick, "onto_top": round(ot, 3),
                    "stacked": s.get("stacked"), "stage": s.get("stage"),
                    "contact_z": s.get("contact_z"), "pick_grip_z": s.get("pick_grip_z"),
                    "pick_grip_target": s.get("pick_grip_target"), "verified": verified})
        if verified:
            height += 1
            top = pick                                    # the pick is now the visible tower-top
        else:
            # A miss may leave the pick still HELD (the no-drop safety in stack_on). Never end
            # holding a block or leave it dropped high: set it down clear of the tower, then stop.
            if s.get("holding"):
                try:
                    place_block(target_xy=[0.45, -0.20], _context=_context)
                except Exception:
                    pass
            break                                         # can't build higher on a failed layer
    # Final stability check (planning.md gap #4): did the tower actually stay standing? A
    # per-layer 'verified' only confirms each block LANDED; this confirms the whole stack is still
    # up by depth (the top block elevated to ~height·block_h, lower blocks occluded beneath it).
    stability = None
    if height > 1:
        try:
            stability = verify_tower(order=order[:height], block_h=block_h, _context=_context)
        except Exception:
            stability = None
    return {"_source": "live", "tower_height": height, "order": order, "log": log,
            "stable": (stability or {}).get("stable"), "stability": stability}


def verify_tower(
    *,
    order: Optional[list] = None,
    block_h: float = 0.028,
    camera: Optional[str] = None,
    _context: Optional[Any] = None,
) -> dict:
    """Predict-then-look tower-STABILITY check (docs/planning.md gap #4): confirm the blocks in
    `order` (base-first) actually form a standing tower of len(order) blocks, not a landed-then-
    toppled or scattered pile. Two signals from the overhead pose:

    * HEIGHT (primary) — the TOP block's depth height above the table should be ≈ n·block_h (its
      top face sits one block above an (n−1)-tall stack). A toppled tower drops its top to ~one
      block_h. This also reads the achieved height directly.
    * BASE-OCCLUSION (secondary) — each lower block should be hidden under the tower (a small
      sliver, or its centroid near the top's). A full footprint far from the top = it's not in
      the stack.

    Returns {stable, n, top_color, top_height_m, expected_top_m, height_ok, base_occluded, layers}.
    """
    panda = _extract(_context, "panda")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "stable": None}
    cam = _select_camera(cameras, camera)
    order = order or ["red", "green", "blue"]
    n = len(order)
    top_color = order[-1]

    move_to_pose(list(_OBSERVE_POSE), _context=_context)
    fb = find_blocks(colors=list(order), _context=_context)
    tops = [b for b in fb.get("blocks", []) if b["color"] == top_color and b["npix"] >= 400]
    if not tops:
        return {"_source": "live", "stable": False, "n": n, "top_color": top_color,
                "note": f"top block {top_color} not seen at all"}
    top = max(tops, key=lambda b: b["npix"])
    geom = _perceive_pose_3d(cam, top_color, [top["u"], top["v"]], _context=_context)
    top_h = geom["height_m"] if geom else None
    expected = n * block_h
    height_ok = top_h is not None and abs(top_h - expected) <= 0.016     # ~half a block tolerance

    # base-occlusion: lower blocks should be occluded (small sliver) or coincident with the top
    layers = []
    for c in order[:-1]:
        insts = [b for b in fb.get("blocks", []) if b["color"] == c]
        big = max(insts, key=lambda b: b["npix"]) if insts else None
        if big is None:
            layers.append({"color": c, "ok": True, "reason": "fully occluded"})
            continue
        near = ((big["u"] - top["u"]) ** 2 + (big["v"] - top["v"]) ** 2) < 70 ** 2
        sliver = big["npix"] < 600
        layers.append({"color": c, "ok": bool(sliver or near), "npix": big["npix"],
                       "near_top": bool(near)})
    base_ok = all(o["ok"] for o in layers)
    return {"_source": "live", "stable": bool(height_ok and base_ok), "n": n,
            "top_color": top_color,
            "top_height_m": round(top_h, 4) if top_h is not None else None,
            "expected_top_m": round(expected, 4), "height_ok": bool(height_ok),
            "base_occluded": bool(base_ok), "layers": layers}


def unstack_all(
    *,
    order: Optional[list] = None,
    block_h: float = 0.028,
    _context: Optional[Any] = None,
) -> dict:
    """Goal-directed PLAN: take a tower apart top-first, scattering each block to the table.

    The inverse of `stack_all` (docs/planning.md). `order` is the tower BASE-FIRST (as built);
    we lift from the top down — each block at its known elevation `i*block_h`, corrected for the
    overhead parallax via `vision_grasp(pick_top=...)` (the depth also reads it as 'tall', so the
    grip lands on its body) — and set it down at a distinct, well-separated clear spot. The base
    (order[0]) is left on the table. Validated by hand on 2026-06-28 (the video-demo teardown);
    this productises it as one call.

    Known gap: lifting the last layer can tip the base onto its end (friction/alignment) — a
    lay-flat/reorientation step is the next piece (partly in `stand_on_end`).
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "unstacked": [], "log": []}
    order = order or ["red", "green", "blue"]
    # Scatter the lifted blocks FAR from the still-standing tower so the place descent (and the arm)
    # never fouls it (Doug, 2026-06-28: "you're not moving far enough away... the existing tower
    # obstructs your descent"). Anchor to the tower's actual xy — captured from the arm position at
    # the first grasp, when it's directly over the tower — and pick, for each block, the farthest
    # in-bounds PERIPHERAL candidate from the tower and from prior drops. (The proper fix is feeding
    # the tower into cuRobo's collision world, the deferred Phase 2; this is the pragmatic one.)
    cands = [[0.40, 0.22], [0.40, -0.22], [0.54, 0.18], [0.54, -0.18], [0.36, 0.20], [0.36, -0.20]]
    base = order[0]
    log: list[dict] = []
    unstacked: list[str] = []
    tower_xy = None
    used: list = []
    for k, idx in enumerate(range(len(order) - 1, 0, -1)):   # top (highest) down to just above base
        color = order[idx]
        pick_top = idx * block_h
        fb = _observe([color], _context)
        insts = [b for b in fb.get("blocks", []) if b["color"] == color and b["npix"] >= 400]
        if not insts:
            log.append({"step": "unstack", "pick": color, "stage": "locate",
                        "note": f"no {color} instance", "pick_top": round(pick_top, 3)})
            break
        inst = max(insts, key=lambda b: b["npix"])
        g = vision_grasp(color=color, target=[inst["u"], inst["v"]], pick_top=pick_top,
                         _context=_context)
        fell = False
        if not g.get("grasped") and pick_top > 0.0:
            # A failed ELEVATED grasp has almost certainly knocked the block down to the table
            # (Doug, 2026-06-28). Re-locate it and retry at TABLE level — don't keep grabbing air at
            # the stale elevation it WOULD have been at on the tower.
            fb2 = _observe([color], _context)
            i2 = [b for b in fb2.get("blocks", []) if b["color"] == color and b["npix"] >= 400]
            if i2:
                inst2 = max(i2, key=lambda b: b["npix"])
                g = vision_grasp(color=color, target=[inst2["u"], inst2["v"]], pick_top=0.0,
                                 _context=_context)
                fell = True
        if not g.get("grasped"):
            # recover: open + lift to the observe pose, log the miss, stop cleanly
            try:
                if gripper is not None:
                    gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
                move_to_pose(list(_OBSERVE_POSE), _context=_context)
            except Exception:
                pass
            log.append({"step": "unstack", "pick": color, "stage": "grasp", "grasped": False,
                        "pick_top": round(pick_top, 3), "retried_table": fell})
            break
        p = panda.get_position()
        if tower_xy is None:
            tower_xy = [float(p[0]), float(p[1])]     # arm is right over the tower after the grasp
        # farthest peripheral candidate from the tower, biased to also spread from prior drops
        def _score(c, _t=tower_xy, _u=used):
            dt = (c[0] - _t[0]) ** 2 + (c[1] - _t[1]) ** 2
            du = min([(c[0] - s[0]) ** 2 + (c[1] - s[1]) ** 2 for s in _u], default=1.0)
            return dt + 0.5 * du
        spot = max(cands, key=_score)
        used.append(spot)
        place_block(target_xy=spot, _context=_context)
        unstacked.append(color)
        log.append({"step": "unstack", "pick": color, "pick_top": round(pick_top, 3),
                    "to": spot, "tower_xy": [round(tower_xy[0], 3), round(tower_xy[1], 3)],
                    "grip_z": g.get("actual_z"), "grasped": True})
    move_to_pose(list(_OBSERVE_POSE), _context=_context)
    return {"_source": "live", "unstacked": unstacked, "base": base, "log": log}


def tidy(
    *,
    colors: Optional[list] = None,
    max_ops: int = 10,
    _context: Optional[Any] = None,
) -> dict:
    """Reset the scene to a clean, stackable state: survey every block (MULTI-INSTANCE) and lay any
    STANDING one flat via `reorient(to='lying')`. The 'undo Elek's 8-on-end' plan — leaves all
    blocks lying so a later `stack_all` can build. Composes `survey_scene` + grab-and-turn
    `reorient`, targeting each standing instance by pixel. Re-surveys after each lay-down (positions
    shift), stops when nothing stands (done), or on a stuck block / `max_ops`.
    """
    panda = _extract(_context, "panda")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "log": []}
    log: list = []
    laid = 0
    for _ in range(max_ops):
        survey = survey_scene(colors=colors, _context=_context)
        standing = [b for b in survey.get("blocks", []) if b.get("pose") == "standing"]
        if not standing:
            return {"_source": "live", "done": True, "laid_flat": laid, "log": log,
                    "blocks": survey.get("blocks", [])}
        b = standing[0]                                   # lay one, then re-survey (poses shift)
        r = reorient(color=b["color"], to="lying", target=[b["u"], b["v"]], _context=_context)
        ok = bool(r.get("reoriented"))
        log.append({"color": b["color"], "at": [round(b["u"], 1), round(b["v"], 1)],
                    "reoriented": ok, "stage": r.get("stage")})
        if ok:
            laid += 1
        else:
            return {"_source": "live", "done": False, "laid_flat": laid, "log": log,
                    "note": f"stuck on {b['color']} at {[round(b['u'], 1), round(b['v'], 1)]}"}
    return {"_source": "live", "done": False, "laid_flat": laid, "log": log, "note": "hit max_ops"}


def build_tower(
    *,
    block_h: float = 0.028,
    max_blocks: int = 8,
    _context: Optional[Any] = None,
) -> dict:
    """Multi-instance tower: stack every lying block into ONE column, base-first, targeting each by
    POSITION so DUPLICATE COLOURS work (survey_scene enumerates all instances; stack_on now takes
    pick_target/onto_target to pin specific blocks by pixel). Picks a central base, orders the rest
    by nearness, and stacks each onto the growing tower — the onto is the tower TOP by pixel, not by
    colour, so a same-colour block elsewhere on the table doesn't confuse it. Stops on the first
    failed layer; confirm the result by re-imaging (a colour-based verify_tower is itself ambiguous
    with duplicate colours). This is the 'build from all N blocks' plan the duplicate scene needs.
    """
    panda = _extract(_context, "panda")
    gripper = _extract(_context, "gripper")
    cameras = _extract(_context, "cameras") or {}
    if panda is None or not cameras:
        time.sleep(0.05)
        return {"_source": "mock", "tower_height": 0, "log": []}
    survey = survey_scene(_context=_context)
    lying = [b for b in survey.get("blocks", []) if b.get("pose") == "lying"]
    if len(lying) < 2:
        return {"_source": "live", "tower_height": len(lying), "log": [],
                "note": "need >=2 lying blocks"}
    cx, cy = 320.0, 355.0                                  # image centre ≈ central reach
    base = min(lying, key=lambda b: (b["u"] - cx) ** 2 + (b["v"] - cy) ** 2)
    rest = [b for b in lying if b is not base]
    rest.sort(key=lambda b: (b["u"] - base["u"]) ** 2 + (b["v"] - base["v"]) ** 2)
    rest = rest[:max_blocks - 1]
    base_uv = [base["u"], base["v"]]
    base_ang = base.get("ang")          # align every stacked block's long axis to the base's
    top_color = base["color"]
    height = 1
    log = [{"step": "base", "color": base["color"], "at": [round(base["u"], 1), round(base["v"], 1)]}]
    for b in rest:
        ot = (height - 1) * block_h
        try:
            s = stack_on(pick=b["color"], pick_target=[b["u"], b["v"]],
                         onto=top_color, onto_target=base_uv, align_ang=base_ang,
                         onto_top=ot, z_floor=ot + block_h + 0.012, max_steps=16, _context=_context)
        except Exception as e:
            try:
                if gripper is not None:
                    gripper.move(width=_GRIPPER_MAX_WIDTH, speed=0.1)
                move_to_pose(list(_OBSERVE_POSE), _context=_context)
            except Exception:
                pass
            log.append({"step": "stack", "color": b["color"], "aborted": type(e).__name__,
                        "note": str(e)[:80]})
            break
        ok = bool(s.get("stacked"))
        log.append({"step": "stack", "color": b["color"], "onto": top_color, "stacked": ok,
                    "stage": s.get("stage"), "contact_z": s.get("contact_z"),
                    "pick_grip_z": s.get("pick_grip_z")})
        if ok:
            height += 1
            top_color = b["color"]
        else:
            if s.get("holding"):
                try:
                    place_block(target_xy=[0.45, -0.20], _context=_context)
                except Exception:
                    pass
            break
    return {"_source": "live", "tower_height": height,
            "order": [l.get("color") for l in log], "log": log}


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
        "name": "compliant_hold",
        "function_spec": "hebbot.skills.primitives:compliant_hold",
        "expectations": {
            "checks": [
                {"name": "completed", "field": "completed", "op": "eq", "value": True},
                {"name": "bounded_drift", "field": "max_position_drift_m",
                 "op": "le", "value": 0.01},
            ],
        },
        "params_schema": {
            "type": "object",
            "properties": {
                "duration_s": {
                    "type": "number", "minimum": 0.25, "maximum": 5.0, "default": 2.0,
                    "description": "stationary Cartesian hold duration",
                },
                "frequency": {
                    "type": "number", "minimum": 50, "maximum": 250, "default": 100,
                    "description": "command loop frequency in Hz",
                },
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
                "max_retry": {"type": "integer", "minimum": 0, "maximum": 2, "default": 2,
                              "description": "full lift/reacquire retries after a failed grasp"},
                "experience_mode": {"type": "string", "enum": ["off", "record", "replay"],
                                    "default": "off",
                                    "description": "off=legacy; record=save verified example; "
                                                   "replay=retrieve verified relational neighbour"},
                "replay_threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                                     "default": 0.82,
                                     "description": "minimum relational similarity for replay"},
                "speed_factor": {"type": "number", "minimum": 0.01, "maximum": 0.2,
                                 "default": 0.1,
                                 "description": "motion speed; experience trials cap this at 0.05"},
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
                "pick_target": {"type": "array", "items": {"type": "number"},
                                "description": "[u,v] pixel of the specific pick INSTANCE (for duplicate colours)"},
                "onto_target": {"type": "array", "items": {"type": "number"},
                                "description": "[u,v] pixel of the specific onto INSTANCE (e.g. the tower top)"},
                "align_ang": {"type": "number", "minimum": -180.0, "maximum": 180.0,
                              "description": "degrees: rotate the placed block's long axis to match this "
                                             "(the base block's orientation) so rectangular footprints overlap "
                                             "instead of crossing — prevents tipping when stacking rectangles"},
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
        "name": "probe_table",
        "function_spec": "hebbot.skills.primitives:probe_table",
        "params_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "default": 0.45},
                "y": {"type": "number", "default": 0.0},
                "orientation": {"type": "array", "items": {"type": "number"},
                                "minItems": 4, "maxItems": 4,
                                "description": "grasp quaternion [w,x,y,z] to test (default gripper-down)"},
                "open_width": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.045},
                "z_floor": {"type": "number", "minimum": 0.0, "maximum": 0.1, "default": 0.012},
                "force_thresh": {"type": "number", "minimum": 1.0, "maximum": 20.0, "default": 5.0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "shove",
        "function_spec": "hebbot.skills.primitives:shove",
        "params_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["blue", "green", "red"], "default": "blue"},
                "target": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                           "description": "[u,v] pixel of the block to push (from find_blocks)"},
                "push_uv": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                            "description": "image push direction [du,dv], e.g. target_pixel − neighbour_pixel (push apart)"},
                "distance": {"type": "number", "minimum": 0.01, "maximum": 0.15, "default": 0.05,
                             "description": "push length (m)"},
                "z_push": {"type": "number", "minimum": 0.0, "maximum": 0.1, "default": 0.022,
                           "description": "EE z to push at (closed gripper at the block base ~6mm up)"},
                "camera": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "scene_sync",
        "function_spec": "hebbot.skills.primitives:scene_sync",
        "params_schema": {
            "type": "object",
            "properties": {
                "moved_px": {"type": "number", "minimum": 1.0, "maximum": 100.0, "default": 9.0,
                             "description": "pixel shift that counts as 'moved' (~9 px ≈ 1 cm at observe height)"},
                "gate_px": {"type": "number", "minimum": 10.0, "maximum": 400.0, "default": 200.0,
                            "description": "max pixel distance to associate a detection with a stored object (a move ≤ this reads as 'moved'; beyond it splits to disappeared+appeared)"},
                "min_npix": {"type": "integer", "minimum": 20, "default": 400,
                             "description": "ignore detections smaller than this (edge glare/slivers)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "scene_show",
        "function_spec": "hebbot.skills.primitives:scene_show",
        "params_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "practice_metrics",
        "function_spec": "hebbot.skills.primitives:practice_metrics",
        "params_schema": {"type": "object", "properties": {}, "additionalProperties": False},
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
                "contact_force": {"type": "number", "minimum": 1.0, "maximum": 10.0, "default": 4.0,
                                  "description": "stack contact force (N); <4 breaks slip-immune contact detection"},
                "relevel": {"type": "boolean", "default": True,
                            "description": "re-home the base to its canonical lying pose each cycle (anti-drift/tip)"},
                "camera": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "stack_all",
        "function_spec": "hebbot.skills.primitives:stack_all",
        "params_schema": {
            "type": "object",
            "properties": {
                "order": {"type": "array", "items": {"type": "string", "enum": ["blue", "green", "red"]},
                          "description": "colours to stack, base first (e.g. ['red','blue','green']); distinct colours, one instance each"},
                "block_h": {"type": "number", "minimum": 0.01, "maximum": 0.06, "default": 0.028,
                            "description": "nominal block height (m) — sets the growing onto_top per layer"},
                "declutter": {"type": "boolean", "default": True,
                              "description": "shove a touching neighbour off each pick before grasping"},
                "pose_fix": {"type": "boolean", "default": False,
                             "description": "DRAFT: survey + lay any standing block flat before stacking (off until reorient/survey_scene are tuned)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "unstack_all",
        "function_spec": "hebbot.skills.primitives:unstack_all",
        "params_schema": {
            "type": "object",
            "properties": {
                "order": {"type": "array", "items": {"type": "string", "enum": ["blue", "green", "red"]},
                          "description": "tower colours BASE-FIRST (as built); unstacked top-first, base left on the table"},
                "block_h": {"type": "number", "minimum": 0.01, "maximum": 0.06, "default": 0.028,
                            "description": "nominal block height (m) — sets each block's lift elevation i*block_h"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "verify_tower",
        "function_spec": "hebbot.skills.primitives:verify_tower",
        "params_schema": {
            "type": "object",
            "properties": {
                "order": {"type": "array", "items": {"type": "string", "enum": ["blue", "green", "red"]},
                          "description": "tower colours BASE-FIRST; checks a standing tower of this many blocks"},
                "block_h": {"type": "number", "minimum": 0.01, "maximum": 0.06, "default": 0.028,
                            "description": "nominal block height (m) — sets the expected top-block elevation"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "survey_scene",
        "function_spec": "hebbot.skills.primitives:survey_scene",
        "params_schema": {
            "type": "object",
            "properties": {
                "colors": {"type": "array", "items": {"type": "string", "enum": ["blue", "green", "red"]},
                           "description": "colours to classify (default: all)"},
                "tilt_confirm": {"type": "boolean", "default": True,
                                 "description": "drop a tilt-glance side view to resolve ambiguous-height blocks"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "reorient",
        "function_spec": "hebbot.skills.primitives:reorient",
        "params_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["blue", "green", "red"],
                          "description": "block colour to reorient"},
                "to": {"type": "string", "enum": ["lying", "standing"], "default": "lying",
                       "description": "target pose: 'lying' lays a standing block flat; 'standing' stands a lying block on end"},
                "target": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                           "description": "[u,v] pixel of a SPECIFIC instance (nearest wins) for multi-instance scenes; else largest of the colour"},
                "block_len": {"type": "number", "minimum": 0.03, "maximum": 0.1, "default": 0.065,
                              "description": "block long-axis length (m) — sets the upright grip height / lay-down geometry"},
                "pitch_sign": {"type": "number", "enum": [1.0, -1.0], "default": 1.0,
                               "description": "which way the wrist pitches (flip if it tips the wrong way)"},
            },
            "required": ["color"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tidy",
        "function_spec": "hebbot.skills.primitives:tidy",
        "params_schema": {
            "type": "object",
            "properties": {
                "colors": {"type": "array", "items": {"type": "string", "enum": ["blue", "green", "red"]},
                           "description": "colours to consider (default: all)"},
                "max_ops": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10,
                            "description": "safety cap on lay-down operations"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "build_tower",
        "function_spec": "hebbot.skills.primitives:build_tower",
        "params_schema": {
            "type": "object",
            "properties": {
                "block_h": {"type": "number", "minimum": 0.01, "maximum": 0.06, "default": 0.028,
                            "description": "nominal block height (m)"},
                "max_blocks": {"type": "integer", "minimum": 2, "maximum": 10, "default": 8,
                               "description": "cap on tower height (blocks)"},
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
