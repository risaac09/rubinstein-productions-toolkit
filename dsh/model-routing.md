# DSH model routing (OpenRouter lane)

The DeepSeek Harness equivalent of `phase-zero/model-routing.md`. Same
philosophy, different model family. This copy is kit-deployed; edit here and
redeploy via `dsh/install.sh` (the brief points at this file).

## The check

Before starting any substantive task, name the model and effort that fit it,
then ask before proceeding. Do not switch silently.

- Open with one line: what the task needs, recommended model, effort level,
  cost tradeoff. Prices per 1M tokens in/out on OpenRouter, verified
  2026-08-22: DeepSeek V4 Flash $0.08/$0.18, DeepSeek V4 Pro $0.41/$0.83,
  Qwen 3 Coder 480B $0.30/$1.00, Qwen 3.5 397B $0.39/$2.34,
  Qwen 3 Max Thinking $0.78/$3.90.
- Isaac confirms, adjusts, or overrides. His answer wins. Once he decides,
  proceed and do not raise it again this session.

## Route on the task, not on its category

Read the work before naming a model. Task-type labels like "repository
execution" or "synthesis" are proxies, and they hide the difference between a
one-line config bump and a branch nobody but Isaac can check. Ask four
questions instead.

**Q1. Who verifies this?** The load-bearing question. Not "is it hard" but "if
it comes back wrong, what catches it, and what does that catch cost?" A test
suite, `validate.sh`, a schema, CI: machine-verified, route down, because a
cheap model behind a strong checker beats an expensive one with no checker.
Isaac reading it: human-verified, route up, because his attention is the
scarce resource and a second review round is the real expense. Nobody until a
client sees it: route up hard.

**Q2. What does a miss cost?** Local branch, pre-push, revertible: absorb the
risk. Pushed, sent, published, or written into a canonical doc: buy the
margin.

**Q3. How much of the thinking is already in the prompt?** A prompt carrying
the diagnosis lowers required effort. A prompt where the model still has to
find the problem raises it. This lever stays inside a tier, so sweep it before
reaching for a bigger model.

**Q4. Does the task need a lane this one cannot serve?** Isaac-voice work,
audit-class passes, and anything depending on Claude-session context do not
belong on this lane at all; hand them back rather than approximating them.
Multimodal input needs `qwen/qwen3.5-397b-a17b`. Coding agents run on
`qwen/qwen3-coder`.

Tier follows Q1 and Q2. Effort follows Q3 and the surface area. Lane follows
Q4. Three levers, not one lookup. The anchor is
`deepseek/deepseek-v4-flash-0731` at low; Q1 and Q2 move up from there through
`deepseek/deepseek-v4-pro` to `qwen/qwen3-max-thinking`, and Q3 sets where in
the effort ladder to start.

## Lane floors

- Deterministic work with no judgment: a script, not a model call.
- Low-impact preprocessing with no subscription budget: the local lane, free
  and private. Always-on small model at `http://mini.local:8080/v1` for
  markitdown conversion, summarization, bulk classification, light drafts.
  The local lane has a quality floor: no audit-class, corpus-sweep, or
  voice-gated work.

## Capacity contract

- Keep at least 20 percent of the paid lane's weekly capacity available for
  urgent synthesis and continuity work. Start a fresh outcome-focused session
  when the task changes instead of carrying a long cached context forward.
- Do not use silent API overage. If the pool is limited, hand eligible
  execution work to another lane or wait for the next reset.
- Escalations are rationed by this budget, not by rarity. Do not hold the
  expensive tiers back to keep them feeling scarce, which under-routes the
  genuinely hard task to preserve a symbol. Name the difficulty, spend the
  tier, return to the anchor after that pass.
- Model and effort are separate levers. Sweep effort before reaching for a
  bigger model. The suggestion is a prompt, not a gate. If Isaac says "just
  go," take the anchor and move.

## The falsifier

Log two fields per substantive task: the tier routed to, and whether the work
needed a second review round. Without that record this rule cannot be shown
wrong, and its next version is another guess with better prose.

## Known failure mode

Routing from the category label before reading the task. It fires in both
directions. Down, when "routine repository execution" sends a multi-commit
branch with voice-gated copy and no test coverage to the cheapest lane. Up,
when a task's importance rather than its difficulty argues for the top tier
after the hard reasoning has already been spent. Importance is not difficulty.

## Books of record

The routing check is injected into Claude sessions by the phase-zero kit; this
file is the DSH-native copy of the same contract. When the two disagree on a
rule, the phase-zero file wins and this file gets fixed.
