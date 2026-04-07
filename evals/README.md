# RP Skill Eval Harness

Measure the quality of RP skills by comparing outputs **with** the skill against a **baseline** (same prompt, no skill). Inspired by `anthropic-skills:skill-creator` but adapted for RP's CLI-first, shell-over-Node stack.

## Why eval skills

A skill that doesn't change outputs vs baseline is dead weight. Evals answer:

- **Is the skill doing work?** (delta vs baseline)
- **Where does it fail?** (assertions pointing at gaps in the instructions)
- **Is it getting better?** (iteration-N vs iteration-(N-1))

## Directory layout

```
evals/
├── README.md                    ← this file
├── lib/                         ← shared harness code
│   ├── grade.py                 ← programmatic grader (reusable)
│   └── benchmark.py             ← markdown scorecard generator
└── <skill-name>/                ← one directory per skill
    ├── evals.json               ← fixtures + assertions
    ├── iteration-1/
    │   ├── <eval-name>/
    │   │   ├── with_skill/
    │   │   │   ├── output.md    ← subagent output
    │   │   │   └── grading.json ← assertion results
    │   │   └── baseline/
    │   │       ├── output.md
    │   │       └── grading.json
    │   └── benchmark.md         ← aggregate scorecard
    └── iteration-2/             ← after skill edits, run again
```

## Workflow

### 1. Write `evals.json` for the skill

```json
{
  "skill_name": "my-skill",
  "universal_assertions": [
    { "id": "no_banned_words", "text": "...", "check": "programmatic",
      "rule": "regex_absent", "patterns": ["\\bsynergy\\b"], "quote_aware": true },
    { "id": "specific_opener", "text": "...", "check": "qualitative" }
  ],
  "evals": [
    { "id": 1, "name": "fixture-name", "type": "scenario_type",
      "prompt": "...",
      "context": "what this tests",
      "fixture_assertions": [ /* assertions specific to this fixture */ ] }
  ]
}
```

**Assertion check types:**

- `"check": "programmatic"` — evaluated deterministically by `grade.py`. Rules: `regex_absent`, `regex_present`, `word_count`, `word_count_max`, `line_count`.
- `"check": "qualitative"` — stubbed by `grade.py`, filled in by a grader subagent.

**Quote-aware checks:** `regex_absent` defaults to `quote_aware: true` — text inside `"..."`, `'...'`, or curly quotes is stripped before matching. Prevents false positives when a skill echoes prospect language back as part of a diagnosis.

### 2. Run fixtures through subagents (Claude Code only)

For each fixture, spawn two subagents in the same turn:

- **with_skill:** prompt tells it to read `~/.claude/skills/<skill-name>/SKILL.md` and follow its instructions
- **baseline:** prompt tells it to complete the task using ONLY general knowledge, no skills

Outputs land in `iteration-1/<fixture-name>/{with_skill,baseline}/output.md`.

> The subagent spawn step can't be fully scripted — it requires the `Agent` tool inside a Claude Code session.

### 3. Grade (programmatic)

```bash
python3 lib/grade.py <skill-name>
```

Walks the latest `iteration-N` directory, applies programmatic assertions, writes `grading.json` per run. **Preserves existing qualitative grades** — safe to re-run.

### 4. Grade (qualitative, via grader subagent)

Spawn a grader subagent (in Claude Code) that reads each output + assertions and updates `grading.json` with real qualitative judgments (replacing `"passed": null, "evidence": "pending_llm_grade"` entries).

### 5. Generate scorecard

```bash
python3 lib/benchmark.py <skill-name>
```

Writes `iteration-N/benchmark.md` with aggregate scores, per-eval table, failure modes, and delta vs baseline.

## Iteration loop

After reading `benchmark.md` + failure modes:
1. Edit the skill (source: `~/rubinstein-productions-toolkit/prompts/skills/<skill>.md`)
2. `mkdir iteration-2`
3. Re-run fixtures through new subagents → iteration-2 outputs
4. Re-grade
5. Compare iteration-2/benchmark.md vs iteration-1/benchmark.md

## Conventions

- **3-5 fixtures per skill.** More is statistically better but cost-heavy.
- **Assertion IDs are stable.** Used to match programmatic vs qualitative across iterations.
- **Baseline stays constant across iterations** (same prompt, no skill) — it's the reference line.
- **Qualitative grades persist across re-runs** of `grade.py` as long as `assertion_id` matches.

## Skills with evals

| Skill | Status | Delta | Signal |
|---|---|---|---|
| `outreach-email-manager` | iteration-1 done (2026-04-05) | -30% word count | ⭐ Brevity discipline win |
| `proposal-scope-builder` | iteration-1 done (2026-04-05) | +17pp qualitative | ⭐ Architectural discipline win |
| `hook-writing` | iteration-1 done (2026-04-05) | tied | Inconclusive at 3 fixtures — expand |

See `ANALYSIS.md` for cross-skill findings.
