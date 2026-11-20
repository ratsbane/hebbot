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

## 2026-07-24 — Codex: warm-start distillation pipeline and first batch boundary

> **Authorship note:** This entry was written by **Codex** while Doug was temporarily detached.
> Claude may later review, comment on, or extend it.

### Smallest reflex target

The next compression target is a learned warm start:

`acquisition observation + initial EE pose → predicted successful pre-close EE xy`

The candidate does not own grasping, contact, or verification. If promoted, it may propose one
bounded approach pose; aligned-depth confirmation, workspace checks, compliant descent,
independent verification, and deliberate fallback remain mandatory. The measurable intended
benefit is fewer RGB-D captures and visual-servo corrections, not merely different parameter
selection.

### Offline distillation implementation

A new `hebbot.learning.warm_start` module now:

- extracts only `task_verified_success=true` `vision_grasp` episodes;
- pairs the first servo observation with the state snapshot nearest the persisted pre-close frame;
- fits a small ridge-regression policy rather than a neural network;
- performs leave-one-out shadow evaluation;
- measures spatial diversity and deliberate servo-step cost;
- records source episode IDs, feature normalization, weights, training bounds, metrics, and
  promotion thresholds in a versioned JSON artifact; and
- returns an explicit orchestrator fallback unless the artifact is promoted, the query lies inside
  its normalized training domain, and predicted XY remains inside `[0.30,0.60] × [-0.28,0.28] m`.

The substrate store gained public episode-state and episode-frame readers so training code does not
reach through the SQLite connection. Synthetic tests cover insufficient-data rejection,
low-error/diverse promotion, out-of-domain fallback, and state/frame extraction.

The first real artifact, trained from the two prior verified episodes, correctly refused promotion:

- **2 examples < 6 minimum**;
- only **8.7 mm** pre-close XY span < 60 mm minimum; and
- too few examples for leave-one-out evaluation.

Artifact:
`/home/doug/hebbot-data/policies/vision_grasp_warm_start_231950c4ca0c.json`

### Live collection cycle 1 — verified

The isolated right-hand red rectangle was deliberately grasped from target `[443,359]`:

- 8 servo steps, final error **5.1 px**;
- geometry approximately 61.2 × 29.3 × 28.9 mm;
- grasp EE z **0.02543 m**;
- held width **28.84 mm**;
- mechanical, proprioceptive, and task-verified success; and
- no depth reacquisition during servoing.

Episode `99abf39a-4810-4b52-8e9c-0d5aaa0e8960` was reset compliantly to
`[0.47,0.12]` and the arm homed at speed factor 0.05.

With three verified examples, the second candidate still correctly refused promotion:

- **3 examples < 6 minimum**;
- **17.1 mm** pre-close XY span < 60 mm minimum; and
- leave-one-out RMSE **13.4 mm**, inside the provisional 15 mm accuracy gate.

Artifact:
`/home/doug/hebbot-data/policies/vision_grasp_warm_start_afb458bc1cdc.json`

This is encouraging but not evidence of a useful policy: the dataset is still too small and
spatially narrow.

### Live collection cycle 2 — safe failure exposes seed/object threshold coupling

At the new placement, the target appeared near `[444.5,418.3]`. The deliberate servo progressed
from 210.8 px to 13.0 px through z=0.295 m, then rejected three stationary RGB-D frames at z=0.25 m
and aborted before contact or closing. The batch stopping condition was honored and the arm homed.

Terminal paired evidence:

- `frames/2026/07/24/8b10d922-c706-46d2-a17d-673ee3dc160b/wrist_000.jpg`
- `frames/2026/07/24/8b10d922-c706-46d2-a17d-673ee3dc160b/wrist_000_depth.npy`

Offline replay revealed the exact cause. Close-range illumination split the valid red mask into two
real components of **538 and 587 px**. The unified physical depth body was large enough, but the
code applied the final **600 px object threshold to each color seed before depth unification**.

Color now serves as a weaker seed (`max(80, final_min_px/3)`), while the unified connected-depth
body must still satisfy the full 600 px threshold. The preserved failure frame now resolves to one
**5,132 px** depth object at `(353.2,343.4)`, height **26.4 mm**. A regression test preserves this
behavior. Local-table rings also exclude all calibrated object colors, preventing adjacent
green/blue objects from masquerading as table support.

### Status

The training and promotion machinery exists and fails closed. No learned candidate has controlled
the robot. Live collection stopped after the first failed cycle, as declared. The offline suite
passes **38 tests**.

The next session should restart the MCP server so the seed-threshold fix is live, then continue the
spatially diverse record batch. Promotion still requires at least six verified examples, at least
60 mm pre-close XY span, leave-one-out RMSE at or below 15 mm, a bounded workspace prediction, and
an in-domain query.

### Post-restart collection — perception passes; high contact stall prevents grasp

After restarting Codex, the managed Hebbot MCP transport returned live at the updated skill
versions and the seed-threshold fix was exercised on the same right-hand red rectangle near
`[444,414]`.

The perception hypothesis passed:

- tracking crossed the former z=0.25 m failure point;
- at z=0.25 m the unified depth object contained **5,436 px** with **0.8 px** image error;
- it reached z=0.20 m with **7.7 px** final error; and
- no aligned-depth reacquisition was required.

The subsequent grasp failed safely for a different reason. The compliant descent stalled at
**EE z=0.04371 m**, 17.7 mm above the intended 0.026 m grip height. Four local closes found air
and reopened; terminal gripper width was approximately 0.040 m. Terminal force included
approximately **−7.45 N** in z, and the saved wrist frame still showed the red rectangle directly
beneath the wrist. This combination is most consistent with a fingertip landing on the block top:
the current broad `stall_guard` permitted close attempts because z was below 0.068 m, but lowering
the commanded target could not overcome the same physical contact.

No fourth verified training example was created and the warm-start candidate remains at three
examples. The single-attempt stopping condition was honored and the arm was homed at speed factor
0.05 with maximum joint error **0.00983 rad**.

Terminal paired evidence:

- `frames/2026/07/25/4e3f49bc-af5f-48f0-9368-7c0e9ba4c3f5/wrist_000.jpg`
- `frames/2026/07/25/4e3f49bc-af5f-48f0-9368-7c0e9ba4c3f5/wrist_000_depth.npy`

The next change should distinguish “reached grip depth” from “persistent high contact” relative to
the episode's intended `grasp_z`, and use the retained pre-close RGB-D/state evidence to determine
whether the residual error comes from final centering, yaw/leveling geometry, or the contact
classifier. Do not undo the seed-threshold fix; it worked as intended.

### Alignment diagnosis and guarded validation

Offline comparison with the nearby verified episode showed that both used −13° grasp yaw, while
the failure retained **11.9 px** height-corrected top-face error after its sole geometry recenter.
The routine did not recheck that correction before descent. Two bounded safety changes followed:

1. geometry alignment now observes and corrects up to three times, and refuses to descend unless
   the final top-face error is within the existing pixel tolerance; and
2. an air-close after the EE physically stalls more than `grip_depth_tol` above its commanded
   target is classified as persistent high contact, reopened, and returned for lateral
   re-acquisition rather than commanding three further downward retries.

A single live validation on the unchanged placement aborted safely **before descent or contact**,
demonstrating that the new pre-descent guard works. It also exposed the next representation
mismatch. Alignment errors oscillated:

`28.37 → 53.23 → 34.16 → 25.58 px`

and the reported geometry collapsed from the expected roughly 57 × 28 mm rectangle to
**32.9 × 14.8 mm**. The reason is architectural: coarse servoing uses the unified connected-depth
body, but `_perceive_pose_3d` still selected one illuminated color component. Repeated corrections
therefore chased different real fragments of the same block.

The post-correction alignment observation now uses `_reacquire_raised_instance`, the same unified
depth body used by coarse servoing. It updates only top-face `(u,v,height)` while retaining the
initial width, length, and grasp-axis estimate. Alignment telemetry now includes dimensions and
rejected follow-up frame counts so another representation switch cannot remain hidden.

The one-failure stopping condition was honored. No fourth verified example was added, no learned
policy controlled the arm, and the arm was homed at speed factor 0.05 with maximum joint error
**0.00939 rad**.

Terminal paired evidence:

- `frames/2026/07/25/add66e88-3ef0-4c57-8230-0b64e652bfe6/wrist_000.jpg`
- `frames/2026/07/25/add66e88-3ef0-4c57-8230-0b64e652bfe6/wrist_000_depth.npy`

The offline suite now passes **40 tests**. The next live session requires an MCP restart to load
the unified-depth alignment fix, then one low-speed validation under the same guard.

### Unified-depth validation succeeds; post-lift view clips the held block

*Added by Codex. Earlier notebook entries were written by Claude; Claude may return to review,
comment on, and contribute to this work.*

After the MCP restart, one low-speed record-mode validation targeted the isolated left red
rectangle near `[194,351]`. The unified-depth alignment change behaved as intended:

- coarse servoing converged in **10 steps** to **3.7 px** error;
- the first geometry observation was already within tolerance at **4.6 px**, so no corrective
  oscillation occurred;
- perceived geometry was approximately **68.7 × 32.6 × 29.3 mm** at **−1°** yaw;
- the close retained an object at **29.2 mm** gripper width;
- there were **zero air retries**, one depth retry, and no persistent-high-contact event; and
- the final grasp depth was **EE z=0.02872 m**.

Episode `46dc107e-17fe-4619-adb3-a03a88674c23` therefore passed the mechanical and
proprioceptive checks. It did **not** pass the live task verifier: at this far-left pose and yaw,
the held block was clipped below the post-lift wrist frame, so target color was absent. The stored
episode remains unverified and is not retroactively relabeled; the warm-start training set remains
at three verified examples.

Saved paired post-lift evidence:

- `frames/2026/07/25/46dc107e-17fe-4619-adb3-a03a88674c23/wrist_after_lift.jpg`
- `frames/2026/07/25/46dc107e-17fe-4619-adb3-a03a88674c23/wrist_after_lift_depth.npy`

Offline replay supports a narrow second visual cue. At the height-corrected predicted source pixel
`(374.7,447.0)`, the saved post-lift frame contains no red target. The verifier can now combine
this **source-vacant** observation with the existing mechanical/proprioceptive grasp requirement
when the held object itself is out of view. A normal target-color component still near that source
is explicitly rejected as `target_remains_at_source`; the existing dim-red held-object cue remains
accepted.

This is useful but not complete identity evidence: vacancy plus retained gripper width could be
fooled if the target moved out of view while another object was caught. The experiment reduces
that ambiguity by deliberately selecting an isolated target, and all RGB-D evidence remains
stored for review. The next live record attempt can evaluate the cue prospectively; this episode
must remain a documented verifier miss.

The block was returned at low speed and the arm was homed. The offline suite passes **42 tests**.

### Prospective verifier trial exposes stale MCP skill process

*Added by Codex.*

One prospective low-speed record trial targeted the sole detected red rectangle at
`[321.2,326.5]`. The grasp was mechanically successful:

- coarse servoing converged in **9 steps** to **5.8 px**;
- initial geometry alignment passed at **8.09 px**, without recentering;
- perceived geometry was approximately **68.3 × 32.1 × 29.0 mm** at **−21°** grasp yaw;
- the close retained an object at **29.2 mm** width;
- there were zero air retries, one depth retry, and no persistent-high-contact event; and
- actual grasp depth was **EE z=0.02757 m**.

The live result nevertheless returned the old verifier payload:

`{"present": false, "note": "target colour absent after lift"}`

That exact payload cannot be produced by the newly added `_post_lift_visual_evidence` path, which
returns an explicit `cue`. The MCP connection had been restarted before that code was edited, but
the serving Hebbot process had not subsequently reloaded it. This trial therefore tested the old
verifier rather than the intended prospective hypothesis.

Episode `91f09005-e9bf-4375-b294-0d1709c33ec8` remains unverified and must not enter the training
set. Its initial, before-close, and after-lift observations all include paired RGB and aligned
depth. An additional held-state RGB-D observation was stored under
`frames/2026/07/25/91088b6b-b6a8-486d-9648-d50f83c75734/`.

The declared single-attempt limit was honored. The block was replaced at approximately
`[0.3715,-0.2025]`; the arm was homed at speed factor 0.05 with maximum joint error
**0.00601 rad**.

Before another physical trial, restart the actual Hebbot MCP server process and confirm that its
registered `vision_grasp` implementation/version reflects the working tree. This process/code
identity should eventually become explicit telemetry, since reconnecting a client is not evidence
that a long-running hardware process loaded new skill code.

### Fresh v124 prospective trial stops on persistent high contact

*Added by Codex.*

The stale MCP process (PID 46208) was terminated and the Codex MCP connection was restarted. The
new process had PID 51077 and dynamic registration advanced `vision_grasp` from **v123 to v124**,
demonstrating that the working-tree implementation, including the new post-lift verifier, was
loaded before motion.

One low-speed record attempt again targeted the red rectangle near `[321,327]`. Coarse visual
servoing located the object and converged in **10 steps** to **9.0 px**. Geometry alignment passed
the current 10 px gate on its initial observation at **9.19 px**. Perceived geometry was
approximately **67.8 × 31.3 × 33.1 mm**, and the chosen grasp yaw was **+22°**.

The grasp did not reach verification. Descent stopped at **EE z=0.05262 m**, 13.4 mm above the
derived 0.0392 m grasp target. Closing at that pose found air at approximately 43.0 mm width, and
the new persistent-high-contact classifier stopped immediately rather than commanding lower
retries. No object was retained, no post-lift frame was generated, and no example entered the
training set.

Episode `4b3c3480-8b66-49ae-b553-caeb98c77ef6` retains paired RGB-D for the initial and
before-close observations. The terminal contact-state RGB-D observation is stored under
`frames/2026/07/25/7e418600-bcfd-46e4-a514-5005c2fd4f56/`.

This result separates two questions that had been confounded:

1. process identity is now confirmed and the new verifier is live; but
2. a geometry observation barely inside the **10 px** alignment gate can still place a fingertip
   on the block top at this pose/yaw, preventing the experiment from reaching that verifier.

The single-attempt stopping rule was honored. The arm was homed at speed factor 0.05 with maximum
joint error **0.00602 rad**. The failed episode remains explicit experience rather than being
hidden or relabeled.

### Final-pose clearance boundary for eventual clutter

*Added by Codex.*

Review of the saved before-close RGB frame showed that the failed target was isolated; no neighbor
was close enough to explain the high contact. The 9.19 px alignment residual was also mostly along
the rectangle's long axis, so replacing the existing gate with an unexplained global 5 px
constant would discard useful geometry and would not address clutter.

The more important architectural omission was temporal: target alignment was last checked before
the wrist yawed to the chosen grasp axis. The eye-in-hand system then changed orientation and
descended without another observation. Tool/camera rotation offsets can consume the small
finger-clearance margin, and in future clutter a neighbor can enter a finger corridor only after
this final orientation change.

`vision_grasp` now establishes a fail-closed final-pose boundary after yaw and before descent:

- it re-acquires the same color-seeded connected-depth body while stationary;
- it derives the allowed image error from the physical per-side jaw clearance,
  `(open_width - object_width) / 2`, projected through camera focal length and object depth;
- it reserves 2 px for calibration/segmentation uncertainty and respects the caller's stricter
  requested tolerance;
- it refuses descent if the target is unavailable or outside that clearance-derived bound; and
- it stores the exact RGB-D capture used for this decision as `final_pose`, rather than saving a
  nearby but different frame.

For the latest failure, 43.3 mm open width around a 31.3 mm object leaves 6.0 mm per side. At
303.5 mm depth this is approximately 7.8 px; after the uncertainty reserve, the derived gate is
approximately **5.8 px**, compared with the pre-yaw observation's 9.19 px error.

This is deliberately only the first clutter-compatible representation. It treats centroid error
conservatively and does not yet model the two finger swept volumes, target mask, neighboring depth
bodies, or support relations separately. The stored final-pose RGB-D makes those extensions
possible without changing what an episode must retain. `source_vacant` verification must remain
restricted to deliberately isolated targets; clutter will require target-specific scene-change
or held-object evidence.

No live motion was performed for this change. The offline suite passes **43 tests**. The MCP server
must be restarted again before a guarded live validation because the running v124 process predates
this edit.

### Live final-pose guard validation — safe refusal before contact

*Added by Codex.*

After a genuine process restart, the Hebbot MCP server ran as PID 55494 and dynamic registration
reported `vision_grasp` **v127**, confirming that the final-pose implementation was live.

One low-speed record attempt targeted the same red rectangle near `[321,327]`. Coarse servoing
converged in **9 steps** to **9.4 px**. The existing pre-yaw geometry gate accepted its first
observation at **8.68 px**. Perceived geometry was approximately **71.8 × 32.0 × 32.1 mm**, with
a chosen grasp yaw of **+20°**.

After yaw, the new stationary re-observation measured:

- target top-face centroid: `(347.50, 328.59)`;
- predicted grasp pixel: `(351.07, 337.88)`;
- final-pose error: **9.95 px**;
- physical clearance-derived tolerance: **5.71 px**;
- unified connected-depth body: **5,799 px**, 29.3 mm high; and
- zero rejected depth frames.

The skill therefore refused descent with
`final-pose jaw-clearance alignment refused descent`. There was no table approach, fingertip
contact, close, lift, or retry. This is the intended fail-closed result and directly prevents the
persistent-high-contact failure observed in the preceding live attempt.

Episode `e461f126-6e61-4c55-88b8-b9756f2cbdb6` stores initial, before-close, and exact evaluated
final-pose RGB-D. The arm was homed at speed factor 0.05 with maximum joint error
**0.00624 rad**. No training example was added.

The measurement also identifies the next bounded improvement. Pre-yaw alignment still uses the
legacy 10 px threshold, so it accepted 8.68 px despite the physical jaw-clearance budget being
5.71 px. Yaw changed the centroid residual by approximately 1.9 px, but most of the unsafe error
already existed before yaw. The pre-yaw correction loop should use the same clearance-derived
tolerance, while the post-yaw observation remains an independent final-pose safety check. A later
clutter experiment can replace conservative centroid gating with explicit target and neighbor
occupancy in the two finger swept volumes.

### Pre-yaw correction adopts the physical clearance budget

*Added by Codex.*

The bounded geometry correction loop now derives its convergence threshold from the nominal
object width, jaw opening, object depth, and the same 2 px uncertainty reserve used by the
post-yaw guard. It records `pixel_tolerance` alongside every observed `pixel_error` and continues
recentering when an observation satisfies the legacy caller tolerance but not the physical
clearance budget.

For the preceding live scene, this changes the decision at 8.68 px from “aligned” to “correct
again,” because the predicted jaw-clearance tolerance was approximately 5.7 px. After the
correction loop converges, wrist yaw still triggers a fresh, independently stored and evaluated
RGB-D observation. A replay-selected action may later change the opening; the final-pose check
uses that actual opening and remains the authoritative fail-closed boundary.

This is still classical scaffolding, but its episode fields are deliberately learning-oriented:
the sequence of requested corrections, changing alignment errors, physical tolerance, final-pose
error, and contact/refusal outcome form supervised examples for a future correction or grasp-
feasibility model. The hand-coded controller is therefore producing a dataset that can be
evaluated for compression into a learned component rather than only accumulating more branches.

No physical motion was performed for this edit. The offline suite remains at **43 passing tests**.

### Two-stage live validation isolates yaw-induced displacement

*Added by Codex.*

After restarting the server as PID 59024, dynamic registration reported `vision_grasp` **v128**.
One low-speed record attempt targeted the red rectangle near `[321,327]`.

The clearance-consistent pre-yaw stage worked:

- coarse servoing converged in 11 steps to **9.1 px**;
- geometry observation measured **3.67 px** error against a **5.71 px** physical tolerance;
- the stage passed without an unnecessary correction; and
- perceived geometry was approximately **59.7 × 32.2 × 31.5 mm**.

After applying the selected **+27°** wrist yaw, the independently observed error increased to
**7.43 px**, outside the unchanged 5.71 px clearance budget. The connected-depth target remained
healthy at 5,406 px with zero rejected frames, so this was not a perception dropout. The final-
pose guard refused descent before contact.

Episode `55e49787-a4b2-4076-a938-27ea4be641e9` stores the initial, before-close, and exact
final-pose paired RGB-D observations. No grasp or training example occurred. The arm was homed at
speed factor 0.05 with maximum joint error **0.00596 rad**.

This is strong evidence that the remaining offset is introduced by the eye-in-hand orientation
change rather than inherited from inadequate pre-yaw centering. The next bounded change should
apply image-Jacobian recentering in the final yawed frame, rotating the planar correction into the
current wrist orientation, then re-observe under the same clearance gate. The measured requested
and realized corrections should be retained so a learned residual model can eventually replace
or augment the nominal rotational transform.

### Bounded yaw-aware final-pose correction

*Added by Codex.*

The final-pose stage now performs an observation followed by at most two translational corrections
before refusing descent. The existing image Jacobian was calibrated in the reference gripper-down
orientation; its planar world correction is rotated by the actually applied wrist yaw before the
motion command. The code also distinguishes the canonical symmetric grasp yaw from an alternate
180° wrist configuration when joint-7 comfort selects that equivalent pose.

Every final-pose observation records error, physical tolerance, connected-body size and height,
rejected depth frames, and—when a correction is attempted—the commanded world `dx`, `dy`, and yaw.
The exact last evaluated RGB-D capture remains the stored `final_pose` evidence. Descent is still
authorized only by a fresh observation inside the physical clearance budget.

This nominal rotational model is intentionally small and interpretable. Its recorded correction
and residual pairs provide direct supervised data for learning systematic eye-hand residuals as a
function of wrist yaw, workspace pose, depth, and object geometry. A learned component can later
predict a residual on top of the geometric transform and be evaluated against the unchanged
fail-closed clearance gate.

No additional physical motion was performed after the v128 safe-refusal trial. The offline suite
passes **44 tests**.

### Yaw-aware correction response — full gain oscillates

*Added by Codex.*

After restarting as PID 62694, `vision_grasp` registered as **v131**. One low-speed record trial
again targeted the isolated red rectangle.

Pre-yaw alignment passed at **4.15 px** against a **5.67 px** clearance-derived tolerance. After
the selected +20° yaw, the initial final-pose error was **23.02 px**. The new correction loop then
produced:

| Pass | Error (px) | Commanded world correction (mm) |
|---|---:|---:|
| 0 | 23.02 | dx −14.61, dy −5.38 |
| 1 | 25.99 | dx +13.59, dy +10.96 |
| 2 | 11.45 | none; bounded stop |

All three observations independently found a healthy connected-depth body with zero rejected
frames. The guard refused descent; there was no contact, close, grasp, or training example.
Episode `21a2c123-33b4-4aeb-92d9-6b672a5c2d66` retains initial, before-close, and final-pose
paired RGB-D plus the complete correction/response history. The arm was homed with maximum joint
error **0.00588 rad**.

The rotational correction direction produced useful motion, but full gain was too aggressive and
oscillated. Under a local linear approximation, half of the first measured image displacement
would have left roughly 4–5 px residual, near or inside the 5.67 px gate. Yawed final-pose
recentering now uses **0.5 gain**, retains the two-correction bound, and records the gain with every
command. Pre-yaw correction remains at its established full gain.

This is an early concrete example of experience improving execution parameters. It is not yet
neural learning—the gain choice is deliberate analysis of one episode—but the stored input,
command, and response tuples are exactly the data from which a learned local Jacobian or residual
correction model can later be estimated and compared.

### Damped correction converges; centroid clearance still permits top contact

*Added by Codex.*

After restart, `vision_grasp` registered as **v132** under PID 65122. The episode-derived 0.5
yawed-recenter gain succeeded at its intended task:

- pre-yaw geometry correction improved **6.03 → 1.20 px**;
- after +24° yaw, final-pose error began at **10.62 px**;
- one damped correction of dx −2.24 mm, dy −2.75 mm reduced it to **2.32 px**;
- the physical clearance-derived tolerance was **5.67 px**; and
- both observations found healthy connected-depth bodies with zero rejected frames.

Despite this excellent centroid alignment, descent stalled at **EE z=0.04309 m**, 17.1 mm above
the 0.026 m target, and closing found air at approximately 42.9 mm. The persistent-high-contact
guard stopped without retry. Episode `1a75bf30-c117-4f37-9266-2e441e5f3d6e` remains failed and
stores the full correction trajectory plus initial, before-close, and final-pose RGB-D. A terminal
contact RGB-D pair is stored under
`frames/2026/07/25/fb733ef9-fa14-4714-b4be-33a1571aa5bc/`. The arm was homed with maximum joint
error **0.00634 rad**.

This falsifies the working assumption that centroid error plus scalar jaw-opening margin is a
sufficient grasp-clearance test. Historical verified episodes do not support simply flipping the
yaw formula: successful examples used object axis 96.3° → grasp yaw −6° and 102.9° → −13°, the
same `yaw ≈ 90° − object_axis` relationship as the current 65.9° → +24° selection.

The next representation should explicitly project the target's oriented top mask and the two
finger approach footprints in the final wrist RGB-D frame. It should separately answer:

- does either finger footprint overlap the target top before the fingers can straddle it?
- is elevated non-target geometry present in either finger swept corridor?
- does the target width along the actual closing axis fit inside the measured opening?
- how much uncertainty remains from depth edges, hand-eye calibration, and wrist leveling?

This is the same representation required for adjacent blocks and clutter, so the failure marks a
useful point to stop tuning scalar centroid thresholds. The successful damped correction remains
valuable execution experience; the contact failure supplies the negative feasibility label.

### Existing footprint dataset is confounded; add a no-contact final-pose probe

*Added by Codex.*

Offline comparison of the available labeled episodes found that the three verified grasps are all
clustered near world y=+0.11 to +0.12 m with object angles 96–103° and grasp yaws −6° to −13°.
The two persistent-high-contact episodes are clustered near y=−0.22 m with object angles 66–68°
and grasp yaws +22° to +24°. The current five examples therefore cannot distinguish workspace
kinematics, wrist yaw, hand-eye residual, and actual finger-footprint interference.

The older verified episodes also predate storage of the exact post-yaw final-pose frame. It would
be scientifically unsound to “calibrate” a footprint separator from pre-yaw success images and
post-yaw failure images.

A new classical skill, `vision_grasp_probe`, now runs the complete acquisition, depth geometry,
pre-yaw alignment, yaw selection, damped final-pose correction, and clearance decision, then
returns at the approximately 0.30 m observation height **without descending**. It:

- caps motion speed at 0.05;
- forces zero grasp retries and an internal recorded episode;
- stores the exact evaluated final-pose RGB-D and all correction/response telemetry;
- reports `safe_to_descend` under its own expectation, rather than masquerading as a failed grasp;
  and
- never calls `grasp_at`.

This enables a safe calibration matrix across workspace position and object orientation before
additional contact trials. The first dataset should deliberately cross the current confound:
positive-y with a +20°-class object yaw, and negative-y with a near-zero yaw, followed by controlled
two-block adjacency. Those final-pose observations can support an explicit finger-corridor model
and later a learned feasibility predictor.

No physical motion was performed for this addition. The offline suite passes **45 tests**. The
running MCP process must be restarted to register the new probe skill.

### First no-contact probe exposes observation-pose identity bug

*Added by Codex.*

After restart, `vision_grasp_probe` registered as v2 under PID 69841. The fresh home-pose RGB frame
showed two red tabletop blocks, approximately at `[147,340]` and `[474,414]`, plus the robot's own
small red wrist label near the top of the image. The legacy scene tracker nevertheless returned a
stale single red target near `[321,327]`.

The first probe used that stale pixel. After moving from home height to the 0.34 m acquisition
height, it repeatedly tracked a red/depth component at `v=7–17`, corresponding to robot self near
the top edge, rather than either tabletop block. It remained at the safe acquisition height for
20 bounded servo observations and never descended. The arm was homed with maximum joint error
**0.00412 rad**.

Episode `b83d4dfe-27c8-442d-a3c4-a7f822403a65` is not a footprint-calibration example. It records
a target-identity/observation-pose failure. The tool's top-level expectation also misleadingly
reported success because the early exit lacked the `safe_to_descend` field.

The probe now supports semantic same-colour selection—`leftmost`, `rightmost`, or `largest`—which
is resolved only **after** moving to the canonical acquisition height. Candidates with `v<60`
are excluded as wrist self. Every early return is normalized to `dry_run=true` and
`safe_to_descend=false`, so expectation labeling fails closed.

This matters beyond calibration: pixel identity from one eye-in-hand pose is not stable after a
large camera-height change, and stale scene tracks become dangerous in clutter. Target selection
must be attached to a defined observation pose or a 3-D/world relation, not treated as a timeless
image coordinate. The offline suite remains at **45 passing tests**.

### First valid semantic no-contact probe crosses yaw and workspace

*Added by Codex.*

After restart, `vision_grasp_probe` v3 ran under PID 71558. One `leftmost` red probe resolved its
instance only after reaching the canonical 0.34 m acquisition height. It selected `[517.9,474.8]`
and tracked that physical block to approximately world `[0.468,+0.161]`.

The contact-free alignment sequence succeeded:

- coarse servo converged in 9 steps to 8.0 px;
- pre-yaw geometry error was **3.81 px** inside a **5.76 px** physical tolerance;
- selected grasp yaw was **+23°**;
- after yaw, error was **9.66 px**;
- one damped correction (dx −1.81 mm, dy +2.58 mm) reduced it to **4.85 px**; and
- the final connected-depth body contained 5,995 px with zero rejected frames.

The probe returned `safe_to_descend=true`, stored exact final-pose RGB-D, and issued no descent,
close, or grasp. Episode `8d9a4318-b8fd-4c61-b357-ef7b7438241e` is therefore a valid
final-pose calibration example, not a manipulation-success label. The arm was homed at speed
factor 0.05 with maximum joint error **0.00853 rad**.

This crosses one important factor absent from the old dataset: a +23° grasp yaw at positive world
y produced stable final-pose geometry. Compared with +22° to +24° high-contact trials at negative
world y, yaw alone is now a weaker explanation; workspace-dependent kinematics, hand-eye residual,
or finger geometry becomes more plausible.

The next safe datum should probe the `rightmost` red instance from a wider 0.45 m acquisition view,
aiming to sample the negative-y side without contact. Only after the probe matrix crosses yaw and
workspace should another descent be authorized.

### Wider-view semantic probe repeats the positive-y block

*Added by Codex.*

A no-contact `rightmost` red probe used a wider 0.45 m acquisition height. It selected
`[481.8,438.9]` and converged to approximately world `[0.468,+0.160]`, the same positive-y block
sampled by the preceding 0.34 m `leftmost` probe.

Pre-yaw alignment passed at 3.63 px. After +27° yaw, the final-pose correction sequence was
**6.93 → 9.88 → 9.60 px** against a 5.77 px tolerance, so the probe correctly returned
`safe_to_descend=false`. No descent or contact occurred. Episode
`8ce1819d-bd30-4cb9-aef1-f79d8a33787f` stores exact paired RGB-D and the complete correction
response. The arm was homed with maximum joint error **0.00859 rad**.

Inspection of the 0.45 m acquisition frame showed both tabletop red blocks: the unsampled block
near image `[130,350]` and the already sampled positive-y block near `[484,440]`. At the narrower
0.34 m view used previously, the `[130,350]` block was outside the field, so `leftmost` selected
the only visible tabletop red—the same `[484,440]` block. Semantic ordering was internally
correct; the field of view changed the candidate set.

The next cross-workspace datum is therefore `leftmost` at `acquire_z=0.45`. No implementation
change or MCP restart is needed. The different pass/fail correction outcomes on two observations
of the same positive-y block also quantify substantial frame-to-frame response variability,
which a future learned residual model must express as uncertainty rather than a deterministic
point correction.

### Negative-y no-contact probe passes; final mask alone does not separate contact

*Added by Codex.*

The 0.45 m `leftmost` probe selected `[126.3,355.2]` and converged to approximately world
`[0.356,-0.264]`, crossing into the negative-y region of the prior persistent-high-contact
episodes without descending.

The probe returned `safe_to_descend=true`:

- pre-yaw geometry correction improved **8.34 → 5.55 px** against a 5.71 px tolerance;
- selected grasp yaw was **+15°**;
- final-pose correction improved **11.43 → 9.25 → 2.87 px**;
- every depth-body observation passed with zero rejected frames; and
- exact initial, before-close, and final-pose RGB-D were stored in episode
  `ca7daea1-d9d7-4bad-a0a9-722ebabb4246`.

No contact or manipulation occurred. The arm was homed with maximum joint error **0.00610 rad**.

Offline color-mask PCA on exact final-pose frames measured:

| Episode class | Final red-mask angle | Pixel aspect |
|---|---:|---:|
| negative-y contact failure (+24° command) | 69.4° | 2.17 |
| positive-y probe pass (+23°) | 65.6° | 2.16 |
| positive-y probe refusal (+27°) | 65.1° | 2.12 |
| negative-y probe pass (+15°) | 77.0° | 2.12 |

The contact failure and positive-y probe pass have similar final mask orientation and aspect, so
those image features alone do not separate physical feasibility. More importantly, probe
`safe_to_descend` is a geometric authorization label, not a contact-ground-truth success label.
The dataset still contains only one tightly aligned negative-y physical contact result.

The next discriminating experiment is one guarded physical descent on the same semantic
negative-y/+15°-class target that just passed the no-contact probe. If it succeeds, orientation-
specific footprint geometry becomes more plausible; if it contacts high, the current geometric
authorization is broadly inadequate at that workspace. Target identity must be resolved at the
canonical acquisition pose exactly as in the probe.

### Sunday no-contact showcase stops on blue robot-self tracking

*Added by Codex.*

Doug requested a visually active arm demonstration for visiting guests. Because physical grasp
reliability remains under investigation, Codex chose a bounded no-contact showcase using only
`vision_grasp_probe` at speed factor 0.05 and `home`, with an explicit stop on identity or
perception anomalies.

The first wide-view red probe completed safely. It selected acquisition pixel `[133.4,350.5]`,
traversed the table, and converged near world `[0.399,+0.129]`. Pre-yaw geometry required three
observations (**7.35 → 5.78 → 0.35 px**); the final −6° pose independently passed at
**3.93 px** against a 5.71 px tolerance. No descent occurred. Episode
`adfe73b2-c964-48f6-aae8-e1741a287330` stores paired RGB-D and telemetry.

The second probe began on the largest blue tabletop candidate near `[574.6,364.1]`, but after
several coarse motions its track jumped to a blue-lit component of the robot/wrist housing at the
right image edge. The false body remained around `u=607–624`, `v=81–127` and reported implausible
69–78 mm local height. It never converged and never descended. Episode
`7b218c9d-d6ff-4bd6-9475-2a88b25000e3` is an explicit robot-self identity failure.

The declared stopping condition was honored after that second cycle. The arm was homed with
maximum joint error **0.00843 rad**.

The existing `v<60` wrist-label exclusion is too narrow and color-specific. A clutter-capable
system must exclude robot self using projected robot geometry and/or require continued tabletop
support consistency, rather than relying on a fixed image strip. Until that exists, unattended
multi-color showcase loops should not be run even in no-contact mode.

### Monday target-persistence gate: implementation and offline qualification

*Added by Codex.*

The next experiment tests whether a selected tabletop block remains the same physical object
through an eye-in-hand servo trajectory. The hypothesis is that combining the existing local
table-relief check with predicted image motion and block-height continuity will prevent target
switches to robot self or neighboring blocks without rejecting ordinary viewpoint variation.

The smallest implementation adds three constraints to the raised-depth reacquisition path:

- reject candidates more than 95 px from the pixel position predicted by the previous commanded
  image-Jacobian correction;
- after the first validated observation, require physical height to remain within 18 mm; and
- at the canonical acquisition view, exclude the narrow upper-right region occupied by visible
  wrist housing (explicitly a temporary approximation, not projected robot geometry).

When a servo step descends, its next expected pixel is the height-corrected grasp pixel at the new
camera distance. When it translates at constant height, the expectation interpolates the prior
centroid toward the commanded target using the actual servo gain. Failed identity checks cause
stationary bounded reacquisition and then a safe stop; they do not authorize motion toward a new
component.

Offline checks pass (**47 tests**). Saved RGB-D replay retained both real red targets at 30.1 mm
relief and the green target at 28.2 mm. Synthetic tests reject both a 70 mm robot-like substitute
for a 28 mm block and a candidate beyond the allowed track displacement. The live MCP process
must be restarted before the bounded no-contact color/instance matrix can test the hypothesis on
hardware.

The first live red-leftmost control trial stopped safely after one commanded translation. It
observed a valid 26.0 mm-high red body at `[146.7,328.0]`, then rejected all three stationary
reacquisition frames. No descent or contact occurred; the arm homed with maximum joint error
**0.00569 rad**.

This failure exposed a predictor error: the expected pixel used the full desired image correction,
while the actual Cartesian correction was capped at 60 mm. The gate therefore looked near an
unreachable future pixel and rejected the same block. The predictor now inverts the image
Jacobian using the Cartesian displacement actually commanded after both motion and workspace
clamps. A regression test demonstrates that a desired 200 px correction capped to 60 mm predicts
60 px rather than 200 px. Offline qualification now passes **48 tests**. A second MCP restart is
required before repeating the control trial.

After reloading, the repeated red-leftmost control retained the target for three observations
before safely stopping: `[160.0,311.7]` at 31.6 mm relief, `[224.8,323.1]` at 41.2 mm, and
`[218.5,340.4]` at 29.4 mm. It then rejected three stationary frames. No descent or contact
occurred; homing completed with maximum joint error **0.00592 rad**. The corrected clamp-aware
predictor therefore improved persistence but did not resolve it.

The gate is not loosened yet because the episode output did not distinguish a pixel-distance
rejection from a height-continuity rejection. Reacquisition telemetry now records the expected
pixel and height plus the best ungated raised-depth candidate (centroid, distance, and height) on
failure. This converts the next safe stop into a discriminating measurement. Tests remain **48
passing**; another MCP reload is required for the diagnostic trial.

The diagnostic trial expected the target at `[228.4,299.2]` after two validated observations
(37.0 and 39.6 mm relief). Three stationary captures rejected it; the best ungated alternative
was a different red body at `[581.7,297.6]`, **353.3 px** away and 52.1 mm high. The identity gate
therefore correctly refused to switch objects. No descent or contact occurred, and homing ended
with maximum joint error **0.00635 rad**.

An immediately saved RGB-D frame at the unchanged pose visibly contains the intended red block.
Offline application of the same raised-depth detector accepts it at `[223.3,305.5]`, about 8 px
from the prediction, with 31.3 mm relief and a 2,325 px body. This identifies a short RealSense/
segmentation dropout, not bad motion prediction or target absence. Coarse reacquisition is extended
from three to six stationary frames; neither the 95 px identity radius nor the 18 mm height
continuity threshold is weakened.

With six-frame stationary reacquisition, the red-leftmost control completed successfully through
15 coarse steps and final +11° wrist yaw. The final-pose guard passed at **3.32 px** against a
5.62 px clearance tolerance. The trajectory did require several transient retries (including five
on one acquisition-height step), confirming that the longer stationary window—not a looser
identity radius—was material. Red-rightmost then also passed in 11 steps, with a +5° final pose
passing at **4.02 px**. Both trials stored initial/before/final RGB-D; neither descended.

Green-leftmost exposed a separate acquisition issue. Semantic selection chose a green centroid at
`[119.5,467.7]`, clipped by the 480 px bottom boundary. Its partial local geometry reported 54.0 mm
relief; after one motion, the best nearby body was only 51.5 px from prediction but measured 31.3
mm, so height continuity correctly stopped the trial. No contact occurred; homing maximum error
was **0.00574 rad**. Canonical semantic selection now excludes centroids within 20 px of any frame
edge, because incomplete objects cannot supply trustworthy initial identity geometry. The height
tolerance remains unchanged.

After reload, green-leftmost selected the remaining in-frame candidate at `[505.7,267.7]`. After
one motion the depth body appeared at `[465.8,291.9]`, only **2.2 px** from its predicted location,
yet all six frames were rejected. The initial reported top height was 48.2 mm and the next best
body top was 31.2 mm—within the declared 18 mm continuity bound. Inspection found that the early
gate compared the prior unified body's 90th-percentile top height with the next color seed's median
local relief, while failure telemetry reported the unified top height. Identity was therefore
tested using unlike statistics. The continuity check now occurs after depth-body unification and
compares top height to top height. Thresholds remain unchanged; no contact occurred.

With consistent height statistics, green-leftmost retained the same 25–35 mm raised body for all
20 coarse observations. It did not identity-switch, but plateaued near 30 px at `z=0.315 m` and
was refused. The command-derived expected pixels show the controller had reached a workspace
constraint and could no longer produce the needed image-v correction. This is a reachability/
servo-stop-classification problem, separate from target persistence.

The decisive blue-largest regression selected the tabletop block at `[579.6,367.3]` and retained
it through 12 coarse steps. It never jumped to the blue-lit wrist housing seen in episode
`7b218c9d-d6ff-4bd6-9475-2a88b25000e3`. Coarse alignment converged, followed by a +6° grasp yaw.
The final jaw-clearance guard improved alignment but correctly refused descent at **7.22 px**
against a **5.53 px** tolerance. Initial, before-close, and final-pose RGB-D were stored under
frame episode directory `30657cb4-d8e7-46d1-9b09-5754ed5e8076`. No contact occurred. Final homing
maximum joint error was **0.00844 rad**.

Conclusion: predicted image motion plus consistent depth-body height is now demonstrably useful
for preserving identity and prevented the known blue robot-self switch. The live sample is not yet
a full 18-trial matrix: two red trials passed final authorization, green preserved identity but
was unreachable under the current servo/workspace policy, and blue preserved identity but failed
the independent final-clearance criterion. The next smallest improvement is explicit workspace-
saturation detection and recovery (for example, re-home/reacquire from a shifted overview pose),
not looser perception gates.

### Explicit workspace-saturation stop experiment

*Added by Codex.*

The green persistence trial remained roughly 30 px off target at `z=0.315 m` for many steps while
command-derived expected pixels showed little achievable motion. Before designing a recovery, the
substrate must distinguish this condition from perception loss and ordinary servo non-convergence.

The new detector labels a correction saturated when the requested Cartesian displacement is at
least 2 mm, the workspace-clipped command retains less than 25% of its norm, and image error is
still above the descent tolerance. Three consecutive saturated observations stop before issuing
the third ineffective motion. Episode history records requested and commanded XY displacement,
the saturation boolean, and its consecutive count. This is classification only: it does not
weaken workspace bounds, descend, or attempt an unvalidated recovery. Offline checks pass **50
tests**.

The first live classification trial retained green identity but again consumed all 20 steps. The
new telemetry nevertheless localized the issue: steps 7–8 and 18–19 were labeled saturated, while
intervening steps requested 20–46 mm primarily in X, commanded approximately 0–1 mm X, but retained
smaller Y motion. Vector-norm comparison incorrectly treated that secondary-axis motion as useful.
Saturation is now evaluated on the dominant requested axis. Under the recorded trajectory, steps
7, 8, and 9 form three consecutive dominant-axis saturations and should stop around step 9 on
replay. The arm made no contact and homed with maximum joint error **0.00851 rad**.

The repeated live probe confirmed the revised classifier. Green identity remained stable and the
primitive stopped explicitly after 11 observations, rather than exhausting 20. Its final three
dominant-X corrections requested −41.4, −19.2, and −39.8 mm while the clipped commands retained
approximately +0.09, +0.54, and −0.05 mm X. The outcome was `workspace saturation`, not generic
non-convergence. No third ineffective motion was issued, no contact occurred, and homing maximum
joint error was **0.00871 rad**.

Architecturally, recovery should not silently reselect an object inside the grasp primitive. The
primitive now emits a structured feasibility constraint (saturated axis/direction, requested and
commanded motion, EE position, image error, and observation count). A deliberate planner or an
explicit composite recovery skill can use that record to choose an alternate target or validated
view, preserving the distinction between foundation-model choice and reflexive execution. The
next thesis-relevant experiment is to retrieve this recent feasibility memory before repeating a
similar target selection, and demonstrate that it changes the deliberate action.

### Feasibility memory changes deliberate selection

*Added by Codex.*

Hypothesis: retrieving the recent green workspace-saturation episode before semantic selection can
prevent an identical infeasible attempt and cause the deliberate layer to choose another action.
This is intentionally opt-in via `feasibility_mode=avoid_saturation`; default probe behavior is
unchanged, and the primitive does not silently substitute another object.

Retrieval scans recent `vision_grasp_probe` episodes for the same color and acquisition height,
requires an explicit workspace-saturation outcome, and compares the stored canonical selected
pixel with current candidates. Candidates within 60 px are excluded. If none remain, the skill
abstains before servo motion and returns a trace containing retrieved episode IDs, avoided
candidates, pixel distances, and eligibility count. The foundation-model layer can then inspect
that evidence and deliberately select a different color, target, or view.

Offline tests construct matching and nonmatching episodes and demonstrate that retrieval is scoped
by color, canonical view, and failure class. A candidate near `[505,270]` retrieves the matching
green saturation at `[501,266]` and abstains without calling the grasp trajectory. Tests pass
**51 total**. Live success requires (1) the green call to abstain with the real saturation episode
ID and no servo motion, followed by (2) an explicit deliberate choice of a different feasible
block for a no-contact probe.

The live comparison met both criteria. Green abstention episode
`a3a51cda-5bbf-4822-a684-ee8903a37e1d` observed the current canonical candidate at
`[501.1,265.7]`, retrieved saturation episode `d91776dc-c189-4191-a29a-f3beba97ba68` at
`[501.3,266.1]`, and associated them at **0.4 px** distance. Eligibility was zero. The arm rose to
the canonical view but issued no servo trajectory.

Codex then deliberately changed color and instance rather than repeating green. Red-rightmost
episode `341ae0a9-7efe-4fb2-8647-e138a1ed6ad1` used the same feasibility mode, retrieved no red
saturation memories, retained three eligible candidates, and completed a 12-step no-contact probe.
Its final +6° pose passed the jaw-clearance guard at **3.78 px**. Exact initial, before-close, and
final-pose RGB-D were stored. No descent or contact occurred; final homing maximum joint error was
**0.00869 rad**.

This is the first direct evidence in this line of work that Hebbot's stored recent experience
changes a subsequent deliberate action: a prior failure was retrieved, prevented its own replay,
and caused selection of an alternative that passed. It is still hand-designed retrieval and
foundation-model-mediated choice, not learned policy improvement. The missing next link is to
record the abstention→alternative pair as a reusable recovery decision and test whether Hebbot can
replay that choice without Codex manually interpreting the trace.

### Explicit recovery record and replay skill

*Added by Codex.*

The next link is represented by `feasibility_recovery_probe`, a bounded no-contact skill rather
than hidden behavior inside the grasp primitive. It first invokes the requested target with
feasibility-memory avoidance. Only if that call explicitly abstains does recovery activate.

In `record` mode, the deliberate layer supplies a fallback color and instance. The episode stores
the requested action, triggering saturation episode IDs, fallback action, and fallback probe
result. In `replay` mode, the skill retrieves recent successful recovery episodes matching the
requested color/instance, selects the recorded fallback, and executes it without requiring Codex
to interpret the abstention trace. The routing decision is logged as `replay`, including candidates
and the source recovery episode. If no recovery exists, it abstains safely.

Offline tests demonstrate replay of green-leftmost → red-rightmost, preservation of the original
saturation trigger, explicit source episode attribution, and refusal to record without a deliberate
fallback. Tests pass **53 total**. The live protocol is one record trial followed by a separate
replay trial from home; success requires the second trial to select red-rightmost from the first
recovery episode and independently pass its no-contact clearance check.

The first live record call exposed an interface error before fallback motion:
`AttributeError: 'function' object has no attribute 'record'`. Classical skills receive
`_log_decision` as the already-bound callable `decision.record`, consistent with the existing
`vision_grasp` replay path; the new skill incorrectly treated it as the internal decision object.
The green primary performed only its canonical observation/abstention and no recovery servo ran.
The arm homed with maximum joint error **0.00850 rad**. The call site now invokes the injected
callback directly, and the failed episode remains part of the experiment record.

After reload, the first complete record attempt correctly paired the green abstention with a
red-rightmost fallback, but the fallback failed its independent final guard: **12.21 px** versus
5.69 px after +7° yaw. Episode `ae5797da-1895-458a-9271-1b28e3eeaaeb` is therefore not replayable.
A single bounded repeat succeeded. Record episode `b22d62fb-d950-4f83-9767-7bf5f5ec737e` stores
the same saturation trigger `d91776dc-c189-4191-a29a-f3beba97ba68`, explicit fallback
red-rightmost, and a passing final pose at **1.14 px** versus 5.69 px after +8° yaw.

The separate replay call supplied only requested green-leftmost; no fallback parameters were
provided by Codex. Episode `217c6362-9344-49ee-abb3-055646dd8120` retrieved the successful record
`b22d62fb-d950-4f83-9767-7bf5f5ec737e`, automatically selected red-rightmost, and passed at
**0.84 px** versus 5.66 px after +2° yaw. Its decisions-table row is `decision='replay'`, outcome
`success`, with reasoning `feasibility memory blocked green/leftmost; chose red/rightmost`, the
original saturation ID in relational state, and the record episode in candidates. Exact RGB-D was
stored for both successful fallback trajectories. No descent or contact occurred; final homing
maximum joint error was **0.00855 rad**.

This closes the intended missing link at a small experimental scale: deliberate recovery was
recorded once, retrieved later, and replayed successfully without foundation-model interpretation
of the second abstention. It remains symbolic/episodic replay, not policy distillation. The next
compression question is whether multiple recovery records can be generalized by relational state
rather than exact requested color/instance keys, and eventually distilled into a recovery policy.

### Cross-color relational recovery shadow probe

*Added by Codex.*

Exact recovery replay keys on requested color/instance. The next compression hypothesis is that
workspace infeasibility is primarily relational to canonical target region, not object color. A
new `relational_recovery_probe` therefore selects a current candidate at the same 0.45 m overview,
retrieves successful recovery records and their saturation triggers, and scores canonical pixel
distance with `similarity = exp(-0.5 * (distance / 80 px)^2)`. Trigger color is reported but not
used in the score. The default acceptance threshold is 0.80 (approximately a 53 px radius).

The skill defaults to `shadow_only=true`: it rises to the overview, observes and ranks recovery
candidates, logs the prediction and threshold, but does not servo toward either requested or
fallback objects. Execution is a separate explicit mode after evidence review. Offline tests show
a blue candidate near `[510,270]` retrieving a successful recovery whose green trigger is
`[500,266]`, while a blue candidate at `[300,300]` remains below threshold. Tests pass **54 total**.
The first live trial is intentionally negative on the current layout; a cross-color positive trial
will require a different-color block in the remembered green region, preferably moved manually
before any execution is authorized.

Live negative-control episode `b6dce89e-6594-4ca7-b34d-eae8afb8ac7d` selected blue-largest at
`[572.2,362.6]`. Both the successful recorded recovery and its successful replay resolve to the
same green saturation trigger `[501.3,266.1]`; each ranked at **119.7 px** distance and **0.3262**
similarity, below the 0.80 threshold. `predicted_recovery=false`; no requested-target servo and no
fallback execution occurred. The arm homed with maximum joint error **0.00837 rad**.

This validates the negative side of the relational gate on hardware. A discriminating positive
control requires changing object identity while holding spatial relation approximately fixed: move
a blue or red block to the current green block's canonical region, then run shadow mode again. The
block should be within roughly 53 px of `[501,266]`; swapping the physical positions of blue and
green is preferable because it preserves an explicit cross-color comparison without adding a new
object.

Doug manually swapped blue and green. Positive shadow episode
`387bc821-ac4e-42b4-a8ac-27f4cc44e518` then selected blue-largest at `[499.4,274.2]`, only
**8.3 px** from the remembered green trigger. Similarity was **0.9946**, above 0.80, and the selected
fallback was red-rightmost. The decision row remained `orchestrator` with shadow-only reasoning;
no requested-target servo or fallback execution occurred.

After homing and explicit authorization, execution episode
`9db9a097-1561-448f-8b59-838a85e1f2b4` independently observed blue at `[499.5,274.1]` and scored
the green trigger **0.9948** at 8.2 px. It replayed red-rightmost, never servoed toward blue, and
passed the final +9° clearance pose at **2.07 px** versus 5.63 px. Its decision row is
`decision='replay'`, outcome `success`, reasoning `relational recovery 0.995 >= 0.800; replaying
red/rightmost`. Exact fallback RGB-D was stored. No descent or contact occurred; final homing
maximum joint error was **0.00877 rad**.

Together, the before/after swap controls isolate spatial relation from color: the same blue request
scored 0.3262 when 119.7 px away and 0.9946–0.9948 after being moved 8 px from the green trigger.
This is genuine one-step generalization beyond the exact symbolic key. It is still a hand-designed
Gaussian representation and threshold. The next scientific question is whether the metric can be
learned/calibrated from several saturation and success boundaries, rather than chosen by Codex.

### First calibrated feasibility candidate

*Added by Codex.*

The first deliberately tiny learned feasibility model uses only canonical overview target pixel
coordinates (`u/640`, `v/480`) and logistic regression with L2 regularization. Training labels are
conservative: successful probes with `safe_to_descend=true` are feasible; explicit workspace
saturation and the immediately preceding repeated-saturation precursor are workspace-limited.
Perception failures, identity rejections, clearance refusals, and generic nonconvergence are
excluded because treating them as workspace labels would teach the model historical software bugs.

Validation groups observations within 35 px and holds out whole spatial groups rather than random
rows. This prevents repeated trials at one block location from appearing in both train and test
sets. Promotion requires at least 8 samples, two spatial groups of each class, and grouped
leave-one-location-out Brier score no worse than 0.25. The implementation and refusal behavior pass
the full **56-test** suite.

Training against the real episode store produced artifact
`feasibility_ab8e01b5564f.json`: **10 defensible examples**, comprising 8 feasible observations in
two spatial groups and 2 workspace-limited observations in only one spatial group. The grouped
Brier score was **0.1434** over the eight evaluable held-out feasible observations. The candidate
was correctly **not promoted** because the positive class has only one independent location; the
limited group itself cannot yet be held out while retaining both classes for training.

For orientation only, the unpromoted candidate assigns workspace-limited probabilities 0.320 at
the known saturation trigger `[501.3,266.1]`, 0.312 at the old blue location `[572.2,362.6]`,
0.127 at the feasible-left region `[130,352]`, and 0.243 at feasible-mid `[452,375]`. The ranking
contains some signal, but its broad similarity between the known limited region and old blue
location demonstrates why deployment would be premature. The next useful data are not more repeats
at `[501,266]`; they are independently labeled boundary probes at additional spatial locations,
including both sides of the reachability transition. This is an instance where the learning loop's
promotion gate prevented apparent in-sample progress from being mistaken for generalization.

A first boundary-oriented live probe targeted green-largest at canonical `[575.1,371.4]`, near the
former blue region where the unpromoted model predicted 0.312 workspace limitation. Episode
`2ef4eb20-40c2-47e3-81c6-565e84fc7be0` ran at speed factor 0.03 with an eight-step cap and no
descent or contact. It reduced pixel error from 265.9 px to 20.4 px at `z=0.405 m`, encountered one
transient workspace clamp on step 5, recovered on step 6, and then stopped as nonconverged. This is
neither a positive nor negative feasibility label: repeated saturation was not established and the
safe final pose was not reached. The failure and aligned RGB-D remain stored as episode data.

Retraining after the attempt again extracted exactly 10 examples with identical weights and
metrics, confirming that the ambiguous failure was excluded rather than mislabeled. The arm was
homed with maximum joint error **0.00876 rad**. A follow-up at this same location may use a larger
bounded step allowance to resolve its label, but even a conclusive positive there would be more
than 35 px from `[501,266]` and therefore supply the independent workspace-limited spatial group
needed for the first honest promotion test.

A single follow-up at the same green-largest location increased the bound to 14 steps, retaining
speed factor 0.03 and the no-contact probe. Episode
`6c72ab1e-c40a-462c-8ffa-a37760839d6a` reached **4.0 px** error at `z=0.360 m` with no workspace
saturation, then lost alignment during the lower-height phase: 28.1 px at 0.315 m and 34.3 px at
0.270 m. It stopped nonconverged, without descent authorization or contact. This strongly suggests
the location is kinematically reachable while exposing low-height visual-servo instability; it is
still not a defensible final-feasibility label under the conservative definition. Retraining again
extracted exactly 10 examples, confirming exclusion. The arm homed with maximum joint error
**0.00867 rad**.

A separate boundary sample targeted green-leftmost at canonical `[301.3,443.1]`, more than 35 px
from every previous feasible group. Episode `f1e2cb17-717b-4b9e-9daf-023d3383ff74` ran at speed
factor 0.03 with a 16-step cap. It reached every observation height without workspace saturation,
completed the final no-contact clearance pose with **1.14 px** alignment versus a 4.0 px tolerance,
and returned `safe_to_descend=true`; no grasp, descent, or table contact was commanded. Exact
initial, before-close, and final-pose aligned RGB-D were stored. The arm homed with maximum joint
error **0.00555 rad** and was verified nominal.

Retraining after this conclusive sample produced artifact `feasibility_afa6fbf4746a.json` from
**11 defensible examples**: 9 feasible in three independent spatial groups and 2 workspace-limited
in one group. Grouped leave-one-location-out Brier improved from 0.1434 to **0.0886** across nine
held-out feasible observations. Promotion still correctly refused because the positive class has
only one independent spatial region. This sample improves the reachable side of the boundary but
does not substitute for a second genuinely saturated location.

Scientific consequence: collecting reachability labels and improving final descent stability are
now separable tasks. The calibrated workspace model still needs another independently saturated
canonical location. The green trial should instead inform a future phase-conditional execution
model or controller improvement, since collapsing its low-height servo failure into a workspace
label would confound perception/control quality with geometric reachability.

## 2026-07-28 — Codex literature note: Waddle, CaP-X, and ASPIRE

*Added by Codex. Previous notebook entries may have been written by Claude; Claude may return to
review, comment on, and contribute to this work.*

Waddle Labs' “Introducing Waddle: agents that control robots” describes an architecture in the
same immediate research neighborhood as Hebbot. Waddle uses a foundation-model coding agent to
decompose tasks, inspect camera observations, write and revise robot programs, and call action
models where useful. Its hierarchy consists of a fixed platform vocabulary of perception and
control primitives, agent-created/refined parameterized skills, and task programs composed from
those skills. It explicitly argues that long-horizon planning should remain with the reasoning
model rather than being buried in an end-to-end action policy.

Sources reviewed:

- Waddle Labs, “Introducing Waddle: agents that control robots,” July 2026:
  <https://www.waddlelabs.ai/research/introducing-waddle>
- Fu et al., “CaP-X: A Framework for Benchmarking and Improving Coding Agents for Robot
  Manipulation,” 2026: <https://arxiv.org/abs/2603.22435>
- Lu et al., “ASPIRE: Agentic Skills Discovery for Robotics,” 2026:
  <https://arxiv.org/abs/2607.00272>

### What their work validates

The independent convergence is meaningful. Waddle's primitive → skill → program hierarchy closely
matches Hebbot's hardware/classical primitives → stable skill contracts with replaceable bodies →
foundation-model composition. Its use of execution feedback, intermediate verification, autonomous
repair, and a persistent shared skill library supports Hebbot's separation of deliberate cognition,
substrate skill records, reflexive execution, and hardware processes. ASPIRE strengthens the case:
it treats fine-grained multimodal execution traces as the raw material for failure diagnosis,
repair synthesis, validation, and reusable skill discovery. Hebbot's decision to preserve failed
attempts, aligned RGB-D, robot state, controller histories, retrieval decisions, surprises, and
independent verification is therefore well aligned with the emerging code-as-policy literature.

CaP-X supplies an important warning and an evaluation pattern. Its results show that coding-agent
performance improves substantially with human-designed abstractions and degrades as that
scaffolding is removed. Hebbot must therefore measure whether gains come from memory/learning or
from progressively better hand-written primitives. A stable benchmark with ablations is necessary
to avoid mistaking substrate engineering for autonomous improvement.

### The important distinction from Hebbot's thesis

Waddle publicly describes two improvement channels. In one, solved tasks yield validated programs
and parameterized skills that enter a shared library, with no retraining between tasks. In the
other, an agent reportedly generated roughly 1,000 randomized LEGO pick-and-place rollouts and
then trained an ACT policy from scratch. The public material does not yet demonstrate an automatic
continuum between ordinary task episodes, immediate memory retrieval, adapted replay, and neural
distillation.

Hebbot's intended contribution remains stronger and more explicit: one structured episode store
feeds both immediate deliberate-layer retrieval and periodic reflexive-layer distillation, with a
measured progression from deliberate script to reusable repair, trajectory replay/adaptation, and
learned policy. The transition itself—not merely a code library at one endpoint and batch policy
training at the other—is the scientific object. Later execution should become measurably cheaper,
faster, or more reliable because prior experience changed the execution path, while the deliberate
layer retains override and demotion authority.

### Architectural lessons to adopt

1. Treat a validated code repair as an intermediate form of compression. Record which failures
   motivated it, candidate repairs attempted, evaluation episodes, the promoted skill version, and
   its applicability conditions. This sits between one-off deliberate reasoning and trajectory or
   neural distillation.
2. Generalize the current model gate into a common lifecycle for every candidate body:
   `candidate → shadow → bounded live evaluation → promoted → monitored → demoted`. The same
   provenance and domain-bound rules should cover code repairs, trajectories, classifiers,
   regressors, and neural policies.
3. Build a small stable Hebbot benchmark spanning isolated pickup, duplicate-color identity,
   modest clutter, infeasible-target avoidance, recovery choice, and relational placement. Compare
   no-memory deliberation, retrieval-assisted deliberation, replay/adaptation, and learned reflexes.
   Measure task success, unsafe contacts, latency, foundation-model cost, servo steps, retries, and
   interventions.
4. Store intervention provenance explicitly: autonomous success/failure, foundation-model repair,
   human instruction, human physical scene change, and safety intervention supervise different
   parts of the system and must not be collapsed into one label.
5. Use the deliberate layer to design bounded, diversity-aware collection curricula, then let a
   cheaper executor gather the batch. Active boundary sampling for the feasibility model is the
   immediate small-scale example; randomized manipulation rollouts can follow once resets and
   verification are sufficiently reliable.
6. Prefer relational transfer over task-name reuse. The cross-color recovery result already shows
   the right pattern: a recovery first associated with green transferred to blue because canonical
   target region, not color identity, was causally relevant.

Multi-agent robot coordination is not a present priority despite Waddle's emphasis on it. A single
Panda with deeply instrumented, scientifically attributable learning is more valuable at this
stage. Likewise, Waddle's thousand-rollout example argues for autonomous clean data collection,
not for prematurely increasing model capacity on Hebbot's current tiny datasets.

The near-term boundary-feasibility experiment should therefore be viewed as the first complete
instance of a generic Hebbot “skill compiler”: episodes and intervention traces → defensible
failure/pattern clusters → a candidate code, trajectory, or model improvement → grouped offline
validation → shadow execution → bounded live testing → promotion with explicit domain limits →
monitoring and possible demotion. Successfully closing that loop would differentiate Hebbot from
mere scripted automation and from a skill library that grows without reflexive compression.

## 2026-07-28 — Codex experiment: geometric capability map versus servo envelope

*Added by Codex.*

The feasibility experiment had been treating repeated visual-servo clipping as evidence about the
Panda's reachable workspace. Doug proposed mapping the physical limits directly, without requiring
blocks. A new arm-free `geometric_capability_map_v1` sampler now queries the resident cuRobo service
for gripper-down endpoint IK and collision-free paths over an XY grid. It preserves separate fields
for `ik_reachable`, `path_found`, positional error, distance from the seed configuration, and the
candidate joint state. The artifact explicitly says that cuRobo currently models robot
self-collision and joint limits but **does not yet include the table or blocks**; it must not be
interpreted as a complete scene-collision map. The panda_py EE target is converted to cuRobo's
`panda_hand` frame using the existing calibrated 0.103 m tool-axis offset.

Offline tests verify the frame conversion and that endpoint IK, path availability, and failed
requests remain distinct evidence. The full suite passes **58 tests**. At EE `z=0.20 m`, all
**153/153** points in the conservative teleop box (`x=0.25..0.65`, `y=-0.40..0.40`, 5 cm grid)
had collision-aware endpoint IK. An expanded no-motion grid (`x=0.15..0.80`,
`y=-0.60..0.60`) found IK for **286/350** points and 31 grid-adjacent boundary candidates. Nine
reachable endpoints had reported IK error above 1 mm and 14 were more than 2.5 rad from the home
seed; those quality signals remain recorded rather than being collapsed into a boolean. A focused
set of **15** poses spanning the visual-servo boundary all had both IK and a cuRobo path from home.

The complete artifacts are stored outside the repository with the episode data:

- `capability_maps/2026-07-28_curobo_ik_z020.json`
- `capability_maps/2026-07-28_curobo_paths_servo_boundary_z020.json`

The source of the earlier “workspace saturation” is now clear in code: `vision_grasp` clips every
servo target to `x∈[0.30,0.60]`, `y∈[-0.28,0.28]`, then declares saturation when clipping removes
most of the dominant correction for three consecutive observations. This is a deliberately
conservative **execution envelope**, not the Panda's physical reach radius.

One live no-contact validation tested `[x=0.45, y=+0.30]`, 2 cm beyond the servo's positive-y
limit. The predeclared sequence first moved to `z=0.30 m`, inspected state and aligned depth, then
moved to `z=0.20 m` only after confirming clearance. Both calls used speed factor 0.03 and reported
`ik_source='curobo'`. The high checkpoint reached within **3.33 mm**, with 0.305 m center depth and
86.2% depth coverage. The lower pose reached within **1.44 mm**, with 0.208 m center depth and 89.0%
coverage. State and force remained nominal; there was no descent to contact, grasp, or object
motion. The arm homed with maximum joint error **0.00886 rad**.

This falsifies the simple interpretation that the remembered `[501,266]` saturation represents a
physical reach boundary. It is evidence that the current visual-servo policy cannot pursue that
image correction while constrained to its safety box. Geometric reachability, path feasibility,
live pose attainment, visual-servo convergence, and final-pose authorization must remain separate
labels. The tiny pixel classifier should not be promoted as a model of physical reach, even after
collecting another clipped region; at most it models the current controller envelope. The next
design decision is whether to (a) retain it explicitly as an execution-envelope predictor, or
(b) replace raw canonical pixels with geometric and phase-conditional features, using cuRobo as a
prior and real episodes to learn perception/controller residual risk.

### Orthogonal outcome axes and corrected learner semantics

*Added by Codex.*

The architectural correction is now implemented. Every new `vision_grasp_probe` result includes
an `outcome_axes` record with independently nullable fields:

- `geometric_reachable`
- `planned_path_found`
- `live_pose_reached`
- `controller_envelope_clipped`
- `controller_envelope_limited`
- `visual_servo_converged`
- `final_pose_authorized`

The rule is deliberately epistemic: absence of evidence remains `null`, not failure. A final-pose
authorization establishes live pose attainment, servo convergence, and lack of controller-envelope
limitation for that execution, but it does not retroactively claim that a separate offline IK or
path query occurred. Conversely, three clipped corrections establish controller-envelope
limitation and failed servo convergence, but say nothing about geometric reachability or path
existence. Perception rejection and other ambiguous failures leave unmeasured axes unknown.

New servo histories write `controller_envelope_clipped` and
`controller_envelope_clipped_count`; failure results write
`controller_envelope_saturation`. The old `workspace_saturated`, count, and saturation fields are
retained as compatibility aliases so historical episodes and retrieval continue to work. The MCP
parameter `feasibility_mode='avoid_saturation'` is likewise retained, but its description now says
that it recalls controller-envelope clipping episodes rather than physical workspace limits.

The original trainer module remains source-compatible but now produces
`controller_envelope_logistic_v1` artifacts and uses `authorized` versus
`controller_envelope_limited` class names. Reprocessing the real store produced
`controller_envelope_cd5e2bb85304.json`: the same 11 source episodes, weights, and grouped Brier
**0.0886**, now correctly described as 9 authorized observations in three spatial groups versus 2
controller-limited observations in one group. It remains unpromoted because one limited group is
insufficient. Numerical identity with the earlier candidate is expected; this change corrects the
scientific interpretation and data contract rather than manufacturing a new empirical result.

The suite passes **61 tests**, including explicit checks that saturation is controller evidence
and not geometric failure, ambiguous outcomes remain unknown, successful probes populate only
supported axes, and the live probe wrapper emits the normalized record. No robot motion was needed
for this semantic/data-contract change. The next learned component should predict residual
perception/controller risk conditioned on separately queried geometric/path evidence, rather than
attempting to rediscover robot kinematics from canonical pixels.

### First live orthogonal-outcome probe

*Added by Codex.*

After MCP restart, `vision_grasp_probe` registered as v23 and a live no-contact trial revisited the
unresolved green-largest region. Episode `36482b0d-75cb-4bc8-b374-c76ce4cdc180` selected canonical
target `[581.6,370.5]`, ran at speed factor 0.03 with a 20-step bound, and stored aligned RGB-D. The
coarse controller progressed through every height without a single controller-envelope clip. It
reached **3.5 px** error at `z=0.20 m`, below the coarse threshold. Failure occurred in the
subsequent depth top-face alignment: four independently perceived corrections produced 4.59,
4.25, 5.35, and **4.82 px** error against the physically derived 4.0 px jaw-clearance tolerance.
The guard refused descent; there was no grasp or contact. Post-trial depth coverage was 80.5% with
0.328 m center depth. The arm homed with maximum joint error **0.00885 rad**.

The first normalized live output correctly reported no envelope clipping and did not claim
geometric failure, but its aggregate `visual_servo_converged` remained unknown. This exposed a
second conflation: coarse image servo, depth top-face alignment, and final yawed-pose alignment are
separate execution phases. The schema now additionally records
`coarse_visual_servo_converged`, `depth_alignment_converged`, and
`final_pose_alignment_converged`. Offline reinterpretation of the stored episode yields,
respectively, **true**, **false**, and **null**, with aggregate visual-servo convergence false and
final-pose authorization false. Geometric reachability, path availability, and live pose
attainment remain null because this particular episode did not independently log those queries.

The suite passes **62 tests**. This is a useful failure: the learned residual-risk target can now
be phase-specific rather than asking one classifier to predict an undifferentiated “feasibility.”
The repeated green-largest evidence points specifically at a tight/noisy depth-alignment gate near
this view, not at the robot's reach, the servo safety envelope, or coarse centering.

### Depth-alignment batch: upstream censoring and adaptive stop

*Added by Codex.*

The depth-alignment controller was parameterized without weakening the 4 px jaw-clearance guard:
recenter gain 0.1–1.0, one to five stationary RGB-D observations per pass (median aggregation), and
two to eight alignment passes. Defaults preserve the previous behavior (gain 1.0, one sample, four
passes). Each alignment-history entry records the selected settings plus accepted and rejected
follow-up frames. Offline median-aggregation and parameter-forwarding tests increased the suite to
**63 tests**.

A planned 16-trial green-largest no-contact matrix began with interleaved configurations, automatic
homing, and a batch-level rule to stop if the coarse stage repeatedly censored the depth experiment.
The first three episodes all reached `z=0.20 m` without controller-envelope clipping or contact but
exhausted the 20-step coarse budget before the 4 px transition criterion:

- configuration A, gain 0.35 / 1 sample / 4 passes, speed 0.05: **7.9 px** final error;
- configuration B, gain 0.50 / 3 samples / 4 passes, speed 0.05: **6.6 px**;
- configuration C, gain 0.35 / 3 samples / 6 passes, speed 0.03: **7.5 px**.

Because none entered the parameterized depth-alignment phase, attributing these outcomes to A/B/C
would be invalid. The batch stopped after 3/3 upstream censorings rather than collecting 13 more
irrelevant failures. The arm homed after every attempt; final homing maximum error was **0.00864
rad**. The MCP schema now permits up to 30 coarse steps while retaining the same 4 px criterion.
The resumed batch should first demonstrate that this larger bounded budget produces depth-stage
entries reliably; only then should the depth parameter comparison continue.

After restart, a gating trial used the 30-step budget with configuration D (gain 0.20, three
samples, six passes) at speed 0.03. It still stopped in coarse servo at **12.8 px** after repeatedly
occupying a 5.5–10 px close-view noise band. Earlier in the same trajectory it had reached 1.9 px
at `z=0.315 m` and 3.0 px at `z=0.270 m`; there was no clipping or contact. This showed that merely
waiting longer does not reliably cross the instantaneous 4 px gate.

The pipeline contained a redundant requirement: coarse servo had to meet the final physical 4 px
jaw-clearance tolerance before invoking the depth top-face alignment stage whose purpose is to
achieve that precision. A separate `coarse_handoff_tol` now defaults to **12 px** (bounded between
the requested final tolerance and 22 px). At the bottom height, coarse servo may hand this bounded
residual to depth alignment; depth alignment and the independent final yawed-pose guard still
enforce the unchanged physically derived 4 px criterion before descent. This is a phase-interface
correction, not a relaxed safety gate.

Doug observed that the arm remained roughly 20 cm above the block while making many fine
corrections and proposed the more natural policy: look, move closer, look and correct again, then
move closer again. The probe now separates a stable mid-view geometry estimate from a close
precision view. After coarse acquisition, it estimates width/yaw/top face at the configured
geometry height, descends without contact to `precision_z` (default **0.15 m EE-z**), reacquires the
same raised depth body with stationary RGB-D samples, and only then performs bounded fine
corrections. Metric width/yaw remain anchored to the stable farther observation; the closer view
updates centroid and top height. The 4 px depth and final-pose guards remain unchanged. The first
live trial should use `approach_z=0.30 m`, making the visible schedule approximately
0.45 m overview → 0.30 m geometry/correction → 0.15 m precision/correction, with no grasp descent.

### Staged trials reveal fixed-height correction anti-pattern

*Added by Codex.*

The first staged green-largest trial was rejected after one lateral move: a same-colour candidate
remained only **6.7 px** from prediction, but its inferred height changed from **55.8 mm to 25.8
mm**. Because the initial target was at the extreme image edge, this is likely an edge-biased first
depth estimate; importantly, the failed attempt was retained rather than silently switching bodies.

A specifically selected, better-centred red block completed coarse alignment without envelope
clipping. At the 0.15 m precision view, eight three-frame median observations yielded top-face
errors of **56.5, 67.4, 41.0, 32.2, 24.9, 16.9, 13.1, and 10.9 px**. The final six improved
monotonically and height stabilized around **29–31 mm**, but the 4 px authorization prevented
contact. At 0.308 m block depth, 10.9 px is roughly **8.5 mm** lateral residual.

Direct observation exposed a more important controller mistake: all eight precision corrections
occurred at essentially the same Z. This contradicted the intended look → approach → correct →
approach policy and spent motion on diminishing fixed-view refinements. Precision alignment is now
a descending staircase from geometry height to `precision_z`: one observation and at most one
combined X/Y correction per distinct Z, followed immediately by descent to the next view. The last
close observation authorizes or refuses contact without another same-height correction. The real
`vision_grasp` MCP schema also exposes these staged controls. Offline tests remain **63/63 passing**.

Two staircase probes then used the same centred red block. With gain 0.7, the eight distinct Z
levels (`0.3000` through `0.1500 m`) ended at **32.9 px**; with gain 1.0 they ended at **28.1 px**.
Both avoided clipping and maintained three accepted depth observations per level. The errors stayed
small through the upper staircase (gain 1.0 reached **4.17 px at z=0.2357 m**) but grew as the
camera/tool parallax increased below approximately 0.21 m. Inspection exposed a sequencing error:
each logged value was the *pre-correction* error, and the final level refused contact without ever
using the one X/Y correction allowed at that Z. The controller now applies at most that one final
correction, takes a verification observation at the same pose, and cannot correct again there.
Higher levels descend after their observation even when already within tolerance, ensuring that
contact authorization comes from the closest view. Offline tests remain **63/63 passing**.

The corrected 0.15 m probe used its final permitted correction and improved **31.22 px → 12.88
px** in one move. This confirmed the sequencing fix, while also locating a practical perception
boundary: below roughly **0.21 m EE-z**, wrist-camera/tool parallax model error grows faster than
the staircase corrects. Rather than repeat fixed-height adjustments, the live contact trial ended
its four-view staircase at **0.2357 m**, inside the reliable region.

One initial real-grasp attempt was invalid because home-view target coordinates were supplied while
the arm was still at the close probe pose; the aligned-depth gate rejected the nearest candidate at
**99.9 px** rather than switching targets. After homing and reacquiring the red target, the next
recorded attempt succeeded. Its final staircase correction improved **6.69 px → 2.46 px**; the
independent yawed-pose check improved **7.36 px → 2.42 px**. Compliant descent contacted at actual
EE **z=0.02796 m**, closed to **28.76 mm**, lifted the block, and passed both proprioceptive and
task verification (`source_vacant`) with zero air retries and no persistent-high-contact event.
RGB plus aligned depth were stored at initial, before-close, final-pose, after-lift, and an additional
held-state observation. This is the first successful contact test of the one-correction-per-Z
staircase policy and supports using approximately 0.236 m—not the closest possible view—as the
current calibrated handoff height. The arm subsequently homed normally (maximum joint error
**0.00844 rad**).

### Volume-collection shakedown stops on yaw observability

*Added by Codex.*

A planned 12-episode interleaved record/replay shakedown used the calibrated staircase policy
(`acquire_z=0.45`, geometry `z=0.30`, precision `z=0.2357`, gain 1.0, three RGB-D samples, four
levels, 4 px authorization, speed 0.05). The batch-level stop rule was three total failures or two
consecutive physical failures. It stopped after four episodes with **1 verified grasp and 3 safe
pre-contact refusals**:

- green, record-only: verified grasp/lift, zero air retries, no persistent high contact;
- red, replay condition: staircase reached **2.79 px**, but a near-quarter-turn grasp yaw
  (`-88°`) moved the target out of the final wrist view; best memory similarity was only 0.560, so
  replay correctly did not activate;
- small blue, record-only: target lost after the first large acquisition translation;
- green, replay condition: staircase reached **1.31 px**, but `+65°` grasp yaw again moved the
  target out of the final wrist view; best similarity was 0.622, so replay did not activate.

Inspection of the two stored final-pose RGB frames showed that this was not merely an overly strict
segmentation threshold: after the large wrist rotations, the intended red/green targets were
physically outside the camera FOV while other blocks and the table remained clearly visible. The
current pipeline performs the entire grasp-axis yaw only after completing its translational
staircase. That is adequate for small yaws (the successful red and green grasps used approximately
`-10°` and `+3°`) but not for arbitrary block orientation. Serious collection can proceed now in a
declared **low-yaw domain**, using target selection that predicts and limits absolute grasp yaw,
while general-orientation collection requires interleaving incremental yaw with the descending
observe/correct staircase so the eye-in-hand target stays visible. Blindly continuing the original
12-run batch would primarily collect duplicate observability refusals, so the stop rule worked as
intended.

The controller now implements that interleaving. From the stable depth principal axis it computes
the shortest gripper-symmetric grasp yaw, then distributes it linearly across the same four
descending alignment levels. A representative `+65°` grasp therefore observes/corrects at about
`0°, +21.7°, +43.3°, +65°` while descending `0.3000 → 0.2786 → 0.2571 → 0.2357 m`; a `-88°`
grasp uses the corresponding negative sequence. Every lateral correction is transformed by the
current partial wrist yaw, preserving the one-X/Y-correction-per-Z rule. The final pose reaches the
full geometric yaw before its last verification, eliminating the single large blind rotation that
moved targets out of frame. A sufficiently similar replay memory may still specialize the final
yaw within existing bounds; otherwise the distributed geometric yaw is retained. New unit coverage
checks the symmetric yaw mapping for the orientations observed live. The offline suite is now
**64/64 passing**. Live resumption should begin with the previously failing `+65°` green target,
because it is a direct falsification test of the proposed observability fix.

The first live distributed-yaw falsification used four levels on the same rotated green target
(measured full yaw about `+68.6°`). It retained the target through `+45.8°`, substantially farther
than the old single final rotation, but the final 22.9° jump lost reacquisition. An eight-level
repeat reduced increments to roughly 9.5°. That trial retained observations through the entire
`+66.2°` rotation, proving that distributed yaw fixes the gross FOV-loss problem, but exposed a
second error: lateral residual grew systematically from **6.9 px at +9.5°** to **42.7 px at
+56.8°**, and the final correction moved the centroid to **113.6 px** error. Thus the current
positive-yaw correction transform was directionally wrong rather than merely under-sampled.

The calibrated zero-yaw image Jacobian had been rotated by positive tool yaw. Live data show that
the commanded wrist quaternion rotates the eye-in-hand image axes with the opposite sign relative
to that convention. Yawed image-servo corrections now use the **inverse tool yaw**; this applies
both during the descending yaw staircase and during independent final-pose correction. A focused
unit test fixes the sign convention at a 90° example, and the offline suite is **65/65 passing**.
The next live attempt should retain eight levels and require the lateral-error trend to shrink,
not merely keep the target visible, before authorizing contact.

The inverse-yaw live retry falsified that sign-only repair as well: it preserved the target but
drove the centroid in the opposite image direction, reaching **204.2 px** before the final
correction and **187.4 px** afterward. The deeper mistake was treating the canonical zero-yaw
grasp pixel as invariant under wrist rotation. Because the D405 is offset from the EE rotation
axis, rotating the rigid eye-in-hand assembly changes the correct image location even when the EE
and block remain perfectly aligned in world XY. Neither sign of a simple planar rotation can force
the target back to the zero-yaw pixel without introducing physical error.

The revised controller separates the invariants. It first completes the proven translational
staircase at zero yaw. It then holds the verified EE X/Y fixed and rotates to the grasp axis in
increments bounded to **10°**, reacquiring the same raised depth body and checking measured XY
drift after every increment. No X/Y corrections occur during this yaw-only tail. For substantial
yaw (≥15°), final authorization now requires continuous object identity/height and ≤**4 mm** EE XY
drift rather than comparison with an invalid canonical pixel. Small yaws retain the calibrated
pixel guard and correction path. This is explicitly a kinematic-hold verification, not a claim that
the yaw-dependent camera projection has been calibrated. Unit coverage fixes the increment bounds
(65°→7 steps, −88°→9), and the offline suite is again **65/65 passing**. The direct live test remains
the rotated green target; success requires continuous visibility through all increments followed by
contact and verified lift.

The direct live test validated the revised yaw invariant through perception. The rotated green
target remained depth-validated through **seven** increments from `+8.7°` to `+60.7°`; measured EE
XY drift increased only from **0.55 mm to 0.73 mm**, far below the 4 mm guard. The controller
therefore authorized real descent. Contact occurred, but one fingertip stopped on the block top at
actual EE **z=0.04766 m**, **21.7 mm** above the intended body grip. The controller detected
`persistent_high_contact`, did not claim a grasp, and retained the complete failed episode.

This localizes the next constraint to jaw approach clearance. The approximately 29.8 mm-wide block
was approached with only 41.8 mm opening—about **6 mm nominal clearance per side**, appropriate
after a calibrated small-yaw pixel check but too narrow while large-yaw hand-eye translation remains
kinematically rather than visually calibrated. Substantial-yaw (≥15°) approaches now request object
width + **30 mm** total clearance (about 15 mm per side), bounded by the existing **65 mm** clutter
cap rather than fully opening to 80 mm. Small-yaw behavior is unchanged. Focused unit coverage checks
the threshold and cap; the offline suite is **66/66 passing**. The same green contact is the next
trial, and success requires descending past the prior 47.7 mm top-contact height and producing a
verified lift.

The first widened-clearance retry was censored before yaw: four-level zero-yaw verification ended
at **7.89 px**, so no contact occurred. An eight-level translational retry then converged to **2.09
px** and entered incremental yaw. The target remained visible through `+59.9°` with only **0.58 mm**
XY drift, but the geometry requested `+69.9°`; that final increment moved the target outside the
wrist FOV. The widened jaws again never reached contact. Combined with the prior successful
continuous observation through `+60.7°`, this establishes an empirical camera visibility boundary
near **±60°** at the current pose.

Geometric and replay yaw are now clipped to ±60° and the outcome records both requested and applied
yaw. This accepts bounded axis mismatch in exchange for continuous observability. For the current
34 mm-wide, 66 mm-long block, the approximately 10° residual mismatch produces a projected jaw
span still comfortably inside the roughly 64 mm widened opening. Unit coverage fixes both positive
and negative clipping behavior; the offline suite is **67/67 passing**. The next retry should use
eight translational levels, terminate yaw at +60°, and finally exercise the widened-clearance
contact hypothesis.

Subsequent live trials clarified the remaining limits. With the corrected zero-yaw centering and
yaw-only tail, one `+60.7°` green attempt retained the target through all seven increments with
**0.73 mm** maximum XY drift and authorized contact. It nevertheless stopped a fingertip at
**z=0.04766 m** (`persistent_high_contact`, 21.7 mm high), motivating the bounded wider opening.
The first widened retry was censored at **7.89 px** before yaw. An eight-level retry reached **2.09
px**, remained visible through `+59.9°`, then lost the target only when requesting `+69.9°`; this
established and motivated the ±60° visibility cap. The next capped trial was again censored by
zero-yaw alignment (**13.26 px** after a worsening final correction), so it never exercised the
wider jaws.

The collection resumed in the declared low-yaw domain, but the scene's remaining eligible-looking
targets were at workspace/image boundaries. Episode 6 selected a blue block near the right side;
coarse servo recorded controller clipping and the depth-alignment centroid diverged to **44.62 px**.
No yaw or contact occurred. At this point the batch stop rule was invoked: continuing to episode 12
would primarily sample known target-selection/pathology regimes rather than manipulation
repeatability.

The architectural conclusion is that a volume runner must make **eligibility** an explicit recorded
decision before grasp execution: minimum component/depth-body area, image-edge margin, predicted
controller-envelope margin, supported yaw domain, and sufficient separation for the chosen jaw
opening. Refusals remain valuable episodes, but they should form a deliberately sampled stratum,
not accidentally dominate the grasp-success cohort. The incremental-yaw identity/kinematic-hold
mechanism is retained as useful progress, but high-yaw contact is not yet promoted to the volume
domain. The arm homed normally after the adaptive stop.

An explicit `select_grasp_target` Hebbot skill now implements that eligibility decision. It retains
every detected instance and annotates it with predicted symmetric grasp yaw, image/controller proxy
margin, nearest-neighbor distance, eligibility score, and a list of rejection reasons. Initial
volume-domain defaults require at least **700 px** object area, **100 px** image-edge margin,
predicted absolute yaw ≤**30°**, and **80 px** nearest-neighbor clearance. Rejection reason values
are `object_area`, `image_or_controller_margin`, `yaw_domain`, and `neighbor_clearance`. Eligible
candidates rank by area and robustness margins rather than whichever color blob happens to be
largest. This keeps the deliberate model-layer target decision distinct from execution: the
selector recommends and records; `vision_grasp` still performs perception, alignment, contact, and
verification as a separate episode. Mock/offline coverage checks registration, ranking, and reason
retention; the suite is **68/68 passing**. After MCP reload, the next live action should call the
selector before any grasp and either execute its top candidate or explicitly record that the scene
has no volume-eligible target.

The first live selector call retained all 14 detected candidates and chose exactly one: a red block
at `(191,339)` with 741 px area, 140 px edge margin, 112.5 px nearest-neighbor clearance, and
predicted `+20°` yaw. `vision_grasp` attempted it in record-only mode. One alignment attempt reached
**1.83 px**, but the retry ended at **6.22 px** and safely refused contact. The marginal component
area produced substantial centroid/depth variability, so the original 700 px criterion was too
permissive for a volume-success cohort even though the decision trace was correct and reviewable.

Eligibility defaults are tightened to **1,000 px** minimum area and **110 px** image/controller
proxy margin; yaw ≤30° and neighbor clearance ≥80 px remain unchanged. These are cohort-quality
thresholds, not universal manipulation limits: smaller/edge targets remain valuable deliberately
sampled refusal or capability-expansion episodes. With the blocks in their current positions, the
honest selector result may be zero eligible targets. The arm homed normally and the offline suite
remains **68/68 passing**.

After the 2026-07-29 MCP restart, Codex repeated the tightened eligibility decision against a fresh,
persisted aligned RGB-D observation. The camera returned 640×480 color and depth, 78.7% valid depth
coverage, and 0.5133 m center depth; the arm was nominal at its canonical home pose. The selector
again returned **zero eligible targets**, which is the expected abstention rather than an execution
failure. The strongest candidate was a 1,637 px blue block at `(538.9,271.2)`, predicted yaw `+2°`
and 113 px neighbor clearance, rejected solely because its 100.1 px edge/controller margin missed
the 110 px cohort threshold. The centered red candidate at `(188.8,337.5)` was rejected solely for
area (898 px). Repeating a grasp on either would intentionally sample a known boundary/noisy regime,
not the desired repeatability cohort. This confirms that the selector survives reload and prevents
volume collection from silently degenerating into boundary-condition retries. No motion was issued;
the next high-information step requires either arranging several blocks inside the eligible visual
domain or explicitly declaring a capability-expansion trial with relaxed, recorded criteria.

Codex then attempted autonomous scene arrangement with two deliberately short `shove` trials,
persisting aligned RGB-D observations and returning home before every comparable selector decision.
The first trial pushed the near-threshold blue instance image-left by a commanded 60 mm. Its home-view
edge margin improved from **100.1 px to 116.9–118.4 px**, but contact rotated the block from predicted
grasp yaw **+2° to −43°**. It therefore exchanged an edge-margin rejection for a yaw-domain rejection
instead of producing an eligible volume target. An immediate post-shove selector observation also
exposed a protocol mistake: `shove` retreats only to about z=0.20 m, where component scale and apparent
geometry are incomparable with home-view eligibility thresholds. All subsequent comparisons were made
only after explicit homing.

The second trial requested a 100 mm image-left shove of the low-yaw green instance seen at
approximately `(612.5,366.2)`. After homing, that right-edge instance was essentially unchanged at
`(609.9,368.0)`, while a different green instance had moved from about `(378.5,409.6)` to
`(279.0,400.5)` and appeared at **46°** predicted yaw. Both motions completed without robot fault and
the arm returned nominally home, but the declared stop condition was invoked after the second
uncontrolled pose/identity result. The architectural conclusion is that `shove` cannot yet serve as
an autonomous scene arranger: its home-view target pixel is apparently resolved after a viewpoint
transition without preserving object identity, and free pushing does not control final yaw. The
smallest meaningful follow-up is to give `shove` the same persistent RGB-D instance/relational identity
used by grasp, and to verify target identity before contact and after retreat. Until then, manual
placement or explicitly sampled capability-expansion grasps are more scientifically honest than
repeated open-loop shoves.

Codex implemented the smallest identity-preserving `shove` revision. Before any camera motion, the
skill now binds the requested overview pixel to a connected raised RGB-D body and records its
top-height fingerprint. During servoing, every observation must reacquire a body of the requested
colour within a bounded pixel gate and within 18 mm of that physical height. Loss of continuity is
now an explicit pre-contact refusal; critically, the previous code merely broke out of its tracking
loop and proceeded to push from the last arm pose. Contact is authorized only after the same body is
observed at the bottom view within the existing 4 px alignment criterion. After the push and retreat,
the skill attempts another same-height RGB-D observation and records it as evidence rather than
silently assuming the requested object moved. The outcome includes the complete identity history,
rejected-frame counts, expected height, and post-push identity result. A focused offline regression
forces identity loss after initial acquisition and verifies that no commanded pose drops below 0.20
m. The complete offline suite is **69/69 passing** and `git diff --check` is clean. A server reload is
required before the live falsification: request the previously ambiguous right-edge green target and
require either continuous identity through contact or a safe abstention—never movement of a different
green instance.

After reload, the live falsification selected the right-edge green instance at home-view
`(610.1,368.0)`. `shove` bound it to a raised-depth body at `(621.63,377.34)` with **2,926 depth
pixels** and a **31.2 mm** top-height fingerprint (one transient frame rejected). After the skill's
legacy vertical transition to z=0.34 m, three frames failed the bounded identity gate. The revised
skill returned `pushed=false` and `target identity/alignment unavailable before contact`; no low-Z
motion, contact, or movement of either green block occurred. After homing, the requested instance
remained at `(609.7,366.6)` and the other green remained at `(279.1,399.8)`. The arm was nominal
(home maximum joint error 0.00402 rad). This validates the refusal and localizes the remaining issue:
the old shove pipeline narrows the view before centering an edge target.

The servo now starts at the calibrated **z=0.45 m overview ceiling**, centers the same depth-bound
body there, and descends through the existing error-gated staircase only after it remains visible.
The bounded location/height continuity and pre-contact refusal are unchanged. The regression now
also fixes the initial z=0.45 behavior; the complete suite remains **69/69 passing** and
`git diff --check` is clean. Another MCP reload is required for the direct retry.

The reloaded direct retry preserved the right-edge green identity throughout the complete overview
servo. The initial body was `(618.41,367.79)`, **2,214 depth pixels**, and **27.8 mm** high. Across
15 observations at z=0.45 m, the body remained inside the bounded gate with observed height
20.8–31.8 mm. Servo error improved from **294.61 px to 33.34 px**, but then occupied a noisy
33–42 px plateau. The old shove controller required ≤22 px before descending, so it repeated all
corrections at the same height, exhausted its loop, and correctly returned `pushed=false`. No contact
or block displacement occurred; the requested green returned at `(606.0,362.8)` and the other at
`(275.9,401.4)` within ordinary viewpoint/segmentation variation. The arm homed normally (maximum
joint error 0.00828 rad). Identity preservation is therefore validated; the remaining failure is the
same fixed-height correction anti-pattern previously removed from grasp.

`shove` now uses ten distinct observation heights from **z=0.45 to 0.20 m**. Each level permits at
most one combined X/Y correction and immediately moves closer; it no longer waits for a 22 px gate
at a fixed overview height. At the final height, one correction is followed by one observation-only
verification, and the unchanged 4 px criterion alone authorizes contact. A focused unit test fixes
the strictly descending, unique-height schedule. The complete suite is **70/70 passing** and
`git diff --check` remains clean. This change needs an MCP reload before the next live retry.

The reloaded staircase retry retained the same green body through all ten distinct heights and final
verification: initial **2,860 depth pixels / 31.5 mm**, with observed heights 23.1–33.3 mm. Error
fell from **298.27 px at z=0.45** to 32.55 px at z=0.3111, then remained 31–36 px through z=0.20;
final observation-only verification was **33.54 px**. The skill correctly refused contact and the
block did not move. A state observation before homing located the cause: EE y was **+0.27864 m**,
effectively pinned at the hard-coded +0.28 m servo clamp, while x and z remained ordinary. Thus the
stable residual is a recorded software controller-envelope boundary, not target loss, depth noise,
or Franka reach failure. The arm homed normally (maximum joint error 0.00844 rad).

For `shove` only, the lateral controller envelope is expanded from ±0.28 to **±0.34 m**. This is a
narrow capability-expansion policy for moving edge blocks inward; grasp policies and their cohort
limits are unchanged, as are the 4 px pre-contact gate and hardware protections. Every servo history
entry now records commanded XY and whether software clipping occurred, and the shove outcome records
the active y limit. A focused regression verifies that the arranger can command y=+0.34 m without
altering other skills. The complete offline suite is **71/71 passing** and `git diff --check` is
clean. An MCP reload is required for the direct capability-expansion retry.

The capability-expansion retry did command beyond the former envelope without clipping. The same
green body remained depth-verified from z=0.45 through z=0.3389 while commanded y progressed from
0.0606 to **0.2965 m**. Height remained 24.9–33.3 mm. At z=0.3389 the residual was still **117.09
px**; after the full correction and descent to z=0.3111, three frames failed the fixed 90 px
continuity gate. The skill safely refused before contact and the arm homed normally. This falsifies
the hypothesis that ±0.28 m saturation was the only blocker, while confirming that the ±0.34 m
capability is reachable without a Franka fault.

The trace exposes a tracker/controller mismatch: a controller-authorized correction larger than the
tracker's next-frame displacement gate guarantees rejection of the motion it just commanded. Shove
continuity is now command-aware: the next observation gate is `clip(previous_error + 40 px, 90,
180)`, recorded per observation. The other same-colour green is over 300 px away in the overview and
the physical height fingerprint remains mandatory, so this bounded expansion admits predicted servo
motion without restoring unbounded nearest-colour substitution. Offline checks cover the lower,
live-relevant, and upper gate bounds; the full suite remains **71/71 passing** and `git diff --check`
is clean. Another MCP reload is required for the direct retry.

## 2026-07-30 — Codex literature/project review: Gemini Robotics ER 2

*Added by Codex. Earlier notebook entries were written by Claude; Claude may return to review,
comment on, and contribute to this work.*

Primary source: Steven Hansen and Peng Xu, [“Introducing Gemini Robotics ER
2”](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-robotics-er-2/),
Google DeepMind, 2026-07-30. Context: Google’s prior public
[Gemini Robotics-ER 1.6 announcement](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-robotics-er-1-6/),
2026-04-14.

### What Google is claiming

Gemini Robotics ER 2 is an embodied-reasoning model intended to act as a **high-level robot brain**,
not as the motor controller itself. It accepts streaming video, audio, and text; converses with a
human; plans multi-step physical tasks; calls user-defined functions, conventional robot APIs, or a
lower-level vision-language-action (VLA) model; monitors execution; and adapts when progress or
failure evidence changes. The model is integrated with the bidirectional Gemini Live API so planning
can overlap ongoing execution rather than creating a serial stop–think–act loop. Google states that
it is publicly available through the Gemini API and Google AI Studio, with Gemini Enterprise Agent
Platform access in private preview.

The announcement emphasizes four advances over ER 1.6:

- improved orchestration across real VLA control, simulated VLA control, and human teleoperation;
- continuous video-based progress and success/failure understanding;
- temporal “moment finding” for identifying when a critical event or task transition occurs;
- collaboration among heterogeneous robots through a shared semantic task representation.

Reported temporal evaluation numbers deserve calibrated interpretation. Five-bin progress
classification reaches **57.4% accuracy**. This is potentially useful supervisory context, but is
far from reliable enough to serve as physical ground truth. Moment finding reaches **91.3% accuracy**
with **0.96 s mean absolute temporal error**, reportedly at four times the execution speed of the
larger comparison category. That is promising for task-level transitions such as “pouring is done,”
but roughly one second remains far too coarse for contact servoing, collision response, or grasp
closure. Google also reports improvements in raw-video success/failure detection, spatial VQA,
generalized instrument reading, safety-instruction following, and human-proximity evaluation, but
the announcement does not provide enough methodological detail to treat those claims as independent
evidence of deployment reliability.

### Architectural correspondence with Hebbot

The correspondence is unusually direct:

| Gemini Robotics ER 2 | Hebbot |
| --- | --- |
| high-level embodied-reasoning model | deliberate foundation-model layer |
| declared robot/VLA/navigation tools | registered Hebbot MCP skills |
| lower-level VLA execution | future reflexive learned skill/policy |
| continuous multimodal observation | wrist RGB-D, robot state, force, and episode checkpoints |
| progress classification | episode phase/progress labels |
| moment finding | contact, closure, lift, slip, completion, and failure boundaries |
| success/failure monitoring and retry | verification, recovery selection, and adapted replay |
| robot APIs | Franka, gripper, D405, cuRobo, and hardware processes |

This supports Hebbot’s deliberate separation of concerns. A foundation model should choose goals,
decompose tasks, interpret ambiguous evidence, and select tools. The substrate should expose stable,
inspectable skills and preserve their complete outcomes. Fast contact-sensitive execution should
remain in classical controllers or learned reflexive policies. Hardware processes should remain
below all three. ER 2’s explicit handoff to arbitrary VLAs and robot APIs is strong external evidence
that this layered design is not merely scaffolding on the way to an end-to-end policy; it is a useful
long-term interface between semantic reasoning and heterogeneous embodiments.

Our current `shove` investigation is a concrete example. A model like ER 2 might recognize that the
block has not moved, classify the step as incomplete, and propose a recovery. It cannot substitute
for the substrate’s millimetre-scale RGB-D identity gate, Cartesian-envelope telemetry, 4 px contact
authorization, or Franka force/controller reflexes. Conversely, Hebbot’s present synchronous tool
loop cannot yet watch video continuously, reason ahead during motion, or recognize task-level
progress with the semantic breadth Google demonstrates. The two systems address complementary
parts of the stack.

### What the announcement does not demonstrate

ER 2 is not presented as an online or continually self-improving robot. The announcement does not
claim persistent episode storage, retrieval of analogous physical experience, adapted replay,
cross-episode credit assignment, local policy training from verified executions, or distillation of
repeated deliberation into faster reflexes. It improves the **deliberate orchestration layer**; it
does not by itself close Hebbot’s central learning loop:

`deliberate execution → complete episode → retrieval → adapted replay → verified dataset → distilled reflex`

This distinction is strategically important. Hebbot should not attempt to out-train Google on a
general embodied foundation model. Its scientific opportunity is to become the embodiment-specific,
experience-preserving substrate beneath frontier reasoners: the system that grounds their proposals
in depth, force, kinematics, controller state, and remembered consequences, and then converts repeated
verified experience into a cheaper local execution policy.

### Practical implications and proposed evaluation

ER 2 could contribute to Hebbot in at least five bounded roles:

1. deliberate planner and Hebbot MCP tool caller;
2. offline critic that labels task phase, progress, failure moments, and recovery opportunities;
3. teacher that proposes structured skill parameters and relational recovery plans;
4. semantic retriever or reranker over stored episodes;
5. label generator for the verified corpus used to train a small local policy.

The smallest high-value evaluation should be **offline**, preserving the distinction between model
judgment and hardware truth. Construct short episode videos from saved Hebbot RGB frames, optionally
paired with human-viewable depth visualizations and structured state/force summaries. Include:

- verified grasp/lift successes;
- pre-contact alignment refusals;
- the wrong-instance open-loop shove;
- the corrected identity-preserving abstention;
- controller-envelope saturation;
- temporal tracking loss after a commanded displacement.

Ask ER 2 to emit a fixed schema containing target identity, task phase, progress bin, success/failure,
critical-event timestamp, causal hypothesis, confidence, and recommended next Hebbot skill/parameters.
Score those fields against the episode database, depth/force evidence, and our written causal analyses.
The main scientific question is not whether the prose sounds intelligent; it is whether its labels
and recovery rankings are calibrated enough to improve retrieval, episode triage, or subsequent live
success over our current structured heuristics.

If that offline gate passes, the next controlled integration would allow ER 2 to select among a
small whitelist of no-contact Hebbot observations/probes while Hebbot retains parameter validation,
episode recording, and all motion/contact authorization. Only after measured improvement should it
orchestrate live contact skills. Meanwhile, its video annotations could immediately enrich the
training corpus for the tiny local feasibility/progress models that advance Hebbot’s compression
thesis.

### Conclusion

Gemini Robotics ER 2 is the clearest external validation so far of Hebbot’s proposed deliberate
layer: a multimodal physical-world reasoner coordinating inspectable lower-level tools while
monitoring temporal progress. Its strongest lesson for Hebbot is to evolve from isolated tool-call
snapshots toward concurrent observation, explicit task progress, and event-boundary records. Its
most important limitation is equally clarifying: a stronger reasoner is not the same as a system
that learns persistently from its own embodied history. Hebbot’s differentiating milestone remains
turning verified deliberate episodes into retrieval gains and ultimately distilled local reflexes.

### 2026-07-30 Codex continuation: adaptive tracking succeeds; close-view parallax remains

After the Gemini Robotics ER 2 review, the command-aware `shove` retry resumed. The right-edge green
body remained continuously depth-verified through all ten staircase levels and final verification.
Its initial identity was **3,182 depth pixels / 26.5 mm** high; observed height remained 20.0–32.5
mm. The adaptive tracker gate expanded from 90 to 180 px for the large overview corrections, then
contracted to 124.1 and 90 px as residuals shrank. No frame was rejected and no same-colour identity
substitution occurred. Commands reached y=**0.3049 m** without software clipping.

Servo error fell from **297.27 px at z=0.45** to 13.93 px at z=0.3111, 7.42 px at z=0.2833, and
**4.87 px at z=0.2556**. Continuing closer made the estimate worse: 9.35 px at z=0.2278 and 14.95
px at z=0.20. The one permitted final correction recovered only to **8.42 px**, so the unchanged
4 px contact guard correctly refused the shove. No block moved, and the arm homed normally (maximum
joint error 0.00825 rad). This validates command-aware continuity and reproduces the close-view
hand-eye/parallax boundary previously measured in `vision_grasp`: more proximity below roughly
0.25 m reduces alignment accuracy rather than improving it.

Visual authorization for `shove` now terminates at calibrated **z=0.255 m**. This changes only the
perception/execution handoff: after authorization, the closed gripper still stages beside the block
at z=0.20 and performs the physical push at the requested z=0.006. Thus the controller does not
pretend the closest camera view is the most accurate, while the contact trajectory remains unchanged.
The active alignment height is recorded in both refusal and push outcomes. Offline coverage fixes
the calibrated value; the full suite remains **71/71 passing** and `git diff --check` is clean. An
MCP reload is required for the direct contact retry.

The z=0.255 handoff retry again retained the same right-edge green identity through every scheduled
view, with no rejected frames or controller clipping. The initial body was **2,572 depth pixels /
34.3 mm**; observed heights remained 23.1–27.9 mm after the overview. Error fell from **297.33 px**
to 22.72 px at z=0.3417, 18.95 px at z=0.3200, and a near-gate **4.11 px at z=0.2983**. The
controller applied its one correction there but immediately descended without observing its result.
Subsequent views worsened to 9.46 px and then 25.22 px at z=0.255; final verification was **20.18
px**, so contact was correctly refused. No block moved and the arm homed normally (maximum joint
error 0.00838 rad). This shows that a universal fixed “best Z” is too brittle under frame-to-frame
variation, while also revealing that the staircase discarded a potentially successful correction.

`shove` now performs an **observation-only verification** after any level whose pre-correction error
is ≤8 px. A verified residual ≤4 px authorizes execution at that current, reliable view. If it does
not pass, the arm descends to the next level without a second correction at the same Z. The bottom
level retains the same correction-plus-verification behavior. This preserves the one-correction-per-Z
rule while allowing the controller to accept demonstrated convergence before entering a worse
close-view regime. The 8 px threshold is recorded as a calibrated controller constant; the complete
offline suite remains **71/71 passing** and `git diff --check` is clean. Another MCP reload is
required for the direct retry.

The near-gate verification retry produced the first identity-preserving autonomous shove. The same
right-edge green body remained continuous from the overview through z=0.2767, with observed height
22.0–33.2 mm and no controller clipping. A verification at z=0.320 worsened 2.36→10.16 px and the
staircase correctly continued; z=0.2983 verified 4.22→5.26 px and also continued. At z=0.2767, the
single correction verified **2.42→1.83 px**, authorizing contact. The closed gripper executed the
configured 100 mm inward shove path from `(0.4121,0.3323)` to `(0.4089,0.1983)` at z=0.006. The
immediate close-view post-identity check did not see the displaced body, but the home-view comparison
verified that the requested green moved from `(604.5,362.4)` to `(587.0,354.6)` while the other green
remained `(275.2,400.5)`→`(276.1,401.3)`. The arm homed normally (maximum joint error 0.00832 rad).

The physical result was only a partial arrangement success. The target's image-edge margin improved
from **34.5 to 52.0 px**, but its predicted grasp yaw changed from **+10° to −75°** and translation
was only about 20 px despite the longer commanded path. The pre-push scene explains this surprise:
the red block at `(517.9,376.0)` was only 87.7 px away and lay directly across the image-left push
corridor. The green appears to have translated into that neighbour and pivoted rather than sliding
freely. Thus identity tracking, staged alignment, authorization, and contact execution are now
demonstrated; the next missing arranger capability is **corridor occupancy**, not further servo
tuning. A deliberate next step is to move the obstructing red block into open central space before
attempting another inward green shove, and eventually make corridor clearance an explicit recorded
precondition rather than relying only on nearest-neighbour distance.

A follow-up attempt tried to clear that corridor by moving the obstructing red instance at
`(503.8,374.6)` image-left by 80 mm. The skill initially bound a **23.6 mm** raised red body, made
one overview correction, then lost bounded identity at z=0.4283 and safely refused before contact.
After homing, the red remained at `(503.8,374.7)` and both greens were unchanged. The arm was nominal
(maximum home joint error 0.00827 rad). This failed attempt is retained: an object that is a valid
home-view colour component may still be a poor depth-body servo target near adjacent objects. One
eligible, well-separated low-yaw red target remains at approximately `(185,335)`, so the scientifically
useful next step is to resume verified grasp collection on that admitted target rather than repeatedly
forcing the blocked arrangement corridor.

The selector-admitted red target was then used for a record-mode `vision_grasp` episode with the
calibrated low-yaw staircase. It failed safely before descent. The first aligned-depth body measured
**48.3 mm** high; subsequent accepted frames measured 31.3 and 35.8 mm. Because continuity remained
anchored to the anomalous initial height, six stationary frames were rejected and the episode ended
`target lost or rejected by aligned-depth gate`. There was no controller clipping, contact, or
mechanical attempt. RGB plus aligned depth were stored, and the arm homed normally (maximum joint
error 0.00516 rad). The selector's RGB-only area/margin/yaw eligibility therefore admitted a target
whose physical depth identity was not stable enough for the executor—a cohort-definition mismatch,
not evidence against the grasp policy.

`select_grasp_target` now evaluates every RGB candidate against **three stationary aligned RGB-D
captures**. Admission requires a connected raised body in all three frames and top-height spread
≤**12 mm**. It records observation count, median physical height, height spread, and median depth-body
area for every candidate. Failed physical continuity adds the explicit rejection reason
`depth_identity`; the existing RGB area, image/controller margin, yaw-domain, and neighbour-clearance
reasons remain independently visible. This moves a proven executor precondition into the deliberate
volume-cohort decision without conflating selection with execution. Focused ranking coverage checks
the new reason; the complete suite remains **71/71 passing** and `git diff --check` is clean. An MCP
reload is required before asking the selector for the next volume target.

---

## 2026-08-31 — Claude review of the July work, and a literature pass including Anthropic's Model Hardware Standard

*Added by Claude, returning after a two-month gap to review the work Codex did at the bench between
2026-07-24 and 2026-07-30, as the July entries invited. Verified against the code, the episode store,
the promoted-policy artifacts, and a live test run — not against the prose. Findings #146 and #147
continue the numbered log, which paused at #145.*

### Review of Codex's work: what holds up

The validation discipline is stronger than the June work it builds on, and it is self-applied. Every
learned artifact is gated before it may be used, and the reasons for rejection are written into the
artifact rather than into a log the next reader has to find. The feasibility classifier holds out by
**spatial group** (35 px clusters), so a candidate cannot pass by memorising one table position.
Warm-start gates on leave-one-out error *and* a spatial-diversity floor. Artifacts are content-hash
named and carry `source_episode_ids`, so any promotion decision is auditable back to the episodes
that produced it. This is exactly the ablation-and-provenance discipline the 2026-07-28 CaP-X note
demanded, applied to Codex's own output.

The fail-safe defaults are correct throughout. `warm_start.propose()` returns
`decision: "orchestrator"` unless the artifact is promoted **and** the query is inside the training
domain **and** the prediction lies in the workspace box. `feasibility_mode` defaults to `off`.
`_label()` returns `None` for ambiguous outcomes instead of manufacturing a class. Abstentions are
recorded as episodes rather than discarded — the failed red-block servo attempt is retained as data,
which is the right instinct and the one most systems get wrong.

The close-view parallax boundary is a genuine transferable result. `shove` independently reproduced
what `vision_grasp` had found: alignment error minimises near z ≈ 0.26–0.28 m and **worsens** below
it (4.87 px at z=0.2556 → 14.95 px at z=0.20). Terminating *visual authorization* at a calibrated
z=0.255 while leaving the contact trajectory unchanged is a clean separation of the perception
handoff from the motion, and the near-gate correction that followed — accept demonstrated
convergence at ≤8 px rather than descend into a worse regime — is Codex correcting its own first
answer from evidence. The partial-success diagnosis (corridor occupancy, not servo tuning) is the
kind of attribution that is easy to get wrong and was got right.

### Finding #146 — the compression loop is not wired; no policy has ever been promoted

| # | Finding |
|---|---------|
| 146 | **No distilled artifact can influence live execution, even if one were promoted.** `primitives.py` has exactly one runtime import from `hebbot.learning` — `probe_outcome_axes` (`primitives.py:2844`), a pure labelling function that normalises a probe result into orthogonal evidence axes at record time. It reads no artifact and makes no control decision; it is corpus schema, and good schema at that, since it returns `None` rather than converting absence of evidence into failure. Every other module in `hebbot.learning` — `feasibility`, `warm_start`, `capability_map` — is imported only by `scripts/train_*.py` and `scripts/map_capability.py`, and `mcp_server.py` imports none of them. So the trained models are offline artifacts that nothing consumes. The naming hides this: the runtime `feasibility_mode="avoid_saturation"` path (`primitives.py:2585`) is *episode memory* — a SQL scan of recent probe episodes matching candidates within 60 px of a remembered saturation — and is a wholly separate mechanism from the trained logistic classifier in `learning/feasibility.py`, which is read by nobody. **And nothing has passed a gate:** all four artifacts in `hebbot-data/policies/` are `promoted: false` — `feasibility_afa6fbf4746a` (n=11, 1 workspace-limited spatial group < 2), `controller_envelope_cd5e2bb85304` (n=11, same), `vision_grasp_warm_start_afb458bc1cdc` (n=3 < 6; pre-close xy span 0.0171 m < 0.0600 m). The gates work; the constraint is data. The store holds **76 `vision_grasp` episodes in the project's lifetime**, of which three carried the verified-success plus capture structure warm-start requires, and all three occurred within 1.7 cm of one another. So the thesis chain — deliberate → episode → retrieval → adapted replay → verified dataset → **distilled reflex** — is real and demonstrated as far as adapted replay, and the final arrow is a stub. The useful consequence: there are currently *two* gaps between experience and reflex (no data, and no wire), and only one of them costs hardware time to close. Wiring one promoted artifact into a default-off runtime path, with the decision recorded as a `decisions` row, would leave the promotion gate as the single thing standing between the corpus and a reflex — which is what the architecture claims it already is. |

*Corrigendum, same day: the first version of #146 stated that nothing in `primitives.py` imports
`hebbot.learning`. That was wrong, and wrong for a traceable reason — `c00ad74` was still unpushed
when the review began, so the checkout being read lacked `learning/outcomes.py` and
`learning/capability_map.py`. The `probe_outcome_axes` runtime import above is the correction. The
finding's substance is unchanged: a labelling helper is not a distilled reflex. `capability_map.py`,
reviewed after the pull, is likewise offline — collision-aware cuRobo IK and path sampling over an
xy grid with no commanded motion, imported only by `scripts/map_capability.py`, producing the two
files in `hebbot-data/capability_maps/`. It is a clean way to separate geometric reach from
controller behaviour, and it strengthens rather than weakens the point that the geometric/execution
split is not yet visible in the eleven labelled episodes.*

Three further defects found in the same pass, none fatal, all worth recording before they harden:

**The newest commit's distinction is presently empirically empty.** `c00ad74 "Separate geometric and
execution feasibility"` produces `controller_envelope_*` alongside `feasibility_*`. Comparing the two
artifacts directly: **identical `source_episode_ids`, identical labels, identical group-LOO Brier
(0.08863), identical class counts.** The two-model story rests on one labelling of the same eleven
episodes. The refactor may well carve correctly once the cohorts diverge, but as of today nothing
distinguishes the two models and the notebook should not imply that two learned things exist.

**The feasibility classifier has two features, `u_norm` and `v_norm`** — it is a linear separator over
*image position*. It cannot represent a reach boundary that is nonlinear in image coordinates, and it
conflates camera pose with robot kinematics. `extract_examples` guards this by filtering on
`acquire_z = 0.45 ± 0.02`, which is right, but only in z: the artifact never records the acquisition
pose it is valid for. Move the observe pose and the artifact becomes silently wrong with no way to
detect it. The acquisition pose belongs in the artifact as a validity precondition.

**Warm-start may be gating on the wrong quantity.** It predicts pre-close EE xy from the *first*
observation, but the value of a warm start is servo steps saved. `mean_deliberate_servo_steps` is
recorded and never gated on, so a candidate could pass the accuracy threshold and save nothing.
(Leave-one-out RMSE at n=3 is near-meaningless anyway; the xy-span floor is what actually caught this
candidate, and deserves the credit.) Two smaller notes: the corpus plumbing is brittle for what it is
being asked to carry — 1,548 episodes but only **122 states** and 627 frames, forcing
`extract_verified_examples` into a nearest-in-time state/frame join plus a substring match on
`"before_close" in f["file_path"]`; and `_spatial_groups` is greedy first-match over
`reversed(recent_episodes)`, so group assignment — and therefore the promotion decision — depends on
episode ordering, which should be deterministic by construction and named as a calibrated constant
like the others.

### Finding #147 — CaP-X's warning has come true, and the loop that would answer it is dormant

| # | Finding |
|---|---------|
| 147 | **Every measurable improvement between 2026-06-27 and 2026-07-30 came from better hand-written primitives, not from learning — and no measurement exists that could tell the difference.** `primitives.py` roughly doubled over the window (2,490 → ~4,700+ lines). The gains are real and they are all substrate: the shove alignment staircase, near-gate verification, adaptive depth-identity gates, the yaw envelope, the z=0.255 handoff. Meanwhile **`practice` has not run since 2026-06-27** and `practice_metrics` holds two episodes in total. The self-improvement loop that was June's headline has been dormant while the substrate was extended around it. This is precisely the failure mode the project's own 2026-07-28 CaP-X note predicted — *"Hebbot must measure whether gains come from memory/learning or from progressively better hand-written primitives"* — arriving one month after it was written down. It is not an argument against the substrate work, which is high quality and was needed. It is that the project's central scientific claim is currently unfalsifiable: with no frozen benchmark and no ablation at a pinned policy-code hash, "the system learned" and "we wrote better code" produce identical evidence. The measurement has to be built **before** more data is collected, or the collected data will not be able to settle the question either. |

### Literature: Anthropic's Model Hardware Standard (2026-08-27)

Primary source: Anthropic, ["Previewing the Model Hardware
Standard"](https://www.anthropic.com/news/model-hardware-standard-research-preview), 2026-08-27,
developed with HHMI Janelia Research Campus.

MHS is a common driver layer through which agents discover, read from, and write to physical
equipment — microscopes, liquid handlers, plate readers, laser controllers, robot arms. Two
primitives: **reads** (retrieve a temperature) and **writes** (set a temperature). The driver carries
**natural-language tags** documenting physical characteristics, adjustable parameters, and *enforced
safety limits* that are not discernible from code — the example given is a robot arm's weight, with a
vendor constraining the speeds and angles an agent may use. From those tags the system generates an
agent-facing reference file. Three access paths: MCP, a CLI, and code/APIs. Anthropic's own framing
is "what MCP did for software, MHS will do for the hardware world." It is an application-gated
research preview, not open source at launch, with stated intent to open-source afterwards; testers
include AWS, Danaher, Hugging Face and Raspberry Pi, and the robotics-adjacent named work is Doosan
multi-robot coordination plus a LeRobot integration.

**Three consequences for Hebbot.**

**It commoditises the layer beneath Hebbot, which is good news.** MHS standardises the *device
interface*. It says nothing about experience, verification, retrieval, or compression. If "how do I
talk to this arm" becomes free, the scarce remaining asset is exactly what Hebbot stores: what
happened when it tried, and what that changed. The correct repositioning is **the experience layer
above MHS**, not an integration layer — and Hebbot's MCP server is already the right shape to sit
there. This does not weaken the differentiator identified against ENPIRE and Waddle; it sharpens it.

**It gives the calibrated constants a declarative home.** Everything this notebook has painfully
measured currently lives as a literal inside `primitives.py`: the z=0.255 visual-authorization
ceiling, the 4 px contact guard, the 8 px near-gate, the 4 N load-bearing contact force, the −1°
gripper roll (#143), `table_z(x,y)` (#144), the workspace-saturation envelope. Those are precisely
what MHS encodes as safety-limit tags. Extracting them into a declarative device-capability file is
worth doing on its own merits — they are calibrated results, not implementation details — and it
would make an MHS driver nearly free later.

**Anthropic's stated limitations restate Hebbot's problem.** The launch material concedes
insufficient spatial reasoning for complex manipulation, absent physical intuition, over-caution, and
heavy context requirements. The named failure is that Claude retried a bubble-formation error into
*the same wells*, not understanding that foam was a physical state change. That is finding **#145** in
a different domain: an agent acting without a model of what its own actions changed. Predict-then-look
external-change detection is a proposed answer to exactly that, and the correspondence is close
enough to be worth putting in front of the preview programme.

**Regulatory note.** EU Machinery Regulation 2023/1230 takes effect **2027-01-20** and covers
AI-based safety functions for the first time; an MHS file that constrains equipment operation
plausibly constitutes a regulated safety component. Hebbot's established habit of recording
*refusals with their evidence* — the 4 px guard, the abstention episodes, the `contacted` flag — is
the audit trail such a regime asks for. Worth treating as a differentiator rather than as future
compliance overhead. **Caveat: do not refactor toward MHS yet.** The specification is not public;
apply, read it, then decide.

### Literature: self-improvement work since the July notes

**RoboClaw**, "An Agentic Framework for Scalable Long-Horizon Robotic Tasks"
(<https://arxiv.org/abs/2603.11558>), introduces **Entangled Action Pairs**: couple every forward
manipulation behaviour with its inverse recovery to form *self-resetting loops* for autonomous data
collection, with the same agent orchestrating learned primitives at deployment. This lands directly on
the #146 bottleneck. Hebbot's `practice` reset (un-stack + place) is a hand-built EAP special case
bolted to one loop; making reset a first-class property of *every* skill is the structural change that
would let verified episodes accumulate without a human at the bench.

**RISE**, "Self-Improving Robot Policy with Compositional World Model"
(<https://arxiv.org/abs/2602.11075>, RSS 2026, OpenDriveLab, code released) — a controllable
multi-view dynamics model plus a progress value model, improving a policy by imagination to escape the
cost, safety, and reset limits of physical rollouts; +35–45% absolute on contact-rich tasks. Relevant
because Hebbot's rollout budget *is* the binding constraint (76 grasps, ever), and because the seeds of
both components already exist here — the #145 scene store and an implicit progress notion. The
counter-consideration is that RISE trains a neural policy, so it is what one reaches for *after* the
corpus exists, not instead of building it.

**Experience distillation outside robotics** — EDGE (<https://arxiv.org/html/2608.21946>) validates
and distils online experience while co-evolving its bank by **utility-based pruning**; also
Mem²Evolve and Procedural Memory Distillation. Worth reading because Hebbot's episode store only ever
grows and retrieval is nearest-neighbour on relational similarity. That literature is about *which*
experiences are worth keeping, a question that arrives the moment collection scales.

Already reviewed and unchanged in status: ENPIRE, Waddle, CaP-X, ASPIRE, Gemini Robotics ER 2.

### Recommended order of work

1. **Wire the loop before collecting more data.** One promoted artifact reachable at runtime behind a
   default-off flag, with the decision recorded as a `decisions` row. Reduces #146's two gaps to one.
2. **Build the measurement before adding capability.** A frozen benchmark with ablations —
   substrate-only vs. +memory vs. +policy — at a pinned `primitives.py` content hash. Without it the
   July gains are unattributable and #147 stands.
3. **Then collect, using the failed gates as the specification.** Revive `practice` under RoboClaw's
   EAP framing, and *randomise target position*: the xy-span rejection is the corpus reporting that
   all of its data came from one spot.
4. **Unified rollout record** — states at frame rate, explicit phase and event boundaries. This is the
   June roadmap item that now blocks both distillation and any ER 2-style offline critic.
5. **Apply to the MHS research preview**, and meanwhile extract the calibrated constants into a
   declarative capability file.

The carried-forward bench threads from 2026-07-30 are unchanged and still correct: corridor occupancy
as an explicit recorded precondition, the selector/executor cohort mismatch (RGB-eligible targets
whose aligned-depth identity is not stable enough for the executor), and resuming verified grasp
collection on the admitted low-yaw red target near (185, 335).

---

## 2026-08-31 (cont.) — closing the wire, and building the measurement that makes #147 falsifiable

*Added by Claude, acting on the review earlier today. Two of the five recommended items are
built and tested offline; the suite is 94/94 (was 71/71). Nothing here has run on hardware,
and one path is deliberately left closed until it has.*

### Finding #148 — the wire is closed, the promotion gate is now the only barrier, and building the ablation immediately proved its own necessity

| # | Finding |
|---|---------|
| 148 | **A distilled artifact can now reach live execution, and the substrate now records what code produced every episode — and the first run of the resulting ablation report refused to compare anything, which is the correct and useful answer.** Three pieces. (1) `learning/registry.py` is the wire: it returns only artifacts whose `promoted` flag is true *and* whose recorded validity envelope matches the call, degrades to deliberate control on any malformed or missing artifact, and reports a reason for every absence so the fallback is logged rather than silent. (2) Two runtime paths use it — `vision_grasp_probe(feasibility_mode="policy")`, where the distilled controller-envelope classifier may **only remove** candidates the deliberate layer would have tried, and `vision_grasp(warm_start_mode="shadow")`, which records what the warm start *would* have proposed and commands nothing. (3) The dispatcher now stamps `policy_version` — the content hash of the primitives module that executed — on every classical outcome, and `learning/benchmark.py` derives each episode's experimental arm from the parameters it already recorded, so the substrate/memory/policy ablation needs no new bookkeeping. **The report's first run against the real store is the finding:** 36 `vision_grasp` episodes carry a verdict, 26 substrate and 10 memory, and *every one of them has no policy version*, because the stamp did not exist when they ran. So the tool declined to compute the delta — an arm comparison inside that bucket would also contain two months of primitive edits, which is exactly the confound of #147. Before the refusal was added it happily printed "memory vs substrate: −5%", a number that means nothing. **The measurement's first act was to invalidate the only comparison the project could previously have made.** Attribution starts from the next episode recorded. |

### The gates now disagree with each other, which is what gates are for

Retraining both candidates under the new rules produced a result worth recording. The
warm-start extractor now finds **five** verified examples rather than the three of 2026-07-24,
and the two gates split:

| gate | 2026-07-24 (n=3) | 2026-08-31 (n=5) |
|---|---|---|
| leave-one-out RMSE | 0.0134 m (passed) | **0.0561 m** (fails 0.015 m) |
| median held-out travel reduction | not measured | **0.132 m** (passes 0.02 m) |
| improved fraction | not measured | **1.00** (passes 0.75) |
| pre-close xy span | 0.0171 m (failed) | — |

The reading is that the *old* accuracy number was an artefact of the very defect the span gate
caught: three grasps 1.7 cm apart are trivially predictable, so a model fitted to them looked
precise and was worthless. Add two more, spread further, and honest accuracy collapses to
56 mm while usefulness becomes obvious — the prediction still removes 132 mm of
the start-to-preclose travel every time. That is a policy which is clearly *pointing the right way*
and clearly *not yet precise enough to hand the arm to*, and the two gates now say so
independently. Accuracy alone would have promoted the useless one and rejected the useful one.
This is the concrete answer to the #146 concern that warm-start gated on the wrong quantity:
`min_travel_reduction_m` and `min_improved_fraction` measure, on held-out episodes, how much
of the servo's work the reflex actually removes.

### Deliberate restraints, and why each one is there

**The policy may only subtract.** `feasibility_mode="policy"` can remove a candidate from
consideration; it can never add one, choose a grasp, or move the arm. The worst case for a
wrong policy is an abstention, which is already a first-class recorded outcome here.

**The applied warm start stays closed.** `warm_start_mode` accepts `off` and `shadow` only;
`apply` returns an explicit error saying the path opens when the shadow record shows on
hardware that the proposal shortens the servo. Commanding a 13 cm open-loop hop into a grasp
on the strength of five offline examples would be exactly the kind of unearned confidence the
notebook keeps catching, and the shadow record is the cheap way to earn it — every ordinary
grasp run with `warm_start_mode="shadow"` produces a live prediction-versus-achieved pair at
no motion cost and no risk.

**A shadow proposal is logged as an `orchestrator` decision, not a `policy` one,** and
`classify_arm` deliberately does not count `shadow` as a policy arm. A reflex that proposed
and was not obeyed did not act, and recording it as though it had would manufacture an effect
in the very ablation built to detect one.

**An artifact carries the pose it is valid at.** Both trainers now write
`validity: {acquire_z, tolerances}`, and the registry refuses an artifact whose envelope
excludes the current call. The feasibility classifier is a linear separator over *image*
position (#146); it is meaningless read from a different observe height, and it can now say so
instead of failing silently.

**Spatial grouping is order-independent.** `_spatial_groups` clusters over a canonical
pixel-sorted order, so the held-out split — and therefore the promotion decision — no longer
depends on what order episodes came back from the store.

### Where this leaves the roadmap

Items 1 and 2 of the 2026-08-31 recommendation are built and tested; items 3, 4 and 5 are
open. The ordering has been vindicated in a small way: building the measurement first was what
revealed that the whole existing corpus is unattributable, which is a fact about *collection*
that would have quietly poisoned any data gathered under item 3. The immediate next steps are
unchanged and now better specified:

1. Run ordinary grasps with `warm_start_mode="shadow"` at the bench. Every one produces a live
   prediction-versus-achieved pair and costs nothing; this is the evidence that opens the
   applied path, and it accumulates during work that was happening anyway.
2. Collect with **randomised target positions**. Both artifacts are now blocked on spatial
   diversity from opposite directions — the classifier needs a second controller-limited
   region, the warm start needs accuracy across a spread it has never seen. One randomised
   collection run addresses both.
3. The unified rollout record (item 4) remains the deeper blocker: five usable warm-start
   examples out of 76 lifetime grasps is a plumbing yield problem, not a hardware-time problem.

The three bench threads carried from 2026-07-30 are still open and unaffected: corridor
occupancy as a recorded precondition, the selector/executor cohort mismatch, and verified grasp
collection on the admitted low-yaw red target near (185, 335).

---

## 2026-08-31 (cont.) — the unified rollout record, and a correction to why the corpus is small

*Added by Claude. Item 4 of the 2026-08-31 recommendation. Suite 100/100. Schema v2 applied to
the live store (backed up first); 1,548 episodes intact.*

### Finding #149 — an episode now describes itself, and measuring the yield problem showed I had named the wrong cause

| # | Finding |
|---|---------|
| 149 | **The substrate records a unified rollout — ordered events, each anchored to the exact robot state and camera frames it was taken with — and building it disproved the premise I had used to justify it.** The claim in #148's roadmap was that "5 usable warm-start examples out of 76 lifetime grasps is a plumbing yield problem, not a hardware-time problem." **Measured, that is wrong.** Of 76 `vision_grasp` episodes: **5 verified successes, 31 verified failures, 38 that ran but recorded no verification at all, 2 errored** — and all 5 of the verified successes extracted cleanly. The extractor loses nothing. The corpus is small because verified successes are rare, which is a hardware-time and grasp-reliability problem, exactly what I said it was not. **But half the corpus carrying no verdict at all *is* a recording gap** — just not the one I named. Those 38 episodes returned before reaching the verification block, so they are invisible to the ablation and to any offline critic: they cannot be counted as successes, cannot honestly be counted as failures, and say nothing about why. The rollout record closes precisely that hole, because `vision_grasp` funnels every executed attempt through a single terminal return, and a verdict event is now written there unconditionally — including when the verdict is `None`, with the note that explains it. An episode that refuses now says so in the same structured place as one that succeeds. |

### What was built

**`events` (schema v2)** — one row per thing that happened: `seq` (the ordering authority),
episode-relative `t`, `phase` (approach / align / grasp / lift / verify / abstain / reset),
`kind` (observation / motion / contact / closure / verdict / refusal), a `label`, a JSON payload,
the **exact `state_t`** of the anchoring states row, and the **exact `frame_ids`** captured with
it. The schema is applied on every `Store` init and is additive, so existing databases migrate on
open; the live store went to v2 with all 1,548 episodes and both prior tables untouched.

**`_record_event`** joins `_context`, `_store`, `_episode_id` and `_log_decision` as a substrate
seam a primitive opts into by declaring it. The `_Rollout` recorder owns the sequence number and
the clock so a body cannot produce an ambiguous ordering, validates phase and kind, and swallows
storage errors — an episode that fails to describe itself is a lost record, never a dropped grasp,
and there is a test that holds that line.

**Instrumentation.** `vision_grasp` records `initial`, `before_close`, `final_pose`, `after_lift`
and the terminal `task_verified` verdict. `vision_grasp_probe` records through its single
`with_axes` funnel, so **every refusal is now recorded as deliberately as every authorization** —
the probe's most informative outcome was previously the one that left the least trace.
`read_camera` returns the frame ids it wrote and `_record_state_snapshot` returns the exact `t` it
wrote, which is what makes the anchoring exact rather than inferred.

**Readers.** `store.rollout(episode_id)` returns the episode with states and frames resolved
inline; `store.event_by_label()` is the targeted lookup; and `rollout_show` exposes it as an MCP
tool, so the deliberate layer can read what actually happened in an attempt — phase sequence,
observation, robot state, verdict — without a Python session on the robot host.

### The join that was right by luck

`warm_start.extract_verified_examples` now reads the `before_close` anchor from the events table
and falls back to the old reconstruction for historical episodes, tagging each example with which
path produced it. Re-run against the live store it returns the same five examples, all tagged
`nearest_timestamp` — the legacy path, still correct.

Worth being precise about why it was correct: the old code matched a state to the *nearest*
timestamp of a frame whose **filename contained the substring `before_close`**, and that worked
only because no competing capture ever landed near one. The retry path writes
`before_close_0`, `before_close_1`, … and the final-pose and after-lift captures land within the
same second; the moment two attempts occur in one episode, "nearest timestamp to a filename match"
starts silently selecting the wrong pose and training on it. A test now builds exactly that
situation — a decoy frame 10 ms away — and asserts the event anchor wins. This is a fragility
fixed before it bit rather than after, which is the cheaper end of the usual order here.

### Honest accounting of what item 4 bought

It did **not** unlock more training data today; #146's yield concern was mine and it was wrong.
What it bought is three things that were going to be needed anyway: the 38 verdict-less episodes
become legible, refusals become first-class records, and the state/frame anchoring stops being a
guess before retries make the guess wrong. It is also the substrate an ER 2-style offline critic
requires — phase labels, event boundaries, and progress are exactly the fields the 2026-07-30
review proposed scoring a frontier reasoner against, and none of them existed until now.

### Where the roadmap stands

Items 1, 2 and 4 are built and tested; the two that remain are the two I cannot do alone.

- **Item 3, collection, needs the bench.** It is now clearly the *only* thing standing between the
  project and a promoted policy: both artifacts are blocked on spatial diversity, and #149 says the
  corpus is small because verified grasps are rare, not because extraction is lossy. Randomised
  target positions during ordinary work, with `warm_start_mode="shadow"` on, is the whole ask.
- **Item 5, the MHS research preview,** is an application Doug has to submit.

The three bench threads from 2026-07-30 remain open and unaffected: corridor occupancy as a
recorded precondition, the selector/executor cohort mismatch, and verified grasp collection on the
admitted low-yaw red target near (185, 335).

---

## 2026-09-01 — seven live grasp attempts, zero successes, and the reason: July hardened `shove`, not `vision_grasp`

*Added by Claude, at the bench, first hardware session since 2026-07-30. The network move put omen
behind a second NAT and the MCP transport was down until it was moved onto the AT&T subnet; the
robot link (192.168.0.253) was never affected. All seven attempts recorded under policy version
`d0c8b03f95` — the first episodes in the project's history with policy attribution.*

### Finding #150 — every failure mode observed today, except one, is a defect `shove` was hardened against in July and `vision_grasp` never received

| # | Finding |
|---|---------|
| 150 | **Seven consecutive `vision_grasp` attempts on an uncluttered bench produced zero verified grasps, and six distinct failures — of which four are verbatim reproductions of problems the July `shove` work diagnosed and fixed in a sibling skill.** July's hardening went into `shove`: adaptive depth-identity gating, median aggregation of raised observations, near-gate verification that accepts a demonstrated convergence rather than descending into a worse view, and a calibrated visual-authorization ceiling at z=0.255. **None of it was back-ported to `vision_grasp` — the skill the entire learning corpus depends on.** That is a far better explanation of "5 verified successes out of 76 lifetime grasps" (#149) than anything proposed before it: the corpus is thin because the collecting skill still carries its June robustness while its sibling moved on. Concretely, today: (1) *discarded converged view* — attempt 4 reached **3.45 px at z=0.2667**, inside the 4 px jaw-clearance tolerance, and descended anyway to 11.17 px and then 23.55 px, refusing at the bottom; this is exactly the "staircase discarded a potentially successful correction" that `shove` fixed on 07-30 with observation-only near-gate verification. (2) *anomalous first-frame depth anchor* — attempt 5 anchored identity to a **58.1 mm** first reading of a 28 mm block and then rejected six stationary frames measuring it correctly at 27.6 mm, ending `target lost or rejected by aligned-depth gate`; the 07-30 entry records the same failure with a 48.3 mm anchor and the same six rejected frames. (3) *neighbour occupancy* — attempt 3 aligned perfectly (final-pose verification **2.9 px**), descended, and stalled **20.6 mm high** with `persistent_high_contact`, closing on air at 62.7 mm: fingers spanning 62.9 mm at 46° yaw onto a block 59 mm away. Same corridor-clearance precondition the 07-30 shove entry called for, unrecorded in the grasp approach too. (4) *unstable colour identity* — the red target's silhouette measured **npix 3293 and 762 on two consecutive runs from an identical pose**, reproducing the selector/executor cohort mismatch on the very target the 07-30 entry admitted for collection. |

### The one new failure: depth top-face corrections do not converge

Not a reproduction — measured for the first time today. The depth top-face alignment stage
corrects, then re-measures, and the re-measurement is frequently *worse* than what it corrected:

| attempt | alignment error sequence (px) |
|---|---|
| 4 | 28.1 → 3.45 → 11.17 → 23.55 |
| 6 | 21.9 → 32.81 |
| 7 | 17.48 → 7.72 → **verified 12.53** |

In attempt 7 the coarse colour servo reported **3.5 px at z=0.28** while the depth top-face stage
reported **17.5 px at z=0.30** in the same episode. The two stages disagree about where the block is
by 10–17 px, and corrections driven by the depth measurement oscillate rather than converge. This is
not reachable by parameter tuning and is the deepest of today's problems.

### What it is NOT: the geometry is intact

The obvious suspicion after two months and a bench that had been moved was that something physical
had drifted. `probe_table` at the plane origin (0.45, 0) settles it:

**contact_z = 0.0164 m, `contacted: true`, Fz −2.55 N, dFz −4.13 N.**

**#144 measured 0.0165 m at the same point on 2026-06-30. The difference is 0.1 mm.** Forward
kinematics and the table reference are exactly where they were when the grasp was working. Whatever
regressed is in perception, not in geometry — which is worth knowing before anyone recalibrates
anything. (The contact moments, Mx 0.634 / My 1.266, sit in the same band #143 found too noisy to
self-calibrate against; consistent with that finding, and uninformative.)

### A parameter that lies

`precision_z` is advertised in the MCP schema as accepting 0.10–0.30, and is then silently clamped:

```python
precision_z = max(0.10, min(float(approach_z), precision_z))     # primitives.py:2124
```

Since `approach_z` defaults to 0.20, a requested `precision_z=0.27` becomes 0.20 with no error and no
note in the result — the alignment record simply reports `precision_z: 0.2`. One experiment was run
and interpreted before the clamp was noticed. Either the schema should declare the coupling or the
call should refuse the combination; silently substituting a different value than the caller asked for
is the kind of thing that makes a whole afternoon's data untrustworthy.

### What the instrumentation did

Item 4 was validated under adversarial conditions: all seven failures produced complete rollout
records, including the refusals, and the `before_close` anchor was captured on real hardware for the
first time — **ee_pose (0.4329, −0.1213, 0.1982)** with gripper width, force, and both frames, on
attempt 3. That is the warm start's training target recorded exactly rather than reconstructed by
nearest timestamp. Every episode also carries `policy_version: d0c8b03f95`, so from today the
ablation has something to attribute.

The record is also honest about the day: seven attempts, zero verified grasps, and the ablation will
show that when it next runs.

### The gate that refuses grasps the hardware would make (Doug: "what would a five-year-old do")

Doug watched the probe land and asked the question that reframes the whole day. A five-year-old does
not align to sub-millimetre before closing their hand — they reach, touch, and let the hand close
around the thing. The arm had just demonstrated it can *feel* the table to 0.1 mm. And yet
`vision_grasp` refuses to touch anything until it is aligned to 4 px.

Following that thought into the code found a defect. `_final_pose_alignment_tolerance_px` computes
the right thing: the physical budget for how far off-centre the jaws may be before a finger overlaps
the block's top face. For today's green block that budget is **≈8.2 px**. Then the return line throws
it away:

```python
return float(min(
    float(requested_tol),                              # <- pixel_tol, default 4.0
    max(3.0, clearance_px - float(uncertainty_px)),    # <- the physics: 8.2
))
```

`requested_tol` is fed `pixel_tol` — the **coarse image servo's convergence threshold**. Two unrelated
quantities shared one parameter, and the stricter one silently wins. **Attempt 4 verified at 6.83 px:
inside the physical budget, refused by the arbitrary cap.** The gripper had its hand in the right
place and would not close because a number said no.

The five-year-old's other lesson is about *where* to stop. Four independent runs today put the
alignment minimum in the same place, and every one of them degrades on the way down:

| attempt | best error | at z | then |
|---|---|---|---|
| 4 | 3.45 px (depth align) | 0.2667 | 11.17 → 23.55 |
| 6 | 10.1 px (coarse) | 0.27 | 32.81 |
| 7 | 3.5 px (coarse) | 0.28 | verified 12.53 |
| 9 | 3.8 px (coarse) | 0.27 | 17.9 → 25.6 → 13.2 |

Attempt 9's **3.8 px at z=0.27 beats even the strict 4 px gate** — and the code then descended to
0.20 and failed at 13.2 px. The skill repeatedly reaches a pose good enough to grasp from and throws
it away, which is precisely what `shove` was fixed to stop doing on 2026-07-30.

One caveat kept honest: raising `pixel_tol` to 10 did not rescue the run, because that same parameter
also governs the coarse servo's own convergence check, which then failed at the bottom. The parameter
is overloaded three ways. It cannot be untangled from the outside.

### The unresolved core: two perception stages that disagree

In attempt 7 the coarse colour servo reported **3.5 px at z=0.28** while the depth top-face stage
reported **17.5 px at z=0.30** in the same episode. Both are measuring the same block. Until that
disagreement is understood, tightening or loosening either gate is guesswork — and #141 chose the
depth top face for a reason (a standing block's colour centroid is pulled toward its lit side face),
so the answer is probably not simply "trust colour".

### Next

1. **Stop capping the physical jaw-clearance budget with `pixel_tol`.** They are different
   quantities; give the jaw gate its own parameter and let the physics govern it. Smallest change
   here, and on today's evidence it alone would have let attempt 4 close.
2. **Back-port the three `shove` mechanisms into `vision_grasp`** — near-gate verification, median
   aggregation of the initial depth observation instead of a single-sample anchor, and corridor /
   neighbour clearance as a recorded precondition. All three are already calibrated on this hardware;
   this is transplanting proven code, not new design.
3. **Then investigate the coarse/depth disagreement and the non-convergent depth correction**, which
   none of the above addresses.
4. Only then resume collection. Running `practice` on the current `vision_grasp` would generate
   failures, not a corpus.

---

## 2026-09-01 (cont.) — ab initio: rebuilding perception from scratch, and what it exposed

*Added by Claude. Doug's instruction was to set everything aside and build two functions from
nothing: one that says where the target is in the image, and one that loops movement commands
until the gripper straddles it, re-looking every iteration. Doing that took four hours of
accumulated assumptions out of the loop, and three of them turned out to be wrong.*

### Finding #151 — the servo was never the problem; the map between the camera and the hand is

| # | Finding |
|---|---------|
| 151 | **A from-scratch locate-and-servo loop converged to 0.8 mm — an order of magnitude better than the production pipeline managed all day — and the grasp still closed on air, which localises the fault precisely: image-space alignment is excellent and the camera-to-hand map is wrong.** The ab initio loop reached **1.3 px (0.8 mm)** in two corrections (154 → 25 → 1.3 px) against production's best of 3.45 px. It then failed at contact exactly as the production skill had, stalling with the fingertips **26.5 mm** up against a **24.9 mm** block. Eight touch probes followed, at every position implied by `_vg_grasp_pixel`, by two bisections toward the camera axis, by the camera axis itself, by the stored `calib_result_2026-06-09.json`, and at four points around the pose that put the block dead-centre in frame. **All eight hit bare table** (contact_z 0.0170–0.0181). Meanwhile `probe_table` at the plane origin read **0.0164 against June's 0.0165**, so forward kinematics and the table reference are intact to 0.1 mm. Kinematics sound, image servo sound, and the transform between them broken — one fault that explains every failure of the day, including why the production skill aligns to 2.9 px and then descends somewhere else. |

### Three assumptions measured and found wrong

**The pixel-to-metre gain is 11% too high.** Two 40 mm test moves gave a full Jacobian:

```
J = [[ -31, -679],     du/dx  du/dy
     [-683,  -18]]     dv/dx  dv/dy     px/m
```

Clean and symmetric — u tracks y, v tracks x, 3–4% cross-coupling (≈2.3° of camera yaw). But
both dominant gains are **681 px/m where the pinhole model on the measured depth predicts 765**.
The effective focal length is 353.6 px, not the 394.5 px the intrinsics report. Every correction
computed from `fx/z` overshoots by 11%.

**Fixed colour thresholds are not stable across the frame.** The first locate() lost the block
completely at the image centre: it separates from blue by up to 82 counts but from red by as
little as **24**, depending where it sits. Replacing the thresholds with the target's own
normalised chromaticity — learn what it looks like, then find that — gave npix **729 / 765 / 762**
across three poses, against the production path's **3293 vs 762 from an identical pose**. The
production `npix` instability is a thresholding artefact, not a perception limit.

**Both sensors are worse far away, not closer.** Descending straight down over a fixed block:

| EE z | camera→block | chroma_g | npix | depth height (true ≈28 mm) | depth σ |
|---|---|---|---|---|---|
| 0.479 | 0.506 | 0.497 | 833 | **8.2 mm** | **146 mm** |
| 0.398 | 0.420 | 0.538 | 1392 | 23.5 mm | 10 mm |
| 0.337 | 0.355 | 0.541 | 1946 | 19.1 mm | 4.7 mm |
| 0.277 | 0.294 | 0.546 | 2895 | 25.2 mm | 4.8 mm |
| 0.228 | 0.243 | **0.558** | **4231** | **25.5 mm** | 7 mm |

Colour recovers toward its reference and gains 5× the pixels; depth goes from a 146 mm-σ reading
that calls a 28 mm block 8 mm tall, to a stable 25.5 mm. **The pipeline takes its identity anchor
and its geometry at acquire_z=0.45, the one distance where both sensors are worthless**, and then
spends the episode defending that measurement against better ones. This is the mechanism behind
attempt 5's 58.1 mm anchor and the 07-30 entry's 48.3 mm one — not bad luck, the expected σ at
that range. (Worth confirming against the datasheet: at EE z=0.45 the camera sits ~0.52 m from the
table, which may be at or past the D405's rated maximum.)

### On borrowed constants

The ab initio loop was honest right up to the point where it needed to know where the gripper
is, and there it borrowed `_vg_grasp_pixel` — the one constant the day's evidence already
implicated. The alignment to that target was excellent and the target was wrong. Worth recording
as a methodological note: the value of building from scratch was entirely in the parts that were
rebuilt, and the single borrowed constant is where it failed.

The stored hand-eye file is no better. `calib_result_2026-06-09.json` places the camera origin
17 mm from the tool axis, which is plausible, but its rotation puts the camera's x-axis **21.6°
away** from where the measured Jacobian puts it, and the block position it predicts sits **15 mm
below the table**. Probed, it is bare table.

### What was built instead

Doug's framing settled the design: *"No one has to calibrate a toddler; they do it themselves"*,
and *"bench time is there to burn — our incentive is to use the hardware more, not less."*

`touch_find` (`skills/selfcal.py`) is the toddler loop. Look from close range, predict where the
target is, then spiral-probe outward with the fingers nearly shut — a point probe — until they
stop above the table. The table plane comes from the run's own low contacts rather than a stored
constant, so a drifted table cannot corrupt it. What it returns is the **see-versus-feel
residual**, which is the correction itself. The search is deliberately brute, up to 121 probes
over ±90 mm, because that is the trade the bench allows: many cheap probes in exchange for
deleting a constant nobody trusts.

`perception/target.py` carries the validated chromaticity perception, and the measured
`EFFECTIVE_FX = 353.6` replaces the intrinsic value in the seed prediction. Nothing in either
module reads `_vg_grasp_pixel` or the June calibration file.

### The toddler loop ran, and broke on something worth knowing

`touch_find` worked as a mechanism on its first live run. It looked, predicted, spiral-probed
**51 times**, and found an object: five hits reading contact_z **0.0441–0.0446** against a
self-measured table plane of 0.0173 — an object **27.0 mm** tall, matching the green block's 28 mm.
No stored constant was consulted anywhere in that chain. The see-versus-feel residual came out at
**76.5 mm**, which if it were a clean camera-to-hand offset would be the correction the whole day
had been missing.

It is not clean, and the reason is the finding:

| # | Finding |
|---|---------|
| 152 | **RETRACTED — see #153. This finding was wrong.** It claimed a point probe had walked the block across the table, inferring that from a touched footprint of 45 x 54 mm which seemed too large for one 60x26 mm block. Doug disproved it in one call, using the obvious check this entry never ran: return to the pose the scene was first photographed from and compare. All three blocks were within 1.2-2.7 px of where they started, far inside the 9 px threshold #145 set for calling something moved. Nothing had been touched at all in the sense claimed. The footprint was never anomalous: it is the block dilated by the ~20 mm fingertip width, which is what any probe of finite width measures. The error was not the inference but the order of work — a satisfying mechanism was written up before the cheapest available check was run against it, which is exactly the failure #141 named ("measuring instead of theorising") reappearing in the same notebook. |

So the day ends with the fault localised and the instrument for measuring it built and demonstrated,
but the correction itself not yet honestly measured. What is established: kinematics are sound to
0.1 mm, image-space servoing reaches 0.8 mm, both sensors are far better close than far, the
colour instability is a thresholding artefact, the gain is 11% high, and the camera-to-hand map is
wrong by an amount large enough to put the fingers on bare table every time. What is not
established is the correction, because every attempt to measure it so far has moved the target.

### Next

1. **Add the visual guard to `touch_find`**: observe, probe once, re-observe, and keep the
   correspondence only if the target's image position is unchanged. Discard and re-seed if it moved.
2. **Prefer a target that cannot be pushed** for the calibration itself — a heavy object, or a
   feature of the table — and keep blocks for verification rather than measurement.
3. Collect several guarded correspondences across the workspace and fit the correction, then store
   it as a promoted artifact through the registry, so the calibration becomes something the arm
   re-learns on its own under the same promotion gate as every other distilled policy.

---

## 2026-09-01 (cont.) — the block is picked up, and calibration turns out to be optional

*Added by Claude, correcting the two preceding entries. Doug caught both errors from the bench.*

### Finding #153 — touch found the block on its first run, and the grasp needed no hand-eye calibration at all

| # | Finding |
|---|---------|
| 153 | **`touch_find` located the green block correctly on its first live run, the block was grasped and lifted from the position it reported, and the 76.5 mm camera-to-hand error was never repaired — it simply stopped mattering.** Everything the search reported was right and was misread. It found an object **27.0 mm** tall (the block measures 26.5 mm by the same probe), centred near **(0.404, −0.188)**, with a touched footprint of 45 x 54 mm that is not anomalous at all: it is a 55 x 28 mm block dilated by the ~20 mm fingertip, which is what a probe of finite width always measures. The two readings that were treated as contradictory are the signature of a real block: with fingers nearly shut the descent stops at **0.0440** because they land on the block's top, and with fingers open to 60 mm it reaches **0.0197** because they straddle it and go down either side. A contact height that depends on finger width is exactly what a solid object of known size produces. Grasping at that touched position with grasp_z 0.027 succeeded first attempt — `grasped: true`, gripper closing to **29.0 mm** on the block's short axis, `grip_high_mm` 2.2, lifted and held at 29.2 mm with 6.4 N of load. **The significance is what was not required: no hand-eye transform, no `_vg_grasp_pixel`, no re-calibration.** `touch_find` reports the target in robot coordinates, which is the only frame the grasp needs; the camera was used solely to seed the search within ~10 cm, and touch resolved the rest. The day's plan had been to repair the calibration so the camera could aim the hand. That plan was wrong in an instructive way — a system that feels does not need the camera to be accurate, only approximately right, and the calibration becomes a convenience rather than a dependency. |

### The methodological failure, recorded plainly

Two findings were written today from the bench, and one of them (#152) was false. Its content was a
mechanism — "the probe moves its subject" — invented to explain a footprint that required no
explanation, and committed before the cheapest available check had been run against it. Doug ran
that check in one call: go back to the pose the first photograph was taken from, take another, and
compare. All three blocks were within 1.2–2.7 px of their starting positions.

Both of the day's wrong turns share a shape. The parallax hypothesis, the "probe moves the block"
hypothesis, and the reading of the straddle as evidence *against* an object were all cases of a
story being fitted to partial data and then defended, when a measurement was cheap and available.
#141 already named this failure — *measuring instead of theorising* — and it recurred here in the
notebook that records it. The bench is not the scarce resource; the discipline to check before
writing is.

### What this changes about the roadmap

The camera-to-hand error is real and unrepaired, and #151's other measurements stand: the gain is
11% high, both sensors are better close than far, and colour instability is a thresholding
artefact. But the priority order inverts. Repairing the calibration is no longer the blocking item,
because a grasp does not depend on it. The higher-value direction is to make `touch_find` the
normal path to a grasp: seed from vision, confirm by touch, grasp from the touched coordinates —
and let the accumulated (see, feel) pairs improve the camera model in the background as a learned
artifact, gated by the promotion rules, rather than as a procedure anyone has to run.

---

## 2026-09-01 (cont.) — the local VLM, scored offline against hardware truth

*Added by Claude. The 2026-07-30 ER 2 entry specified how a frontier reasoner should first be
evaluated here: "offline, preserving the distinction between model judgment and hardware truth",
scored on "whether its labels are calibrated enough to improve retrieval, episode triage, or
subsequent live success over our current structured heuristics." That test is now run, against a
local model, on frames whose outcomes the bench already settled.*

### Finding #154 — qwen2.5vl:7b enumerates correctly and judges geometry wrongly, including the one safety call that mattered

| # | Finding |
|---|---------|
| 154 | **The local VLM is accurate about *what* is on the table and unreliable about *where* and *whether* — and on the single frame where a geometric judgment had a known hardware answer, it got it backwards.** Model: `qwen2.5vl:7b` (6.0 GB, ollama 0.32.6), ~2–6 s per query on the RTX 5060 Ti, running comfortably alongside cuRobo (546 MB of 16 GB). **Enumeration is right:** on the isolated-green frame it reported one green block; on the crowded frame it reported green, red and blue, matching the scene. **Geometry is wrong:** it called every block "standing" when all were lying flat (the successful grasp closed on 29 mm, the short axis of a lying block), and asked whether the nearest neighbour was clear it answered `true` on **the exact frame whose grasp failed because a finger landed on a neighbour 59 mm away**. A wrong "clear" is the dangerous direction — it authorises the contact that the day's #150 catalogued as a real failure mode. **Localisation is coarse but not useless:** raw pixel output is meaningless (v=502, 715 on a 480-row image) because the model emits normalised 0–1000 coordinates; decoded, it lands **40–68 px** from the chromaticity tracker across three frames, roughly 20–35 mm. That is far too coarse to grasp from, where the requirement is single-digit millimetres — but the coarse touch search steps at **45 mm**, so it is precise enough to *seed* one. |

### The role this argues for

Not a critic, and certainly not an authority over contact. A **semantic seed**. `touch_find` currently
starts from a colour threshold that only knows blue, green and red; a VLM seed accepts "the green
block", "the one nearest the edge", "the block that is not like the others" and lands inside the
coarse search radius, after which touch resolves the millimetres. That is the same division of
labour the day arrived at everywhere else: vision to get approximately right, contact to get exactly
right. The model's confident wrong answers about clearance and pose are precisely the questions
touch already answers correctly and cheaply, so there is no reason to ask it.

Recorded also as a caution about the ER 2 roadmap in the 07-30 entry: the proposal there was to let a
strong reasoner "select among a small whitelist of no-contact observations/probes" once an offline
gate passed. On this evidence a 7B local model does not pass that gate for any geometric field, and
the useful integration is narrower and further from the arm than the roadmap assumed.

---

## 2026-09-01 (cont.) — the practice loop's own data gives the calibration, and says why the correction could not converge

*Added by Claude while `practice_touch` was still running. The loop's (see, feel) pairs were being
written to the events table by the rollout record built this morning, so they could be analysed
mid-run without touching the arm.*

### Finding #155 — the camera-to-hand error is a rotation and scale, not an offset; a running mean was structurally incapable of learning it

| # | Finding |
|---|---------|
| 155 | **Four touch correspondences are enough to show that the error scales with distance from the image centre and is rotated, which is why the accumulated translation correction did not converge and the loop did not get faster.** `touch_find` learns a running-mean offset. Its four residuals were **(−48.1, −30.0), (−80.9, −77.0), (−107.5, −12.5), (−129.8, −96.8) mm** — a spread of 82 mm in x and 84 mm in y, as large as the mean itself. Fitting the measured offsets against the predicted ones: **translation only leaves 45.8 mm rms; a full affine leaves 17.1 mm**, and the fitted matrix has both eigenvalue magnitudes equal at **0.8293**, i.e. it is a rotation-plus-uniform-scale rather than an arbitrary affine — **scale 0.83, rotation ≈14°**. An error proportional to the target's distance from the principal point cannot be absorbed by a constant, so the running mean was chasing a quantity that changes with every observation. The loop was therefore doing ~6.75 min per find with no downward trend, which is the same fact seen from the outside. |

### The measurement that misled me, and why touch beat it

Earlier today the image Jacobian was measured from two 40 mm robot moves and reported here as
"clean and symmetric, u tracks y, v tracks x, 3–4% cross-coupling, ≈2.3° of camera yaw". The
diagonal terms of that measurement are sound — 27 px of image motion over 40 mm of travel is a
strong signal, and the 681 px/m gain it gave is why the servo converged to 0.8 mm. **The
cross-terms were not.** They amounted to about **1 px** of motion over the same 40 mm, which is
below the noise of a colour centroid, and a 14° rotation was read as 2.3° from that noise. The
touch data resolves it because it works over a 200 mm baseline against a hard contact, not a
one-pixel difference against a segmentation.

The general lesson is the one this notebook keeps relearning in new costumes: a quantity can be
measured, reported with a number, and still be noise. The diagonal and the off-diagonal of the same
matrix, measured in the same experiment, had completely different signal-to-noise, and nothing in
the presentation of the result said so.

### Consequence for the design

`touch_find` should fit a similarity (rotation + scale + offset) over its accumulated
correspondences rather than averaging an offset, and it should refuse to apply the fit until the
residual is small enough to be worth applying — the same promotion-gate discipline as every other
distilled artifact here. On four points the affine fit is close to underdetermined (8 equations, 6
parameters, 17 mm rms), so the honest gate would still reject it; but it is already three times
better than the model in use, and it is the right shape.

The deeper point stands from #153: none of this is required to grasp. Touch finds the block in
robot coordinates whatever the camera model says. A better camera model buys **search time** — a
smaller radius and fewer probes — not capability.

---

## 2026-09-01 (cont.) — the bracket was loose; #155's constants are expired, and no stack was achieved

*Added by Claude. Recorded before the GEN-1.5 note because it invalidates a constant committed
earlier the same day, and a notebook carrying a known-wrong number is worse than one with a gap.*

### Finding #156 — the camera mount was physically twisted, which is probably what "the camera-to-hand rotation" was measuring

| # | Finding |
|---|---------|
| 156 | **Doug noticed the wrist camera's mounting bracket was twisted and straightened it. That almost certainly *is* the ~14° rotation #155 fitted and attributed to geometry, which means every camera-side constant fitted on 2026-09-01 is expired.** #155 fitted a similarity to four touch correspondences and reported scale 0.83, rotation ≈14°, residual 17.1 mm against 45.8 mm for a translation model. The conclusion that the error is *not* a constant offset still stands — that was established from residuals spreading 82 mm, which no mounting angle explains away. But the specific matrix is now wrong in a new direction, and after the bracket was straightened it will over-rotate every prediction. **The wider point is worse and more useful: a loose mechanical part was being modelled as if it were a fixed property of the geometry.** Four hours went into fitting increasingly sophisticated transforms to a bracket that was moving. Nothing in the software could have caught this, because the model had no term for "the mount might not be rigid" and no way to notice that its own residuals were drifting rather than scattering. The residual *history* — not the mean, not the fit — was the signal, and it was recorded but never examined as a time series. |

### And the honest record on stacking: none achieved

Across roughly two hours the arm grasped blocks repeatedly and well — the best grasps closed to
29.0 mm on the short axis at 2.2 and 4.6 mm above the intended height — but **no verified stack was
produced.** The final attempt picked a block up and set it down 40 mm away on bare table, verified at
29.1 mm, one block tall. Its cause is a one-line error: the touch clustering links hits within
**45 mm**, and the blocks are **55 mm long**, so a single block reliably presents as two objects and
the arm dutifully "stacked" one onto the other half of itself. The cluster radius has to exceed the
longest object dimension, not the probe spacing.

The failure chain over the session is worth listing plainly because none of it was about learning or
policy, and all of it was geometric bookkeeping:

| failure | cause |
|---|---|
| fingers on bare table | camera-to-hand offset, itself partly a loose bracket |
| release bounced the block off | drop height 14 mm instead of 3 mm |
| block "walked" (#152, retracted) | never happened; the check was never run |
| grasp stalled 17 mm high | 80 mm pre-open with the neighbour 78 mm away |
| grasp stalled 8.7 mm high | wrist yaw closing along the block's long axis |
| orientation measured wrong | touch extent probe reached the neighbour |
| "two objects" that were one | cluster radius 45 mm < block length 55 mm |

Seven distinct defects, every one of them in the explicit-geometry pipeline, none in perception or
control as such. That observation is what makes the next entry uncomfortable rather than reassuring.

---

## 2026-09-05 — literature: GEN-1.5, and the sharpest challenge to this project's premise so far

*Added by Claude, at Doug's request. Primary source: Generalist AI, ["GEN-1.5"](https://generalistai.com/blog/gen-1.5).*

### What it is

A robot foundation model taking a 30-second video context with proprioception and language and
emitting 100 Hz action trajectories. Roughly eight months of continuous pretraining on real-world
interaction data captured in homes, warehouses and factories, with **no simulation data**. The
headline claim is *emergent in-context learning of physical skills*: **59% ±10%** one-shot across ten
manipulation tasks from a 3–12 s demonstration with **zero gradient updates**, rising to **83% ±9%**
with ten gradient steps on ~50 demonstrations. The authors emphasise that none of this was designed
for — "no architectural changes to promote in-context learning, no inner or outer meta-learning
loop." It emerged from scale.

The qualitative results are more striking than the numbers: ambidexterity from single-hand training
data, zero-shot sim-to-real despite no simulation in pretraining, human-hand-to-robot transfer, and
improvising a banana as a brush — composing a contact sequence absent from training.

### Finding #157 — GEN-1.5 is the strongest published argument against this project's premise, and this week's failures are evidence for its side

| # | Finding |
|---|---------|
| 157 | **GEN-1.5's list of things it does not claim — no world model, no persistent memory, no lifelong learning, no RL, no long-horizon planning — is an inventory of nearly everything Hebbot is built out of, and it reaches 59% one-shot on novel manipulation tasks anyway.** If skill acquisition emerges from scale plus demonstrations, the episode store, the promotion gates and the verification machinery are scaffolding on the way to something that did not need them. **The honest concession is that essentially nothing that defeated this project on 2026-09-01 was a policy-learning problem.** The seven failures above are all explicit-geometry bookkeeping: a hand-eye offset, a loose bracket, a drop height, a pre-open width, a wrist yaw, a probe threshold, a clustering radius. **A behavioural-cloning policy has no hand-eye transform to get wrong** — it maps pixels to actions and never represents the quantity that was miscalibrated. GEN-1.5 might plausibly have stacked these blocks during the hours spent fitting similarity transforms to five touch correspondences. That is the case against the substrate, stated at full strength, and it should stay stated. |

### What it structurally cannot do

The counter is equally concrete, and this week supplies it rather than theory.

GEN-1.5 has **no way to notice that the bracket moved**, no way to accumulate the fact, and no way to
report it. Its context is a 30-second rolling buffer; nothing survives it, and improvement comes from
more human demonstrations rather than from its own experience. It cannot form the proposition "my
camera disagrees with my hand by 76 mm", because it has no representation in which the camera and
the hand are separate things that could disagree.

It also has **no verification**. 59% one-shot is four failures in ten, and nothing in the
architecture distinguishes a success from a failure after the fact or explains one. Their own note
that in-context skills are "more brittle than finetuned models" is the admission that matters most.

And a gap visible only from a week spent on contact: **the post never mentions force or touch as a
sensing modality.** It is vision plus proprioception. Every reliable measurement made here came from
touch — `probe_table` reproduced June's table height to **0.1 mm** across two months, a network
change and a camera remount, while vision was silently 76 mm out. A policy trained without contact
as a sense inherits exactly the blind spot that broke this pipeline, and has no touch-based recourse
when vision is wrong.

### The synthesis worth pursuing

GEN-1.5 reads less as a refutation of the layered thesis than as a strong candidate for the layer the
thesis predicts. This project's framing has always been deliberate cognition compressing into
reflex. GEN-1.5 is an excellent reflex with no deliberate layer, no memory, and no self-knowledge;
the substrate here supplies precisely those. The productive question is therefore not which approach
wins but **which failures each one owns**: this week's were geometric bookkeeping, fully attributable
and each fixed by a line of code, while GEN-1.5's are the unexplained 41% — unattributed and
unrepeatable, because nothing recorded why.

### Reading it critically

Undisclosed: architecture, data scale, hardware platform, and any baseline against π0- or
RT-2-class models. Figure 3 shows validation loss, not a scaling law. **59% ±10% across ten tasks**
is a wide interval on a small n, and with plausibly 10–20 trials per task that error bar carries a
lot of weight. The emergence claim rests on qualitative demonstrations that are genuinely impressive
but singular — one banana, one sim-to-real transfer — and singular demonstrations are what this
notebook has repeatedly learned to distrust in its own work.

---

## 2026-09-06 — literature: a third-party LLM-on-robot-arms benchmark, and the precision cliff

*Added by Claude at Doug's request. Source: ["GPT-6 Astra on Robot Arms"](https://openai.robocurve.org/gpt-6-astra/),
dated 2026-09-04.*

**Provenance caveat, stated first because the URL invites the wrong assumption.** `openai.robocurve.org`
is a subdomain of **robocurve.org**, a third party; the `openai.` prefix is theirs, not OpenAI's, and
this is **not an official announcement**. It is an independent benchmark report whose numbers cannot
be verified from here, and it discloses nothing about the tested model's architecture, training data
or method. It is recorded below for the *shape* of its result, not for its rankings.

### What it measures

A language model commanding **absolute end-effector poses** from three camera views (top, two wrist)
plus proprioceptive state, on bimanual 6-DoF I2RT YAM arms with parallel-jaw grippers, under a
**20-LLM-call budget** at medium reasoning effort. That is structurally the same loop this project
ran by hand on 2026-09-01: look, reason, command a pose, repeat.

| task | Astra | Fable 5.1 | Fable 5 |
|---|---|---|---|
| block into bowl | 19/20 (95%) | 8/20 (40%) | 1/20 (5%) |
| puzzle piece into groove | 2/20 (10%) | 2/20 (10%) | 0/20 (0%) |

Cost $0.94–$2.69 per run; 2.5–8.2 minutes per run.

### Finding #158 — every model collapses at the same tolerance boundary, and none of them can feel

| # | Finding |
|---|---------|
| 158 | **The informative number is not the ranking but the cliff: 95% → 10% between two tasks on the same rig with the same model, and every model tested collapses in the same place.** A bowl forgives roughly 20 mm of placement error; a groove forgives about one. The failure is therefore not a reasoning failure — it is that open-loop vision-to-pose cannot close the last few millimetres, however good the reasoning above it. **This reproduces exactly the boundary hit here on 2026-09-01**, where grasps succeeded on isolated forgiving targets and failed with 8.7 mm and 17 mm stalls the moment placement had to be exact, and where the measurement that finally worked was `probe_table` reporting contact to **0.1 mm**. **The benchmark's embodiment lists three cameras and proprioception and no force or contact sensing** — the same omission as GEN-1.5 (#157). Two independent systems, different architectures, both stuck at the same precision boundary, both blind in the same modality. That is strong external circumstantial support for the touch thesis, and it should be held loosely, because neither source set out to test it and neither reports having tried. |

### The economics, which this project has never had a number for

The cost figures make the compression argument quantitative rather than philosophical: **deliberate
control of a pick-and-place costs roughly $1–3 and 2.5–8 minutes at 20 LLM calls.** If a distilled
reflex costs microseconds and nothing, the value of moving a skill from the deliberate layer to the
reflexive layer is measurable in dollars per pick. That is the y-axis the ENPIRE-style improvement
curve (#146) has been missing, and it belongs in the scorecard.

It is also an indictment of this session's own conduct. **Hundreds of tool calls went into a single
grasp here.** A 20-call budget would have forced coarse-to-fine search from the outset — the
discipline Doug had to prompt twice ("what would a five-year-old do", "you could binary search
that"), and which when finally applied cut the search from 26 probes to 2. A call budget is worth
adopting as a *discipline*, not merely as an economy.

### Reading it critically

The report is honest about its weaknesses and they are material: trials **not interleaved**, run on
**different rigs** (rig-1 vs rig-3), **operator grading with model identity visible**, list-price
costing that may overstate one model's true cost, and medium reasoning effort only. At n=20 per
cell, 19/20 against 8/20 is a real difference; **2/20 against 2/20 is noise**, and should be read as
a shared floor rather than a tie.

Recorded without comment on the rankings, which include a Claude family member and are not in
dispute here; the listed caveats apply symmetrically to every cell.

### What to take into the project

1. **The precision cliff is the target** — not better planning, but a contact-based last centimetre.
   This is what the touch work is actually for, and there is now an external benchmark shape to aim at.
2. **Adopt a bounded call budget** as a forcing function for efficient search.
3. **Put the cost-per-deliberate-execution into the ENPIRE scorecard** as the denominator for the
   distillation argument.

---

## 2026-11-09 — omen would not boot: a lost NVIDIA patch left the realtime kernel with no initramfs

*Added by Claude. Diagnosed from omen's apt, dpkg, DKMS and journal logs; the fix was verified by
rebooting into the default kernel. Doug recalled the workaround of booting the previous kernel, and
the logs bear that out: repeated reconfigure attempts on 2026-09-30.*

### Finding #159 — a manual patch lived inside a package-owned folder, and its loss presented as a kernel panic

| # | Finding |
|---|---------|
| 159 | **omen panicked on its GRUB default, `7.0.0-34-realtime`, because that kernel had no initramfs; the root cause was the loss of a hand edit, not a kernel or hardware fault.** The PREEMPT_RT bypass (`IGNORE_PREEMPT_RT_PRESENCE=1`) was a manual edit to `/usr/src/nvidia-595.84/dkms.conf`. The update to driver 595.91.07 replaced that folder, so DKMS began failing `preempt_rt_sanity_check` on every realtime kernel. `/etc/kernel/postinst.d/dkms` exited 1, run-parts stopped before the initramfs hook, and `/boot/initrd.img-7.0.0-34-realtime` was never written, while GRUB still listed the kernel as default. **The quieter consequence is that no NVIDIA module loaded on the realtime kernels after the boot ending 2026-09-11** (the last was `7.0.0-30-realtime` with 595.84). The last bench work recorded here was 2026-09-01, inside that boot, so no experiment in this notebook ran without the GPU. |

**Fix.** `/etc/dkms/nvidia.conf` injects the bypass from outside the driver folder;
`dpkg --configure -a` then built NVIDIA for `-34-realtime`, generated the initramfs and cleared the
half-configured packages. NVIDIA was also installed for `-31-realtime` so the fallback kernel has a
GPU. `/etc/apt/apt.conf.d/51hebbot-hold-kernel` excludes `linux-`, `nvidia-`, `libnvidia-` and
`xserver-xorg-video-nvidia` from unattended-upgrades.

**Verified after reboot:** `7.0.0-34-realtime`, `nvidia-smi` reporting the RTX 5060 Ti on driver
595.91.07, `systemctl` state `running`, `ulimit -r` 99, no half-configured packages, and the Franka
controller answering ping at 192.168.0.253.

**Not verified:** the override has not yet been exercised by a real driver package update, and the
arm, FCI control, the cameras and cuRobo have not been run since the reboot. Kernel and NVIDIA
updates on omen are now manual, including security updates. `docs/panda-setup.md` still describes the
earlier Kbuild-edit method.

---

## 2026-11-09 — to read: RoboDojo

*Noted by Claude at Doug's request; not yet read.* [RoboDojo](https://robodojo-benchmark.com/)
([arXiv 2607.04434](https://arxiv.org/abs/2607.04434)) is a sim-and-real benchmark for generalist
manipulation policies. Search summaries describe 42 Isaac Sim tasks and 18 real tasks, scored on
generalization, memory, precision, long-horizon and open-vocabulary axes, with remote access to
standardized hardware. Questions to answer when reading it: can an individual enter, and does an
agent-plus-skills system fit its policy interface?

---

## 2026-11-09 (cont.) — the first touch_stack runs: the probe was lying, then the block was

*Added by Claude, at the bench with Doug. `touch_stack` ran twice in `measure_only` on hardware;
neither run produced a usable pose for both blocks, and no grasp was attempted. Doug saw the blocks
shift during probing before the photos confirmed it.*

### Finding #160 — a light block yields to a probe coming down beside it, so "the probe read table" does not mean nothing was there

| # | Finding |
|---|---------|
| 160 | **Thirty probes along two lines read bare table every time, yet the blocks visibly turned as the lines passed them: green by very roughly 60 degrees and 61 px in u, blue by about 45 degrees and 49 px, photographed from one fixed pose after every probe, while look-only repeats differed by at most 2 px and the block not being probed stayed still.** The test was meant to compare contacts on the block's top against contacts at its edge, and it did not do that: the seed was wrong and no probe landed on a top. What it showed instead is worse for the method. A fingertip descending alongside a block can turn it aside and still arrive at the table, reading as an ordinary miss. The contact that moved the block is invisible in the height reading. The likely mechanism, not established here, is the fingertip side catching a corner on the way down. It also retroactively explains why both `measure_only` runs reported blocks larger than they are: the target moved during its own measurement. Unlike #152, this one was checked first, and Doug saw it happen. |

### Two probe defects found on the way

**False stops were a force-zero error, not the hover height.** The first run had 8 impossible
readings in 114 probes. An interleaved test of 60 bare-table probes showed none at the 0.09 m hover
and two at the old 0.16 m. `probe_table` zeroes force before the controller engages; once engaged, the
estimate shifts by up to about 5 N in free air, so the 6 N trigger rides at its threshold. Free-air
tracking lag peaked at 7.4 mm against the 10 mm lag trigger, so `touch_stack` now relies on lag.
The same flaw existed in the placement descent, where a false trip would drop a block; it now
averages its zero after the shift and requires 50 ms of sustained force. That descent has not yet
run on hardware.

**Table readings with a 0.012 m floor were floor plus lag.** About 5.5 mm of tracking lag lands the
hand near 0.0175 whether or not a table is there, and 57 of 60 probes stopped this way. With the
floor at 0.008 the table reads 0.0162, against 0.0165 from June. Treat earlier table heights taken
with `z_floor=0.012` as not being contact measurements.

### What holds and what does not

Block tops read 0.0443-0.0462 m across 70 contacts with no false stops in the second run, and the
skill refused its bad measurement instead of acting on it. But finding edges by descending near
them does not work on blocks this light. Whether a probe landing squarely on top also moves one is
still untested. #153's conclusion needs narrowing: touch found a block and grasped it, but touch
near an unsecured light object disturbs it.

---

## 2026-11-09 (cont.) — camera for the pose, touch to confirm

*Added by Claude, at the bench with Doug. No grasp attempted yet; the blocks were taped down.*

### Finding #161 — with the block fixed, touch measures well, and it calibrates a camera that then needs almost no touch

| # | Finding |
|---|---------|
| 161 | **On two taped blocks, edge-finding gave a 1 mm rectangle fit, the right length (56 and 55 mm against 55) and the right yaw. Those two touch-measured positions then calibrated the camera from 60 no-contact photographs, and a far look followed by a re-look from directly above located a block held out of the fit to about 6 mm.** Far views on the held-out block were 6-19 mm; accepted close looks were 4.7-7.6 mm (one first look at 13.5 mm, corrected to 6.2 mm by the second). The complete skill then measured both blocks in 69 s and 7 probes rather than 14.6 minutes and 146, landing 3.2 and 1.3 mm from the touch positions. **Those last two numbers are in-sample, because the map was fit on those same blocks; the honest figure is the held-out 6 mm.** |

The learned-chromaticity detector failed up close: glare washed out the patch it learns from until
the table matched it, once producing a single 200,000-pixel blob. A size limit alone left bad
frames, so close views now use the first-pass colour seed, which was within 20 px of image centre
in all 32 saved close frames. Readings that jump more than 60 mm are discarded and re-looked at.
That limit was added after an earlier test script drove the arm up to about 0.4 m off target on a
bad detection, at safe height, and stopped only when inverse kinematics refused a target.

The camera map lives in `hebbot-data/camera_map.json` and is valid only while the mount is unchanged
(#156). Probing footprint is 20 mm along x and 37.5 mm across y, which biases touch-mode yaw at
diagonal angles and widens the gap it needs along y; touch mode is reserved for fixed targets.

Not yet shown: a real grasp and placement from these poses, the placement descent on hardware,
whether a centre probe moves a loose block, and accuracy on a third, independent block.

**Later the same day: the first `touch_stack` stack.** With green taped as the base and blue loose,
the skill picked blue and set it on green in 126 s and 10 probes: grasp closed at 29.0 mm, placement
contact 0.1 mm from prediction, pile top 0.0753 m against 0.0754 expected, and the wrist photo shows
blue on green. Six empty free-air descents first produced no false contacts. One trial, with a fixed
base. Its log exposed a defect now fixed: the first verification probe returned a cached pre-placement
reading, so only the second reading actually tested the pile. Doug inspected the stack by eye and
found blue centred on green; that is a visual check, not a measured offset.

## 2026-11-09 (cont.) — scene memory: beliefs with error bars, not a map

Doug raised the day's design question: coding agents work partly because of external memory,
and the robot needs its equivalent — muscle memory ("the cup only fit by its handle, wrist at
90°"), spatial memory that is sometimes vague ("over there to the right"), and a fourth
dimension, since things move. The day had already run the experiment twice: the stale
verification cache was perceptual memory failing (a reading outlived the action that
invalidated it), and the camera map is perceptual memory working (60 observations compressed
into an affine map stamped "valid while the mount is unchanged").

The design conclusion: a fact about code stays true until an observable edit; a fact about the
table decays silently at a rate set by what it is about. So the store holds *beliefs* —
every observation carries sensor, error bar, timestamp and a scene epoch — and the hard part
is not storage but knowing when to stop believing. Vagueness is a coordinate with an honest
3 cm error bar. Movement is detected, not tracked: the pre/post deltas that flagged `MOVED`
in today's edge-probe log, generalized. Dense reconstruction (SLAM, NeRFs) deliberately
refused: the coding-agent lesson is that cheap curated notes with an index beat total recall,
and with only a wrist camera every look is an arm motion, so memory's job is choosing when a
7–13 s observation is worth paying for again.

Doug's instruction: this version contains errors we cannot predict; build it and find out.
v1 is `hebbot.memory.scene` (`scene.sqlite` beside hebbot.db): `observations` are curated
conclusions, not raw probes; `actions` advance the scene epoch (the cache bug generalized —
old-epoch percepts are demoted, not deleted); object `properties` hold slow learned facts
(body size from touch fits, `resting_on`, `in_gripper`, "taped down" via `scene_note`).
`touch_stack` now writes poses, grasps, places, returns and verifications into it, and reads
one hint back: a remembered position gets one look before the survey sweep. The hint supplies
only the viewpoint, never the pose, so a stale memory costs one wasted look (~8 s) and cannot
shift the answer. `scene_recall` renders it as prose for the orchestrator — one line per
object with position, ±mm, sensor, age and actions-since. The July image-frame store
(`scene_sync`, scene.json) stays; porting its moved/appeared/disappeared association onto the
world-frame store is future work. 185 tests pass (15 new). Not yet shown: any of it on
hardware, whether the hint actually saves time there, and what the unpredicted errors are.

## 2026-11-10 — nine repeats: the memory on the bench, and what it is actually for

Eight verified stacks in nine attempts (r1–r8, 112–138 s, zero failed stacks), through five
taped rounds, an 11-hour overnight gap, and three loose-base rounds after the tape came off.
The one non-success was r7: Doug set blue at x≈0.60 and the close-look viewpoint, which sits
~6 cm beyond the target, fell 1 mm outside EE_LIMITS — a refusal, home in 40 s, nothing
touched. Doug's verdict: the envelope is too conservative; the arm can reach that block.
EE_LIMITS is a trust boundary on camera-derived commands (from the close-look crash), not
reachability; widening x 0.60→~0.65 is a one-constant decision deferred to the code freeze.

What the bench taught about the memory: position hints are nearly worthless *here* — the
D405's field of view is so wide that the survey finds a block at pose 1 anyway, so a correct
hint saves ~2 s and a stale one costs ~18 (r3, where the hint failed properly out-of-frame
and the survey recovered). The real value was elsewhere. The disturbance ledger correctly
flagged only the actions near each object (r1: green's `place`, not the 22-cm-distant
grasp). The belief survived the night and woke correct (r4). And the store became a free
drift recorder: green, loose, walked +x by 1–2 mm per run — 0.4672→0.4678→0.4693→0.4713→
0.4728 across the loose rounds — exactly the tap-drift Doug's eye caught, now measured.
Found wanting: a failed locate writes no negative evidence (r7 disproved blue's remembered
spot and recorded nothing); `hinted:True` conflates memory-right with camera-wide; and my
"beyond blue" compile of Doug's words for red was ~1 cm right on the constrained axis and
~7 cm off on the invented one — told beliefs need anisotropic error bars. All queued, none
built: the bench must force each.

## 2026-11-11 — touch_unstack: the arm resets its own bench, and contact detection grows up

The self-reset routine, `touch_unstack`, is touch_stack's inverse and the first skill that
acts from memory alone — no camera. The remembered pile supplies grasp position, yaw, height
and width; one probe confirms the pile is as remembered (±8 mm) before anything moves; the
destination is sampled away from every belief by 10 cm plus that belief's own error bar (so
red's told ±50 mm earns a 15 cm berth), probed as table, and only then trusted. The sim found
its own bug first: the test world's grasp was height-blind and picked the base out from under
the block at a shared xy; the real jaws close at base-top+10 mm, which only the pick spans,
so the fix belonged in the test world.

The hardware then taught contact detection three lessons in three runs. u1: the place at
(0.37, −0.20) pressed the block to the command floor and the 4 N force rise never came — the
external-force estimate under-reads near the workspace edge (the same spot's probe tripped on
geometry at −1.4 N). The block came back to the pile untouched-by-failure: pile check 0.0749
vs 0.0749 believed, return contact 0.0570 vs 0.0569 predicted, and the no-release-in-free-
space rule meant a failed unstack was a no-op on the world. (I then misread the oblique wrist
photo as "blue beside green" against the touch evidence; Doug's eyes corrected me. Touch said
on-pile to 0.1 mm. Trust the contact.) Fix one: contact is force OR geometry — the hand held
10 mm above its sinking command. u2 then reset verifiably (84 s) but released 14.5 mm high,
and r9b 23.4 mm high: at 300 N/m a ~3 N gravity-model error makes the hand ride ~10 mm above
its command in free air, exactly the lag threshold. Fix two: baseline the free-air droop in
the settle window and trip on growth beyond it. u3 placed at +3.6 mm. Gentle.

Between u2 and u3, the world intervened twice. Doug's table bump moved green 7.3 cm and spun
it 66°; the stale hints still found everything (wide FOV), the scene was re-measured and
rewritten, and the corridor check refused blue's grasp because green's new heading pointed
its long end at the fingers — honest refusal, 58 s. The recovery was an orchestrator move,
not a new skill: swap the parameters. r9b stacked *green on blue* — first inverted stack,
grip_high 0.0 mm — and u3 unstacked it back to the table. A full autonomous double cycle:
u2→r9b→u3, every leg verified by touch, the only human contribution an accident. Also found:
a scene-wide action (`bumped`, no xy) shows per-object as "none near it" — the disturbance
filter has no way to say "everything"; queued. 195 tests pass. Next: the alternating
stack/unstack campaign loop, then the frozen 30-attempt run with Wilson intervals on the
tally and refusal-vs-failure scoring decided at freeze time.

## 2026-11-12 — the campaign fails, investigates itself, and finds a calibration bug

The 30-attempt campaign began as a stop-rule exercise and became the best diagnostic
session this bench has run. f1 opened 5/5 — stack places +1.6 to +9.4 mm, table places
sub-millimetre — then halted on a cycle-5 unstack: the memory-only grasp air-closed
(grip_high 8.9 mm) and the retry's pile check found 0.0444 m where memory said 0.0740.
Doug's bench report: the failed grasp had shoved blue 4.5 cm sideways and spun it 90°.
Everything after follows from that shove. The failure needed eyes, not hands — the human
was serving as the camera the robot already owns — so we built what the halt priced out:
(a) a failed grasp now *retracts* what it touched (inference ±50 mm, resting_on
withdrawn; the honest "I don't know where it is now" instead of a confident lie), and
(b) `touch_look`, the investigation skill — camera finds each block, one probe confirms
its height, resting_on is re-derived mechanically, and a base hidden under a pile is
vouched for rather than seen. The loop looks before retrying, and a look that finds the
pick already off the pile *is* the reset. Six new tests, 201 passing. The design
conversation behind it (state dictates action; memories as descriptions whose
dependencies say exactly what an action stales) is the map for the next memory rewrite;
only the forced slice was built.

f2 validated the machinery beat for beat — the stale pile belief triggered the refusal,
the first live look found blue where Doug had described it, cycle 0 declared the reset
accomplished by f1's shove — and then cycle 1's camera-guided stack grasp air-closed at
(0.39, −0.24), grip_high 18.5 mm, and the old stop-on-scored-failure rule halted a
fully-recovered system. Amended: a scored failure is data; the loop looks, continues
when both blocks are accounted for on the table, unstacks first if a verify ever missed
a real stack, and stops only for what it cannot explain. f3 then earned that policy:
four identical air-closes (grip_high 16–27 mm), each failed grasp walking blue ~1 cm
further into the corner, each look re-finding it — until the fourth look probed blue's
top at 64.3 mm, taller than flat, shorter than on-end, resting on nothing memory knew.
Correct, earned stop. An assessment pass found blue flat again (the 64.3 was a propped
transient) and a probe line across its short axis bracketed the camera's position to
~5 mm — the camera is *right*, and the fingers still miss by 15+.

Which leaves the discovery. Every one of the six air-closes — f1's pile grasp aside, all
camera-guided — happened with blue at yaw 74–86°; every one of ~37 camera-guided
successes sits at yaw ≲ 11°. The bench never left yaw 0 before f1's shove, so a
yaw-dependent offset between the commanded EE axis and the fingers' actual closing line
was invisible: the camera map's `b` absorbed it at the orientation everything was fitted
at, and rotating the wrist rotates it back out. f4 closed the case from the other side:
cycle 1 failed once more in the bad configuration, the shove knocked blue out of it, and
the next twelve stacks ran without a miss (z −0.3 to +3.7 mm; the elevated pile-place
errors of c2/f1 gone too). f4's own stop was new and small: a one-frame far-look artifact
predicted blue 30 cm from a touch-verified position; the skill refused correctly and
find_pick/find_base joined the recoverable stages. Campaign stands at 17/24 verified with
six to run. Next: finish the protocol, then measure the finger-line offset directly —
probe a known edge with the wrist at 0° and 90° and difference the answers. If it is a
constant lateral offset, yaw-90 grasps join the reliable set for one line of frame
arithmetic, and the boundary-failure ledger (force under-read, envelope refusals, camera
residuals, now this) gains its fifth entry: every sensor on this arm is best in the
middle and lies at the edges.

## 2026-11-12 (later) — the campaign closes: 22/30, and the stop rule stays quiet

f5 ran the final six: 5/6 verified, and the one miss was the campaign's second far-look
artifact (blue "predicted" at (−1.2, 2.1) — a depth glitch; physically absurd, correctly
refused). This time the amended loop spent one look on it and continued; across the whole
campaign the loop handled 11 failures autonomously and stopped for a human exactly three
times, each for something a camera genuinely could not explain at that moment. Final
books: **22/30 verified, Wilson 95% [56%, 86%]**; treating the two camera artifacts as
refusals per the freeze protocol (nothing attempted, nothing touched), the lateral rate
is **22/28 = 79% [60%, 90%]**. The ≤9/30 stop rule is not triggered and not close: the
classical touch pipeline passes its own gate, and the learned-policy track stays
optional rather than forced. The failure ledger is as clean as the success one: six
lateral failures, all one cause (the yaw-74–86° finger-line offset), two refusals, both
one cause (far-look blob artifacts). No block was ever lost, dropped from height, or
released unverified. Watch items carried forward: f5's last reset placed +22.6 mm high
yet verified at its destination — first elevated place since the droop fix, so
CONTACT_LAG_M stays on watch — and red is now a ±50 mm told-belief aged by 138
unobserved actions. Next, in order: measure the finger-line offset directly (probe a
known edge with the wrist at 0° and 90°, difference the answers — Doug concurs this is
the right thread), then the deferred EE_LIMITS x widening as its own tested change.
Same-day context: NVIDIA announced GR00T 2 at CoRL — a 2B open "System One" world-action
model with LLM agents explicitly layered above it, few-demo LoRA adaptation, and no
force sensing anywhere in sight; reading note in research_notes. The architecture bet
is now consensus; the touch-verification niche is still empty.

## 2026-11-12 (evening) — the verifier gate goes live: Hebbot's first verified skills

ASPIRE's admission rule, ported this morning and run on hardware this evening:
touch_stack, touch_unstack and touch_look are the substrate's first **verified** skills —
two fresh-context dispatcher runs each, identity params only, judged by their own probed
expectations, 6/6 legs with zero failures, evidence bound to fingerprint 148938ef06 under
hardware tag omen-franka-d405-2026-11. Status is derived, never stored: the evidence
expires the moment the source hash or hardware tag stops matching, which closes the
limitation ASPIRE's own paper lists (sim-learned skills "verified only once at
admission"). One property surfaced immediately: all three skills share a fingerprint
because they share a source file, so editing any one demotes all three. Conservative and
currently correct — they share contact constants — but per-function hashing is the known
refinement if it chafes. The first real demotion arrives with the very next bench task:
the yaw-offset measurement will edit touch_stack.py, expiring today's evidence by design,
and the re-earn cycle will be the gate's first full revolution.

## 2026-11-12 (night) — the yaw offset: measured, explained, fixed, validated 1/1

The campaign's one systematic failure now has a number. Measuring it took three runs
and taught two lessons on the way. y1 false-tripped in free air because I rebuilt
probing from the placement detector's 4 N force trigger instead of checking that
probe_table already takes an orientation — Doug's eyes caught the arm "probing air"
at two-block height from across the room. y2, with the proven probe rotated, produced
clean baseline geometry and then a new bench lesson: **a probe is also an actuator** —
walking edge-probes herded the loose block ~4 cm at a centimetre a touch, the drift
control screamed, and the ledger got a probe_sweep disturbance. y3, on a taped block,
was clean: the repeat-0 control reproduced to one bisection step, the chord and edge
changes across yaws matched the rotated footprint's dilation arithmetic exactly, and
the midpoints shifted +3.75 mm (wrist 75°) and +5.63 mm (90°) — the finger-line
offset, isolated. One subtlety nearly spoiled the fit and a unit test caught it:
axis 90 normalises to −90, so both measured yaws rotate the *same* way 15° apart,
and the first solve (treating them as opposite) invented a 7.6 mm offset along the
jaw axis. The honest weighted fit — chord midpoints plus the +x-edge dilation
residuals — gives d ≈ (2.5, −2.6) mm in the hand frame, direction soft, but every
direct measurement reproduced to <0.7 mm, and the effect at the failure yaws is
~4.4–5.2 mm: over half the grasp's 9 mm per-side clearance, the systematic share of
the 0/6 cliff. The camera map's b absorbed the yaw-0 projection all along, which is
why 37/37 succeeded there and why the fix is only the rotation residual
[R(a)−R(0)]d, applied at every rotated-wrist command site in both skills.
Validation: Doug set blue perpendicular, untaped, mid-table — the exact 0/6
configuration — and the corrected grasp closed at grip_high 3.2 mm, first try,
stack probe-verified (v1, 129 s). Editing touch_stack.py demoted all three verified
skills by fingerprint expiry, as designed; re-earning them under the new hash is the
gate's first full revolution. Still open and climbing: pile places release high
(+7, +9, now +13 mm) while table places stay sub-millimetre; and rotated-wrist free
air exceeds the probe's 10 mm lag trigger when reaching toward the base (y3's −x
phantoms) — both on watch. 211 tests.

## 2026-11-13 — contact detection, third generation: a rate, not a level

The "climbing" pile-place release error was never climbing — the ledgers say it was
standing still in a bad neighbourhood. Analysis of all 75 recorded places (episode
outcomes carry everything: grasp heights, expected contacts, verify readings) ruled out
slip (no correlation with grip geometry, and the same carry places sub-millimetre
elsewhere) and ruled out time (r=0.18). The pattern is spatial: the three worst releases
(+22.6, +21.2, +12.3 mm) are the three destinations at x ≤ 0.386; everything at
x ≥ 0.41 places under a millimetre; and c2/f1's elevated stack places all happened while
the pile lived at x ≈ 0.39. One cause, three costumes now on record: u1's force
under-read, y3's phantom probe trips, and these early releases are all the gravity
model erring in the near-base reach — and there, the error *grows during the descent*,
so any lag threshold, fixed or baselined at the start, eventually lies. Doug called the
fix: a droop model that adapts during the descent. The implementation is cleaner still —
differentiate instead of adapt. Drift never stops the hand (the worst measured ate about
half the command rate); a surface stops it entirely. The trigger is now the hand's own
descent rate collapsing below a quarter of the commanded speed: generation three, after
absolute lag (killed by constant droop, u2/r9b) and baselined lag (killed by drifting
droop, f5/vgate). Sim tests attack it with drift worse than anything measured, plus
every prior generation's nemesis; 213 pass. Hardware: a place forced to (0.37, 0.0) —
the exact crime scene — released at −0.8 mm where the old trigger gave +12 to +23, and
the full re-earn ran clean around one more memory-grasp air-close (grip_high 6.8, blue
shoved off the pile, look caught it, pile rebuilt — the f1 recovery machinery's third
quiet save; that tail is now ~3 in 30 and on watch). All three skills re-verified under
the new fingerprint; the gate has now cycled three times in two days and feels less
like process than like breathing. Still open: probe_table itself keeps the absolute
10 mm lag trigger and still phantoms at rotated-yaw low-x reach (y3); queued until
probing needs that region.

## 2026-11-13 — mixed-yaw campaign: 10/10 across the whole arc

The reset was the door yaw diversity had to come through: touch_unstack placed every
block back at the pile's own yaw, so a campaign could never leave the angle it started
at. Now it turns the held block to a sampled destination yaw on the way down — a
`dest_yaw_deg` param, uniform across the axis range when omitted — with the
destination-side wrist and finger-line correction computed at the new yaw while the
pile-side moves keep the old one (216 tests, three new). Campaign m1, ten scored
cycles, grasp yaws −89.5 to +87.3 including the exact band that went ~1/7 before the
correction (74–86°): **10/10 stacks verified, 20/20 legs, zero recoveries, zero
humans**. Worst grip_high 5.7 mm against the campaign's 8.9–26.9 air-closes; and the
camera re-found each block within ~1.2° of the yaw the reset reported leaving it at,
which is the correction validated from both sides of the loop. The yaw-offset arc that
began as a 0/6 cliff on 2026-11-12 closes at 14/14 post-fix rotated grasps.

What the new |yaw| axis shows, now that it has data: place contact reads higher as
|yaw| grows, in both legs (r = +0.76 stack, +0.81 unstack) but at very different
scales — table places span −1.3 to +0.3 mm, pile places 0.1 to 4.7 mm, slope
~0.03 mm/deg. So v1's +13 mm outlier had a real correlate but a small one; everything
here sits well inside the 8 mm verify window. On watch, not on fire: likely the held
block riding differently in the jaws at rotated yaw, which the pile (a 45 mm higher
contact with a longer lever arm) amplifies. Editing touch_unstack expired all three
skills' verification evidence again, as the gate is built to do; the re-earn ran
clean the same evening, 6/6, all three skills verified under ee0ef14ea7 — the
gate's fourth cycle, and both of its unstack legs sampled yaws of their own.

## 2026-11-13 — ASPIRE runs the bench: first verified stack through a foreign harness

The day's question was whether NVIDIA's ASPIRE could run this hardware, and the answer
is a probe-verified yes. A `franka_real` embodiment now lives on a branch of the ASPIRE
clone — station config, a plant with one guarded actuation chokepoint, their adapter
protocol with the same motion-interlock semantics as our all-clear rule, an in-process
panda_py analytical IK provider, and our colour rules behind their SegmentationProvider
protocol until Meta approves SAM3 access. Their full test suite: 708 passed, 12 of them
ours, zero regressions. Three live defects found by running the real thing: their
CameraObservation frame semantics silently skip the world transform if you name both
frames "base"; the hand-eye translation was our camera map's nadir offset all along
(camera 59 mm *behind* the TCP — localization validated against touch truth to 2.5 mm);
and Franka's redundant q_7 pinned at the seed makes every rotated grasp "unreachable."
First stack attempt failed honestly — closed on air after three unchecked move refusals,
exactly the artifact-reading lesson their fix loop is built on. Second attempt stacked:
the program's own camera verify said risen-and-near, and touch_look probed blue's top at
0.075 m resting on green. Their real-robot harness scores hardware success from video by
hand; ours was confirmed by a finger. That asymmetry — probe-verified check_success —
is upstream improvement #2, behind evidence-expiry. The bench ends stacked, memory
honest at epoch 220+, arm home. Queued: unstack reset to close the ASPIRE loop, scene
camera (a spare D435 exists) when fix-loop campaigns start. SAM3 cleared Meta's review
the same evening: the service holds 4.5 GiB of the 5060 Ti, localizes blue and green
within 10 mm of touch truth from the home pose — including blue correctly *on* the pile
— and found red, invisible to every colour rule this bench has ever run, 10 cm from its
three-day-stale told position: the first camera fix on red in the bench's history,
recorded at ±12 mm pending a confirming finger.

## 2026-11-13 (evening) — red moves on a camera's word; the bench gains a third eye

ASPIRE's move_red task: SAM3 found red (10 cm from its stale told position), the
program grasped it at its measured yaw and set it on a clearance-checked patch, and
verified by re-localization to 13.6 mm — the first robot action on red ever driven by
perception rather than a human's words. The scene camera then revealed what nobody
knew: red is *standing on end*, and the grasp-at-10-mm convention picked a standing
block up by its lower third and set it down still standing without ever knowing.
Hardware archaeology for the D435: three cables and two topologies all trained at
USB2 (its SuperSpeed lanes or every cable in the house — undetermined), but measured
throughput at USB2 is 13.5 fps of 640x480 RGB-D, ample for a static scene view.
Calibration without a checkerboard: the depth cloud's dominant plane plus two
touch-verified block tops solve the fixed pose to 3.5 mm per landmark and 1.0 mm on
the table plane (exact on a synthetic pose test). Both cameras now ride every ASPIRE
observation. Found along the way, a real-camera bug class their sims can never show:
silhouette depth-bleed smears a 55 mm standing block into a 225 mm point cloud from
an oblique view — calibration dodged it by construction (top-band statistics), programs
must dodge it by extents rejection, and robust banding in mask_to_point_cloud becomes
upstream improvement candidate #3, after evidence-expiry and probe-verified success.

## 2026-11-13 (late) — the foreign harness learns to check its own work

Upstream improvement #2 is no longer a critique, it's a diff. ASPIRE's real-robot
embodiments return check_success()=False and leave success to be judged from the
video; our franka_real adapter now re-observes after the program ends and measures
the task's physical postcondition, filing the evidence (which camera testified, what
it measured, which criteria passed) as logs/success-report.json in the run. The
localizer is a top-face band centroid — the same depth-bleed defence the calibration
uses — and the very first live verdict earned its regression test: it elected 8572 px
of something blue at the robot's base over the 55 mm block, until candidates outside
the workspace box were made to lose the election. Thresholds are budgeted from live
bias measured against touch truth (pile top 0.075 reads 0.0875 from home's oblique
view). Validated with zero motion on the stacked bench: stack_blocks earns
task_success=True — blue measured at (0.4615, −0.157) against the finger's
(0.461, −0.156) — and unstack_blocks correctly returns False on the same scene,
with "could not tell" distinguishable from "failed" in the report. The unstack
program is also written and sandbox-proven (its dry run aborted honestly at the
first interlock refusal), closing the station's own stack/unstack reset loop on
paper; it awaits an all-clear to run for real. ASPIRE branch at 4ee885d, 20/20 tests.

## 2026-11-13 (night) — the cycle closes, and a ghost gets a name

The stack/unstack reset loop ran live through ASPIRE's surface, each half earning its
own verdict: unstack set blue down 4 mm from its chosen clear spot (green, newly
exposed, measured 1.3 mm from touch truth by the new success predicate), and the
re-stack put it back — termination_reason=task_succeeded on both, the first
motion-earned verdicts on this embodiment. Along the way the recurring mid-carry
execution_failed anomaly finally printed its error text and died: planned
trajectories re-assert the program's last *commanded* gripper value (0.02 m) while
the fingers physically hold the block at 0.029 m, so every carry ended by re-issuing
a grasp on an already-gripping gripper, which libfranka rejects — "Command failed!" —
poisoning otherwise successful moves. Re-closing while holding is now a self-reporting
no-op. Also built: upstream improvement #3 — mask_to_point_cloud trims depths more
than 3 robust sigmas (MAD) from the mask's median, killing silhouette bleed while
keeping genuinely deep structure; the 225 mm phantom block is now a unit test.
ASPIRE branch at 0d4035c, 22/22 adapter tests, 179 across the cloud consumers. The
"slow arm" during the cycle is the motion generator grinding 19-waypoint descents at
speed_factor 0.12 (~2 min per tall move) — a tuning item, not a fault. Bench ends:
blue back on green at the pile, red still standing, arm home.

## 2026-11-13 (later) — the survey, the knocked camera, and glow that voted

"The box is on the bench — run the survey" turned placeholder geometry into measured
goals with zero motion: SAM3 found five wooden-block instances (0.88–0.93) inside
what turned out to be a mesh basket, rim measured from the wrist depth cloud at
z≈0.10, footprint centred (0.44, 0.23) half-extent 0.11; the wall line moved to
y=−0.12 after noticing the placeholder ran within a centimetre of the standing red
block. The survey's snapshots also caught the evidence-expiry lesson live: the D435
scene camera had been physically knocked and was staring at the robot's column while
its profile still carried the old calibrated pose — the slab verdict's preferred
witness, confidently wrong. After re-aiming, the checkerboard-free recalibration
(plane + the stack and red as touch-verified landmarks, no motion) failed honestly
on its first solve: 146 mm residuals, because the base column's blue LED strips
out-pixelled the real blue block and the landmark picker took the biggest blob —
the third time pixel count has voted against physics on this bench. The cure is now
uniform policy: rank candidates, gate on plausibility; here the fitted plane itself
is the gate (a block raises its whole blob above the table, glow hugs it). Second
solve: 4.3 mm residuals, camera within centimetres of its pre-knock pose, and the
recalibrated witness validated against bench truth — thousands of band points at
the stack and red, zero at the empty pick target. ASPIRE branch at dc05898, 28/28
adapter tests. The live pick-from-clutter now waits only on an all-clear.

## 2026-11-13 (night, second) — first clutter pick, verdict by the rehabilitated witness

The live pick_from_clutter ran once and succeeded: attempt 1, the tallest candidate
in the basket (a block standing on end, top 0.0875) grasped at (0.4785, 0.1707),
carried over the rim, placed at the target — the program's own re-scan found it
17 mm off centre, and check_success earned the verdict through the just-recalibrated
scene camera: 1659 slab-band points at (0.34, −0.20) against a threshold of 60.
The run's one blemish is its best data: the place "lower" was aborted by the
cartesian reflex, because PLACE_EE_Z assumes a flat block (27 mm) and this one stood
tall (55 mm) — its bottom hit the table 25 mm early, contact fired the reflex,
recovery ran, release left the block standing upright on target. An accidental
contact-based place: the same descent-contact trigger hebbot uses deliberately,
delivered here by a safety system. Lesson filed for build_wall: preserve the
EE-to-block-bottom distance measured at grasp instead of assuming orientation,
or every standing pick becomes a reflex abort at its slot. Bench ends: stack and
red untouched, one green block standing at (0.34, −0.20), arm home.

## 2026-11-13 (night, third) — the wall's two blockers, removed at the desk

Both prerequisites for build_wall built and committed (ASPIRE 479b3b2), no motion.
Speed: the crawl was never the planner's fault — panda_py's generator (Ruckig
point-to-point under the hood) comes to rest at every waypoint it is handed, so a
dense retimed path executes as a stop-start grind. execute_waypoints now decimates
after guarding: greedy joint-space chord fitting at 0.05 rad tolerance, then the
workspace guard re-run over the chords at the dropped parameters, because the chords
are what the arm will actually follow; if the sparser path can't be shown safe the
dense one is sent — slow and dense beats clever. A straight 19-waypoint descent now
sends 2 waypoints; speed_factor stays at 0.12, untouched. Place height: both clutter
programs now read the held block's orientation from its scanned footprint (long side
visible = lying flat, 28 mm in hand; narrow = standing, 55 mm) and place preserving
bottom clearance, which turns last run's reflex abort into a 3 mm release. build_wall
prefers lying candidates — the wall wants flat courses — and reports each slot's
orientation instead of papering over a standing one. 31/31 tests; no-motion smoke of
both programs walks the full perception chain and aborts honestly at the interlock.
The smoke's own verdict was a nice epistemic moment: pick_from_clutter came back
task_success=True with zero motion, because the block from the live pick still sits
at the target and the predicate measures the world, not the program's effort.

## 2026-11-13 (night, fourth) — the basket bites, and the hand learns its own width

First live build_wall lasted one descend: the chooser took the highest flat block —
at x=0.501, nine millimetres from the basket's measured east wall — and drove the
hand into the rim on the way down (Doug watched it bump the edge). Cartesian reflex,
honest abort through go_home, slots_built 0. The diagnosis is a category error:
in_box asked whether a block belongs to the basket; nothing asked whether the hand
can get at it. The decimated descend reached the rim faster, but speed was never the
bug — the collision was geometrically certain at any speed. Fix: the survey's
measured wall lines (x 0.36–0.51, y 0.10–0.36) now live in both task goals, and both
programs gate candidates on a yaw-aware hand envelope — 60 mm half-span along the
finger-closing axis, 40 mm along the block — per wall. Wall-huggers are excluded as
physically unpickable by vertical descend, and when only wall-huggers remain the
program reports wall_blocked rather than bumping the basket. No-motion smoke against
the live scene: the chooser moved from (0.501, 0.225) to (0.437, 0.199), seven
centimetres clear of the wall that claimed the run. ASPIRE at f3d0b21, 31/31 tests.
The wall itself still waits: the earned-verdict slab report meanwhile showed slot_3
already reading 1286 points from the stack's edge 3.6 cm off the wall line — the
bench is getting crowded, worth watching when the real courses go down.

## 2026-11-14 (small hours) — shoving practiced, and a witness election

Doug's suggestion ("a good chance for you to practice shoving") became the bench's
first push primitive: fingers closed into a bundle, contact low on the bottom block,
quasistatic slide along −y. One push carried the intact stack from (0.4541, −0.1574)
to (0.4485, −0.2184) — 6.1 cm, slight overshoot past the −0.205 target — with blue
riding green the whole way; run under stack_blocks so the verdict was the
postcondition a shove must keep, and it was earned: blue-to-green 0.0156, on_green
true, zero reflexes. The no-motion smoke beforehand caught a lovely bug: the bench
now has TWO green blocks (the basket pick was lime), and the stack verdict matched
blue to the big fully-visible newcomer at the pick target instead of the pile's
edge-ring green — 'blue is 0.105 from green' on an intact stack. Relation predicates
now elect their witness by nearness to blue, not pixel count (locate_colour_top grew
near_xy); for unstack the same choice is conservative. The shove's own check gates on
height alone for the same reason. Slab re-check after the shove: all five wall slots
read 0 band points; the stack's 2389 sit at its new home 6 cm clear of the line.
ASPIRE at 4dcf9b0, 32/32 tests. Noted for later: the home-view oblique bias grows
with distance — the stack's true 0.073 top read 0.1017 from home at y −0.218 (+29 mm
vs +15 mm at the old pile), so the single-block threshold needs a second look if
blocks drift much farther out. Bench: stack at (0.45, −0.22), wall line empty,
basket mid-clutter, lime block at the pick target, arm home.

## 2026-11-13 (Friday evening) → 2026-11-15 (Sunday) — a sorting demo for Scarlett

Doug brought a ten-year-old visitor and asked for a quick block sort. The program
(`sort_colours.py`, written in minutes from the clutter pick's grasp mechanics) lines
red, green, blue up along y = 0 at x 0.32/0.41/0.50 — blue off the stack first, red
before green so no descent lands beside the standing red. First live run, Friday:
blue came off the stack cleanly, carried to its slot, and the lower-to-place fired the
cartesian reflex a few millimetres early. The clutter pick treats that as a completed
contact-place and releases; my sort program had made "lower" fatal, so it gave up with
blue already sitting 3.7 cm from its slot. One-line lesson, re-learned: lower is
contact-tolerant, by design. Scarlett enjoyed it anyway. The resume ran Sunday on an
empty bench: blue re-placed 2 mm from its slot, green 2 mm from its slot (verified by
nadir re-localization), and the row's pair looks tidy from the scene camera. Red did
not make it: its standing place also fired the reflex, and after the release and the
reflex recovery it ended up lying flat at (0.198, 0.118) — 16 cm from its slot and
outside the workspace box (x < 0.25), so the arm cannot fetch it back without a hand.
Two findings. (1) The 3 mm release gap is thinner than the stack of errors (top-read
bias, grasp seat, table plane): both placements this run met the table before it
closed; sort_colours now releases from 8 mm. (2) A refused survey is silent: the
program's `survey()` had no max_steps, the blue-slot→red-block survey exceeded the
default budget, and the red localization looked from the wrong pose — it happened to
work, which is worse. Surveys now carry the long-move budget and a refused one aborts
the find. Final: 2 of 3 sorted. Bench: green (0.41, 0.00) and blue (0.50, 0.00) lying
flat on the row, red flat near the base at (0.20, 0.12), lime standing at the pick
target, basket mid-clutter, arm home.

## 2026-11-15 (Sunday) — the wall is blocked, the place goes closed-loop, PR #41 opens

Three things Doug asked for in one breath. (1) The wall: ready for its all-clear
since Friday, it ran and stopped honestly before its first pick — `wall_blocked`.
The scan from above the basket found three blocks, all at x 0.49–0.52, y 0.28–0.34:
the north-east corner, against both walls, exactly where the yaw-aware envelope says
a vertical descend cannot go (that envelope was written on Friday with the rim bump
still ringing). The other two "wooden block" instances were the sorted pair on the
row and red by the base. Nothing for the arm to do until a hand spreads the basket.
(2) The thing every placement this weekend kept showing: open-loop lowering. The
motion generator is contact-blind, and three of four live places met the table a
few millimetres before the height math said they would. ASPIRE's franka_real now
has the bench's own answer — `descend_until_contact`, a cartesian-impedance descent
whose command sinks at 20 mm/s and stops when the hand stops following it (rate
below a quarter of commanded, or a 4 N rise, sustained five ticks, after 18 mm of
travel), then eases the command back to the contact height before the release.
It is the second and only other way the package moves the arm, guarded by the same
workspace box, exposed to programs as `franka_real.descend_to_contact`; the clutter
pick, the wall and the sort now pre-place 3 cm above the expected contact and descend
to it. Off-robot tests drive it against a fake plant with a 300 N/m surface (stops and
unloads; reaches the floor in free air; refused like a waypoint). ASPIRE at b875103,
35 adapter tests; the full suite's five failures all predate today (BEHAVIOR and
curobo assets, and two release-asset doc checks the Franka port never satisfied —
worth fixing before any embodiment PR). Not yet run live: it needs an all-clear, and
the honest first test is a bare-hand descent onto the empty table. (3) PR #41 to
NVlabs/ASPIRE, the silhouette depth-bleed trim. I first wrote a live-validation line
into its test plan that I had not actually measured, caught it on re-reading the
session record, and rewrote the body before anyone saw it; then did the measurement:
same scene camera, three blocks, trim off/on — 178→71, 154→76, 92→66 mm along the
ray, 87–96 % of pixels kept, green's centroid error against its nadir-verified
position 56→26 mm, blue's 36→27 mm. Posted as a comment. The residual is the oblique
view's side faces, not bleed.

## 2026-11-16 (Monday) — the basket is the wrong container

Doug's read of the wall_blocked result: the Franka Hand's body is much wider than
the span its fingers cover, so it was the palm, not the fingers, that met the basket
at 9 mm from the wall. We listed the ways round it: pick the grasp axis so the slim
side of the body faces the nearest wall; back off laterally when the descent stalls
(a safety net, not a plan — it protects the hand and leaves the block); descend
inboard and tilt about the closing axis so the fingertips reach past the body (the
only software answer that gains real reach, ~2–2.5 cm at 25–30°, and only when the
closing axis can run parallel to the wall); rake wall-hugging blocks inboard with the
closed bundle (the only answer for blocks touching the wall); give curobo the basket
as collision geometry; straddle the rim (fails by the numbers — the body underside
is ~5 mm below the 100 mm rim at standing-block grasp depth, ~3 cm below for a flat
one); finger extensions. All of it rides on the contact primitive, which is a good
sign for the primitive and a bad sign for the basket: every option is code that only
this container needs. Decision: replace the container with something low-rimmed
(≤ ~60 mm, ideally ≤ 40) so the body straddles the wall and clearance drops to the
finger scale, and keep the options as notes. The hand's envelope in the gate is
still an estimate; a caliper reading of the body across finger travel is owed before
the gate is trusted anywhere. Queue stands: bare-hand contact descent first.

## 2026-11-16 (Monday, later) — stepping back: ASPIRE as data factory, and a dreaming cycle

Doug stepped back to look at ASPIRE whole. Its own framing ("training is skill
refinement instead of gradient descent; the trained model is a repo of sensorimotor
skills") is the hebbot thesis as the notebook has carried it since June: the practice
loop surfaces failure modes, the high layer turns each into a sturdier primitive, the
repo plus this notebook is the model. ASPIRE adds the discipline hebbot lacked —
dev/held-out campaigns, a success predicate the program cannot touch, a coordinator
that is also the curriculum, candidate search (evosearch: K=8 whole programs per
iteration, each a distinct hypothesis, top-3 seed the next eight, one held-out
measurement at the end). Hebbot has what ASPIRE lacks: mutable lower layers (this
week's bench wins were the contact descent and the bleed trim, both below the tool
API, where ASPIRE's search cannot reach), distillation as the goal rather than
refinement, evidence with a lifetime, and a memory of the world. But ASPIRE's reflex
is still code: System 2 writes System 1 as a program over frozen primitives, and
nothing in the repo trains anything.

Doug's direction: keep high-level cognition for novel steps, and for tasks we find
ourselves practising repeatedly, collect the practice as samples and train a VLA
during a dreaming cycle, so that over time the repeated tasks run as learned reflex
memories. The port serves this because a skill program that runs a task, verifies it
with a predicate it cannot touch, and resets for the next trial is the self-resetting
autonomous collector the distillation needs — the trace is a labelled dataset for
free. The literature review (reports/Hebbot robotics literature 2026.md) shapes the
plan: nobody has distilled tens of real LLM-agent episodes into a neural policy, and
the nearest existence proof (Tether, Panda: 10 demos → 1,085 unattended successes in
26 h with 5 interventions → diffusion policies near human-demo quality) says
hundreds of self-collected successes, not tens; Hi-VLA says the unit is the stage,
not the task, and the learned reflex should be a fixed-instruction tool the
orchestrator calls, with an upward failure signal; a 16 GB card fine-tunes ACT or
SmolVLA with LoRA, not π0.5; and the 100 Hz contact loop stays below the policy as an
action it can emit, not a thing it feels.

**The dreaming cycle, as planned:**
1. *Record at policy rate.* The trace is per call; a policy needs per-tick wrist
   video, EE pose, gripper width, and commanded delta at 10–30 Hz. A rollout recorder
   in the franka_real adapter turns every practice run into a training episode,
   labelled by the verifier.
2. *Practise one stage unattended.* Pick the most-repeated stage (top-down grasp of a
   known block, or place-by-contact), run it as a self-resetting campaign with the
   success predicate as labeller, and bank verified episodes. Threshold before
   training: ~50 verified successes to try a small policy, hundreds to expect one.
3. *Dream overnight on omen.* Select verified episodes, fine-tune ACT (last-centimetre
   segment) or SmolVLA with LoRA on that stage alone, as a fixed-instruction tool.
4. *Paired gate on the bench in the morning.* Reflex versus program on the same
   scenes; promotion only when the learned stage matches the program's held-out rate,
   with the program kept as fallback and the failure signal routed upward.
5. *Measure the curve.* Deliberation cost (LLM calls, tokens, wall time) against
   practice across the promotion event. The review found that curve nowhere on
   hardware; it is the project's most distinctive available result.

## 2026-11-17 (Tuesday, 6:30) — the dreaming cycle, discussed before building

Doug's recap of the goal, verbatim in spirit: high-level cognition guides the real arm
through whatever tasks we assign; every run is captured as training data; failed runs'
data is discarded and successes retained; an automatic dreaming cycle trains a VLA
policy on the retained captures during inactivity; when deciding how to do a task the
high-level layer asks whether a policy exists for it and uses that instead; and even
while a policy runs, the high-level layer monitors and evaluates it and records more
data for later dream cycles.

Amendments agreed: (1) keep the failures, just don't imitate them — label every
episode per stage so a verified grasp before a failed place is still a grasp sample,
and the negatives train the monitor; (2) "a policy exists" means *promoted*: the
skill record carries the held-out rate, the gated scenes and the preconditions, and
an unpromoted checkpoint is invisible to the orchestrator; (3) the richest data is
the program's recovery from a state the policy drifted into — the DAgger loop — so
the monitor watches stage boundaries, the verifier, timeouts, force and workspace
limits and the told-vs-seen residuals, and intervenes between stages, never at
10 Hz; (4) dreaming is training only; promotion needs the bench and the bench needs
the all-clear; promotion rule written before any result exists — policy matches or
beats the program over ≥ 20 paired scenes with no safety events; (5) self-reset
picks the first stage — place-by-contact of a known block, trivially reversible;
(6) ACT first (precision record at ~50 demos, deterministic head fits our unimodal
program data, retrains from scratch in an evening), Diffusion Policy as the second
local body (Tether's recipe, multimodal), SmolVLA when a second instruction shares a
checkpoint, π0.5 for the DROID prior now that the rig matches DROID's cameras.
Expectation set: the literature says the first dream probably fails the gate; a
failing gate still measures how many verified episodes the stage needs.

**Format decision: capture in a format we own, export to LeRobot for training.**
Per-episode directory: a tick table at full control rate (robot-clock timestamps on
every tick so the 100 Hz contact loop and 30 Hz cameras align), colour as MP4,
depth as 16-bit PNG (never video-encoded), and an episode manifest with the verifier
label per stage, the producer (program or policy and version), and a link to the
ASPIRE run directory. The capture carries what no training format wants and the
project needs — external wrench, contact events, stage boundaries, residuals — and
feeds the monitor, the analysis and the deliberation-cost curve. A ~100-line
converter emits LeRobotDataset (which ACT, Diffusion Policy, SmolVLA, openpi and
GR00T all read) and is rewritten when they break the format again; RLDS only if we
ever contribute to an Open-X-style corpus; MCAP in reserve if the capture outgrows a
tick table. Action representation: EE delta per tick at a fixed rate, sampled from
actual state regardless of what produced the motion, so the recorder serves the
program, the policy and teleop alike.

**The Spark workstation joins the bench** (ssh `spark`, 192.168.1.68): DGX Spark,
GB10, aarch64, 121 GB unified memory, 20 cores, 3.7 TB NVMe with 2 TB free, torch
2.10+cu130 installed. A Qwen3 27B sglang server (docker `sglang-qwen38`) currently
holds 81 GB, leaving ~37 GB. Consequences: the "rent a GPU" column is gone — π0.5
LoRA (22 GB) fits beside the Qwen server, full π0.5, GR00T N1.7 (40 GB) and
MolmoAct2 bf16 (~44 GB) fit with it paused, DreamZero inference becomes possible.
The Spark's limit is bandwidth, not memory (~¼ of a datacenter card): ACT/DP finish
in an evening, π0.5 LoRA is an all-night job — the right shape for a nightly cycle.
Topology: capture on the Franka host → sync to the Spark (the archive) → train in
idle time → checkpoints back to omen for inference beside the control loop; the
Spark serves over the LAN only if a model outgrows 16 GB. The inactivity signal for
dreaming is probably the sglang server's own idleness. First task before any
training: the aarch64 environment — LeRobot, openpi and flash-attention need wheels
or an NGC container, and that half-day decides whether SmolVLA/π0.5 are practical
on it or only ACT/DP. Noted in passing: a local 27B model already running there is a
candidate cheaper System 2 for routine monitored runs.

Doug, mid-write: the Panda, omen and the Spark all sit idle most of the time, and
he wants them busy. That reframes the cycle from an experiment into a standing
schedule. Each machine has an obvious job: the Panda runs self-resetting practice
campaigns (the data factory), omen runs the harness and perception for those and,
between campaigns, a bench sim for evosearch-style candidate search, the Spark
dreams nightly and can supply a local System 2 (the Qwen3 27B already running
there) so routine monitored runs don't spend API tokens. The one thing that can't
be decided by me: unattended arm time needs a standing authorization in place of
the per-run all-clear — bounded by the workspace box, the contact-force limits, a
watchdog that homes and stops on any reflex or verifier anomaly, a scene sanity
check before each episode, and a time window — and that is Doug's call to write
down before the first unattended campaign.

**Standing authorization (Doug, 2026-11-17, ~7:00):** "You have my standing
authorization to run the arm unattended, and my instruction to keep the hardware we
have busy." The per-run all-clear that governed every live run since the Franka port
began is replaced by this. The bounds I asked for stay as engineering, not
permission: the adapter's workspace box and contact-force limits, a watchdog that
homes and stops on any reflex or verifier anomaly, a scene sanity check before each
episode, and honest reporting here. First use: the queued live validation of
`descend_to_contact`, starting with a bare hand on the empty table.

**`descend_to_contact` validated live, after one honest miss (runs 0002–0004).**
The first bare-hand descent at (0.50, −0.15) stopped at the floor without ever
touching: the per-tick series (added for the second run) showed the hand following
the sinking command at about half its 20 mm/s and trailing it by 21 mm when the
command reached the floor 42 mm down — joint friction at 300 N/m, not contact — so
the fingertips were still ~9 mm above the table. A floor that bounds the *command*
can never reach anything on this arm. Fix: the floor bounds the *hand*; the command
may lead it by up to 35 mm (≈10 N at 300 N/m, under the reflex thresholds), and a
stop is `floor` when the hand gets there, `press` when the command runs out of lead,
`contact` when the hand stops following. Third run: contact at z = 0.0128 on three
of three descents, within 0.1 mm of each other, lag 0.5 mm after the unload. The
closed pads sit 4 mm below the grip line that meets the table at 0.017 with a block
in hand. Each touch pressed ~8 N before the trigger fired, because the command
already trailed the hand by 23 mm at touchdown — acceptable on a table, a number to
remember for towers. 36 adapter tests, with a fake plant that trails its command.

**First self-resetting stage, live: blue A↔B, six of six legs verified (run 0006).**
`round_trip.py` carries blue from the row slot (0.50, 0.00) to the validated bare
patch (0.50, −0.15) and back, three times: survey, localize, hover, descend, close,
lift, carry, pre-place 3 cm above the expected contact, `descend_to_contact`, release,
retreat, re-localize. Every placement stopped on contact (z 0.027–0.032 against an
expected 0.028 for a flat block, 5–8 N pressed, lag ≤ 0.4 mm after the unload); every
grasp held (gripper 0.74). The scene ends where it began, so the next run needs no
hand. The data has a bias in it: the block landed 11–14 mm short in x on all six
legs while y was within 3 mm — a systematic offset, most likely the grasp sitting
off-centre along the block's long axis or a localization offset along the wrist
camera's own axis. Inside the 30 mm verify window, outside what a tower would
forgive, and precisely the kind of number the recorder and a dataset exist to pin
down. This is the practice stage for the dreaming cycle's first campaign.

**The recorder's first live episode (run 0007), and the first unattended campaign.**
`FrankaEpisodeRecorder` mirrors NVIDIA's own real-YAM recorder in shape and
differs where the Franka differs: libfranka lets exactly one thread read or control
the arm, so a second thread's `get_state` would race the move the program is in the
middle of; the arm stream comes instead from panda_py's own 1 kHz log, fed from its
control callback. Cameras at 20 Hz on a daemon thread through the station's
threaded cameras (mp4 colour, 16-bit PNG depth every fourth sample), the gripper
sampled under a new env lock, every command as a host-timestamped event. Live: the
same round trip, 6/6 again, 156k arm samples and 2693 camera samples over 224 s,
zero sample errors, 136 MB raw (depth) + 9.6 MB video. Two things the data said:
the camera thread ran at ~12 Hz, not 20 — the PNG and mp4 writes sit in the
sampling loop (queue them later); and the robot clock gained ~1 % on the host over
the episode, so the converter scales by both clock pairs rather than trusting
either. ASPIRE at the recorder commit; `scripts/franka_raw_to_lerobot.py` in hebbot
builds a LeRobot dataset from run directories (state = q + gripper, action = the
next sample's, images from the mp4s via PyAV). The Spark imports ACT, Diffusion
Policy, SmolVLA and π0 under `~/venvs/lerobot`. Episodes relay omen → Mac → Spark
until omen's key is on the Spark (the classifier refused to add it). Then the first
unattended campaign: five round trips back to back, stopping at the first run that
is not ok.

**The chain closes: arm → recorder → LeRobot → ACT on the Spark, in one morning.**
Run 0007's raw episode relayed to the Spark and converted: 1 episode, 2693 frames at
20 Hz nominal, two 640×480 video streams, state = q + gripper width (ranges sane),
22 MB as a LeRobot dataset. ACT (52M parameters) trained on it for 300 steps as a
smoke test: ~4 steps/s at batch 8, GPU at 94 %, loss 15 → 4.9 by step 100,
checkpoint written. Nothing about that checkpoint is a policy — one episode is a
memorisation exercise — but every link in the dreaming cycle's pipeline has now
carried real data once. Meanwhile the first unattended campaign began: seed 8 ok.
The recorder's encoding now runs on its own thread behind a bounded queue (frames
dropped under pressure keep a NaN stamp), to land after the campaign so no run's
code changes under it.

**Run 0010 stops the campaign, and the bias is found in the hand, not the eye.**
The watchdog did its job: leg 1 landed 21 mm short in x, leg 2 came back to A 33 mm
off in y against green, and leg 3's descent to grasp fired the cartesian reflex —
the campaign stopped there, blue lying against green, arm home. The trace settled
the bias question that six-of-six had raised twice: after every placement descent
whose pre-place target was x 0.500, the *hand itself* ended at x 0.486–0.489. The
cartesian hold of 400 N/m in the plane could not beat joint friction, so the hand
slid ~13 mm toward the base while sinking 3 cm, and the block went where the hand
went. Stiffened to 1500 N/m in x, y (60 Nm/rad about), the drift fell to 2 mm —
and the bare hand then reported three false contacts 26 mm above the table at ~1 N:
with the plane held stiff, the hand sticks in free air until the 300 N/m z spring
overcomes friction, and a 0.05 s stall looks like a stop. Retuned: z 600 N/m (a
stick lasts about half as long; the press on contact is K_z times the lag at
arrival, and the lag halves too), the stall path must persist 0.3 s while the 4 N
force path stays immediate. Bare hand again: 0.0127, 0.0129, 0.0128 — 0.1 mm
spread — at 4.5–5.8 N instead of 8, drift 2–3 mm, lag ≤ 1 mm. Then the next
wrinkle: those descents took 5.5 s of wall time for 2.5 s of ticks; with the
recorder's threads alive the 100 Hz loop ran at ~45 Hz. panda_py's own 1 kHz loop
is C++ and unaffected, but the command sank per tick, so the real descent was slower
than asked. The loop now integrates the command by wall clock, measures the stall
over a wall-clock window, and reports its achieved rate. The round trip gained a
neighbour check: every block instance in the survey view is back-projected, the
fingers' landing points for the short-axis grasp are tested against them, the
long-axis grasp is the fallback, and a blocked pick stops the run instead of
gambling; an off-target placement stops it too. The LeRobot dataset on the Spark
is four episodes; the converter now labels by the program's own verdict, not the
harness's "it ran".

**The wrist camera has slipped on its mount (found 2026-11-17, ~13:50).** Three
grasps in a row missed the blue block in three different ways — two air closes along
the long axis, a pad standing on the block's top at TCP z 0.041 after a contact
approach — and a cross-check program that localizes the same blocks through the
wrist and the scene cameras put the two eyes 8–11 cm apart in y, the wrist's error
growing between surveys. The run-10 timeline dates the slip: leg 1's verify still
matched the hand's FK to 5 mm (13:24), leg 2's was 38 mm off (13:25), before any
reflex; then leg 3's descent to grasp fired the reflex on a block that was not where
the eye said. Physical proof: the wrist camera's own frame at the identical survey
pose (0.5, 0, 0.33) in runs 7, 10 and 16 — the first two are pixel-identical, the
third has the basket and every block shifted ~75 px while the basket itself never
moved. A loose bracket, the bench's old enemy (#157), moving under carry
accelerations and still moving. Everything vision-aimed since 13:25 is suspect,
including the recorded images; episodes 7–9 predate it and stay in the dataset.
Needs a hand: re-seat and tighten the mount, then re-calibrate the hand-eye (the
touch-truth method validated it to 2.5 mm on 11-13). What the morning's failures
were NOT: the contact descent (bare hand 0.1 mm repeatable, lateral drift now 2 mm),
the neighbour check, or the programs' logic — each fix on the way was real, and the
grasp approach by contact is what finally exposed the eye by feel: a pad on the block
top at exactly the block's height. The hebbot thesis in one morning: a system that
feels caught the camera lying.

**Doug, on the camera (14:10):** it is genuinely loose and flopping — the cable feeding
it had shifted and pulls on it; adjusted for now, a proper fix later. And a design
direction given mid-stream: *"we should design the system as much as possible to
withstand minor camera changes. It should self-calibrate constantly, if at all
possible"* — with the rider that we still make every effort to keep it steady.

**The eye learns from the hand (built and tested the same hour, 50 tests).** The
adapter keeps a running correction of the wrist camera's pose — a base-frame
translation, persisted between runs — and a program tool,
`franka_real.report_hand_eye_residual(seen_xy, truth_xy)`, updates it: a placement
residual (the camera's re-localization against the hand's position at release,
truth to a few mm) is averaged in at half weight; a touch-probed position is taken
whole; any residual over 2 cm comes back flagged as drift so the program can
re-probe instead of trusting the next look. The round trip reports after every
verified leg, so a campaign calibrates as it practises. `touch_calibrate.py` is the
bootstrap: a 5×5 grid of contact descents at 2 cm around the camera's estimate with
the fingers closed, a pad on the block top stops ~3 cm high, the table stops low; the
hits' centroid is the block, a 3×3 pass at 1 cm refines it; two blocks give the yaw
too. The standing lime needs a floor a hand-body's height below its top, or the body
would sweep it while the pads went to the table beside it — the probe geometry of a
200 mm-wide hand is part of the method. This is the see-vs-feel residual the
literature review called unpublished, now a tool with a history file.

**First touch calibration (run 0019): one block right, one lesson.** Blue by feel:
six coarse hits with the pads stopping at 0.046–0.058 (a flat block's top), centroid
(0.4865, −0.0134); the camera had it at (0.4898, 0.0133) — 27 mm off in y, 3 mm in x,
the same direction as the earlier verifies. The fine pass found nothing: a pad
landing on a block's edge at 4–6 N shoves a 55 g block on a smooth table, so the
block moved under the probing; the coarse centroid is the measurement, to ~1 cm. The
second target failed in two ways: "green block" from above the row returned the row
green, not the standing lime, and the grid around x ≈ 0.35 reported "contacts" at
0.06–0.10 m where nothing stands — the near-base stall the old probe_table also
phantom-tripped on (TODO has carried it since 11-13). A stall alone is not a touch;
a touch pushes back. Fixes: the two flat row blocks as targets (nearest instance to
the expected spot), probes only at x ≥ 0.40, a hit needs ≥ 2.5 N of rise, and the
probe presses no harder than 3 N. Nothing reached the correction file — that run
predated the tool — so the rerun after Doug's cable adjustment sets it from fresh
touch truth.

**The Spark's first real dream, for the record (14:30):** ACT on the three good
round-trip episodes (8,350 frames), 3,000 steps at batch 16, ~2 steps/s on the GB10,
loss 0.99 → 0.44 (L1 0.19 → 0.14), checkpoints at 1,500 and 3,000 in
`~/runs/act_rt3/`. Three episodes is a memorisation exercise and this checkpoint
will never touch the arm; the number that matters is the throughput — a night buys
~50k steps — and that the whole path ran unattended while the bench was busy
discovering that its camera had moved.

**The eye levels itself against the table (14:40–15:10).** After the cable
adjustment the wrist read blue 16 cm sideways and 6 cm up; the cross-check showed a
steady −9 cm in y with the z error swinging +6 → −6 cm across the image — a tilt of
~15–18° about the camera's x, which no translation can absorb. The table can: every
survey look contains it and it is horizontal. The adapter now fits the dominant
plane in each depth image (three refits with tightening bands shed the blocks and
the basket), rotates the camera pose about its own origin until the plane is
horizontal, shifts it so the plane sits at the configured table height (0.017), and
applies the hand-taught translation on top; a look too low to contain the table
reuses the last levelling; a fit that is not the table — few inliers, absurd tilt,
or above the camera — is ignored and reported. Tested with the synthetic camera
mounted 12° off: tilt measured to 0.5°, optical axis levelled to 0.01°, the plane
landed at table height. One bug worth its own line: a matrix product inside the fit
deadlocked against the recorder's threads in the test suite (OpenBLAS and Python
threads), intermittently and order-dependently — the fit is now BLAS-free by
construction, since it runs inside every look on the live arm.

**Doug, 15:20: the wrist camera is a D405, and it is being replaced.** He found the
Franka-supplied STL for a rigid D435 wrist mount; Max is printing it, hopefully for
this evening; the D435 moves from the tripod to the wrist and the D405 goes. Two
consequences. The D405's depth is only good to ~0.5 m, which is why the levelling
fit from the parked pose (camera 0.54 m up) found mush — 22 % inliers at a
21° "tilt" is noise, not the table; survey height (0.33 m) is in range and is the
test that counts. And the hand-eye starts over tonight: new intrinsics, new mount,
new offsets, and no scene camera to cross-check against — so touch truth (the
probe grid) and the table (the levelling) become the whole calibration kit, which
is the kit the day built anyway. Plane fit now seeds by RANSAC (three-point
samples, best consensus, then the refits): one least-squares plane over a view
that includes the floor past the table edge and the base landed 39° off.

**15:40 — the eye, re-taught by touch, and the second body smoke-tested.** With the
levelling in effect, the touch calibration (run 0028) found blue 26 mm from where the
camera put it in y and 4 mm in x — the residual the levelling leaves on a loose
D405 — and wrote the correction. The green measurement was discarded on review: its
grid at x 0.42 lost 10 of 15 cells to reach limits near the base and the hits'
centroid leaned 12 mm toward the reachable side; half a grid finds half a block,
so a grid with more than a fifth unreachable is now "partial" and not evidence. The
probing had also shoved blue 9 cm from its nominal spot, so the round trip's first
look rejected it; the first look now searches 15 cm and takes the block where it is
as A. On the Spark, Diffusion Policy (278M parameters) trained 500 steps on the same
three episodes at 0.79 s/step, loss 0.08 — both local bodies run on the GB10.

**16:00–16:40 — six campaign launches, six lessons, on a camera that changes angle
between looks.** Each launch runs the touch calibration first and stops at the first
run that is not ok; each stop was a specific defect, fixed and committed before the
next: an unreachable grid cell was fatal (now a cell without data); the calibration
measured green twice as "blue" because nearest-to-the-spot beat score (now best
score within 25 cm); a weak mask with no valid depth raised inside the sandbox (weak
masks skipped first); the block had been shoved past the first look's radius (two
looks, and a block found far from A is carried home first); a grid cut by the probe
box leaned its centroid like one cut by reach (counted as partial); one block by feel
is a calibration (the second adds only yaw). The run that then did calibrate
properly (0048: blue 2 cm, green 8 mm, yaw 0.5°) still lost its first pick to a pad
on the block top — the eye's look-to-look wobble on the loose D405 is ~2 cm, which
is the open fingers' whole margin. So the grasp approach now uses what the contact
told it: a stop above the grasp height means the block lies off along the finger
axis, and the approach retries shifted ±2.5 and ±5 cm along that axis before giving
up. Doug's question — D435 or D405 on the rigid wrist bracket — answered D405 (the
last centimetre is its range; the D435's depth is blind under 28 cm; two views for
the policy; the dataset stays consistent), with the surveys to drop to ~25–28 cm.

**17:05 — the loop closes on the loose camera: calibrate by touch, pick, place by
contact, teach the eye, repeat (runs 0056–0057).** The calibration (coarse pass only,
both blocks, yaw 4.5°) seeded the eye; the round trip then verified six of six legs.
Every pick straddled the block on its first approach (contact stop = floor, no
shift needed), every place stopped on contact at 0.027–0.040 with 4.5–6 N and 2–3 mm
of lateral drift, and after every verify the program reported seen-vs-released-at:
the first residual was (7, −20) mm — flagged as drift, taken at half weight — and
the next five were (3,−4), (2,−9), (−12,16), (3,−8), (−2,5) mm, the running
correction settling around (−0.002, −0.013). Placement errors against the target
4–24 mm, median 10. The D405 is still flopping on its cable; it just no longer
matters at this task's tolerances, because the eye is re-taught every forty
seconds by a hand that knows where it released. Fourteen campaign launches today
between the first slip and this; each stop was one defect, named in its commit.

**Run 0058: four more clean legs, then a hand.** Residuals 1–7 mm, placement errors
3–9 mm — the eye converged. Leg 5 closed on air; the scene video shows a human hand
reaching in beside the gripper six seconds before the grasp, and the wrist view at
the close has the block's edge at the bottom of the frame, centimetres from the
straddle the survey had fixed. Not a defect: the block moved after the look. Noted
for the record because the arm now runs unattended under a standing authorization —
anyone reaching into the workspace should say so first.

**17:50 — Doug tightened the bracket properly, and the campaign restarts from a
clean eye.** Before that: run 0065 placed the block home correctly by contact and
then the verify look jumped 29 mm — the loose camera moved on the carry from the
corner — so a drift-flagged verify now looks again with the taught eye before
calling a placement off-target; and the campaign skips the touch calibration once
the file holds placement residuals, since probing shoves the block 2–3 cm per
calibration while the placements teach the eye to a few mm. Doug then asked for
a hold, disconnected the D435, got the D405's bracket "much tighter", and
reconnected; the running campaign was killed (seed 69 cut mid-run, recorded as not
ok), the arm held where it stopped, and the correction file was retired — the camera
is in a new position again — before relaunching with a fresh touch calibration.

**18:30 — after the tightening, the eye starts from nothing, and the programs learn
to live with that.** Runs 72–88, one defect each: the calibration's retreat move
hit a reach limit and counted as failure (now not load-bearing); every first-leg
failure of the day had followed a touch calibration — its probing shoves the block
after measuring it, so the next look is wrong by the shove — so the campaign no
longer calibrates by touch unless asked, and the pick recovers from an air close the
way it recovers from a pad on the block, retrying shifted ±2.5/±5 cm along the
fingers and ±2.5 across (run 0085 held on its seventh attempt); the verify then
rejected the eye's reading as 17 cm from the target and called the block lost — an
untaught eye after a bracket change is that far out, and the distance is precisely
the residual the verify exists to report, so it now looks 20 cm wide. And run 0085's
seventh attempt had grabbed the standing lime, not blue, and set it down at A: a
place spot now gets looked at first, and a spot with a block within 7 cm is not a
place (the other spot, then a spare at (0.42, −0.12), then stop). A calibration
that probes a standing block with the flat-block floor sweeps it with the hand
body — the lime's wandering today is probably that; standing blocks are not
calibration targets.

**19:05 — runs 93–97: the eye bootstraps from one placement, then the lime gets in
the way.** Run 0093: the pick found blue on its seventh shifted attempt, placed it
home by contact, verified at 18 mm, and the first residual (12, −15 mm) taught the
untaught eye whole; then the next pick was blocked — the lime standing behind green
and the eye reading green 2.6 cm toward blue left no margin at 9 cm between the
spots, so A and B moved 3 cm outboard. Run 0097: the approach to blue, at the old
home spot next to the lime, stopped with "non-convergence" and nothing moved on the
table — the wrist camera hangs 6 cm toward the base from the fingertips and passes
~1.5 cm from the lime's top edge on that approach. The lime standing in the row is a
hazard to the camera on every approach and cannot be picked where it stands (green 3
cm away on one side), so this one needs a hand: Doug to move the lime away. Arm
parked, campaign stopped, a survey-height level check running to see whether the
bump moved the camera.

**19:40 — Doug, watching: "you've been rotating the gripper 180° sometimes, and then
missing a pickup... you just got the blue block on the third try, gripper nicely
centred on the long axis."** Both halves of that were a convention error that had
been hiding in plain sight since Friday. With `down_quat(yaw)` the hand's x axis
points along `yaw`, and the Franka Hand's fingers travel along the hand's *y* — so
the "short-axis grasp" at long-axis + 90° closed across the 55 mm length; the hold
width had said so all day (0.74 of 80 mm = 59 mm) and nobody read it. Twelve
millimetres of margin a side, which is exactly why a 2 cm eye error meant air, and
why the shifted retries worked when they did: the shift I called "along the
fingers" was across them. And the block's axis angle comes back anywhere in a full
turn while the gripper is symmetric, so half the grasps spun the wrist a needless
half-turn — swinging the camera to the far side of the hand into the bargain. Fixed
in run 0106's code: the grasp yaw is the long axis itself (fingers across the 28 mm
width, 26 mm of margin), the neighbour check and the retries use the true finger
axis, and the yaw is folded into (−90°, 90°]. Run 0105, the one Doug watched: four
legs verified (placement errors 2.5–20 mm, residuals ≤ 15 mm), the fourth pick on
its third attempt, the fifth blocked by green as the old finger geometry saw it.
The lime (standing light-green) is in the far corner now, red moved into reach.

**20:00 — Doug measured the hand with the pads on the table:** the D405 housing's
underside is 50 mm above the table (so ~50 mm above the pad bottoms, matching the
config's 54 mm origin height), and the camera's midline sits 55 mm from the gripper
centre. Consequences: at grasp depth on a lying block the camera clears the block's
top by ~29 mm — the camera-on-flat-block reading of run 0114 was wrong, those stops
at TCP 0.042 were pads on a block top (bare pads touch the table at 0.0128, so a
28 mm block's top is at 0.041 — exact) and the eye or the yaw was off for that
pick; but a standing block's top (55 mm) passes 2 mm under the camera, which is the
run 0097 collision with the lime. Standing blocks stay out of the row; the
camera-aware yaw choice stays for them. Pads on the table at the front of the table
read 17.9 mm against 12.8 at the row — the table is not level to 5 mm over 10 cm,
or the hand's pads are not, which the contact descent absorbs anyway.

**20:30 — runs 121–129.** 121: the first survey from the arm left on the table for
Doug's measurement blew the step budget — every program now goes home first. 125,
watched live by Doug: a pad landed on blue and pushed it, and the shifted retries
then "grasped at empty air" around a position the block had left — after any
contact stop the pick now lifts to the hover height, looks again, and continues
the search around the block's new position (129: it worked, held on the second
attempt). Then 129's pre-place stalled with the base joint at −2.88 rad, a degree
from its stop: the camera-aware yaw had picked the half-turn whose IK winds the arm
nearly a full turn. Of the two equivalent yaws the pick now rejects one whose IK at
the hover or at either place spot comes within 0.3 rad of a joint limit, then
prefers the camera-clear side, then the shorter turn.

**21:10 — three clean runs in a row (0137–0139): 18 legs, every pick on its first
attempt, placement errors 1–16 mm, median ~5.** With fingers across the width, the
eye taught by every placement, the yaw chosen for the arm's joints and the camera's
side, and the re-look after any pad touches the block, the round trip is now the
practice stage it was meant to be. Run 0140 verified five legs and then declined
a pick over green as the camera-footprint rule read it; the measured 29 mm of
clearance over a lying block means that rule now applies to standing neighbours
only. Dataset on the Spark: six clean episodes, 21k frames, three more relaying.

**21:40 — omen's /tmp is a 7.4 GB tmpfs, and 72 recorded runs filled it** ("Disk
quota exceeded" in run 0149's startup). Every run writes ~230 MB (the depth PNGs are
most of it). The run archive moved to ~/franka_runs on the 770 GB disk, /tmp/franka_runs
is a symlink to it, and the launcher and relay script point there. Also the
afternoon's drift: green had been nudged end-to-end with blue at the home spot;
run 0145's first pick was "blocked" by it along what was really the width, so a
blocked width grasp now tries the fingers turned 90° under the same checks.

**22:00 — nine clean episodes, and the first real dream.** The converter was being
OOM-killed on the Spark (it held both cameras' decoded frames for a whole episode,
~7 GB, beside the 83 GB Qwen server); it now streams frames through PyAV and lets
LeRobot's writer put images on disk as they arrive. Dataset: runs 7, 8, 9, 57, 113,
133, 137, 138, 139 — nine episodes, ~36k frames, the later ones 4,600–5,050 frames
each as the round trips grew retries and re-looks. `dream.sh` launched: it waits for
the GPU to be idle, rebuilds the dataset from every relayed episode, trains ACT for
5,000 steps and leaves a dated checkpoint. Run 0153 clean on the arm meanwhile.

**22:30 — Doug, watching the campaign:** only blue is ever carried; vary the colours,
stack sometimes (place on a block, pick off a block), and later build rows. Agreed in
that order of readiness. Done now: the round trip's colour rotates blue/green/red by
run seed and the spots jitter ±2 cm, the converter names the colour in each episode's
task string. Next: a stacking variant as a second program in the rotation (contact
descent with a higher expected stop; verify by height; two-high keeps the camera
39 mm above the lower block). Rows after the day's robustness helpers — contact
approach, re-look after a touch, joint- and camera-aware yaw, the wide verify — are
lifted into the task-context recipe every program copies. Caveat kept in mind: ACT
is task-blind, so mixed tasks want a task index or a per-task dataset; SmolVLA reads
the string. Run 0155's lesson: the spare spot at (0.42, −0.12) had no IK for the
retreat or the survey over it — moved to the table's front (0.60, −0.07). Runs
0153, 0154, 0157 clean.

**23:00 — omen's key is on the Spark (second try: the first append had carried
omen's login colour-escape instead of the key), and every run now pushes itself to
the Spark as it lands.** omen initiates all transfers — the Spark cannot reach omen
behind the Google Wifi mesh — which is the direction every transfer needs anyway:
episodes up, checkpoints pulled down later. Runs 0159 and 0161 were the first
non-blue attempts' honest stops: every spot taken with three colours in play (two
spare spots added), then red found nowhere because it rests at the table's left
edge beyond the first looks (a fourth look added).

**23:50 — the first full dream cycle completed on the Spark:** nine episodes rebuilt
from the synced archive, ACT trained 5,000 steps at batch 16 (loss 0.21 by step
500, the 1 kHz log lines sparse after), a dated checkpoint in
`~/runs/dream-20261117-1159/`. Not a policy anyone would run yet, but the night
job now exists end to end: omen pushes every run as it lands, the dream rebuilds
and trains on whatever is there. Evening campaigns with three colours: 0165 the
first red round trip (fetched from the table's left edge), 0177 blue clean, 0178
green's first three legs verified before a survey straight above a far spot left
the generator unconverged — the place spots are now judged from one look above the
row's centre. Each run teaches the eye a little; the residuals on verified legs sit
around 1–15 mm.

**00:30 — three colours, honest stops, and a block that stood up.** 0181 the first
green round trip, clean. 0182: red invisible at y −0.32, outside the hand's working
area — a colour the looks cannot reach now yields to the next, so the arm keeps
practising. 0185: one wild look read a block 24 cm from where the hand had just
released it — a fitted tilt more than 8° from the last accepted one is now rejected
as a wrong plane, and the verify looks twice before calling a placement lost. 0189:
blue slipped from the jaws on a carry and landed on end mid-table; the lying-only
look then called it lost, and the next run would have left it standing as a camera
hazard. A standing block of the colour is now carried too, ranked after a lying one
(the fingers close across its 28 mm at mid-height, the camera clears its top by
3 cm), the place expects the standing contact height, and the camera-hazard flag
uses the top height. Every one of these stops was the program refusing to guess;
every fix was one sentence in a commit.

**01:00 — green round trip 0201 clean (6/6, errors 7–16 mm); 0202 four legs then a
drop.** The block left the jaws on the carry and the verify's 16 cm "residual" went
into the eye at half weight, bending the correction by 8 cm — undone by hand in the
file. A block seen more than 5 cm from the release point is now reported as dropped
and teaches nothing. Both slips tonight followed a hold width a few millimetres over
the block's, a grip across a corner; the grasp force rises from 25 to 40 N. Earlier
in the evening: run 0193 blocked by green's own second detection (within 2.5 cm is
the block), 0197 carried the standing blue twice and then misjudged its posture
(standing is now top or footprint; only the table's y edge is out of bounds for a
standing block). Still every stop an honest refusal, still each fix a sentence.

**14:45 — the wrist camera's cable.** Run 0213 refused to start: the D405 was off the
bus; the cable had slipped through a tie at the elbow and pulled the connector. Doug
re-seated it, tightened the tie and added a second. Level check 0214 passed (tilt 2–3°,
the running correction a few centimetres; no recalibration needed). Run 0217: three
legs verified, then at 14:41:26 the camera disconnected mid-verify and the last look
read the block 10 cm off, so the new dropped-block guard fired on a dead camera
instead of a dropped block. The connection lasted ten minutes of motion. The bracket
Max printed is ready for pickup this evening; a USB cable that survives the elbow is
the other half of that job.

**15:15 — the front edge.** Doug's cable management continued (hub now taped to the
arm's first segment, the cable taped to the hub); each hub reconnect left the D405
enumerated but unable to stream until a `hardware_reset()`. Run 0223 clean, the first
full round trip since the cable work. Then the red block fell on the floor twice (0221
leg 6, and 0224 mid-pick): both times at the spare spot C, x 0.60, where the grasp
search's shifted attempts went out to x 0.627 with the fingers open and swept the
block off the front edge. C and D come in to x 0.57 and no attempt is centred beyond
0.60. Also learned: the blue block standing in the basket was a candidate (0219
reached for it, a reflex and Doug's bump stopped it); the basket side, y > 0.12, is
now out of bounds for looks.

**15:30 — duty cycle.** Doug's reading of the afternoon: the cable trouble is a
consequence of our own success. The arm now makes a few hundred reaches a day across
the whole table, the elbow swinging through its full travel, a different duty cycle
from the handful of careful runs the cable routing was taped for. The routing has to
come up to the same standard as the motion; the rigid bracket and a short, properly
routed cable this evening are that. Duty-cycle problems are good problems to have.
The campaign script now hardware-resets the camera and retries once when a run gets
no frame, so a re-enumeration between runs no longer stops a batch. Next things the
duty cycle will find: the finger pads, and the tape on the bracket.

**15:50 — the grasp at the end of the block, and why the eye could not see it.** Doug:
"very frequently you're grabbing a long block from very close to one end, and
sometimes you miss completely; grab near the middle." Scene-camera frames at the
six grasp closes of run 0231 confirm it: the fingers sit 15–20 mm from the end
nearest the robot (the camera's side of the hand), every time. Yet the pick estimate
against the previous release point agrees to 2 mm, and the placement residuals are
under a centimetre. The reason is structural: the fingers centre a held block along
their travel, so a placement residual exposes the eye's error on that axis; along
the block's length nothing centres it, the biased grasp is carried from pick to
place and back, and the residual reads zero for any bias. The correction file had
no touch sample at all (retired and recreated at a camera change; every lesson since
a placement). Touch is the only truth on that axis. Changes: touch lessons carry
weight 1; the calibration runs first in every batch and probes whichever lying
blocks it finds; and the correction now lives in the hand frame, where a shifted or
tilted mount puts the error, so a lesson at one wrist yaw holds at another. Also
today: the IK reseeds from home when the arm has wound into another branch (0229).

**16:20 — the touch found it.** Calibration 0240 (green lying, red on end gave a partial
grid): the eye read green 30 mm short along x, toward the robot, the very end the
fingers kept landing on; the lesson was taken whole, in the hand frame. Run 0241
(green): 6/6, every pick on the first attempt, placement errors 2–14 mm, and the
scene-camera frames at the closes show the fingers at the block's middle. Also:
the calibration now probes a standing block (floor 25 mm below its top, the body
clear), and takes whichever lying blocks it finds rather than blue and green at the
row's start positions.

**17:15 — the first clean batch.** Touch calibration 0254, then runs 0255–0260 all
6/6: green ×4, red ×2 (red carried standing). Four stops before it, all the camera:
the stream stalled mid-run three times after the hub was retaped (a stale frame now
fails the run by name — "the wrist camera's last frame is 3.2 s old: the stream has
stalled" — and the campaign resets and retries), then the cable at the far end of the
hub pulled out; Doug: "all of our cable-management fixes held, but this time the USB
cable at the other end of the hub pulled loose. More tape." Thirty clean round trips
on the bench now, nineteen of them since the colour rotation; the next batch (0261)
is on the arm and the Spark is dreaming on all thirty (10 000 steps of ACT).

**18:30 — second clean batch.** 0268 calibrated, 0269–0274 all 6/6. Before it, 0261's
batch went five clean and stopped on a pick (0266: the red's position wandered 2 cm
between looks, twelve approaches, no grasp) as the room went dim with the sunset; the
placement lesson weight came down from 0.5 to 0.15, since at 0.5 the noise walked the
hand-frame correction 3 cm over the afternoon and touch now anchors every batch. The
Spark is a third of the way through a 10 000-step ACT dream on thirty clean episodes.

**19:45 — the second dream, and a third clean batch.** Spark: ACT, 10 000 steps on
thirty clean episodes (green, red, blue; standing and lying), checkpoint at
~/runs/dream-20261117-1703, pulled to omen:~/policies/. Nothing is promoted: that is
the bench's paired gate, still to build (the learned_policy body and a program that
runs a checkpoint beside the written skill). Bench: 0276–0281 all 6/6, and 0283–0284
after another calibration; nineteen round trips in a row since the touch calibration
became the start of every batch. Every stop today has been the camera or a hazard of
my own; the skill itself has not missed since the eye learned where the block is.

**20:15 — the probe that shoves.** Three stops since the clean streak: 0286 a grip
across the block's length (59 mm hold) that let go on the carry — the gripper reading
is now the tell, open and turn 90°; 0291 a lying block taken as standing from a
shrunken mask (the top height now decides unless it is ambiguous); and 0293 eleven
closes in front of the block right after a calibration. The last one indicted the
calibration itself: the probe grid swept across the block from one side and the
pads shoved it ahead of them (hits 6 cm wide across a 28 mm block, centroid a cell
toward +x), the eye took that whole, and every pick then overshot. The grid now
probes centre-outward, the pads landing first on the block's top where they cannot
push it, and stops after an empty ring. Also tonight: a reverse SSH tunnel from omen
to the Spark (Doug installed it), so the bench is reachable from Nashville tomorrow.

**21:45 — an empty table.** After the centre-out calibration: 0295–0296 clean, then
a run of single-fix stops — a corner grip (17 mm hold) re-looked and retried; the
front of the table 5 mm higher than the row, so a contact within 8 mm of the grasp
floor is the floor; a standing block read as lying from 33 cm up, so an empty first
close brings a look from the hover; a dropped depth frame killing a run (the API now
takes a fresh frame, three times). Then calibration 0306 chose the green standing at
x 0.605, its outer probe ring reached 0.625, and the block went over the front edge;
red had gone too, and run 0307 found an empty table and stopped. Probes now stop at
x 0.60 and targets at 0.56. The arm is parked; the blocks are on the floor until
Doug is back. Eight batch-starts of touch calibration today; the eye and the touch
agree to a few millimetres when the probe does not shove.

## 2026-11-18 (Wednesday) — two greens, and the bench on its own

**06:30 — blocks back, basket gone, two greens.** Doug put the blocks back and moved
the basket off the bench entirely ("the table space it occupied is making you feel
crowded"); it now sits by the base, out of the work region. Calibration 0308: touch
residual under 5 mm on both axes, the smallest yet. 0309–0312 clean, then 0313 stopped
on a "drop" that was not one: there are two green blocks on the table now, and the
verify look after the placement, which ranked masks by score, picked the other green
15 cm away. The block sat exactly where the hand had put it. see_lying now ranks
lying first and then nearest the point it was asked about, which is the right rule
for every look the program makes. Batch 0314 launched on it.

**07:00 — the hover disagrees with the survey.** 0314 calibrated (6 mm, then 10 mm);
0315 placed the blue beside the green, then the next pick: a pad met the block at the
survey's position, the fingers turned, and the re-look from the hover put the blue
3 cm from where the survey had it, twelve descents on that spot, every one a pad on a
block (the blue and the green were touching, so the search had nowhere to straddle).
A hover re-look that moves the block more than 1.5 cm and is then contradicted by the
next pad is now distrusted; the search goes back to the survey's reading. Behind it, a
pattern in the records since 0295: touch calibrations settle the eye near base y
−0.005, the placement lessons walk it to −0.03 and hold it there with residuals that
never shrink, and both states run clean. The walked correction is a 31 mm hand-frame
offset, which is what the hover and the turned hand disagree about. Written up in the
TODO as a question to settle with a controlled test; the batch's calibration
re-anchors the eye meanwhile. 0316 launched.

**08:20 — a full table.** 0316 calibrated (its two targets disagreed by 3 cm between
them: 20 mm one way, 11 back), then 0317–0320 clean. 0321 placed the blue at E and
stopped with no spot for the next leg: four blocks on the table now, the two greens
at C and D rule out A and B by the 7 cm clearance, and E held the blue itself. Two
spots in the table's middle, F (0.47, −0.10) and G (0.47, 0.08). 0322 launched.

**Midday — two batches six for six, from the road.** 0322 calibrated, 0323–0328
all clean with the middle spots F and G in play; 0329 calibrated, 0330–0335 all
clean. Twelve round trips without a stop, the first full batches since the touch
calibration became the start of each one. The Mac left for Nashville between them,
and the omen-away route failed off the home network: its first hop was `galahad`,
a LAN name. `candleshoe`, the house's public address, lands on Galahad and the jump
through it to the Spark works; the route now goes candleshoe, spark, tunnel. 0329
was launched and watched that way; 0336 launched back on the LAN.

**Afternoon — the learned-policy runner and the paired gate, built.** Doug asked
whether we were ready; the answer was yes, and the pieces went in this afternoon
while 0336 ran. (1) `scripts/policy_server.py`: a LeRobot checkpoint behind a small
HTTP server in the lerobot venv (loads the ACT model and its pre/post processors,
takes state, hand position, wrench and both camera images as the converter wrote
them, returns the 100-step chunk). Smoke-tested on the Spark against
dream-20261117-1703: 30 ms an inference after warm-up, joint targets near home.
(2) ASPIRE `franka_real.policy_chunk` (5b5dc59): the adapter sends the observation,
takes the first N steps of the chunk through the same guarded waypoint path as every
program motion (joint limits, the workspace box, the robot's reflexes; speed factor
0.10), applies the chunk's final gripper width after the motion, and names the
policy in the record on every chunk. Three tests. (3) `learned_round_trip.py`: the
policy drives from home, the program watches the gripper and the hand for grasps,
carries of 8 cm or more, and releases; stops on a refused chunk, a fault, a chunk
budget or a still hand; then surveys the table and reports what moved. (4)
`scripts/franka_gate.sh` alternates program and policy seeds after a touch
calibration, and `scripts/paired_gate.py` tallies verified legs against carried legs
and applies the rule written on Tuesday: at least 20 pairs, the policy matching or
beating the program, no safety events. Torch is not on omen yet (its harness runs
Python 3.14 on the realtime kernel; a separate 3.12 venv is the plan and the
installer is Doug's to run), so the first gate runs with the Spark as the policy
host over the LAN. Expectation unchanged: the first gate probably fails, and the
failure measures how many episodes the stage needs. The checkpoint was trained on
whole six-leg episodes, so that is what it is judged on; legs give partial credit.

**Evening — the first gate stops on a clump, and the arm learns to sweep.** The
gate at 343 calibrated and then its first program run, 0344, came back
approach_blocked: blue wedged between the two greens, touching both, twelve pads
on a neighbour. I asked Doug to spread the blocks; he said no, it was time the arm
learned to deal with it, and described the move: start in the clear beside the
clump, lower the closed fingers to just above the table, drive straight through
the clump to roughly ten centimetres beyond it. That is `declutter.py` (ASPIRE
8b5c918): a survey from above the table, blocks whose centres chain within
6.5 cm form a clump, a second look from above the clump, then the sweep, which
crosses the clump's long axis (so the bundle splits a row instead of pushing it
along as a train) and always runs toward the table's middle; up to three sweeps,
then a last survey, and ok means no clump remains. The pusher is the closed
bundle of shove_stack.py at 30 mm. The first attempt (0345) made no sweep: its
hand box ended at y 0.15 and the clump sat at 0.09, so both directions were refused
for room; the box now reaches 0.18 (the basket is gone), and the start is set off
the clump's width along the sweep line rather than its full radius. The second
attempt split blue from green with one sweep, 5.3 cm to 8.3 cm centre to centre
(blue moved 1 cm, the green 2.6 cm; the bundle went between them). A leaning-block
test from the top height was dropped: a lone red read 0.052 and 0.060 on two looks.
The gate now sweeps before every program run (`franka_gate.sh`), under the
program's seed, and relaunched at 346.

**Late evening — two more stops, two more rules.** The gate at 346 stopped at its
own declutter (0347): the calibration's probing had left blue and a green 6 cm
apart at x 0.55, the front of the end box, and the planner found no room either
way (one start lay 1.7 mm past the start box; the other push ran into the front
edge). A sweep's start may now sit anywhere in the clear out to the table's edge,
nothing to drop there, and when the across sweep has no room a push along the
clump's axis brings it in for the next sweep to split (ASPIRE 3c090bd). The gate at
348: calibration, a declutter that found the table already clear (probing had
moved blue 4 cm), then 0349 verified four legs and met air twelve times on the
fifth. The leveling had found no table plane on any leg of that run ("not the
table", a third of the points as inliers), so heights were raw, and a lying green
read 0.062 high: the top alone made it standing, and the pads closed at a
standing block's mid-height, 2 cm above the block. Its footprint was 62 mm, a
block's length, which no block on end shows; standing now needs a top above 6 cm
and a footprint under 52 mm (047084c). Relaunched at 350. Still no policy run:
three gates, three program-side stops, each a rule the skill lacked.

**Night — the first policy run, and the eye walks off.** Gate 350: calibration,
declutter (clear), 0351 six for six, then the first policy run, 0352. Six chunks
of ten steps from home with the gripper open; every chunk crept the same way, the
base joint turning 15 degrees and the shoulder leaning back, the hand 6 cm toward
the base and a little up, never toward a block. The sixth chunk's first waypoint
put the hand at x 0.249, past the box's near face, and the guard refused it before
anything moved. Carried 0, no block disturbed; the refusal is the run's one safety
event. Three seconds a chunk round trip through the Spark, including two camera
frames. The expected first result: 52 episodes and chunked open-loop execution at
a tenth speed have not learned the reach.

Then 0353 (program) verified one leg and met air twelve times on the second: the
survey and the hover re-look disagreed by 6 cm. The correction file says why. The
placement lessons had walked the hand-frame offset from 0.025 after the touch at
350 to 0.053 by the end of 0351 (every residual +2–3 cm in base y, ten in a row,
none shrinking as the offset grew) and 0.059 by 0353's first leg. A lesson that
does not reduce the next residual is not measuring what it corrects. The
placements now report their residual at weight zero (ASPIRE 29b4528);
touch alone sets the eye, first in every batch; the gate's calibration
at 354 brings the offset back to touch's fit in one step (alpha 1). The TODO's
two-authorities question now has a cost attached and a clean experiment waiting:
a run of placements with the offset held, to see whether the residual is the
grasp (constant per block orientation) or the eye (a height-dependent tilt).

**Later — the arm winds itself up.** Gate 354: the touch brought the eye's offset
back from 0.059 to 0.035 hand y in one step, the declutter found the table clear,
and 0355 verified two legs with residuals of 1–2 cm under the held eye. Its third
leg picked after a lengthwise close and a turn, then the carry to E was refused by
the guard: the planned descent from carry height to the pre-place pose passed
through y −0.33, z 0.59. The joint targets explain it: the base joint was at −137
degrees before the pick, the wrist at −2.85 rad against its limit. Turned grasps
add up, the analytical solver keeps the solution nearest the seed, and once the arm
has wound round every joint-space path swings the hand wide; the harness's
fallback to the home branch (run 0229) only fires when the seed's branch has no
solution at all. The program now goes home before a carry when the base joint is
past 86 degrees, block held, and once more if the guard refuses a carry path
(ASPIRE f6d914a). Relaunched at 356. Four gates tonight, four
program-side stops, each a rule: the clump, the sweep's room, the standing
footprint, the walked eye, the wound arm. The program is being hardened by the
gate before the policy gets its ten runs, which is the gate doing its job in the
other direction.

**00:10 (19 Nov) — the unwind was wrong, and it cost the blue block.** Gate 356:
calibration and declutter fine, then 0357's first leg picked blue at the front
edge (x 0.598), the base joint at −131 degrees, and my new rule sent the arm home
with the block "held". go_home opens the jaws before it moves; my check for that
had grepped the function's text up to its first return, which is the error branch
above the gripper line. Blue dropped from carry height at the table's front edge
and is not in the scene camera's view: on the floor, most likely, by the front
edge near y −0.09. The run then placed air and called the block lost. The gate
goes on without it (a colour the looks cannot find yields to the next), but the
tally will need it back.

The IK, run offline on omen for the wound poses of 0355 and 0357: a down-pointing
hand at yaw ~80 degrees (fingers across a block lying across the table) has no
solution near the home branch at all; every valid solution has the base joint
past ±100 degrees, from the home seed as from the pose itself. So the wound
configuration is the arm's ordinary reach for such a block, not a fault, and it
has carried hundreds of legs. What failed in 0355 was narrower: the carry to E
with the wrist 0.05 rad from its stop, and the yaw check at pick time covered
spots A, B and C only. Reverted the unwind (ASPIRE 5f4ad1d); the yaw check
now covers the hover and the carry and place heights at every spot. Relaunched at
358.

**00:40 — the base side is out of reach, and the sweep brings strays in.** Gate
358 stopped at its calibration: the only target in the probe band was the red at
x 0.34, and 20 of 25 probes were refused, every cell at x 0.36 and nearer. Nearer
the base than about x 0.38 the hand cannot get down to the table; the red has sat
there untouched for hours, and the two greens had been left at the front edge
beyond the probe band. So the table had drifted into a state the skill could not
use, with no clump to blame. Three rules (ASPIRE d77954e, then b78fd7c): the
sweep now also brings a stray block outside the working band (x 0.40–0.56, y
−0.26–0.15) toward the table's middle, one a pass, starting out past the front
edge if need be (nothing to drop there; the box ends at 0.66), and reports a block
it cannot get behind as marooned; the pick no longer reaches for a block nearer
the base than x 0.38; the calibration's targets start at x 0.42 so the grid's
inner ring stays reachable. Run 0359: one green brought in from 0.576 to 0.502 in
one sweep; the other green, at 0.593, needed a start at 0.655 and got it on the
next commit; the red is marooned at 0.34 until a hand moves it. Relaunched at 360
with three blocks on the table, one of them out of play.

**01:30 — the eye, measured against the hand.** Gate 360 calibrated on the one
green in the band, the declutter found nothing to do (the other green had been
probed out to x 0.603, the red marooned), and 0361 verified one leg and called the
second off-target: the hand released the block within 5 mm of B and the eye saw
it 3.7 cm away, three looks running. The residuals of every leg tonight, laid
beside the levelling report of its verify look, split cleanly: about 1 cm when the
look fitted the table afresh, 2–4 cm when it reused an earlier fit, and the fit
failed on most looks at 0.33 m (inliers 0.30–0.32 against the 0.35 floor; "not
the table"). So I wrote `eye_check.py`: the green at B, whose position the hand
knows, looked at from 0.33, 0.25 and 0.20 m at wrist yaws 0 and 90, every reading
against the truth with that look's levelling report. Run 9001, levelling on:

| height | yaw | fit | residual (x, y) |
|---|---|---|---|
| 0.33 | 0 | reused (inliers 0.30) | +1.9, +2.6 cm |
| 0.33 | 90 | reused (0.20) | −1.1, +1.1 |
| 0.25 | 0 | fresh (0.65) | +1.2, +0.4 |
| 0.25 | 90 | fresh (0.48) | −0.8, +1.2 |
| 0.20 | 0 | fresh (0.92) | +1.3, +0.4 |
| 0.20 | 90 | fresh (0.62) | −0.8, +1.2 |

Two things in one table. At 0.33 m the D405's table is too noisy for the plane
fit, so the look reuses whichever fit came last, and the reading is off by up to
3 cm, which is what every survey, every verify and every touch calibration has
been living with. Below 0.25 m the fit holds and the residual is a steady 1.3 cm
that rotates with the wrist: (+1.2, +0.4) at yaw 0 becomes (−0.8, +1.2) at yaw
90, which is a hand-frame translation, the thing the touch offset is supposed to
hold, learned through looks at 0.33 m that were themselves off. Next: the same
check with the levelling held off (a switch, CAP_HARNESS_WRIST_LEVEL=0, ASPIRE),
to see whether the calibrated camera pose alone is as good at 0.33 m as the fresh
fit is at 0.25; then the surveys move to 0.25, or the levelling goes, whichever
the numbers say. The first attempt at the switch skipped the fit entirely and the
run died in the sandbox: a C-level import under the program's frame, whose
builtins are a mapping proxy; the fit now runs and is merely not applied.

**01:55 — levelling off: the calibrated pose alone.** Run 9004, the same six looks
with the fit run but not applied:

| height | yaw | residual (x, y) |
|---|---|---|
| 0.33 | 0 | +0.6, +1.3 cm |
| 0.33 | 90 | −2.5, −0.3 |
| 0.25 | 0 | +0.7, +1.1 |
| 0.25 | 90 | −2.1, +0.1 |
| 0.20 | 0 | +1.0, +1.0 |
| 0.20 | 90 | −1.9, +0.3 |

No height dependence to speak of (6 mm at most across 13 cm, a tilt under 2.5
degrees if it is one), 1.4 cm at yaw 0 and 2.1 at yaw 90, steady look to look. Not
a pure hand-frame translation either (the yaw-0 error rotated would give (−1.2,
+0.8) at yaw 90, not (−2.1, +0.1)), so part of it is in the base frame: the
block's hand-truth could be off by a few millimetres, or the camera's yaw. The
decision: the reuse of a stale fit at 0.33 m was the 3 cm, and no fit at all is
the steadier eye. The gate and campaign scripts now run with
CAP_HARNESS_WRIST_LEVEL=0 (ASPIRE 069cb3a and its rework); the touch calibration
at the head of each batch learns the offset under the same regime it will be used
in, which it never did before. Doug is back at the bench and has picked up the
blue block. The red is still marooned near the base and a green sits at the front
edge; those want a hand too.

## 2026-11-19 (Thursday) — a new bracket, and the base side

**Morning — the bracket.** Doug fitted the D405's new bracket, the one that will
not wobble. The level check afterwards: camera 36.5 cm above the table by the
hand's pose, the fitted plane 36.5 and 36.7 below the camera. The camera's height
survived the swap; x and y are the touch calibration's to learn, and it does, from
scratch, at the head of every batch.

**Morning — "not seen", then a standing green.** The first gate after the swap,
362, calibrated on nothing: no lying block in the probe band. Doug had set the
blocks out again, two of them on the base side (a green at 38 cm out, the red at
36) and the blue off the far left edge; the band starts at 40. The next gate's
calibration took the green at 38 and found a top at 6.7 cm over a 4 by 8 cm patch
where the eye had read 5.5: a block on end, or leaning, that the eye called lying.
Then the reach check: a straight-down hand at 26 to 42 cm out, two heights. The low
moves at 30 and 32 cm aborted by reflex, and at 34 the harness hung on a
self-collision reflex and never came back; the process had to be killed, the D405
reset, and the arm parked by a one-line program. Doug, watching the arm try for
the red: the IK is missing some obvious solutions, and fixing an IK problem is
more complicated than moving some blocks. He moved them. The band stays at 40 cm;
the IK and the reflex recovery are in the TODO. Gate 366 launched.

**11:10 — the eye is right, and my own rule bit.** Gate 366's calibration, the
first under the new bracket and the first with the levelling off, learned a
hand-frame offset of (−0.3, −0.3) cm where the old bracket had settled at 3.5 cm:
the camera is now within 3 mm of the pose the harness was configured with. Then
0367 verified three legs with residuals of (0.2, 0.9), (0.1, 0.9) and (0.2, 1.1)
cm, the same number three times, which is what a correct eye with a small
constant left in it looks like; the walked offsets and the 3 cm verify misses are
gone with the stale levelling. The fourth leg was refused by my widened yaw check
from last night: a margin of 0.22 rad at some spot the leg was never going to. The
check now covers the hover, A, B and C, and the carry and place heights at the
slot this leg will use (ASPIRE c9ee470). Gate 368 launched.

**11:40 — six for six, then the policy again.** Gate 368 stopped at its
calibration (a blue at the far edge as second target, a probe at y −0.33 refused,
and a communication reflex on the descent with the control loop's command success
down to 60%); the work box now ends at y −0.26 (ASPIRE b92064b). Gate 370:
calibration, declutter, then 0371 six for six with placement errors of 5–9 mm and
residuals under 1.2 cm on every leg, the cleanest run on record. Policy 0372: five
chunks, the same slow lean toward the base as 0352, refused by the guard at x
0.239 with nothing touched. The gate goes on; the tally so far is 12 legs to 0.

**11:45 — five pairs, then the control loop.** Gate 370 ran five pairs: programs
0371, 0373, 0375 and 0377 six for six each, policies 0372, 0374, 0376 and 0378
zero each (four or five chunks, the lean toward the base, the guard). 24 legs to 0
on the tally. Then 0379 aborted its first survey on a communication_constraints
reflex, the control loop's command success rate down to 77%, one packet lost in a
row. The same reflex hit calibration 0368 and twice inside 0377, where the
harness's automatic recovery carried on unseen. That is omen's 1 kHz loop losing
packets, not the skill; load average 2.3–3.5 on a box that should be idle but for
the harness, the SAM3 service and the episode sync. Relaunched at 380 while
looking at what is loading it.

**12:00 — the control thread at ordinary priority.** What loaded omen was Chrome on
its desktop, half a core, niced. That should not have touched a 1 kHz loop on a
realtime kernel, and the reason it did: the harness builds the Panda with
panda_py's default, RealtimeConfig.kIgnore, so libfranka never asks for
SCHED_FIFO and the control thread shares the run queue with the browser. Every
cap-harness thread showed policy TS. Now kEnforce whenever the kernel is a
realtime one (ASPIRE 12d7dbf): the next run's control thread sampled at FIFO 99
while moving. Gate 380's calibration passed under the old scheduling; its
program run at 381 is the first with the new.

**12:30 — the sync.** 0381 verified three legs at 3–5 mm and then lost the link
twice more (success rate 63%, then 56%) with the control thread at FIFO 99. So
not scheduling. The robot's NIC is a wired r8169 on its own interrupt line,
serviced on CPU 12 while the GPU's interrupts live on 2 and 4, and its
missed-packet counter did not move across the failed run: not the card. What
has changed is the episode sync: after every run the gate started an rsync of
the whole archive to the Spark in the background, over omen's LAN, which is a USB
WiFi adapter; the archive has grown to where the sync overlaps the next run's
motions, and USB WiFi drivers are a known source of tens-of-milliseconds
stalls. 44% of a window lost is a stall of that size, and the reflexes fell
where a sync would be running: 0368's calibration after 0367's sync, 0379's first
survey, 0381's fourth leg. The sync is now blocking, between runs, niced and
rate-limited (hebbot scripts/sync_episodes.sh, gate and campaign). If the
reflexes stop, that was it; if not, the IRQ on CPU 12 and the WiFi driver get a
closer look.

**12:45 — not the link.** Calibration 0382 lost the link twice more before the
sync change could matter. Measured instead of guessed: 15,000 pings to the robot
at 1 kHz with the arm idle, no loss, 2.7 ms worst; 60,000 during gate 384's
calibration motions, no loss, 3.0 ms worst. The wire, the card and the kernel's
network path are fine while moving. What stalls is inside the harness process
itself: libfranka's callback not answering within a millisecond for a 40 ms
stretch, repeatedly. Candidates in order: the GIL held by a camera or recorder
thread when the control thread needs it, Chrome on omen's desktop at 27–36% of a
core since Doug sat down there this morning (the first reflex came twenty minutes
after), and the two RealSense streams on the same USB host controller. The clean
runs this morning (0371–0377) ran under the same code as the failing ones, so it
is load, not a change of ours.

**13:10 — survive the reflex.** Gate 384: calibration clean, the declutter swept
twice (blue in from the front edge, then the blue and a green apart; Doug saw the
sweep live), then 0385 lost the link on its first survey. A motion-only program
(`survey_loop.py`, 16 survey poses with the recorder on and Chrome busy) ran
clean, so the stalls are rare and I am not going to find them by inspection this
afternoon. What I can do is stop them costing runs: the reflex is transient and
the robot recovers in under a second, but the harness reported the move as failed
and every skill stops on a failed move. The env now recovers, pauses half a
second, sends the same batch again, and only then reports non-convergence; the
retry is an event in the episode, so the count stays visible (ASPIRE 43ae409).
Adapter tests 43 passed, the one pre-existing levelling tolerance failure as
before. Gate 386 launched. Open in the TODO: where the 40 ms stall lives.

**13:40 — and the descent.** Gate 386's calibration lost a probe mid-descent to
the same reflex, which the waypoint retry does not cover: the contact descent
rides the impedance controller. The adapter now recovers and descends once more
from wherever the hand stopped (ASPIRE b2a5441); a second abort is reported as
before. Gate 388 launched. Chrome on omen still at a third of a core; no word yet
on what it is doing.

**14:05 — the best run yet, called a failure.** Gate 388: the calibration took one
reflex on a waypoint move, recovered, resent, and passed; the declutter had none;
then 0389 verified all six legs with placement errors of 0.6, 3.2, 0.6, 2.9, 3.4
and 1.9 mm and residuals under 7 mm, the best run on record, and came back not ok:
a reflex cut a placement descent short 1.9 cm above the block, the retry started
from there, and the descent's own minimum travel refused it. The retry may now
start near the floor (ASPIRE b834a19; adapter tests 44 passed). Gate 390
launched. Reflex count today: nine, two of them now survived.

**14:20 — hard interrupts.** Gate 390's calibration took three reflexes in a few
minutes, the loss a steady 22–25% of the window: not a 40 ms stall any more but a
loop that is late a quarter of the time. omen's state: every core on the
powersave governor, idle states down to C3 enabled, nothing isolated, the GPU's
two busy interrupt lines on CPUs 2 and 4 (107 and 205 million interrupts since
boot), the NIC's on 12, gnome-shell at 18% and Chrome at 27% of a core, so the
desktop is live and the GPU is compositing. A hard interrupt preempts even a
FIFO-99 thread, so a control thread that lands on CPU 2 or 4 pays for every
frame the desktop draws. Changes, all reversible: the harness runs under
taskset on CPUs 8–11 (gate and campaign scripts), the governor is performance,
and C2/C3 are off on those four cores. Gate 392 launched; the retry events say
how many reflexes a run takes now.

**14:40 — EEE.** Doug's thought: the cabling changes, omen moved back in its
enclosure, the WiFi antenna moved. Checked: the robot's wired link is 1 Gb/s full
with no errors and no link change since the 9th; the WiFi sits at −44 dBm,
780/866 Mb/s, no drops growing. Neither moved. But the robot NIC has
Energy-Efficient Ethernet enabled and active, Tx LPI 12 µs: the link drops into
low-power idle between packets and pays a wake-up on every one, and Franka's own
FCI guidance is to turn that off. With the control loop's 1 ms budget already
spent on cameras, recorder and a live desktop, that wake-up is the kind of margin
that goes missing a quarter of the time. ASPM is already off, the TX queue drops
nothing. Pinning the harness to CPUs 8–11 did not stop the reflexes (two more in
392's calibration), so the CPU side was not it. EEE goes off as soon as the
current run ends (turning it off restarts autonegotiation, which would drop a live
FCI connection); the xhci interrupts have been moved to CPUs 13–14 meanwhile.

**15:00 — EEE off, and the controller itself.** The calibration at 392 passed,
surviving two reflexes on the retries; the declutter at 393 ran, and with the
gate script stopped first, EEE went off on the robot's port the moment the run
ended (the gate and campaign scripts now turn it off themselves whenever they find
it on). The first ping after the change lost one packet in two, which read for a
minute like a degraded controller (Doug could not log back into Franka Desk at
the same moment); fifty more at 10 Hz lost none, 0.27 ms worst, and Desk answered
omen in 0.36 s. The lost packet was the link coming back from autonegotiation.
The controller is fine; Desk's login trouble is a session matter, and a reboot is
Doug's call, not a need.
On WiFi: Doug would like omen wired to the LAN but the cabling is hard, and WiFi
should work fine; the numbers agree (−44 dBm, no drops), and nothing today
touched the WiFi path.

**15:20 — not EEE either.** Gate 394's calibration, the first with EEE off: two
reflexes in 95 motions, both survived by the retries, the calibration ok. So the
list of things that were not it now reads: the wire, the card, the WiFi, the
episode sync, the control thread's priority, the CPUs it runs on, the USB
interrupts' CPUs, the governor and idle states, EEE. The rate is about two
reflexes per hundred motions since late morning and was zero through the night
and until 11:00. What is left: inside the process (the interpreter's threads
against the control loop), the controller's side, and whatever changed at 11:00,
which was the bracket swap, a camera replug, Doug at omen's desktop, and omen
moved in its enclosure. Next measurement: cyclictest at FIFO 98 on the harness's
CPUs during a run, which splits host latency from in-process stalls.

**15:50 — the hang.** Program 0395 took a reflex early, the harness logged
"Attempting automatic error recovery" and never came back; the gate's 1500 s
timeout killed it twenty-five minutes later with no result written. The same
shape as the reach check's hang this morning (9006). So of today's reflexes, most
recover in a second and two have hung the process in panda_py's own recovery.
The gate now treats a run with no result as hung: it resets the wrist camera,
parks the arm through park.py, and goes on to the next pair, stopping only on two
hung runs in a row; the per-run timeout is 900 s (hebbot scripts/franka_gate.sh).
Where the hang sits (panda_py's recovery inside the control thread, or the
harness's retry on top of it) goes in the TODO beside the stall itself.

**16:00 — the sweep's start and the box.** Gate 396 calibrated, then its
declutter went for the blue at the front edge (x 0.593) from a start at 0.656 and
the guard refused the hover: the joint-space path to that start bulged to 0.661,
past the box's face at 0.66. Starts now stop at 0.645 (ASPIRE, declutter.py); a
stray beyond x 0.58 is left marooned, and the pick reaches to 0.60 anyway. And a
declutter that is not ok no longer stops the gate; the program run after it is
the judge. One more reflex inside that declutter, recovered. Gate 398 launched.

**16:40 — touch, for the next version.** Doug, watching the arm: the VLAs we are
contemplating take vision, text and position, not touch, and the touches look
important from the bench. Talked it through; agreed not to add senses now and to
write the plan down (TODO: derived contact channels and phase labels first, the
language channel second, a force encoder later; torques, gripper force and a
microphone as the sensors; the contact descent stays a reflex the policy calls).
Meanwhile gate 398 is the best of the day: calibration, then 0399, 0401, 0403 six
for six with no reflexes, policies 0400, 0402, 0404 zero each. Eight pairs on the
tally, 48 legs to 0.

**18:30 — gate 398 complete: ten pairs, 60 legs to 0.** The first gate to run its
full length. Programs 0399 through 0417, ten runs, six for six each: 60 legs. The
reflexes did not stop (0 to 3 a run, 16 across the ten) and did not cost a run,
the retries carried every one. Policies 0400 through 0418, ten runs, zero legs
each, every one the same: five to eight chunks of a slow lean toward the base,
refused by the guard at x 0.24, nothing on the table touched. Ten safety events,
all the guard doing its job. With gate 370's four pairs the day's tally is 14
pairs, 84 legs to 0; the rule wants 20, so one more gate (420, launched) makes the
verdict formal, and it will be FAIL, which is the measurement: 52 episodes and a
10% speed open-loop chunk do not learn the reach. Something else in the numbers:
the placement error is a per-run constant, 1–5 mm in three runs (0403, 0409,
0415) and 12–20 mm in the other seven, the same number six times within a run. A
biased grasp along the block is carried from pick to place and back, as the
adapter's docstring says, so the eye is better than the 12 mm suggests and the
grasp's centring is the next millimetre to find. The day's clean episodes (ten
six-leg runs, plus 0371–0377, 0389, 0391 earlier) more than double the dataset
the checkpoint was trained on; the next dream should run on all of them.

**19:00 — what the policy was trained on.** Starting the next dream on the day's
episodes, I read the converter before trusting it: it keeps every run under the
suite whose program reported ok, whatever the program was. The suite holds touch
calibrations, declutters, eye checks, reach checks and park runs beside the round
trips, and all of them ended ok, so the 52-episode dataset the checkpoint came from
was a mix: round trips and calibrations at least, each labelled "carry the blue
block between A and B". A calibration is the hand probing low around one block;
a policy that learned that beside the carries would lean toward a block and
hover, which is roughly what every policy run did. The converter now takes
`--program round_trip.py` and keeps only runs whose provenance names that file;
the dream passes it (hebbot scripts/franka_raw_to_lerobot.py, dream.sh). The
dream (20000 steps, batch 16) restarted on the Spark over every relayed run,
filtered. One cost on the way: stopping the first attempt with a pattern kill
matched "pretrained_model" against "lerobot.*train" and took the policy host down
with it; restarted, and the gate at 420 had already stopped on its own, four
reflexes in one calibration, two on one descent. Relaunched at 422.

**19:30 — the hang, caught in the act.** Gate 422's calibration hung after its
first reflex, twelve minutes in, and this time I got to it before the timeout.
Every thread was asleep: the main thread on a futex with no timeout, the Python
threads trading the interpreter lock at the switch interval. gdb on the main
thread: std::thread::join inside panda_py, called from the descent's
stop_controller. panda_py runs its automatic error recovery on the controller's
own thread and logs "Attempting automatic error recovery" through Python; that
log needs the interpreter lock, which the main thread holds while it joins; the
join waits for the recovery and the recovery waits for the lock. The race is
whether the recovery's log lands before or after the join starts, which is why
most reflexes recover and one in six hangs. The fix is a 1.5 s sleep on the
descent's abort path before the join, which releases the lock (ASPIRE, env.py);
the gate also parks and goes on if a calibration hangs. Relaunched at 424. The
dream on the Spark is converting round trips only, fifteen kept by 19:20.

**20:40 — gate 424: three pairs, then two reflexes on one hover.** Calibration
424 took a reflex on a descent and came through the new join path without
hanging. Programs 0425, 0427, 0429 six for six; policies 0426, 0428, 0430 zero.
Seventeen pairs on the day, 102 legs to 0. Then 0431 verified two legs and lost
its third hover to two reflexes in a row, the single retry spent. A batch is now
resent up to twice (ASPIRE, env.py). Gate 432 launched; three more pairs make the
twenty.

**16:20 (omen's clock) — 91 round trips.** The filtered conversion finished: 91
episodes, every one a round_trip.py run that reported ok, against the 52 of the
first dataset that were a mix. Training runs at 2 steps a second; 20,000 steps is
about 2 h 45 m, so the checkpoint lands this evening. The bench is idle, parked
at home, waiting on the controller reboot; the reflex rate in the last
calibration was 13% of motions with omen healthy by every number I can read.

**17:00 (omen's clock) — the controller recovered on its own, the reflexes did
not.** I had the bench idle an hour and a half waiting on a controller reboot;
Doug asked whether we were done with the arm, which was the right question, and
the gate went back on. Calibration 434: eight reflexes in 93 motions, 9%, the
second target lost to two on one probe. A descent is now retried up to twice like
a batch (ASPIRE, adapter.py). The rate over the day: 0 until 11:00, 2% at midday,
13% and 9% in the last two calibrations, with omen healthy and the controller
answering in 0.2 ms. Whatever it is, it lives in the robot's view of our packets,
and the retries are how the bench stays busy until it is found.

**17:20 (omen's clock) — manual recovery required.** Calibration 436 saw no
block in the band from any look (the previous gate's probing had shoved them),
and the declutter I ran to tidy could not make its first move: libfranka,
"Automatic Error Recovery command rejected: manual error recovery required".
The robot has gone into a state only Desk can clear. The gate now declutters
before it calibrates as well, so a shoved table does not cost a gate; the bench
waits on Doug for the recovery.

**18:00 (omen's clock) — controller rebooted; the wrist camera's hub fails.**
Doug repositioned the arm by hand and rebooted the controller. Parked, gate 438
launched with the declutter first: declutter ok, calibration ok, then the wrist
camera gave no frame on both runs of the first pair, before and after the
hardware reset. The D405 is not on the USB bus: the VIA Labs USB 3 hub it hangs
from at usb 2-4 re-enumerates and fails to configure (error -71), and the camera
behind it never appears. The hub or its cable, disturbed when the arm was moved by
hand. The gate is stopped; this is a hand's job.

**18:05 — and the reflexes are gone.** Calibration 438, the first after the
controller reboot: 81 motions, zero reflexes, where the two before it ran at 13%
and 9%. So the day's packet loss was the controller's, not omen's: it degraded
through the day from 11:00 (Desk refusing a login was the other symptom), and
nothing on omen's side moved the needle because nothing on omen's side was
wrong. Doug's instinct to reboot it was right and I argued it down on the
strength of a clean ping, which measures the wrong thing: ICMP answered from a
healthy network stack while the real-time controller behind it was late. The
retries stay, since a reflex is cheap to survive and the next degradation will
be gradual too; the mitigation list from today (EEE off, the harness pinned,
realtime priority, the sync blocking, the join pause) stays too, each one
harmless and one of them, the join pause, a real deadlock fixed. What should
change: a rising reflex rate with omen healthy is the controller's cue to
reboot, and the gate's per-run reflex count is the number to watch for it.

**18:50 (omen's clock) — gate 440: two pairs, then the joint stops.** With the
camera's hub reseated, gate 440 ran declutter, calibration (107 motions, no
reflex), 0441 six for six at 5–8 mm, policy 0442 zero, 0443 six for six, policy
0444 zero. Nineteen pairs on the day, 114 legs to 0. Then 0445 verified three legs
and lost its fourth approach: the IK had put the base and wrist 0.04 rad from
their stops, the solution nearest the seed, and the motion generator refused to
converge there three times with no reflex. The solver now penalises any solution
within 0.08 rad of a stop so that a clear one wins (ASPIRE, franka_kinematics.py).
Gate 446 launched for the twentieth pair.

**19:10 (omen's clock) — the stops.** The solver now prefers a solution at least
0.08 rad clear of every joint stop when one lies within 2 rad of the seed, and
takes the cramped one otherwise (ASPIRE a34deee, 3ab65ba): for 0445's approach
the only clear solution was the other base branch, 6.4 rad away, which the guard
would refuse as a swing. So 0445's failure is upstream, in the choice of grasp
yaw, whose margin check runs from the survey's seed while the approach runs from
the hover's; that goes in the TODO. Gate 446 running for the twentieth pair.

**19:40 (omen's clock) — twenty pairs: the first paired gate, formally.** Across
gates 370, 398, 424, 440 and 446, twenty program/policy pairs on the same table:

| | legs | safety events |
|---|---|---|
| program (round_trip.py) | 120 of 120 | 0 |
| policy (dream-20261117-1703, 10k steps) | 0 of 120 | 20 |

Every policy run the same: five to eight chunks of a slow lean toward the base
from home, the guard refusing the chunk that would cross the box's near face,
nothing on the table touched. Verdict by Tuesday's rule: FAIL. The measurement
behind it: 52 episodes, of which an unknown share were calibrations labelled as
carries, trained to 10,000 steps and run as 10-step open-loop chunks at a tenth
speed, do not learn the reach. The second checkpoint, 91 clean round trips at
20,000 steps, lands tonight and gets the same twenty pairs. The program side's
number is the one to keep: 120 for 120 across a day that included a camera
bracket swap, a controller going bad and recovering, a marooned block, a dropped
block, and eleven rules added to the skill.

**19:55 (omen's clock) — the hub again.** Gate 446 added two more pairs (0447,
0449 six for six; 0448, 0450 zero), then 0451's first approach was refused by the
harness's own guard: the wrist camera's last frame was 4.2 s old, the stream had
stalled on the USB side, and a stale frame would have been mistaken for the
scene. The right refusal. The hub the D405 hangs from is the weak part on the
bench now; it has dropped the camera twice since the arm was moved by hand. The
gate now treats a stalled stream reported in the run's result the way it treats
a dead camera (reset, one retry). Relaunched at 452.

**20:15 (omen's clock) — the second checkpoint.** dream-20261119-1447 finished:
91 round-trip episodes, 20,000 steps at batch 16, 2 h 46 m on the Spark, the
checkpoint at ~/runs/dream-20261119-1447/checkpoints/020000/pretrained_model.
Gate 452 had added two more pairs with the old policy (0453, 0455 six for six;
0454 zero) before I stopped it between runs. The policy host on the Spark now
serves the new checkpoint; gate 458 is its twenty pairs. What it has over the
first: nearly twice the episodes, none of them calibrations, twice the steps.
What it has not: anything closed-loop, any force in the decision, any narration.
If it reaches for a block at all, that is the day's second result.

**20:30 (omen's clock) — two gates at once, my doing.** The swap script ran in
the background under a ten-minute cap I had forgotten; I assumed it had been
cut short, restarted the host and launched gate 458 by hand, and the script
finished after all and launched its own. Two gates, two harness runs on one
robot, both runs without a result, the gate parking twice. Stopped everything
through a script (never a pattern kill from the command line again), parked,
and launched once: gate 460, the second checkpoint's twenty pairs, the host
confirmed serving 020000.

**21:00 (omen's clock) — the hub gives out.** Gate 460: declutter and calibration
fine, then every run of the first two pairs got no frame from the wrist camera,
before and after the hardware reset, and the gate stopped itself after two
hung runs in a row. The VIA Labs hub's USB 3 side has dropped off the bus
entirely (only its USB 2 half still enumerates) and the D405 behind it went with
it at 19:20; 103 disconnect or error events on that port since boot. The second
checkpoint has not had a single run yet. The bench is parked and waits for a hand
on the hub; the Spark serves checkpoint 020000 and is ready.

## 2026-11-20 (Friday) — the policy works offline; the runner was the fault

**Morning — the second checkpoint leans too, so I looked at it offline.** Doug
reseated the camera hub's cables and ordered a 3 m locking USB 3 cable to run the
D405 straight to omen. Gate 466 on dream-20261119-1447 (91 clean round trips,
20k steps): programs 0467–0473 six for six; policies 0468, 0470, 0472, 0474 the
same lean toward the base as yesterday's checkpoint. Twice the clean data and
twice the steps changed nothing, so the data mix was not the main fault.

Offline, on its own training episodes, the checkpoint tracks the recorded joints
within 0.10–0.15 rad and from home predicts the reach to the survey pose. A probe
in the policy server saved the bench's first observation of run 0474: state,
hand position and gripper width match the dataset's frame 0 to the millimetre;
the wrist image is close; the scene camera's colours differ (dataset frames warm,
blue mean 0.30 against 0.52 live). Swapping inputs between the two showed the
images do not matter here: fed the bench's own first observation, the checkpoint
predicts the right move, shoulder forward, the survey pose 2.5 s out. The first
ten steps were executed faithfully on the bench. The fault was the runner
executing ten of a hundred steps and asking again: the model's first step carries
a 0.1–0.2 rad error in the base joint even on its training frames, the runner
re-planned from that stationary, slightly turned state every half second, which
the data never visited (in training the arm is always moving, the command leading
the joints), and the error compounded into the lean. Classic behaviour-cloning
drift, made worse by re-planning far more often than the model was trained for.

The checkpoint was trained with n_action_steps 100. The runner now executes the
whole chunk (ASPIRE d273ff7), and the adapter runs a chunk in segments split at
each gripper change, so a chunk that descends, closes and lifts closes at the
bottom (3de3b43, a new test, 45 passed). The next policy run is the first with
both.
