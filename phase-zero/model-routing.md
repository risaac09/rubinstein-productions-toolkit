# Model routing check

Before starting any substantive task, name the model and effort that fit it,
then ask before proceeding. Do not switch silently.

- Open with one line: task type, recommended model, effort level, cost
  tradeoff. Prices per 1M tokens in/out: Fable 5 $10/$50, Opus 5 $5/$25,
  Sonnet 5 $3/$15 (intro $2/$10 through 2026-08-31), Haiku 4.5 $1/$5.
- Isaac confirms, adjusts, or overrides. His answer wins. Once he decides,
  proceed and do not raise it again this session.

Routing defaults:

- Routine synthesis, continuity work, component edits, extraction, and
  research legwork: Sonnet 5 at medium.
- Orchestration, architecture, hard reasoning, and final synthesis: begin on
  Sonnet 5 at medium. Escalate to Opus 5 only when a named difficulty survives
  that pass.
- The single hardest long-horizon task worth the premium: Fable 5, sweep
  effort low to high.
- Bulk reads, search, mechanical edits, validation: Haiku 4.5 at low.
- Low-impact preprocessing with no subscription budget: the local lane,
  free and private. Always-on small model at `http://mini.local:8080/v1`
  (Gemma 4 E4B, OpenAI-compatible; `llm-mini` sets LLM_BASE_URL) for
  markitdown conversion, summarization, bulk classification, light drafts.
  Heavy local jobs use the M2's on-demand 35B (`llm-start`), never during
  Resolve. The local lane has a quality floor: no audit-class, corpus-sweep,
  or voice-gated work.
- Deterministic work with no judgment: a script, not a model call.

Capacity contract, effective 2026-08-12:

- Claude runs on Max 5x. ChatGPT Plus is a separate paid pool. Use Claude for
  synthesis, continuity, Isaac-voice work, and Claude-native projects. Route
  routine repository execution, local audits, and artifact production to Codex
  Terra at medium when the task does not depend on Claude context.
- Do not use usage credits or silent API overage on either service. If Claude
  reaches an included limit, hand eligible execution work to Codex through the
  content-free coordination protocol or wait for Claude's next reset. If both
  pools are limited, continue deterministic local work or move the task to the
  next week.
- Keep at least 20 percent of Claude's weekly capacity available for urgent
  synthesis and continuity work. Start a fresh outcome-focused session when the
  task changes instead of carrying a long cached context forward.
- Opus and Fable are explicit escalations. Name the difficulty they address and
  return to Sonnet after that pass.

Model and effort are separate levers. The right setting can be the same model
at a lower effort. Sweep effort before reaching for a bigger model: Opus 5
runs the full ladder to max, and its low and medium tiers hold up on work that
used to need a higher tier. The suggestion is a prompt, not a gate. If Isaac
says "just go," take the default and move.

This copy is kit-deployed. The source lives in
`rubinstein-productions-toolkit/phase-zero/model-routing.md`; edit it there
and redeploy. Never edit the deployed copy.
