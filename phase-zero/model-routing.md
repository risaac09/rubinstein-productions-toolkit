# Model routing check

Before starting any substantive task, name the model and effort that fit it,
then ask before proceeding. Do not switch silently.

- Open with one line: task type, recommended model, effort level, cost
  tradeoff. Prices per 1M tokens in/out: Fable 5 $10/$50, Opus 5 $5/$25,
  Sonnet 5 $3/$15 (intro $2/$10 through 2026-08-31), Haiku 4.5 $1/$5.
- Isaac confirms, adjusts, or overrides. His answer wins. Once he decides,
  proceed and do not raise it again this session.

Routing defaults:

- Orchestration, architecture, hard reasoning, final synthesis: Opus 5 at
  high or xhigh.
- The single hardest long-horizon task worth the premium: Fable 5, sweep
  effort low to high.
- Component edits, extraction, research legwork: Sonnet 5 at medium.
- Bulk reads, search, mechanical edits, validation: Haiku 4.5 at low.
- Deterministic work with no judgment: a script, not a model call.

Model and effort are separate levers. The right setting can be the same model
at a lower effort. Sweep effort before reaching for a bigger model: Opus 5
runs the full ladder to max, and its low and medium tiers hold up on work that
used to need a higher tier. The suggestion is a prompt, not a gate. If Isaac
says "just go," take the default and move.

This copy is kit-deployed. The source lives in
`rubinstein-productions-toolkit/phase-zero/model-routing.md`; edit it there
and redeploy. Never edit the deployed copy.
