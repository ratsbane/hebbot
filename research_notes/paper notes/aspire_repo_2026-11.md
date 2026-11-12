# Reading note: ASPIRE (NVlabs) — skills as the trained artifact, from source

NVIDIA GEAR lab. Repo cloned by Doug to ~/git/ASPIRE, 2026-11-12; v1.0 (Oct 2026);
Apache 2.0 with NVIDIA NOTICE. Read date 2026-11-12, **[read from source]**: README,
AGENTS.md, `.claude/skills/` (iterative-debugging, evosearch), `src/cap_harness/`
(agent, program catalog, yam_real layout), docs index. Paper is arXiv 2607.00272,
already covered in the 2026 literature review; this note is about what the *code*
adds to that. The earlier review's line — "skills were learned in simulation and
verified only once at admission" — is now inspectable.

## What it is

"Training" is skill refinement by coding agents instead of gradient descent; the
trained model is a repo of sensorimotor programs. Claimed results: 99% on 80
LIBERO-Pro tasks plus Robosuite/BEHAVIOR-1K selections (v1.0), sim-to-real table
bussing and a Rubik's cube on a real bimanual YAM, with Claude Opus 4.6 / GPT-6
Astra as System 2 and an explicit success-vs-API-cost scaling panel ("stronger
models discover better strategies"). The learning loops ship *as agent skills*
(`.claude/` and `.codex/` both present) — the methodology is literally a set of
SKILL.md files a coding agent follows.

## The machinery (the part the paper compressed)

1. **CAP harness**: System 2 acts only through registered `mcp__cap__*` tools.
   `CapProgramCatalog` is a frozen, reviewed program set — each program carries its
   sha256 and a `moves_robot` flag, and "remote callers cannot supply code or
   paths". The AGENTS.md is a production operating contract: motion authorization
   semantics, "smallest reliable unit of execution", one startup home, skills
   loaded once and not reread.
2. **The fix loop** (`iterative-debugging`): per task — inspect one scene, generate
   task-level code, debug on development seeds *by reading recorded run artifacts*
   (debugging organized by failure mode, not by seed), measure once on unseen-check
   seeds, then a coordinator-run held-out evaluation on seeds the workers never
   touched. The coordinator is also the curriculum, and writes its choices down.
3. **The verifier gate**: when a worker proposes skills for the library, a separate
   verifier agent re-solves the task *from the skill library and the proposals
   alone — without the proposer's code — within two attempts*. Only `verified`
   proposals are promoted. There is also a phase-3 sweep: old sub-threshold tasks
   are re-run against the final library, results kept beside the originals.
4. **evosearch**: for tasks the fix loop can't crack, K=8 candidate evolutionary
   search on development seeds, validation selection, held-out scoring; the fix
   loop's best program is seeded verbatim as candidate A.
5. **Backends/providers**: LIBERO-Pro, Robosuite, BEHAVIOR-1K sims; SAM3,
   GraspNet, cuRobo, pyroki as bootstrap-scripted services; real robot is a
   bimanual YAM behind the same CAP tool surface.

## Relevance to Hebbot

1. **This is Hebbot's architecture at industrial scale**, independently converged:
   skills as records, an LLM orchestrator over a guarded tool surface, promotion by
   evidence, run artifacts as the debugging substrate. Even the operating rules in
   AGENTS.md are a production edition of the conventions this bench evolved
   conversationally (per-run motion authorization, startup homing, skill loading).
2. **The verifier gate is portable today** and is the sharpest piece: promotion
   evidence must come from a *fresh context* reproducing success from the record
   alone. Hebbot translation: a skill's evidence is dispatcher runs launched from
   registered defaults with no discovery-session context, bound to the exact code
   hash that ran (`policy_version`, which the substrate already stamps) and to a
   declared hardware tag — so a source edit or bracket change expires evidence
   instead of letting stale proof vouch for new behaviour. That last part is *our*
   addition; the lit review flagged evidence-expiry as ASPIRE's own stated
   limitation. Implemented as `substrate/verification.py` the same day as this note.
3. **Dev/held-out discipline, named.** Their workers never grade on the seeds they
   debugged. Our analogue: the sim bench (pytest worlds) is the development set,
   hardware campaigns are the held-out set, and the campaign ledgers in `runs/` are
   the equivalent of their RUN_ROOT artifacts. Worth keeping deliberate rather than
   accidental.
4. **evosearch wants a bench sim.** Multi-candidate code search needs cheap
   rollouts; our MuJoCo-Playground-on-16GB plan (DLO note) is the missing
   prerequisite. When a bench MJX model exists, evosearch is the ready-made shape
   for discovering a yaw-robust grasp or the shove skill.
5. **SAM3 as a bootstrap-scripted local service** is one evening of adaptation and
   addresses the known "red is invisible to colour-blob logic" gap.
6. **Not worth adopting**: the CAP harness itself (our substrate/MCP server fills
   that role and speaks panda_py) and the YAM real stack (different robot and
   transport). "Implement ASPIRE on our own hardware" — the protocol, not the
   harness — is on the TODO list.

## Derived / caveats

- Benchmark numbers are the README's own claims, **[not independently verified]**;
  the videos are demonstrations, not statistics.
- The verifier runs on the *first few development seeds*, i.e. verification is
  cheaper than discovery but still in-distribution; held-out generalization is
  established separately by the coordinator. Hebbot's hardware verification
  collapses these into one (there is no seed to overfit — the bench is the
  distribution), which is simpler but means our gate's run count carries the whole
  statistical load (Wilson intervals, per the DLO note).
- Their fresh-verifier independence is agent-level (no proposer code or context).
  For a classical Hebbot skill the code *is* the skill, so independence reduces to
  default-params, unattended, context-free invocation — weaker than theirs for
  LLM-written skills; revisit when Hebbot promotes its first composition authored
  by the orchestrator.
