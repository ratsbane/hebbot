# Hebbot Foundations

*The intellectual and architectural commitments of the Hebbot project. The canonical reference for "what Hebbot is and what shape it has." Started 2026-06-05; will evolve as the substrate takes shape.*

---

## Thesis

**Use agentic AI to bootstrap a system with continuous learning.**

Specifically: build a substrate that lets a foundation model direct an embodied system the way [Claude Code](https://www.anthropic.com/claude-code) lets one direct a codebase, with the deliberate cognition of the LLM and the reflexive cognition of learned motor skills coexisting and continuously trading work between layers via bidirectional feedback.

The piano analogy is the technical specification disguised as metaphor: when you first learn a passage you're deliberate — find the note, press the key, hear it. After enough practice, whole sections compress into reflexive motor execution; you think about the phrase, your fingers handle the keys. But the deliberate layer never goes away — it watches, ready to take over the moment something surprises (sticky key, wrong tuning, deciding to rubato a measure). Hebbot is the architecture that lets a foundation model live in that same arc with embodied hardware.

This compression — *deliberate cognition becoming reflexive, with bidirectional feedback between the layers* — is what most modern AI robotics is missing. End-to-end VLA models collapse the two into one network. World models simulate the environment but don't reason about it. SayCan-era LLM-as-planner work pointed at the right shape but predated modern LLM tooling. Hebbot is what fills that shape, with much better infrastructure than those predecessors had.

---

## Structural commitments

### 1. Two cognitive layers, with bidirectional feedback

- **Deliberate layer (cortical-style)** — slow, attention-heavy, reasoning-capable. A foundation model. Handles planning, novel situations, error recovery, deciding *which* skill to invoke. Works at human-conversation timescales (~seconds).
- **Reflexive layer (subcortical-style)** — fast, low-latency, automatic. Learned policies, real-time servoing, recovery reflexes. Works at motor-control timescales (~milliseconds).
- **Bidirectional feedback is the substrate's load-bearing feature.** Reflexive layer reports back when execution surprises it ("descent felt off, force readings spiked"). Deliberate layer adjusts (re-plan, re-parameterize, demote back to deliberate execution, escalate to user). Neither layer is sufficient alone.

**Verification has three levels.** "Did the skill work?" is answered at increasing fidelity and cost, and the substrate needs all three:

1. **Mechanical** — did the body run without raising? Free, but blind to everything real.
2. **Proprioceptive** — does the result match the skill's declared *expectations*, judged from the robot's own sensors (joint error, force, gripper width)? Cheap, automatic, runs on every call. This is where *surprise detection* lives: a result outside the predicted bounds is a surprise the deliberate layer must address. **Blind to the world** — `close_gripper` reporting `grasped: true` only means the fingers settled at the expected width holding force; it cannot tell the cup from a same-width pen, and "place A on B" has essentially no proprioceptive signal at all.
3. **Perceptual** — does the scene, *seen by a camera and judged against the goal*, confirm the action did what was intended? "Is the gripper holding the right object?" "Did the block actually land on the stack, or topple off?" Expensive, so it runs at task checkpoints, not every call. This level necessarily belongs to the **deliberate layer**: it needs the goal (which lives in the orchestrator's intent, not the primitive), needs perception + semantic judgment, and is best evaluated through relational *geometry* — re-detect the objects' poses and check the relative transform (commitment #11) — rather than asking a VLM "is A on B?" directly.

The two expectation levels are complementary, not competing: proprioceptive is fast and a *weak* label; perceptual is slow and a *strong*, ground-truth label (the one that makes episodes worth distilling, #9). Critically, **a failed perceptual check is the same surprise on the same feedback channel** as a proprioceptive one — both set the outcome to `surprise` and hand control back to the deliberate layer. Implementation status: the proprioceptive level exists (skills declare `expectations`; the dispatcher flags surprises). The perceptual level is a `vlm_grounded`-body or `verify(predicate)` skill, gated on a VLM grounding primitive + the relational-state pipeline of commitment #11.

### 2. Hebbian compression as the learning principle

Skills become reflexive through repeated deliberate execution. The system notices recurring patterns ("the LLM has invoked these waypoint sequences in this context 30 times"), collects the executions as training data, and trains a small reflexive policy that handles the common case without LLM-level reasoning. The compression is *Hebbian* in the precise sense — repeated co-activation strengthens the pathway.

The deliberate layer retains the override. It can always demote a reflexive skill back to deliberate execution if confidence drops.

### 3. AI as both builder and user (Goal 2)

The same foundation model that *uses* the substrate at runtime also *builds and refines* the substrate over time. Most robotics research has humans design the cognitive architecture and then evaluate AI systems running inside it. Hebbot flips this: the AI is the active architect, the human (Doug) provides hardware, judgment, direction, and the willingness to redirect. The development trajectory becomes part of the artifact.

This is the *only* path to a substrate that actually fits how an LLM thinks. Humans don't really know what abstractions an LLM will find natural until the LLM has tried to use them and failed.

### 4. Substrate ≠ orchestrator

These are different concerns:

- **The substrate** is the contribution: skills, observation primitives, memory, feedback bus, skill-compression machinery. Owned by us, open-source, runs anywhere.
- **The orchestrator** is the runtime agent loop. Today: [Claude Code](https://www.anthropic.com/claude-code). Tomorrow: possibly something else (custom, LangGraph, whatever fits). The orchestrator can change; the substrate doesn't.

The substrate is what Hebbot ships. The orchestrator is replaceable infrastructure.

### 5. Library + thin MCP wrapper

The substrate is **plain Python**:

- Skills are functions.
- Perception primitives are functions.
- Memory is files + a small access library.
- The bidirectional-feedback bus is internal data structures.

These are importable, type-checkable at write time, debuggable in one process, testable with pytest. They don't require an agent to be useful.

[MCP](https://modelcontextprotocol.io) is **the agent-facing boundary**. One MCP server, exposing the substrate's public skills as MCP tools. Agents (Claude Code, others) speak to that server. They never speak directly to the implementation.

```
[Orchestrator: Claude Code today]
       |
       v   <-- MCP (agent-facing API, one server)
[Hebbot MCP server (thin wrapper)]
       |
       v   <-- direct Python calls
[Hebbot substrate library]
   - skills
   - perception primitives
   - memory + feedback bus
   - skill-compression machinery
       |
       v   <-- ZMQ (real-time / process isolation)
[Hardware processes]
   - franka_rt (real-time priority)
   - camera_daemon (GPU memory ownership)
   - others as needed
```

ZMQ (or similar fast IPC) handles the substrate-to-hardware boundary. Real-time control, GPU memory ownership, and independent restartability each justify a separate process — but those processes don't need to speak MCP. MCP is only the *agent-facing* protocol.

### 6. The litmus test

> *Could a stranger pick up the Hebbot library, write a 200-line custom agent loop, and have a working robot?*

If yes, the substrate is designed right.

If no, orchestrator-specific assumptions have leaked into the substrate and we've actually built a Claude-Code-plugin instead of a substrate.

This is a hard test we should be able to satisfy at any point in the project's life. It rules out shortcuts that bind the substrate to one agent runtime.

### 7. Hardware portability

The same skills should work on a **Franka Panda** and a **$250 [SO-100](https://github.com/huggingface/lerobot)**. If they don't, the substrate has leaked actuator-specific assumptions into the public API. Portability is one of how the substrate proves it's at the right level of abstraction.

### 8. World models and VLAs are components, not paradigms

The substrate hosts learned components. **VLAs** (Vision-Language-Action models) are *policies* — they answer "what action should I take?" **World models** are *forward predictors* — they answer "what state comes next?" These are different and complementary. Either or both can live inside the substrate as a kind of skill, prediction tool, or feedback mechanism. Neither *is* the substrate, and neither is what Hebbot is trying to be.

When the deliberate layer queries "what would happen if I tried this approach?", that's a world-model call. When it invokes "pick up the cup," the underlying skill might be a learned VLA, a classical IK + servo, or a hybrid. The substrate accommodates all of them.

### 9. Unified memory: every episode contributes, two ways

Every skill execution writes a structured episode to a single memory store. That same data feeds two consumers:

- **Retrieval-augmented context (immediate, deliberate-layer).** When the LLM is reasoning about the next action, it can pull the *k* most relevant past episodes — by visual similarity to the current scene, by skill name + outcome, by free-text semantic match — and read them as context. Modern multimodal LLMs read the retrieved images directly; classical metadata filters tighten the search. A single past example can fire on a similar future query. This is RAG applied to embodied experience.
- **Distillation corpus (periodic, reflexive-layer).** Episodes accumulate over time. When recurring patterns emerge — the same skill called with similar parameters in similar scenes, succeeding consistently — a distillation pass trains a small reflexive policy from those rollouts. This is the Hebbian compression of #2 made concrete: deliberate execution becomes reflexive when the data supports it.

One write path, two read paths. The substrate doesn't need to choose between "RAG vs. VLA training" — those are different uses of the same underlying store.

**Episode schema (sketch):**

```python
episode = {
    "id": uuid,
    "timestamp": ...,
    "skill": "pick",
    "params": {"x": 0.45, "y": 0.0},
    "frames": [{"camera": "d435", "image": <path>, "t": 0.0}, ...],
    "states": [{"joints": [...], "ee_pose": [...], "force": [...], "t": 0.0}, ...],
    "deliberate_context": {"goal": "pick the red block", "reasoning": "...", "retrieved_ids": [...]},
    "outcome": {"success": True, "duration_s": 4.3, "notes": "..."},
}
```

**Implementation components:**
- **Image embeddings** for similarity search: [DINOv2](https://arxiv.org/abs/2304.07193) (scene similarity) or [SigLIP 2](https://arxiv.org/abs/2502.14786) (image-text aligned).
- **Vector store:** [LanceDB](https://lancedb.github.io) or [ChromaDB](https://www.trychroma.com) — local, serverless, fast enough.
- **Structured store:** SQLite or DuckDB for metadata, queryable joins, filters.
- **Frames + states:** raw filesystem (JPEG + numpy arrays in HDF5 or Parquet).

**Substrate API the deliberate layer sees:**
```
memory.recall(image=current_frame, k=5)
memory.recall(skill="pick", outcome="failure", k=5)
memory.recall(query="things that went wrong when the cup tipped", image=current_frame, k=5)
```

**Distillation runs periodically** (nightly, or triggered by N new episodes). Identifies skills with enough examples to support training a reflexive policy. The trained policy goes into the skill library; future invocations may use it instead of (or in addition to) the deliberate path. The deliberate layer retains override.

This is the memory architecture that makes "every robot movement contributes to the experience" load-bearing rather than aspirational.

### 10. Skills are records, not code

A skill is a row in the substrate's skill store, not a Python function in the source tree. Each record contains:

- **Name and parameter signature** — what the orchestrator sees when listing tools
- **Expectation model** — the forward-prediction bounds that drive surprise detection (commitment #1)
- **Execution body** — a typed field with one of several forms (see below)
- **Episode history** — the memory link (commitment #9)
- **Statistics** — success rate, surprise frequency, average duration

The execution body field has multiple legal types, each with a generic substrate runtime:

| Body type | What it is | When it's used |
|---|---|---|
| `classical` | Reference to a hand-written primitive + arg map | Bootstrap primitives only — `move_to`, `open_gripper`, `close_gripper`, `read_camera`, `get_state`. The only thing that ships as code. |
| `script` | Source code string, sandbox-evaluated | LLM-authored at runtime — the first version of a new skill the orchestrator composes |
| `composition` | Ordered list of subskill calls + arg-passing rules | Skills built by composing existing skills (`pick_object` = locate + approach + grasp + verify) |
| `trajectory` | Recorded waypoints + adaptation parameters | Skills learned from one or a handful of demonstrations, replayed with correspondence-driven warping (Tether-style) |
| `learned_policy` | Path to a policy weights file + invocation config | After enough data, distilled into a small task-specific neural net (the "reflex") |
| `vlm_grounded` | A VLM prompt template + post-processing | Skills whose action depends heavily on language/perception |

**The Hebbian compression arc lives in this field.** A skill's body progresses from `script` → `trajectory` → `learned_policy` as data accumulates. The skill's *contract* (name + parameters + expectations) is stable; the implementation underneath changes. The orchestrator never sees this — it calls `pick(x, y)` on day 1 and day 180 the same way.

**Primitives are the only hand-written exception.** A small bootstrap set lives in the codebase as `classical` body-type skills. Everything else in the store is data created at runtime.

**Composition is first-class.** A `composition` body is just a sequence of `(skill_name, arg_map)` invocations plus optional parent-level phases. The substrate executes them in order, recording nested episodes. Parent skills can declare expectations that span multiple child calls.

**Predicates over names.** Skills take parameter predicates rather than hardcoded variants. One `pick_object(predicate)` accepts diverse forms (text, bbox, pose) — variants live in the predicates, resolved by perception primitives and lower-level skills. We do *not* end up with `pick_red_block`, `pick_blue_block`, etc.

**Skill discovery is dynamic.** The substrate's single MCP server publishes the skill store's current contents as MCP tools — generated from the database, not from a hand-curated list. New skills are visible to the orchestrator the moment they're added to the store. This is the corrected version of commitment #5: the MCP server is a thin window onto the skill store.

### 11. Relational state, not absolute state

When the substrate retrieves "have I seen something like this before?" — for both RAG context (#9) and skill replay/distillation — the embedding key must be **the relational geometry between the gripper and the target object(s)**, not the raw camera frame.

Raw scene pixels are a deceptive key. Two scenes can look 0.92 similar by DINOv2 distance while differing in exactly the way that matters for the action (a cup 5 cm closer looks nearly identical but requires a different motion). Lighting, background, and irrelevant clutter dominate visual embedding distance and drown out the signal we need.

**The right relational state vector captures:**

- Detected object 6-DoF pose (in robot frame)
- Current gripper pose (in robot frame)
- The **relative transform** between them — the load-bearing piece
- Gripper width, object dimensions, contact state
- Optionally: object class / semantic embedding for filtered retrieval

Producing this state vector requires a perception pipeline running continuously alongside any skill that touches objects:

```
camera frame → detection (Moondream / Florence-2 / VLM grounding)
            → segmentation (SAM 2 / Grounding-SAM)
            → 6-DoF pose (FoundationPose-style, or depth + ICP)
            → relational state vector
```

The detection + segmentation + pose-estimation steps were each tried in `panda-mcp` and were either underpowered, under-resourced, or used pre-foundation-model tooling. **The OMEN's RTX 5060 Ti + 16 GB VRAM + CUDA 13 changes what's feasible.** SAM 2 runs live, FoundationPose-style 6-DoF pose models are tractable, and the VLM grounders Doug already uses (Moondream, Qwen-VL) have themselves gotten significantly better since 2026-03. This is a deliberate revisit, not flailing — the compute landscape has shifted enough to make them work.

**Decision logging is part of this.** Every substrate decision (replay vs. orchestrator-led, which past episode to retrieve, which body type to invoke) gets logged with the relational state vector that drove it, the top-*k* retrieval candidates with similarity scores, the decision reasoning, and the eventual outcome. This is the dataset every threshold-tuning attempt later draws on. Without it, all threshold tuning is guessing.

```python
DecisionLog = {
    "ts": ...,
    "skill_called": "pick_object",
    "params": {"predicate": "red block"},
    "relational_state": {"object_pose": ..., "gripper_pose": ..., "relative": ..., "object_dims": ...},
    "candidates": [
        {"episode_id": ..., "relational_similarity": 0.91, "outcome": "success"},
        ...  # top-k retrieved
    ],
    "decision": "replay_trajectory" | "ask_orchestrator" | "execute_policy",
    "decision_reasoning": "best candidate similarity 0.91 > threshold 0.85",
    "outcome": "success" | "fail" | "surprise",
    "surprise_events": [...],
}
```

---

## Anti-goals (what Hebbot explicitly rules out)

- **Competing with Pi / Physical Intelligence / OpenVLA / Tesla on raw end-to-end VLA performance benchmarks.** Losing game on compute and data, and architecturally wrong for the thesis (those approaches collapse high/low into one network — Hebbot needs them separate).
- **Making the LLM a low-level controller.** This was the panda-mcp local-minimum self-diagnosis ("we've been polishing Claude as a low-level controller instead of building the neural motor layer that replaces it"). The resolution is not to ditch reflexive learning, but to build the proper high↔low interface.
- **Building a general-purpose agent runtime.** Claude Code (and others) exist; rebuilding them is not the work. Hebbot is the substrate, not the orchestrator.
- **Compute-extravagant or non-replicable approaches.** A hobbyist with $5K and patience should be able to replicate the substrate on equivalent hardware.
- **Hardware that can't be ported to cheaper substrates.** See #7 above.
- **Orchestrator-specific assumptions in the substrate.** See the litmus test.

---

## Opportunistic goal stack

Hebbot is developed opportunistically — *learn / iterate / experiment / see what emerges* — with optional formal payoffs along the way:

- **Learn / iterate.** Always-on baseline goal.
- **Publish along the way.** Substack/blog series, every 2–3 weeks, retrospective-with-framing tone (Karpathy / Chollet / Eric Jang style).
- **Write a paper** if something genuinely novel surfaces.
- **Enter a robotics competition** (LeRobot benchmarks, RoboCup@Home variants, real-world tracks) when the substrate is mature enough.
- **Meet interesting people** via the writing. Distribution is how this happens.
- **Eventually build a genuinely useful applied robotic system.** Concrete vision deliberately under-specified — the design constraints are "unstructured / messy / real" environments, not benchmark soup.

Strategy decisions should be evaluated on *which path compounds across multiple goals*, not which optimizes one metric.

---

## Appendix A: Storage and runtime architecture

Not a structural commitment in its own right — the concrete shape that flows from the commitments above. Captured here so we don't drift between sessions. **Expect this to change as we build.**

### Storage layout

Lakehouse pattern: small structured data in SQLite, raw blobs on filesystem, vectors in a vector DB.

```
~/hebbot-data/
├── hebbot.db                  SQLite — skill store, episode store, decisions
├── vectors/                   LanceDB or ChromaDB
├── frames/
│   └── YYYY/MM/DD/<episode_id>/<camera>_NNN.jpg
├── trajectories/              parquet per recorded trajectory body
│   └── <skill>/<traj_id>.parquet
└── policies/                  trained reflexive policies
    └── <skill>/v<N>/{model.pt, metadata.json}
```

### SQLite schema (starter)

Five tables. JSON columns wherever the shape will likely change.

- **`skills`** — `(name, params_schema, expectations, body_type, body, version, created_at, updated_at, statistics)`
- **`episodes`** — `(id, skill_name, parent_id, started_at, ended_at, success, params, deliberate_ctx, outcome, body_type_used)`
- **`frames`** — `(id, episode_id, camera, t, file_path, width, height)`
- **`states`** — `(episode_id, t, joints, ee_pose, force, gripper_width)`
- **`decisions`** — `(id, episode_id, timestamp, skill_called, params, relational_state, candidates, decision, reasoning, threshold_values, outcome)`

See `src/hebbot/substrate/schema.sql` for the canonical version.

### Runtime topology

Three processes, three protocols:

```
[ Orchestrator ]       <-- Claude Code / etc.
       |
       | MCP (stdio or HTTP)
       v
[ Hebbot MCP server ]  thin wrapper, exposes skill store as dynamic MCP tools
       |
       | Python imports
       v
[ Hebbot substrate library ]
       |
       +-- ZMQ pub/sub --> [ perception daemon ] --> [ FastAPI viewer @ :8081 ]
       |
       +-- ZMQ req/rep --> [ franka_rt (real-time) ]
       |
       +-- ZMQ pub/sub --> [ camera daemons ] (one per physical camera)
```

### Perception daemon

Separate process that:
1. Subscribes to camera streams (ZMQ from camera daemons)
2. Runs detector → segmenter → 6-DoF pose at ~10 Hz per camera
3. Publishes results on ZMQ topics (`perception/<camera>/result`)
4. Hosts FastAPI viewer on `http://omen:8081/`
   - `/live/<camera>` — Server-Sent Events stream of annotated JPEGs
   - `/state` — current relational state JSON
   - `/decisions` — tail of decision log
   - `/` — dashboard, multi-camera grid

Annotated viewer overlays: detection boxes + class + confidence, SAM 2 masks (translucent), 6-DoF pose axes, relational state text, current decision-log tail in sidebar. Toggleable per-overlay from dashboard.

`ssh -L 8081:omen:8081 omen` and open `http://localhost:8081/` from any laptop for headless debugging.

### MCP server

Single process. Reads `skills` table from `hebbot.db` at startup (and on file-watch change). Exposes one MCP tool per skill row, with the row's `params_schema` as the tool schema. Tool invocation delegates to the substrate library's `call(skill_name, **params)`.

Skill additions appear as new MCP tools without restart. Skill body type changes (script → trajectory → learned_policy) are transparent — the orchestrator sees the same MCP tool.

### Distillation runtime

Cron at 3 AM (or systemd timer). Pseudocode:

```python
for skill in skills_with_n_or_more_new_successful_episodes_since_last_distillation():
    candidate_body_type = pick_next_body_type(skill)  # script → trajectory → learned_policy
    new_body = train_body(skill, candidate_body_type, episodes)
    if validate(new_body, holdout_episodes):
        update_skill_body(skill, new_body)
```

Promotion is a database UPDATE; next call picks up the new body. No substrate code changes; no MCP tool changes.

---

## Provenance

These foundations crystallized through a series of conversations between Doug and Claude Code during the 2026-05-25 → 2026-06-05 reset period. The conversation transcripts are preserved in [`transcripts/`](../transcripts/) — they're the record of how the framing emerged from the work, including dead ends, reversals, and the moments where Doug pushed back and reshaped the thesis.

The earlier `panda-mcp` project's lab notebook (findings #1–98) is the empirical grounding. The architectural commitments here are what *survived contact with reality* in that project.
