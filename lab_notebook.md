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

## 2026-06-27 (cont.) — the perceived-geometry grasp: rectangles solved by measuring, not assuming

| # | Finding |
|---|---------|
| 141 | **`vision_grasp` now derives the whole grasp from the block's measured 3-D geometry — and finally grasps a rectangular block (green) reliably, first try, while keeping the cube path healthy.** Months of green-grasp failures had a non-obvious cause, found this session by *measuring instead of theorising* (the recurring Moravec lesson — I'd even diagnosed green as a "thin tile" when depth showed an ordinary 28×37×65 mm block). **Diagnosis chain:** (a) the calibration was fine — a **cube grasps perfectly** as a control; (b) green centres to <2 px yet closes *empty*, and a raise-and-look proved the grasp had **shoved green ~30 mm** — a long light block gets cammed/squirted out by an off-parallel close, where a symmetric cube can't; (c) the grip yaw came from the **colour silhouette's** principal axis, which is noisy (green's reported angle swung 65°→79°→92°) and, for a standing block, whose centroid is pulled toward the lit side face. **The rebuild (all live-verified):** perceive pose from the **depth top-face** (lighting-immune) — centroid, *stable* principal axis, metric width, height — done from **0.30 m** (close-range RealSense depth is garbage: it inflated a 37 mm cube to 60 mm); fingers **parallel to the true long edges** across the short axis; **table-level descent** (fingertips stall on the table beside the block — mid-height gripping fails even a cube, which Doug caught live as "gripped a little too high"); a **narrow symmetric pre-open** (just wider than the block, not the full 80 mm) so the close can't squirt it; a **tilt-glance** that resolves standing-vs-lying a single top-down view can't (Doug's "back off and view from another angle," now a first-class grasp step); and **verify + retry** where each miss biases the next grasp 12 mm lower (Doug's "open + go down + retry"). **Result:** green grasped at width 28.7 mm on attempt 0 and held while lifted; the navy cube grasped on attempt 1 (its dark surface misreads depth as 60 mm tall — the lower-bias retry covers it). The grasp stopped assuming "compact table cube" (the #139/#138 frontier) and started reading each block. |

## 2026-06-27 (cont.) — loop hardening: honest verify, and the stacking frontier

| # | Finding |
|---|---------|
| 142 | **With the robust grasp underneath, `practice` ran clean cycles — then a stress run surfaced three loop-level lessons, each a turn of the self-improvement flywheel.** (1) **Cascade-stop works:** a long run went 3 clean cycles, then a bad trial (stack landed 40 mm high — the lying-rect base had been **tipped/walked** by repeated presses), and the "stop after 2 consecutive failures" guard fired instead of thrashing the scene (the #140 collapse is gone). (2) **Gentler contact is the WRONG lever:** dropping stack `contact_force` 4 N→2.5 N to spare the base **broke slip-immune contact detection** — most stacks aborted `no_contact`, two false-tripped at 0.19 m. 4 N is load-bearing; base drift needs re-canonicalisation, not a softer press. (3) **The verify was lying:** those `no_contact` non-stacks still passed verification on centroid-coincidence alone (10/12 "verified" that never stacked). Fixed: verify now requires `stack_on` to have **actually reported contact** AND centroids to coincide — honest failure detection is the whole point of a self-improvement loop. **Open frontier:** the loop is still sensitive to block *variety/pose* — a lying **rectangular** base tips under repeated stacking, and stacking a long rect *pick* (blue is actually ~67×37 mm, not a cube) is less reliable than a compact cube. The next hardening is **base re-canonicalisation** (detect a drifted/tipped base via depth and re-level it) — the general fix that lets the loop run on any block, not just a compact cube. The grasp itself (#141) stayed solid throughout: blue and green both grasp on hardware. |

## 2026-06-27 (cont.) — gripper leveling, and the table as a kinematic-accuracy probe

| # | Finding |
|---|---------|
| 143 | **Gripper-finger leveling, calibrated human-in-the-loop when the force estimate proved too noisy to self-calibrate.** Doug spotted (watching the bench) that grasps land "a little too high" and "the gripper isn't quite level, so one side moves the block" — one fingertip mechanically sits low, so it taps the block first (nudging it) and stalls the descent high (→ the lower-bias retry then rescues it). The flange comes down vertical (~0.1°), so the tilt is in the fingers vs the flange. **Attempted auto-cal with `probe_table`** (press open fingers to the table, null the horizontal contact torque) **failed** — the external-wrench estimate at gram-level fingertip contact is dominated by noise/false-triggers (Mx/My scattered 0.17–0.95 between identical probes). **So we calibrated by eye under teleop:** lower the open fingers to the table, Doug reports which tip is low, adjust the wrist roll, re-check — converged in ~4 passes to **−1° roll about the gripper's local x** (both tips then meet the table together). Baked into every grasp via `_level_quat` (applied in grasp_at, in the gripper's LOCAL frame so it holds at any yaw). A clean example of the high layer using the human's eyes when the robot's own sensing is too coarse. |
| 144 | **The flat tabletop is a forward-kinematics accuracy probe — and the impedance descent's NULLSPACE was silently stalling it ~4 mm above the surface.** Building a "lower until resistance" table-finder exposed two things. (1) **Contact detection is subtle:** force at fingertip contact is too noisy; a commanded-vs-actual position gap is confounded by the controller's ~6 mm motion lag; an actual-z plateau false-fires when a config descends laggily. What finally worked: **zero the impedance `nullspace_stiffness`** (the 1.0 pull back toward the high starting joint-config was stalling the descent ~4 mm short, losing authority before the table) **and stiffen Z to 600** so the arm reconfigures down and *presses* — giving a clean contact (−2 to −4 N) at the true surface (0.0165 m, matching Doug's manual touch). The **press force certifies contact**: at hard near-base corners the descent still can't reach and stalls with ~0 N (a `contacted` flag now flags these). (2) **The table is physically flat (Doug), so the ~±1.4 mm of contact-z variation across the workspace IS the robot's FK z-error** — `probe_table` against a known plane measures the kinematics. Stored as `table_z(x,y)` (a fitted plane, origin 0.46,0), which encodes "the EE-z that really lands the fingertip on the surface here" → referencing it per-xy compensates the FK error for grasp/place floors. `grasp_at` itself is unaffected (it stalls on the block, well above the bare table). |

## 2026-06-27 (cont.) — the arm's world model: a persistent scene store + predict-then-look

| # | Finding |
|---|---------|
| 145 | **A persistent world model — the table plane as immovable substrate, blocks as tracked movable objects — with EXTERNAL-move detection by prediction error.** Doug pushed to a higher concept: the arm needs a stored model of its world (the only fixed surface is the tabletop, #144; blocks move), and a way to know when an external force moved a block. Built `scene_sync` (+ `scene_show`): go to the overhead observe pose, re-perceive every block, associate each to the stored model (same colour, nearest within a gate), classify it **stable / moved / appeared / disappeared**, and persist to `data_dir/scene.json`. v1 lives in the OBSERVE-pose image frame — calibration-free and directly actionable (grasps already target pixels). **The detection IS the prediction error:** when no action of mine touched the scene, a `moved` result means something external (a hand, gravity, a knocked neighbour) shifted that block. **Demoed live on hardware:** synced twice → both blocks `stable` (residuals <3 px, pure jitter); Doug then slid one green block by hand → next sync flagged green `moved` 157 px from→to while blue held at 0.1 px. The arm noticing a change it didn't cause — the "back off and re-assess" instinct formalized as a little predict→observe→correct belief loop, the innovation doubling as the alarm. `moved_px ≈ 9 px ≈ 1 cm` sits above the ~1–2 mm perception noise and the ±1.4 mm FK error (#144). **Next:** optimal per-colour assignment (robust to multiple same-colour blocks), depth-enriched metric poses (unify with the table plane into true 3-D), action-hooks so the loop's OWN moves update the model instead of false-flagging, and feeding the model into cuRobo's collision world (Phase 2). |

---

## ENPIRE — what it is, what we took from it, and how Hebbot differs (2026-06-27)

**ENPIRE** (NVIDIA GEAR, *"Agentic Robot Policy Self-Improvement in the Real World"*, https://research.nvidia.com/labs/gear/enpire/) is the closest published work to what Hebbot is. Reviewing against it sharpened both our positioning and our roadmap.

**What ENPIRE does.** A coding agent (they name Claude, Codex, Kimi) acts as an autonomous researcher that improves a robot policy *expressed as executable Python code* — not neural-net weights — inside a repeatable real-world loop: **EN** (environment: automatic **reset + verification**), **R** (rollout: execute on hardware, preserve state/action/video/result), **PI** (policy improvement: revise the code from rewards, videos, traces, failures), **E** (evolution: a *hypothesis git-tree*, branch/compare/reuse/prune — 86+ ideas). The agent reads failure logs and video, forms hypotheses, edits the code, re-runs. Tool APIs: perception **SAM 3**, planning **cuRobo**, control **YAM** (dual-arm). Tasks: Push-T, pin insertion, zip-tie, GPU insertion. Verification is an *auto-derived reward* with multi-camera fused binary verdicts. Results: **99% pass@8**, fleet scaling (1/4/8 agents, MRU/MTU metrics), per-idea deltas (e.g. "BC regularization +10.8 pp").

**Direct lineage.** We adopted **cuRobo as our motion backend directly from ENPIRE** (we were on a different IK planner until recently) — and cuRobo turned out to be the single change that finally made the calibration-free vision grasp repeatable (#130-ish). So ENPIRE didn't just validate our thesis in the abstract; one of its tool choices unblocked us concretely.

**The alignment is the architecture, not a coincidence.** Hebbot's policy *is* `primitives.py` (executable Python); the coding agent (Claude, in this very transcript) revises it from `practice` logs and bench observation; the loop is `practice` = {attempt → verify → reset → log → refine}. That is EN-R-PI-E, hand-built. We arrived here partly in parallel and partly by adopting their pieces. Independent-ish convergence on the same architecture is itself evidence the bet is right.

**Where we stand against EN-R-PI-E** (honest scorecard, 2026-06-27):
- **EN**: reset ✅ (un-stack+place); verify ✅ but *hand-coded, single-camera* (ENPIRE: auto-derived reward, multi-camera fusion, and it verifies the *reset* + randomizes the init state — we do neither yet).
- **R**: execute ✅; we save frames + trial logs, but not a unified per-rollout audit record.
- **PI**: ✅ this is the whole project — but agent-mediated through a human + a manual deploy/reconnect each edit, not yet an unattended self-re-running loop.
- **E**: we have **git + the lab notebook** (findings = a linear hypothesis log), *not* a branching tree with automated compare/prune.
- **Metrics**: just added `practice_metrics` (persisted success-rate per run, tagged with the policy-code content hash) — our first ENPIRE-style pass@k curve. Previously: qualitative only.

**How we deliberately differ — the contribution of an open follow-on.** ENPIRE published the *architecture* but **not its code, prompts, or agent transcripts.** Hebbot's distinct value is to ship exactly those: the working substrate, and the **actual AI chat transcripts** that produced each primitive — the reasoning, the wrong turns (theorizing "thin block" before measuring; the gap-detector sign bug; the nullspace stall), and the corrections. ENPIRE shows *that* a coding agent can self-improve a real-robot policy; an open Hebbot writeup shows *how it actually reads* turn by turn, reproducibly. Further differences/emphases:
- **From-scratch bootstrap, single agent, classical perception** (no SAM 3, no fleet, one Franka) — the minimal viable ENPIRE, which makes the artifacts legible.
- **A persistent world model with predict-then-look external-change detection** (#145) — a *belief state* that flags "the world changed without me." ENPIRE resets + scores; it doesn't foreground a running model of the scene.
- **The explicit high↔low layering thesis** ("do it with high cognition, then make it reflexive") as the organizing principle, vs ENPIRE's flatter agent-edits-policy framing.

**Roadmap to a credible follow-on** (priority): (1) the success-rate **curve** (`practice_metrics`, just built) — make improvement *visible*; (2) **unified rollout records** (state/action/video/result) so PI can read traces without a human watching; (3) **auto/multi-view verification** (our tilt-cam is the seed of their camera fusion); (4) an **evolution tree** (branch-per-hypothesis) layered on git; (5) eventually scene randomization + a second task. The writeup ("a follow-on to ENPIRE, with the code and transcripts they didn't release") is the through-line that ties the artifacts together.

---

## 2026-07-24 — Codex audit: automation is real; Hebbian compression is the missing link

> **Authorship note:** This entry was written by **Codex** after inspecting the repository, live
> skill store, episode database, and robot/camera state. Previous notebook entries were written by
> **Claude**. Claude will return at times to review, comment on, and contribute to the work; future
> entries should retain their author attribution where it is useful.

### Concise model of the project

Hebbot is a plain-Python substrate between a deliberate foundation-model orchestrator and isolated
hardware processes. Its intended learning arc is:

`deliberate choice → recorded episode → retrieval/replay → adaptation → verified repetition → reflexive policy`

The layer boundary is important:

- **Deliberate foundation-model layer:** chooses skills, reasons about goals and surprises, and
  currently performs most policy improvement by inspecting results and editing code.
- **Hebbot substrate and skill records:** SQLite-backed skill/episode/decision records, dispatcher,
  expectation checks, dynamic MCP exposure, frames, and the intended retrieval/compression seams.
- **Reflexive or learned execution:** currently classical visual servoing, impedance/contact
  behavior, cuRobo-assisted motion, and local retry/recovery logic. A distilled learned policy does
  not yet exist.
- **Hardware processes:** Franka FCI/panda-py, the wrist RealSense D405, and the persistent cuRobo
  service. These provide actuation, sensing, real-time control, and GPU planning; they are not the
  deliberate or memory layers.

The motion stack is genuinely capable. The learning/compression stack is still mostly architectural
scaffolding.

### Repository and live-system evidence

- The pre-existing uncommitted `src/hebbot/skills/primitives.py` diff was preserved. It is a large,
  coherent experimental branch (`+1098/−49` at inspection time), not a stray edit. Its purpose is
  to harden geometry-aware manipulation: deeper-grip retry telemetry, the corrected table-contact
  datum, elevated/cluttered grasps, instance-targeted stacking, scene survey, reorientation,
  decluttering, tidying, tower construction, teardown, and verification.
- The live MCP store exposed **27 skills: 26 `classical`, one `composition`** (`home`). The `script`,
  `trajectory`, `learned_policy`, and `vlm_grounded` body types exist in the schema but have no
  dispatcher implementation.
- All **21** mock/substrate tests passed.
- The live Franka was healthy at its home pose with the gripper open. The wrist D405 returned live
  aligned RGB-D metadata at **640×480**, center depth **0.4801 m**, and depth coverage **65.9%**.
- The persisted scene model contained four tracked rectangular-block records, one marked missing.
- The episode database contained **878 episodes**, **161 frame rows**, and **zero state rows**.
- All **814 routing decisions** were `orchestrator`; none contained relational-state vectors or
  retrieval candidates.
- The only persisted `practice_metrics` run requested 10 blue-on-green trials, stopped after 2,
  verified 0, and reported a **0% task-success rate**.

These counts expose an important labeling distinction: a stored episode's `success` often means
that the Python body completed mechanically, not that the embodied task was perceptually verified.
For example, the top-level `practice` call can be stored as successful while its internal verified
trial rate is zero. Mechanical, proprioceptive, and perceptual outcomes must remain separate if the
episode corpus is to support retrieval or distillation.

### Current experimental frontier

The active manipulation frontier is robust, geometry-aware work with multiple rectangular blocks:
multi-instance perception, depth-derived shape and orientation, visual-servoed grasping,
force/contact-aware stacking, reorientation, decluttering, reset loops, and simple persistent scene
tracking. This is meaningful embodied progress, and the classical routines already contain useful
reflex-like behavior.

However, later work described inside the uncommitted diff (comments refer to experiments after the
last numbered notebook finding) has not been consolidated into the notebook. The code and live
skill versions have advanced beyond the written experimental record. That gap should be repaired
before interpreting the current routines as a stable baseline.

### Most important unresolved scientific/technical question

**Can repeated experience change subsequent execution automatically, without a foundation model
manually editing `primitives.py`?**

Not yet. Episodes are written, but there is no implemented relational retrieval, automatic replay,
trajectory-body execution, policy promotion, or distillation runtime. No recorded decision has
selected `replay` or `policy`. State trajectories are not being stored. Most task-level plans are
large `classical` Python functions rather than data-defined compositions or promoted skill bodies.

Hebbot therefore currently demonstrates **foundation-model-guided iterative robot automation plus
classical reflexive controllers**, not yet Hebbian compression. The key missing causal link is:
past experience does not presently alter the next execution through the substrate.

### Three candidate experiments

1. **Complete-rollout instrumentation.** Run a small set of `vision_grasp` trials while recording
   before/after RGB-D, time-aligned robot states, perceived geometry, parameters, retry telemetry,
   deliberate context, and strong post-lift verification. This repairs the corpus but does not yet
   make experience affect behavior.
2. **Relational nearest-neighbor replay.** For one narrow `vision_grasp` case, store a compact
   relational state and successful execution parameters. On a later trial, retrieve the nearest
   successful episode, adapt/reuse its grasp yaw, width, height, and servo settings, and log the
   candidate scores plus a real `replay` decision. Compare it with the current fixed heuristic.
3. **Trajectory-body promotion.** Record successful approach/grasp/lift waypoints, implement the
   `trajectory` body runner, adapt a stored trajectory to a translated object pose, and verify it
   across several placements. This reaches further along the thesis but confounds retrieval,
   correspondence, trajectory execution, and verification in one experiment.

### Recommendation

Run **relational nearest-neighbor replay for one narrow `vision_grasp` task**.

This is the smallest experiment that makes repeated experience causally affect a later physical
action. Keep the initial version narrow: one block class and pose family, a few low-speed training
placements, one held-out nearby placement, the existing classical controller as executor, and the
current heuristic as baseline/fallback. The retrieved episode should choose or adapt parameters,
and success must require independent post-lift verification rather than only
`gripper.grasp() == True`.

This would not yet be learned-policy distillation, but it would close the load-bearing missing link
between stored experience and future behavior. Trajectory replay and eventual policy promotion can
then be evaluated on top of an evidence-producing retrieval path rather than built speculatively.

## 2026-07-24 — Codex hypothesis review: make compression falsifiable

> **Authorship note:** This entry was written by **Codex** in discussion with Doug. It proposes a
> refinement of the project's hypothesis and evaluation criteria, not a replacement for the
> architectural commitments in `docs/foundations.md`.

### Sharpened hypothesis

> **Repeated foundation-model-directed execution can produce structured embodied experience that
> enables increasingly autonomous selection and execution of skills, while preserving deliberate
> oversight and recovery. Compared with repeated stateless deliberation, retrieval, replay, and
> eventual distillation should reduce deliberate intervention and execution cost without
> materially reducing verified task success under familiar conditions.**

This formulation makes compression observable: fewer model decisions, lower latency/cost, and
larger execution chunks beneath the deliberate layer. It keeps task-level verification
load-bearing, preserves the foundation model's role in novelty and surprise, supplies stateless
deliberation as a baseline, and permits the hypothesis to fail.

### Three learning loops that must not be conflated

1. **An agent improves source code.** Claude or Codex reads failures and edits `primitives.py`.
   This is valuable agentic robotics research, close to ENPIRE, but it is not by itself Hebbian
   compression inside the substrate.
2. **The substrate retrieves and adapts experience.** A current situation recalls past episodes
   and reuses successful parameters or trajectories. This is the first genuine
   experience-dependent behavior within Hebbot.
3. **The substrate distills a reflexive policy.** Repeated verified episodes train or promote a
   lower-level controller. This is the strongest form of the thesis.

All three belong in the project, but every experimental result should identify which loop improved.
Otherwise strong progress in agent-authored automation can make retrieval and distillation appear
more mature than they are.

### Operational definition of compression

A skill should count as **compressed** only when:

- its public task contract remains stable;
- execution moves from a more deliberative route to a less deliberative route;
- the promotion is based on recorded embodied experience;
- the new route is evaluated against a baseline;
- task-level verification remains acceptable;
- novelty or low confidence returns control upward; and
- the provenance of the selected experience or policy remains inspectable.

A large hand-written Python routine may execute reflexively, but it was not *compressed from
experience*. This distinction should be explicit in claims and notebook entries.

### Primary evaluation curve

Task success alone cannot distinguish Hebbot from a well-scripted automation system. The primary
curve should be:

> **Verified task success versus deliberate interventions per episode.**

Supporting measurements:

- foundation-model calls and tokens per successful episode;
- wall-clock time;
- recovery and escalation frequency;
- replay coverage;
- success conditioned on replay confidence;
- familiar-state versus novel-state performance;
- false-confidence rate (automatic executions that should have escalated); and
- data efficiency (verified episodes required before promotion).

The desired result is not undifferentiated autonomy. It is that familiar cases migrate downward
while unfamiliar or surprising cases reliably move upward.

### Verification labels are training data

Mechanical, proprioceptive, and perceptual outcomes should be stored separately rather than
overloaded into one `success` field:

- `mechanical_success`
- `proprioceptive_success`
- `task_verified_success`
- `verification_method`
- `surprise_type`
- `confidence`
- `human_override`

The current `practice` evidence—mechanically completed calls with zero verified task successes—
shows why this separation matters. Incorrect or weak labels would train the reflex to repeat the
wrong behavior.

### A narrower first scientific claim

Do not initially claim general continuous robot learning. A more credible first claim is:

> **In a bounded manipulation task, relational retrieval of verified episodes reduces deliberate
> parameter selection while preserving success across nearby scene variations.**

Evidence can then advance through a falsifiable ladder:

1. Experience is recorded completely.
2. A relevant episode can be retrieved.
3. Retrieval changes the next execution.
4. Replay succeeds under small variation.
5. Confidence predicts replay success.
6. Failures correctly escalate.
7. Repeated replay supports policy distillation.
8. The distilled policy generalizes within a measured competence region.
9. Deliberation re-enters outside that region.

Each rung is useful evidence and exposes the precise point at which the thesis fails.

### Test relational state rather than assuming it

The commitment to relational rather than raw pixel state is plausible and well motivated, but it
should be evaluated empirically. Compare whether replay success is best predicted by:

- raw visual similarity;
- object and gripper geometry;
- relational geometry; or
- relational geometry plus semantic/object features.

The useful representation may be task-specific and partially learned rather than one universal
state vector.

### Refine “skills are records, not code”

The experimental history shows that executable Python is an effective development medium. A more
durable formulation is:

> **Skill identity, contract, lineage, evaluation, and promotion state are records. Execution
> bodies may reference code, compositions, trajectories, or policies.**

The important invariant is not that code disappears. It is that the substrate can observe, compare,
and replace the execution body behind a stable contract.

### Promotion requires demotion and forgetting

The substrate also needs:

- confidence decay when the environment changes;
- demotion after verified failures;
- context-dependent competence regions;
- preservation of failed episodes;
- policy/version rollback; and
- detection that old experience is no longer applicable.

Compression is not only strengthening repeated pathways; it also requires contextual gating,
inhibition, updating, and safe withdrawal of an obsolete reflex.

### Failures as first-class experience

For each failed or surprising execution, preserve:

- where execution diverged;
- observations immediately before and after;
- whether the cause was perception, planning, control, verification, or recovery;
- whether the failed action disturbed the scene; and
- the recovery decision and its outcome.

Successful episodes teach what to repeat. Failures and near-successes often define the competence
boundary, which is essential for safe routing between reflexive and deliberate execution.

### Refined overall goal

> **Build and evaluate an open embodied-learning substrate in which a foundation model bootstraps
> skills through deliberate interaction; verified experience is retained and reused; familiar
> behavior progressively moves into faster, cheaper execution paths; and uncertainty, novelty, or
> surprise reliably returns control to deliberate reasoning.**

This preserves the piano analogy while turning it into an experimental program. Hebbot's distinct
contribution is not simply capable robot automation or end-to-end policy learning, but the
observable and reversible transfer of work between deliberate and reflexive cognitive layers.

## 2026-07-24 — Codex experiment: retrieval path built; live baseline exposes RGB-only tracking gap

> **Authorship note:** This entry was written by **Codex**. The experiment followed the explicit
> pre-motion protocol: low speeds, state/camera observations between consequential actions,
> preservation of failed attempts, and a two-failure stopping condition.

### Hypothesis and implementation

The intended experiment was the first narrow causal test of Hebbian compression:

> For one familiar rectangular-block grasp family, relational nearest-neighbor retrieval can select
> useful grasp parameters for a later trial, reducing deliberate parameter selection while
> preserving independently verified success.

An opt-in `vision_grasp(experience_mode="record"|"replay")` path was added without changing legacy
call behavior. It:

- records live robot-state snapshots;
- saves initial, pre-close, and post-lift **paired RGB + aligned raw depth** checkpoints;
- stores a compact relational state (object dimensions/orientation, EE pose, and image relation);
- ranks only `task_verified_success=True` episodes;
- logs candidates, thresholds, and a genuine `replay` decision when a candidate qualifies;
- reuses grasp parameters only inside hard safety bounds, with the geometry heuristic as fallback;
- separates mechanical, proprioceptive, and perceptual task verification; and
- caps experimental point-to-point motion at speed factor 0.05 and compliant descent at 0.02 m/s.

Offline coverage grew from 21 to 25 passing tests, including multi-checkpoint frame persistence,
verified-only retrieval, relational similarity, and aligned-depth rejection of flat color
artifacts.

### Live attempt 1 — near-base reach saturation (failed, preserved)

Target: red rectangle initially detected near wrist pixel `[439.0, 187.8]`.

The block was physically too close to the Franka base. The coarse servo saturated at its configured
`x=0.30 m` workspace boundary with image error plateaued near **137 px**. The old routine then
continued despite non-convergence, and the depth-centroid recenter step lacked the same workspace
clamp, allowing EE x to reach approximately **0.25 m**. The compliant descent stalled high at
**EE z≈0.170 m** and did not close. Outcome was correctly stored as a surprise with:

- `mechanical_success=false`
- `proprioceptive_success=false`
- `task_verified_success=false`
- two state snapshots
- two paired RGB-D checkpoints (four frame rows)
- relational state and an `orchestrator` decision with no candidates

No experience from this episode is eligible for retrieval.

Two safety fixes followed offline:

1. coarse servo now aborts unless it reaches the final approach height and pixel tolerance;
2. depth-centroid recentering now enforces the same `[0.30, 0.60] × [-0.28, 0.28] m` workspace
   bounds.

Doug then redistributed the blocks farther from the base and separated them across the reachable
table.

### Live attempt 2 — same-color component jumping (failed safely, preserved)

Target: isolated central red rectangle near `[424.2, 367.0]`.

The corrected guard worked: the servo descended only to its **0.20 m approach height**, failed to
converge, and aborted before any grasp/contact. The target initially converged well:

- 158 px → 68.8 px → **7.1 px** at z=0.34 m
- **6.6 px** at z=0.295 m

At closer viewpoints, RGB component tracking alternated between the physical red block and small
red shadow/reflection fragments. Component size oscillated:

`2208 → 2283 → 2262 → 2157 → 1349 → 355 → 2269 → 3904 → 2088 → 372 → 2112 → 4685 px`

Those false associations produced 80–130 px vertical jumps. The run stopped at final error
**47.2 px at z=0.20 m**, with no close or contact. It stored one state snapshot and one paired
initial RGB-D checkpoint; because geometry selection was never reached, it correctly contains no
relational state and cannot become a replay candidate.

### Z-calibration conclusion

Doug correctly observed that the arm never approached the table. The logs show why: the safety
guard aborted at the deliberately high servo approach pose, **EE z=0.20 m**. The measured
finger/table contact datum is approximately **EE z=0.016 m**, and the planned compliant grasp
descent toward roughly **0.026 m** never began. This experiment therefore provides no evidence that
the Z/table calibration is wrong; it failed earlier in visual association.

### Aligned depth promoted into the tracking loop

The failed servo was RGB-only even though aligned depth was already saved and later used for
top-face geometry. That was the important architectural surprise: depth participated after
tracking, but not in deciding *which colored component was physical*.

`vision_grasp` now captures aligned RGB-D at every tracked servo step. A color component is accepted
only when:

- enough aligned depth is valid inside it; and
- its median depth is measurably closer than a local ring of surrounding table pixels.

A flat same-color shadow/reflection has table depth and is rejected. A physical block has positive
height above the local table and remains eligible. Synthetic tests confirm that a closer flat red
artifact is rejected while a farther 30 mm raised red block is selected.

### Result and next condition

The retrieval/storage path exists and produced inspectable failure episodes, but the scientific
hypothesis was **not yet tested**: neither live baseline was task-verified, so there is no legitimate
experience to replay. In accordance with the predeclared stopping condition, no third physical
attempt was made.

Next live work should begin with a fresh observation and one aligned-depth-gated baseline. Only
after it yields a post-lift, perceptually verified episode should a nearby held-out placement be
attempted in `replay` mode.

## 2026-07-24 — Codex continuation: aligned depth validates the object, but not a stable image part

> **Authorship note:** This entry was written by **Codex**. Claude may later review, comment on,
> or extend it. The predeclared limit of two failed physical attempts was respected.

Doug asked to proceed after aligned depth had been added to the servo association loop. The target
was the separated central red rectangular block, initially near wrist pixel `[423.9, 367.4]`.
Both trials used the low-speed experimental settings, inspected state and paired RGB-D observations
between consequential actions, and aborted before closing the gripper because the final image
tolerance was not met.

### Trial 1 — depth-gated, original close-range gain

The arm descended only to the guarded approach height, **EE z=0.20 m**, and made no contact. The
aligned-depth gate consistently classified the selected red components as raised by approximately
**20–29 mm** above their local surround. This is important positive evidence: the tracker was no
longer locking onto flat red shadows or reflections.

Nevertheless, the vertical image coordinate oscillated strongly at close range. Representative
transitions included `v=281 → 380` at z=0.25 m and
`285 → 412 → 400 → 291 → 418` around z=0.205 m. The attempt failed safely before grasping.

The immediate control hypothesis was that close-range image feedback was too aggressive. Offline,
the lateral servo gain below z=0.29 m was reduced to **0.35×**, while retaining full gain at higher
poses.

### Trial 2 — depth-gated, damped close-range gain

The damping improved the high-level approach:

- at z=0.34 m, image error fell `157.7 → 68.6 → 7.3 px`;
- at z=0.295 m, error was **6.4 px**;
- at z=0.25 m, error was approximately `22.8 → 20.9 px`; and
- at z=0.205 m, error was **21.7 px**.

At z=0.20 m the association entered a repeatable cycle. A smaller component near `v≈292`
(`npix≈1048`) alternated with larger components near `v≈405, 401, 398, 395`
(`npix≈2323` for one observation). Every selected component remained physically raised, with
estimated height approximately **21–27 mm**, but after 20 servo steps the final error was still
**28.9 px**. The guard again prevented close/contact.

The terminal observation was saved as paired files:

- `frames/2026/07/24/bd841612-3abc-4f4b-a2ed-6ee55b2e7cbb/wrist_000.jpg`
- `frames/2026/07/24/bd841612-3abc-4f4b-a2ed-6ee55b2e7cbb/wrist_000_depth.npy`

The arm was then homed successfully at speed factor 0.05; final maximum joint error was
**0.00891 rad**, and the gripper remained open at approximately **0.07959 m**.

### Scientific conclusion

Aligned depth solved the question it was introduced to answer: tracked pixels correspond to
material raised above the table, and raw aligned depth is now stored beside RGB for later review.
It did **not** make the representation stable. The most likely explanation is that the red
rectangle's visible top and side surfaces fragment into multiple disconnected color components as
viewpoint changes; each fragment is a real raised surface, so a per-component depth test cannot
choose a consistent object part.

This is a useful failed experiment rather than a grasp calibration result. The arm never entered
the compliant table-approach phase, so it still provides no evidence for or against the current Z
contact calibration. The two-failure stopping condition was reached, no task-verified baseline was
created, and therefore no replay trial was legitimate.

The smallest next change is to track a unified depth-foreground object or stable top-face region,
rather than selecting among disconnected color components. That should be tested offline against
the saved paired RGB-D sequence before another live attempt.

## 2026-07-24 — Codex experiment: unified depth foreground removes component hopping

> **Authorship note:** This entry was written by **Codex**. Claude may later review, comment on,
> or extend it. The experiment used the declared two-failure stopping condition.

### Hypothesis

Color should identify only a candidate object seed. A connected aligned-depth foreground mask
around that seed should represent the complete physical object, and its highest-surface centroid
should remain stable even when lighting divides the object's RGB mask into disconnected patches.

`_nearest_raised_instance` was changed accordingly:

1. reject color seeds that are not measurably raised above their local table;
2. construct an 8–80 mm raised-depth foreground in a bounded ROI around the nearest valid seed;
3. select the depth component overlapping that seed; and
4. servo on the centroid of pixels within 10 mm of that component's 90th-percentile height.

The 80 mm ceiling excludes the much taller wrist/gripper foreground. A synthetic test in which
one 30 mm raised rectangle has two disconnected red patches now returns one centroid at the center
of the full depth body, while rejecting a closer flat red artifact. The full offline suite passed:
**26 tests**.

### Live trial 1 — representation succeeds; convergence threshold is too tight

Target: the previously studied center-right red rectangle, selected near `[421, 365]`. Motion used
speed factor 0.05 and `experience_mode="record"`.

The previous large association cycle disappeared. The approach converged as follows:

- z=0.34 m: `164.0 → 71.1 → 7.6 px`;
- z=0.295 m: **4.3 px**;
- z=0.25 m: **8.0 px**;
- z=0.205 m: **18.8 px**; and
- z=0.20 m: centroid error remained **4.2–12.6 px** through the remaining steps.

Estimated top height remained stable at **26.4–29.5 mm**. Unlike the prior tracker, there were no
approximately 110 px jumps between `v≈292` and `v≈405`: the top-face centroid stayed in the local
`u=356–371`, `v=363–383` neighborhood at the final height.

The run nevertheless exhausted 20 steps because the default final threshold was 4 px; the closest
sample was 4.2 px. It aborted before contact or gripper closure. This was classified as the one
small, clearly correctable issue allowed by the protocol, and a second trial used a 10 px final
tolerance.

Initial paired evidence:

- `frames/2026/07/24/94889c40-350f-4b47-8eb0-79254151551f/wrist_initial.jpg`
- `frames/2026/07/24/94889c40-350f-4b47-8eb0-79254151551f/wrist_initial_depth.npy`

### Live trial 2 — intermittent depth-object rejection

The second trial initially tracked cleanly:

- z=0.34 m: **12.7 px**, height 30.7 mm;
- z=0.295 m: **1.0 px**, height 27.5 mm; and
- z=0.25 m: **19.2 px**, height 27.3 mm.

On the following frame, the aligned-depth object was lost or rejected. The skill aborted at
approximately EE z=0.204 m before contact or gripper closure. A terminal RGB image still visibly
contains the unobstructed red rectangle, so this failure is best classified as intermittent depth
mask/validation loss, not disappearance of the physical target:

- `frames/2026/07/24/e9941b15-5a85-475f-a497-c427eb0aa1c2/wrist_000.jpg`
- `frames/2026/07/24/e9941b15-5a85-475f-a497-c427eb0aa1c2/wrist_000_depth.npy`

The two-failure stopping condition was reached. No close, contact, compliant table descent, verified
baseline, or replay occurred. The arm was homed at speed factor 0.05; final maximum joint error was
**0.00888 rad**, EE z was approximately 0.486 m, and the gripper was open at approximately
**0.0793 m**. Final paired home observation:

- `frames/2026/07/24/fac53115-78df-44ea-b761-9c4b48cf00ad/wrist_000.jpg`
- `frames/2026/07/24/fac53115-78df-44ea-b761-9c4b48cf00ad/wrist_000_depth.npy`

### Conclusion and next missing link

The experiment supports its narrow representation hypothesis: connected depth foreground is much
more stable than selecting individual color components. The remaining failure is temporal.
`vision_grasp` currently treats one rejected RGB-D frame as definitive target loss, even after a
long sequence of geometrically consistent observations.

The smallest next experiment is therefore not another segmentation rewrite. Add a short,
explicitly bounded temporal hold: on a rejected frame, do not move; reacquire up to two RGB-D
frames and accept only a depth object consistent with the last centroid and height. Record each
dropout and reacquisition. Offline-inject single-frame missing/invalid depth into a synthetic
sequence first, then repeat one low-speed live baseline.

### Implementation follow-up — bounded temporal depth hold

Codex implemented the proposed temporal continuity rule without conducting another physical trial.
During tracked close servoing, Hebbot now:

- captures the normal aligned RGB-D observation;
- if it is rejected, holds the arm stationary and captures at most two replacement frames;
- accepts a replacement only if it independently passes the same color-seed, raised-depth,
  connected-object, and top-face tests;
- records the number of rejected frames on every successful servo step; and
- records a `depth_reacquire_failed` history event and aborts after three consecutive rejected
  frames.

Synthetic sequence tests verify both sides of the rule: invalid, invalid, valid resumes on the
third frame with exactly two rejections recorded; three invalid frames abort after exactly three
captures. The full offline suite now passes **28 tests**.

This is intentionally not a permissive extrapolator: it does not move on stale perception and does
not relax the geometric acceptance criteria. It adds only short-term persistence to distinguish
one RealSense dropout from evidence that the object is truly lost. The next live baseline remains
subject to the same low-speed, inter-action observation, and two-failure protocol.

## 2026-07-24 — Codex live follow-up: temporal gate passes; Franka UDP timeout stops descent

> **Authorship note:** This entry was written by **Codex**. Claude may later review, comment on,
> or extend it.

One low-speed live baseline tested the bounded temporal RGB-D hold on the same center-right red
rectangle (`target=[421,365]`, `experience_mode="record"`, speed factor 0.05, pixel tolerance
10 px). Pre-motion state was nominal at home, with EE z approximately 0.485 m, gripper width
approximately 0.0793 m, and wrist aligned-depth coverage **76.3%**.

The client process terminated without returning the skill result, so Codex treated the unknown
state as a stopping condition and issued no second grasp attempt. Immediate observation found:

- the arm stationary at EE z approximately **0.204 m**;
- the red block still on the table and visibly unobstructed;
- gripper width approximately **0.0417 m**, consistent with grasp setup rather than a completed
  closure; and
- no evidence of contact or scene disturbance.

The persisted episode `76b45884-4f54-4f5d-ab5c-dd9ec752fb5a` provides the decisive diagnosis:

- it contains initial and `before_close_0` paired RGB-D checkpoints;
- it contains state snapshots at t=0 and t=21.63 s;
- the pre-close checkpoint was saved at t=21.65 s, proving the visual-servo checkpoint passed and
  the grasp pipeline advanced beyond the earlier failure boundary; and
- it ended after approximately 45.4 s with
  `RuntimeError: libfranka: UDP receive: Timeout`.

Paired pre-close evidence:

- `frames/2026/07/24/76b45884-4f54-4f5d-ab5c-dd9ec752fb5a/wrist_before_close_0.jpg`
- `frames/2026/07/24/76b45884-4f54-4f5d-ab5c-dd9ec752fb5a/wrist_before_close_0_depth.npy`

This outcome supports the temporal-perception hypothesis narrowly: the pipeline no longer aborted
at a transient aligned-depth rejection and did reach pre-close setup. It does **not** demonstrate
successful compliant descent or grasp. The terminal failure belongs to the hardware-process/control
layer, not the deliberate selection, experience substrate, or perception association layer.

The arm was recovered with `home(speed_factor=0.05)`. Final maximum joint error was **0.00908 rad**,
EE z was approximately 0.486 m, and the gripper was open at approximately 0.0792 m. Final paired
home observation:

- `frames/2026/07/24/0cd3716f-a6dc-4d11-869b-4fd38105806d/wrist_000.jpg`
- `frames/2026/07/24/0cd3716f-a6dc-4d11-869b-4fd38105806d/wrist_000_depth.npy`

Before another physical grasp, inspect the compliant controller transition and Franka communication
timing. The next experiment should distinguish a transient FCI/network timeout from a reproducible
controller-start or 100 Hz loop failure using the smallest non-contact or high-clearance controller
exercise available. Do not weaken the perception gate in response to this episode.

## 2026-07-24 — Codex experiment: verified baseline and verified relational replay

> **Authorship note:** This entry was written by **Codex**. Claude may later review, comment on,
> or extend it.

This continuation closed the first end-to-end experience-reuse link in the current Hebbot
implementation: deliberate record execution → independent task verification → episode storage →
relational retrieval → bounded action-parameter replay → independent replay verification.

### Isolating and fixing the Franka timeout

A new diagnostic primitive, `compliant_hold`, started the same Cartesian impedance controller at
the home pose without translation, descent, contact, perception, or gripper commands. A two-second
live run completed **199 cycles at 100 Hz**, with **1.12 mm maximum Cartesian drift** and no FCI
error. This localized the earlier failure away from controller startup and basic communication.

The actual `grasp_at` lifecycle exposed two coupled problems:

1. the experimental descent from z=0.20 m to z=0.026 m at 0.02 m/s requires approximately
   **8.7 s**, but each control context had a fixed **8.0 s** runtime; and
2. local grip-depth retries could execute blocking Franka Hand commands and create another control
   context while the arm controller remained active.

The failed episode lasted approximately three 8 s intervals after its pre-close checkpoint, which
was consistent with this lifecycle rather than random sensor loss. Each descent now receives a
distance/speed-derived runtime budget (10.1 s for the full experimental descent), and its arm
controller is stopped in `finally` before any gripper operation. Every local inch-down retry starts
a fresh bounded controller context.

### First physical grasp after the lifecycle fix

One low-speed record trial successfully:

- converged in 8 visual-servo steps to **5.8 px**;
- recovered from exactly **one rejected depth frame** while stationary;
- measured block geometry approximately 66.3 × 29.2 × 29.7 mm;
- descended to **EE z=0.02538 m**;
- closed with no air retry and one grip-depth retry;
- held at **28.75–29.03 mm** after lift; and
- placed the block back compliantly at its source XY before homing.

This was the first direct confirmation that the arm really does enter the calibrated near-table
phase: the earlier z=0.20 m stops were perception guards, not the final grasp height.

The automatic label was nevertheless false because verification saved one RGB-D frame and then
captured a different, unrecorded RGB frame for classification. The saved frame visibly showed the
held red rectangle clipped at the lower edge, but close-range exposure darkened it to median RGB
approximately `(99,36,28)`, below the on-table red mask's `R>115` threshold.

### Evidence-aligned held-object verification

`read_camera` now accepts an internal injected capture so storage and classification use the exact
same RGB-D observation. A verifier-specific held-red cue handles the darker close-range appearance
only in the central/lower gripper region; it never changes servo segmentation or motion. Against
the retained successful frame it finds **2,220 pixels at `(392.3,471.4)`**. Synthetic tests confirm
that the cue accepts a dim clipped held object and rejects the same colors in the upper scene.

### Automatically verified record baseline

Episode `428b8512-c2d4-4f98-997a-354f7fdd2253` then produced:

- visual convergence in 7 steps, final error **8.5 px**;
- depth geometry approximately 64.4 × 27.7 × 29.5 mm;
- grasp at **EE z=0.02522 m**;
- actual held width **28.77 mm**;
- mechanical success `true`;
- proprioceptive success `true`; and
- task-verified success `true`.

The persisted post-lift frame itself supplied the held-object verdict:
`held_dim_red`, **5,280 px**, `(u,v)=(377.9,455.7)`, 154.9 px from the declared gripper-region
reference. Stored action:

- grip z 0.026 m;
- grip width 0.0237 m;
- open width 0.0397 m; and
- grip yaw −6°.

Paired post-lift evidence:

- `frames/2026/07/24/428b8512-c2d4-4f98-997a-354f7fdd2253/wrist_after_lift.jpg`
- `frames/2026/07/24/428b8512-c2d4-4f98-997a-354f7fdd2253/wrist_after_lift_depth.npy`

### Automatically verified replay

Episode `95f56be9-ee5f-4a9c-b3bd-c8cd4325b2d4` ran in
`experience_mode="replay"`. Retrieval found the verified baseline with relational similarity
**0.9394**, above the declared 0.82 threshold, selected that episode, and reused exactly the stored
bounded action parameters.

Replay then:

- converged in 9 servo steps to **6.9 px**;
- measured nearby geometry approximately 64.1 × 29.2 × 31.5 mm;
- descended without UDP timeout to **EE z=0.02532 m**;
- held at **28.79 mm**;
- produced mechanical, proprioceptive, and task-verified success; and
- verified the exact post-lift frame using 2,598 held-red pixels.

Paired replay evidence:

- `frames/2026/07/24/95f56be9-ee5f-4a9c-b3bd-c8cd4325b2d4/wrist_after_lift.jpg`
- `frames/2026/07/24/95f56be9-ee5f-4a9c-b3bd-c8cd4325b2d4/wrist_after_lift_depth.npy`

Both successful grasps were reset with compliant `place_block` at their current XY locations.
After replay, the arm was homed at speed factor 0.05 with maximum joint error **0.00937 rad**,
EE z approximately 0.486 m, and the gripper open at approximately 0.0792 m.

### Scientific interpretation

This is the first genuine, inspectable Hebbot reuse result, but it is a deliberately narrow form of
compression. A verified episode influenced later execution through relational retrieval and
bounded action-parameter reuse. It is more than scripted automation because the replay choice and
parameters came from stored verified experience, and the replay was independently reverified.

It is **not yet policy or reflex distillation**. The expensive visual servo, depth geometry,
controller sequence, and verification still execute in full. No latency, deliberation cost, or
sample-efficiency reduction has yet been demonstrated beyond replacing heuristic grasp-parameter
selection. The next thesis-focused comparison should quantify record versus replay decision path,
latency, perception calls, corrections, and success across several nearby placements, then define
a strictly gated reflex candidate whose execution omits a measurable portion of deliberate work
and falls back on novelty or verification failure.
