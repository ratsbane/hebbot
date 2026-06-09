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
