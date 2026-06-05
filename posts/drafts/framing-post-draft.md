# Why I'm building Claude Code for Robots

*Draft 2026-05-30 — substack/blog post #1 in the series.*

---

When you start learning the piano, every key press is a small act of cognition. You look at the score, you find the note, you tell your finger to move, you press, you listen. The whole loop is slow and deliberate — your prefrontal cortex is in the driver's seat for an action that, eventually, you won't even notice yourself doing.

After enough practice, something interesting happens: whole passages collapse. You're no longer thinking about each key. You're thinking about the *phrase* — the shape, the dynamic, the feeling — and your fingers handle the rest. The deliberate cognition is still there, watching, ready to take over the moment something goes wrong (the keyboard sticks, a string is out of tune, you want to try a rubato). But the routine motor work has been pushed down to faster, cheaper, more reflexive machinery.

Neuroscientists have a name for this: the cortex–basal ganglia–cerebellum loop. Procedural memory. Motor chunking. Automatization. It's how a tennis player returns a serve in 400 milliseconds when the conscious reaction time alone is 250. It's how a surgeon ties a knot while talking to a colleague. It's why "practice" actually means something physical — your brain literally builds new neural infrastructure to support skills that used to require attention.

**This compression — deliberate cognition becoming reflexive, with bidirectional feedback between the layers — is what most modern AI robotics is missing.**

---

The current center of gravity in robot learning is end-to-end vision-language-action models. You take a foundation model, you bolt on a robot, you feed in pixels and language, you train it to output joint commands. Some of these models are extraordinary. [π0](https://arxiv.org/abs/2410.24164) and its successor π0.5 from [Physical Intelligence](https://www.physicalintelligence.company), [OpenVLA](https://arxiv.org/abs/2406.09246), [RT-2](https://arxiv.org/abs/2307.15818), [Gemini Robotics](https://deepmind.google/discover/blog/gemini-robotics-brings-ai-into-the-physical-world/), the Tesla Optimus stack — these are real engineering achievements, often trained on millions of demonstrations across hundreds of robots, costing more compute than most countries' research budgets.

And they collapse the entire stack into one network.

There's no "deliberate" and "reflexive" anymore. There's just the policy. When the policy fails, you can't intervene — there's no language-level handle to grab. When you want to teach it a new skill, you can't just *show it once*; you have to retrain or fine-tune. When it succeeds, you can't ask *why* — the reasoning has been baked into the weights and you can't read it back out.

This is fine if your goal is a benchmark number on a defined task. It's not fine if your goal is a robot that genuinely operates in the world.

I think there's a different shape the field has barely tried.

---

About a year ago, Anthropic shipped [Claude Code](https://www.anthropic.com/claude-code): a CLI tool that lets Claude operate on real software projects. You hand it a task, and it reads your code, runs your tests, edits files, calls your build tools, observes the results, and iterates. It uses memory to keep track of what it's learned about your project across sessions. It has permissions and a sense of which actions are reversible vs. dangerous. It uses many small tools — read, edit, grep, bash — composed at runtime rather than one giant monolithic model that "does software."

Claude Code is shockingly effective at a class of problems that, two years ago, we would have said required a specialized code model trained end-to-end on millions of repos.

The reason it works isn't the model. It's the **pattern**:

- A foundation model at the center, doing the deliberate cognition
- A library of composable, named primitives the model can call
- Tight observe-decide-act loops where the model reads state and decides the next move
- Memory that persists across attempts
- Permissions and safety as first-class concerns
- Many small focused capabilities over one giant policy
- Errors and surprises as inputs, not crashes

This pattern is what I want to build for robotics. Not the protocol (MCP, function calling, whatever) — those are implementation details. The *pattern*: a substrate that lets a foundation model effectively direct an embodied system, the way Claude Code lets it direct a codebase.

And — here's where the piano comes back — that substrate has to support **skill compression**: deliberate, cognition-heavy procedures gradually becoming reflexive, low-latency, automatic, *while remaining inspectable and overridable from the top*.

---

What I mean concretely:

Imagine a robot arm trying to pick up a coffee cup. The first time, it's slow and deliberate — the LLM examines the scene, plans the grasp, sends a sequence of waypoints to the arm, watches what happens, adjusts. Two minutes of cycle time per cup. Painful.

After 50 cups, *something* should have changed. The system should have noticed that "pick up coffee cup" is a recurring pattern. It should have collected the deliberate executions as training data. It should have trained a small reflexive policy that handles the common case in 4 seconds without LLM-level reasoning. And — critically — it should know *when* to defer to the reflex (normal cup on a normal table) and *when* to escalate back to deliberate cognition (cup is upside down, table is sticky, lighting just changed dramatically).

The bidirectional feedback is the magic. The reflexive layer can report back ("this descent felt off, the force readings spiked"), and the deliberate layer can respond ("let me re-plan, this isn't a standard cup"). Neither layer is sufficient alone. Neither is the bottom-up "train end-to-end and pray" approach, and neither is the top-down "let the LLM decide every joint angle" approach.

The substrate's job is to *enable that compression and that feedback* — not just to dispatch tool calls.

---

There's a meta-move here too, which is the part I'm most excited about.

In the standard robotics research workflow, a human designs the cognitive architecture (the modules, the interfaces, the data flows), and then they evaluate AI systems running inside that architecture. The human is the architect; the AI is the user.

I want to flip this. I want to give the AI — Claude, currently; whatever the best model is six months from now — full access to control the arm, receive feedback from the camera, observe the results of its own actions, and **iteratively build the substrate itself**, along with the lower-level skills that fill it. The human (me) provides the hardware, the judgment, the direction, the willingness to say "no, that's wrong, try this instead." But the AI is the active architect, not the passive operator.

This is the *only* path I can see to building a substrate that actually fits how an LLM thinks. Humans don't really know what abstractions an LLM will find natural until the LLM has tried to use them and failed. Every time I sit down to design "the right" interface for the robot, I'm guessing at what Claude will find easy. Why guess? Let Claude tell me.

This makes the whole project a bootstrapping exercise. The substrate gets better, which lets the AI work more effectively in the domain, which lets it build a better substrate. The development trajectory itself becomes the artifact.

---

Some things I should be transparent about.

**This isn't novel in every direction.** [SayCan](https://arxiv.org/abs/2204.01691), [Code as Policies](https://arxiv.org/abs/2209.07753), [ProgPrompt](https://arxiv.org/abs/2209.11302), [Inner Monologue](https://arxiv.org/abs/2207.05608), [PaLM-E](https://arxiv.org/abs/2303.03378), and several other 2022–2023 papers pointed at this shape — language-model planners calling robot skills. They were directionally right but predated modern LLM tooling and the substrate around it (no MCP, no good memory primitives, no Claude Code-style iteration loops). The shape they sketched is the shape I'm filling in, with much better tools than they had.

**It's not faster than VLA in the short term.** A well-trained π0.5 will out-perform my system on a benchmark task next week. The substrate approach has to earn its keep over a longer horizon: generalization to unstructured environments, transferability between hardware platforms, inspectability when things go wrong, ability to incorporate new skills without retraining the whole model.

**The hardware I'm working with is modest.** A Franka Panda arm, a handful of cameras (a couple of RealSense D435s and D405s, ZED 2i and ZED Mini stereo cameras), and a modest GPU host for inference and data collection. There's nothing here that an interested hobbyist with $5K and patience couldn't replicate, which I think is part of the point.

**I'm planning to port to a $250 arm.** The [SO-100 from LeRobot](https://github.com/huggingface/lerobot). If the skills work on the Franka and on the SO-100, the substrate has proven its hardware portability. If they don't, the substrate is too tightly coupled to the expensive hardware and needs more abstraction.

---

What I'm planning to build, in serial posts:

1. **This post** — the framing. (You're reading it.)
2. **Designing skill primitives** — what makes a primitive that an LLM can actually reason about and compose. How to spec one. Why my first three were wrong.
3. **Observation primitives** — what cameras give you, what they don't, and what perception scaffolding you need before the LLM can do anything useful.
4. **Memory and state** — how to give an agent loop persistent context across attempts without bloating its working context.
5. **First demo: messy-scene manipulation** — video of the substrate handling something deliberately unstructured. Probably my actual desk.
6. **Skill compression in practice** — the first time the system identifies a recurring pattern and trains a reflexive policy for it. (This is the post I most want to write and probably the hardest to get to.)
7. **Same skills on a $250 arm** — the SO-100 portability test.

I'll post roughly every two to three weeks. Each post will end with something concrete — code, video, dataset, repo link.

---

## Further reading

A non-exhaustive map of work this project is in conversation with.

### LLM-as-controller for robots (the lineage)

The earlier wave of papers that framed language models as planners calling robot skills:

- Ahn et al., [*Do As I Can, Not As I Say: Grounding Language in Robotic Affordances* (SayCan)](https://arxiv.org/abs/2204.01691), 2022
- Huang et al., [*Inner Monologue: Embodied Reasoning through Planning with Language Models*](https://arxiv.org/abs/2207.05608), 2022
- Liang et al., [*Code as Policies: Language Model Programs for Embodied Control*](https://arxiv.org/abs/2209.07753), 2022
- Singh et al., [*ProgPrompt: Generating Situated Robot Task Plans using Large Language Models*](https://arxiv.org/abs/2209.11302), 2022
- Driess et al., [*PaLM-E: An Embodied Multimodal Language Model*](https://arxiv.org/abs/2303.03378), 2023

### Modern end-to-end VLAs (what I'm contrasting with)

- Brohan et al., [*RT-2: Vision-Language-Action Models Transfer Web Knowledge to Robotic Control*](https://arxiv.org/abs/2307.15818), 2023
- Padalkar et al., [*Open X-Embodiment: Robotic Learning Datasets and RT-X Models*](https://arxiv.org/abs/2310.08864), 2023
- Octo Model Team, [*Octo: An Open-Source Generalist Robot Policy*](https://arxiv.org/abs/2405.12213), 2024
- Kim et al., [*OpenVLA: An Open-Source Vision-Language-Action Model*](https://arxiv.org/abs/2406.09246), 2024
- Black et al., [*π₀: A Vision-Language-Action Flow Model for General Robot Control*](https://arxiv.org/abs/2410.24164), 2024
- Khazatsky et al., [*DROID: A Large-Scale In-The-Wild Robot Manipulation Dataset*](https://arxiv.org/abs/2403.12945), 2024

### Cognitive science and motor learning (the grounding)

- Hebb, D.O., *The Organization of Behavior*, Wiley, 1949 — the original "neurons that fire together wire together" book, eponym for [Hebbot](https://github.com/ratsbane/hebbot)
- Anderson et al., [*An integrated theory of the mind*](https://psycnet.apa.org/doi/10.1037/0033-295X.111.4.1036) (ACT-R), Psychological Review, 2004
- Wolpert, Diedrichsen, & Flanagan, [*Principles of sensorimotor learning*](https://www.nature.com/articles/nrn3112), Nature Reviews Neuroscience, 2011 — what cognitive science knows about how skill compression actually works in nervous systems
- Krakauer et al., [*Motor Learning*](https://onlinelibrary.wiley.com/doi/10.1002/cphy.c170043), Comprehensive Physiology, 2019 — a comprehensive review covering the deliberate → reflexive arc
- Laird, J.E., *The Soar Cognitive Architecture*, MIT Press, 2012 — the other big pre-LLM cognitive architecture, also a useful template for the kind of high/low layering Hebbot aims at

### Agentic AI bootstrapping skills (the meta-move)

- Yao et al., [*ReAct: Synergizing Reasoning and Acting in Language Models*](https://arxiv.org/abs/2210.03629), 2023
- Wang et al., [*Voyager: An Open-Ended Embodied Agent with Large Language Models*](https://arxiv.org/abs/2305.16291), 2023 — closest published cousin to the "AI bootstraps its own skill library" half of Hebbot's thesis
- Ma et al., [*Eureka: Human-Level Reward Design via Coding Large Language Models*](https://arxiv.org/abs/2310.12931), 2023

### Imitation learning + skill libraries (the lower layer)

- Chi et al., [*Diffusion Policy: Visuomotor Policy Learning via Action Diffusion*](https://arxiv.org/abs/2303.04137), 2023
- Zhao et al., [*Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware* (ALOHA / ACT)](https://arxiv.org/abs/2304.13705), 2023
- Fu et al., [*Mobile ALOHA: Learning Bimanual Mobile Manipulation with Low-Cost Whole-Body Teleoperation*](https://arxiv.org/abs/2401.02117), 2024

---

If you're working on related things, building toward similar ideas, or just curious about the messy reality of robotics that doesn't fit on a benchmark — say hi. I'm @ratsbane on most places, and I read everything that comes in.

If you think this is wrongheaded — also say hi. I'd rather know now than discover it in nine months.

The next post will be on designing skill primitives. Expect it in three weeks.

— Doug
