# cuRobo Integration Plan

**Status:** proposed (2026-06-24). Written while Omen was offline — treat API specifics as a
starting point to validate against the installed cuRobo version.

## Why

Every IK problem Hebbot has hit traces to one root cause: `panda_py` returns a **single
analytical IK solution** with no awareness of collisions, smoothness, or configuration quality.
[cuRobo](https://curobo.org) ([NVlabs/curobo](https://github.com/NVlabs/curobo)) replaces that
with GPU-parallel **trajectory optimization** — collisions, joint limits, and smoothness are all
in the objective.

| Failure we hit | Root cause (analytical IK) | cuRobo |
|---|---|---|
| Teleop down-jog **stalled** near the block | single branch + nullspace bias couldn't descend | plans a collision-free path; picks a good config |
| **Branch flips** tracing the keep-out boundary (J1 +2.0→−2.2 between adjacent targets) | each pose solved independently, no continuity | optimizes a *continuous* trajectory |
| **Self-collision reflex** at the blue touch-off | IK ignores self-collision | self-collision is a hard constraint, built in |
| **Contorted near-limit configs** (J4 ≈ −3.07) | no config-quality awareness | avoids them, or cleanly reports infeasible |

The hand-mapped self-collision keep-out boundary (`lab_notebook.md`, 2026-06-18) is the empirical
ground truth that **validates** cuRobo's collision model; cuRobo is the principled version that
makes managing that boundary manually unnecessary.

**What cuRobo does NOT do:** it moves the arm safely to wherever you tell it — it does not tell
you *where*. The camera→gripper offset / ~2 cm localization is a **perception** problem and stays
the separate, primary track. cuRobo is the motion layer only.

## Design: cuRobo as a planning backend behind `move_to_pose`

Keep the MCP primitive signature identical; swap the internals:

```
move_to_pose(position, orientation, speed_factor)
  OLD:  ik_full(pose) -> single q -> execute
  NEW:  curobo.plan_single(current_q, goal_pose) -> collision-free joint trajectory
        -> panda_py executes the trajectory waypoints
```

- **Where it runs:** on Omen's GPU (cuRobo needs CUDA), inside the same process that already
  holds FCI via `panda_py`. Planning latency is ~tens of ms — fine for non-realtime moves.
- **Execution:** cuRobo plans a joint-space trajectory; `panda_py` executes it (move through the
  interpolated waypoints, or stream them to a joint controller). cuRobo plans; panda_py actuates.
- **Self-collision avoidance needs ZERO perception** (pure robot model) → the first win lands with
  no world model at all.

### Gotchas to get right
- **Quaternion convention differs.** cuRobo `Pose` is **`[w, x, y, z]` (scalar-first)**;
  `panda_py.ik` orientation is **`[x, y, z, w]` (scalar-last)**. Gripper-down:
  cuRobo `[0,1,0,0]`, panda_py `[1,0,0,0]`. The MCP primitive currently takes `[w,x,y,z]`.
- **End-effector frame.** cuRobo's `franka.yml` defines its own EE link (typically `panda_hand`
  or a tool frame). Confirm it matches Hebbot's TCP convention, or apply a fixed offset — this is
  the same EE/TCP bookkeeping that matters for grasping (the ~3.4 cm flange→fingertip offset from
  the touch-off work).
- **Collision-sphere model.** cuRobo approximates the arm with spheres (shipped in `franka.yml`).
  The default is good; tighten only if it's over-conservative near the base.

### World model
- **Phase 1:** no world model — self-collision + joint limits only. Kills the reflexes/branch
  flips immediately.
- **Phase 2:** add the **table plane** as a `Cuboid` (so it never plans through the table) and the
  **other blocks** as obstacles (positions from perception) for clutter-aware reaching.
- **Phase 3 (maybe never):** reactive MPC for live obstacle avoidance.

## Phased adoption

1. **Spike** (`scripts/curobo_spike.py`) — GPU-only, no arm. Plan one move with cuRobo, solve the
   same pose with `panda_py`, diff the configs. Prove the value on a hard near-base pose (where
   panda_py gives the contorted J4 ≈ −3.0 config that reflexed) before touching the substrate.
2. **`move_to_pose` backend** — self-collision-aware point-to-point, no world model. Behind a
   feature flag so we can A/B against the analytical path.
3. **World model** — table + block obstacles.
4. **Grasp / push integration** — route the compliant grasp approach and the Push-T `execute_push`
   waypoints through cuRobo planning.

## Install notes / caveats
- Needs an NVIDIA GPU + CUDA toolkit; build is non-trivial. Follow the official install
  (https://curobo.org/get_started/1_install_instructions.html) — likely a source build in a
  dedicated venv on Omen (keep it separate from the `panda_py` substrate venv if the torch/CUDA
  versions conflict; the planner can run in the substrate venv if versions are compatible, which
  is cleanest since it needs `panda_py` for execution).
- First call is slow (CUDA graph capture / warmup) — call `motion_gen.warmup()` once at startup.
- Pin the cuRobo version once it works; the API moves between releases.

## Open decisions
- Same venv as the substrate (simplest for execution) vs. a separate planning venv (cleaner deps)?
- Confirm cuRobo's Franka EE link vs. Hebbot's TCP before wiring into `move_to_pose`.
- How conservative to set the table/world collision margins for low pushes (Push-T runs the tip
  ~1 cm above the table — don't let the table obstacle forbid the push height).

## Validated install + spike result (2026-06-25, Omen, RTX 5060 Ti / CUDA 13.1)

Built and ran successfully on the Blackwell card. Working recipe — a **fresh `~/curobo-venv`**,
isolated from the substrate + perception venvs (script: `~/curobo_build.sh` on Omen):

1. Bootstrap from the uv-managed 3.12 interpreter (`~/.local/share/uv/python/cpython-3.12.13-*/bin/python3.12 -m venv ~/curobo-venv`); the system `python3` is 3.14 (too new for wheels).
2. **torch matching the CUDA _major_ of the toolkit (13):**
   `pip install "torch==2.9.1+cu130" --extra-index-url https://download.pytorch.org/whl/cu130`
   — use `--extra-index-url` (not `--index-url`) so PyPI still supplies the pure-Python deps.
3. `pip install panda-python numpy scipy` — the PyPI name is **`panda-python`** (imports as `panda_py`).
4. cuRobo from source: `git clone --depth 1 https://github.com/NVlabs/curobo`, then
   `CUDA_HOME=/usr/local/cuda-13.1  TORCH_CUDA_ARCH_LIST=12.0  pip install -e ~/curobo --no-build-isolation`
   (`12.0` = Blackwell sm_120).
5. **Kernel backend (the non-obvious gotcha):** cuRobo needs a runtime kernel backend. Install the
   cuda.core one: `pip install "cuda-core[cu13]"`. (It bumps nvrtc/nvjitlink to 13.3 vs torch's
   13.0 pin — a harmless same-major forward-compat warning; torch + the GPU still work.)

**API note:** the current cuRobo is refactored from this doc's assumptions —
`curobo.inverse_kinematics.InverseKinematics` + `.solve_pose(GoalToolPose.from_poses({tool: Pose(...)}))`,
and `curobo.motion_planner.MotionPlanner` + `.plan_pose(...)`. Quaternion is `[w,x,y,z]` (gripper-down `[0,1,0,0]`).
`scripts/curobo_spike.py` is updated to this API.

### Spike result — the thesis, confirmed
| pose | panda_py (analytical) | cuRobo (multi-seed, self-collision-aware) |
|---|---|---|
| normal (0.45, 0, 0.30) | J4 −2.43, err 0.4 mm | J4 −2.41, err 0.0 mm |
| **hard (0.30, 0, 0.06)** | **J4 −3.011 (jammed at limit), err 16.7 mm — misses** | **J4 −2.66 (0.4 rad margin), err 0.0 mm — exact** |

## Phase 1 integration — cuRobo behind `move_to_pose` (2026-06-25)

**Architecture: a persistent cuRobo planning service.** cuRobo's setup + warmup is slow, so it
must stay resident; and keeping torch/cuRobo out of the substrate venv avoids dep conflicts. So:

- **`scripts/curobo_service.py`** runs in `curobo-venv`, loads collision-aware IK once, and serves
  `POST /ik {position, quaternion[w,x,y,z], start_q[7]}` over `127.0.0.1:8211`. It runs as a
  **systemd service** (`/etc/systemd/system/curobo.service`, `User=doug`, `Restart=always`) installed
  2026-06-26 — auto-starts on boot and self-heals on crash, so no manual launch after an omen reboot.
  Manage with `systemctl {status,restart} curobo` / `journalctl -u curobo`. (Legacy manual launch, if
  ever needed: `cd ~/hebbot && setsid ~/curobo-venv/bin/python scripts/curobo_service.py >/tmp/svc.log 2>&1 </dev/null &`.)
- **`move_to_pose`** (in `hebbot/.venv`, stdlib `urllib` only) calls it when **`HEBBOT_CUROBO_URL`**
  is set, else uses analytical `ik_full`. `_curobo_ik()` is the client.

**Two things the integration handles:**
1. **Frame offset.** panda_py's configured EE is **~10.3 cm along tool +z** from cuRobo's
   `panda_hand` (`_HAND_EE_OFFSET`). The client translates the EE-frame target to panda_hand
   before asking; FK-verified to land the panda_py EE *exactly* on target (0.0 mm).
2. **Continuity.** cuRobo IK returns a *global* optimum that can be a 2.9 rad swing. Seeding with
   `current_state` (+ `return_seeds=8`, pick nearest) keeps it to ~0.6–1.5 rad. Requires
   `use_cuda_graph=False` (varying problem shape per call).

**Safety:** the client result is FK-verified (EE within 1 cm of target) and swing-guarded
(max joint Δ < 2.5 rad); any miss/timeout falls back to analytical `ik_full`. Result reports
`ik_source: "curobo" | "analytical"`.

**Status:** validated arm-free (offset exact, continuity bounded, fallback works). The only
untested piece is the live `move_to_joint_position` execution — pending the Panda being powered on.

**Known tradeoff / next tuning:** at hard near-base poses, home-seeding can force J4 near its limit
(still hits target + collision-free, unlike analytical which jams *and* misses). A limit-margin-aware
pick among the 8 seeds, and/or routing through `MotionPlanner.plan_pose` for full collision-free
*trajectories* (not just endpoint configs), is the Phase 1b refinement.

## Phase 1b — orientation-held trajectory execution (built + validated 2026-06-25)

**Key result:** cuRobo's `MotionPlanner.plan_pose` trajectories hold the gripper vertical along the
*whole path*, not just the goal — verified arm-free by FK'ing every waypoint: **max tilt 0.02°** to
(0.45,0,0.30), **0.60°** to the near-base (0.32,0,0.12). The old joint point-to-point let orientation
wander freely. (No explicit orientation constraint needed for top-down moves; `plan_grasp` adds a
linear approach when wanted.)

- **Service `/plan`** (`curobo_service.py`): `plan_pose(start_q, pose)` → 7-DOF joint waypoints + dt (0.025 s).
- **Executor** (`scripts/exec_traj.py`): streams the waypoints through panda_py's **`JointPosition`**
  controller in a `create_context` loop. **Validated live on the arm:**
  - 0.4× speed → **2.9 mm** accuracy, **max tilt 0.56°** through the move.
  - full speed → 5.9 mm, max tilt 0.83° (controller lags a bit more when faster → slower = more accurate;
    a settle-at-goal would tighten full-speed accuracy).

The executor needs FCI, so today it runs standalone (stop the MCP server first). **Next:** move it inside
the MCP's panda connection (behind `move_to_pose`, `planned=True`), wire `plan_grasp` (orientation-held
approach→grasp→lift), then the grasp-pixel servo on top → the autonomous vision grasp.

## Production status (2026-06-26) — cuRobo is the motion backend under the whole stack

cuRobo's collision-aware IK is now the **default** behind `move_to_pose` (set `HEBBOT_CUROBO_URL`,
defaults to the local service), FK-verified + swing-guarded with analytical `ik_full` as the fallback.
Every higher-level capability built since runs on top of it and depends on its two properties —
**clean, continuous configs** and **held orientation**:

- **Autonomous vision grasp** — calibration-free, image-Jacobian visual servo to a recorded
  "grasp-pixel", then a compliant grasp. No hand-eye `X` in the loop.
- **Pick-and-place / auto-reset** — `vision_grasp` + `place_block`; height-robust (coarse-to-fine
  descending servo) and multi-colour (blue/green/red).
- **Block stacking** — `stack_on`, force-sensed contact-stop; built a 3-high tower (blue·green·red)
  repeatably. Stacking onto an elevated cube needs a parallax correction (`onto_top`), evaluating the
  grasp-pixel at the reduced camera-to-target distance.
- **Reorientation** — `stand_on_end`, the first non-top-down maneuver (grip cross-section → pitch the
  wrist 90° → balance-place), all via cuRobo's arbitrary-orientation planning.

**Ops:** the planning service runs as a **systemd service** on omen (`/etc/systemd/system/curobo.service`,
`Restart=always`) — auto-starts on boot, self-heals on crash. Manage with `systemctl status curobo` /
`journalctl -u curobo`.

The full reasoning trail (every finding, every live-verified result, every dead end) is in
`lab_notebook.md` — that running log + the git history *is* the agentic-development transcript.
