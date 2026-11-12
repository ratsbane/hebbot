# Robotics ecosystem status for a hobbyist open-source robot-learning project (as of 2026-11-09)

Method note: every date below comes from a page fetched or a repository queried on 2026-11-09, unless marked otherwise. "PASSED" means the date is before 2026-11-09. Labels: **[primary]** is the organiser's or legislator's own page; **[secondary]** is a tracker, blog or community post. EUR-Lex's web front end returned empty responses to automated requests, so the legal text was retrieved from the EU Publications Office, which serves the same Official Journal documents. Those URLs need an `Accept: application/xhtml+xml` header and may show an error in a plain browser.

## Model Hardware Standard: updates since 2026-08-27, and adjacent standards

### Takeaway
I found no change to MHS since the 27 August preview: it is still application-gated, with no public specification, repository, version, tag syntax or open-source date. Meanwhile an unofficial ecosystem has formed around it, and MCP robot servers are already usable on both ROS and the SO-101.

### Cited Findings
**Anthropic's status [primary]**
- MHS was published as a research preview on 2026-08-27 and began as a collaboration with HHMI Janelia. It is a standardised driver using read and write commands, and devices are discoverable in a standard format — [Anthropic](https://www.anthropic.com/news/model-hardware-standard-research-preview)
- Users describe a device in natural language, directly or by answering an agent's questions. The driver turns this into a reference file covering what can be measured, what can be adjusted, and the safety limits it enforces. The announcement does not document any tag syntax — [Anthropic](https://www.anthropic.com/news/model-hardware-standard-research-preview)
- Agents can reach devices through MCP, a CLI, or code files/APIs. Code files let long or fast operations run without the agent reasoning at each step. Hardware needs a programmable interface — [Anthropic](https://www.anthropic.com/news/model-hardware-standard-research-preview)
- Anthropic plans to open-source MHS "after more work on the standard" and to publish preview findings as safe-deployment guidance. No date is given, and the page carries no update notes after 27 August — [Anthropic](https://www.anthropic.com/news/model-hardware-standard-research-preview)
- How to apply: an "Apply for access" form linked from the MHS site. The site shows no fields, criteria, documents, specification, repository, dates or version history, and still says "limited research preview" — [modelhardwarestandard.com](https://modelhardwarestandard.com). The announcement describes this route as a waitlist — [Anthropic](https://www.anthropic.com/news/model-hardware-standard-research-preview)
- Named participants relevant to this project: Hugging Face (adding MHS to LeRobot), Raspberry Pi (testing a Camera MHS Driver), AWS (Strands Robots, with a private pre-release package during the preview), Universal Robots (early access, support planned), Doosan Robotics (testing) — [Anthropic](https://www.anthropic.com/news/model-hardware-standard-research-preview)
- A query of the `anthropics` GitHub organisation on 2026-11-09 returned no repository with "mhs" or "hardware" in its name — [github.com/anthropics](https://github.com/anthropics). A community list says github.com/modelhardwarestandard exists with 0 public repositories, but dates that entry to August 2026 — [awesome-mhs](https://github.com/carllippert/awesome-mhs)

**LeRobot integration**
- The LeRobot v0.6.0 notes do not mention MHS or MCP — [HF blog](https://huggingface.co/blog/lerobot-release-v060). Neither do the v0.7.0 notes (2026-11-05) — [GitHub release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)
- GitHub searches of huggingface/lerobot on 2026-11-09 for code containing "Model Hardware Standard" and pull requests matching "MHS" returned nothing — [repo](https://github.com/huggingface/lerobot)

**Unofficial and adjacent MHS-shaped work [secondary, repository READMEs]**
- PKU-YuanGroup/OpenMHS: "0.9, first public preview" (SDK 0.9.0.dev0, 2026-11-06), MIT, repository created 2026-11-07, 0 stars at check time. Its endpoint-enforced limits, a latching e-stop, chained action receipts in an offline-verifiable evidence log, and declared simulation with a fidelity contract are all in the README. It has JSON-RPC, MCP, CLI and Python surfaces, and states it is not a safety certification — [OpenMHS](https://github.com/PKU-YuanGroup/OpenMHS)
- awesome-mhs catalogues many "not affiliated" projects, including TARE (MIT, confirms every command by instrument readback), MHS Interop Bridge (generates an MHS manifest from a SiLA 2 Feature Definition), EVA-Client's EVA-MHS preview (real-robot deployment via MCP), a QwenLM plugin exposing `mhs_discover/read/write` as MCP tools, and Fastly's edge-mhs gateway — [awesome-mhs](https://github.com/carllippert/awesome-mhs)
- Several separate projects use the name "OpenMHS" or "Open-MHS", including PKU-YuanGroup, xi-zhao, Pratyush-exe and Abenor-Labs — [GitHub search](https://github.com/search?q=model+hardware+standard&type=repositories); [awesome-mhs](https://github.com/carllippert/awesome-mhs)

**MCP robot servers available now**
- robotmcp/ros-mcp-server connects LLMs to ROS 1 and ROS 2 robots without changing robot code. Apache-2.0, 1,519 stars, last push 2026-11-08 — [GitHub](https://github.com/robotmcp/ros-mcp-server); [release post](https://robotmcp.ai/blog/ros-mcp-server-release)
- wise-vision/ros2_mcp lists, subscribes, publishes and calls ROS 2 topics, services and actions; MPL-2.0, ROS 2 only — [GitHub](https://github.com/wise-vision/ros2_mcp)
- IliaLarchenko/robot_MCP drives SO-ARM100/101 and LeKiwi arms over streamable-HTTP and SSE, and includes a CLI agent for Claude, Gemini or GPT; Apache 2.0 — [mcpservers.org listing](https://mcpservers.org/en/servers/IliaLarchenko/robot_MCP)
- titansage02/so101-mcp connects the SO-101 via LeRobot and has a mock-robot mode — [LobeHub listing](https://lobehub.com/en/mcp/titansage02-so101-mcp)

### Inferences
- The 2026-08-31 notebook entry is still accurate on status. Any "built on MHS" claim in a public post would be unverifiable today, whereas "MCP server shaped to sit above MHS" is defensible.
- OpenMHS's receipts and evidence log are the closest public relative to Hebbot's experience substrate that I found. It is a very new, unproven project; it is worth reading before a paper's related-work section is drafted, and not a dependency to adopt yet.
- An SO-100 port has prior art in two MCP servers. Their tool surfaces are worth comparing, though neither was evaluated for safety behaviour here.

### Gaps
- News after mid-September on MHS may exist without being indexed; the conclusion rests on Anthropic's two pages and the GitHub checks. A private LeRobot branch or pre-release package would not appear in these searches, and GitHub code search covers only indexed default branches.
- Form criteria are not visible without opening the form, so whether a hobbyist would be accepted is unknown.
- I did not check non-MHS industrial standards directly (OPC UA robotics, SiLA 2 itself). SiLA 2 appears here only through the Interop Bridge listing.

## LeRobot: current state

### Takeaway
LeRobot is at v0.7.0 (2026-11-05), with dataset format v3.0 and SO-100/SO-101 natively supported. Franka hardware is available only through community plugins, although simulated Franka benchmarks and Franka-tuned policies are in-tree.

### Cited Findings
**Releases [primary]**
- GitHub release timestamps: v0.6.0 2026-07-06, v0.6.1 2026-08-03, v0.7.0 2026-11-05; Apache-2.0; 28,774 stars on 2026-11-09 — [GitHub API/releases](https://github.com/huggingface/lerobot/releases)
- v0.6.0: `lerobot-rollout` (base, sentry, highlight, episodic, dagger strategies), `lerobot-annotate`, a `lerobot.rewards` API, world-model policies (VLA-JEPA, LingBot-VA, FastWAM), RealSense depth across SO-100/101 and others, 12-bit depth video, Isaac Teleop for SO-101 — [HF blog](https://huggingface.co/blog/lerobot-release-v060)
- v0.6.0 evaluation: six new benchmarks (LIBERO-plus, RoboTwin 2.0, RoboCasa365, RoboCerebra, RoboMME, VLABench), nine benchmark families in total — [HF blog](https://huggingface.co/blog/lerobot-release-v060)
- v0.7.0 adds FSDP2/HSDP training, Trackio replacing W&B, chat-style rollout, `lerobot[remote]` with `lerobot-policy-server` replacing `lerobot.async_inference`, a shared simulator core, and the NVIDIA RoboLab benchmark — [release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)
- v0.7.0 policies: FineART-VLA, DM05, Wall-OSS-0.5, LingBot-VLA 2.0, Cosmos3, LaWAM, FLUX 3 Action, OpenGalaxea G0.5, Long-WAM — [release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)
- v0.7.0 breaking change relevant to the port: Feetech and Dynamixel now share one rustypot-backed bus. macOS needs 14+, or 15+ for MPS — [release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)

**Hardware**
- Native robot folders at v0.7-era main: `so_follower`, `bi_so_follower`, `koch_follower`, `lekiwi`, `omx_follower`, `openarm_follower`, `rebot_b601_follower`, `yam_follower`, `reachy2`, `hope_jr`, `unitree_g1`, `earthrover_mini_plus`, `sim_robot`. There is no Franka folder — [repo](https://github.com/huggingface/lerobot/tree/main/src/lerobot/robots)
- The docs call the SO-101 "our flagship, low-cost arm", and `so100.mdx` and `so101.mdx` both exist — [docs](https://huggingface.co/docs/lerobot/index); [repo docs](https://github.com/huggingface/lerobot/tree/main/docs/source)
- Franka plugins listed by LeRobot: `lerobot-robot-franka-research3` (FR3 through `franky`, with joint, Cartesian and impedance control) and LeFranX (Franka with XHand for VR teleop, flagged with a warning symbol in the table) — [third-party robots doc](https://github.com/huggingface/lerobot/blob/main/docs/source/third_party_robots.mdx). PR #1276, "Support for franka emika", was still open on 2026-10-21 — [repo](https://github.com/huggingface/lerobot/pull/1276)
- In-tree simulated Franka content: RoboLab (120 tabletop tasks on a DROID Franka Panda + Robotiq 2F-85, including stacking) — [doc](https://github.com/huggingface/lerobot/blob/main/docs/source/robolab.mdx). `flux-3-action-so101` and `flux-3-action-droid` fine-tunes exist for SO-101 and Franka Panda — [doc](https://github.com/huggingface/lerobot/blob/main/docs/source/flux3.mdx)

**Data and hub**
- `CODEBASE_VERSION = "v3.0"` — [dataset_metadata.py](https://github.com/huggingface/lerobot/blob/main/src/lerobot/datasets/dataset_metadata.py). Datasets are synchronised video plus state/action and can stream from the Hub — [docs](https://huggingface.co/docs/lerobot/index)
- v0.7.0 adds disk-less streaming (`--dataset.streaming=true`), a dataset reader registry, and native LanceDB-stored datasets — [release](https://github.com/huggingface/lerobot/releases/tag/v0.7.0)
- LeLab is a browser GUI for calibrating, recording and training — [docs](https://huggingface.co/docs/lerobot/index)

**Cheap hardware**
- The SO-ARM100 repository's parts table totals $229.88 / €226.3 for a table with 12 servos and 2 control boards, which matches a leader-plus-follower pair, and a second table totals $121.94 / €124.3 — [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100)
- SO-ARM 102 is a third-party next-generation kit from Robonine with a LeRobot plugin; claimed 2x payload and frame stiffness, 67% faster shoulder/elbow, 10% longer reach than SO-ARM 101 — [third-party robots doc](https://github.com/huggingface/lerobot/blob/main/docs/source/third_party_robots.mdx)
- Retail snapshots vary widely, for example A$714.19 for an assembled SO-101 Pro kit in Australia — [Pakronics](https://www.pakronics.com.au/products/so-arm101-low-cost-ai-arm-servo-motor-kit-pro-for-lerobot-assembled-version-ss100046482)
- One community guide calls SO-100 deprecated in favour of SO-101 — [secondary](https://web-3kk.pages.dev/en/knowledge/hardware/arms/lerobot-arms)

### Inferences
- The project's $250 SO-100 target looks like the cost of a teleoperable pair. An MCP-driven arm needs only a follower, at about half the servo count, which may cut the cost materially. The SO-101 is the safer target, given that it is the documented flagship.
- The natural LeRobot hook is a plugin, since that is how Franka support already arrives. Exporting Hebbot episodes as LeRobotDataset v3.0 would connect it to the Hub without changing the substrate.
- RoboLab offers a Franka Panda stacking benchmark runnable from LeRobot, in simulation only.

### Gaps
- Whether `lerobot-robot-franka-research3` or `franky` works on a Panda rather than an FR3 was not verified.
- The second BOM total was not confirmed as follower-only. BOM figures exclude any costs I did not see itemised.
- The full v0.7.0 changelog was truncated when read; the dataset version comes from the code rather than the notes.

## Competitions a hobbyist or one-person team could enter, 2026-27

### Takeaway
Three entries are realistically open: LeHome 2027 (the simulation round opens for registration 2026-12-01), the RoboCup ARM Challenge qualifier (2027 registration opens December 2026, eligibility to be confirmed), and RGMC 12 (interest form only so far). No open competition I found lets a Franka or SO-100 compete from home in a real-world track.

### Cited Findings
| Competition | Status on 2026-11-09 | Dates | Hardware / eligibility | Source |
|---|---|---|---|---|
| **LeHome Challenge 2027** | Not open yet | Registration opens 2026-12-01; benchmark 2027-01-03; code submission to 2027-04-15; results 04-16 to 04-20; on-site 05-25 to 05-27 in Seoul | Simulation round open to all teams, top 6 advance. Common robot and sensor setup; 2027 platform not named; specs late Apr/early May. Recipe scoring: one point per consecutive correct layer from the bottom bun. Team limits, travel support and prize amounts TBD | [LeHome](https://lehome-challenge.com/) |
| **RGMC 12** at ICRA 2027 | Interest form only | 2027-05-24 to 05-28 (Seoul); application forms "soon" | 5 tracks, not listed on the main page; no hardware rules stated; 25+ teams interested | [RGMC](https://sites.google.com/view/rgmcomp?pli=1&authuser=0) |
| **RoboCup ARM Challenge** | 2027 registration expected Dec 2026 | No other 2027 dates; 2026 deadline 2026-04-24 PASSED | MATLAB/Simulink in simulation; virtual qualifier is free; finalists use UR5e at RoboCup, with some fees. Aimed at young researchers and students; no formal criteria shown | [ARM Challenge](https://arm.robocup.org/) |
| **RoboSoft 2027 Wild Manipulation** | Open, but needs a soft robot | Rulebook final Dec 2026; application 2027-01-18; notification 01-25; confirmation 03-15; on-site registration 04-05 | Compliance must enable a relevant function; 2-minute video and technical description; ten $3,000 travel grants | [RoboSoft](https://robosoft27.com/call-for-competitions/) |
| **RoboCup@Home 2027 OPL** | Qualification open | Intention 2026-10-30 PASSED (advised, not mandatory); materials 2026-12-06; qualification 12-23; confirmation 2027-01-22; event 06-15 to 06-21, Nuremberg | 8+2 page TDP, video of at least 5 integrated abilities, website; running hardware required | [RoboCup@Home](https://athome.robocup.org/call-for-participation-robocuphome-2027/) |
| **PhyRC 2027** | CLOSED to new teams | Registration 2026-10-15 PASSED; Phase 1 closes 2026-12-15; Phase 2 roster 2027-01-15 | Solo was allowed; no robot for Phase 1 (Isaac Sim, 12 GB RTX); no late-entry route | [PhyRC](https://emprise.cs.cornell.edu/phyrc/challenge-2027/) |
| **AI for Industry Challenge** | Finished | Timeline ran to 2026-08-25 PASSED | Registration closed; sources conflict on 2026-04-17 vs 2026-05-08 | [Discourse](https://discourse.openrobotics.org/t/ai-for-industry-challenge-challenge-details/52380); [Intrinsic](https://www.intrinsic.ai/events/ai-for-industry-challenge) |
| **RoboChess** (CoRL 2026) | Unclear, likely over | Winners listed for 2026-11-09; submission dates were placeholders as of 2026-06-17 | Real-world track names SO-101 and Franka; EvalAI; sim track on Isaac Lab, MuJoCo, Genesis | [RoboChess](https://sites.google.com/view/corl-workshop-robochess) |
| **RoboSynChallenge** (NeurIPS 2026) | Entry status unknown | Session 2026-12-11 | Simulated training, final real-world assessment; platforms and eligibility not stated | [NeurIPS](https://neurips.cc/virtual/2026/competition/137687) |

- ICRA 2027's official competition list has five entries: Humanoid Manufacturing, Legged Robot Challenges/Wearable Robot Systems, RGMC, RoboRacer and PhyRC — [ICRA](https://2027.ieee-icra.org/program/competitions/). LeHome says it is on-site at ICRA 2027 but is not on that list — [LeHome](https://lehome-challenge.com/)
- ICRA 2027 competition proposals closed 2026-08-31 (PASSED); competitions run 2027-05-25 to 05-27 — [ICRA](https://2027.ieee-icra.org/contribute/)
- The 2026 LeHome edition used the SO-ARM101, with online simulation Feb-Apr 2026 and an on-site final for the top eight at ICRA 2026; it has concluded — [search result for lehome-challenge.com, 2026 archive](https://lehome-challenge.com/)
- Community SO-101 events are frequent but mostly local: the Robot Pizza Cup ran 2026-10-30 to 11-01 (PASSED) — [starter repo](https://github.com/FunRobots/robot-pizza-cup). The HF LeRobot Worldwide Hackathon's visible activity is from 2025 — [HF](https://huggingface.co/LeRobot-worldwide-hackathon)
- NVIDIA's Video2Data challenge kicked off 2026-09-21 — [NVIDIA forum](https://forums.developer.nvidia.com/t/nvidia-video-to-data-v2d-challenge-turn-videos-into-robot-skills/384050)

### Inferences
- LeHome's simulation round is the best fit: it is free of a hardware requirement, runs for about 3.5 months, and its scoring is effectively ordered stacking. It does reward a trained policy on deformable objects, though, which differs from Hebbot's agentic bench approach. Travel to Seoul would only matter for a top-six finish.
- RGMC is worth an interest-form submission because it costs nothing and reveals the track rules. Its on-site requirements may rule out a solo entrant.
- Hebbot's hardware would not qualify for RoboSoft without a functional soft gripper, and a fixed arm is a poor fit for RoboCup@Home's integrated-abilities video.
- RoboChess is the only event found that names both of the project's arms, so a 2027 edition would be worth watching.

### Gaps
- I could not confirm ARM Challenge eligibility for a non-student hobbyist, or whether finalists may attend remotely.
- RGMC 2027 track names and rules, IROS 2027 competitions, and any official LeRobot-run 2027 challenge were not found.
- RoboChess's final dates and outcome could not be confirmed. LeHome's ICRA affiliation should be confirmed with its organisers.

## Tight-tolerance or stacking benchmarks with reproducible protocols

### Takeaway
No benchmark I found specifies small-block stacking at a stated clearance. The strongest base is the YCB Block Pick and Place protocol for the task and NIST's Assembly Task Board templates for the statistics.

### Cited Findings
- YCB Block Pick and Place tests grasping small objects and transferring them to a specified location, with points for completion and precision. HERB grasped reliably but failed precise placement, because the block's pose relative to the gripper was unknown after a push grasp — [Calli et al., arXiv:1502.03143](https://arxiv.org/pdf/1502.03143)
- The same paper's Peg Insertion Learning Assessment perturbs the peg board's position; a learned PR2 controller succeeded 4 of 10 trials at 5 mm perturbation — [Calli et al.](https://arxiv.org/pdf/1502.03143)
- The block protocol uses a printed template showing target positions — [secondary summary, YCB-related search result](https://www.emergentmind.com/topics/ycb-object-and-model-set)
- NIST Assembly Task Boards 1-5 provide benchmark templates, CAD/STL and laser-cut files, a protocol template, multiple-repetition statistical guidance, a test-metrics document, and peg-hole performance data. Board 1 covers peg insertions, gear meshing, connectors and nut threading. The page was updated 2026-10-01 — [NIST](https://www.nist.gov/el/intelligent-systems-division-73500/robotic-grasping-and-manipulation-assembly/assembly). Metrics: [NIST PDF](https://www.nist.gov/document/testmethod-metrics-analysisv3pdf)
- Board 1 can be purchased from UMass Lowell's NERVE Center — [NERVE form](https://www.uml.edu/Research/NERVE/NIST-Assembly-Task-Board-Form.aspx)
- REASSEMBLE chose Board 1 because it suits single-arm systems, and published 3D-printed holders — [arXiv:2502.05086](https://arxiv.org/abs/2502.05086)
- RAMP is an open-source benchmark built from 3D-printed parts and aluminium extrusions — [arXiv:2305.09644](https://arxiv.org/pdf/2305.09644). FMB uses 66 3D-printed objects designed for replication — [IJRR](https://journals.sagepub.com/doi/10.1177/02783649241276017)
- The Industrial Dexterity Benchmark (2026-07-15, v3 2026-09-21) offers boards for cable management, harnesses and gearbox assembly; only the datacenter cable board has results (78% best vs 36% baseline, 48 trials per configuration) — [arXiv:2607.14021](https://arxiv.org/abs/2607.14021)
- SceneReplica addresses replicable object placement — [arXiv:2306.15620](https://arxiv.org/pdf/2306.15620). IndustReal covers sim-to-real pick, place and insertion and says it provides reproduction tools — [arXiv:2305.17110](https://arxiv.org/abs/2305.17110)
- The COMPARE community ran an IROS 2026 workshop on reproducibility guidelines for open-source manipulation — [COMPARE digest](https://groups.google.com/g/compare-ecosystem/c/OjX5ntlSvtE)

### Inferences
- A defensible public benchmark would use YCB-style blocks and a printed target layout, add a declared block-to-block tolerance, and report repetitions and failure statistics in NIST's manner. Stating these as a new protocol is more honest than claiming compliance with an existing one.
- The HERB result describes the same grasp-pose uncertainty that the project's error-model work addresses, so it is a useful citation.

### Gaps
- The NIST page and my sources do not state insertion clearances or a scoring rubric; these would be in the board templates, which I did not open.
- YCB's Appendix B.4 detailed scoring was not extracted. IndustReal's robot and clearance figures were not on the abstract page.

## Workshops and venues after November 2026

### Takeaway
The firm near-term deadline is RSS 2027 on 2026-12-04, and its two-stage format explicitly tolerates preliminary experiments and negative results. The best-matched workshops, at ICLR and CVPR 2027, are still proposals.

### Cited Findings
| Venue | Date status | Details | Source |
|---|---|---|---|
| **RSS 2027** (Athens, 07-06 to 07-11) | **Confirmed**: Stage 1 abstract 2026-12-04 AoE | Decision 02-05; rebuttal 02-12; Stage 2 decision 02-26; final paper 04-16; notification 04-30. 6-page abstract, then 8 pages. Double-blind, no GitHub or YouTube links. Negative results are not grounds for rejection if insightful. Hallucinated citations mean desk rejection; LLM use is allowed, disclosure encouraged | [RSS CFP](https://roboticsconference.org/information/cfp/) |
| **Agentic Physical AI**, ICLR 2027 | **Proposed**: 2027-02-01 AoE | Under review; call planned Dec 2026; notification 02-26; up to 8 pages or 2-4 page short/position papers; non-archival; short papers must be primarily human-authored. Topics include active experimentation, memory updating, lab systems | [Workshop](https://agentic-physical-ai.github.io/) |
| **Agentic Robotics**, CVPR 2027 (Seattle, 06-20 or 21) | **Proposed**: 2027-03-12 AoE | 4 or 8 pages; non-archival; double-blind; depends on proposal decision | [Workshop](https://agenticrobotics.github.io/) |
| **Never-Ending Information: Robot Active Lifelong Memory Management**, ICRA 2027 (Seoul) | Deadline **TBA 2027**; acceptance not stated | 2-4 page research papers or 1-2 page deployment reports; non-archival; forgetting and compression; memory for agents | [Workshop](https://active-memory-icra27.github.io/) |
| **IROS 2027** (Florence, 09-27 to 10-01) | **Secondary**: 2027-03-01 | Listed from the IEEE RAS calendar; the official site is a teaser with no full call | [Physical AI Lab](https://physicalailab.io/en/iros-2027-florence-dates-deadline-venue/); [official site](https://2027.ieee-iros.org/) |

- ICLR will notify workshop organisers on 2026-11-29, and its suggested contribution deadline is 2027-02-01 — [ICLR](https://iclr.cc/Conferences/2027/CallForWorkshops). That page prints the workshop dates as April 2026, apparently a typo.
- ICRA 2027 main papers closed 2026-09-16 (PASSED). Workshop and tutorial proposals closed 2026-11-02 (PASSED), with acceptances on 2027-01-31 — [ICRA](https://2027.ieee-icra.org/contribute/)
- Still open at ICRA 2027: Satellite Conference Centers 2026-11-10, Community Building Day 2026-11-30, Arts in Robotics 2026-12-01 — [ICRA](https://2027.ieee-icra.org/contribute/)
- CoRL 2026 runs 11-09 to 11-12 in Austin, and its workshop deadlines have passed, for example Sim-to-Real-to-Field on 2026-10-08 — [CoRL coverage](https://runtimewire.com/article/corl-2026-austin-robot-learning); [workshop search result](https://how-real-is-real-enough.github.io/)
- ICLR 2027 main deadline was 2026-09-25 (PASSED) — [ICLR CFP](https://iclr.cc/Conferences/2027/CallForPapers)

### Inferences
- RSS's format suits a single-arm substrate study, since Stage 1 needs a developed hypothesis and method more than complete results. Its anonymity rule conflicts with public posts that advertise the work, so timing of posts needs care.
- The Agentic Physical AI short-paper track is the closest thematic fit, and its human-authorship rule matters for an agent-assisted project. ICRA's deployment-report format suits the lab notebook's material.

### Gaps
- RA-L, JOSS, HRI, AAAI workshops, ICML 2027 workshops and CoRL 2027 were not checked. RSS 2027 and IROS 2027 workshop calls were not found.
- ICRA 2027's accepted workshop list is not due until 2027-01-31, so further matches may appear.

## Regulatory: EU Machinery Regulation 2023/1230

### Takeaway
The application date is **20 January 2027**, verified against the Official Journal corrigendum and unchanged by the 2026 Digital Omnibus. A non-commercial software project is unlikely to be the main target, but own-use construction and any sale of kits are not automatically outside it.

### Cited Findings
- The 29 June 2023 Official Journal text said Article 54 applies "from 14 January 2027" — [Regulation (EU) 2023/1230, OJ L 165](https://publications.europa.eu/resource/celex/32023R1230)
- A corrigendum in OJ L 169/35 (2023-07-04) corrected Article 54 to 20 January 2027, and Articles 51 and 52 likewise — [Corrigendum](https://publications.europa.eu/resource/celex/32023R1230R%2801%29)
- The Commission states the Regulation "applies on a mandatory basis as of 20 January 2027", and that a corrigendum fixed a clerical error on dates — [European Commission](https://single-market-economy.ec.europa.eu/sectors/mechanical-engineering/machinery_en). EU-OSHA gives the same date — [EU-OSHA](https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery)
- Regulation (EU) 2026/1744 (Digital Omnibus on AI, OJ 2026-07-24) amends 2023/1230 by adding delegated-act powers for AI safety requirements, applying by 2 August 2028, and moves 2023/1230 to Section B of the AI Act's Annex I. A text search of the omnibus finds no occurrence of "January 2027" — [Regulation 2026/1744](https://publications.europa.eu/resource/celex/32026R1744)
- Article 2(2)(m) excludes "machinery or related products specially designed and constructed for research purposes for temporary use in laboratories" — [Regulation 2023/1230](https://publications.europa.eu/resource/celex/32023R1230)
- "Making available on the market" means supply "in the course of a commercial activity, whether in return for payment or free of charge" — [Regulation 2023/1230](https://publications.europa.eu/resource/celex/32023R1230)
- "Manufacturer" includes a person who "manufactures products within the scope of this Regulation, and puts those products into service for its own use" — [Regulation 2023/1230](https://publications.europa.eu/resource/celex/32023R1230)
- Products placed on the market in conformity with Directive 2006/42/EC before the date may continue to be made available (Article 52, as corrected) — [Regulation 2023/1230](https://publications.europa.eu/resource/celex/32023R1230); [Corrigendum](https://publications.europa.eu/resource/celex/32023R1230R%2801%29)

### Inferences (not legal advice)
- Publishing MIT-licensed code non-commercially does not obviously amount to making a product available on the market. Selling kits or configured arms in the EU would change that analysis.
- A self-built SO-100 used at home could fall within the own-use "manufacturer" limb. The research exclusion is narrow, since it requires both a research purpose and temporary laboratory use.
- An existing Panda keeps its status as a product already placed on the market. Whether software-driven changes amount to a "substantial modification" is an open question below.

### Gaps
- Whether Doug is in the EU is not known from the materials reviewed; the Regulation matters mainly for EU placement or service.
- I did not analyse "substantial modification", the software safety-component provisions, or household-appliance exclusions. No Commission guidance specific to hobbyists was found. A consolidated text could not be retrieved, though the corrigendum and omnibus were read directly.
