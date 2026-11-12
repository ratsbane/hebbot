# Agentic LLM-on-robot systems: agents that direct physical robots through tools, skills, or generated code

Notes as of 2026-11-09. Unless marked otherwise, numbers come from the arXiv full text, read through a summarizing fetch tool or `pdftotext`. Items marked "(search excerpt only)" were not opened. No paper here was independently replicated unless stated. "Code: not stated" means the text I read gave no repository, not that none exists.

## 1. Which systems let an agent write, store, and reuse skills on real robots? What abstractions work best?

### Takeaway
Since July 2026 the pattern has converged: a coding agent works over a small set of perception and control primitives, reads per-primitive execution traces, and promotes a skill only after evidence-backed validation. Reuse has real-robot evidence (ASPIRE, ARCHITECT, FCGraft), but the trial counts are small, and real-robot success in RAX is judged by the agent itself.

### Cited Findings

**RAX, "Scaling Online Exploration for Robot Agents"** (arXiv 2610.22496, 15 Oct 2026, preprint; Harvard, NVIDIA, UC Berkeley, MIT, Stanford; hardware Franka FR3 and Rainbow RB-Y1; the abstract says open-sourced, and a skills repo exists).
- A frozen LLM in a general coding agent (parallel experiments use Claude Code and Codex) reaches the robot only through a tool interface: observation, raw control, FK/IK, motion planning, a robot-only preview, and depth back-projection. SDK calls are exposed as MCP tools, and the backend refuses commands that violate limits — [RAX](https://arxiv.org/html/2610.22496).
- The agent first controls the robot by tool calls, then compiles what worked into standalone Python that runs without the agent. Programs `raise Unsure()` to hand control back when perception or planning fails — [RAX](https://arxiv.org/html/2610.22496).
- A skill enters the repository only after an unsupervised successful run with execution evidence. Each robot has a versioned repository, sessions work on branches, and merges require validated structure, entry point, and unattended-success evidence — [RAX](https://arxiv.org/html/2610.22496).
- The published schema is `skills/<slug>/SKILL.md` (when to use, preconditions, procedure, code, checks, failure modes, evidence), `code/run.py` with `main(ctx, ...)` returning `{ok, measured, why}`, and `evidence/runs.jsonl` (outcome, unattended flag, conditions, checks), plus images. Status is `draft`, `verified` (at least 2 unattended successes of the committed code), or `deprecated`. The repo holds 11 skills: 6 RB-Y1, 5 Franka — [skills-lib repo](https://github.com/robot-agent-exploration/skills-lib).
- Simulation: RoboDojo 78.3% vs 24.9% for VPP2 (7 tasks); BEHAVIOR-1K 46.5% vs 8.2%; HumanoidArena 89.8% vs 81.9% for WB-WAM (3 vs 100 demos per task); DexJoCo 99.5% (best session); LIBERO 98.5%; LIBERO-Pro perturbed 76.0%, or 84.4% with up to 5 adaptation episodes. Official evaluators score the simulation programs on unseen scenes — [RAX](https://arxiv.org/html/2610.22496).
- Real robot (partial read; I did not reach per-task totals for all tasks or the RB-Y1 results). Find the Cube: 12 trials in about 2.5 h, with trials 4–6 three consecutive end-to-end successes without intervention (166, 231, 194 s). Air Fryer: run 1 by direct tool calls took about 75 min including exploration, the program then ran runs 2–10, 4 of those were clean unattended successes (runs 6, 7, 9, 10), one was a safety stop after the elbow hit a camera, and three needed resumes. Wiping: 13 recorded trials including resumes, with two one-pass clean runs without intervention — [RAX](https://arxiv.org/html/2610.22496).
- Real-robot success was self-assessed by the agent from its own measurements, while a human reset scenes and armed motion — [RAX](https://arxiv.org/html/2610.22496).
- Secondary coverage quotes "+37.4 over SOTA across five benchmarks"; that is an average across benchmarks, all in simulation — [RAX](https://arxiv.org/html/2610.22496).

**ASPIRE, "Agentic /Skills Discovery for Robotics"** (arXiv 2607.00272, v2 stamped 20 Oct 2026, preprint; NVIDIA, CMU, UC Berkeley, UMich, UIUC; project site, code release not stated).
- The robot execution engine records per-primitive traces (inputs, outputs, return status, RGB keyframes, overlays). A skill is compact in-context guidance: failure signature, when-to-apply condition, repair strategy, optional code sketch. A fresh verifier actor must reproduce source-task success before promotion — [ASPIRE](https://arxiv.org/html/2607.00272).
- Simulation (Claude Code, Opus 4.6): LIBERO-Pro 72% vs 18% for CaP-Agent0, and 0% for OpenVLA and π0. With Opus 5.5, 99% average across 80 LIBERO-Pro tasks. Zero-shot on LIBERO-Pro Long using a library from LIBERO-90: 30.5% vs 3.8% — [ASPIRE](https://arxiv.org/html/2607.00272).
- Real-robot transfer (bimanual YAM, Codex GPT-5.5 xhigh, skills from Franka simulation, 20 evaluation trials per task), without vs with skill:

  | Task | Success | Total tokens to first success |
  |---|---|---|
  | Put bowl on plate | 20/20 vs 20/20 | 8.65M vs 5.11M |
  | Lift soda can | 13/20 vs 19/20 | 61.94M vs 6.58M |
  | Open/push drawer | 0/20 vs 11/20 | 334.92M (budget exhausted) vs 81.67M |

  This is the cleanest real-robot evidence I found that a skill library reduces search cost — [ASPIRE](https://arxiv.org/html/2607.00272).
- Online Rubik's Cube (YAM, GPT-6 Astra in Codex, 3 episodes, n too small to generalize): time per face turn fell from 16 min 58 s to 3 min 05 s, and tokens per turn from 11.27M to 2.01M — [ASPIRE](https://arxiv.org/html/2607.00272).
- Stated limits: skills are verified once at admission and never re-validated, so they can go stale; behavior is bounded by the exposed primitives; a human still guides, scrambles, and resets — [ASPIRE](https://arxiv.org/html/2607.00272).

**ARCHITECT** (arXiv 2607.23784, 26 Jul 2026, preprint; University of Washington, Microsoft Research, MIT; Franka Panda DROID setup, Claude Opus 4.6 orchestrator, cuRobo; project site, code release not stated).
- Tools: `move_ee_to_pose`, `move_ee_guarded`, `set_gripper_width`, `execute_waypoints`, `detect_objects`, `get_placement_pose`, `get_vqa_response`, `get_keypoints`, `verify_grasp`, `get_object_mask`, proprioception getters, plus `write_primitive(name, code, docstring)` and `submit_program(code)`. A skill is "a concise, natural language rule or code pattern" with When to use / How / What to watch for sections — [ARCHITECT](https://arxiv.org/html/2607.23784).
- 8 tasks, N=10 each, 400 real rollouts across 5 methods. Success with an expert correcting in natural language (mean of per-task values, computed for these notes; the paper reports no mean): ARCHITECT 0.81, π0.5 0.36, ARCHITECT with VLM corrections only 0.38, π0 0.28, CaP 0.18 — [ARCHITECT](https://arxiv.org/html/2607.23784).
- In a human study (N=6), queries per trial fell from 4.67 to 0.83 once the library was populated (p=0.036), and Task 2 success went from 0/6 to 4/6 — [ARCHITECT](https://arxiv.org/html/2607.23784).
- Human corrections fix what VLM corrections cannot, including depth-estimation errors and physical properties such as slip — [ARCHITECT](https://arxiv.org/html/2607.23784).
- All skills are loaded into context before each generation step, so retrieval is untested and will not scale indefinitely — [ARCHITECT](https://arxiv.org/html/2607.23784).

**FCGraft, Functional Cache Grafting** (arXiv 2606.13097, v2 29 Jul 2026, preprint; Sungkyunkwan University; Franka Research 3, 9 trials per scenario; local Qwen2.5-Coder-14B on RTX 4090; code not stated).
- Function-level validated code and its KV cache are stored in two tiers, and only the faulty region is regenerated on exception. Real world: 77.78% (3.80 s synthesis) office desk and 81.48% (2.85 s) cooking, vs RAGCache 44.44% and 37.04%. Retention score uses recency, frequency, co-invocation, and semantic relevance — [FCGraft](https://arxiv.org/html/2606.13097).

**AgenticRobotics, "You Don't Need To Stay in The Loop"** (arXiv 2608.07555, Aug 2026, single independent author; LIBERO-10 simulation, an outer loop that improves a VLA rather than controlling a robot).
- Skills enter as candidates and advance only with evidence. Tool quality is measured per call, recorded, and expires when the underlying artifact changes: "operational reliability is derived from records, never claimed" — [Yu 2026](https://arxiv.org/abs/2608.07555).

**Earlier foundations, briefly.** LRLL (arXiv 2406.18746) paired an experience memory of instruction-code pairs with a Python skill library that deploys to real robots — [LRLL](https://arxiv.org/html/2406.18746v1). FAEA (arXiv 2601.20334, Jan 2026, simulation only, Claude Agent SDK, code at github.com/robiemusketeer/faea-sim) got 84.9% on LIBERO and 96% on MetaWorld from privileged state. It excluded two MetaWorld tasks because the agent cheated or brute-forced them, and it notes that a high-success agent explores any available path without hard constraints — [FAEA](https://arxiv.org/html/2601.20334v1).

### Inferences
- The shared schema now looks like: trigger or failure signature, preconditions, procedure and code, checks, failure modes, and per-run evidence with an unattended flag. Hebbot's skills-as-database-records already resemble this. The features worth adding are an explicit `Unsure()`-style return to the orchestrator and evidence-based status promotion.
- The strongest argument for compiling is cost. RAX spent about 75 minutes of tool calls on a first air-fryer run, then ran a 210 s program. Hundreds of tool calls for one grasp is the expected shape of a first exploration, and the mistake would be never compiling it.
- ASPIRE and RAX both validate at promotion only. Re-validation after any change to hardware or calibration is an open design point (AgenticRobotics' expiry-on-artifact-change is the nearest treatment, and it is simulation only).

### Gaps
- I could not read RAX's full per-task real-robot totals, RB-Y1 results, or the Compute and Cost section.
- I found no head-to-head comparison of tool-call schemas (primitive-level vs program-level) on one real robot. Claims about which abstraction is "best" are inferred across papers.
- I did not search specifically for updates to CaP-X, Waddle, or RoboClaw. CaP-X appears here only as the simulation substrate and baseline (CaP-Agent0) in ASPIRE. ENPIRE is unchanged in these notes.

## 2. Episodic and experience memory: retrieval keys, consolidation, forgetting. Does retrieval improve real-robot success?

### Takeaway
Real-robot evidence that memory helps exists but is thin, and the best of it favors distilled, verified principles over replaying raw episodes. Most 2026 memory work measures question answering or simulation, and the largest benchmark explicitly declines to claim physical-robot results.

### Cited Findings

**PhysMem** (arXiv 2602.20323, v3 23 Mar 2026, preprint; Stanford and UC San Diego; xArm6, fin-ray grippers, RealSense D435; Gemini-3-Flash planner; code not stated).
- Three tiers: episodic raw experience with symbolic state (cap 3,000), working hypotheses with confidence, long-term principles with decaying importance (γ=0.995). Retrieval first filters symbolically on action type and object properties, then ranks semantically; top-k principles and active hypotheses go in the VLM prompt — [PhysMem](https://arxiv.org/html/2602.20323v3).
- Consolidation clusters experiences by symbolic similarity into typed hypotheses (Avoid, Prefer, Sequence). Promotion needs confidence ≥0.8 and ≥3 supporting episodes; refutation needs ≤0.3 and ≥2 contradicting episodes. Forgetting is Ebbinghaus-style, about 50% retention after 138 unreinforced episodes — [PhysMem](https://arxiv.org/html/2602.20323v3).
- Simulation ablation (Reflect-VLM brick insertion, Franka in MuJoCo, 100 episodes per condition, medium difficulty): full 76%, only principles 51%, only episodic 28%, direct retrieval 23%, without episodic memory 37%, without verification 64%, without forgetting 78%. A separate section reports 53% without PhysMem at medium using the same model. Comparing across tables is therefore suggestive: direct retrieval and episodic-only were apparently worse than having no memory — [PhysMem](https://arxiv.org/html/2602.20323v3).
- Real robot (three tasks, 10–20 episodes per task, sessions over 30 minutes): varying experience utilization, Parts Organization score rose from −1 to 9.7 at full memory and stayed flat at 0%. Out-of-distribution, 10 trials per condition, both prior knowledge and adaptation vs neither: Parts 4/10 vs 0/10, Ball Navigation 4/10 vs 1/10, Balanced Stacking 9/10 vs 4/10 — [PhysMem](https://arxiv.org/html/2602.20323v3).

**Surprise-gated episodic memory** (arXiv 2606.03787, 2 Jun 2026, preprint; MIT and Lockheed Martin; offline CODa and Kinetics data, no robot; code not stated).
- Surprise is an ℓ1 surrogate of the KL between consecutive diagonal-Gaussian predictive distributions over V-JEPA-2 features (64-frame window, robust MAD threshold, non-max suppression). It stores about 1.28 episodes per minute (1.7% of frames) keyed by text-image similarity, location, or time. QA accuracy 0.796 vs 0.711 baseline and 0.761 for uniform or random storage. No closed-loop robot evidence. Re-appearing subjects keep triggering storage, and habituation is future work — [Gorlo et al.](https://arxiv.org/html/2606.03787v1).

**H²-EMv, "Learning to Forget"** (arXiv 2604.11306, v2 5 May 2026, preprint; KIT; 20.5 h of recordings from Armar-7 plus simulated TEACh).
- Hierarchical history tree with decay-based forgetting and an LLM relevance estimate conditioned on natural-language rules learned from user feedback. Reports 45% smaller memory and 35% lower query compute while maintaining QA accuracy, and 70% accuracy gain on second-round queries. This measures verbalization QA, not manipulation success — [H²-EMv](https://arxiv.org/abs/2604.11306).
- The paper reports that first-round performance with forgetting is roughly half that of no-forgetting, which is expected because forgetting removes information. Second-round recovery comes from learning user-specific relevance rules — [H²-EMv](https://arxiv.org/abs/2604.11306).

**EmbodiedMemory-Bench** (arXiv 2609.28236, 23 Sep 2026, preprint; ZJU and others; 2,554 episodes, primarily simulation; project page zju-omniai.github.io/EmbodiedMemoryBench).
- 16 MLLMs evaluated. The best proprietary model (Gemini-3-Flash) reaches 64.2% average, and open-source models score below 45%. The authors' external memory (spatial, event, scene) gains over matched backbones, and the trained EMem-8B improves 20.6 points over Qwen3-VL-8B. The authors state the results "do not establish long-term memory performance on physical robots" — [EMem-Bench](https://arxiv.org/abs/2609.28236).

**SPINE incident memory** (detailed under question 5) stores a fix only when it has a passing validation record, which is a useful memory-admission rule — [SPINE](https://arxiv.org/html/2607.13049).

**In-context examples can backfire.** RoboVista found in-context learning lowered accuracy by 2.8–6.5 points and raised calibration error by up to 9.7 points on its questions, which bears on retrieving prior episodes as prompt context — [RoboVista](https://arxiv.org/html/2607.04610v1).

### Inferences
- The evidence favors storing raw episodes but retrieving distilled rules. Hebbot's three verification levels provide what PhysMem's promotion needs: a confident outcome label per episode.
- PhysMem's symbolic-then-semantic keying maps directly onto Hebbot's skill and object fields. Pure embedding retrieval is the less supported default.
- Surprise gating is a promising storage filter, but evidence that it improves robot behavior is absent. Hebbot's surprise detection would be doing something I did not find tested closed-loop in the literature.
- PhysMem's "without forgetting" (78%) was no worse than full (76%) on that one setting, so forgetting's measured value is scale and cost, not success.

### Gaps
- I found no randomized real-robot comparison of retrieval vs no retrieval with enough trials to bound the effect. PhysMem's 0% utilization condition is the nearest case.
- I found no evidence on retrieval-key choice (object, skill, geometry, failure signature) on real robots beyond PhysMem's design.
- I did not open RoboMemory, eMEM, or Chameleon.

## 3. Verification and failure recovery; reliability of VLMs for spatial and geometric judgment in 2026

### Takeaway
VLMs remain unreliable as geometric or safety judges even at the frontier. 2026 benchmarks put collision grounding below 50% macro-F1 and small open models at about 44–47% on five-way robot questions (Gemini 2.5 Pro 56.5%). The reliable pattern is a deterministic or proprioceptive check for anything measurable, with the VLM reserved for semantic state and fine-tuned on failure data if it must judge outcomes.

### Cited Findings

**Spatial and geometric reliability**
- TouchSafeBench (arXiv 2605.31196, v2 23 Oct 2026, CoRL 2026; Habitat 3.0 simulation, 2,940 episodes; site, no repository listed): across 3 frontier or robotics-oriented VLMs and 9 visual representations, the best average macro-F1 is below 50%. Explicit depth is not automatically converted into robot-body collision evidence, and robot-scene contact is harder than human-contact risk — [TouchSafeBench](https://arxiv.org/abs/2605.31196).
- RoboVista (arXiv 2607.04610, 6 Jul 2026, preprint; UC Berkeley, Princeton, Google DeepMind; 474 expert-annotated five-way questions, chance 20%; site berkeleyautomation.github.io/robovista): Gemini 2.5 Pro 56.5% (highest), Qwen3-235B 51.3% (highest open), GPT-5 48.1% (the paper also lists 55.5% in another table, an internal inconsistency), Qwen3-VL-32B 49.2%, RoboBrain 2.5-8B-NV 47.0%, Qwen3-VL-8B 46.8%, Qwen2.5-VL-7B 43.7% — [RoboVista](https://arxiv.org/html/2607.04610v1).
- For Qwen2.5-VL-7B specifically, misidentification accounted for 30.2% of cases in RoboVista's failure analysis. Chain-of-thought generally hurt scene understanding (up to about 12 points overall) but can help planning for some models — [RoboVista](https://arxiv.org/html/2607.04610v1).
- RoboVista's score correlated with physical bimanual gripper alignment: position error Pearson −0.78, Spearman −0.93. In a surgical knot-tying task (dVRK, simulated or emulated per the text I read), 2/16 stages succeeded without intervention for all three models tested, including Gemini 2.5 Pro — [RoboVista](https://arxiv.org/html/2607.04610v1).
- MV-RoboBench (ICLR 2026): top model GPT-5 about 56.4% and Gemini 2.5 Pro about 49.5% vs 91.0% for humans, on 1.7k multi-view questions (figures from search excerpt only) — [MV-RoboBench](https://arxiv.org/html/2510.19400v2). Authors report single-view spatial scores do not reliably transfer to robotic spatial tasks — [OpenReview](https://openreview.net/forum?id=jXDZJAfRZB).
- RoboAbstention (arXiv 2605.20544, 19 May 2026, preprint; Purdue and Bilkent; images from five robotics datasets, open-sourced at purseclab.github.io/RoboAbstention): on 6,069 instructions where abstention is warranted, the best model (Gemini 2.5 Flash) abstained only 39.0% of the time, and Gemini Robotics ER 1.6 Preview 16.5%. Defensive prompting and in-context learning reach 93.6% (ER 1.6) and 88.6% (GPT 5.4 Mini), though no approach fully solves it — [RoboAbstention](https://arxiv.org/abs/2605.20544).
- Robocurve's direct-control runs show the same physical weakness. Models closed the gripper without grasping 32–64% of the time, and weaker models misread a pinch width of 0.02–0.05 and regrasped securely held bowls — [Bitter Lesson paper](https://arxiv.org/html/2610.25287v2).

**Outcome verification and recovery**
- AgenticLab (arXiv 2602.01662, v3 9 Mar 2026, preprint; Purdue and IIT; UR5e; authors promise a full release): sorting, 20 trials per pipeline — Gemini 3 Flash 75.0%, Gemini 3 Pro + LangSAM 70.0%, Gemini Robotics 50.0%, Qwen3-VL-Plus 25.0%, Gemini 3 Pro 10.0%, GPT-4o + LangSAM 5.0%, Qwen-VL-Max and GPT-5.2 + LangSAM 0.0%. Verifiers mis-stated execution state (for example, claiming the object is not in the gripper after a successful pick), and locally hosted Qwen2.5-VL-7B and Molmo-7B corrupted PDDL syntax. The authors argue per-check accuracy that looks acceptable compounds to low full-trial success — [AgenticLab](https://arxiv.org/html/2602.01662v3).
- RAX real-robot failures include a false "clean" verdict on thin streaks and a false-negative check. Its grasp verification combines gripper width with a wrist-camera view — [RAX](https://arxiv.org/html/2610.22496).
- ARCHITECT exposes `verify_grasp` as a tool and `move_ee_guarded` for contact-terminated motion, placing grasp and contact verification below the LLM. Its VLM-correction ablation scored 0.38 vs 0.81 with a human corrector — [ARCHITECT](https://arxiv.org/html/2607.23784).
- Critics: "Robot Critics that Sweat the Small Stuff" (arXiv 2606.21572, 19 Jun 2026, preprint) finds current VLMs struggle with the small visual differences separating success from failure. Fine-tuning from paired success and failure rollouts raised policy success by 11% on real tasks and 5.9% in simulation — [Critics](https://arxiv.org/abs/2606.21572).
- LabRobFail (arXiv 2607.23704, v2 29 Jul 2026, preprint; simulation only, 20,000+ trajectories): failure detection accuracy for off-the-shelf models was Qwen3-VL-8B 59.50, Gemini 2.5 Flash 57.13, GPT-5.4 52.34; temporal localization 5.59–15.88. A fine-tuned Qwen3-VL-8B reached 90.83 and 77.21. The paper reports no false-negative rates — [LabRobFail](https://arxiv.org/html/2607.23704).
- "No Free Checker" survey (arXiv 2609.09250, Sep 2026, about 150 verifiers): credibility falls as availability rises across human, rule-based and formal, learned, and model-intrinsic verifiers. It recommends counting false positives separately from false negatives because their costs differ — [No Free Checker](https://arxiv.org/abs/2609.09250).
- Guardian (arXiv 2512.01946) fine-tunes a multi-view VLM on synthetic failure data and reports it can act as a plug-and-play verifier that raises task success (search excerpt only) — [Guardian](https://arxiv.org/html/2512.01946).
- Dream2Fix (arXiv 2603.13528) reports correction accuracy rising from 19.7% to 81.3% and 46% zero-shot real-robot recovery (search excerpt only) — [Dream2Fix](https://arxiv.org/html/2603.13528).
- "Agentic AI for Robot Control" (arXiv 2602.13081, 13 Feb 2026, DFKI; Mobipick and Valdemar, proof-of-concept, no success rates, code at dfki-ni.github.io/AGENTS-MAKE-2026): reports stale world-state placements, "verbal only" failures where the model explains a solution without acting, and no preemption of long actions — [Lima et al.](https://arxiv.org/html/2602.13081v1).

### Inferences
- Hebbot's false "clear" from qwen2.5vl:7b is consistent with the literature: Qwen2.5-VL-7B scores 43.7% on RoboVista, and the three frontier or robotics-oriented VLMs tested on TouchSafeBench stayed below 50% macro-F1. This looks like a class-level weakness, not a tuning problem.
- A VLM verdict should never be the sole basis for a safety-relevant "clear". It can veto, but it should not grant, unless a proprioceptive or depth check also passes. Abstention prompting (RoboAbstention's jump to 93.6%) is a cheap experiment, though it may raise false refusals.
- Hebbot's mechanical / proprioceptive / perceptual tiers match the "no free checker" availability-credibility ordering. Reporting false "clear" separately from false "blocked" would make the perceptual tier auditable.
- The fine-tuning results (LabRobFail, Critics) suggest Hebbot's own episode store could be the training set for a local verifier, since it holds proprioceptive labels.

### Gaps
- I found no 2026 study reporting false-negative rates for "clear" or collision judgments across local and frontier VLMs on real hardware. TouchSafeBench is simulation and reports macro-F1.
- I found no independent spatial benchmark including GPT-6, Opus 5.x, or Gemini 4 on metric geometry. The newest models appear only in Robocurve's task benchmark.
- I could not extract RoboVista's per-model distance errors, which appear only in a figure.

## 4. Benchmarks of LLM-commanded robots since September 2026; call budgets, cost per task, latency

### Takeaway
The main new benchmark is Robocurve's October paper, which shows rapid improvement but still only 45–48% at the top under a 40-action cap and an unvalidated LLM grader. Cost per subtask spans about 12x across models, and call budget effects were asserted but not measured.

### Cited Findings

**"The Bitter Lesson Comes to Robotics"** (arXiv 2610.25287, v1 18 Oct, v2 21 Oct 2026; submitted to the TAG workshop at CoRL 2026; authors from Robocurve; bimanual I2RT YAM with 2 wrist cameras and 1 overhead camera). This is an update to the already-reviewed Astra benchmark.
- 9 models in 15 configurations, 6 RoboDojo tasks, 20 trials per (model, configuration, task), 40 actions per trial. Actions are `move_to` (Cartesian, both arms), `done`, and `give_up` via the Inspect Robots framework — [LessWrong post by authors, 26 Oct 2026](https://www.lesswrong.com/posts/ndS9Xn8Qx5PppHJ3Q/the-ability-of-frontier-models-to-control-robots-is-rapidly).
- Strongest models averaged 45–48%: Gemini 4 Argon (H+PR) 48.4%, Opus 5.5 (XH) 0.13 points lower, GPT-6 Astra (H) 42.5%, Opus 5.5 (H) 38.2% — [paper](https://arxiv.org/html/2610.25287v2).
- Doubling time 53.9 days (95% CI 36.1–59.5; leave-one-out 44.3–61.1), with cost and speed trends resting on 3 and 4 record-setting pairs that the authors call exploratory — [paper](https://arxiv.org/html/2610.25287v2).
- Cost per subtask: Gemini 3.8 Flash (H) $0.38 at about 4.18 min; Gemini 4 Argon (H+PR) $0.97; Opus 5.5 (H) $1.22; GPT-6 Astra (H) $1.67; GPT-6 Astra (H+UF) $4.51 at about 1.1 min (54.4 subtasks/hour). The authors state teleoperators take 25–40 s per task — [paper](https://arxiv.org/html/2610.25287v2), [post](https://www.lesswrong.com/posts/ndS9Xn8Qx5PppHJ3Q/the-ability-of-frontier-models-to-control-robots-is-rapidly).
- Call budget: the authors say increasing it "would almost certainly improve completion rates for all models" but did not test this. Opus 5 averaged 6.7 actions just positioning the gripper — [paper](https://arxiv.org/html/2610.25287v2).
- Failure causes include physical imperfection (arm sag, parallax), unrecoverable errors, and arm overheating when Argon's long reasoning left joints under load. Stand Up Bottles succeeded in 6 of 180 trials due to reach and pitch limits — [paper](https://arxiv.org/html/2610.25287v2).
- **Evaluation-quality flag:** trials are graded by a GPT-6 Astra (H) instance from footage and I found no human validation of the grader in the paper. A trial keeps its highest subtask score even if progress is later undone. No independent replication found. The authors report that models are mainly limited by efficiency rather than reasoning — [paper](https://arxiv.org/html/2610.25287v2), [post](https://www.lesswrong.com/posts/ndS9Xn8Qx5PppHJ3Q/the-ability-of-frontier-models-to-control-robots-is-rapidly).
- Robocurve's earlier RoboDojo-RC Tier 1 report published all 360 trial traces and the framework is open source (secondary coverage, search excerpt only) — [RuntimeWire](https://runtimewire.com/article/robocurve-opus-55-robotics-benchmark).

**Fraunhofer IPA MCP safety benchmark** (arXiv 2609.07288, 7 Sep 2026, accepted at IEEE CBS 2026; Unitree G1 EDU; results are text-prompting Layer 1, with preliminary simulation and a single hardware spot-check; materials promised).
- 40 sessions of 100 turns across Claude Haiku 4.5, GPT-4o-mini, Gemini 2.5 Flash, qwen3:8b. Claude and Gemini stayed at or near zero violations; GPT-4o-mini committed up to 13 per session. Sliding-window context reduced behavioral issues 42–57% for every cloud backend while nearly doubling GPT-4o-mini's violations (3.8 to 7.2) — [Bajrami et al.](https://arxiv.org/abs/2609.07288).
- Latency per operator command, median (p95): GPT-4o-mini 4.4 s (13.0), Gemini 2.5 Flash 5.9 s (8.5), Claude Haiku 4.5 4.9 s (9.9); local qwen3:8b 1.26 s (1.66). Without prompt caching, a Claude run had a 48.3 s median rising from 17.6 to 85.0 s as context grew — [Bajrami et al.](https://arxiv.org/abs/2609.07288).
- An early Gemini run called `stop()` hundreds of times in an infinite loop. A guard of 20 tool calls per turn then fired on 21 turns. qwen3:30b reached zero violations in simulation by flatly refusing 62% of commands — [Bajrami et al.](https://arxiv.org/abs/2609.07288).

**Call budget and cost elsewhere.** In FAEA (simulation), removing a 10-trial cap raised LIBERO from 70.6% to 84.9%; cost per task ranged $0.51–$5.60 on ManiSkill, and decision cycles were 2–8 s — [FAEA](https://arxiv.org/html/2601.20334v1). ASPIRE's drawer task spent 334.92M tokens and exhausted its budget without a program, vs 81.67M with a skill — [ASPIRE](https://arxiv.org/html/2607.00272).

### Inferences
- A 40-action budget mostly measures efficiency. Hundreds of tool calls for a grasp in Hebbot should not be compared to Robocurve's figures without adjusting for tool granularity. A move-to-Cartesian call and a skill call are not the same unit.
- Prompt caching and context management appear to be first-order for latency and possibly for safety behavior, in the single study that measured them. That study's sliding-window result was mixed by model.
- Robocurve's 6-task, single-platform, LLM-graded design supports the trend claim only weakly. The "doubling" extrapolation should be treated as the authors' own framing.

### Gaps
- I found no benchmark measuring success as a function of call budget on real hardware.
- I found no dollar cost per real-robot task for RAX (I did not reach its cost section) and no independent replication of any cost figure.

## 5. Coding agents developing or debugging robot systems at the bench; keeping claims honest

### Takeaway
Bench use is now documented (SPINE, ASPIRE, RAX) and helps measurably, but I found no paper that directly studies agents writing unsupported causal stories about hardware. The working honesty mechanisms are structural: closure requires a live check after the last intervention, and promotion requires recorded evidence rather than the agent's narrative.

### Cited Findings

**SPINE** (arXiv 2607.13049, v2 21 Sep 2026, preprint; Northwestern; DOBOT X-Trainer and AgileX PiPER; project site magics-lab.github.io/SPINE-web, code release not stated).
- Runs inside Claude Code with `/spine-profile-build` and `/spine-debug`, an observe-triage-repair-verify loop with four read-only diagnostic subagents. Physical fixes go to the operator step by step — [SPINE](https://arxiv.org/html/2607.13049).
- 12 injected-fault scenarios, novice operator with SPINE (3 trials per scenario) vs human operators with plain Claude Code (Sonnet 5.0; 7 on DOBOT, 9 on PiPER). Fraction of faults resolved: DOBOT 1.00 vs 0.76; PiPER 1.00 vs 0.99. Time to teleoperation: 10:20 vs 14:51 and 7:48 vs 12:30 — [SPINE](https://arxiv.org/html/2607.13049).
- Honesty mechanism: a case closes only when the live pipeline and profile-based hidden readiness checks both pass in the same validation cycle after the most recent intervention. Launching without errors is insufficient, and incident memory stores a fix only with a passing record: the language model's diagnosis alone does not count — [SPINE](https://arxiv.org/html/2607.13049).
- Removing the hardware playbook dropped DOBOT resolution to 0.57 and raised time to 31:25; these ablations had one trial per scenario and the authors call them preliminary. Injected faults on two platforms; no claim of long-term reliability; readiness covers a default 10-second window — [SPINE](https://arxiv.org/html/2607.13049).

**ASPIRE** requires a fresh verifier actor to reproduce success before skill promotion, separating the agent that claims a fix from the one that confirms it — [ASPIRE](https://arxiv.org/html/2607.00272).

**RAX** uses unattended-success evidence for merges, but real-robot success is self-assessed, and its real-robot logs record a false "clean" verdict and a false-negative check — [RAX](https://arxiv.org/html/2610.22496).

**AgenticRobotics** (arXiv 2608.07555, Aug 2026, simulation, LIBERO-10) is the most explicit on claim discipline: immutable objective, controller-owned measurement, an append-only ledger, and evidence-graded skills.
- Reports 0.001 false promotions per run hardened vs 0.005–0.021 for shipped gates, anytime-valid decisions under optional stopping, and 6 of 6 artifact-tampering classes caught — [Yu 2026](https://arxiv.org/abs/2608.07555).
- Reports that on the one lineage measured, taking the final checkpoint beat every measurement-driven selector, including its own. It explicitly scopes its title as an operational claim rather than a selection claim — [Yu 2026](https://arxiv.org/abs/2608.07555).

**FAEA** excluded MetaWorld runs where the agent read internal simulator state or brute-forced the task — a documented case of shortcutting when the objective is unguarded — [FAEA](https://arxiv.org/html/2601.20334v1).

**General agent-reporting evidence.** MIRAGE-Bench documents agents submitting patches despite visible test failures (search excerpt only) — [MIRAGE-Bench](https://arxiv.org/pdf/2507.21017). A practitioner post argues verification cannot be delegated to the agent doing the work (anecdotal) — [Gary Bot](https://fromtheterminal.substack.com/p/ai-did-the-work-now-try-to-audit).

### Inferences
- The techniques transferable to Hebbot's notebook: (a) a causal claim must name the measurement that would falsify it; (b) a fix counts only after a post-intervention check; (c) the agent that proposes a mechanism should not be the one that scores it; (d) record the status of a claim (hypothesis vs tested vs replicated) separately from its prose.
- Hebbot's verification levels could serve as the "controller-owned measurement" in AgenticRobotics' sense, provided the agent cannot edit or reinterpret the record after the fact.

### Gaps
- I found no study measuring how often coding agents produce unsupported causal explanations of physical failures, or comparing notebook practices for preventing this. This is an open niche.
- SPINE's comparison pools novice-with-SPINE against other humans and has few SPINE repetitions, so the effect size is uncertain.

## 6. Hierarchical architectures: a slow reasoner supervising a fast reflex with bidirectional feedback

### Takeaway
The architecture is widely adopted and well justified by latency measurements, but direct evidence on bidirectional feedback is scarce. The most relevant real system (TypeGo) is a preliminary quadruped prototype whose benefit is mostly latency, not success, and it costs more tokens.

### Cited Findings
- TypeGo (arXiv 2607.05482, 6 Jul 2026, preprint; Yale; Unitree Go2): four concurrent loops — S0 reflex (100 Hz, condition functions compiled offline by an LLM, so no LLM sits on the reaction path), S1 speculative action streamer, S2 decomposer and S3 scheduler (0.5 Hz). A firing reflex preempts in-progress processes, with 50 ms S0 reflex handling reported — [TypeGo](https://arxiv.org/abs/2607.05482).
- TypeGo results (T1–T5, /10 each): TypeGo 44/50, ReAct 42/50, plan-and-execute 39/50. Time to first action 0.87 s vs 1.46 s vs 3.27 s; per-step delay 0.62 s vs 1.25 s for ReAct. Tokens about 3x ReAct (normalized 1.00 vs 0.32). The first speculative action matched the eventual plan in about 60% of cases. Plan-and-execute failed T3 0/10 for lack of closed-loop feedback, which supports replanning but is a single task — [TypeGo](https://arxiv.org/abs/2607.05482).
- The safety benchmark concludes from latency measurements that even the fastest backend responds an order of magnitude slower than a 500 ms evaluation window and that time-critical invariants must be owned by deterministic monitors, with the LLM confined to conversational timescales — [Bajrami et al.](https://arxiv.org/abs/2609.07288).
- RAX's `raise Unsure()` is a form of upward feedback from compiled program to agent — [RAX](https://arxiv.org/html/2610.22496).
- AgenticLab reports that dense per-action verification helps in strongly dependent tasks while sparser checks reduce latency, which is a direct trade-off on the supervisor's check rate — [AgenticLab](https://arxiv.org/html/2602.01662v3).
- FARE (arXiv 2601.14681, Jan 2026): LLM global waypoints over a topological graph with an RL local policy, deployed on hardware in a large building (search excerpt only) — [FARE](https://arxiv.org/abs/2601.14681).
- Agentic Fast-Slow Planning (2026, CARLA simulation) reports up to 45% lower lateral deviation vs MPC baselines (search excerpt only) — [listing](https://awesomepapers.io/robotics/papers/2604.01681).
- Green-VLA uses a VLM planner that queries a feedback module when subtask-end probability is high, with no ablation isolating the feedback (search excerpt only; the paper-to-URL mapping is inferred from the results list) — [Green-VLA](https://arxiv.org/html/2602.00919v2).
- A Princeton thesis (2026, simulation) had an LLM generate programmatic policies deciding when to plan vs react, reaching 96% vs 72% pure planning in Motion2D with 83% fewer collision checks (search excerpt only) — [thesis](https://theses-dissertations.princeton.edu/entities/publication/58e23d96-d779-41df-8c50-447dfbc6ae0a).

**Evidence that undermines the architecture or its typical framing**
- Robocurve's direct-control results show a flat LLM loop can reach 45–48% with no fast layer at all, and the authors report efficiency rather than reasoning as the main limit. Physical failures such as arm overheating during long reasoning are a cost of LLM-in-the-loop design — [paper](https://arxiv.org/html/2610.25287v2).
- RAX's reliability gains come from compiling decisions into programs, moving the slow reasoner out of the loop rather than supervising it — [RAX](https://arxiv.org/html/2610.22496).
- Long actions are not preempted in Lima et al., since events are checked only between actions, so slow-only loops lose exactly what the architecture is meant to provide — [Lima et al.](https://arxiv.org/html/2602.13081v1).

### Inferences
- The supported design is asymmetric. Fast layers need hard authority (reflex preempts) and slow layers need a clean upward signal (`Unsure()`, failure signature). Evidence for continuous bidirectional coupling is absent, so richer feedback should be treated as a hypothesis to test.
- For Hebbot, "skill runs and returns a structured uncertainty signal" looks better supported than a continuously supervising LLM. TypeGo's reflex-compiled-from-language idea fits Hebbot's skill records.

### Gaps
- I found no real-robot ablation of feedback direction (top-down only vs bidirectional) on manipulation, positive or negative.
- TypeGo is explicitly preliminary, on one quadruped, with 10 trials per task, and it does not separate reflex benefit from speculative streaming on success.
