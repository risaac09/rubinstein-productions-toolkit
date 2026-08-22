# DSH model routing (OpenRouter lane)

The DeepSeek Harness equivalent of `phase-zero/model-routing.md`. Same
philosophy, different model family. This copy is kit-deployed; edit here and
redeploy via `dsh/install.sh` (the brief points at this file).

## The check

Before starting any substantive task, name the model and effort that fit it,
then ask before proceeding. Do not switch silently.

- Open with one line: task type, recommended model, effort level, cost
  tradeoff. Prices per 1M tokens in/out on OpenRouter, verified 2026-08-22:
  DeepSeek V4 Flash $0.08/$0.18, DeepSeek V4 Pro $0.41/$0.83,
  Qwen 3 Coder 480B $0.30/$1.00, Qwen 3.5 397B $0.39/$2.34,
  Qwen 3 Max Thinking $0.78/$3.90.
- Isaac confirms, adjusts, or overrides. His answer wins. Once he decides,
  proceed and do not raise it again this session.

## Routing defaults

- Default lane, lowest cost: `deepseek/deepseek-v4-flash-0731` at low effort.
  Routine synthesis, continuity work, component edits, extraction, research
  legwork, bulk reads, mechanical edits.
- Coding agents: `qwen/qwen3-coder` at medium.
- Orchestration, architecture, hard reasoning, final synthesis: begin on the
  default at medium. Escalate to `qwen/qwen3-max-thinking` or
  `deepseek/deepseek-v4-pro` only when a named difficulty survives that pass.
- Multimodal: `qwen/qwen3.5-397b-a17b`.
- Low-impact preprocessing with no subscription budget: the local lane, free
  and private. Always-on small model at `http://mini.local:8080/v1` for
  markitdown conversion, summarization, bulk classification, light drafts.
  The local lane has a quality floor: no audit-class, corpus-sweep, or
  voice-gated work.
- Deterministic work with no judgment: a script, not a model call.

## Capacity contract

- Keep at least 20 percent of the paid lane's weekly capacity available for
  urgent synthesis and continuity work. Start a fresh outcome-focused session
  when the task changes instead of carrying a long cached context forward.
- Do not use silent API overage. If the pool is limited, hand eligible
  execution work to another lane or wait for the next reset.
- Model and effort are separate levers. Sweep effort before reaching for a
  bigger model. The suggestion is a prompt, not a gate. If Isaac says "just
  go," take the default and move.

## Books of record

The routing check is injected into Claude sessions by the phase-zero kit; this
file is the DSH-native copy of the same contract. When the two disagree on a
rule, the phase-zero file wins and this file gets fixed.