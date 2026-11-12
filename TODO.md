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
- **Finger-line yaw offset measurement**: probe a known edge with the wrist at 0°
  and 90°, difference the answers. 6/6 grasp failures at yaw 74–86° vs ~37/37
  successes at yaw ≲ 11° (2026-11-12 campaign); camera exonerated to ~5 mm by
  probe line. If constant, it is one line of frame arithmetic in the grasp pose.
- **Run the verifier gate on hardware**: `verify_skill` for touch_stack /
  touch_unstack / touch_look on omen to mint the first `verified` skill records
  under a real hardware_tag.
- **EE_LIMITS x 0.60 → ~0.65** as its own tested change (deferred from the r7
  refusal and the campaign freeze).
- **SAM3 provider** (from the ASPIRE repo's bootstrap scripts) for segmentation —
  the "red is invisible to colour-blob logic" gap.
- Queued-until-forced (see lab notebook): negative evidence on failed locate,
  anisotropic told error bars, `hinted` conflation, scene-wide disturbance
  attribution, relations/descriptions layer + local VLM as its sensor, scene
  camera attachment, CONTACT_LAG_M margin review (+22.6 mm place in f5).
