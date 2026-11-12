# Compressing agent- or code-driven robot behaviour into fast learned reflexes at very small data scale, and autonomous real-hardware data collection

Reading note. Search date 2026-11-09. Evidence tags: **[read]** means I read the paper's page or full text (long pages were sometimes only partly read, flagged where it matters); **[snippet]** means I saw only a search-result excerpt, so verify before relying on it. "Derived" means my arithmetic from the paper's numbers. A code status of "not stated" means I did not find a release in what I read, not that none exists. Reviewed-list updates: RoboClaw has no version newer than v3 (1 Apr 2026) that I could find. My one combined query for updates returned only RoboClaw, so I have no update information on RISE, Waddle, CaP-X or EDGE. ENPIRE (2606.19980) and ASPIRE (2607.00272) appeared only as search hits and I did not re-read them.

## Which methods distil behaviour from an LLM agent, code-as-policy, or classical controller into a neural or lightweight policy, and what data volumes and gains are reported on real robots?

### Takeaway
I found no paper that trains a neural reflex from tens of real-robot episodes produced by an LLM agent. The evidence supports two nearby routes: freeze agent behaviour into code that makes no model calls (real but small n), or use a non-parametric or scripted expert to generate hundreds to thousands of real or simulated successes, then train BC. Tether is the strongest real-robot example of the second route, with 1,085 real successes from 1,946 attempts, about 25 times Hebbot's current attempt count and over 200 times its success count.

### Cited Findings

#### Real robot
- **Tether (autonomous play, then BC).** A non-parametric correspondence-driven trajectory-warping policy runs from 10 demos per task on a Franka Panda with two ZED cameras. Four play sessions (about 26 h, 6 tasks) produced 1,085 successes from 1,946 attempts (55.8%), filtered by a VLM. Diffusion Policies trained on the filtered successes improved over time and reached success rates similar to policies trained on human demos (human baselines used 141 to 202 demos per task). arXiv March 2026; venue per an OpenReview PDF is ICLR 2026. Code not stated; project page tether-research.github.io. **[read, partial]** — [arXiv 2603.03278](https://arxiv.org/html/2603.03278v1), [OpenReview](https://openreview.net/pdf/50a58fee893ebdf6275b02112aa34201b0ccb068.pdf)
- **Learning and Transferring Closed-Loop Robot Software (Kuroki, Kawaharazuka, Tang; arXiv 2609.19906, v3 30 Oct 2026).** A coding agent writes closed-loop code from 10 successful demos per task, refines it in simulation, and stores validation-selected versions. The final policy runs at 50 Hz over the SONIC whole-body controller with no coding-model calls, against about 52.6 model calls per episode for the online coding-agent baseline. Physical result on a Unitree G1: 11/15 (close drawer 4/5, close fridge 3/5, navigate-and-stop 4/5). The physical runs test learning only, not transfer. Code not stated. **[read]** — [arXiv](https://arxiv.org/html/2609.19906)
- **Residual Compliant Policy (arXiv 2610.14035, 1 Oct 2026).** Franka FR3 with a Resense HEX21 F/T sensor and D435. Uses 1 or 3 demos per task (7 total), augmented to 40 to 150 trajectories by Laplacian editing and cross-entropy trajectory optimisation. A flow-matching base policy is topped by a residual PPO policy, trained in simulation, that offsets the admittance controller's reference pose. 520 real trials: in-distribution base 89/90, residual 90/90; hard tests base 35/40, residual 40/40. Used a digital twin (Newton/MuJoCo plus FoundationPose) and a haptic device for demos. Project page only; code not stated. **[read]** — [arXiv](https://arxiv.org/html/2610.14035)
- **ZPRL, latent residual RL over a frozen BC policy (arXiv 2605.19919, 19 May 2026).** Base 82 to 150 demos; online 330 to 1,350 real trajectories (3.5 to 12.5 h). Insert Bills rose from about 20% to 77.5%; mean improvement 33.7% (absolute versus relative is not clear). Hardware xArm-6 and Franka Panda; bimanual hardware for two tasks unnamed. The 25-demo ablation is simulation only. Code not stated. **[read, partial]** — [arXiv](https://arxiv.org/html/2605.19919)
- **HiRe-MoMa (arXiv 2610.20607, CoRL 2026 submission).** Fine-tunes a frozen base mobile-manipulation policy on the real robot; base pre-trained on 80 to 200 trajectories. **[snippet]** — [arXiv](https://arxiv.org/abs/2610.20607)
- **APEX (arXiv 2606.16504, 15 Jun 2026).** An adaptive tracking layer sits between policy and low-level controller, with no model change. UR5, 20 trials per task. Mean success moved only from about 43% to 48% (flow matching) and 48% to 50% (diffusion), with individual tasks moving between −10 and +15 points. Code not stated. **[read]** — [arXiv](https://arxiv.org/html/2606.16504)
- **EmbodiedSWE-Gen (arXiv 2609.27308, Sept 2026).** Expands one coding-agent solution in simulation into many trajectories, kept only if they pass a grader and an open-loop replay check. For a real test, 500 simulated successes fine-tuned π0.5-DROID, and one real lamp-disassembly rollout succeeded. That is a single-rollout proof of concept. A community repository exists, not confirmed as official. **[snippet]** — [alphaXiv](https://www.alphaxiv.org/abs/2609.27308)
- **MimicFunc (arXiv 2508.13534, 2025).** Generates rollouts on a physical robot and keeps successes as training data for a visuomotor policy. Pour: 53.85% versus 46.15% for teleoperated demos; small sample. **[snippet]** — [arXiv](https://arxiv.org/pdf/2508.13534)
- **RoboClaw (already reviewed, no new version found).** v3 is dated 1 Apr 2026. π0.5 with LoRA on an AgiBot G01; each rollout iteration adds 50 samples across five iterations. No total episode count, collection hours, labelling procedure detail or promotion test appears. **[read, targeted]** — [arXiv](https://arxiv.org/html/2603.11558v3)

#### Agent-as-policy on real robots (the deliberate path, no distillation)
- **ARCHITECT (arXiv 2607.23784, 26 Jul 2026).** Franka Panda, Robotiq 2F-85, ZED cameras, cuRobo, Claude Opus 4.6 orchestrator with GPT-5.4 VQA. No demos; up to 5 corrected rollouts per method, then 10-episode evaluations over 8 tasks (400 real rollouts in the benchmark). Success 0.70 to 1.0 on seven of eight tasks; the extraction I got names fold-cloth (0.80) as the exception even though 0.80 is inside that range, so check the paper's table before quoting per-task values. Project page robo-architect.github.io; code release not stated. **[read]** — [arXiv](https://arxiv.org/html/2607.23784)
- **Agent as Policy (arXiv 2609.12541, Sept 2026).** Real-world tasks; at least 80% success in 7 of 8 task configurations, no task-specific training. Abstract says saved procedures shorten repeated trials. Hardware and episode counts were not on the abstract page. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2609.12541)
- **GPT-Policy (arXiv 2609.19138, 16 Sept 2026).** A commercial VLM learns in context with no gradient updates, using a context compiler and a constrained controller. Code at github.com/cheng-haha/GPT-Policy. No numbers on the abstract page. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2609.19138)

#### Simulation only, or foundational
- **RHO (arXiv 2606.16458, 15 Jun 2026).** Coding agents search for neurosymbolic policy repositories at training time. LIBERO-PRO 45.0% versus π0.5 12.83%; Robosuite 70.0% with single-turn deployment and no corrective LLM edits. Simulation benchmarks only. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2606.16458)
- **OpenRUA (arXiv 2610.02459, 1 Oct 2026).** Coding agent as zero-shot visuomotor policy, simulation only; 99.0% on CaP-Bench, 28.1% on unseen RoboCasa365 composites. Fresh workspace each episode, so no reuse across episodes. Code at github.com/terminalworld/OpenRUA. **[read, partial]** — [arXiv](https://arxiv.org/html/2610.02459v1)
- **Push-T with agentic robotics (arXiv 2608.18227).** Code policies written by Claude Code reached about 96.8% mean target coverage versus about 87.4% for a Diffusion Policy trained on 200 human demos, with fewer steps. Simulation. **[snippet]** — [arXiv](https://arxiv.org/pdf/2608.18227)
- Foundational planner-to-BC distillation: OPTIMUS distils a TAMP expert into a visuomotor transformer (CoRL 2023) — [PMLR](https://proceedings.mlr.press/v229/dalal23a/dalal23a.pdf); Scaling Up and Distilling Down uses planners and grasp samplers to collect data and distils a language-conditioned policy (CoRL 2023) — [PMLR](https://proceedings.mlr.press/v229/ha23a/ha23a.pdf); MoPA-PD distils motion-planner-augmented RL into an image policy, motivated by planner compute cost (CoRL 2021) — [arXiv](https://arxiv.org/html/2111.06383). **[snippet]**

### Inferences
- At 5 successes, frozen code or a parameterised skill is the only credible "reflex" Hebbot can promote today. A neural BC policy trained on 5 episodes has no support in anything found here; the small-n neural successes all depend on heavy augmentation, a twin, or an object-pose representation.
- Hebbot's existing skill-episode store is the same shape as Tether's pipeline (expert generates attempts, a verifier filters, BC trains). The missing piece is volume, not architecture. Tether's 42 successes per hour (derived from 1,085 in 26 h) would be reached only by raising both success rate and cycle rate.
- The 56 mm LOO warm-start regressor is a poor candidate for end-to-end replacement. Residual Compliant Policy's structure, where learning adjusts a classical reference and the classical loop keeps contact authority, fits better with a touch-find layer that already works.
- Feasibility on one Franka and 16 GB: frozen code, trajectory warping and small BC or flow policies look feasible. Residual Compliant Policy is feasible only if a usable twin and force signal exist. ZPRL's 330 to 1,350 real trajectories are out of reach until self-reset works. The π0.5 LoRA route (RoboClaw, EmbodiedSWE-Gen) has unverified VRAM needs at 16 GB.

### Gaps
- No real-robot paper found that distils real LLM-agent episodes into a neural policy at tens of episodes, and none reporting latency gain of a distilled policy against its planner on hardware in 2026. This is an absence in my searches, not proof of absence.
- ExpertGen (March 2026, diffusion expert refined by noise-space RL then distilled) appeared in a search summary without a URL, so it is not cited above.
- Tether's per-task figures (Figure 6) and its 1/5/10-demo ablation numbers were not in the extracted text.

## What is the current best few-demo learning (1–50 demos) on real arms?

### Takeaway
At 1 to 10 demos, decomposed alignment-plus-replay and trajectory warping beat monolithic BC in the best-supported comparisons. Neural policies become competitive at 1 demo mainly when Gaussian-splat or point-cloud augmentation expands that demo to hundreds. I did not extract precision tolerances, so these rates should not be assumed to hold for tight-tolerance grasps.

### Cited Findings

#### Retrieval, warping, decomposition (real robot)
- **MT3, Learning a Thousand Tasks in a Day (Science Robotics, 12 Nov 2025; foundational).** Sawyer, one head-mounted D415. Alignment via pose estimation and motion planning, then open-loop replay of end-effector velocities, with geometry-based retrieval. With 3 demos per task it outperformed all other methods given 50 demos per task. On 1,000 tasks with 1 demo each: 78.25% seen and 68% unseen over 2,200 rollouts. Decomposition gave roughly an order-of-magnitude data-efficiency gain below 10 demos. Code not stated; rollout dataset released. **[read, partial]** — [arXiv](https://arxiv.org/html/2511.10110)
- **Tether (see above).** 10 demos, 12 tasks, 10 trials each on a Franka. Authors claim it beats all baselines at few demos, including Diffusion Policy on 10 demos, zero-shot and finetuned π0, and KAT, which had no successes. Handles coffee-pod insertion without a wrist camera. Runs correspondence on one NVIDIA A6000. **[read, partial]** — [arXiv](https://arxiv.org/html/2603.03278v1)
- **FOCI Policy (arXiv 2609.08743, CoRL 2026).** Extracts interaction segments and represents skills as relative SE(3) motion between objects; reports strong results on RLBench, COLOSSEUM and real tasks with much less data. Hardware, demo counts and success numbers were not on the abstract page. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2609.08743)

#### Keypoint and object-centric (real robot)
- **Afford2Act (arXiv 2510.01433).** Keypoint policy with 40 demos per task; degrades less than dense-image baselines as demos are cut. **[snippet]** — [arXiv](https://arxiv.org/html/2510.01433)
- **KALM (arXiv 2410.23254, 2024; foundational).** Keypoint abstraction from large models, about 10 demos, real-world tasks. **[snippet]** — [arXiv](https://arxiv.org/pdf/2410.23254)
- **KEYGEN (arXiv 2609.28818).** Object-centric 3D keypoints fed to a diffusion policy; reports zero-shot robustness to rescaling. No demo-count figure seen. **[snippet]** — [arXiv](https://arxiv.org/pdf/2609.28818)

#### Demonstration generation (real robot)
- **FTE, Follow-the-Expert (arXiv 2605.01232, 2 May 2026).** Spot arm, one demo, DMP trajectory prior with a 3DGS twin, Diffusion Policy on 256 synthesised demos. 40 real trials per condition: sweeping 92.5% (RoboSplat's MPLib 12.5%, 1001 Demos' TrajOpt 75.0%); push-pick-place 75.0% (2.5%, 30.0%); write "A" 62.5% (0.0%, 5.0%). Authors' own baseline implementations. Code not stated; compute not stated. **[read]** — [arXiv](https://arxiv.org/html/2605.01232)
- **RoboSplat (arXiv 2504.13175, 2025; foundational).** One demo plus multi-view images on a Franka FR3 with two D435i. 87.8% mean real success versus 57.2% for a baseline on 200 real demos with 2D augmentation. **[snippet]** — [arXiv](https://arxiv.org/html/2504.13175)
- **One Demo is Worth a Thousand Trajectories (arXiv 2606.19586).** Fisheye eye-in-hand Gaussian-splat action-view augmentation with trajectory optimisation. arXiv date is 17 Jun 2026 but the journal reference is CoRL 2025 (PMLR 305), which the page does not explain. No numbers on the abstract page; project page chuerpan.com/1001-demos.github.io. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2606.19586)
- **DemoGen (arXiv 2502.16932, RSS 2025; foundational).** Hundreds of spatially augmented demos from one human demo in seconds, using point-cloud editing, with no on-robot rollouts, unlike MimicGen. Code released. **[snippet]** — [GitHub](https://github.com/TEA-Lab/DemoGen), [RSS](https://roboticsconference.org/program/papers/157/)
- **Follow-ups.** AffordGen (arXiv 2604.10579) generates diverse demos via affordance correspondence — [arXiv](https://arxiv.org/pdf/2604.10579); SoftMimicGen (arXiv 2603.25725) uses 1 to 3 source demos per task to generate 1,000 for deformables — [arXiv](https://arxiv.org/pdf/2603.25725). **[snippet]**

#### Relevant to precision, sim-only
- **ZPRL demo ablation (simulation, Robomimic Square).** Final success 0.86 at 25 demos, 0.96 at 50, 0.98 at 75 and 100, with steps to 0.9 falling from 2.54M to 1.21M between 25 and 50. Residual RL needs far more interaction than a small real budget allows. **[read]** — [arXiv](https://arxiv.org/html/2605.19919)

### Inferences
- Best fit for Hebbot now is MT3-style or Tether-style alignment plus replay, built from the 5 verified grasps as source trajectories, because those methods are designed for very few demos and use one RGB-D view (MT3) or two RGB views (Tether). It needs little or no policy training and can be promoted through the existing gate as a lightweight artifact.
- DemoGen-style point-cloud augmentation is the cheapest neural follow-on. It needs a calibrated depth camera and no twin. Splat approaches need multi-view capture, which adds calibration risk in a setup where recent notebook entries found the error to be a rotation and scale.
- Precision is the open question for Hebbot's small blocks. Tether's authors claim tasks requiring millimetre-level precision, including coffee-pod insertion, but I did not see measured tolerances, so check its figures and project page first.

### Gaps
- I could not verify Instant Policy (ICLR 2025) follow-up numbers, or the 2026 in-context papers (arXiv 2604.15215, 2603.07530, "RoboTTT", "Behavior prompting policy"). They appeared only in reference lists.
- No head-to-head of warping versus splat augmentation versus keypoint BC on the same hardware and tasks was found.
- GEN-1.5 was already covered in lab notebook #156-157 and was not re-checked here.

## Autonomous self-resetting data collection and self-supervised real-world grasp or placement learning: yields per hour, labelling, reset strategies

### Takeaway
Real, published autonomous collection runs at roughly 40 to 75 attempts per hour with human interventions on the order of 0.3% to 6% of trials. In the two best-documented systems, reset is handled by task design (each end state is another task's start) or by a physically validated reset grasp. The best measured label quality is a pre/post-image VLM check at 98.4% precision and 89.6% recall.

### Cited Findings

#### Real robot
- **Tether.** About 26 h, 1,946 attempts (about 75 per hour, derived), 1,085 successes (one per 86 s), 5 interventions (0.26%; about one per 5.2 h). No manual resets: tasks are arranged so each end state is a valid start for another, and failures randomise the scene. Label: Gemini Robotics-ER 1.5 comparing pre/post images from left, right and wrist views, 98.4% precision and 89.6% recall on success across all 1,946 attempts. **[read, partial]** — [arXiv](https://arxiv.org/html/2603.03278v1)
- **AutoDex (arXiv 2606.23689, v2 4 Nov 2026).** xArm with Allegro or Inspire hand and 20 synchronised RGB cameras. 3,593 trials over 100 objects, 1,912 successes (Allegro 39.4%, Inspire 75.7%). A 500-trajectory study took 10.3 h versus 28.4 h for teleoperation (about 48 per hour, derived), with 31 interventions (about 6%, derived) and 0.2 human-hours versus 9.3. Mean loop 48.2 s. Label: multi-view 6D tracking, success if the object stays at least 5 cm above initial height for the full 3 s hold; label accuracy not measured. Reset: reorient to the least-seen stable pose using a cached collision-free reset grasp; passive release ejected the object 5.3% of the time. Grasps from the physically validated database reached 76% on 515 trials across 20 objects, versus 34% for the model-screened database. No policy training. Code and data listed at snuvclab.github.io/AutoDex. **[read]** — [arXiv](https://arxiv.org/html/2606.23689)
- **ZPRL online phases.** 76 to 159 trajectories per hour (derived from 330 to 1,350 trajectories in 3.5 to 12.5 h). Reset method was not extracted, so this is not a like-for-like autonomy figure. **[read, partial]** — [arXiv](https://arxiv.org/html/2605.19919)
- **Gentle grasping (arXiv 2503.07926, 2025).** 1,500 samples in 25 h (about 60 per hour); labels mostly self-generated, with some manually corrected from visual inspection. **[snippet]** — [arXiv](https://arxiv.org/pdf/2503.07926)
- **RoboClaw (reviewed).** Entangled Action Pairs link a forward policy to an inverse reset policy, with 53.7% reduction in human time and 25% success improvement on long-horizon tasks per the abstract. Agent-judged subtask success, no detail. **[snippet + read, targeted]** — [arXiv](https://arxiv.org/html/2603.11558v3)

#### Labelling guidance
- **No Free Checker (arXiv 2609.09250, Sept 2026 survey of about 150 verifiers).** Frames availability against credibility. Notes that a single agreement rate hides how false positives and negatives split and that RoboReward shows the lowest agreement among compared reward benchmarks. Conformal guarantees can fail under cross-task shift (SAFE). The recommendations I took from it were inferred from the first 42% of the text: report FP and FN separately, fix curation scores before training, and do not treat a geometric quality score as proof of completion. **[read, partial]** — [arXiv](https://arxiv.org/html/2609.09250)

#### Foundational
- **SERL.** Supports reset-free training with forward and backward controllers. **[snippet]** — [arXiv](https://arxiv.org/pdf/2401.16013)

### Inferences
- A rough yield model for Hebbot: at Tether's 75 attempts per hour and Hebbot's current 6.6% success rate, about 5 successes per hour, so 50 successes would take about 10 hours. Reaching 30% success cuts that to about 2.2 hours. Raising grasp success is therefore worth more than optimising cycle time.
- Hebbot's block setup lends itself to Tether's "end state is another start" reset, using pick-and-place between regions. A failed grasp that displaces the block is a useful randomisation, as Tether notes, as long as a workspace check catches ejection (AutoDex's 5.3% ejection rate under passive release is the relevant warning).
- Best labelling design: a physical test (gripper width plus lift-and-hold, as in AutoDex's 5 cm for 3 s) combined with a pre/post-image VLM check. Measure FP and FN by hand on a sample before trusting either; Tether is the only source found with a measured figure, and AutoDex did not measure one.
- Feasibility is good: this needs no GPU beyond existing perception. The VLM label can be remote or local, though Hebbot's local VLM accuracy would need its own measurement.

### Gaps
- I found no 2026 report of autonomous reset-free Franka pick-and-place hours and yield specifically, and no published parallel-jaw self-supervised grasp yield from 2026 to compare with these numbers.
- Label accuracy under Tether's VLM was measured on its own tasks; transfer to small blocks is unknown.

## Statistically sound promotion and gating; deciding when a reflex should replace a deliberate path or be demoted; runtime competence detection

### Takeaway
Anytime-valid sequential tests are now the rigorous default for hardware policy comparison, and partial-credit metrics are the strongest lever at n of about 50. Runtime monitors are well developed for detection but I found no published demotion procedure, so promotion can follow published methods while demotion must be assembled by analogy.

### Cited Findings

#### Sequential promotion (evaluated on real hardware data)
- **N-SCORE (Snyder, Badithela, Matni, Pappas, Majumdar, Itkina, Nishimura; RSS 2026).** Maintains a wealth process that starts at 1 and multiplies by 1 + ξ(r₁ − r₀), with online-tuned ξ in [0,1]; it stops and promotes when the process reaches 1/α, so 20 at α = 0.05. Works for any bounded [0,1] progress metric, assuming i.i.d. trials, and Type-I control is proven. On LBM 1.0 hardware data (N = 50 per policy per task, 5 tasks) partial-credit metrics reached decisions in 12 to 36 trials per policy on 4 of 5 tasks, while binary tests failed to decide on 3 of 5 (BikeRotor, CutApple, CleanLitter). Summed time-to-decision on hardware was 286 for N-SCORE on progress metrics, versus 376 to 426 for binary methods, out of 500 rollouts available (my check: these totals equal twice the per-policy column sum, with undecided tasks counted at N = 50). Reported savings are up to 70% versus batch and up to 50% versus binary sequential methods; the text states 40 to 45% hardware savings and the table caption says 50%. Over 4,500 hardware rollouts used; codebase linked from the paper. **[read, pp. 1–8]** — [RSS 2026 p076](https://www.roboticsproceedings.org/rss22/p076.pdf)
- **N-SCORE Bernoulli results (synthetic).** STEP is best for binary outcomes: mean time-to-decision 95.1 versus 117.9 for N-SCORE-2 over gaps of 10 to 50 points, with power 0.953 and 0.965 at the hardest 10-point alternatives. **[read]** — [RSS 2026 p076](https://www.roboticsproceedings.org/rss22/p076.pdf)
- **AnyRank (Badithela et al., arXiv 2610.14603, Oct 2026).** Ranks many policies by anytime-valid pairwise tests plus graphical multiple-hypothesis correction, with FWER bounded. Priors from simulation or held-out data can improve efficiency without invalidating the guarantee. Up to 50% fewer hardware evaluations reported. Still assumes i.i.d. data. Code: github.com/irom-princeton/multi_hypothesis_test. **[read, partial]** — [arXiv](https://arxiv.org/html/2610.14603)

#### Sequential promotion (simulation)
- **AgenticRobotics (Hang Yu, arXiv 2608.07555, 2 Aug 2026; single author, LIBERO only, 5,475 episodes).** Monte Carlo at a null of 56.5% with a +8-point effect: McNemar with peeking has Type-I 0.161 (about 3 times nominal). E-process Type-I 0.003, power 0.61, mean 920 episodes. Mixture-SPRT Type-I 0.007, power 0.86, mean 700. A fixed min-delta 12-point rule at 100 episodes has power 0.31. Its controller token cost was not retained, which the author calls a design defect. Code in public repo HangYu8123/AgenticRobotics. **[read]** — [arXiv](https://arxiv.org/html/2608.07555)
- **PolicyCanary.** Community release gate using a betting confidence sequence over paired differences; self-reported and unreviewed. **[snippet]** — [GitHub](https://github.com/bbeennyy860-cyber/policycanary)
- **Imperfect-simulator evaluation (arXiv 2510.04354).** Uses simulation as a cheaper prior to reduce hardware trials. **[snippet]** — [arXiv](https://arxiv.org/pdf/2510.04354)

#### Runtime competence
- **PATCH (arXiv 2606.16690, 15 Jun 2026).** Monitors persistent latent "innovation" in the corridor of the active action chunk, filtered for robot self-motion, and needs no failure data. Two Piper arms with ACT policies. Offline TPR 0.97, TNR 0.95 on 400 windows; online 90% intervention signal accuracy and 85% completion over 20 trials. 18.1 ms per frame on an RTX 5090. Project page only. **[read]** — [arXiv](https://arxiv.org/html/2606.16690)
- **FAIL-Detect (arXiv 2503.08558, 2025).** Sequential OOD detection with a time-varying conformal threshold. **[snippet]** — [arXiv](https://arxiv.org/pdf/2503.08558)
- **FIPER (NeurIPS 2025).** Random-network-distillation OOD plus action-chunk entropy, conformal calibration on a small set of successful rollouts. **[snippet]** — [NeurIPS](https://neurips.cc/virtual/2025/poster/119369)
- **FoMo-FD (arXiv 2607.27511) and SAFECAST (arXiv 2608.04246).** Conformal calibration on successes for surgical imitation policies, and contrast-set training for detector robustness under shift. **[snippet]** — [FoMo-FD](https://arxiv.org/pdf/2607.27511), [SAFECAST](https://arxiv.org/pdf/2608.04246)
- **Deployment-Time Reliability of Learned Robot Policies (thesis, arXiv 2603.11400).** Introduces STAC and Sentinel monitors. **[snippet]** — [arXiv](https://arxiv.org/html/2603.11400)
- **Uncertainty-aware policy steering (RSS 2026).** Chooses between executing, asking for clarification, or requesting intervention, with conformal calibration of the verifier. **[snippet]** — [RSS 2026 p142](https://roboticsproceedings.org/rss22/p142.pdf)
- **Confidence-Gated Robot Autonomy (arXiv 2605.18045, 18 May 2026; activity recognition and PyBullet, no physical robot).** Uncertainty gating only helps above a dataset-specific competence level (about 33% to 70% accuracy). Softmax, MC Dropout and ensembles gave nearly identical act/defer decisions, with threshold choice mattering much more. Semantic OOD was at or below chance (AUROC 0.35 to 0.49). A poorly chosen fallback substantially raised cost and collisions. **[read]** — [arXiv](https://arxiv.org/html/2605.18045)

### Inferences
- My calculations for Hebbot. Current 5/76 has a Clopper-Pearson 95% interval of 2.2% to 14.7%. To show 90% or better with one-sided 95% confidence needs 29 consecutive successes, or 14 for 80%. Results of 19/20 and 45/50 have two-sided lower bounds of only 75% and 78%.
- A simplified sequential betting simulation (plug-in fraction, α = 0.05, up to 200 pairs, 20,000 runs; **my own simulation, not N-SCORE**) against a 6.6% baseline gives about 10 paired trials on average for a true 80% reflex, 25 for 50%, 77 for 30%, and 49% power at 20% within 200 pairs. A reflex that moves from 50% to 70% needs about 117 pairs (82% power). It never falsely rejected at the exact null, so it is conservative. Run the real N-SCORE or STEP before adopting these budgets.
- Practical gate: since the deliberate baseline is weak, a learned or frozen skill can clear promotion quickly if it is far better, and the test will correctly refuse marginal gains. If episodes record stages (for example approach, contact, lift, verify; I did not inspect the schema), use them as a pre-registered partial-credit score, because that is N-SCORE's largest measured gain. Run paired, randomised trials with reflex and deliberate alternating, to address the i.i.d. assumption as far as a drifting lab allows.
- Demotion (by analogy, no direct source): run the same test in reverse with a fresh e-process after every promotion, testing whether the reflex is worse than its promotion estimate or the deliberate path. Calibrate a runtime monitor only on successful reflex rollouts from the same distribution, as FIPER and FAIL-Detect do, and tune the threshold before comparing uncertainty measures, following Confidence-Gated Autonomy.
- Fallback routing should test cost, not only safety: a reflex that fails after contact is not equivalent to one that defers before motion. Confidence-Gated Autonomy's fallback result is a warning that a poor fallback can do more harm than the reflex it replaces.
- A 16 GB GPU is adequate for a small monitor. PATCH's 18.1 ms was on a 5090, so on lesser hardware it would need measurement. Feature-reconstruction or RND monitors on an existing policy encoder should be light.

### Gaps
- I found no published procedure for demoting a promoted robot skill, and none combining sequential promotion with runtime monitoring.
- All sequential methods here assume i.i.d. trials. I found no treatment of the non-stationarity from camera drift or gripper wear on a single arm.
- Conformal guarantees under task-distribution shift are unresolved: the survey notes SAFE's guarantee can fail in this case.
- STEP's original paper was seen only via the N-SCORE comparison; its own numbers were not checked.

## Which approaches explicitly measure deliberation-to-reflex compression, such as reduction of LLM calls or cost per task over practice?

### Takeaway
I found no real-robot paper that plots LLM calls or cost per task against practice. The closest evidence is point comparisons: 52.6 calls per episode versus zero for frozen code, and human corrections per trial falling from 4.67 to 0.83 with a reused skill library, so a practice-curve measurement would be novel for Hebbot.

### Cited Findings

#### Real robot
- **Kuroki et al.** Online coding-agent baseline about 52.6 model calls per episode; the frozen generated code makes none at execution. RoboCasa simulation: online agent 51.7% versus validation-selected code around 64.2% on source tasks. Cost is not reported. Physical G1 11/15. **[read]** — [arXiv](https://arxiv.org/html/2609.19906)
- **ARCHITECT.** With a skill library from an earlier task, mean human queries per trial fell from 4.67 to 0.83 (p = 0.036, 6 participants) and Task 2 success rose from 0/6 to 4/6. Counts human corrections rather than LLM calls; API cost not reported. **[read]** — [arXiv](https://arxiv.org/html/2607.23784)
- **FCGraft (arXiv 2606.13097, v2 29 Jul 2026).** Two-tier cache of validated functions with stitching and exception patching, with a local Qwen2.5-Coder-14B on RTX 4090 and a Franka FR3 for the real tests. Policy synthesis latency down 2.88 times versus RAGCache in real-world tests (2.3 times on average); real success 77.78% (office desk) and 81.48% (cooking) versus CaP's 33.33% and 51.85%. LLM call counts and cost not reported in the part I read; code not stated. **[read, partial]** — [arXiv](https://arxiv.org/html/2606.13097)
- **Agent as Policy.** Reports reused procedures shorten execution across repeated trials; no figures on the abstract page. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2609.12541)

#### Simulation or non-robot
- **RHO (O3DE).** Held-out success 23.5% to 44.3%, with 20% less wall-clock time and 27% fewer tool calls. **[read, abstract only]** — [arXiv](https://arxiv.org/abs/2606.16458)
- **SkillDroid (arXiv 2604.14872; phone GUI, not robotics).** Successful LLM traces become parameterised templates; replay reported 100% success over 79 rounds with zero LLM calls in pure replay. **[snippet]** — [arXiv](https://arxiv.org/html/2604.14872)
- **SR-Platform (arXiv 2605.14700; simulation environment generation).** Full cache hits skip LLM generation and cut median latency; latency only, no dollars. **[snippet]** — [arXiv](https://arxiv.org/pdf/2605.14700)
- **AgenticRobotics.** 121.6 GPU-hours total but explicitly no token accounting. **[read]** — [arXiv](https://arxiv.org/html/2608.07555)
- **OpenRUA.** Includes a cost appendix I did not reach; each episode starts fresh, so it cannot show a practice curve. Budget sensitivity: Opus 5 went from 50% to 70% success when turns rose from 100 to 200. **[read, partial]** — [arXiv](https://arxiv.org/html/2610.02459v1)

### Inferences
- Hebbot can report a metric that this literature mostly lacks: per-skill LLM calls, tokens, wall-clock time and success, plotted against episode index and against promotion events. The episode store should make this possible if each episode records its model calls, which I did not check.
- The right unit is cost per verified success, not cost per attempt, since a cheap reflex with a low success rate can cost more overall. Include the human-intervention and reset time as ARCHITECT and AutoDex do.
- FCGraft's local coder on a 4090 suggests that a code cache could reduce cloud cost, but a 14B coder at 16 GB would need a quantised build and its latency would have to be measured.

### Gaps
- No real-robot dollar cost per task over repeated practice was found.
- FCGraft's generated-token results and OpenRUA's cost appendix were in unread portions of long pages.
