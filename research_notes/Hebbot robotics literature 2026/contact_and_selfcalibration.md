# Contact-based precision manipulation and hand-eye self-calibration for a single Franka Panda (joint-torque/estimated-wrench only)

Searched 2026-11-09. Verification tags: **[full]** = numbers read from the paper's full text (arXiv HTML or PDF); **[abs]** = abstract/listing page only; **[snippet]** = seen only in a search result, paper not opened, treat as unverified. Dates are the arXiv stamp seen, which may be a revision date. Unless a venue is given, the paper is an unreviewed arXiv preprint. Text under "Inferences" is my reasoning, not a sourced result.

## Q1. Methods using only joint torque / estimated external wrench for contact localisation, insertion, placement, stacking, tight-tolerance tasks

### Takeaway
Joint-torque-only policies are real and do better than vision-only (FR3: 82% vs 30%; a 6-DoF arm with an estimated wrench: 77-83% vs 27-47%), but I found none that reports sub-millimetre insertion or block stacking on joint torque alone. Every result at 0.1 mm clearance or tighter uses fingertip or wrist force sensing, and whole-arm contact localisation from proprioception is only centimetre-grade.

### Cited Findings
**Joint-torque or built-in-wrench policies**

| Work | Hardware / force source | Tasks, data | Results | Code |
|---|---|---|---|---|
| [Learning When to See and When to Feel](https://arxiv.org/html/2604.01414), Lei et al., v2 stamped 9 Aug 2026 [full] | Franka Research 3; 7-joint external torque, 10-step history; no wrist F/T or tactile | Bottle placement by weight, connector pull-out, lid opening; no insertion. 110/250/150 demos; 20/10/20 trials | Bottle/connector/lid: vision-only 8/20, 0/10, 7/20 (30.0%); feature concatenation 30.0%; torque gating 68.0%; proposed 16/20, 7/10, 18/20 (82.0%) | [Yes](https://arxiv.org/html/2604.01414) (GitHub RollingOat/vision-torque-fusion-for-contact-aware-manipulation) |
| [FILIC](https://arxiv.org/html/2509.17053), Ge et al., v2 stamped 19 Jun 2026 [full] | 6-DoF AIRBOT Play for the force-estimation test; real-policy arm not named in what I read. Wrench = J^+T(tau_obs - tau_inv), tau_inv from a synchronised MuJoCo twin | Real: plug adapter, e-stop, C-clamp, bottle cap; 30 demos per task, 30 trials; ~2 cm lateral start offset; real tolerances not given | Position only / +joint torque / +estimated EE force. Plug 46.7/63.3/80.0; e-stop 43.3/60.0/83.3; clamp 30.0/63.3/76.6; cap 26.6/70.0/80.0 | [Yes](https://github.com/OpenGHz/mujoco-wrench-estimator.git) |
| [FACTR 2](https://arxiv.org/html/2606.12406), Oh et al., v2 stamped 12 Aug 2026 [full] | Franka as reference arm; policies on bimanual AgileX Piper. LSTM predicts free-space torque; external = measured minus predicted | LEGO, NIST belt, NIST insertion, tool clean-up, cap screwing; 250 demos, 20 rollouts; main results are task progress; Table 9 reports success rates | Without vs with external torque (Table 9). ACT belt 0.220 vs 0.347 no upsampling, 0.283 vs 0.770 PC+C upsampling; ACT tool 0.763 vs 0.788 PC+C. Flow matching belt 0.241 vs 0.649 PC+C | Stated at [project page](https://jasonjzliu.com/factr2) |
| [LeCo (Learning to Leverage Compliance)](https://arxiv.org/html/2609.31439v1), Wang et al., 25 Sep 2026 [full] | Flexiv Rizon 10s robot wrench interface; no separate F/T described | Four connector insertions; 40 demos per task; 20-30 trials; clearance not given | LeCo 94/100; HIL-SERL 66/100 and ConRFT 78/100 (sums are mine) | None mentioned ([project](https://rtnow.github.io/leco/)) |

**Force estimation quality on a Franka**
- FACTR 2's learned estimator against the Franka factory external-torque signal in contact: L1 0.547 ± 0.348 Nm, vs disturbance observer 1.471 ± 0.761 and FILIC 4.395 ± 1.531. In free space (truth = 0) the estimator gives 0.414 ± 0.278 Nm and the Franka's own external signal 0.449 ± 0.208 Nm. Estimator training takes ~1 min on an RTX 3090; inference 1.76 ms on one CPU thread — [FACTR 2](https://arxiv.org/html/2606.12406) [full]. An aggregator repeats the table — [SOTA2](https://www.sota2.com/research/sota/external-joint-torque-estimation-on-franka-contact-setting).
- The Franka factory estimate is the *ground truth* in that comparison, so the paper shows no improvement over it — [FACTR 2](https://arxiv.org/html/2606.12406) [full].
- FR3 exposes external force estimates and contact information at 1 kHz — [Franka brochure, Feb 2026](https://franka.de/hubfs/Brochure_Franka%20Research%203_February%202026_English.pdf) [snippet].
- libfranka reports the wrench in the stiffness frame, and a user could not reconstruct it from joint torques and the Jacobian — [libfranka issue #112](https://github.com/frankaemika/libfranka/issues/112) [snippet].
- A hobby project reports a fixed 1 N contact threshold misfiring in free air on a drifting signal, fixed by tracking the free-space baseline and requiring a sustained rise — [franka-force-control](https://github.com/sathvik-lokesh/franka-force-control) [snippet; not peer reviewed; partly simulation].
- CoPRE: a transformer trained on contact-free recordings predicts nominal torque, and contact needs 3 consecutive samples over threshold. F90 sensitivity 3.5 N on ARX L5, 5.5 N on Unitree G1; ARX recall 74.1%, false-alarm time 3.32%. Not tested on Franka — [CoPRE, 23 Sep 2026](https://arxiv.org/html/2609.27381) [full].

**Contact localisation from proprioception**
- UniTac localises a touch on the robot body to about 8.0 cm on Franka and 7.2 cm on Spot, using joint torques and positions with no tactile hardware — [secondary summary](https://pith.science/paper/2507.07980) [snippet].
- A learned method without joint torque sensing reports 3.0 cm mean error on a Franka in motion — [arXiv 2011.03142](https://arxiv.org/pdf/2011.03142v2) [snippet].
- SCOPE jointly estimates contact location and object pose from proprioception on Franka arms, but for a *grasped* object touching something — [arXiv 2206.01245](https://arxiv.org/pdf/2206.01245) [snippet].
- A simulated Franka study benchmarks differentiable contact features against particle filters and finds the particle filter worst, partly from per-particle optimisation cost — [arXiv 2305.16778](https://arxiv.org/pdf/2305.16778) [snippet].
- The Contact Particle Filter is the foundational joint-torque contact localiser, shown on a simulated humanoid — [Manuelli and Tedrake](https://www.researchgate.net/publication/311754859_Localizing_external_contact_using_proprioceptive_sensors_The_Contact_Particle_Filter) [snippet].

**Tight tolerance, with better sensors than yours (bounds on what is possible)**
- InsertAnything: Franka, PaXini fingertip 3-axis force, impedance control, sim-trained PPO, hole pose from hand-guided calibration (no vision). At 0.02 mm nominal clearance, force vs pose-only, 20 trials each: circular 20/20 vs 15/20, hexagon 20/20 vs 10/20, triangle 17/20 vs 14/20, L-shape 18/20 vs 6/20. Open scripts and checkpoints — [paper](https://arxiv.org/html/2609.24511v1), [project](https://mzhsoul.github.io/InsertAnything/), 21 Sep 2026 [full].
- From Reach to Insert: Franka Panda, Paxini 6-axis gripper. Without vs with tactile, 75 trials per clearance: 1.5 mm 74 vs 73; 0.25 mm 54 vs 70 (72% vs 93%); 0.05 mm 39 vs 50 (52% vs 67%). Pure imitation scored 0/15 on every geometry at 0.05 mm. RTX 5090; no code mentioned. The intro says 70% where the abstract says 67% — [paper](https://arxiv.org/html/2605.04649v1), 6 May 2026 [full].

### Inferences
- FILIC and the FR3 fusion paper agree on a useful ordering: a wrench expressed at the end effector beats raw joint torques as an input (FILIC's third column vs second), and how force is fused matters as much as having it (feature concatenation stayed at 30.0% on FR3).
- The free-space noise of about 0.45 Nm (FACTR 2) divided by a ~0.5 m lever arm suggests force noise on the order of 1 N. That is rough arithmetic on an L1 figure, not a measured Cartesian floor; measure yours at the probing pose.
- Your 0.1 mm result is z-axis *repeatability* against a stiff table with a known contact point. It does not establish lateral accuracy, or accuracy when the contact point on the finger is uncertain. Nothing I found shows equivalent lateral precision from joint torque alone.
- For a Franka owner the factory external torque signal already serves as the reference, so training NEXT is probably not worthwhile unless your own free-space baseline is worse than FACTR 2's figures.

### Gaps
- No paper found on Franka joint-torque-only block stacking, or on insertion with a stated sub-mm clearance using joint torque alone. FILIC's sim peg has 0.5 mm total clearance (0.95 cm peg, 1 cm square hole), and its real tolerances are unstated.
- [IMPACT (2606.10818)](https://arxiv.org/pdf/2606.10818) and [NeuralActuator (2607.11734)](https://arxiv.org/pdf/2607.11734) surfaced in search and look relevant to torque-residual estimation; neither was opened.
- Stacking-specific contact work: a dedicated search returned only marketing claims ([Franka applications page](https://franka.de/applications)) and hobby code.

## Q2. Force-aware or contact-aware policies that work with few demonstrations

### Takeaway
"Few" really means 7-50 demonstrations in the credible work: Residual Compliant Policy (7 total, FR3), FILIC (30 per task), LeCo (40 per task), Force Policy (50 per task, RSS 2026). Residual or correction layers on top of a stiff base seem to be the sample-efficient pattern, while VLA-based stacks need far more compute than a 16 GB GPU.

### Cited Findings
- **Residual Compliant Policy**: FR3, digital twin during data collection, 7 demos total across three flipping/pushing tasks. Out-of-distribution, 40 trials: full method 40/40, base policy without residual RL 35/40, baselines 33/40 and 24/40. Residual RL adjusts an admittance controller's reference. Force source not stated on the page; code not confirmed — [arXiv 2610.14035](https://arxiv.org/abs/2610.14035), 1 Oct 2026 [abs], [project](https://residual-compliant-policy.github.io).
- **Force Policy**, RSS 2026: Flexiv Rizon 4, flange 6-DoF F/T, 50 demos per task, 10-20 trials. Plug in EV charger (final stage): RISE-2 0%, pi0.5 0%, RDP 5%, FoAR 10%, ForceVLA 0%, TA-VLA 0%, Force Policy 65% (plug-in stage counts partial completion as half a success). Scrape sticker (hard) full-off: 10/20/20/20/0/10 vs 90%. Non-pi0.5 policies ran on an RTX 3090; pi0.5 needed an A800 "due to memory limitations" — [RSS 2026 p128](https://www.roboticsproceedings.org/rss22/p128.pdf) [full]. I did not confirm a code release.
- **FILIC**: 30 real demos per task, with 10 contact-free and 20 corrective for the adapter — [arXiv 2509.17053](https://arxiv.org/html/2509.17053) [full].
- **LeCo**: 40 demos per task plus SpaceMouse corrections; RTX 4090 learning server, Jetson AGX execution — [arXiv 2609.31439](https://arxiv.org/html/2609.31439v1) [full].
- **Compliant Residual DAgger**: turns a position-based policy force-aware with "minimal correction data"; 64% improvement in base-policy success across book flipping, belt assembly, cable routing, gear insertion — [arXiv 2506.16685](https://arxiv.org/abs/2506.16685), June 2025, v5 Dec 2025 [abs]. Foundational.
- **Wrench-ACT** (predicts wrenches for a pure force controller): UR5e, Schunk F/T, 200-300 demos per task, 50 rollouts. Peg 74% vs 6% position ACT, fan 58% vs 8%, connector 80% vs 12%, fuse 88% vs 96%, pen 84% vs 78%. Average 76.8% vs 40%. The arms differ by data source: bilateral wrench demos against VR position demos, so action space and teleop interface are confounded — [arXiv 2609.37552](https://arxiv.org/html/2609.37552v1), 29 Sep 2026 [full]. Dataset promised, code not mentioned.
- **FOMO-Dex**: dexterous hand, force modulates nominal actions, 50 demos per setting; lower relevance — [arXiv 2610.17161](https://arxiv.org/html/2610.17161v1) [full].
- Snippet-only, not opened: [Constraint-Decomposed Impedance Control](https://erickun0125.github.io/constraint-decomposed-impedance/) (listed as CoRL 2026 Spotlight; results seen were simulation), [ForceRFT 2609.22840](https://arxiv.org/pdf/2609.22840), [Master Micro Residual Correction 2603.15152](https://arxiv.org/pdf/2603.15152), [FACTR 2502.17432](https://arxiv.org/pdf/2502.17432), [ManipForce 2509.19047](https://arxiv.org/html/2509.19047). [PhaForce 2603.08342](https://arxiv.org/html/2603.08342) reports 86% average on real tasks, about 40 points over baselines [snippet].
- Compute for the VLA route: LIFT trains on one 8-GPU A800 node ([arXiv 2607.14236](https://arxiv.org/html/2607.14236) [full]); FAWAM uses 8 A100s ([arXiv 2606.08555](https://arxiv.org/html/2606.08555) [full]); FACTR 2 behaviour cloning used an A6000 (48 GB) for 12-24 hours ([arXiv 2606.12406](https://arxiv.org/html/2606.12406) [full]).

### Inferences
- Best fit for one Franka and 16 GB: a small ACT-class or fusion policy (FILIC pattern, or the released FR3 code with fewer demos), or better, a residual layer on your existing scripted motion, with admittance tracking and the factory wrench as input. RCP's 7-demo result suggests this is the sample-efficient route, but its force source is unconfirmed and its tasks are flipping and pushing, not stacking.
- pi0.5-based force stacks are not realistic locally: Force Policy's authors moved pi0.5 off a 24 GB RTX 3090 for memory reasons. Rented training might work; local inference on 16 GB would need testing.

### Gaps
- No paper tested few-demo force policies specifically on block stacking.
- Whether RCP uses joint torque or a wrist sensor is unresolved from the page I read.
- Demo count sensitivity (does performance hold at 10 vs 50?) is rarely reported.

## Q3. Active contact search efficiency: information gain, Bayesian search, coarse-to-fine, and how 121 probes could become few

### Takeaway
With a known object model and a Bayesian belief, published systems reach their pose thresholds in about 5-9 contacts, not 121 (F/T-only 8.6 ± 4.5 on real replay data), but they use wrist F/T, and residual error stays in the 3-6 mm range. Free-space ("no contact here") evidence was the largest single gain in the best-measured study.

### Cited Findings
- **Bayesian Active Object Recognition and 6D Pose** (Franka Panda, Bota wrist F/T, GelSight Mini, free-space constraints; particle filter over class and pose; 11 YCB objects, 5 trials each, 55 per condition, 15 cycles). Real action cycles to threshold: class FT-only 6.6 ± 4.7 vs full 4.2 ± 2.2; pose (ADD-S < 6 mm) FT-only 8.6 ± 4.5 vs full 5.3 ± 1.7. Final real ADD-S: FT-only 6.1 ± 8.6 mm, full 2.9 ± 0.8 mm. Free-space information gave the largest improvement and GelSight added only modest gains over F/T. Real FT-only ablations were replays of recorded data. Next touch is *not* information gain: it maximises the minimum distance to prior contacts and unreachable targets. Code promised, not released — [arXiv 2603.21410](https://arxiv.org/html/2603.21410), 22 Mar 2026 [full].
- **BayesContact** (KUKA iiwa14, ATI F/T, wrist depth; guarded vertical dips stopped at a force threshold or max displacement). Next probe by information gain, using one-step expected posterior entropy over a finite candidate set. Converged ADD-S real: rectangle 6.12 to 4.04 mm, rectangle-teeth 6.58 to 4.21 mm against the vision-only comparator; insertion 3/10 to 5/10 and 3/10 to 6/10; sim ADD-S reduced 35.8%. Probe counts appear only in a figure I could not read. No code mentioned — [arXiv 2607.16123](https://arxiv.org/html/2607.16123), v2 28 Jul 2026 [full].
- FORCA generated programs probe a surface, update estimated height or grasp position from robot pose at contact plus tool geometry, and use that later in the same rollout; drawer used 3 probe locations — [arXiv 2610.31841](https://arxiv.org/html/2610.31841v1), 22 Oct 2026 [full].
- Blind force search is criticised as needing extensive time and distance, and is "not efficient especially when the search area is large and the search dimension is high" — seen in peg-in-hole search results, for example [Search strategies for peg-in-hole](https://www.researchgate.net/publication/3930400_Search_strategies_for_peg-in-hole_assemblies_with_position_uncertainty) and [Contact Pose Identification](https://arxiv.org/pdf/2101.12467) [snippet; quote not attributable to one paper].
- A 2024 precursor runs a particle filter over class and pose with GP implicit surfaces, simulation only — [arXiv 2409.06912](https://arxiv.org/abs/2409.06912) [snippet].

### Inferences
- Your time budget: 405 s / 121 = 3.35 s per probe. Ten probes is about 34 s, a 12-fold saving, before any belief-update compute.
- Information bound: 121 cells is log2(121) = 6.9 bits, but a point probe on a small object rarely hits early, so each probe yields far less than 1 bit. Prior width and object footprint dominate. Two levers: (1) shrink the prior (even a bad camera estimate, corrected for the rotation-and-scale error you found, reduces the cell count); (2) grid at about one block width so a hit comes in N/k probes (k = cells the block covers), then bisect edges.
- Edge bisection arithmetic (illustrative, block width assumed 25 mm, 1 mm target): log2(25) is about 5 probes per edge. With known block width two orthogonal edges suffice, so about 10 probes after first hit. Expect a total of about 15-25 rather than 121, without any learned model.
- Keep the misses. Every descent that reached the table without contact is a free-space observation, and that was the biggest gain in 2603.21410. A simple grid-belief update that uses misses costs almost nothing.
- Information-gain selection (BayesContact) is a further refinement over a grid belief, implementable in a few dozen lines for a known-footprint block. The entropy step is one-step lookahead over finite candidates.
- Lateral sweeps would give a continuous coordinate per contact but need caution: a 100 g block at friction 0.4 resists only about 0.4 N, below the ~1 N noise estimated in Q1, so it would be pushed before detection. Vertical dips are the safe choice for light blocks; sweeps suit fixed features.

### Gaps
- No 2026 paper found doing information-gain probing with joint-torque-only sensing on a free object; both good results use wrist F/T.
- I did not inspect the current touch_find code. The commit log says it already searches coarse-to-fine, so the estimates above should be checked against its actual probe distribution.
- Tilted-probe and sequential-contact hole search appeared in peg-in-hole snippets but could not be tied to specific measured savings.

## Q4. Hand-eye self-calibration without fiducials; online drift and mechanical-change detection

### Takeaway
Targetless hand-eye methods are now around 2-13 mm and 1-1.5 degrees: good enough to catch your 76 mm and 14-degree errors, not good enough to replace touch for the last centimetre. I found no peer-reviewed or preprint method for detecting a loose or moved wrist camera from a residual time series; the closest are older online camera-laser monitors and small engineering checks.

### Cited Findings
| Work | Setup | Accuracy | Notes |
|---|---|---|---|
| [AnchorHEC](https://arxiv.org/html/2611.02884), KAIST, 2 Nov 2026 [full] | RGB + FK + known intrinsics, 3D foundation models (VGGT default), RoMa v2; physical xArm6 with Azure Kinect, Franka only in sim | Physical wrist camera, 15 poses: room 1.35 cm / 1.48° (JCR 10.16 cm / 4.13°); tabletop 1.22 cm / 1.11° (2.41 cm / 1.83°) | No fiducials except ChArUco for reference. Bundle adjustment 1-2 min on one CPU core; matching 12-19 s per camera on an unspecified GPU. Needs rigid mount and static textured scene. "Calibration after independent wrist-camera remounting remains untested." Code not stated ([project](https://hwanggb.github.io/AnchorHEC)) |
| [SyncHeC](https://arxiv.org/abs/2611.02999), single author, 2 Nov 2026 [abs] | Mask contours, metric depth, one 6.5 s trajectory; also estimates latency | 0.21 cm vs marker board; 0.34 cm touch test; latency error 2.7 ms | Claims +8.1 points policy success (1,800 matched pairs, 95% CI 6.3-9.9) from better calibration. Treat as unreviewed; no code stated |
| [OmniCalib](https://www.alphaxiv.org/de/abs/2609.19582), 18 Sep 2026 [abs-level page] | AGIBOT A3 humanoid, depth ICP, chest camera observes hand | CAD corrections left wrist 10.56 mm / 1.74°, right 6.33 mm / 1.25°; ICP residual 2.09 mm | Corrections are not errors. No drift-detection claim; one robot only |
| [Kalib](https://arxiv.org/html/2408.10562v1) 2024 [snippet] | Keypoint tracking + PnP on a robot reference point | About 0.3 cm, 0.4° | Foundational |
| [Sensors 2024 flange method](https://doi.org/10.3390/s24041071) [snippet] | Flange geometry, eye-to-hand with a 3D scanner | Submillimetre claim, as few as 4 non-coplanar points | Different sensor class |

Also seen, not opened: [Hydra 2504.20584](https://arxiv.org/pdf/2504.20584) (robot mesh to point cloud), [PlaneHEC 2507.19851](https://arxiv.org/pdf/2507.19851) (plane-based), [Continuous hand-eye calibration 2004.12611](https://arxiv.org/pdf/2004.12611), [EKF eye-in-hand 2211.06881](https://arxiv.org/pdf/2211.06881) [snippet; uses feature markers].

**Drift and change detection**
- A probabilistic monitor that flags sudden miscalibration in a fraction of the frames offline methods need, paired with an online tracker for gradual drift, for camera-laser — [Automatic Online Calibration of Cameras and Lasers](https://www.roboticsproceedings.org/rss09/p29.pdf) [snippet]. One search summary dated it 2009 and another 2013; the URL's "rss09" is the proceedings volume number, and I did not open the paper to settle it.
- Calibration errors often appear only in downstream task performance; a learned generative model compares predicted image features with observed ones — [DURableVS 2202.03697](https://arxiv.org/pdf/2202.03697) [snippet].
- Miscalibration does not show up as a sensor-reading offset or drift, which weakens raw thresholds — [Learning Camera Miscalibration Detection 2005.11711](https://arxiv.org/pdf/2005.11711) [snippet].
- Engineering examples, not studies: runtime drift monitor fitting depth against FK geometry, treating occluded views as inconclusive — [cascade PR #299](https://github.com/johnnynunez/cascade/pull/299) [snippet]; fixed-marker reprojection check at 3 px whose author notes a single marker misses some rotations — [dobot PR #249](https://github.com/shc0507/dobot_mg400/pull/249) [snippet].
- Patents propose comparing a new calibration with the initial one and alerting past a set deviation or rate — [USPTO 10576636](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10576636) [snippet].
- ViSP treats post-fit mean rotation, translation and global residuals as the primary quality check — [ViSP tutorial](https://visp-doc.inria.fr/doxygen/visp-daily/tutorial-calibration-extrinsic-eye-in-hand.html) [snippet].
- I found no 2026 paper that closes the loop between touch points and camera extrinsics; SyncHeC's touch test is an evaluation, not an input.

### Inferences
- Sensitivity check: 1.5° at 300 mm working distance is about 8 mm laterally, so AnchorHEC-class accuracy would flag a 76 mm or 14° error easily, but a monitor built on it would not see 2-3 mm of slip.
- Touch is an unused calibration source. Each successful probe gives a base-frame 3D point from FK plus tool geometry, which is also visible in earlier wrist views. Logging (pixel, depth, arm pose, touched point) yields a hand-eye residual series without fiducials. Design suggestion; no paper validates it.
- Your table probe is a free reference plane: compare the depth-camera plane against the touched plane at each session. One view constrains only tilt and height (3 of 6 degrees of freedom); varying arm pose adds more. PlaneHEC is the nearest literature, unread.
- Loose bracket test: a fixed misalignment gives a constant transform; a loose mount sags with gravity, so residuals should correlate with wrist orientation and stiffen after motion. Fitting on disjoint subsets of poses and comparing the estimates exposes non-rigidity that a single refit silently absorbs, which fits your 14-degree episode.
- Monitoring recipe: an always-on residual, CUSUM or Bayesian change-point on it, and an "inconclusive" state for occluded views (cascade's design). No source confirms this combination in a manipulation stack; treat as design.

### Gaps
- No published detection of mechanical looseness from residual time series was found. Detection delay and false-alarm rates for wrist cameras are unknown.
- AnchorHEC and SyncHeC have no confirmed public code, so reproduction on a D4xx RealSense is untested. Fit of VGGT and RoMa v2 on a 16 GB GPU was not verified.
- SyncHeC's eye-in-hand evaluation details (how the robot body is observed from the wrist) were not visible from the abstract.

## Q5. Direct tests of whether force/contact closes the precision gap for vision-only policies or LLM-commanded arms

### Takeaway
Yes on average, with gains of roughly 15-55 points on contact-rich tasks, but the sharpest result is that naive force augmentation can add nothing (FR3, 40 rollouts per task: pi0.5 39.0%, ForceVLA 40.5%, TA-VLA 37.5%, FACT 66.0%). The only LLM-commanded test found (FORCA) shows large gains from force and contact probing, with n = 10 per task and wrist F/T on a UR5e.

### Cited Findings
| Work | Hardware / force | Comparison | Trials |
|---|---|---|---|
| [Demystifying VLA failures (FACT)](https://arxiv.org/html/2608.01402), Stanford, v2 16 Oct 2026 [full] | FR3, Bota SensONE wrist F/T, 100 demos per task | pi0.5 39.0% (plug 30, USB 37.5, button 12.5, board 100, key 15); ForceVLA 40.5%; TA-VLA 37.5%; FACT 66.0% | 40 rollouts/task; ~2,500 total |
| [FAWAM](https://arxiv.org/html/2606.08555), v3 27 Oct 2026 [full] | FR3, ATI Axia80 wrist F/T, 90 demos + 20 interventions | pi0.5 47.5%, GE-Act 48.75%; ForceVLA and TA-VLA 63.75%; FAWAM 85.0% | 20/task, 4 tasks |
| [FR3 vision-torque fusion](https://arxiv.org/html/2604.01414) [full] | FR3 joint external torque | Vision-only 30.0%; concatenation 30.0%; proposed 82.0% | 50 per method |
| [FILIC](https://arxiv.org/html/2509.17053) [full] | Estimated wrench | Position only 26.6-46.7%; estimated force 76.6-83.3% | 30 per condition |
| [Force Policy, RSS 2026](https://www.roboticsproceedings.org/rss22/p128.pdf) [full] | Flexiv flange F/T | EV plug final stage: RISE-2 and pi0.5 0%, Force Policy 65%; note RISE-2 was 100% at contact and 90% at match | 10-20 |
| [InsertAnything](https://arxiv.org/html/2609.24511v1) [full] | Franka fingertip force | Biased hexagon (0-3 mm): force 38/45 (84.4%) vs pose-only 31/45 (68.9%); at 2-3 mm bias 11/15 vs 7/15; sim overall 91.9% vs 71.5%; force zeroed on L-shape 75.3% to 40.6% | 45 real |
| [LIFT](https://arxiv.org/html/2607.14236), v3 12 Oct 2026 [full] | Flexiv built-in 6D EE F/T | pi0.5 with DAgger vs LIFT, peak scores: book insertion 0.4 vs 0.6; Hanoi ring placement 0.3 vs 0.6 | 3×10 per checkpoint |
| [From Reach to Insert](https://arxiv.org/html/2605.04649v1) [full] | Paxini tactile | 0.25 mm: 72% vs 93%; 0.05 mm: 52% vs 67%, without vs with tactile | 75 per clearance |
| [FAMIL, ScienceDirect](https://www.sciencedirect.com/science/article/pii/S2667379726000641) [snippet] | Not read | Whiteboard erasing: ACT vision-only 38% vs 96% | Not read |

**LLM-commanded**
- FORCA (coding agent, "Claude Fable 5", UR5e, WSG-50, fixed D455, wrist F/T at 100 Hz): force-aware vs vision-only variant of the same agent, 10 trials. Drawer 100% vs 40%; door handle 100% vs 10%; vase wipe marker removal 99% vs 73% static, 93% vs 21% hand-held; cucumber peel 81.1% vs 37.4%, contact loss 2.2% vs 40.6%. Generated programs probe surfaces and correct geometry within the rollout. No code stated; no non-force coding-agent baseline besides the vision-only variant — [arXiv 2610.31841](https://arxiv.org/html/2610.31841v1) [full].
- Stanford attributes VLA precision failures to the flow noise schedule giving few samples at low-noise timesteps (tau < 0.2), and force failures to force being sparse and needing state-dependent modulation that concatenation does not provide — [arXiv 2608.01402](https://arxiv.org/html/2608.01402) [full]. So force alone is not a fix for the precision part of the gap.

### Inferences
- The pattern across Force Policy and FACT: vision gets to contact, force decides the last stage. This matches your history (touch_find grasping without hand-eye calibration). Force Policy's RISE-2 row (100% contact, 90% match, 0% plug in) is the clearest single illustration.
- The "gap" measured is contact-rich success, not millimetres. Only InsertAnything and From Reach to Insert measure against stated clearances, and neither uses joint torque.
- Stacking failed on explicit-geometry bookkeeping, and FORCA's within-rollout probe-and-update pattern is the closest published analogue: treat every contact as a measurement that updates the geometry used downstream, rather than trusting a static model. Candidate use for you: probe the top of the base block and the held block's bottom relative to the fingers before release. Hanoi ring placement in LIFT (0.3 to 0.6) is the nearest task analogue, with a non-trivial residual failure rate even with force.
- Cross-paper comparison is unsafe: baselines, sensors, demo counts and task sets all differ. Sample sizes of 10-20 per task leave wide intervals.

### Gaps
- No result found that directly compares a VLM/LLM arm with joint-torque-only force feedback against the same arm without it.
- FORCA's gains may not transfer to joint-torque estimates, because its sensor is a wrist F/T.
- How much of FACT's 27-point gain comes from its precision fix versus its force fusion was not extracted.
- CoRL 2026 proceedings were not systematically filtered. [alphaXiv lists 687 accepted papers](https://www.alphaxiv.org/conferences/corl/2026), and the [RAI Institute round-up](https://rai-inst.com/resources/blog/corl-2026-publication-and-workshop-round-up/) says force-aware residual control mitigates a trade-off but no single controller wins under flow-matching policies (paper not identified). Not systematically covered: ICRA/IROS 2026 proceedings. Most items here are unreviewed arXiv preprints.
