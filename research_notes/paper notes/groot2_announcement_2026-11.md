# Reading note: GR00T 2 — NVIDIA's open "System One" world-action model

NVIDIA GEAR lab (Jim Fan et al.), announced 9–10 Nov 2026 at CoRL (Austin), paired with
Isaac Lab 3.0. Read date 2026-11-12. **Announcement-level note**: sourced from the
LinkedIn announcement (supplied by Doug), press coverage, and search summaries of the
whitepaper at research.nvidia.com/labs/gear/gr00t-2/ — the whitepaper itself is located
but **[not yet read]**. Numbers below are NVIDIA's own; no independent replication exists
yet and the 50 evaluation tasks are unnamed (a gap the trade press has already flagged).

## What they claim

A **2B-parameter world-action model** — one transformer, built on Cosmos, that jointly
predicts future observations *and* actions. Positioned explicitly as "System One": fast,
onboard, reflexive, with "Unified Cognition" hooks for frontier LLM agents to sit above
it doing physical reasoning, error recovery, and safety monitoring. Runs untethered on
robot-local compute (Turbo variant: 124 ms per action chunk on a Jetson AGX Thor aboard
a Unitree G1; full model: 153 ms on a single RTX 5090).

Headline numbers: **71% average success over 50 tasks from one demonstration per task,
95% from five**; 40/50 tasks completed with no task-specific demonstrations; long-horizon
chains of 20+ steps / 5+ minutes (chemistry-lab and model-airplane assembly demos).
Trained on 100K hours (~11 robot-years) of egocentric human video on 1,024 GB300s.
**X-Adapt**: cross-embodiment adaptation from human wearables and simulation, deployed
zero-shot to varied robots — recipes promised December. Each task adaptation is a
**30-minute LoRA fine-tune on one RTX 5090**.

Open-source claims: weights, agentic framework, simulation engine, annotation engine,
hardware designs, and the 100K-hour dataset "available today"; X-Adapt recipes December.
**License unverified** — press notes no license text or weights date was visible at
announcement, and the GR00T N1.6 precedent is a non-commercial license. Check before
building anything on it.

## Relevance to Hebbot

1. **This is the strongest candidate yet for the layered thesis's System-One slot.** 2B
   parameters is ~4–5 GB at bf16 — inference fits omen's 16 GB with room to spare, the
   first frontier-lab VLA where that is comfortably true (N1.7 wanted 40–80 GB to
   fine-tune). LoRA adaptation "in 30 minutes on a 5090" (32 GB) is unverified on 16 GB
   but plausible at reduced batch; worth one evening's experiment when weights land.
2. **The few-demo numbers land exactly on our question.** 1→71%, 5→95% is adaptation at
   the "tens of successes" data scale the distillation plan assumed. The campaign loop we
   just ran is precisely the machinery that mints such demonstrations: 22 verified stack
   episodes with full probe records, collected unattended. If the license allows, the
   obvious experiment is LoRA-adapting GR00T 2 to touch_stack's task from our own
   episodes and scoring it with our own verify-by-probe.
3. **NVIDIA has institutionalised the architecture Hebbot bet on.** "Unified Cognition" —
   frontier agent above, fast local policy below, with error recovery and safety at the
   boundary — is the LLM-orchestrator-over-reflexes thesis, now shipped by the largest
   player. The hobby-scale version differs in one load-bearing way: our System Two
   verifies through touch, not through the policy's own self-report.
4. **World-action modelling rhymes with the scene-memory direction.** A model that
   predicts the observations an action should produce is the learned form of this week's
   design conversation — actions as transformations over descriptions, with expected
   changes checked against what the sensors then say. Their version is pixels; ours is
   beliefs with error bars. The convergence is encouraging; the mechanisms stay distinct.
5. **Vision-only, again.** Nothing in the announcement mentions force, torque, or
   contact sensing. This week's bench story is a counterexample walking: a vision policy
   commanding a yaw-90 grasp through our finger-line offset would report success and be
   wrong, silently. Touch verification remains the niche no foundation model is
   contesting, and wrapping *any* future learned layer in verify-by-probe remains the
   promotion-gate plan.
6. **Benchmark hygiene.** 95%-on-50-unnamed-tasks is exactly the shape of claim our own
   protocol was designed against: we publish the task, the ledger, every probe, and a
   Wilson interval (22/30 → [56%, 86%]). Keep that discipline when comparing anything
   to these numbers.
7. **Artefacts worth a later look regardless of the model**: the 100K-hour egocentric
   dataset (annotation conventions), the annotation engine, and Isaac Lab 3.0 versus
   MuJoCo Playground for the learned-policy track (the DLO paper's MP-on-16GB result is
   still our baseline plan there).

## Derived / caveats

- All success rates, model sizes, and timings are NVIDIA's self-reported numbers via
  press and whitepaper summaries, **[not yet read]** in the primary.
- "Fits 16 GB" for inference is my arithmetic from the 2B figure; the 16 GB LoRA
  question is untested speculation.
- License status is the gating fact for every Hebbot-facing item above; verify at the
  weights repo before any work is scheduled.
- Sources: [GR00T 2 research page](https://research.nvidia.com/labs/gear/gr00t-2/),
  [whitepaper PDF](https://research.nvidia.com/labs/gear/gr00t-2/assets/gr00t2_whitepaper.pdf),
  [The Robot Report](https://www.therobotreport.com/nvidia-launches-gr00t-2-isaac-lab-3-0-corl/),
  [TechTimes on the unnamed tasks](https://www.techtimes.com/articles/330365/20261110/nvidia-gr00t-2-robot-model-reports-95-success-while-leaving-50-test-tasks-unnamed.htm),
  [The Next Web](https://thenextweb.com/news/nvidia-gr00t-2-robots-human-video-corl).
