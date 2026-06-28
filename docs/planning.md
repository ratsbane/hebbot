# Higher-level planning — a methodology for Hebbot

*Draft, 2026-06-28. How goals become behavior: the layer between a natural-language goal
("stack all the blocks") and the reflexive primitives that touch the world.*

## The gap this fills

We have a growing set of **primitives** (`find_blocks`, `vision_grasp`, `place_block`,
`stack_on`, `shove`, `probe_table`, `scene_sync`, `stand_on_end`) and a **world model**
(`scene_sync` — the persistent scene store with predict-then-look change detection). What's
missing is the connective tissue: a principled way to turn a *goal* into an ordered,
verified, failure-tolerant sequence of those primitives — and to do it *reproducibly* rather
than improvised in a chat each time.

Right now the planner is the agent (Claude) reasoning turn-by-turn in conversation. That
works, but it isn't reproducible, can't run unattended, and can't itself be measured or
improved. The methodology formalizes it.

## Four principles

**1. Plans are executable orchestration, not chat.** A plan is a Python function that operates
on the world model and composes primitives + sub-plans — the same "policy = executable Python"
shape as everything else here. `practice` is already our first plan (attempt → verify → reset →
loop). A goal like "stack all the blocks" becomes a `stack_all()` plan. Benefits: reproducible,
autonomous (runs unattended), debuggable, and **improvable** — the self-improvement loop then
applies to the *planning* layer, not just the primitives.

**2. Plans run on the world model.** `scene_sync` is the shared state. The plan loop is
**perceive → choose next action → act → verify (predict-then-look) → replan on surprise.** The
model is what lets a plan ask "are there unstacked blocks left?" without re-deriving the world,
and lets it notice when reality diverges from intent (a block slid, a stack toppled).

**3. The hierarchy is explicit, and compositions are first-class.**

```
Goal            "stack all the blocks"
  └─ Plan       stack_all()                 — orchestration over the scene model
       └─ Composed skill   grasp_in_clutter(block)   — assess clutter → shove → grasp
            └─ Primitive    find_blocks / shove / vision_grasp / place_block / ...
```

A **composed skill** sits between the plan and the primitives. "Grasp that block" is not a
primitive — it's *capture image → assess angle + nearby clutter → shove clutter aside → grasp*.
Naming these compositions explicitly is what makes the next principle possible.

**4. Reflexivization is the engine (the high↔low thesis).** When a composition proves reliable,
it *becomes a primitive* — the high layer stops planning it step-by-step and just calls the
reflex. That is literally "do it with high cognition, then make it reflexive." Planning is what
*discovers which compositions are worth reflexivizing*: a composition the plan invokes often and
that succeeds consistently is a candidate to harden into a primitive, which then simplifies every
plan above it.

**Corollary — failure-driven discovery.** You don't design the full plan up front. You draft it,
run it, and the failures surface the missing subgoals/primitives — exactly how `practice`
surfaced its own next work.

## Worked example: `stack_all`

The decomposition Doug sketched, with the primitive inventory it implies:

| Plan step | Needs | Status |
|---|---|---|
| 1. Photo from above; count + identify blocks | `scene_sync` (world model) | ✅ have |
| 2. Photo from the side; find already-stacked / standing blocks | a **scene-level side-view perception** | 🟡 only inside the grasp (tilt-glance); lift to scene level |
| 3. Select a base block / location to stack on | plan logic (reads the model) | ✅ in plan code |
| 4. Identify the next block to stack | plan logic | ✅ |
| 5. **Grasp that block** (composed) | clutter test → `shove` → `vision_grasp` | 🟡 pieces exist (`shove` new); needs the **grasp_in_clutter composition** |
| 6. Place the block on the stack | `stack_on`/`place_block` with growing `onto_top` | ✅ have |
| 7. If unstacked blocks remain, go to 4; verify each placement | `scene_sync` + plan loop | ✅ have |

### Skeleton (illustrative — not yet implemented)

```python
def stack_all(_context):
    scene = scene_sync()                       # 1. perceive from above
    side = scene_side_view()                   # 2. NEEDED: side perception (standing/stacked)
    base = choose_base(scene, side)            # 3. plan logic
    placed = [base]
    while unstacked(scene, placed):            # 7. loop
        b = choose_next(scene, placed)         # 4. plan logic
        ok = grasp_in_clutter(b, scene)        # 5. COMPOSITION: clutter test -> shove -> grasp
        if not ok:
            scene = scene_sync(); continue     #    replan on failure (predict-then-look)
        place_on_stack(b, base, height(placed))# 6. place with growing onto_top
        scene = scene_sync()                   #    verify: did it land + stay?
        if not verified(scene, b, base):
            continue                           #    a toppled/slid block re-enters the loop
        placed.append(b)
    return {"stacked": placed}
```

The skeleton makes the **gaps concrete** — they're the un-built calls:
`scene_side_view`, `grasp_in_clutter`, `choose_base/choose_next`, `verified`.

## Identified gaps → roadmap

Walking the plan names four targeted pieces (not a vague "make it smarter"):

1. **Scene-level side-view perception** — a primitive that tilts the wrist, captures, and reports
   which blocks are standing / already stacked (the tilt-glance, promoted from inside `vision_grasp`).
2. **`grasp_in_clutter` composition** — clutter test (is a same-region neighbor within Xmm?) →
   `shove` it apart → `vision_grasp`. `shove` was the missing primitive; built 2026-06-28.
3. **Reorientation** — lay-flat / stand-on-end so a block in the wrong pose can be stacked
   (partially in `stand_on_end`).
4. **Tower-stability verification** — confirm a placement *stayed* (predict-then-look already
   gives the mechanism; needs a height/coincidence check tuned for a growing stack).

## Why this matters beyond stacking

- **It's the third artifact in the ENPIRE-follow-on story.** ENPIRE shows a coding agent improving
  *primitives*; an agent-authored *goal planner* that's itself executable, measurable, and
  improvable extends self-improvement up a level. Combined with the open code + transcripts, that's
  a distinct contribution.
- **It operationalizes the thesis.** The high layer (plan) develops a process; reliable processes
  reflexivize into the low layer (primitives); the simplified high layer reaches for harder goals.
  Planning is the loop that drives that ratchet.

## How to start

Once `shove` / `grasp_in_clutter` are validated on hardware, draft `stack_all` as a real plan
(like `practice` but goal-directed), run it, and let it harvest the gaps above as failures —
the same failure-driven pattern that's served us throughout.
