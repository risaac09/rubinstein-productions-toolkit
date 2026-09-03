# Model routing check

Before starting any substantive task, name the model and effort that fit it,
then ask before proceeding. Do not switch silently.

- Open with one line: what the task needs, recommended model, effort level,
  cost tradeoff. Prices per 1M tokens in/out: Fable 5 $10/$50, Opus 5 $5/$25,
  Sonnet 5 $3/$15, Haiku 4.5 $1/$5.
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

**Q4. Does it need Claude's context, continuity, or Isaac's voice?** This picks
the pool, and it is independent of Q1 through Q3. A hard task with no
Claude-context dependence goes to a strong Codex model, not to Opus.

Tier follows Q1 and Q2. Effort follows Q3 and the surface area. Pool follows
Q4 and the capacity contract. Three levers, not one lookup. The anchor is
Sonnet 5 at medium; Q1 and Q2 move up from there, Q3 sets where in the ladder
to start.

## Lane floors

- Deterministic work with no judgment: a script, not a model call.
- Bulk reads, search, mechanical edits, validation: Haiku 4.5 at low.
- Low-impact preprocessing with no subscription budget: the local lane, free
  and private. Always-on small model at `http://mini.local:8080/v1` (Gemma 4
  E4B, OpenAI-compatible; `llm-mini` sets LLM_BASE_URL) for markitdown
  conversion, summarization, bulk classification, light drafts. Heavy local
  jobs use the M2's on-demand 35B (`llm-start`), never during Resolve. The
  local lane has a quality floor: no audit-class, corpus-sweep, or voice-gated
  work.
- Fable 5 is for the single hardest long-horizon task of the cycle, swept low
  to high.

## Capacity contract, effective 2026-08-12

- Claude runs on Max 5x. ChatGPT Plus is a separate paid pool. Claude carries
  synthesis, continuity, Isaac-voice work, and Claude-native projects. Work
  that clears Q4 with no Claude-context dependence goes to Codex at a tier set
  by Q1 and Q2, not automatically to Terra at medium.
- Do not use usage credits or silent API overage on either service. If Claude
  reaches an included limit, hand eligible execution work to Codex through the
  content-free coordination protocol or wait for Claude's next reset. If both
  pools are limited, continue deterministic local work or move the task to the
  next week.
- Keep at least 20 percent of Claude's weekly capacity available for urgent
  synthesis and continuity work. Start a fresh outcome-focused session when the
  task changes instead of carrying a long cached context forward.
- Escalations are rationed by this budget, not by rarity. Do not hold Opus or
  Fable back to keep them feeling scarce, which under-routes the genuinely hard
  task to preserve a symbol. Name the difficulty, spend the tier, return to the
  anchor after that pass.

## The falsifier

Log two fields per substantive task: the tier routed to, and whether the work
needed a second review round. Without that record this rule cannot be shown
wrong, and its next version is another guess with better prose. The open
Claude-versus-Sol card in `stack-data/docs/DECISIONS.md` closes on that data,
not on argument.

## Known failure mode

Routing from the category label before reading the task. It fires in both
directions. Down, when "routine repository execution" sends a four-commit
branch with voice-gated copy and no test coverage to a cheap lane. Up, when a
task's importance rather than its difficulty argues for Fable after the hard
reasoning has already been spent. Importance is not difficulty.

Model and effort are separate levers. The right setting is often the same model
at a lower effort: Opus 5 runs the full ladder to max, and its low and medium
tiers hold up on work that used to need a higher tier. The suggestion is a
prompt, not a gate. If Isaac says "just go," take the anchor and move.

This copy is kit-deployed. The source lives in
`rubinstein-productions-toolkit/phase-zero/model-routing.md`; edit it there
and redeploy. Never edit the deployed copy.
