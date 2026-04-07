# Resume: RP Skill Eval Harness — Multi-Batch Testing

**Session paused:** 2026-04-05 ~5:35pm ET (rate limit hit)
**Context:** Mid-way through Batch A of 4-batch plan testing all custom RP skills.

## What's complete

### Skills fully evaluated (benchmark.md exists):
- `outreach-email-manager` — 5 fixtures, delta: -30% word count (brevity discipline)
- `proposal-scope-builder` — 3 fixtures, delta: +17pp qualitative (architectural win)
- `hook-writing` — 3 fixtures, tied at 93% (inconclusive, need more fixtures)

### Batch A progress (outputs saved, not yet graded):
- `content-publishing-agent` — **6/6 outputs saved** ✓
- `network-stewardship` — **6/6 outputs saved** ✓
- `finance-hub` — **6/6 outputs saved** ✓
- `project-management-coordinator` — **1/6 outputs saved** (5 to redo)

## Harness + fixtures ready (just need to run)

### Batch A remainder (5 subagent runs):
- `project-management-coordinator/new-booking-workflow/with_skill`
- `project-management-coordinator/pre-shoot-prep/with_skill`
- `project-management-coordinator/pre-shoot-prep/baseline`
- `project-management-coordinator/phase-transition-comms/with_skill`
- `project-management-coordinator/phase-transition-comms/baseline`

### Batches B, C, D (fixtures not yet written):
- **Batch B:** brand-intake, creative-strategy-engine, review-audit, market-listener
- **Batch C:** mom-test, jobs-to-be-done, storybrand-messaging, made-to-stick
- **Batch D:** rubinstein-productions-coo, writing-skills, humanizer, stop-slop

## Resume steps (in order)

### 1. Finish Batch A (5 subagent runs for project-management-coordinator)

Fixtures are in `~/rubinstein-productions-toolkit/evals/project-management-coordinator/evals.json`. Spawn 5 parallel subagents from Claude Code session matching the pattern in earlier runs. See existing `linkedin-post-from-session` subagent prompts for format reference.

Output paths:
- `evals/project-management-coordinator/iteration-1/new-booking-workflow/with_skill/output.md`
- `evals/project-management-coordinator/iteration-1/pre-shoot-prep/with_skill/output.md`
- `evals/project-management-coordinator/iteration-1/pre-shoot-prep/baseline/output.md`
- `evals/project-management-coordinator/iteration-1/phase-transition-comms/with_skill/output.md`
- `evals/project-management-coordinator/iteration-1/phase-transition-comms/baseline/output.md`

### 2. Grade Batch A (all 4 skills)

```bash
cd ~/rubinstein-productions-toolkit/evals
for s in content-publishing-agent network-stewardship finance-hub project-management-coordinator; do
  python3 lib/grade.py $s
done
```

Then spawn ONE grader subagent to grade qualitative assertions across all 4 skills (24 files total). See previous grader prompt pattern in iteration-1 for outreach-email-manager.

Then:
```bash
for s in content-publishing-agent network-stewardship finance-hub project-management-coordinator; do
  python3 lib/benchmark.py $s
done
```

### 3. Batch B, C, D — design fixtures, spawn subagents, grade

For each remaining skill:
1. Read `~/.claude/skills/<skill>/SKILL.md` briefly
2. Create `~/rubinstein-productions-toolkit/evals/<skill>/evals.json` with 3 fixtures, each with 3-5 universal/fixture assertions
3. Create `iteration-1/<fixture>/{with_skill,baseline}` directories
4. Spawn 6 subagents (3 fixtures × 2 modes)
5. Grade programmatic + spawn qualitative grader
6. Generate benchmark.md

### 4. Final cross-skill analysis

Update `~/rubinstein-productions-toolkit/evals/ANALYSIS.md` with all 15 tested skills. Identify:
- Which skills show clear delta (keep)
- Which tie with baseline (candidates for deprecation or redesign)
- Which skills enforce structure vs restraint vs creative quality

## Key file locations

- Harness: `~/rubinstein-productions-toolkit/evals/lib/{grade.py,benchmark.py}`
- Skills: `~/.claude/skills/<skill>/SKILL.md` (symlinks to toolkit)
- Pattern reference: `~/rubinstein-productions-toolkit/evals/README.md`
- Per-skill fixtures: `~/rubinstein-productions-toolkit/evals/<skill>/evals.json`

## Memory to consult

- `project_skill_evals.md` (to be created) — summary of skill eval findings
- `feedback_source_of_truth.md` — don't edit derived files
- `reference_observability_logs.md` — skill usage stats via ~/.claude/hooks/stats

## Decision to confirm with Isaac at resume

After Batch A completes and we have grades for 4 more skills (7 total tested), **check in** before proceeding with Batches B/C/D. The cost-per-skill is significant and Isaac may want to:
- Stop and review findings
- Change batch ordering
- Scope down (e.g., skip transform skills)
