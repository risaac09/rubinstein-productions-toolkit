# Private Frontier Lab: a blueprint
*Working Document | Created: 2026-06-17*
*Purpose: Answer "what would it take to build a prototypical AI frontier lab in
a private repo?" with a reuse-vs-build map grounded in what already exists, a
minimal viable version, the maximal version, and the scope discipline to keep it
from becoming systems-instead-of-shipping.*

---

## The finding

You are not starting from zero. About 70% of a lab already runs as code across
the repos. It is scattered and unconnected, not absent. A "frontier lab" here
means one private repo that composes the existing parts into a single loop:
**author a skill or prompt, run it through a model gateway, grade it on an eval
harness, log the run, see it on a dashboard, iterate.** Plus the one piece that
does not exist yet: a runtime that executes agents instead of only describing
them.

## What already exists (the 70%)

| Layer | Status | Reuse | Key files |
|---|---|---|---|
| **Model gateway** | Runs | High | `stack-data/agents/lib-llm.sh` (LLM_BASE_URL vs ANTHROPIC_API_KEY switch) |
| **Eval / benchmark** | Runs | High | `toolkit/evals/lib/grade.py`, `benchmark.py`, `evals/*/evals.json`, `evals/ANALYSIS.md` (19 skills, 104 runs) |
| **Data + schema** | Runs | High | `stack-data/schemas/`, `scripts/validate.sh`, `sd-doctor`, the `sd-*` CLIs |
| **Automation / CLI** | Runs | High | `weekly-sync.yml`, `cli/rp-*`, `agents/*.sh`, inert `.forgejo` port |
| **Interfaces / GUI** | Runs | High | `alchemy/`, `royal-metrics/`, `gene-keys-data/viewer/` (single-file vanilla JS) |
| **Governance** | Runs | High | `voice-lint`, `source-tracking-protocol.md`, `registry/skill-contracts.yaml`, `second-brain-mirror/canonicals/` |
| **Agent runtime** | Docs only | Med | `prompts/skills/*.md`, `skill-contracts.yaml`, the orchestrator is a prompt, not a process |

## The two real gaps (the build, not the reuse)

1. **No executable agent runtime.** The orchestrator and the vehicle agents are prompt documents loaded by hand in a Claude Code session. There is no runner that spawns them, routes between them, gates on evals, and collects results. The eval harness even says the spawn step cannot be scripted today.
2. **No analytics or observability.** The data is schema-perfect but analysis is narrative only. There is no run log (what agent, what model, how long, what it cost), no time-series metrics, and no dashboard reading the live data. `royal-metrics` is manual entry.

Everything else is plumbing that already works.

## Architecture: `rp-lab` (private repo)

```
rp-lab/
├── gateway/        thin wrapper over lib-llm.sh: one client, model + provider switch, token + cost log
├── runtime/        the new piece. spawns a skill against an input, routes orchestrator -> sub-skill, returns output
├── evals/          reuse grade.py + benchmark.py; add a batch runner that calls runtime per fixture
├── runlog/         append-only JSONL: {ts, skill, model, tokens, cost, ms, eval_score}. the spine of observability
├── data/           symlink or sync from stack-data dist/; the substrate to experiment on
├── dashboard/      single-file PWA over runlog + data (the royal-metrics pattern, pointed at runs)
├── skills/         synced from toolkit/prompts/skills (one source of truth, versioned)
└── governance/     voice-lint, source-tracking checks, the deploy red lines, run as pre-commit + CI
```

The loop: **runtime runs a skill through the gateway, the eval harness grades the
output, the run log records it, the dashboard shows the trend.** That loop is
what turns a pile of prompts into a lab.

## Minimal viable lab (build one thing)

Reuse everything above and build only the spine:

1. **`gateway/` + `runlog/`.** Wrap `lib-llm.sh` so every call writes a JSONL line (skill, model, tokens, cost, latency). One day of work, and it immediately gives you cost and usage visibility you do not have now.
2. **`runtime/` v0.** A script that takes a skill name plus an input, loads the skill, calls the gateway, returns output. No multi-agent routing yet. Two to three days.
3. **Eval batch runner.** Glue the runtime to `grade.py` so `evals/` runs without hand-spawning subagents. One to two days.

That is the whole minimal lab: a runnable skill, an automatic grade, a logged
run. It reuses the gateway, the evals, the governance, and the data. The new
code is small.

## Maximal lab (multi-quarter)

- **Full agent runtime:** planner plus executor, orchestrator routing in code, inter-agent context passing, sub-goal verification.
- **Analytics pipeline:** time-series metrics store, computed fields over the JSON, anomaly and trend alerting, a live dashboard on `stack-data`.
- **CI eval gates:** evals run on every PR that touches `skills/`, baseline regenerated when a skill changes.
- **Observability:** central run log, per-agent health, token and cost tracking per model and per skill.
- **Deployed backend:** the `rp-shared` + `rp-api` foundation (separate spec) for anything multi-device or paid.

## How it relates to the other drafts

The lab is the meta-environment. `idea-to-pilot` is how a lab experiment becomes
a shipped pilot. The `rp-shared` foundation is the deploy target a pilot
graduates onto. The three stack: lab makes it, the pipeline ships it, the
foundation hosts it.

## Scope discipline (the honest part)

This is the largest systems-build on the table, so the same rule from the
foundation proposal applies harder: building a lab is the textbook
"building-systems-instead-of-shipping" risk, and the warm money this week is the
evaluation pipeline, not infrastructure.

**Recommendation:**
- **Scope it now** (this doc). Costs nothing, keeps the vision concrete.
- **Build the minimal lab only when a real need pulls it.** The honest trigger: when Isaac is iterating on skills often enough that hand-spawning evals and not knowing token cost actually hurts. Until then the manual loop is fine.
- **Defer the maximal lab** until the practice has revenue and the pilots have users. A lab with no shipped product to study is a hobby, not leverage.

**Decision (for Isaac to record):**
- [ ] Scope now, build the minimal lab when skill-iteration volume justifies it (recommended).
- [ ] Build the minimal lab now (you want the cost log and auto-evals this week).
- [ ] Shelve, the manual loop is fine for now.

**Trigger to start the minimal lab:** the first time you think "I wish I could
just run this skill and see if it got better, and what it cost." That is the day
the spine earns its keep.
