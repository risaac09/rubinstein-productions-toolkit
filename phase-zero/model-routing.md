# Model routing check

Before a substantive task, state in one line the model, effort, and why, then
proceed. Ask first only when escalating above the anchor (Sonnet 5 at medium),
when spending Fable, or before launching a Workflow fan-out (state the agent
count). If Isaac redirects, his call wins for the rest of the session.
Prices per 1M tokens in/out: Fable 5 $10/$50, Opus 5 $5/$25, Sonnet 5 $3/$15,
Haiku 4.5 $1/$5.

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
  Haiku 4.5 at low, batched. Private drafting that must not leave the machine
  goes to the local base-model lane (stack-data DECISIONS 2026-10-05); it
  never verifies, cites or arbitrates.
- Routine synthesis, continuity, component edits, extraction, research legwork:
  Sonnet 5 at medium.
- Orchestration, architecture, hard reasoning, final synthesis: start on
  Sonnet 5 at medium; escalate to Opus 5 only for a named difficulty.
- The single hardest long-horizon task worth the premium: Fable 5.

Importance is not difficulty. Model and effort are separate levers; the same
model at a lower effort is often right.

## Capacity contract (2026-08-12)

Claude runs on Max 5x; ChatGPT Plus is a separate pool. No usage credits or
silent API overage on either. At a Claude limit, hand eligible execution work
to Codex or wait for the reset. Keep 20 percent of Claude's weekly capacity for
urgent synthesis and continuity. Escalations are rationed by this budget, not
by rarity.

## The falsifier

Log the tier and whether the work needed a second review round:

    sd-ai-engage --json '{"routedTier":"opus-5/medium","secondRound":false}' --land

The reasoning behind each rule, the known failure mode, and worked examples:
`rubinstein-productions-toolkit/phase-zero/model-routing-rationale.md`.

This copy is kit-deployed. The source lives in
`rubinstein-productions-toolkit/phase-zero/model-routing.md`; edit it there
and redeploy. Never edit the deployed copy.
