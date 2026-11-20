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
- **Paired gate: run it, then decide what the policy needs** (built 2026-11-18
  afternoon; see the notebook). Pieces: `scripts/policy_server.py` (lerobot venv,
  Spark or omen), `franka_real.policy_chunk` in ASPIRE, `learned_round_trip.py`,
  `scripts/franka_gate.sh` (`/tmp/gate.sh <seed0> <pairs>` on omen, with
  `CAP_HARNESS_POLICY_URL` pointing at the host), `scripts/paired_gate.py` for the
  tally. Still to do: a lerobot venv on omen (uv, Python 3.12) so the host is
  local; `producer` in the episode manifest so the converter can keep policy
  episodes out of the dream until they pass; per-stage labels so a dream can
  train on grasp or place alone; chunk execution is stop-go (0.5 s of motion,
  then a query), a streaming joint controller would smooth it.
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
- **Touch and other senses for the next policy version** (Doug, 2026-11-19
  afternoon, watching the arm pick and place: the VLAs we are looking at take
  vision, text and position, not touch; agreed to note it, not build it now).
  What the data has: the 6-axis wrench and gripper width every frame at 20 Hz;
  what it lacks: the program's own touch knowledge (contact descent stop reason,
  contact height, force rise, "already grasping"), which lives in the episode
  events and never reaches the LeRobot dataset. Plan, cheapest first: (1) derived
  per-frame channels in the state vector from the events: in-contact, held, force
  rise over the last frames, the program's phase; a converter change and a
  retrain. (2) Sensor summaries and the phase through the language channel,
  which doubles as the per-stage instruction. (3) A force-window encoder as its
  own modality, later. Sensors worth adding when we do: per-joint external
  torques (which link touches), gripper current or finger force (slip), a bench
  microphone (taps and slides), all recorded at 100 Hz and reduced per frame to
  max and rise. Architecture stance: keep the contact descent as a reflex
  primitive and let the policy choose when to call it and where; touch stays with
  the layer that measures it at 1 kHz.
  Addendum (Doug, 2026-11-19 evening): train the VLA to emit text beside
  actions, narration of what should be happening and, more useful, exceptions
  when sensing disagrees. The exception labels exist already: the program's own
  failure taxonomy in the episode events (air_close, pad on a neighbour,
  contact high, lengthwise, dropped, reflex, re-look disagrees with survey,
  standing where lying expected), each timestamped with the signals around it.
  Narration labels come from the phases and the touch events. Needs a VLA with
  a text head (not ACT), over-sampling of the rare exception moments, and a
  gate that scores every emitted claim against the recorded signal before an
  orchestrator acts on it. Mass estimates stay with the reflex layer (half a
  newton against a wrench estimate noisy at a newton): the model may request
  the measurement, not compute it.
- **A grasp yaw whose approach lands on the joint stops** (run 0445, 2026-11-19):
  the pick's yaw check (yaw_margin) solves the IK from the survey's seed, the
  approach itself is solved from the hover's, and for one yaw that put the base
  and wrist 0.04 rad from their stops, where the motion generator will not
  converge. The solver now prefers clear solutions within 2 rad of the seed
  (3ab65ba) but had none for that pose. Fix upstream: evaluate each candidate
  yaw's approach and carry from the pose the arm will actually be in (hover
  seed), or turn the hand 180 degrees when the approach is cramped (same grasp
  for a symmetric gripper, the other wrist solution).
- **(Resolved 2026-11-19 18:05: the controller.) A rising reflex rate loses the
  robot's packets.** A controller reboot took the rate from 9-13% of motions to
  zero (calibration 0438: 81 motions, none); omen had been healthy throughout and
  pinging the controller at 1 kHz showed nothing, because ICMP is answered by
  a network stack that was fine while the real-time side was late. Rule: a reflex
  rate climbing with omen healthy means reboot the controller; the per-run count
  of reflex_recovered events is the gauge. Original notes follow.
- **(superseded) A 40 ms stall somewhere in the harness process loses the robot's packets**
  (2026-11-19). Seven communication_constraints_violation reflexes in a day,
  command success 56-78% in the window, on a link that pings clean at 1 kHz idle
  and moving (max 3 ms). Not the NIC (own IRQ on CPU 12, no missed packets), not
  scheduling (control thread at FIFO 99 after ASPIRE 12d7dbf, still reflexed),
  not the episode sync (made blocking, still reflexed). A motion-only loop ran
  clean. Suspects: the recorder's writer thread (16-bit PNG in Python holds the
  GIL ~30 ms, 10 a second over two cameras) if panda_py's loop touches the GIL;
  the two RealSense streams on one USB host controller; Chrome on omen's desktop
  at a third of a core (the first reflex came twenty minutes after Doug sat
  down there). The env now recovers and resends a batch once (43ae409), so a
  reflex costs a half-second, not a run. To find it: count waypoints_retry events
  per run against what else ran; try a run with the recorder's depth PNGs off;
  try one with Chrome closed; perf the process on a reflex.
  Ruled out by 15:20 on the 19th: EEE (off, still two reflexes per hundred
  motions), CPU pinning to 8-11, performance governor and C2/C3 off, xhci
  interrupts moved to 13-14, the wire (1 Gb/s, no errors), the WiFi (-44 dBm).
  The runs now survive them on retries; the per-run count of reflex_recovered
  events is the number to watch. Timeline: zero reflexes until ~11:00 on the
  19th, then about two per hundred motions; at 11:00 the bracket was swapped,
  Also: two of the reflexes hung the harness in panda_py's automatic error
  recovery (9006 after a self-collision reflex, 0395 after a communication
  one); the gate now kills, parks and continues. Find whether the hang is
  panda_py's recovery thread or the harness's retry on top of it (a stack
  dump of a hung process: py-spy dump, or faulthandler on SIGUSR1).
  the D405 replugged, omen moved in its enclosure, and the desktop came alive.
- **The arm's reach near the base is set by the IK and the self-collision reflex,
  not the geometry** (2026-11-19). A straight-down hand at x 0.30-0.34, z 0.05-0.10
  is refused or aborted: calibration 0358 lost 20 of 25 probes at x 0.30-0.36, and
  reach_check.py (run 9006) had its low moves abort by reflex at x 0.30-0.32 and
  the run hang on a self_collision_avoidance_violation at 0.34 (the harness's
  automatic recovery did not return; the process had to be killed and the D405
  reset). Doug, watching: the IK is missing obvious solutions. The analytical
  solver sweeps q7 in the seed's branch and falls back to the home branch only
  when nothing is valid; a pose near the base may need the other elbow or a
  tilted hand, which no program asks for. The working band starts at x 0.40 until
  this is fixed; blocks nearer the base are moved by hand. A fix needs: the
  harness to survive a reflex (recover, then report), and an IK that offers every
  branch with a self-collision check, or a tilted-hand look and grasp for the
  base side.
- **The eye's two authorities disagree by 2-3 cm in base y** (found 2026-11-18
  morning, runs 0295-0315). Touch calibrations settle the wrist correction near
  base (0.01, -0.005); the placement lessons at weight 0.15 then walk it ~1 mm a
  leg toward (0, -0.03) and sit there, with per-leg residuals that never shrink
  (+8 to +15 mm base y, 25 legs after calibration 0308). Both states run clean
  (the straddle forgives 2.6 cm). What breaks is the walked correction being a
  31 mm hand-frame offset: rotated with a turned hand, or read from 14 cm
  instead of 33, the looks disagree by 3 cm (run 0315). Candidates: a TCP or
  pad offset along the finger axis that touch and grasp see differently; the
  table-plane levelling applied on about half the looks (leveled False /
  reused True alternate leg by leg, tilt 2-3 deg, 14 mm at survey height); the
  touch centroid quantised to 1 cm by the 2 cm grid. Decide which is right with
  a controlled test: one block, the same look from 33 and 14 cm and at two
  yaws, then a probe; and log the wrist RGB at every look (only depth is kept
  now). Until then the batch's calibration re-anchors the eye.
  Update 2026-11-18 night: the walk is faster than 1 mm a leg when the
  residuals all point one way (0351: ten residuals of +2-3 cm base y, the
  offset from 0.025 to 0.053 hand y in one run; 0353 then failed with the
  survey and the hover 6 cm apart). Placement lessons now report at weight
  zero (ASPIRE 29b4528); touch is the only authority. The held offset makes
  the controlled test cheap: the per-leg residuals of a batch, now
  uncorrupted by the walk, show directly whether they depend on the block's
  yaw (the grasp) or on the look height (a tilt).
- **Replace the basket with a low-rimmed container** (decided 2026-11-16): the
  Franka Hand body is far wider than the finger span and bumped the 100 mm wall
  from 9 mm away. Options (grasp-axis choice, lateral back-off, tilted approach,
  raking, basket collision geometry) are written up in the notebook but parked
  as off-track. Spec: rim ≤ ~60 mm (ideally ≤ 40) so the body straddles the
  wall at grasp depth; interior size then stops mattering. Then re-survey the
  goal's wall lines/rim (no motion), collapse the `wall_clear` gate to finger
  clearance, and reword pick-from-clutter's TASK_CONTEXT item 2. Doug
  (2026-11-17): the basket (or its replacement) may be shoved, from inside or
  outside, to wherever suits the task; a shove nearer the row shortens the
  carry and is a practice move in itself. Meanwhile `round_trip.py` treats
  y > 0.12 as the basket side and looks for nothing there (run 0219 reached
  for the blue block standing inside it).
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
