# TODO

Standing queue. An item leaves this list by being done or by being written off in
the lab notebook; bench-forced items jump the order.

- **Implement ASPIRE on our own hardware** (added 2026-11-12). The protocol, not
  the harness: campaign layout with dev/held-out discipline, coordinator-as-
  curriculum, verifier-gated skill promotion, and eventually evosearch-style
  candidate search once a bench sim exists. Clone at `~/git/ASPIRE` (Apache 2.0);
  reading note in `research_notes/paper notes/aspire_repo_2026-11.md`. The
  verifier gate itself landed 2026-11-12 (`substrate/verification.py`).
- **Upstream PR to ASPIRE** when we have something proven: first candidate is
  evidence-expiry-on-change (fingerprint-bound verification), which their paper
  lists as a limitation and our substrate now implements. Prove it on our bench
  first, then port to their skill_report/verifier flow.
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
- Queued-until-forced (see lab notebook): negative evidence on failed locate,
  anisotropic told error bars, `hinted` conflation, scene-wide disturbance
  attribution, relations/descriptions layer + local VLM as its sensor, scene
  camera attachment.
