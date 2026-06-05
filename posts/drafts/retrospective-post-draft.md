# Seven Months of Trying to Teach a Robot Arm

*Draft 2026-05-30. Substack/blog post #1.*

---

I bought a used Franka Panda arm last winter and put it in my apartment. The plan, roughly, was to teach myself robot manipulation by doing it. I had a vague long-term idea — something about a tiny outdoor robot that could wash a car while you charge it — and a clearer short-term one, which was that I wanted to understand how modern AI systems actually drive physical hardware, not just how they're described in papers.

This is a writeup of what I've tried over the past seven months. Some of it worked. A lot of it didn't. The thing I'm most interested in sharing is not "look at the working demo" — there are better working demos than mine out there — but the specific shape of *what didn't work and what it taught me*, because that's the part that's been hardest to find when I've gone looking for it.

I'll save the framing I'm now operating under for a later post. This one is about the seven months that led to it.

---

## The setup

The hardware:

- **Franka Panda** (7-DOF arm), bought used. Talks to a Linux host over Ethernet via `panda-py` and the libfranka FCI.
- **Intel RealSense D435** as a fixed scene camera looking down at a small worktable.
- **Intel RealSense D405** mounted to the gripper as a wrist camera.
- A Raspberry Pi 5 as the host. (This is going away soon — it's a story for another post.)

The software, very roughly:

- **MCP servers** wrapping the robot, the cameras, the gripper, and a few perception primitives. (`franka_mcp`, `realsense_mcp`, `depth_mcp`, etc.) MCP because at the time it was the cleanest way to expose tools to Claude.
- **Claude** as the planner — it sees a camera frame, decides what to do, calls a skill ("pick the red block"), watches the result, decides again. Slow but flexible.
- A small stack of perception models — initially HSV color detection, later Moondream 2B for detect-by-natural-language.
- A real-time servo layer (`franka_rt`) built later, using ZMQ to decouple `panda-py` (which holds the robot connection) from the MCP process.

The goal was straightforward: get the robot to reliably pick things up under language instruction, and learn what each layer of the stack actually contributes.

---

## What worked

### Analytical IK with collision recovery

The single most reliable component of the system is the parameterized `pick_at(x, y)` skill: take a robot-frame coordinate, compute IK, descend in incremental 4 cm Z steps, grasp, lift. Mean position error across hundreds of picks is on the order of 1 mm. The IK solver itself (`panda-py` exposes `libfranka`'s analytical solutions) is essentially perfect when given a reachable target.

The non-obvious part of making this reliable was **collision recovery**. The default libfranka collision thresholds are conservative — set for industrial safety, not for a hobbyist pressing a gripper against a table. The first weeks of work were full of "cartesian reflex" errors that fired on completely innocuous contact, requiring an e-stop reset. Tuning the thresholds for descent (30 Nm joint / 50 N Cartesian) and restoring them on completion, plus catching `move_to_joint_position` failures and re-issuing with `panda.recover()`, eliminated almost all of these. Up to three recovery attempts per pick is enough for any non-catastrophic case.

### Moondream + ArUco homography for perception

For "where is the red block in the camera image, and where is that in robot coordinates," the working stack ended up being narrower than I expected:

- **Moondream 2B** for detect-by-language: "find the red block." Returns a pixel bounding box. Mean error around 2 pixels. Latency around 2.4 s on a separate inference machine.
- **4 ArUco markers** flat on the table, used to compute a homography from camera pixels to robot frame. After refresh, RMSE around 5.2 mm.
- Combined: sub-millimeter robot-frame accuracy at calibration points, 1–4 mm picks at the workspace edges.

This stack handles roughly 80% of what I want to do. Everything else is a supporting role.

### A real-time servo layer

After the MCP-Claude planning loop kept hitting a frustrating wall on the descent — the planner couldn't react fast enough to fine-grained contact — I built `franka_rt`: a small ZMQ-based server that holds the `panda-py` connection and exposes a streaming control interface. Two-phase descent (fast above 30 mm, slow inside the last 30 mm), collision recovery in a `finally` block, waypoints recomputed from forward kinematics after each recovery. Position accuracy on the order of 1.6–2.6 mm; descents taking 6–10 s end-to-end.

This was meaningful because it let me get *off* of having every motion decision pass through Claude. Anything that needed to be fast had a place to live.

### Online error correction

A small, dumb thing that worked surprisingly well: an exponential moving average of `(target - actual)` position errors across successful picks, applied as a correction to future target positions. Buffer of 10 picks, three-sample minimum before applying, max correction of ±25 mm. The corrections it ends up applying are tiny — typically under 2 mm — because the IK is already accurate. But it's exactly the kind of cheap safety layer that's invisible when calibration is fine and saves you when calibration drifts.

---

## What didn't work, and what it taught me

This is the more interesting list.

### Depth cameras for fine perception

The D435's depth stream is wonderful for understanding the broad shape of a scene and useless for fine localization. At 640×480 with the geometry I had, I could not reliably detect the gripper's fingertips against the depth background — the robot's arm body dominates, the gripper is too thin. I spent a couple of days on background-subtraction approaches to use the robot's known forward kinematics as a calibration target. Best result: 50–120 mm error, against ArUco's 5 mm.

**Lesson:** Use depth for what it's good at (scene overview, finding the table plane, broad reachability checks). Use a 2D detector + a known geometric transform for anything that has to be accurate to the millimeter. Don't try to make the depth camera do both.

### Small VLMs for spatial reasoning

I tried SmolVLM2-500M for "where is the red block in this image" tasks. It scored 0/4 on basic spatial accuracy benchmarks. Distinguishing left from right was at chance.

I also tried teaching a small VLM to directly regress robot-frame coordinates from images — feeding it 134 hand-labeled examples and asking it to output `(x, y)`. The model mode-collapsed to the dataset mean. It learned "predict the average pick location" because that minimized the loss on a small dataset, and there was no geometric inductive bias to push it toward learning the actual mapping.

**Lesson:** Use VLMs for what they're good at (detection, classification, language grounding), and let geometry handle the geometric parts. The chain "image → detector returns pixel box → homography returns robot coordinates" was the thing that actually worked, and it's the chain that's intuitively obvious in retrospect. The earlier attempt to collapse it into one neural net was a category error.

### NUDGE: learned servo from too little data

NUDGE (Neural Unified Direction and Gap Estimation) was my attempt to learn reflexive descent corrections from data — a small CNN that takes a wrist-camera frame plus the current gripper position and outputs a small XY correction. The idea was sound: the wrist camera sees the block clearly during descent, and small position errors should be visible in pixels.

The model trained. The train loss came down nicely. The validation loss did not. The model massively overfit on 216 collected approaches, settling into a constant +2.8 mm X bias regardless of input. In deployment, it produced corrections, but they were not actually conditioned on the visual signal.

**Lesson:** I learned something important about my own intuition for how much data this requires. The Tether paper (ICLR 2026) and others have since clarified the rough scale: 50–100 well-curated demonstrations are enough to *fine-tune* a pre-trained vision-language model; 1000+ trajectories are needed to *train* a learned policy from scratch with good downstream performance. NUDGE was in the wrong regime — too much architecture, too little data, no pretraining. The right move would have been to use the wrist-camera frames to fine-tune a pre-trained VLM's grounding, or to use a non-parametric method like DINOBot. I had built the wrong tool for the job, and it took me a few weeks to admit it.

### Homography for tall objects

Camera-to-robot homography is exact for points on the calibration plane. The moment you try to use it to localize a tall object (a soup can, say) on the table, the parallax error becomes large — for the camera angle I had, on the order of 12 cm.

**Lesson:** Geometric methods come with geometric assumptions, and the assumptions are not negotiable. "Detect the object, then assume it's on the table plane" works for flat blocks. It does not work for cans. The fix is either depth fusion (with all the caveats above) or a wrist camera approach (which is what I went to).

### The IK solver bug I shipped for months

`panda_py` exposes two inverse kinematics functions: `ik()` and `ik_full()`. The difference is that `ik()` returns one solution branch (the "elbow up" configuration); `ik_full()` returns all four. There are workspace regions — particularly near the robot base, at table height — where the only feasible configuration is "shoulder rotated 180 degrees," which `ik()` silently drops.

For weeks, picks at low X near the base were intermittently stalling mid-descent. The IK returned a "valid" solution, the planner accepted it, the robot couldn't actually reach it, the planner concluded it was a collision, recovery triggered, the e-stop fired. I attributed it to many things before identifying the right one: a 10-line wrapper change covering 86% → 99.5% of the workspace at table height, at the cost of about 96 microseconds.

**Lesson:** The most common bug in software-driving-hardware is *silent capability loss*. The system technically works, just not in the cases that matter most. Build observability for the cases where things "succeeded but the result was bad" — those are the cases where a real-world system actually breaks.

### Single-camera viewpoint and environment uniformity

Every successful pick I logged for months came from approximately the same camera angle, against approximately the same dark wood table, under approximately the same lighting. When I finally started reading the open-source manipulation literature carefully (Tether, DROID, ForceVLA, RH20T), the recurring theme was: **environment diversity dominates everything else** in determining whether a learned policy generalizes.

**Lesson:** I had been building a system that would have looked great on the bench and shattered the moment it saw a slightly different scene. The fact that I had collected ~100 episodes was meaningless if all 100 episodes came from the same nine cubic inches of physical space. Recognizing this — and recognizing that I hadn't actually started solving the harder version of the problem — was uncomfortable but useful.

---

## The local minimum

The single most useful sentence I wrote in this notebook came from a frustrated session in early March:

> *We've been polishing Claude as a low-level controller — collision retry, push_at fixes, can stacking — instead of building the neural motor layer that replaces it. The robot works well enough with Claude in the loop, but that's a dead end for autonomy.*

By this point I had a system that picked blocks reliably, recovered from collisions, used natural-language detection, ran across most of the workspace, and could be directed by Claude through a clean MCP interface. All of which was real progress.

And none of which was on the path to anything genuinely interesting.

Every improvement I had been making for the previous month was a refinement of "Claude reasons about every motion." The latency of that reasoning, the round-trip through MCP and back, the absence of any reflexive layer that could respond on the timescale physical interaction actually requires — none of these were going to be fixed by more Claude polish. They were structural.

After that entry I worked for about a week on the wrist camera and the IK fix and a literature survey, and then I stopped. For two months. I told myself I was busy with other projects, and I was, but the deeper reason was that I didn't know what to do next that wouldn't be more of the same.

---

## What came out of stopping

The thing that brought me back was, oddly, articulating clearly what I'd actually been trying to do. I'll write the full version of this in a future post — the short version is that the frame I needed wasn't "robot driven by language model," it was something closer to the cortex-cerebellum architecture of human motor control: a deliberate, slow, cognition-heavy layer that issues intentions, and a fast, reflexive, learned layer that executes them, with feedback flowing both directions and the deliberate layer able to take over again whenever something surprising happens. The piano-player analogy is the version of this that finally clicked for me, and it turned out to have direct correspondences in cognitive science, in the older hierarchical-RL literature, and even in the architecture of well-designed software systems like Claude Code itself.

That framing reorganized what was worth working on. The seven months of work didn't become a sunk cost — they became the lower-level scaffolding (IK, calibration, MCP servers, basic perception) that the upper layer of the architecture needs to exist on top of. The failures stopped feeling like wasted time and started feeling like the part of the work that actually located where the hard problems are.

I'll write that post soon. This one is the foundation it sits on.

---

## What's next

In the immediate term: I'm porting the project to a new host machine, integrating ZED stereo cameras to match the DROID dataset setup, deliberately introducing environment variation (varied lighting, vertical structure, multiple camera angles), and beginning a more serious data-collection push. The IK bug is fixed. The NUDGE artifacts are archived rather than carried forward. The system has been pruned to its actually-load-bearing parts.

In the slightly-less-immediate term: write the substrate-framing post, port the same skill library to a $250 SO-100 arm to test hardware portability, and start exploring what the bidirectional high-low feedback actually looks like in practice.

If you're working on related things — particularly the messy gap between language-model planners and learned motor primitives — I'd love to hear from you.

— Doug
