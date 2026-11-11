# Model routing check

Before a substantive task, state in one line the model, effort, and why, then
proceed. Ask first only when escalating above the anchor (Sonnet 5.5 at medium),
when spending Fable, or before launching a Workflow fan-out (state the agent
count). If the operator redirects, the operator's call wins for the rest of the session.
Prices per 1M tokens in/out: Fable 5.1 $10/$50, Opus 5.5 $4/$20, Sonnet 5.5 $2/$10,
Haiku 5.5 $0.10/$0.50 (to 100k prompt tokens; $0.50/$2.50 above).

## Route on the task, not its category

Read the work before naming a model. Tier follows Q1 and Q2, effort follows Q3,
pool follows Q4.

- Q1. Who verifies it? A test suite, schema, CI or validator: route down.
  The operator's own review: route up. Nobody until a client sees it: route up hard.
- Q2. What does a miss cost? Local and revertible: absorb it. Pushed, sent,
  published, or written into a canonical doc: buy the margin.
- Q3. How much of the thinking is already in the prompt? A carried diagnosis
  lowers effort; a problem still to be found raises it.
- Q4. Does it need Claude's context, continuity, or the operator's voice? If not, a
  strong Codex model is the right pool, not Opus.

## Lane floors

- Deterministic work with no judgment: a script, not a model call.
- Bulk reads, search, mechanical edits, validation, low-impact preprocessing:
  Haiku 5.5 at low, batched. Private drafting that must not leave the machine
  can go to the local base-model lane when the operator has it running (stack-data
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

Fan-out: at most five live writers per parent. Input tokens ran about 240x output on 2026-10-20, so parent context lifetime is the cost. End a long lane at a checkpoint with a written handover instead of keeping one context alive for hours.

Scout before the fan-out, in this order, each a minute: a one-word probe of
the tool the agents will use (a default that worked last week can be refused
today); the holder of the document or the source's own index (one email, one
List of Illustrations, beat six and nine agents); the queue items gated on
the operator's own pending calls, asked first (a 29-agent run moved 5 of 46 without
that); the shared file every writer will touch, removed before any routing
change. Evidence: stack-data docs/learnings-synthesis-2026-11-01.md, family F4.

## Capacity contract (2026-09-13)

Claude runs on Max 20x; ChatGPT Plus is a separate pool. No usage credits or
silent API overage on either. At a Claude limit, hand eligible execution work
to Codex or wait for the reset. Keep 20 percent of Claude's weekly capacity for
urgent synthesis and continuity. Escalations are rationed by this budget, not
by rarity.

## The falsifier

Log the tier and whether the work needed a second review round:

    sd-ai-engage --json '{"routedTier":"opus-5.5/medium","secondRound":false,"items":["OPS-07"]}' --land

`items` names the planning ids the work moved (`scripts/sd-ref --list --state
open`); the session id is filled from the environment.

To compare pools, add `pool` (claude, codex, local, openrouter or script). Add
review load when you have it: `cards` (integer), `g1FirstPass` (0 to 1, the
share of cards that passed the first gate) and `reviewEditLines` (integer,
lines the operator or a reviewer edited). All are optional and validated; a typo
refuses the entry. `routedTier` is `<model>/<effort>` with model one of
fable-5.1, opus-5.5, sonnet-5.5, haiku-5.5 and effort low, medium, high, xhigh
or max (`sonnet-5.5/medium`), or `codex/<slug>`, `local/<slug>`, `openrouter/<slug>`.
Detail goes in `model`, `effort` and `taskClass`, not in the tier string.
`sd-ai-engage --summary [--since DATE]` reads the log back
by pool and tier.

The reasoning behind each rule, the known failure mode, and worked examples:
`stack-data/kit/phase-zero/model-routing-rationale.md`.

This copy is kit-deployed. The source lives in
`stack-data/kit/phase-zero/model-routing.md`; edit it there
and redeploy. Never edit the deployed copy.
