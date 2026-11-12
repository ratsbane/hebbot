# Open-weight robot foundation models for one Panda and 16 GB, and the evidence for and against layered robot cognition

Searched 2026-11-09. Hardware assumed: Franka Panda, one wrist RealSense, RTX 5060 Ti 16 GB. "Vendor" means numbers reported by the model's own developers. "Independent" means evaluators who did not build the model. No source benchmarks an RTX 5060 Ti; all latency and VRAM figures come from other GPUs.

## Which open-weight VLAs and robot foundation models exist, and which fit in 16 GB?

### Takeaway
In practice, a 16 GB card can run π0.5, SmolVLA, Evo-1 and V-JEPA Policy for inference, and GR00T N1.7 and RDT2 only at the edge. Of the mainstream VLAs it can fine-tune only SmolVLA (plus non-VLA ACT/Diffusion, and RDT2's action expert at the limit), so π0.5-class fine-tuning needs rented 40 GB GPUs. The model that fits for training is also the weakest on the one independent Franka benchmark found (PhAIL).

### Cited Findings

**Model table** (entries are as stated by the cited source; "n/s" = not stated in what I read)

| Model (date) | Licence | Params | Inference memory | Fine-tuning memory | Rate / latency | Demos used | Franka evidence |
|---|---|---|---|---|---|---|---|
| π0.5 (openpi, Sep 2025) | Repo [Apache-2.0, also ships LICENSE_GEMMA](https://github.com/Physical-Intelligence/openpi); LeRobot ports [carry a Gemma tag (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [3.6B (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [>8 GB](https://github.com/Physical-Intelligence/openpi); [8.87 GiB peak measured, RTX 4090, 3 views](https://arxiv.org/html/2609.37250v1) | [LoRA >22.5 GB, full >70 GB](https://github.com/Physical-Intelligence/openpi); [24–40 GB at BS 8, "24 GB tight at BS 1"](https://huggingface.co/docs/lerobot/main/hardware_guide.md) | [159 ms, RTX 4090](https://arxiv.org/html/2609.37250v1); [~190 ms](https://developer.nvidia.com/blog/pretrained-to-imagine-fine-tuned-to-act-the-rise-of-world-action-models/) | [50 per task (VLA-REPLICA)](https://arxiv.org/html/2605.20774) | [`pi05_droid` checkpoint](https://github.com/Physical-Intelligence/openpi); [FR3 results in PhAIL](https://arxiv.org/html/2605.29710) |
| π0 / π0-FAST (Feb 2025) | [Apache-2.0 repo](https://github.com/Physical-Intelligence/openpi) | n/s | [>8 GB](https://github.com/Physical-Intelligence/openpi) | as π0.5 | [π0 11.5 Hz (third-party, Evo-1 paper)](https://www.alphaxiv.org/abs/2511.04555.md) | | [`pi0_droid`, `pi0_fast_droid`](https://github.com/Physical-Intelligence/openpi) |
| SmolVLA (Jun 2025) | [Apache-2.0 (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [450M](https://huggingface.co/blog/smolvla) | ["consumer-grade GPUs or even CPUs"](https://arxiv.org/abs/2506.01844) | [~10–16 GB at BS 8; freeze vision encoder if short](https://huggingface.co/docs/lerobot/main/hardware_guide.md) | [12.7 Hz (Evo-1 paper)](https://www.alphaxiv.org/abs/2511.04555.md); [~0.1 s/chunk, RTX 4090](https://justintiensmith.github.io/vla-research/2026/04/10/are-vlas-overhyped-first-results-on-a-real-robot.html) | 50 (see below) | [FR3 in PhAIL](https://arxiv.org/html/2605.29710) |
| Evo-1 (Nov 2025) | [n/s in paper; check repo](https://www.alphaxiv.org/abs/2511.04555.md) | [0.77B](https://www.alphaxiv.org/abs/2511.04555.md) | [~2.3 GB, RTX 4090d](https://www.alphaxiv.org/abs/2511.04555.md) | [Trained on 8×A100; consumer figure n/s](https://www.alphaxiv.org/abs/2511.04555.md) | [~16.4 Hz](https://www.alphaxiv.org/abs/2511.04555.md) | [100 per task, no robot-data pretraining](https://www.alphaxiv.org/abs/2511.04555.md) | None; [real tests on xArm6](https://www.alphaxiv.org/abs/2511.04555.md) |
| GR00T N1.7 (2026) | [Code Apache-2.0; weights NVIDIA Open Model License; README also says "fully commercially licensable under Apache 2.0"](https://github.com/NVIDIA/Isaac-GR00T) (internal conflict). [Gated Cosmos-Reason2-2B backbone](https://github.com/NVIDIA/Isaac-GR00T) | [3B](https://github.com/NVIDIA/Isaac-GR00T) | [16 GB+](https://github.com/NVIDIA/Isaac-GR00T) | [40 GB+ recommended](https://github.com/NVIDIA/Isaac-GR00T) | [~40 Hz RTX PRO 6000 Blackwell, ~28 Hz Jetson Thor (batch-1 LIBERO)](https://github.com/NVIDIA/Isaac-GR00T) | n/s | [`GR00T-N1.7-LIBERO` (Panda), `GR00T-N1.7-DROID`](https://github.com/NVIDIA/Isaac-GR00T); [N1.6 on FR3 in PhAIL](https://arxiv.org/html/2605.29710) |
| MolmoAct2 (Ai2, May 2026) | [Repo says Apache 2.0, "for research and educational use"](https://github.com/allenai/molmoact2); [no licence tag on card (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [5.4B (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [DROID float32 ~88 GB; YAM float32 ~26 GB, YAM bf16 under 16 GB](https://github.com/allenai/molmoact2) | [n/s; authors used 1,152 GPU-hours on 32 H100s for DROID](https://arxiv.org/html/2605.02881) | [DROID 15 Hz](https://arxiv.org/html/2605.02881) | [74,604 filtered DROID episodes (authors' fine-tune)](https://arxiv.org/html/2605.02881) | [Deployment "verified only on SO-100 and Franka DROID"](https://github.com/allenai/molmoact2) |
| RDT2 (Sep 2025; paper Feb 2026) | [Apache-2.0](https://github.com/thu-ml/RDT2) | n/s (adapted from Qwen2.5-VL-7B) | [~16 GB VRAM, >32 GB RAM](https://github.com/thu-ml/RDT2) | [RDT2-FM expert ~16 GB; VQ LoRA >32 GB; VQ full >80 GB](https://github.com/thu-ml/RDT2) | [24-step chunk, 0.8 s at 30 fps](https://github.com/thu-ml/RDT2) | n/s | [Verified on bimanual FR3 and bimanual UR5e only, with specific end effectors and 3D-printed parts](https://github.com/thu-ml/RDT2) |
| X-VLA (Nov 2025) | [Apache-2.0 (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [0.9B (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | n/s | [LeRobot lists it in the 24–40 GB large-VLA group](https://huggingface.co/docs/lerobot/main/hardware_guide.md) | n/s | [50 per task](https://arxiv.org/html/2605.20774) | None found |
| OpenVLA-OFT (Feb 2025) | [MIT](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [7.5B (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [~16–18 GB (aggregator, citing repo)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [27–80 GB (aggregator, citing repo)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | [OpenVLA 7.9 Hz](https://www.alphaxiv.org/abs/2511.04555.md) | | |
| LingBot-VLA (Jan 2026) | [Paper does not state weight licence](https://arxiv.org/html/2601.18692v1) | [3B-scale Qwen2.5-VL variant in throughput tests; total n/s](https://arxiv.org/html/2601.18692v1) | n/s | [8-GPU training figures only](https://arxiv.org/html/2601.18692v1) | [50-step chunk; rate n/s](https://arxiv.org/html/2601.18692v1) | [130 per task; 80 in efficiency test](https://arxiv.org/html/2601.18692v1) | [None; bimanual Franka appears only in pretraining data](https://arxiv.org/html/2601.18692v1) |
| Xiaomi-Robotics-0 (Feb 2026) | [Apache-2.0 per Gitee mirror only](https://gitee.com/fengcha007/Xiaomi-Robotics-0) | [4.7B (press)](https://news.lavx.hu/article/xiaomi-open-sources-4-7b-parameter-embodied-ai-model-with-80ms-latency) | n/s; [RTX 4090 claim (secondary coverage; exact outlet not pinned down)](https://eu.36kr.com/en/p/3680313089142664) | n/s | [80 ms, 30 Hz (press, not confirmed in paper abstract)](https://news.lavx.hu/article/xiaomi-open-sources-4-7b-parameter-embodied-ai-model-with-80ms-latency) | n/s | [LIBERO, CALVIN, SimplerEnv checkpoints (via README, per search)](https://gitee.com/fengcha007/Xiaomi-Robotics-0) |
| OpenVLA (Jun 2024) | [MIT](https://huggingface.co/openvla/openvla-7b) | [7.5B (aggregator)](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026) | n/s | n/s | [7.9 Hz](https://www.alphaxiv.org/abs/2511.04555.md) | | |

**Newer closed models.** π*0.6 and π0.7 are [listed as having no released weights by a third-party guide, with newer models offered through a waitlisted Pi API beta since 13 Oct 2026](https://physicalailab.io/en/physical-intelligence-pi-models-openpi-api/). [The openpi README's latest updates stop at September 2025](https://github.com/Physical-Intelligence/openpi). GEN-1.5: see the end-to-end section.

**Non-VLA baselines from LeRobot.** [ACT ~2–6 GB, Diffusion ~8–14 GB at BS 8](https://huggingface.co/docs/lerobot/main/hardware_guide.md).

**Training-time anchors, indicative only.** On a ~50-episode dataset for 5 epochs: [SmolVLA BS 4 on a 24 GB L4/A10G ~3–6 h; π0/π0.5 BS 4 on an A100 40 GB ~4–8 h; ±50%](https://huggingface.co/docs/lerobot/main/hardware_guide.md). The guide states [memory scales roughly linearly with batch size and that AdamW state adds ~30–100%](https://huggingface.co/docs/lerobot/main/hardware_guide.md). It describes 24 GB consumer cards as ["comfortable" for Light BC, Diffusion and SmolVLA, "tight for VLAs at batch 1"](https://huggingface.co/docs/lerobot/main/hardware_guide.md). One Anyscale tutorial puts π0.5 at [~28 GB even with the PaliGemma backbone frozen, in BF16](https://docs.anyscale.com/tutorials/vla-fine-tuning.md).

**Framework caveats.** [openpi's PyTorch path does not support LoRA, mixed-precision training, π0-FAST or EMA](https://github.com/Physical-Intelligence/openpi), so the >22.5 GB LoRA figure applies to the JAX path. [MolmoAct2's stack uses CUDA 12.8 and tells RTX 50-series users to check for `sm_120` support](https://github.com/allenai/molmoact2).

**Demo counts in practice.** [One hobbyist saw SmolVLA ~0% at 20 demos, ~30% (3/10) at 50, and concluded "more consistent data" mattered more than count](https://justintiensmith.github.io/vla-research/2026/04/10/are-vlas-overhyped-first-results-on-a-real-robot.html). [The TAPT long-horizon paper used 30 demos per real task on FR3](https://arxiv.org/html/2605.13119v2). [PhAIL used 449 episodes (~13 h) across four object types](https://arxiv.org/html/2605.29710).

### Inferences
- **Runs locally for inference:** π0.5 (about 8.9 GiB measured), SmolVLA, Evo-1, V-JEPA Policy. **Marginal:** GR00T N1.7 ("16 GB+" leaves no headroom when the card also drives other work) and RDT2 (~16 GB, and its verified setups are bimanual). **Likely no:** MolmoAct2-DROID, the Franka checkpoint. The card's ~88 GB float32 figure halves to roughly 44 GB in bf16 by simple arithmetic; only YAM is documented under 16 GB. This needs confirming on the actual checkpoint.
- **Fine-tunes locally:** SmolVLA, at batch size 4 or less with the vision encoder frozen; ACT and Diffusion comfortably; RDT2-FM at the limit. **Needs cloud:** π0, π0.5, GR00T, X-VLA, MolmoAct2.
- The likely working pattern is to rent an A100 40 GB for π0.5 fine-tunes of hours per skill, and run inference on the 5060 Ti. A 5060 Ti will be slower than the RTX 4090 figures above, by an unmeasured amount.
- **Camera mismatch is a concrete risk for this rig.** [MolmoAct2-DROID requests both an `external` and a `wrist` camera](https://github.com/allenai/molmoact2), and DROID-derived checkpoints generally assume an external view. With a single wrist RealSense, a DROID checkpoint would be run off-spec unless a second camera is added.

### Gaps
- No RTX 5060 Ti measurement exists for any model.
- I could not find per-task real Franka results for MolmoAct2: [the paper's Appendix C.2 and §6.8 fell outside what I read](https://arxiv.org/html/2605.02881). Secondary sources conflict (50.1%, 50.6, 87.1%) and were not reconciled.
- Licences for MolmoAct2 weights, Xiaomi-Robotics-0 and LingBot-VLA were not confirmed on official model cards.
- pi0.5, SmolVLA, X-VLA and OpenVLA parameter counts rest partly on an aggregator ([presenc.ai](https://presenc.ai/research/best-open-weight-robotics-vla-models-2026), which itself cites HF cards).
- No RDT2 demo-count guidance. No demonstrated low-VRAM (under 16 GB) π0.5 fine-tune found.

## Force/torque-aware VLAs and world-model policies

### Takeaway
Force-aware policies beat vision-only baselines by about 35 percentage points on contact-rich tasks in authors' own Franka/Flexiv tests, but none reports clearance tolerances, none is confirmed open-weight, and no independent replication was found. The Panda exposes an estimated wrench without an added sensor, but the papers read used dedicated F/T sensors.

### Cited Findings
- **ForceVLA2 (Mar 2026; authors' test, Flexiv Rizon 4s with 6D F/T at 300 Hz, 20 trials per task).** Average success [π0 18.0%, π0.5 31.0%, ForceVLA 35.0%, ForceVLA2 66.0%; gear assembly 0 / 20 / 10 / 70%](https://arxiv.org/html/2603.15169). It used [1,000 trajectories, 8 A100s for ~10 h, 15 Hz inference on a 4090; no release mentioned, clearances not reported](https://arxiv.org/html/2603.15169). Its [conclusion's numbers contradict its own table](https://arxiv.org/html/2603.15169).
- **FAWAM (Jun 2026, v3 Oct 2026; authors' test, FR3 with ATI Axia80 wrist F/T at 120 Hz, 90 demos per task + 20 intervention episodes, 20 trials).** Average success [π0.5 47.50%, GE-Act 48.75%, ForceVLA 63.75%, TA-VLA 63.75%, FAWAM 85.00%](https://arxiv.org/html/2606.08555), with the [abstract's 36.25 being percentage points over the best vision-only baseline](https://arxiv.org/abs/2606.08555). Tasks are [erasing, peeling, pivoting and wiping, with no clearance values](https://arxiv.org/html/2606.08555), and it runs [at 1 Hz for actions plus a 10 Hz residual corrector](https://arxiv.org/html/2606.08555). [No code link on the arXiv page](https://arxiv.org/abs/2606.08555).
- **FD-VLA (Feb 2026, ICRA 2026).** It [predicts a force token from vision and state, "without relying on physical force sensors," and claims this outperforms direct sensor measurements in physical experiments](https://arxiv.org/abs/2602.02142). The abstract gives no numbers.
- **Other force-aware 2026 work, seen in search only and not read:** [CAAT](https://arxiv.org/pdf/2608.01102), [ForceFlow](https://arxiv.org/pdf/2605.11048), [PSR](https://arxiv.org/pdf/2609.21753), [Opt2VLA (humanoid)](https://arxiv.org/abs/2609.23968v1), [FWBC-VLA](https://arxiv.org/pdf/2609.03889).
- **Panda wrench access.** libfranka exposes [`O_F_ext_hat_K` and `K_F_ext_hat_K`, estimated external wrenches, and `tau_ext_hat_filtered`, the error between measured and model-expected joint torques; wrenches report zero near singularities](https://frankarobotics.github.io/libfranka/0.15.0/structfranka_1_1RobotState.html).

### Inferences
- The evidence favours adding force for contact-rich tasks, but "tight-tolerance" is largely untested: gear assembly (ForceVLA2) is the nearest task, and no paper reports a clearance. This does not settle the project's millimetre-precision question.
- The Panda's model-based wrench estimate differs from the 120–300 Hz external sensors used in these papers, so results may not transfer. TA-VLA's joint-torque input is the closest match, and it scored 63.75% in FAWAM's table.
- FD-VLA is a real complication for the touch thesis: if a force token predicted from vision works as well as or better than sensing, force sensing may matter less at inference than at training time. It is one abstract with no numbers I could check.
- The 8×A100 training runs mean force-aware VLAs of this size are cloud-only for this project.

### Gaps
- No independent evaluation of any force-aware VLA found.
- No open-weight, force-accepting VLA confirmed. No study on the Panda's internal wrench estimate as VLA input.

## Independent evaluations: precision, stacking, novel settings

### Takeaway
Independent results land far below vendor headlines. Real Franka and low-cost-arm evaluations put the best open VLAs around 0.5 on easy tasks, with stacking at 0–0.4, and even the best is about 7× slower than human teleoperation. SmolVLA shows the largest gap between its authors' number and independent ones.

### Cited Findings
- **PhAIL (28 May 2026; single author; FR3 + Robotiq 2F-85, bin-to-bin picking, 449 episodes, default recipes, N ≤ 30 per cell).** Human-relative throughput [π0.5 13.8%, GR00T N1.6 13.3%, ACT 10.5%, SmolVLA 6.4%](https://arxiv.org/html/2605.29710). Intervention rates are [4.2%, 4.2%, 2.0%, 18.6%](https://arxiv.org/html/2605.29710). Best VLA is ["roughly 7× slower than the human"](https://arxiv.org/html/2605.29710). Completion by configuration: [GR00T 57.8/35.6%, OpenPI 51.1/45.0%, ACT 35.6/31.6%, SmolVLA 12.1/9.5%](https://arxiv.org/html/2605.29710). [π0.5 vs GR00T stayed statistically unresolved](https://arxiv.org/abs/2605.29710).
- **VLA-REPLICA (20 May 2026; SO-101, RealSense top + wrist webcam, 50 demos per task).** ID / OOD average: [π0.5 0.54 / 0.35; π0 0.34 / 0.30; SmolVLA 0.26 / 0.30; ACT 0.18 / 0.075; X-VLA 0.14 / 0.075](https://arxiv.org/html/2605.20774). **Stacking (ID): only [π0.5 0.4 and SmolVLA 0.2 succeeded at all; every other model scored 0](https://arxiv.org/html/2605.20774).** No insertion task. [Rebuilt setups reproduced within a few points (0.49 vs 0.48 ID)](https://arxiv.org/html/2605.20774).
- **RobotArena ∞ (ICLR 2026).** Crowdsourced evaluation of six VLAs; [performance drops substantially on environments from datasets a policy was not trained on](https://proceedings.iclr.cc/paper_files/paper/2026/file/2aa3da3c1463ee2cdaaee94be4f8ba3f-Paper-Conference.pdf) (search summary; I did not read the full PDF).
- **Hobbyist SO-101 (Apr 2026).** [SmolVLA ~30% (3/10), π0.5 ~30%, ACT ~40% on apple-in-bowl at 50 demos; grasping the dominant failure, rarely recovered](https://justintiensmith.github.io/vla-research/2026/04/10/are-vlas-overhyped-first-results-on-a-real-robot.html).
- **Developer-run but unflattering.** On LingBot-VLA's GM-100 (130 demos/task), [WALL-OSS 4.05%, GR00T N1.6 7.59%, π0.5 13.02%, LingBot-VLA 15.74–17.30%](https://arxiv.org/html/2601.18692v1).
- **Vendor comparison points.** SmolVLA's own paper is [reported at 78.3% real-world vs π0 61.7% and ACT 48.3% (via a Medium summary, primary table not read)](https://medium.com/@deepkarkada/smolvla-compact-vla-for-robot-policy-b30604ac23c5). Evo-1's authors report [78% vs π0 73%, SmolVLA 50%, OpenVLA-OFT 55%, on xArm6](https://www.alphaxiv.org/abs/2511.04555.md).
- **On benchmarks.** Reuss (Oct 2025) calls [LIBERO "basically solved" and notes open-weight VLAs still trail closed frontier models in zero-shot behaviour](https://mbreuss.github.io/blog_post_iclr_26_vla.html). V-JEPA Policy's real tests show [PRTS 100/95%, π0.5 85/50%](https://arxiv.org/html/2609.37250v1), again authors' numbers.
- **GEN-1.5 update.** Coverage found ([eWeek](https://www.eweek.com/news/generalist-gen-1-5-robot-one-shot-learning/), [remio](https://www.remio.ai/post/generalist-ai-says-gen-1-5-learns-robot-tasks-from-one-demo), [humanoid.guide](https://humanoid.guide/generalist-unveils-gen-1-5-for-one-shot-physical-learning/)) repeats company numbers. I found no independent evaluation or replication as of this date, and the search summary reported no public weights or API (not checked outlet by outlet).

### Inferences
- PhAIL and VLA-REPLICA measure different things (throughput vs binary success), so do not compare raw values across them. The consistent signal is ordering (π0.5 at or near top, SmolVLA low on Franka) and low absolute numbers.
- SmolVLA's Franka result may partly reflect a base model trained on small-arm community data. PhAIL used default recipes, so it is a floor for what careful tuning achieves, not a ceiling.
- VLA-REPLICA's stacking values fall in 0.2 increments, which implies around five trials per task. Treat them as coarse.
- The pattern matches the notebook's #158 precision cliff: forgiving placement works, exact placement collapses. Stacking success at 50 demos remains below 50% even for the best open model.

### Gaps
- No independent test of VLAs on millimetre-scale insertion found.
- No independent real-robot evaluation of MolmoAct2, GR00T N1.7 or Xiaomi-Robotics-0 found. PhAIL's GR00T is N1.6.
- [FailBench](https://arxiv.org/pdf/2609.03611) and a [July 2026 critique arguing no VLA has published an independent autonomous success rate](https://uthandonethemba.com/2026/07/17/embodied-part2-vla-models/) were seen but not read in full; the second is opinion.

## Can small fine-tuned VLAs be the reflex layer under an LLM orchestrator?

### Takeaway
Yes in architecture, with real-robot evidence on Franka and ALOHA (authors' own). The main hazard is that narrow fine-tuning can destroy the steerability a hierarchy depends on. Failure signals from VLAs are maturing, but nothing I read closes the full loop of surprise signal to deliberation to compressed reflex.

### Cited Findings
- **Google DeepMind Hi-VLA study (Jun 2026, v2 Sep 2026).** [Flat VLA 69.63 / 25.30 / 50.90 and best hierarchy 78.22 / 67.08 / 80.89 on short / long / reasoning; naive hierarchy 69.57 / 40.56 / 66.49; real ALOHA 12/15 vs 9/15 vs 3/15](https://arxiv.org/html/2606.10267v2). Main low-level policy is [Gemini Robotics On-Device 3B](https://arxiv.org/html/2606.10267v2), closed.
- **Steerability warning.** [π0.5 falls from 73.3 to 32.0 under narrow single-instruction fine-tuning in the hierarchy; sim-fine-tuned GROD-1B falls to 7.5 on long-horizon](https://arxiv.org/html/2606.10267v2). The paper calls this ["very critical for maintaining good hierarchical performance"](https://arxiv.org/html/2606.10267v2).
- **Other Hi-VLA design results.** [VLM size matters little (Qwen3-VL 4B / 8B / 32B: 76.0 / 70.7 / 73.3), while thinking helps](https://arxiv.org/html/2606.10267v2). [Execution horizon of ~4–8 s recommended; success-detector termination beats VLM-judged (57.39 vs 43.50 on long-horizon)](https://arxiv.org/html/2606.10267v2). [Cross-episode memory summaries help (60.00 vs 52.36 long; 80.30 vs 72.62 reasoning), in-episode summaries neutral to negative](https://arxiv.org/html/2606.10267v2). [Adding contact information moved long-horizon from 47.90 (bounding boxes) to 52.36](https://arxiv.org/html/2606.10267v2).
- **TAPT tool-aligned VLAs (SJTU; May 2026, v2 Oct 2026).** A VLM calls [bounded VLA tools with tool-family labels on residual adapters; FR3 with one fixed D435, 30 demos per task; real-world success 16.7% → 58.3% over standard π0.5](https://arxiv.org/html/2605.13119v2). The VLA [emits a continuous progress signal at 2 Hz; a monitor requests replanning when progress stagnates](https://arxiv.org/html/2605.13119v2). [Replan success was 61.4% for π0.5](https://arxiv.org/html/2605.13119v2). It tests [OpenVLA, OpenVLA-OFT and π0.5, not SmolVLA](https://arxiv.org/html/2605.13119v2).
- **ART (Aug 2026).** [Two-stage LoRA fine-tuning to decouple tool reasoning from VLA core skills, tested in LIBERO and real settings](https://arxiv.org/pdf/2608.14047) (snippet only).
- **World Action Agent (24 Sep 2026, work in progress).** A VLM harness with action rehearsal reports [75.6% on LIBERO-Pro; fine-tuning Qwen3.5-9B on harness traces raised out-of-domain success from 1.7% to 43.3%; simulation only, no code listed](https://arxiv.org/abs/2609.29964). This is the closest published analogue I found to deliberation compressing into a fine-tuned model.
- **FAEA (Jan 2026).** Claude Agent SDK control reaches [84.9%, 85.7% and 96% on LIBERO, ManiSkill3 and MetaWorld, with privileged state in simulation](https://arxiv.org/html/2601.20334v1).

**Failure and uncertainty signals from VLAs**
- [PFD perturbation ensemble on π0.5: AUROC 0.866 on LIBERO-PRO vs ACE 0.844, Sampling 0.814; on a real TM5-900, RND 0.948 and PFD 0.862, on an RTX 3090 with K=5 variants, runtime unreported](https://arxiv.org/html/2606.20754v2).
- [Hide-and-Seek (ICML 2026): tested on OpenVLA, π0 and π0.5; reported ~13% higher accuracy than a VLM monitor at over 2,000× speed](https://arxiv.org/pdf/2605.30834) (search summary).
- [SAFE: latent-feature failure detector with conformal thresholds](https://arxiv.org/html/2506.09937v1). [FailureSpot: failures appear as abnormal patterns in predicted action chunks](https://arxiv.org/html/2609.04277v1). [SAFECAST](https://www.alphaxiv.org/abs/2608.04246) (search only).
- **Actual "prediction vs observation" work.** [A VAE-style world model trained on nominal data uses variance and forecast-vs-actual error, with conformal thresholds, and reportedly beats five baselines on bimanual cable data](https://arxiv.org/html/2603.06987v1) (snippet). [OWPA, a Nov 2026 unreviewed preprint, pairs a VLA with an LLM-written world model that is repaired when predictions fail](https://www.preprints.org/manuscript/202611.0227) (snippet).

### Inferences
- The hierarchical architecture has its best support where Hebbot needs it, in long-horizon and reasoning-heavy work. At short horizons the benefit is small (naive hierarchy equals flat).
- **Narrow fine-tuning for one reflex may conflict with an LLM steering it through language.** Bounded, fixed-instruction tools (TAPT's approach) are one way out: the LLM picks which skill, the skill does not need broad language following.
- SmolVLA as the reflex layer is untested in this role. Given PhAIL, the realistic local path may be π0.5 inference with cloud fine-tunes, or ACT/Diffusion per skill locally.
- TAPT's progress signal and the PFD/RND-style monitors could supply a surprise-like signal. Note K=5 forward passes on a 16 GB card multiply latency; a lightweight probe such as Hide-and-Seek seems a better fit.
- The DeepMind memory result (cross-episode helps, in-episode does not) lines up with Hebbot's episode-store design, on tabletop tasks only.

### Gaps
- No paper found uses SmolVLA as an LLM-called tool.
- No study connects VLA surprise signals to an LLM that then writes or promotes a new reflex. [VASO](https://arxiv.org/pdf/2606.05395) and [Teach and Grow](https://arxiv.org/pdf/2608.17209) look relevant from titles but were not read.

## Strongest 2026 arguments: pure end-to-end scaling versus hierarchical/hybrid

### Takeaway
The best head-to-head (DeepMind, one lab, same stack) favours hierarchy strongly for long-horizon and reasoning tasks and weakly for short ones. Pure end-to-end's best evidence is scaling results (RoboArena's top WAM, GEN-1.5) that are not head-to-head with hierarchies. The notion that deliberation compresses into reflexes has almost no direct test either way.

### Cited Findings

**For end-to-end / scaling**
- [DreamZero, a 14B WAM trained only on DROID, led RoboArena in April 2026 at 1750 vs Pi-0.5 1622, Pi-FAST 1592, Pi-0 1475](https://developer.nvidia.com/blog/pretrained-to-imagine-fine-tuned-to-act-the-rise-of-world-action-models/) (NVIDIA blog by a researcher in the field).
- [mimic robotics argues handcrafted modular pipelines will lose to one model mapping images and sensor readings to actions](https://www.mimicrobotics.com/blog/robotics-in-the-era-of-the-scaling-hypothesis) (company position).
- [EMERGE-Policy cites Cosmos Policy at 98.2% on one benchmark column and explicitly notes comparison with an end-to-end policy does not isolate the value of cognitive decomposition](https://arxiv.org/pdf/2608.29896) (snippet).
- GEN-1.5's challenge stands as recorded in notebook #157; see the independence note above. Reuss notes [closed models lead on zero-shot, with far larger fleets and funding](https://mbreuss.github.io/blog_post_iclr_26_vla.html) (Oct 2025).

**For hierarchical / hybrid**
- [DeepMind's best hierarchy beats flat by about 42 points long-horizon and 30 reasoning, with 12/15 vs 3/15 on real ALOHA](https://arxiv.org/html/2606.10267v2). Its [Appendix B uses a scripted privileged low-level policy as a proxy for a future VLA: full hierarchy ~95%, ablations near 0%](https://arxiv.org/html/2606.10267v2). This is the paper's direct argument that "hierarchy still matters as VLAs improve," but the proxy is scripted, not learned.
- [LIBERO cross-stack: best hierarchy 83.8 vs flat 43.3](https://arxiv.org/html/2606.10267v2).
- [TAPT: 16.7% → 58.3% on real FR3](https://arxiv.org/html/2605.13119v2). [WAA claims it beats end-to-end VLAs on LIBERO-Pro; no per-baseline numbers in the abstract](https://arxiv.org/abs/2609.29964).
- [Rego argues scaling improves coverage without changing structure, and reports an ICRA debate participant concluding data is "necessary but not sufficient"](https://gerardrego.substack.com/p/data-is-not-all-you-need-robotics) (opinion). [BVP concedes video world models drift over long horizons](https://www.bvp.com/atlas/can-world-models-unlock-general-purpose-robotics) (investor piece).

**Blurring the line.** [π0.7 uses a BAGEL-based subgoal world model](https://developer.nvidia.com/blog/pretrained-to-imagine-fine-tuned-to-act-the-rise-of-world-action-models/), and [hybrids such as HiRoC pair planner-executor hierarchy with RL post-training and list end-to-end training as future work](https://arxiv.org/html/2608.05999v1).

### Inferences
- The end-to-end trajectory undermines one version of the premise and not another. If the premise is "a flat policy cannot do it," short-horizon results and scaling trends weaken that. If it is "layered systems do better at long-horizon, verifiable, and recoverable work," the best 2026 data support it.
- The compression thesis itself, that deliberate cognition is progressively converted into reflex, remains essentially untested. WAA's fine-tune on harness traces (simulated, work in progress) is the only direct gesture at it I found. Hebbot would be producing primary evidence here.
- Most hierarchical wins are by the authors of the hierarchy. The DeepMind study is the strongest because it benchmarks on one stack, but the low-level VLA was fixed at each comparison; it does not test a future flat model trained at much larger scale.
- A fair summary for the notebook: scaling is winning at the skill level, and hierarchy is winning at the task level. A reflex layer that improves through scaling is compatible with both.

### Gaps
- No head-to-head of a hierarchical agent against a frontier-scale flat model (π0.7, GEN-1.5) found.
- No 2026 essay found that settles the debate; the sharpest public arguments are company or opinion pieces.

## Action-conditioned world models on modest hardware

### Takeaway
V-JEPA Policy (4.66 GiB) is the only WAM-class model I found with measured memory well under 16 GB. Video-generation world models at the 5B scale and above are not realistic for real-time surprise here. A small nominal-only predictive monitor is the most achievable route, and using prediction error as surprise remains mostly unvalidated on these specific models.

### Cited Findings
- **V-JEPA Policy (29 Sep 2026).** [~0.9B params; on RTX 4090, three views, batch 1: V-JEPA Policy 4.66 GiB and 178 ms, FastWAM 12.74 GiB and 203 ms, PRTS 9.96 GiB and 116 ms, π0.5 8.87 GiB and 159 ms](https://arxiv.org/html/2609.37250v1). [LIBERO-Plus with pretrained predictor 91.5 vs π0.5 80.7; real-world 75/85% vs π0.5 85/50% and PRTS 100/95%](https://arxiv.org/html/2609.37250v1). [Code at github.com/breez3young/VJEPA-Policy; weights not explicitly mentioned; prediction error as a surprise signal not evaluated](https://arxiv.org/html/2609.37250v1).
- **V-JEPA 2-AC (2025).** [About 300M action-conditioned predictor; 16 s per action for planning with 800 samples on an RTX 4090, vs 4 min for Cosmos with 80](https://www.alphaxiv.org/abs/2506.09985) (via search summary of the paper; planning time, not per-step prediction latency). [A third-party issue lists a released 11.76 GB checkpoint](https://github.com/Sino-Huang/NovPhy/issues/138) (not official).
- **Fast-WAM.** [590–800 ms per action chunk with video generation, 3–4× slower than Pi-0.5](https://developer.nvidia.com/blog/pretrained-to-imagine-fine-tuned-to-act-the-rise-of-world-action-models/).
- **Boundless-World-Model (May 2026).** [Wan2.2-TI2V-5B fine-tune with a 14-dim EE action encoder, weights at huggingface.co/BLM-Lab/Boundless-World-Model, 57-frame output chunks, validated on H200, VRAM and licence not stated, described as 1st among open-source on WorldArena tracks](https://github.com/vllm-project/vllm-omni/issues/4903).
- **Others.** [OpenWAM pairs a 5B video expert with a 2B action expert; pretrained video weights downloadable](https://openwam.stanford.edu/). [JEPA-WAM's full inference stack runs on an RTX 5090](https://arxiv.org/pdf/2609.20277). [Ctrl-World (ICLR 2026) has frame-level action conditioning](https://proceedings.iclr.cc/paper_files/paper/2026/file/0ae94013da7cd459402fd77874e09ee3-Paper-Conference.pdf); openness unconfirmed.
- Reuss notes WAM priors ["often trade robot-data efficiency for compute cost"](https://developer.nvidia.com/blog/pretrained-to-imagine-fine-tuned-to-act-the-rise-of-world-action-models/).

### Inferences
- For prediction or surprise at usable rates on 16 GB, candidates in order: a small custom nominal-only predictor in the manner of the VAE monitor; V-JEPA Policy's predictor; V-JEPA 2-AC for slow, off-loop checking. Wan-scale models would be offline at best.
- These are memory-and-latency estimates by extrapolation from RTX 4090s; the 5060 Ti has less throughput.

### Gaps
- No VRAM figure found for BWM or V-JEPA 2-AC on consumer GPUs.
- Efficient-WAM (listed as ~1B parameters) and Light-WAM appeared only as titles inside search-result reference lists; I could not locate or verify either.
- No study was found that compares prediction-error monitors with hidden-state probes on the same VLA.
