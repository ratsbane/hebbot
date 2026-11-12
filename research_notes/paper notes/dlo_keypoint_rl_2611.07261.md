# Reading note: An Empirical Study of RL for Keypoint Positioning of Deformable Linear Objects

Rodrigues, Zeynalpour, Solak, Hjorth, Ajoudani — HRII lab, IIT Genoa. arXiv 2611.07261v1, 5 Nov 2026. EU Horizon project TORNADO. Read date 2026-11-11, full text **[read]**. Supplied by Doug as a PDF; code release not stated anywhere in the paper.

## What they did

Planar manipulation of a deformable linear object (rope, cable, catheter) with one end rigidly grasped by a Franka Panda. The policy outputs Cartesian end-effector increments (xy translation + yaw) at 10 Hz over a Cartesian pose controller; physics at 1 kHz; episodes ≤ 200 steps (20 s). Tasks: move the DLO's free tip to a target (single-point), or tip and EE-attached keypoint to two targets (two-point). Model-free PPO throughout; MLPs (actor 2×256, critic 3×256, SiLU).

Three claims, each with evidence:

1. **The simulator, not the learner, is the bottleneck.** They compare three complete training stacks under the same ~240 min wall-clock budget: Stable-Baselines3 + CPU MuJoCo (4 envs), Stable-Baselines JAX + CPU MuJoCo, and MuJoCo Playground (MP: JAX PPO + MJX GPU sim, 1024 envs). Final success rate 0.860 ± 0.108, 0.880 ± 0.110, **0.950 ± 0.032**. MP collects ~13× more experience in the same time. Swapping only the learner to JAX gains little; the win comes from parallelising the physics. Hyperparameters per stack were swept with Optuna using a success-oriented objective: 0.90·mean(final SR) + 0.10·AUC − 0.35·σ(final SR) across seeds — a recipe that explicitly buys cross-seed stability.
2. **Representation matters as much as compute.** Ablation (20M env steps): DLO keypoints *relative to the EE* beat robot-base-frame absolutes, and an *asymmetric actor–critic* (critic sees privileged dense sim state, 12 keypoints; actor sees only the deployable 5-keypoint observation) beats symmetric. Relative + asymmetric wins and is used everywhere after.
3. **Robustness training transfers zero-shot.** Training adds observation noise, ≤ 1-step delay, domain randomisation (table friction U(0.5, 0.8); joint stiffness/damping/passive damping scale U(0.95, 1.05)), and random mid-episode disturbance forces. Bending/torsional stiffness of the DLO itself could NOT be randomised (MP plugin limitation — their sim-gap is acknowledged). Sim results over 250 episodes per condition: single-point 96.4% under noise+DR, disturbance recovery 98.0%, two-point 88.8%. A single policy trained jointly on 30/37.5/45 cm DLOs holds 84.4–86.4% across lengths.

**Real robot, zero-shot** (no fine-tuning, no policy change): Franka Panda, overhead RealSense D435i, ArUco-based extrinsics, ROS, 4 mm silicone catheter. Perception is markerless: YOLO26-seg fine-tuned on a task-specific rope dataset (98.81% mask precision, 29.8 FPS), mask skeletonised, N equally spaced centreline keypoints, depth-projected to the table plane in robot-base frame. Results: single-point **9/10**, disturbance recovery **10/10**, two-point **8/10**; median completion 9.30 s / 6.55 s / 9.05 s.

## Failure structure (the part most worth keeping)

- Sim failure taxonomy: timeouts vs safety terminations, plus task-level Type-1/2/3 (which target was never/temporarily/finally unsatisfied), with 95% Wilson intervals on everything.
- **Failures concentrate at the workspace boundary**: under noise+DR, 78.6% of two-point failures involve a target within 5 cm of the admissible-workspace edge, though such pairs are only 44.4% of episodes; failure rate 19.8% peripheral vs 4.3% interior. Also worse at large target separations (83.1% SR at ≥ 0.20 m vs 92.7% below 0.10 m).
- Real failure modes: perception loss when the robot occludes the markerless keypoints; the stiff catheter storing and releasing elastic energy (sudden deflection); perception latency causing overshoot (mitigated, not removed, by training with delay).

## Relevance to Hebbot

1. **This sharpens the answer to our "learning at tiny data scale" question.** The paper's route is the opposite of learning from tens of real episodes: make 20M sim steps cheap (GPU-parallel MJX), train with noise/delay/DR, transfer zero-shot through a markerless keypoint abstraction. If the frozen 30-run forces a learned-policy test, the credible path on our hardware is MP in simulation on omen's 16 GB GPU (they ran main experiments on a V100 32 GB with 1024 envs; fewer envs fit 16 GB — only the Optuna sweeps used an A100). Big caveat for us: their task is planar and quasi-static with clean overhead perception; last-centimetre *contact* tasks are exactly where MJX fidelity is weakest, and their own inability to randomise DLO stiffness shows the sim-gap discipline required.
2. **Boundary-failure convergence with our bench.** Their failures cluster at the workspace edge; our r7 refusal and u1's force-estimate under-read both happened at the envelope edge. Two completely different architectures (learned reactive policy; classical force-triggered placement) degrade in the same region. Supports: keeping the reset sampler's x ≤ 0.52 ceiling, scoring refusals separately from failures at the freeze, and treating near-boundary attempts as their own stratum when reading the 30-run.
3. **Evaluation methodology to borrow.** Wilson intervals and a failure taxonomy per attempt. At n=30 the interval is wide (25/30 → roughly 0.66–0.93), which is worth stating in the freeze protocol rather than discovering afterwards.
4. **If we ever distil touch_stack into a policy**: observations relative to the EE (we already have this frame — the camera map's b-offset), asymmetric actor–critic with privileged sim state, and train-time noise+delay matching our measured probe noise and camera latency.
5. **Perception.** A fixed overhead camera with ArUco extrinsics and a fine-tuned segmentation model at 30 FPS is hobbyist-feasible and is their whole transfer story. Supports our planned scene-camera attachment, and is one concrete shape for the "more generic segmentation" need (red invisible to colour-blob logic). Their robot-occlusion failures are the known cost of overhead viewing.
6. **A boundary of our scene-memory model, now explicit.** A DLO cannot be a belief of the form (xy, yaw, top_z): it is N keypoints with per-keypoint uncertainty. Our store would represent a rope either as one vague blob or not at all. Not a v1 concern — no deformables on the bench — but recorded: the object table assumes rigid bodies.
7. **Speed vs verification.** Their reactive policy completes in ~9 s; our deliberate probing takes 84–125 s. But their policy never verifies its own success — success is scored externally. Hebbot's touch-verified success is a differentiator that survives even a future learned layer: wrap policy rollouts in the same verify-by-probe, and the promotion gates still work.

## Derived / caveats

- "13× more experience" and env counts are the paper's numbers; SR values from their Tables II–IV, failure percentages from §IV-C/D prose. All **[read]**.
- Wilson-interval arithmetic for n=30 is mine, not the paper's.
- YOLO26-seg and MuJoCo Playground are the artefacts worth a later look; the paper states no code release of its own.
