# Hebbot Lab Notebook

*A running log of experiments, findings, and reasoning for the Hebbot project. Started 2026-06-05 after renaming from `panda-mcp`. Prior 98 findings in the `panda-mcp` lab notebook remain valid as historical reference; new findings continue from #99.*

---

## 2026-06-05 — Project rename + OMEN setup complete

Hebbot started today as a fresh repo following the [2026-05-28 reset](https://github.com/ratsbane/panda-mcp/blob/main/lab_notebook.md) in the prior `panda-mcp` notebook. The decision to rename came after iterating on the thesis statement to its current form: *use agentic AI to bootstrap a system with continuous learning*. The name **Hebbot** honors Donald Hebb (Hebbian learning) + robot + the *good hebbot / good habit* pun, and is search-clean (no existing tech usage).

### What's working as of today

The new host machine (`omen`, an HP OMEN 16L with RTX 5060 Ti 16GB, Ubuntu 26.04, real-time kernel) is fully operational and connected to the Franka Panda. End-to-end chain verified yesterday:

- OMEN ↔ Linux 26.04 + PREEMPT_RT kernel ↔ NVIDIA driver 595 + CUDA 13.1 ↔ panda-python venv (Python 3.12 via uv) ↔ Ethernet w/ NetworkManager `shared` mode (dnsmasq DHCP) ↔ Franka FCI ↔ libfranka 1 kHz control loop ↔ **physical 7-DOF arm motion**

Full setup details in [`docs/panda-setup.md`](docs/panda-setup.md).

### Findings

| # | Finding |
|---|---------|
| 99 | The Franka's network interface is a **DHCP client**, not a static-IP peer. A Linux host on the direct-Ethernet link must run a DHCP server (NetworkManager `ipv4.method=shared` does this via dnsmasq). Static-IP on both ends fails silently — Franka has no IP and emits no packets. This was the morning's hardest debug; documented end-to-end in `docs/panda-setup.md`. |
| 100 | NVIDIA's open kernel module refuses to build on PREEMPT_RT kernels by default — sanity check at `Kbuild:305: preempt_rt_sanity_check`. Removing the check from the Kbuild and rebuilding via DKMS works; modules need to be signed with the MOK key and enrolled at first boot (Secure Boot kept on). NVIDIA + PREEMPT_RT then coexist on a single kernel. Officially unsupported by NVIDIA; works in practice. |
| 101 | D405 needs `dev.hardware_reset()` from librealsense at the start of every session — USB unplug alone doesn't reset the camera's internal state, and subsequent pipe.start() succeeds but never delivers frames. Calling hardware_reset + 5-second wait before `pipe.start()` solves it. |
| 102 | `panda-python` ships pre-built wheels for Python 3.10–3.12 only as of mid-2026; no wheels for Python 3.13+. Ubuntu 26.04 ships Python 3.14 by default, so the project venv must explicitly use Python 3.12 (uv handles this cleanly with `uv venv --python 3.12`). |

### Open threads

- **pyzed Python bindings fail to open the ZED 2i** on this system, despite C++ `ZED_Explorer` working fine. Same error from Stereolabs's own sample code. Probably a Stereolabs SDK 5.3.1 + Python 3.12 + Ubuntu 26.04 environment issue. Parked for later debug; not blocking robot work.
- **First end-to-end pick attempt** still to do. Wrist-camera-only depth detection works mechanically but the arm at `move_to_start` pose isn't looking at the block area — need a deliberate survey pose, or plug in the D435 scene camera to use the existing perception pipeline.
- The previously-collected ~100 skill episodes on `panda-mcp` are mostly from a single-viewpoint setup; whether to selectively port them or treat as discardable per the environment-diversity finding (#89 in `panda-mcp` notebook) is a near-term decision.


---

## 2026-06-06 — First running substrate: 6 skills live-verified, MCP boundary up

Built the Hebbot substrate end-to-end today, from foundations doc through working code, live-verified on the Franka. The repo went from "README + foundations" yesterday to a working library with 5 primitives + 1 composed skill today.

### What's running

**Substrate library** (`hebbot.substrate`):
- SQLite store implementing the foundations Appendix-A schema (skills, episodes, frames, states, decisions tables).
- Skill dispatcher with body-type runners. `classical` and `composition` body types implemented; `script`, `trajectory`, `learned_policy`, `vlm_grounded` are stubbed and raise `UnsupportedBodyTypeError` cleanly.
- Episode lifecycle: `start_episode` → run body → `end_episode` with success + outcome. Parent/child linkage via `parent_id` works.

**Primitives** (`hebbot.skills.primitives`, all live-verified):

| Skill | Body | Live accuracy |
|---|---|---|
| `get_state` | classical | reads live joints, EE pose, force, gripper width |
| `move_to_joints` | classical | ~1.2 mrad max error at speed_factor 0.05 |
| `move_to_pose` | classical | uses `ik_full` with closest-joint branch selection (mock-tested; live untested at intricate poses) |
| `open_gripper` | classical | within ~17 μm of target width |
| `close_gripper` | classical | within ~260 μm of full close |

**Composed skill** (`hebbot.skills.composed`):

- `home` — composition of `open_gripper(width=0.08)` + `move_to_joints(HOME_Q, speed_factor=0.1)`. Verified live; SQLite shows correct nesting: one `home` episode (parent_id=NULL) with two child episodes (parent_id=home.id).

**MCP server** (`hebbot.mcp_server`):

- Single MCP server, stdio transport. CLI entry point `hebbot-mcp` (set up via `pyproject.toml` `[project.scripts]`).
- Dynamically generates one MCP tool per skill row in the store. Tool schema comes from `skills.params_schema`. Adding a skill = inserting a row; no server restart needed for the change to be visible on `tools/list`.
- Verified end-to-end via direct JSON-RPC stdio handshake (`initialize` + `notifications/initialized` + `tools/list` over `ssh omen hebbot-mcp ...`). All 6 skills appear in the tool list.
- `.mcp.json` committed in repo root: spawns the server via SSH so a macbook-side Claude Code session can call substrate skills on omen.

**Tests**: 8/8 passing in mock mode. Covers primitive registration, dispatch, schema validation, composition execution, parent-child nesting, arg_map pass-through, halt-on-failure.

### Findings

| # | Finding |
|---|---------|
| 103 | The classical-body dispatcher pattern — body JSON `{"function": "module.path:function_name"}`, importlib resolution at call time, cached lookup, optional `_context` kwarg auto-detection — is clean and avoids any registration-time imports. Adding a new primitive = write the function + add a `PRIMITIVE_SPECS` entry. No decorators, no globals beyond the cache. |
| 104 | Composition body type as `{"steps": [{"skill": name, "args": {...}, "arg_map": {...}}, ...]}` is enough for the cases tested. `arg_map` (child arg → parent param name) handles the parent → child param propagation; static `args` handles the rest. Halt-on-failure with `completed_steps` + `sub_results` trail gives the orchestrator everything it needs to diagnose mid-composition failures. |
| 105 | SSH-wrapped stdio MCP is a clean pattern for remote substrate: `"command": "ssh", "args": ["omen", "hebbot-mcp", ...]`. SSH provides bidirectional stdio transparently; the MCP client (Claude Code) doesn't need to know the server runs on another machine. No SSE/HTTP needed. |
| 106 | The substrate's hardware-bootstrap path (try to connect, fall back to mock if failed) lets the same code base run on a workstation without a robot — useful for development and CI. Skills check for the relevant context key (`panda`, `gripper`) and return mock data if missing. The episode's `outcome.result._source` field distinguishes ("mock" vs "live"); future retrieval / distillation passes can filter on this. |
| 107 | `parent_id` foreign key on `episodes` (self-referential) cleanly captures the composition tree. One SQL query returns the full episode tree for a top-level call (`WITH RECURSIVE`). Same query also gives the right structure for the planned RAG retrieval: "show me past `home` executions and their sub-step outcomes." |

### Open threads / what's not built yet

- **MCP boundary not yet exercised from Claude Code itself.** Direct JSON-RPC handshake passes, but the loop "Claude reads tool list → decides to invoke → tool returns → Claude interprets" hasn't been run. That's the next session's first task.
- **`script` body type** still stubbed. This is the bridge to "the orchestrator creates new skill records at runtime" — without it, all skills require a code change to add. Probably the highest-leverage next implementation.
- **`trajectory` and `learned_policy` body types** stubbed. These come later as data accumulates.
- **Frame recording** not wired to episodes yet. The `frames` table exists; no perception primitive writes to it. Next-after-script chunk.
- **Perception pipeline** (detection → segmentation → 6-DoF pose → relational state vector, per commitment #11) not built. Foundations doc has the design; no code yet.
- **Decision logging** (`decisions` table from foundations Appendix A) not implemented in the runtime. Every dispatcher call should be logging.
- **pyzed bug** from setup day still parked — not blocking, C++ ZED tools work.

### What this means for next session

The substrate is *real enough* now that the next session can pick up at any of: implementing `script` body type, building the perception primitive set, wiring decision logging, or doing the MCP-from-Claude-Code round trip. Pick whichever has the most leverage.


---

## 2026-06-06 (later) — MCP round-trip from Claude Code, `move_to_pose` IK fix, first camera frame

Picked up the "MCP boundary not yet exercised from Claude Code itself" open thread and closed it, plus fixed a `move_to_pose` bug it surfaced and got the first camera frame off the D405.

### MCP round-trip, for real

The loop the prior entry flagged as untested — *Claude reads tool list → decides to invoke → tool returns → Claude interprets* — ran end to end this session via the SSH-wrapped stdio server in `.mcp.json`. Five of six skills verified live through the MCP boundary in one sequence: `get_state`, `move_to_joints` (small base rotation, 2.2 mrad max error), `close_gripper` (grasped at 0.3 mm), `open_gripper` (79.8 mm), and the `home` composition (both child steps, back to ready pose). The substrate's `_source: "live"` tagging made it trivial to confirm none of it fell back to mock.

### The `move_to_pose` quaternion bug (finding #108)

`move_to_pose` was the one skill that failed — and it failed for *every* target, including a gripper-down pose 4 cm from where the arm was physically sitting. That "can't even reach next to itself" symptom is what made it obviously a code bug rather than a reachability limit.

Root cause: the skill documents and accepts orientation as scalar-first `[w, x, y, z]` but passed it straight to `panda_py.ik_full`, which wants scalar-last `[x, y, z, w]` (Eigen convention). The default gripper-down quaternion `[0, 1, 0, 0]` was therefore read as a 180° rotation about **y** (gripper sideways), not about **x** (gripper down) — and there's no IK solution that points the gripper sideways at a downward-facing table target, so it failed cleanly every time. Confirmed empirically against the live arm: `ik_full` at the current ready pose returns all-NaN for `[0,1,0,0]` and 4 valid branches for `[1,0,0,0]`. One-line reorder before the call; API convention unchanged. Verified the fixed path resolves a spread of gripper-down targets at ~0.4 mm.

### First camera frame (finding #109)

Got the D405 wrist cam delivering frames into the project venv — `scripts/capture_realsense.py`, color + colorized depth, 640×480. Finding #101's `hardware_reset()` + settle-before-`start()` recipe was necessary as documented. Depth coverage ~72% on a glossy white tabletop (low texture + specular = hard for stereo), but the color stream is clean and shows the block scene clearly.

### Findings

| # | Finding |
|---|---------|
| 108 | `move_to_pose` orientation convention bug: the skill's documented API is scalar-first `[w,x,y,z]` but `panda_py.ik_full` (and `ik`, and the Eigen layer under them) expect scalar-last `[x,y,z,w]`. Passing the gripper-down default `[0,1,0,0]` unconverted is read as 180° about y, not x → no IK solution for *any* downward target, failing identically regardless of position. The "fails even adjacent to the arm's own pose" signature is the tell that it's a convention bug, not reachability. Reorder `[w,x,y,z]→[x,y,z,w]` before `ik_full`. |
| 109 | The **D405 color stream is genuine RGB**, not grayscale — unlike the D435 (IR/grayscale stereo imagers + a separate dedicated RGB sensor), the D405 uses a *color stereo pair*, so color comes straight off the depth imagers. Verified against captured pixels (mean per-pixel channel spread 30.8; 88% of pixels non-gray) and the visible scene. Practical consequence: on the D405 you get aligned color + depth from one short-range module with no separate RGB sensor to calibrate against. |

### Process note

The running stdio MCP server holds the FCI connection for the whole session and does **not** hot-reload edited modules (importlib's `sys.modules` cache means "resolve at call time" still returns the already-loaded module). So a code fix to a primitive isn't visible through the live MCP tool until the server is restarted — and you can't open a second `panda_py.Panda` connection alongside it to test out-of-band. The fix above was verified by exercising the real fixed function against a fake-panda harness (real `fk`/`ik_full` math, no FCI), which is enough here because the motion step reuses the already-live-verified `move_to_joints` path. Worth a cleaner story (server-side reload signal, or a dev path that doesn't hold FCI) before primitive iteration gets frequent.

### Open threads carried forward

- ~~`move_to_pose` still wants a **live** arm-motion confirmation through the MCP tool once the server is restarted on the fixed code~~ **Done** — after reconnecting the MCP server on the fixed code, `move_to_pose([0.45, 0, 0.40])` (the pre-fix failing target) executed live through the tool: arm moved, 2.97 mm Cartesian position error at speed_factor 0.1. All six skills now live-verified through the MCP boundary.
- D405 capture is a standalone script, **not yet a substrate primitive** — no `read_camera` / perception primitive writes to the `frames` table yet. That's the bridge from "camera works" to "episodes have images."
- Everything from the prior entry's not-yet-built list (`script` body type, decision logging, relational-state perception pipeline) still stands.


---

## 2026-06-06 (later still) — `read_camera` is a substrate primitive; frames table now has a writer

Closed the "no perception primitive writes to the `frames` table" open thread. `read_camera` now exists as a `classical` primitive, captures from the D405, and records its frames into the episode — verified live on the arm-free camera path.

### The substrate change that made it possible (finding #110)

The blocker wasn't the camera — it was that the classical body-runner only injected `_context` (hardware handles) into a primitive. It had no way to hand a primitive the *store* or the *current episode id*, so no primitive could write a `frames` row (which is `NOT NULL` on `episode_id`). Rather than special-case cameras, generalized the injection: a primitive opts into any of `_context`, `_store`, `_episode_id` by declaring them in its signature, and the runner supplies what's declared (`inspect.signature` membership test, same shape as the old `_accepts_context` check). Primitives that declare none are still called with just their params. This is the seam every *persisting* primitive needs — `read_camera` writes frames through it today; decision-logging and per-step state recording will write `decisions` / `states` through the same seam without further runner changes.

### `read_camera` (finding #111)

- Reads aligned color (+ depth) from a camera handle the context supplies under `"cameras"` (a `{name: RealSenseCamera}` map). Camera bootstrap — the finding-#101 `hardware_reset` + settle, depth/color stream config, role-naming (D405→`wrist`, D435→`scene`) — lives in `hebbot.perception.realsense`, so the primitive stays driver-agnostic and just calls `cam.capture()`.
- On `save`, writes the color frame as JPEG and the depth frame as a **lossless `.npy`** (uint16 — JPEG-ing depth would corrupt it) under the Appendix-A `frames/YYYY/MM/DD/<episode_id>/` layout, then inserts one `frames` row per image (`wrist` for color, `wrist:depth` for depth). Paths stored relative to the data root, as the schema comment intends.
- Mock mode (no camera in context) returns metadata with null paths and writes nothing — keeps tests/CI hardware-free; the fake-camera test exercises the real save + frames-write path under `pytest.importorskip("PIL")`.

Live result on omen (isolated `/tmp/hebbot-camtest` data dir to avoid churning the production DB): D405 opened as `wrist`, 640×480, center-pixel depth 0.507 m, 72.9% depth coverage; color JPEG + depth `.npy` written and **two `frames` rows** created, both correctly linked to the `read_camera` episode. The episode id appearing in the saved path is the proof the `_episode_id` injection works end to end.

### Findings

| # | Finding |
|---|---------|
| 110 | The classical body-runner generalizes cleanly from "inject `_context`" to "inject any substrate-supplied kwarg the primitive declares" (`_context`, `_store`, `_episode_id`). Signature-membership test, no registration changes, backward-compatible with primitives that declare only `_context`. This is the single seam through which *persisting* primitives reach the store and their episode — frames now, decisions/states next — so it's worth having gotten the shape right before three subsystems each invent their own. |
| 111 | A `classical` primitive can own a full capture-and-persist path: read aligned color+depth, save color as JPEG + depth as lossless `.npy` (never JPEG depth), write one `frames` row per image under `frames/YYYY/MM/DD/<episode_id>/`. Depth aligned to color (rs.align) so a color (row,col) indexes the same point in depth — needed for the relational-state work in commitment #11. The camera driver wrapper belongs in `perception/`, not in the primitive: the primitive should be driver-agnostic and just call `.capture()`. |

### Process / smaller notes

- Same MCP-server-no-hot-reload constraint as before: `read_camera` and the camera bootstrap won't show up on the **running** server until it's restarted (`--register-primitives` re-registers the new skill; `make_hardware_context` now opens the D405, adding ~5 s settle to startup). Verified live via a standalone substrate call instead — and the D405 was free because the running server holds only the FCI arm connection, not the camera.
- `make_hardware_context` now loads cameras **independently** of the arm — a missing/declined robot no longer forces an empty context, so camera-only (perception-development) sessions work. Added `--no-camera` / `--camera-settle`.
- Saved-frame date path is UTC (`time.gmtime`) — it read `2026/06/07` while the local clock was still on the 6th. Intentional: UTC keeps the frames tree monotonic regardless of timezone.

### Open threads carried forward

- ~~`read_camera` still wants a **live** confirmation through the MCP tool once the server is restarted on this code~~ **Done** — after reconnecting, `read_camera` ran live through the MCP tool against the production data dir: `wrist`, 640×480, center depth 0.530 m, 71.6% coverage, both `frames` rows (`wrist` + `wrist:depth`) written to `~/hebbot-data/hebbot.db` and tied to the `read_camera` episode. First real frames in the production DB. Full path verified: Claude Code → MCP → substrate → D405 → frames table.
- Depth is saved as raw `.npy` per frame; no compaction (Parquet/HDF5 per episode) yet — fine at current volume, revisit when episodes get long.
- Decision logging and per-step `states` recording are the obvious next users of the finding-#110 injection seam.
- Still standing: `script` body type, the relational-state perception pipeline (detection → segmentation → 6-DoF pose) that turns these raw frames into the state vector of commitment #11.


---

## 2026-06-06 (evening) — Decision logging + expectation model (commitment #1, slice 1)

Two substrate layers built on top of the finding-#110 injection seam: every call now logs a routing decision, and skills can declare expectations that turn an off-prediction result into a *surprise*. This is the first concrete piece of commitment #1 (bidirectional feedback) in the runtime.

### Decision logging (finding #112)

Every top-level skill call writes a row to the `decisions` table. Today every decision is `orchestrator` (the orchestrator chose the skill; the substrate ran it as registered — there's no replay/policy retrieval yet to route otherwise), but the trail is now recorded so it exists when routing logic arrives. A `_Decision` recorder is created per dispatch and committed *after* execution so the outcome is real, not predicted. Skill bodies opt into richer logging through the same seam: a body declaring `_log_decision` gets a bound recorder and can log `replay`/`policy` with candidates + relational_state + reasoning; the dispatcher stamps the outcome. Composition children are **not** baseline-logged — they're part of the parent's execution, not independent routing decisions (verified: `get_state + home + move_to_joints` → 5 episodes, 3 decisions).

### Expectation model / surprise detection (finding #113)

A skill record can carry `expectations` — forward-prediction bounds on its result. After a classical body runs *without raising*, the dispatcher evaluates the result; a violated bound is a **surprise** (the action completed, but not as predicted). Three outcomes now exist where there were two: `success` / `surprise` / `fail`.

- `move_to_joints` predicts `max_error ≤ 0.05` rad, `move_to_pose` predicts `position_error ≤ 0.02` m, `close_gripper` predicts `grasped == true`. Bounds chosen against this session's live numbers (joint settle ~0.004, pose ~0.003).
- A surprise sets `success=False` + `surprise=True` + `surprise_events` on the outcome, so (a) compositions **halt** instead of blindly continuing, and (b) the orchestrator sees it — the MCP server returns the outcome verbatim, which *is* the feedback channel for this slice. No separate bus needed yet.
- **Missing field → skip the check**, deliberately: mock results omit live-only fields like `max_error`, and we don't want mock/CI runs to spuriously surprise. The cost is that a genuinely-missing live field passes silently; acceptable for slice 1.
- Comparison errors (bad op, non-numeric field) yield a *flagged* event rather than crashing dispatch — surfacing a malformed expectation instead of hiding it (the retrospective's "observability for succeeded-but-bad" lesson, applied to the checker itself).

### Findings

| # | Finding |
|---|---------|
| 112 | Decision logging belongs at the dispatcher (it owns routing + has store/episode directly), but the *enrichment* belongs at the body via the injection seam — a body that actually routes (`replay`/`policy`) records its choice + candidates through injected `_log_decision`, and the dispatcher commits it post-execution with the real outcome. Committing post-execution is what lets a body decide *before* running while the outcome is filled in *after* — the natural shape for replay-vs-execute decisions later. Baseline `orchestrator` rows for top-level calls only; composition children are part of the parent's execution, not separate decisions. |
| 113 | Surprise detection works as a thin post-run check of the result dict against declared bounds — no per-skill code, just data in the `expectations` column + one evaluator. The load-bearing design choice is **missing-field-skips**: it's what lets the same expectations ride harmlessly through mock mode, and makes expectations opt-in per field that actually appears. Setting `success=False` on surprise (not a separate axis) makes compositions halt for free and makes the orchestrator treat a surprise as "didn't cleanly succeed" — correct for the deliberate-layer-takes-over pattern. |

### The bigger frame this exposed: verification has three levels

Working through this with Doug surfaced that what got built is the **middle** of three levels of "did it work," and the most important one is still missing:

1. **Mechanical** — did the code run without raising? (free; blind to everything real)
2. **Proprioceptive** — robot's own sensors vs. predicted bounds: `max_error`, force, gripper width. *(this slice; cheap, automatic, every call; blind to the world)*
3. **Perceptual** — look at the scene and judge against the goal: "is the gripper holding the *right* thing?", "did the block actually land on the stack, or topple?" *(not built; expensive, checkpoint-based; the actual ground truth)*

Proprioception is **blind to the world**: `close_gripper` reporting `grasped: true` only means the fingers settled at the expected width holding force — it can't tell a cup from a same-width pen, and "place A on B" has essentially no proprioceptive signal at all. Perceptual verification is necessarily a different mechanism and belongs with the **deliberate layer**: it needs the goal (which lives in the orchestrator, not the primitive), needs perception + semantic judgment, and is expensive enough to run only at checkpoints. The elegant part: a failed perceptual check is *the same surprise on the same feedback channel* — proprioceptive and perceptual expectations are complementary (fast/weak-label vs. slow/strong-label), not competing. A perceptually-verified outcome is also the **strong ground-truth label** that makes the episode store worth distilling.

Design intent recorded in `docs/foundations.md` (commitment #1, "Verification has three levels"). It's gated on a VLM grounding primitive + the relational-state pieces of commitment #11, and best done via relational *geometry* (re-detect poses, check the relative transform) rather than asking a VLM "is A on B?" directly — small VLMs were at-chance on spatial reasoning (panda-mcp finding; retrospective). Pinning the interface waits until that perception exists, so we don't build a stub.

### Open threads carried forward

- **Run real tasks against this before building the verify interface** — a "grab the green block" attempt exercises read_camera + motion + grasp + the proprioceptive surprise model + decision logging end-to-end on real episodes, and forces the perception-localization question (pixel → robot frame) that perceptual verification depends on anyway. Right ordering: let the task reveal what perception is actually needed.
- Perceptual verification (`vlm_grounded` body / a `verify(predicate)` skill) — gated on a VLM grounding primitive + relational state.
- Expectation coverage: only 3 primitives declare expectations; the missing-field-skip means absent live fields pass silently — revisit when it matters.
- Decision logging + expectation model land in the **running** MCP server only after a restart (same no-hot-reload constraint); pure substrate, no hardware needed.
- Still standing from before: `script` body type; the detection → segmentation → 6-DoF pose pipeline (commitment #11).


---

## 2026-06-06 (late) — Perception stack up: GPU inference + Moondream grounding on Omen

Stood up the GPU inference stack and got Moondream2 localizing objects on Omen's RTX 5060 Ti. This is the perception foundation the green-block grab and the perceptual-verify layer both need. Most of the session was fighting a flaky NIC and CUDA version pins; the wins and the scars are below. Full reproducible recipe in [`docs/perception-setup.md`](docs/perception-setup.md).

### What works now

- **Blackwell GPU is usable for inference.** torch 2.11.0+cu128 installs and runs on the RTX 5060 Ti (`sm_120`, capability (12,0)); a CUDA matmul executes. The compatibility worry (new Blackwell card + nonstandard NVIDIA-on-PREEMPT_RT stack from finding #100) is resolved.
- **Moondream2 grounds objects accurately.** Loaded on GPU (rev `2025-06-21`, `trust_remote_code`). `point("green block")` returns normalized coords; on our 4-block desk frame it returned green/blue/tan/red in the correct left-to-right order, each landing on its block (annotated overlay confirmed). `detect()` gives boxes, `caption()` gives an accurate scene description. This is the localization primitive for the grasp task and the grounding for perceptual verification (#1's third level).
- Lives in a **dedicated `~/perception-venv`**, separate from the substrate venv — matches foundations' separate-perception-daemon design and keeps the substrate lean.

### Findings

| # | Finding |
|---|---------|
| 114 | Blackwell `sm_120` runs fine with **torch 2.11.0+cu128** (`uv pip install torch --torch-backend=cu128`). No special build needed beyond cu128 wheels; driver 595 is forward-compatible. |
| 115 | Omen's onboard NIC is committed to the Franka link, so internet rides the **flaky wifi card** — and **uv/pip don't resume partial downloads**, so multi-GB CUDA wheels (cudnn 627 MB, cublas 567 MB) restart from zero on every drop and *never converge*. Workaround that does converge: prefetch big wheels with `curl -4 -C - --retry` (resume keeps every byte), then `uv pip install --find-links`. Real fix incoming (USB wifi adapter). Keep the curl-resume pattern for any multi-GB pull regardless. |
| 116 | Two version pins make moondream2 load: **`transformers<5`** (its remote code uses the pre-5.x import API; 5.x fails as a masked `Could not import module 'PreTrainedModel'`) and **torchvision must be the `+cu128` build** (`--torch-backend=cu128` only fixes *torch*, so torchvision came from PyPI as cu130 and its CUDA-version check `RuntimeError` cascaded into the same masked error). Install the `+cu128` torchvision explicitly `--no-deps`. `timm` is what dragged the mismatched torchvision in. |
| 117 | A VLM grounder (Moondream) gives object localization with **zero calibration** — `point(predicate)` → normalized pixel — which is exactly what the planned auto hand-eye calibration consumes (detect a known-pose target across known EE poses → solve camera→EE). So the perception primitive and the calibration loop compose: grounder finds the pixel, calibration turns pixel+depth+EE-pose into robot coordinates. |

### Process notes

- The torch wheel URL is `%2B`-encoded (`+cu128`); the saved file must be renamed `%2B`→`+` or uv rejects the version. (Cost me a retry loop.)
- The masked `ModuleNotFoundError: Could not import module 'PreTrainedModel'` is transformers' lazy loader hiding the *real* import error — unmask it with `python -c "from transformers.modeling_utils import PreTrainedModel"` to see the true traceback (that's how the torchvision CUDA mismatch surfaced).

### Open threads / next

- **Moondream runs in-process in the test venv**; the foundations design is a separate perception daemon (FastAPI/ZMQ) the substrate calls. Not built — fine for now, revisit when the substrate needs live grounding.
- **No substrate primitive wraps Moondream yet** (a `locate(predicate)` / `vlm_grounded` body). Comes once the camera→robot geometry exists.
- Green-block task sequence still stands: server reconnect → touch-off (temporary `z_table`) → auto hand-eye calibration (move-to-known-poses + Moondream grounding, solve camera→EE) → analytic/servo grasp. Perception is now unblocked.


---

## 2026-06-07 — Xbox teleop + first grasp (full pick-and-place), touch-off & desk plane

First real manipulation on the substrate today, all human-in-the-loop via a new teleop primitive: grasped, lifted, carried, and placed the green block. Also did a touch-off to map the desk plane and fixed two bugs the session exposed.

### `teleop_xbox` primitive (finding #118)

A classical primitive that jogs the EE with an Xbox controller through panda_py's `CartesianImpedance` controller, run *inside the MCP call* on the held FCI connection (the call blocks while the human drives; START returns). Left stick = table plane, right stick = z, A/B = close/open gripper, START = exit. Gamepad read via `evdev` (codes confirmed live: ABS_X/Y/RY, BTN_A/B/START on the "Microsoft X-Box 360 pad"). It doubles as a **safe touch-off**: compliant control means driving into a surface stops at contact with bounded force — no hard collision reflex — so it returns `final_z`/`min_z_reached` as the table reading. Architecture note: teleop *has* to be a primitive (not a standalone script) because the MCP server holds the single FCI connection; running it in the call reuses that connection.

### First grasp → full pick-and-place (finding #119)

Drove the open gripper to straddle the green block, closed (A button), lifted clear (confirmed held at gripper_width ≈ 0.029 m), carried, lowered, released (B) on the surface, and homed clean (8.5 mrad, no surprise). First end-to-end manipulation. This is exactly the *deliberate execution* phase of the Hebbian arc (#2) — done by hand through the tools, generating episodes that later compress into a `pick` skill. We did **not** pre-build a pick skill; the point is to let the task teach its shape first.

### Touch-off + desk plane (finding #120)

Touched bare desk at four points and fit a plane: `z = 0.0124·x + 0.0084·y + 0.025`, **tilt only 0.86°**, RMS residual 2.5 mm against a 9 mm z-range — i.e., the desk is flat to within our touch-measurement noise; any real tilt is ≤ ~1°. `z_table ≈ 0.025–0.034 m` (EE-frame) across the workspace; grasp height for a desk block is EE z ≈ 0.03. Diagnostic insight: Doug expected the gripper tilt to *mirror* on the far side of the desk; it didn't, because teleop holds orientation fixed across the workspace, so the same fingertip leads everywhere — non-mirroring is evidence the tilt is a *fixed* cause (and it turned out to be mostly the teleop drift below, not the desk). The clean desk-vs-gripper test (unused): rotate the wrist 180° at one spot and re-touch — same physical tip → desk; other tip → gripper/orientation.

### Two bugs the session exposed, both fixed (finding #121)

| # | Finding |
|---|---------|
| 118 | An Xbox-teleop primitive is a clean fit for the substrate: run the gamepad→`CartesianImpedance` loop inside the (blocking) MCP call on the held FCI connection. Decouple the 6×6 impedance — firm rotational + nullspace stiffness to hold gripper-down and tame the elbow, compliant translation for soft contact — so the same primitive is both teleop and a safe touch-off. |
| 119 | First full pick-and-place (teleop) — grasp/lift/carry/place/home. Validates the motion + grasp mechanics end-to-end on a real object, and is the deliberate-execution seed for later skill compression. The grasp held a ~2.9 cm block; homing carried no surprise. |
| 120 | The desk is flat to ~1° (0.86° fit, noise-level over 9 mm). Single-fingertip contact + non-mirroring across the workspace is explained by *fixed-orientation* teleop, not desk tilt. A multi-point touch-off is a cheap, calibration-free way to get the work plane. |
| 121 | Two bugs, fixed: (a) **teleop orientation drift** — default Cartesian impedance held orientation only compliantly, so the gripper cocked ~25° and the elbow reached a contorted config over a session; fix = decoupled stiff-rotation/compliant-translation impedance + nullspace bias to start config. (b) **`close_gripper` errors re-grasping an already-held object** (libfranka "Command failed"); fix = catch it and treat an in-band held width as already-grasped (idempotent). |

### Open threads / next

- **Live-verify both fixes** on the next server restart: teleop orientation should now hold gripper-down across a session; `close_gripper` on an already-held object should no-op cleanly.
- **Auto hand-eye calibration** is the immediate next build (B): move to known EE poses, Moondream-`point` a fixed block in each, deproject with D405 depth + intrinsics, solve camera→EE (+ block position) by least squares. Unlocks autonomous localize-and-grasp.
- Carried over: Moondream as a separate perception daemon; a `locate(predicate)`/`vlm_grounded` substrate primitive; the TP-Link USB-wifi adapter still needs its driver sorted (deferred; onboard wifi + curl-resume works).


---

## 2026-06-07 — Fiducial-free hand-eye calibration + first AUTONOMOUS grasp

Closed the full loop: the arm located and grasped the green block with no human in the loop — from a camera→EE calibration solved using only the robot's own kinematics + Moondream + depth. End-to-end today: GPU/VLM stack → teleop → first (teleop) grasp → touch-off → hand-eye calibration → autonomous localize-and-grasp.

### Auto hand-eye calibration (finding #122)

Doug's idea: no ArUco, no tape measure — use the arm's known kinematics as ground truth. A standalone script (substrate library used **directly**, no MCP — litmus-test #6 in practice; `scripts/calibrate_collect.py`) drove the arm to 12 "look-at" poses around the green block (elevation 62–88°, azimuth ±45° — orientation variation is **required**: gripper-down-only poses leave the extrinsic translation tangled with the block position, only their combination observable). At each pose: `get_state` → EE pose Tᵢ, `read_camera` → wrist frame + intrinsics. Then `scripts/calibrate_solve.py` (perception venv): Moondream-`point` the block in each frame, deproject (pixel + aligned depth + intrinsics) → camera-frame cᵢ, and least-squares solve `Tᵢ·X·cᵢ = p` for the camera→EE extrinsic X and block position p (for fixed X the optimal p is mean(Tᵢ X cᵢ), so minimize the spread).

Result: **X translation [−0.046, 0.077, −0.056] m, rotation ≈ −108° about the optical axis** (that −108° is why the rough B1 estimate's y was so wrong — image axes ≠ EE axes), block at (0.483, −0.005, 0.036), **back-projection spread RMS 5.6 mm** across 12 views. Saved `scripts/calib_result_2026-06-07.json`.

### First autonomous grasp + the descent lesson (finding #123)

With X, autonomous localization is analytic (detect → deproject → `T·X·c`). Grasping at the calibrated p:
- **Attempt 1 (stiff `move_to_pose` descent):** xy dead-on (~1 mm at hover/approach) but a fingertip grazed the block and tripped `cartesian_reflex`, aborting the descent high → closed on air. Exactly the panda-mcp lesson (default libfranka collision thresholds are tight; no two-phase/recovery ported).
- **Attempt 2 (soft impedance descent):** stalled *high* (z=0.084, above the block) — soft vertical stiffness + a nullspace bias toward the high hover config fought the descent so it never reached grasp height.
- **Attempt 3 (firm impedance descent):** `set_impedance(diag([400,400,300, 30,30,30]))`, `nullspace_stiffness(1)` → reached z=0.044 cleanly and grasped (width 0.0285, **HOLDING**), `grasped=True` confirmed by the expectation model.

The lesson: **the grasp descent wants firm Cartesian impedance, not stiff position control** — firm enough to reach grasp height, force-limited so a contact deflects instead of hard-reflexing, and the nullspace bias must not hold the arm up.

### Findings

| # | Finding |
|---|---------|
| 122 | Fiducial-free hand-eye calibration works: move to varied known EE poses, VLM-`point` a fixed object in each, deproject with depth+intrinsics, least-squares `Tᵢ X cᵢ = p` for X (+p). 12 poses → 5.6 mm RMS, no markers/measurement. **Orientation variation across poses is mandatory** — gripper-down-only leaves X's translation unobservable (tangled with p). Run as a plain library script (litmus-test #6). |
| 123 | Autonomous localize-and-grasp closed end-to-end. The descent is the crux: **stiff position control hard-reflexes on the lightest contact; soft impedance + nullspace-to-hover stalls high; firm impedance (transl ~300–400, nullspace ~1) reaches grasp height and is force-limited so contact deflects.** This is the compliant, recovery-free substitute for panda-mcp's two-phase `franka_rt` descent — and it should become the basis of a `pick` primitive. |

### Open threads / next

- **Promote to skills:** the calibration + the firm-impedance grasp descent are scripts now; the grasp descent should become a `pick`/`grasp_at` substrate primitive (the deliberate-execution → skill compression step), and X should live as a stored calibration the substrate loads.
- **Full re-localize loop:** this grasp used the calibration-time p (block hadn't moved); the general path is survey → Moondream → `T·X·c` → grasp, which needs the Moondream/substrate venvs bridged (a `locate` primitive or the perception daemon).
- Carried: Moondream-as-daemon; `locate(predicate)`/`vlm_grounded` primitive; TP-Link driver; live-verify the teleop/close_gripper fixes through MCP.


---

## 2026-06-09 — Autonomous localization validated; accuracy is distance-dependent (finding #124)

Ran the full autonomous localization loop unsupervised on a fresh block scene (Doug freed the arm after the Evan collaboration wrapped): survey frame → Moondream `point(color)` → deproject (pixel + aligned depth + intrinsics) → `base = T·X·c` with the 2026-06-07 hand-eye `X`. Pipeline works — it returns plausible robot coords for all four blocks (green/blue/pink/tan) on a scene it had never seen, with a 2-day-old calibration. **But single-shot accuracy is much worse than the calibration's 5.6 mm RMS suggested**, and it's distance-dependent.

Measured by re-localizing the same block (green) from three gripper-down views and comparing:

| view | EE height | green estimate (x, y) |
|---|---|---|
| survey | 0.40 m | (0.493, −0.140) ← outlier |
| close | 0.18 m | (0.459, −0.175) |
| close | 0.22 m | (0.442, −0.169) |

The two **close** views agree to ~1.8 cm; the **far** survey is ~5 cm off. Blue showed the same survey-vs-close shift in the *same direction* — so it's a **systematic** error, not Moondream noise. `z` (depth) is reliable across all views (~0.071–0.079, <1 cm spread).

### Finding

| # | Finding |
|---|---------|
| 124 | Autonomous localization (Moondream `point` + hand-eye `X`) **works as a pipeline** but is only **~2 cm accurate at close range (0.18–0.22 m) and ~5 cm at 0.40 m** — distance-dependent, systematic (consistent across blocks/views), with `z` reliable (<1 cm). The calibration's 5.6 mm RMS was **optimistic**: its poses were all similar look-at views, so it never measured generalization to a far/overhead survey. Likely error sources: Moondream's centroid landing on different parts of a finite-size block across view angles, plus extrinsic-rotation error amplified at distance. Implication: a single far-survey localize is **not grasp-accurate**; autonomous grasp needs **coarse→fine** (survey to find, then approach + re-localize close, then grasp) and/or better object localization (segmentation + top-face centroid, or the 6-DoF relational pose of commitment #11). The open 8 cm gripper tolerates ~2 cm (close-view) error on a 2.5 cm block — marginally graspable; 5 cm (far) would miss. |

### Safety / process notes (unsupervised run)
- Stayed **descent-free** the whole time (gripper-down hovers + captures only) — no grasp attempts: the compliant-descent grasp isn't an MCP primitive yet (adding it needs a server restart, which needs Doug's `/mcp`), a stiff-descent grasp risks a reflex that I couldn't clear unsupervised, and there's no e-stop in hand. Localization validation needs none of that.
- One close view landed in a contorted IK branch with elevated force *estimates* (no real contact — homed cleanly after).

### Next
- **Coarse→fine autonomous grasp:** survey to locate → approach to ~0.20 m → re-localize → grasp. Needs the compliant `pick_at` primitive (restart) + a supervised first run.
- **Tighter localization:** segment the block + use the top-face centroid (steadier than Moondream's point across views), or 6-DoF pose; consider re-calibrating with more diverse pose *distances/heights* so the RMS reflects real accuracy.

**Follow-up (same session): the error is geometric, not detection.** Compared Moondream `point` vs `detect` bbox-center for green across the 3 views — they agree to ~1 mm and give the *same* ~50/35 mm cross-view spread. So switching the 2D localization method won't help; the systematic far-vs-close error lives in the **calibration `X` / depth deprojection** (X fit only on close look-at poses → poor generalization to the 0.40 m overhead survey). Fix is on the geometry side: **re-calibrate with pose distances/heights spanning the working range** (and/or coarse→fine localize from close), not a better detector. `scripts/localize_blocks.py` + the bbox-vs-point comparison were run unsupervised on saved frames (arm-free).

**⚠️ CAVEAT (added after the fact): #124 is likely contaminated by a loose camera.** Right after this session the D405 dropped frames — root cause was *physical*: a cable tie had worked loose and the cable got caught and pulled partly out of the camera. That means the camera (or its connector) was **working loose during these very captures**, so the ~2–5 cm cross-view inconsistency may be the **camera physically shifting between views**, not the calibration pose-diversity I concluded. The geometric-error conclusion still points at the calibration, but the *cause* is more likely a moving/stale camera mount than pose distribution. Cable reseated; **`X` must be re-validated / re-done on the now-stable camera** before trusting any localization numbers. A supervised compliant grasp at the (suspect) localized green also stalled — a fingertip hit the block top (xy off by > straddle margin), consistent with a stale/contaminated `X`; the soft descent stopped safely without grabbing or crashing.

---

## 2026-06-09 — Recalibration on the stable camera (confirms #124 was a stale/wrong `X`)

After reseating the camera cable, re-ran hand-eye calibration with **distance diversity added** (`calibrate_collect.py` now orbits at 0.24 / 0.30 / 0.38 m × 4 azimuths × 3 elevations = 12 poses, EE z spanning 0.24→0.41 m) — the fix #124 called for. Solver (single fixed target = green, minimize cross-pose back-projection spread): **RMS 5.2 mm, max 14.1 mm, 12/12 poses**.

The new `X` vs the stale 2026-06-07 `X`, localizing the *same* green block:

| | stale X (2026-06-07) | **new X (2026-06-09)** | reality check |
|---|---|---|---|
| green top **z** | 0.075 m | **0.039 m** | matches GRASP_Z 0.038 (known good teleop grab height) ✓ |
| green **xy** | (0.44, −0.166) | **(0.43, −0.093)** | ~7 cm apart in y |

### Finding

| # | Finding |
|---|---------|
| 125 | **#124's distance-dependent error was mostly a stale/wrong extrinsic, not a detector or pose-distribution limit.** The 2026-06-07 `X` placed green's top 4 cm in the air (z=0.075) and ~7 cm off in y. Recalibrating on the stable camera with distance-diverse poses gave a self-consistent `X` (5.2 mm RMS over 3 distances) whose green-top **z=0.039 m matches the empirically-known grasp height** — a physical corroboration the old `X` never had. **Physical proof the new `X` is right:** a compliant descent at the new (0.43, −0.093) *hit a block* (fingertip on top, stall-guard lifted) — 7 cm from the stale-X location, where descending would have found only bare table. So recalibration cut a ~7 cm gross error down to a **~2 cm residual** in xy. That residual sits right at the straddle margin (8 cm open fingers, ~3–4 cm rotated block) → open-loop grasp still catches a fingertip on the block top. **Open-loop localize→descend is now calibration-limited at ~2 cm; closing it needs visual servoing at the approach** (re-image near the block, re-center, descend), the originally-scoped fix. New extrinsic saved: `scripts/calib_result_2026-06-09.json`. |

---

## 2026-06-09 — FIRST SUCCESSFUL AUTONOMOUS GRASP (touch-off ground truth)

Doug teleop-jogged the **closed** gripper tip onto green's **top-center** and pressed START; FK gives green's true base position with no calibration in the loop. Touch: **(0.464, −0.111), EE z=0.073 at contact**. Then grasped at that xy with the compliant descent → **`grasped=True`, width=0.029, lifted HOLDING**. Placed back. (Green is a **2.9 cm** cube — narrower than estimated, so the straddle margin is genuinely tight.)

How wrong calibration had been, vs the touch ground truth:

| source | green xy | error |
|---|---|---|
| calib consensus (12-view) | (0.43, −0.093) | 3.8 cm |
| single overhead view | (0.50, −0.083) | 4.6 cm |
| **touch-off (FK truth)** | **(0.464, −0.111)** | — |

### Findings

| # | Finding |
|---|---------|
| 126 | **The grasp was never the problem — localization was.** At FK-ground-truth xy, the compliant descent grasps a 2.9 cm cube first try. Single-point hand-eye calibration is **metrically unreliable to ~4 cm** even at 5 mm RMS (RMS measures self-consistency, not accuracy; the two calib-based localizations missed by ~4 cm in *opposite* directions). The fix that worked: **touch-off** — jog the closed tip to a block's top-center, read FK. To grasp from *vision*, the touch-off points must become ground-truth anchors that properly constrain `X` (multi-point), or close the loop with visual servoing. |
| 127 | **Stall-guard semantics, post-touch-off:** with the block top known at EE z≈0.073, a compliant-descent stall *below* that (≈0.060) means the open fingers are past the top → straddling → **close** (light side-graze on a rotated narrow cube); a stall *at/above* the top height means a finger is on top → refuse (grab-air). Raised guard 0.055→0.068. Also: re-level to true gripper-down (`[w,x,y,z]=[0,1,0,0]`) before descending — don't inherit teleop tilt. |
| 128 | **Teleop/IK robustness gaps (from the touch-off):** (a) the down (−z) jog **stalled near the block** — IK couldn't solve straight-down Cartesian motion in that arm config, forcing a side-to-side wiggle to reach contact; (b) the gripper held a **few-degree tilt** off vertical (tip offset ~3.4 cm below EE → ~2–6 mm lateral error at the tip — negligible for grasping, but it biases a touch-off used as a calibration anchor). Both worth fixing before relying on teleop for precise touch-offs. |
| 129 | **Teleop fixes (mostly resolved #128):** (a) **descent stall was the nullspace bias**, not IK — `set_nullspace_stiffness` 20 pulled the elbow toward the (high) session-start config and resisted reaching down; softening 20→10 let the down-jog reach EE z=0.025 with every joint mid-range (`joints_at_min_z` confirmed no saturation). (b) **Tilt** addressed in two parts: force a truly-vertical hold orientation at start (`_level_gripper_down`, preserves yaw) **and** raise rotational stiffness 30→100 (Cartesian-impedance entries 3–5). Leveling alone wasn't enough — at stiffness 30 the gripper drifted ~9.5° under jogging; 100 cut it to ~3° central / ~5.7° at extreme reach (e.g. y=+0.20, J1 near twist limit). **Residual: control-crossover leakage** — translation jogs leak into orientation because the impedance isn't perfectly decoupled; stiffness only masks it. A properly decoupled / feedforward controller is the real fix (deferred). Doug's call: good enough for touch-offs now. |

---

## 2026-06-25 — cuRobo motion layer + grasp-pixel calibration

(Full cuRobo install/integration narrative in `docs/curobo-integration.md`.)

| # | Finding |
|---|---------|
| 130 | **Grasp-pixel = the camera→gripper offset, captured calibration-free.** Touched off blue (gripper dead-center, FK truth = block xy), lifted straight up, segmented blue's centroid: when the gripper is over a block at approach height **z=0.20, the block sits at pixel (357.5, 372.7)** — +37 px right, +133 px below image center. Deprojecting that (depth 176 mm) gives a **~60 mm metric camera-axis-to-gripper offset** — exactly the scale of every grasp miss we fought. So vision grasping = *drive any block to (357.5, 372.7) at z=0.20, descend straight down*; no reliance on the never-trustworthy hand-eye `X`. **Caveat: strongly orientation-dependent.** The pixel drifts ~69 px over 10 cm of height (camera is tilted ~ a few °), and any gripper tilt re-projects it (~7 px/deg) while yaw rotates it around. So it's valid **only at the measured orientation** (dead-vertical gripper-down). Fine for fixed top-down picks *if orientation is held*; a proper multi-point metric `X` is the orientation-agnostic alternative. |
| 131 | **cuRobo fixes the "maintain gripper angle while moving" problem the old IK couldn't.** Old `move_to_pose` = one analytical IK endpoint + a joint point-to-point move → orientation uncontrolled mid-path (wanders). cuRobo `MotionPlanner.plan_pose` trajectories hold the gripper vertical along the **whole path**: FK of every waypoint shows **≤0.6° tilt**. Built `/plan` (service) + a `JointPosition`-streaming executor (`scripts/exec_traj.py`); **validated live** — 0.4× speed reached target within **2.9 mm, max tilt 0.56°**; full speed 5.9 mm / 0.83°. This directly enables the robust top-down grasp the grasp-pixel needs (orientation held through approach + descent). Also: cuRobo collision-aware IK is now the default behind `move_to_pose` (3 live moves, all `ik_source=curobo`, sub-2 mm, incl. a hard near-base pose that used to jam). |

---

## 2026-06-25 (evening) — FIRST AUTONOMOUS *VISION* GRASP (calibration-free, closed-loop)

End-to-end from a single camera frame, no hand-eye `X` anywhere in the loop. Blue cube re-placed roughly after the earlier teleop-drift incident; the robot found it, servoed over it, and grasped — all from pixels.

**Pipeline:** observe → segment → image Jacobian → visual servo to the grasp-pixel → `grasp_at` → lift.

| step | result |
|---|---|
| segment | dark-navy-cube detector `R<40 & B−G>18 & B>50` (the cube reads RGB≈(1,45,72) — near-zero red; this cleanly rejects the **bright bluish background** R≈96 and the **gray cast-shadow streak**, both of which a naive `B>R` blue test catches) |
| image Jacobian | two 3 cm EE perturbations at z=0.20 → **J=[[−74,−2072],[−2015,49]] px/m**, well-conditioned. Scale ~2015 px/m matches optics `fx/z`=1970; the **off-diagonal dominance = camera is mounted ~90° rotated** vs EE axes (EE-x→image-v, EE-y→image-u) |
| visual servo | start pixel err (+89,−25) → one `Δee = J⁻¹·Δpixel` step → **residual 0.4 mm / sub-pixel (−0.8,−0.6)**. The solved EE (0.4584,−0.0105) independently matched the block's known world xy → cross-check passed |
| grasp | `grasp_at` (new MCP primitive: compliant Cartesian-impedance descent, firm xy+orientation / soft z, stall-guard) → **`grasped:true`**, width 29 mm |
| lift | held at z=0.278, width **29.3 mm** stable, force 5.6 N — Doug confirmed "perfect grasp" |

| # | Finding |
|---|---------|
| 132 | **The grasp-pixel + image-Jacobian closes the localization loop that calibration (`X`) never could — and it's a one-shot servo.** A 2×2 image Jacobian (2 perturbation moves, ~30 s) plus the grasp-pixel reference (357.5,372.7 @ z=0.20, finding #130) is the entire perception stack for a top-down pick: segment the block, `Δee = J⁻¹·(grasp_pixel − block_pixel)`, move, descend. **Converged sub-pixel in a single step** (linear regime — block already <3 cm from target). J is locally constant so it's measured once and reused. This is the calibration-free approach Doug + Caitlin sketched, now live: no `X`, no deprojection, no metric extrinsic — just "drive the block to the pixel where the gripper-over-block was recorded." **Segmentation was the only real fight:** the cube sits in its own shadow (dark navy, not saturated blue) under a bluish-white-balanced background, so the discriminator is *low-red + blue-dominant*, not *blue-bright*. Validated overlay before trusting any centroid. Limits inherited from #130: valid only at the measured height + dead-vertical orientation; J is z-specific (rescale by 1/z for other heights). |

**Repeatability (same session):** picked the block, placed it at a fresh offset (0.42, 0.05), retreated to a deliberately off-center observation pose, and re-ran the pipeline cold. This time the block sat at pixel (501.7, 301.9) — a **144 px / ~7 cm** error, so the servo did real work: **3 iterations** (33×71 mm → 3×3 mm → 1 mm residual) converged it to the grasp-pixel, then `grasp_at` → `grasped:true`, lifted, held 29.3 mm. Two-for-two. The one-shot first pick wasn't luck — the loop genuinely regulates pixel error to ~0 with a fixed J (Newton-step on a near-linear map). Iteration count scales with starting offset; ~1 step per ~3 cm.

**Now a substrate primitive (`vision_grasp`).** The whole locate→servo→grasp loop is folded into one MCP tool: it drops to the servo height, segments the cube in-process (`cam.capture` BGR → the low-red/blue-dominant mask), iterates `Δee = Jinv·(grasp_pixel − block_pixel)` until pixel-converged, then runs `grasp_at`. Calibration constants (`_VG_GRASP_PIXEL`, `_VG_JINV`) + the mask are embedded from #130/#132. Expectation: `grasped==True`. **Validated live as a single tool call** (`vision_grasp("blue")`) from a ~4.5 cm offset: 91 px → 12 px → 1.2 px in 2 corrective moves, then grasped (29 mm) — third autonomous grasp, fully agent-callable. The manual ssh-segment + local servo-math scaffolding (`scripts/segblue.py`, `scripts/servo_step.py`) is retired into the primitive; kept in-repo as the calibration record. Next: orientation/height-robust version (multi-point or re-measure J per z), and generalize the mask beyond the one blue cube.

---

## 2026-06-25 (night) — height-robust grasp + the auto-reset cycle

Two extensions, both live-verified: a coarse-to-fine **descending** servo (the block can start anywhere on the table, not just inside the z=0.20 FOV) and a **`place_block`** primitive — together = autonomous **pick-and-place**, i.e. the auto-reset the Push-T/ENPIRE self-improvement loop has been blocked on.

**grasp_pixel(z) is a perspective curve, not a constant.** The under-gripper table point slides in the image as the camera changes height — measured at three heights (gripper held over the block, moved straight up), it fits **u(z)=328.9+5.94/z, v(z)=235.6+27.4/z** sub-pixel. And the image-Jacobian gain is ~f/z, so **Jinv(z)=Jinv₀·(z/0.20)** (verified to ~7% at z=0.36). With both, the servo runs at any height: start high (z=0.34, wide FOV), drive the block toward grasp_pixel(z), drop a notch once centred, repeat. **Live: a 17 cm offset (block 195 px off, out of the z=0.20 FOV) → centred at z=0.34 in one step → descended 0.34→0.295→0.25→0.205→0.20 holding <3 px the whole way → grasped (4th autonomous grasp).**

| # | Finding |
|---|---------|
| 133 | **Coarse-to-fine height servo + place = the auto-reset primitive, and the segmentation needed real hardening to survive it.** Descending widens the catch basin (start high, narrow as you drop) and is the same Newton-on-pixels loop with `grasp_pixel(z)` and `Jinv(z)` — both clean `a+b/z` / `∝1/z` perspective laws, no re-calibration per height. **But the first far-pose test flung the arm toward the base** (no-IK abort): at a contorted corner the camera tilted off-vertical and caught **bright cyan ceiling-light glare** (G≈120, B≈250) — which is *also* low-red + blue-dominant, so the mask averaged a 13900 px phantom and the servo chased it. The cube is **dark** (G≈55), so a **G<95 ceiling** rejects the glare and leaves the lit cube untouched — the single discriminator. Added defense-in-depth so no bad read can ever fling the arm again: a **z-scaled blob-plausibility gate** (block area ∝1/z²; abort on glare/clutter instead of chasing), a **per-step clamp** (±0.15 m), and a **reachable-workspace clamp**. Lesson echoing the calibration one: the servo loop is trivially robust *given a trustworthy centroid* — the fragility is always at the sensor edge (here, an out-of-distribution viewpoint), and the fix is to gate the input, not the loop. `place_block` is the inverse map (deproject a world-xy or a target-pixel to the gripper pose, compliant set-down, release). **Full cycle live-verified:** vision_grasp(17 cm offset) → place_block(0.40,−0.12) → re-image confirms the cube upright at the new home. Hebbot can now pick *and* reset a block unattended. |

**Second colour calibrated (green) — toward stacking (2026-06-26).** Segmentation generalised to `_segment(bgr, color)` with a per-colour mask table. Green cube RGB≈(70,90,38): green-dominant, low blue. Mask `(G−B>25)&(G−R>12)&(G>45)` — the **`G−B>25` test is the decisive one**: the *blue* cube is G−B≈−36 and neutral table/glare/shadow is G−B≈+5, so blue and green can never be confused (verified live: full green cube masked, **0** pixels on the adjacent blue cube). The grasp **geometry** (`grasp_pixel(z)`, `Jinv(z)`) is colour-independent — pure optics — so `vision_grasp(color="green")` works with only the new mask; the blob-plausibility gate's blue-tuned 5600 px reference covers green too (green normalises to ~5500 px at z=0.20 despite being a 2.9 cm cube — the mask is slightly conservative). This is the "distinguish two blocks" prerequisite for stacking; next is the `stack_on(pick, onto)` primitive (servo-while-holding + descend-to-contact release).

| # | Finding |
|---|---------|
| 134 | **Block stacking works — and the precision was never the worry, the verification was.** Manual proof-of-concept (Doug-confirmed): `vision_grasp("green")` → lift → image-servo the gripper over blue (same coarse-to-fine loop, blue's centroid as target, converged **1.6 px**) → `place_block` descend + release → **green seated cleanly on blue.** The held cube's bottom met blue's top at EE **0.105** (a cube-height above the table-place datum). The in-hand-offset worry didn't bite — the grasp centres the cube well enough that a 1.6 px gripper-over-target servo lands the stack. **The real trap was mine: I misread the 2D wrist image and called it a failure — green-on-top-of-blue, seen from the tilted wrist cam, looks like two adjacent cubes (the upper cube projects low + large, the lower one peeks out behind).** The `contact_z` already said "success" (one cube up); I should have trusted the contact signal over the ambiguous projection. **Lesson: the wrist view cannot verify *height* — use `contact_z`, a side/scene camera, or depth to confirm a stack, never the top-down centroid.** Productised as `stack_on(pick, onto)`: it descends to **detected contact** (commanded−actual z divergence) and releases there rather than pressing to a guessed `place_z` — which auto-finds the top at *any* level, so it generalises to 3-high and uneven cubes without re-tuning. Next: live-test the one-call primitive, and add a non-wrist verification view. |

## 2026-06-26 — FIRST AUTONOMOUS 3-BLOCK TOWER (blue·green·red) + parallax + tilt-verify

Stacked **red on green-on-blue** — a 3-high tower, vision-only, no hand-eye calibration. Doug-confirmed "almost perfect"; a tilted wrist capture shows three clean layers. New ground for the project.

| # | Finding |
|---|---------|
| 135 | **Stacking on an *elevated* cube needs a parallax correction — that's the one new idea for going past 2-high.** The grasp-pixel `(a+b/z)` is calibrated for a table-height target; an object elevated `h` above the table sits at camera distance `z−h`, so its "gripper-is-over-it" pixel is `grasp_pixel(z−h)`, not `grasp_pixel(z)`. Servoing to the table value over green's top (h≈5.3 cm = blue 2.4 + green 2.9) is **~16 mm off** — enough to miss and topple. **Fix: evaluate grasp_pixel at d=z−h and scale Jinv by d/0.20.** Live, the naive target pointed to (0.42,−0.155); the corrected one to (0.404,−0.145) — matching the tower's true xy — and converged the held red to **1.7 mm** over green. (Green-on-blue worked at h=0 only because its 8 mm parallax error was inside the seat margin; the 3rd cube's 16 mm is not.) **Red mask:** `(R−G>40)&(R−B>40)&(R>115)` — the R>115 floor rejects the cube's own reddish cast shadow (R≈101 vs cube R≈160). Red is a 2.9 cm cube. **Descent caveat:** used `place_block`, whose fixed-`place_z` descent pressed ~9 N (contact came at EE 0.165, 3 cm above my estimate) — it held only because red was centred (vertical press). This re-confirms `stack_on`'s **contact-stop** is the right descent for towers; productising red + an `onto_top` parallax arg into `stack_on` makes the 3-high a single safe call. **Verification solved:** a ~35° wrist tilt from the +x side images the tower side-on — three layers plainly visible — which the top-down wrist view cannot do (#134). Tilting the eye-in-hand camera **removes the need for a separate scene cam** for height checks (Doug's call). |

| # | Finding |
|---|---------|
| 136 | **Rebuilt the 3-tower a second time (reset → restack), and found the real bug + the right verification habit.** (a) **`stack_on`'s contact-detect false-negatives.** Its one-call green-on-blue *did* stack — but the held cube **slips up in the gripper** when its bottom meets the target, so commanded−actual EE-z never diverges; the descent ran to the floor (over-pressing ~12 N) and reported `no_contact`. **Fix: detect contact by external force** (`O_F_ext_z` rises on contact — slip-immune), not by the position gap. Until then, `place_block` to a known `place_z`≈contact is the reliable descent. (b) **`onto_top` semantics nailed empirically:** `0` to stack onto a table cube (the grasp-pixel is *already* calibrated on a table cube), `≈0.053` to stack onto a 2-high tower (green's top). My `0.024` guess for green-on-blue walked the drop ~2 cm off — yet it still seated (within margin). (c) **I misread the top-down wrist view as failure *twice* when both had actually stacked.** The 2-D top-down projection genuinely cannot resolve height; the **35° tilt-from-+x view is the reliable verifier** every time. Stop trusting the top-down centroid for stack outcome — tilt and look. |

> **Design direction (Doug, 2026-06-26):** a future VLA should carry *"back off and re-view from a different camera angle"* as a **first-class action**, not a fixed perception step. Active perception / next-best-view: when the policy's observation is ambiguous or its prediction uncertain, *change the viewpoint*. Today's whole snag — height being unobservable top-down — was resolved exactly this way; the tilt turns an unobservable into an observable. The action space needs camera/viewpoint moves and an information-gain objective. This is the eye-in-hand version of "look before you conclude," and it's the robustness habit both the agent layer (today) and a learned low-level policy (later) should share. Mirrors the Hebbot high↔low layering: the agent chose active perception ad-hoc today; the goal is to push it down into a primitive the policy emits on its own.

## 2026-06-26 (evening) — Moravec's paradox, live

| # | Finding |
|---|---------|
| 137 | **The grasp pipeline localizes position + colour and is blind to *shape* — and that blindness, not any reasoning gap, is what broke an "easy" task.** Trying to un-stack the green block, I failed to lift it four times (closing the gripper in mid-air above it), reasoning elaborately each time about parallax and grasp_z — while never noticing green is a **tall rectangular block, not a cube**. I'd hard-coded "2.9 cm cube" and computed a grasp height for that fiction. A two-year-old would just *see* it's tall and grab it lower (or, as Doug noted, smash the tower and laugh). Textbook Moravec's paradox: the calibration-free servo + image Jacobian (the "hard" part) works beautifully; perceiving an object's *form* (the "trivial" part) was the actual blocker. **Two concrete capability gaps this exposed, both needed for "set the blocks on their ends":** (1) **per-instance segmentation** — the colour masks are one-blob-per-colour, so 3 green blocks merge into one centroid and a block fuses with its reflection (needs connected-components, which the substrate venv lacks scipy for — pure-numpy labelling or a learned segmenter); (2) a **reorient-to-vertical primitive** — grip the narrow cross-section (fingers yaw-aligned to the block's long axis), lift, pitch the wrist 90°, lower onto the end face, balance-release. Everything to date is fixed-orientation top-down pick/place. **The deeper point for the project:** robust manipulation must derive the grasp from the object's *measured geometry* (extent, principal axes, height — from depth or segmentation), never an assumed primitive. This is the embodied-perception frontier the whole Hebbot thesis is pointed at, surfacing exactly where theory predicts.

## 2026-06-26 (late) — shape→reflex: find_blocks + stand_on_end

Turned finding #137's reasoning into two primitives (the high-cognition→reflex loop Doug named).

| # | Finding |
|---|---------|
| 138 | **Per-instance, shape-aware perception works with pure numpy; and "grip height" is orientation-dependent, not a constant.** (a) **`find_blocks`** runs connected-components (min-label propagation — no scipy/cv2 in the substrate venv) on each colour mask, so multiple same-colour blocks separate into instances, each with centroid, principal-axis angle, extent, aspect, and a rect/cube tag. Verified live: it split 3 green instances + classed rect-vs-cube cleanly. This is the fix for the #137 blindness — the pipeline now reports *form*, not just colour+position. (b) **`stand_on_end`** is the first non-top-down maneuver: centre over a lying rect block, yaw the fingers across its narrow axis (image angle → world yaw via the camera rotation; the analytic ψ=φ was right first try), grip, lift, pitch the wrist 90° about the finger axis → vertical, lower to a force-sensed table contact, release. (c) **Bug found live, then the lesson:** the first grip closed empty. Cause 1 — it servoed on `_segment` (merges all red, incl. two reflection specks) instead of the `find_blocks` *instance* centroid → ~1 cm off. Cause 2 (Doug's catch) — **grip_z=0.045 was the mid-height of a *standing* block, but red is *lying*, so it presents a ~one-cube-tall profile and the fingers closed above it.** Generalised lesson, the orientation sequel to #137: the grasp height must come from the block's *presented* geometry (lying → low, standing → tall), never a fixed default. Fixed: servo on the instance centroid + grip_z=0.037 for the lying profile. The yaw/pitch convention and the place geometry still want a live tuning pass. |

## 2026-06-26 (night) — per-instance targeting works; vision_grasp is cube-shaped

| # | Finding |
|---|---------|
| 139 | **The perception→manipulation bridge (per-instance targeting) works; the live test then exposed that `vision_grasp` itself assumes a compact cube.** Added `vision_grasp(target=[u,v])`: the servo locks onto the `find_blocks` connected-component *nearest* that pixel and follows it each step, instead of `_segment`'s merged colour centroid. **Validated live twice** — it tracked the green cube among 3 green blocks (servo to 1.2 px) and the red rect among 2 reds, picking the right instance both times. But both *grasps* failed **downstream of the (working) targeting**, and the reasons rhyme: (a) the green "cube" is in a **cluster** (gray cube + green rect ~3 cm away) so the open gripper caught a neighbour on the way down; and the aspect classifier can't tell a standing rect from a cube top-down (#138). (b) The red **rect** was rejected by the plausibility gate — `npix` expected ~5330 for a *cube*, but a 6 cm rect's profile is bigger and clips out of the narrowing FOV as it descends. **Conclusion:** `vision_grasp`'s size-gate, grasp height, and fixed gripper-down orientation all bake in "compact table cube." Grasping the real variety (rects lying/standing, clutter) needs the grasp to be **shape-aware** — derive grasp_z, the size band, and the yaw from the perceived instance geometry (`find_blocks` already reports extent + principal axis). This is the #137/#138 frontier, now concretely the bridge to a general self-improvement loop. **Path:** run the first loop on *clean compact cubes* (which `vision_grasp` already handles) to get it spinning + generating trials, and build shape-aware grasping in parallel (the loop will demand it). |

## 2026-06-27 — the self-improvement loop runs (and reports its own next work)

| # | Finding |
|---|---------|
| 140 | **`practice` — the self-improvement loop — is built and ran unattended, and its first runs *generated the next round of work*, exactly as the thesis predicts.** The loop is `{attempt = stack_on, verify = find_blocks coincidence, reset = vision_grasp(pick_top)+place_block}` × N, returning a per-trial log the agent reviews and folds back into the primitives. Milestones reached this session leading here: shape-aware grasp of real **rectangular** blocks (per-instance targeting + skip-cube-gate + cross-axis yaw + small tracking steps + yaw-normalisation), `stack_on` made shape-aware, green-on-blue stacked & **tilt-verified**, and the elevated un-stack grasp working (the reset path). **What the loop's runs taught (each fixed by the high layer, live):** (1) the "onto absent" verify was too strict — a sliver of the lower block peeks out at close range → switched to centroid-coincidence; (2) the reset's release height must track the (elevated) grasp height → `place_z = 0.04 + pick_top`. **And what the loop is STILL telling us — the next hardening pass:** (3) the grasp isn't robust at *all* table positions (a trial failed to grasp at the +y edge), and (4) **failures cascade** — a failed grasp nudges the blocks out of the fixed overview FOV, so the next trial can't relocate them and the whole run collapses. So the loop needs: robust re-location (wide scan / re-find, never assume a position), a gentler grasp that doesn't disturb neighbours, and stop-/recover-on-repeated-failure so one miss doesn't topple the run. **This is the layered-cognition flywheel turning for real: the loop runs, surfaces its own failure modes as structured logs, and the high layer turns each into a more robust reflex.** |
