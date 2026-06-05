# Hebbot

*A substrate for agentic foundation models to bootstrap continuously-learning embodied systems.*

---

## What this is

Hebbot is a framework for interfacing the higher-level cognition of a large language model with the lower-level, autonomous, reflexive movements of an embodied system — a robot arm, today, and other hardware over time.

The thesis: skilled motor behavior is what happens when deliberate cognition gradually compresses into reflexive cognition, with bidirectional feedback between the two layers. Pianists, surgeons, and tennis players don't think about every motion they execute — but they *can*, and they take over from the reflexes the moment something surprising happens. Hebbot is the substrate that lets a foundation model direct an embodied system through that same arc: deliberate at first, reflexive after enough practice, override-able always.

The name honors [Donald Hebb](https://en.wikipedia.org/wiki/Donald_O._Hebb) — *neurons that fire together wire together* — the founding principle of how systems learn through repeated association. It also makes a *good hebbot* of practice.

## Status

Earliest days — this repo will accumulate as the substrate takes shape. First commits set up the project structure and document what was learned getting the hardware (Franka Panda + RealSense + ZED cameras + Linux host) talking to each other reliably.

## Posts

The project is being developed in public via a series of posts. The framing post and the retrospective on the seven months of prior work that led to this reset are linked here as they go live.

## Related history

Hebbot inherits selectively from the [panda-mcp](https://github.com/ratsbane/panda-mcp) project — the working perception-to-action pipeline, calibration data, and lab notebook all live there. Pieces will be ported into Hebbot as they fit the new framing; pieces that don't won't.

## License

MIT — see [LICENSE](LICENSE).
