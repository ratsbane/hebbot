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
