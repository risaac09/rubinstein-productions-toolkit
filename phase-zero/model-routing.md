# Model routing check

Before a substantive task, state in one line the model, effort, and why, then
proceed. Ask first only when escalating above the anchor (Sonnet 5.5 at medium),
when spending Fable, or before launching a Workflow fan-out (state the agent
count). If Isaac redirects, his call wins for the rest of the session.
Prices per 1M tokens in/out: Fable 5.1 $10/$50, Opus 5.5 $4/$20, Sonnet 5.5 $2/$10,
Haiku 5.5 $0.10/$0.50 (to 100k prompt tokens; $0.50/$2.50 above).

## Route on the task, not its category

Read the work before naming a model. Tier follows Q1 and Q2, effort follows Q3,
pool follows Q4.

- Q1. Who verifies it? A test suite, schema, CI or validator: route down.
  Isaac's own review: route up. Nobody until a client sees it: route up hard.
- Q2. What does a miss cost? Local and revertible: absorb it. Pushed, sent,
  published, or written into a canonical doc: buy the margin.
- Q3. How much of the thinking is already in the prompt? A carried diagnosis
  lowers effort; a problem still to be found raises it.
- Q4. Does it need Claude's context, continuity, or Isaac's voice? If not, a
  strong Codex model is the right pool, not Opus.

## Lane floors

- Deterministic work with no judgment: a script, not a model call.
- Bulk reads, search, mechanical edits, validation, low-impact preprocessing:
  Haiku 5.5 at low, batched. Private drafting that must not leave the machine
  can go to the local base-model lane when Isaac has it running (stack-data
  DECISIONS, "The local lane returns as a base-model lane (2026-10-05)";
  endpoint and rules in model-routing-rationale.md). It never arbitrates,
  never supplies citations or identifiers, and runs no tools.
- Routine synthesis, continuity, component edits, extraction, research legwork:
  Sonnet 5.5 at medium.
- Orchestration, architecture, hard reasoning, final synthesis: start on
  Sonnet 5.5 at medium; escalate to Opus 5.5 only for a named difficulty.
- The single hardest long-horizon task worth the premium: Fable 5.1.

Importance is not difficulty. Model and effort are separate levers; the same
model at a lower effort is often right.

## Capacity contract (2026-09-13)

Claude runs on Max 20x; ChatGPT Plus is a separate pool. No usage credits or
silent API overage on either. At a Claude limit, hand eligible execution work
to Codex or wait for the reset. Keep 20 percent of Claude's weekly capacity for
urgent synthesis and continuity. Escalations are rationed by this budget, not
by rarity.

## The falsifier

Log the tier and whether the work needed a second review round:

    sd-ai-engage --json '{"routedTier":"opus-5/medium","secondRound":false,"items":["OPS-07"]}' --land

`items` names the planning ids the work moved (`scripts/sd-ref --list --state
open`); the session id is filled from the environment.

To compare pools, add `pool` (claude, codex, local, openrouter or script). Add
review load when you have it: `cards` (integer), `g1FirstPass` (0 to 1, the
share of cards that passed the first gate) and `reviewEditLines` (integer,
lines Isaac or a reviewer edited). All are optional and validated; a typo
refuses the entry. `sd-ai-engage --summary [--since DATE]` reads the log back
by pool and tier.

The reasoning behind each rule, the known failure mode, and worked examples:
`stack-data/kit/phase-zero/model-routing-rationale.md`.

This copy is kit-deployed. The source lives in
`stack-data/kit/phase-zero/model-routing.md`; edit it there
and redeploy. Never edit the deployed copy.
