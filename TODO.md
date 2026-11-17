# TODO

Standing queue. An item leaves this list by being done or by being written off in
the lab notebook; bench-forced items jump the order.

- **Implement ASPIRE on our own hardware** (added 2026-11-12). The protocol, not
  the harness: campaign layout with dev/held-out discipline, coordinator-as-
  curriculum, verifier-gated skill promotion, and eventually evosearch-style
  candidate search once a bench sim exists. Clone at `~/git/ASPIRE` (Apache 2.0);
  reading note in `research_notes/paper notes/aspire_repo_2026-11.md`. The
  verifier gate itself landed 2026-11-12 (`substrate/verification.py`).
- **Wrist camera: rigid bracket for the D405** (Doug, 2026-11-17; a fixed bracket
  without the swivel is available for either camera; recommended the D405 on
  the wrist — close-range depth is its job, the D435 is blind under 28 cm — and
  the D435 stays as the scene camera; strain-relieve the cable). Commissioning the rigid mount, in order: delete the correction
  file; `level_check.py` at survey height (the table-plane levelling must
  report leveled with a small tilt); `touch_calibrate.py` to set the translation
  from touch truth; then the round trip and the campaign. Until then the D405 wobbles ~2 cm between looks; the grasp approach retries
  shifted along the finger axis to live with it. Drop surveys to ~0.27 m.
  Software landed today for this: table-plane levelling in every look (RANSAC
  seed + refits, BLAS-free), the persistent hand-taught translation with drift
  flag, touch probing, the two-camera cross-check. Still to do: lower the carry
  speed during campaigns; a touch-find port so picks need the eye only as a seed.
- **Dreaming cycle: distil practised stages into learned reflexes** (planned
  2026-11-16, discussed and amended 2026-11-17; both notebook entries). Status
  2026-11-17 midday: (1) DONE — `FrankaEpisodeRecorder` in ASPIRE franka_real
  (arm 1 kHz from panda_py's log, cameras 20 Hz mp4 + PNG16 depth, gripper,
  events; 517cb56) and hebbot `scripts/franka_raw_to_lerobot.py` (state = q +
  gripper, action = next sample's; clock-pair scaling); first episode converted
  on the Spark. Known: the sampler runs ~12 Hz because encoding sits in the
  loop — queue the writes. (2) PAUSED 19:10 for a hand: the standing lime block sits in the row beside
  green and the wrist camera passes ~1.5 cm from it on every approach to blue
  (run 97 stopped on it); Doug to move the lime away, then relaunch
  `/tmp/campaign.sh ... <seed> 4` (no touch calibration; the placements teach
  the eye; the launcher's copy is `scripts/franka_campaign.sh`). Clean 6/6 runs
  so far: 7, 8, 9, 57; bracket tightened 17:50, camera tilt now 3° and steady; `scripts/dream.sh` on the Spark is the nightly job (waits for
  the GPU, rebuilds the dataset, trains ACT, dated checkpoint); omen pushes every run to the Spark's `~/episodes/` itself after each run
  (`scripts/franka_sync_episodes.sh`; the Spark cannot reach omen behind the
  mesh router, so all transfers are omen-initiated). ~50 verified to try training for real, hundreds to
  expect anything. (3) NEXT — nightly ACT on the Spark (`~/venvs/lerobot`,
  smoke-tested), Diffusion Policy second, SmolVLA/π0 later; checkpoints to
  omen for inference; `descend_to_contact` as an action. (4) morning paired
  gate, policy vs program on ≥ 20 scenes, no safety events, program kept as
  fallback; promoted bodies only are visible to the orchestrator. (5) monitored
  policy runs record the program's recoveries as corrections. (6) the
  deliberation-cost curve across the promotion event. Also chase the 11–14 mm
  x-bias every round-trip placement shows.
- **Keep the Panda, omen and the Spark busy** (Doug, 2026-11-17): the cycle is a
  standing schedule, not an experiment. Panda: self-resetting practice campaigns.
  omen: harness + perception for them, and between campaigns a MuJoCo bench twin
  so evosearch-style candidate search has somewhere to run. Spark: nightly
  dreams, and its local Qwen3 27B as a cheap System 2 for routine monitored runs
  (which also makes the deliberation-cost curve cheap to draw). Standing
  authorization for unattended arm time given 2026-11-17 (notebook); the bounds
  are now engineering to build: workspace box and force limits (exist), watchdog
  that homes and stops on any reflex or verifier anomaly, scene sanity check per
  episode.
- **omen must not sleep** (2026-11-17: found asleep when the first unattended-
  capable run was due; Doug woke it by hand). Mask the systemd sleep/suspend
  targets and check the GNOME power settings, then confirm with an overnight
  ssh probe. A host that naps cannot keep the Panda busy.
- **Spark workstation as the training host** (2026-11-17; ssh `spark`,
  192.168.1.68, GB10, 121 GB unified, 2 TB free, torch 2.10+cu130; a Qwen3 27B
  sglang server holds 81 GB). First: the aarch64 environment — LeRobot (ACT, DP,
  SmolVLA), openpi, flash-attention via wheels or an NGC container; that decides
  which bodies are practical there. Then: episode sync from the Franka host, a
  dream job that runs when the sglang server is idle (37 GB fits up to π0.5 LoRA;
  pause the container for GR00T/MolmoAct2), checkpoint shipping to omen.
- **Upstream PRs to ASPIRE**: #3 (silhouette depth-bleed trim) is open as
  NVlabs/ASPIRE#41 (2026-11-15) with live numbers in a comment. #2 (probe-verified
  check_success) is built and live-validated on franka_real (4ee885d, 2026-11-13)
  but lives inside the embodiment port; extracting it needs the two release-asset
  doc checks (embodiment guide, API reference) satisfied first. #4, new: the
  contact-aware place (`franka_real.descend_to_contact`, b875103) — the bench's
  rate trigger ported; validate live before offering it. #1, evidence-expiry-on-
  change (fingerprint-bound verification), which their paper lists as a
  limitation and our substrate implements, is still desk work.
- **Live-validate `descend_to_contact`** (2026-11-15): bare-hand descent done
  2026-11-17 — contact at z = 0.0128, 3/3 within 0.1 mm, after fixing the floor
  to bound the hand rather than the command (notebook). Round trip done the
  same morning: `round_trip.py`, blue A↔B ×3, 6/6 verified, every place by
  contact, with an 11–14 mm x-bias to chase. Next: the clutter pick and the
  wall once the basket is replaced.
- **Replace the basket with a low-rimmed container** (decided 2026-11-16): the
  Franka Hand body is far wider than the finger span and bumped the 100 mm wall
  from 9 mm away. Options (grasp-axis choice, lateral back-off, tilted approach,
  raking, basket collision geometry) are written up in the notebook but parked
  as off-track. Spec: rim ≤ ~60 mm (ideally ≤ 40) so the body straddles the
  wall at grasp depth; interior size then stops mattering. Then re-survey the
  goal's wall lines/rim (no motion), collapse the `wall_clear` gate to finger
  clearance, and reword pick-from-clutter's TASK_CONTEXT item 2.
- **EE_LIMITS x 0.60 → ~0.65** as its own tested change (deferred from the r7
  refusal and the campaign freeze).
- **SAM3 into hebbot's own perception** — the service now runs on omen
  (2026-11-13, port 8114, 4.5 GiB VRAM) feeding ASPIRE's localize_object, and
  found red 10 cm from its told position (first camera fix ever; recorded
  ±12 mm). Remaining: a hebbot-side provider so touch_look and the camera map
  can use it, and a confirm probe for red next arm session.
- **Yaw-dependent place height, pile side** (from m1's new |yaw| axis): contact
  reads higher with |yaw| in both legs (r ≈ +0.8) but only ~1 mm on the table vs
  0.1–4.7 mm on the pile, ~0.03 mm/deg. Inside the 8 mm verify window; watch,
  and suspect the held block's ride in the jaws at rotated yaw.
- **Memory-grasp alignment tail** (~3 air-closes in ~30 pile grasps, each one
  shoving the block off the pile): widen open_width or add a camera fallback
  if it holds above ~10%. The look-recovery absorbs it today.
- **probe_table still uses the absolute 10 mm lag trigger** and phantom-trips at
  rotated-yaw near-base reach (y3's −x side). Port the descent-rate trigger to
  it when probing needs that region. (The place-side pathology is fixed:
  2026-11-13's rate trigger, validated at the (0.37, 0.0) crime scene.)
- **Scene camera, hebbot side**: the D435 is mounted, calibrated (3.5 mm
  landmark residuals; pose in ASPIRE's configs/franka/franka-bench.json) and
  serving ASPIRE observations at 13.5 fps over USB2 (2026-11-13). Remaining:
  attach it to hebbot's own look/memory for scene-wide disturbance
  attribution, and a proper SS cable doubles the rate. Known limit: oblique
  localization smears by silhouette depth-bleed; trust it for video and
  attribution, not grasp targets.
- Queued-until-forced (see lab notebook): negative evidence on failed locate,
  anisotropic told error bars, `hinted` conflation, scene-wide disturbance
  attribution, relations/descriptions layer + local VLM as its sensor.
