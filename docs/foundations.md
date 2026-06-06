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
- **Publish along the way.** Substack/blog series, every 2–3 weeks, retrospective-with-framing tone (Karpathy / Chollet / Eric Jang style). See [the publishing memory](../.claude/projects/-home-doug-hebbot/memory/hebbot-publishing.md) for venue and cadence details.
- **Write a paper** if something genuinely novel surfaces.
- **Enter a robotics competition** (LeRobot benchmarks, RoboCup@Home variants, real-world tracks) when the substrate is mature enough.
- **Meet interesting people** via the writing. Distribution is how this happens.
- **Eventually build a genuinely useful applied robotic system.** Concrete vision deliberately under-specified — the design constraints are "unstructured / messy / real" environments, not benchmark soup.

Strategy decisions should be evaluated on *which path compounds across multiple goals*, not which optimizes one metric.

---

## Provenance

These foundations crystallized through a series of conversations between Doug and Claude Code during the 2026-05-25 → 2026-06-05 reset period. The conversation transcripts are preserved in [`transcripts/`](../transcripts/) — they're the record of how the framing emerged from the work, including dead ends, reversals, and the moments where Doug pushed back and reshaped the thesis.

The earlier `panda-mcp` project's lab notebook (findings #1–98) is the empirical grounding. The architectural commitments here are what *survived contact with reality* in that project.
