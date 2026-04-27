# RP Skill Eval Harness — Cross-Skill Analysis

**Last updated:** 2026-04-06
**Skills evaluated:** 19 (11 full + 8 truncated)
**Total runs:** 104 (52 fixtures × 2 modes)

---

## Headline

**After 11 skills tested, a clear pattern emerges:** skills win when they enforce canonical vocabulary, structure, or restraint. Skills tie with baseline when they ask for general-purpose synthesis Claude Opus already does well.

The largest measurable skill win so far is **review-audit at +33pp qualitative** — baseline ignores the 5-bucket canonical framework. The next largest are **proposal-scope-builder (+17pp)**, **brand-intake (+13pp + -39% words)**, **network-stewardship (+9pp but flagged for transactional leak)**, and **creative-strategy-engine (+7pp)**.

---

## Cross-skill summary

| Skill | Qual Δ (w vs b) | Word delta | Verdict |
|---|---|---|---|
| **review-audit** | **+33pp** (100% / 67%) | +32% | ⭐ Strongest structural win — canonical 5-bucket framework |
| **proposal-scope-builder** | **+17pp** (100% / 83%) | — | ⭐ Architectural + vocabulary discipline |
| **brand-intake** | **+13pp** (100% / 87%) | **-39%** | ⭐ Rare double-win: Say-Why module + brevity |
| network-stewardship | +9pp (92% / 83%) | +36% | ⚠️ Win on qual, fails prog, leaks transactional framing |
| creative-strategy-engine | +7pp (100% / 93%) | +53% | ✅ Canonical persona enforcement |
| project-management-coordinator | +6pp (100% / 94%) | +34% | ✅ RP-specific framing (Mirror/Map tiers) |
| outreach-email-manager | (earlier: tied qual) | **-30%** | ⭐ Brevity discipline |
| hook-writing | 0pp (93% / 93%) | +36% | ⚠️ Inconclusive at 3 fixtures |
| content-publishing-agent | 0pp (100% / 100%) | +17% | ⚠️ Tied |
| finance-hub | 0pp (100% / 100%) | +43% | ⚠️ Tied |
| market-listener | 0pp (100% / 100%) | ~tied | ⚠️ Tied — skill may be redundant for analysis (still needed for signals.json plumbing) |
| **made-to-stick** *(truncated n=2)* | **+40pp** (100% / 60%) | +125% | ⭐ Strongest Batch C win — SUCCESs + Curse of Knowledge enforcement |
| **mom-test** *(truncated n=2)* | **+25pp** (88% / 63%) | +16% | ⭐ Three-rules + no-pitching discipline |
| **jobs-to-be-done** *(truncated n=2)* | **+13pp** (100% / 88%) | +48% | ✅ Three-dimensions framework enforcement |
| **storybrand-messaging** *(truncated n=2)* | **+10pp** (80% / 70%) | +248% | ⚠️ Win but extreme bloat; one-liner fixture tied |
| **rubinstein-productions-coo** *(truncated n=2)* | **+90pp** (100% / 10%) | +24% | ⭐⭐ Strongest delta in full suite — baseline knows nothing about RP tiers |
| **writing-skills** *(truncated n=2)* | **+75pp** (88% / 13%) | +21% | ⭐⭐ TDD methodology invisible to baseline |
| **stop-slop** *(truncated n=2)* | **+67pp** (83%+100%prog / 17%+50%prog) | ~tied words | ⭐ Restraint + pattern removal discipline |
| **humanizer** *(truncated n=2)* | **+0pp qual** (60% / 60%) | ~tied words | ⚠️ Tied on qual; +33pp prog (em-dash removal) |

---

## Patterns

### Pattern 1: Skills win when they enforce canonical vocabulary

Baseline Opus writes good content but uses its own vocabulary. Skills that impose a shared language win measurably:

- **review-audit** — baseline never uses "pain points / trigger moments / objections / transformations / standout language" buckets; skill enforces them
- **brand-intake** — baseline doesn't know the 3 Say-Why questions exist
- **creative-strategy-engine** — baseline invents personas ("Story Carrier", "Pitch-Fatigued Operator") instead of canonical ED/Founder/Org-in-transition
- **proposal-scope-builder** — baseline uses ad-hoc section headers; skill enforces 6-section structure + "Bilingual Dashboard" naming
- **project-management-coordinator** — baseline skips Mirror/Map/Territory tier language

These skills exist to impose a **shared vocabulary** so downstream work stays coherent. That's real load-bearing work.

### Pattern 2: Skills win when they enforce restraint

- **outreach-email-manager** — -30% word count
- **brand-intake** — -39% word count (while also adding Say-Why module)

Baseline Opus expands. Skills that cap it produce tighter outputs without losing quality.

### Pattern 3: Skills tie when the task is general synthesis

- **market-listener** (warm leads / competitor moves / topic trends)
- **content-publishing-agent** (LinkedIn posts / case studies / essays)
- **finance-hub** (can-I-afford questions / burn rate)
- **hook-writing** (creative ad opening lines)

These skills ask Claude to synthesize context into content. Baseline Opus does this well, and adding protocol ceremony mostly inflates word count without improving the rubric. **These skills may be redundant at the Opus capability tier**, though they might matter at Sonnet/Haiku or for enforcing voice consistency over time.

### Pattern 4: Protocol ceremony can leak transactional framing

**network-stewardship** adds 9pp qual lift but fails programmatic checks: "funnel", "engagement", "highest stakes for the RP layer... if it becomes pipeline". The protocol scaffolding paradoxically makes the output more transactional than baseline. **This skill needs revision.**

---

## Per-skill recommendations

### Keep as-is (proven value)
- **review-audit** — +33pp. Canonical framework enforcement is real.
- **proposal-scope-builder** — +17pp. Keep.
- **brand-intake** — +13pp + -39% words. Say-Why module is doing work.
- **outreach-email-manager** — -30% words. Keep.
- **creative-strategy-engine** — +7pp. Canonical personas.
- **project-management-coordinator** — +6pp. RP framing.

### Revise
- **network-stewardship** — protocol language leaks "funnel/engagement/pipeline" framing. Rewrite to remove transactional vocabulary at the skill level, not just ask Claude to avoid it.

### Candidates for deprecation or narrow redefinition
- **market-listener** — tied on analysis. Skill may only need to own signals.json reading, not analytical output shape.
- **content-publishing-agent** — tied. Maybe skill should only enforce Pillar/Level/Platform metadata, not drive the writing.
- **finance-hub** — tied. Baseline does the math correctly; skill's tax-reserve flow might matter long-term but doesn't show in these fixtures.
- **hook-writing** — inconclusive at 3 fixtures. Needs 6-8 fixtures to call (earlier recommendation still stands).

---

## Meta-findings

### 1. Baseline Claude Opus is strong
Almost every skill tested shows baseline hitting 80%+ on qualitative rubrics. The skill's job is not to make Claude good — Claude is already good. The skill's job is to make Claude **consistent with RP's specific language**.

### 2. Skills that enforce canonical vocabulary show the clearest deltas
+33pp (review-audit), +17pp (proposal-scope-builder), +13pp (brand-intake). The unifying pattern: baseline doesn't know specific RP terms exist (Say-Why, Bilingual Dashboard, 5 buckets), so it substitutes its own.

### 3. Word count is a real dimension
brand-intake (-39%) and outreach-email-manager (-30%) win on brevity. Several other skills (finance-hub +43%, network-stewardship +36%, CSE +53%) lose on bloat even when tying on quality. Adding protocol ceremony costs 30-50% more words with unclear benefit.

### 4. Qualitative grader subagents produce honest judgments
Independent grader with no session context flagged network-stewardship's transactional leak despite the skill "winning" on the rubric. Use the grader's evidence lines to spot regressions.

---

## Harness-level findings (from building the evaluation infrastructure)

### Design lessons from iteration-1 build

**Bug 1: Regex checks produced false positives on quoted language.**
The `no_consultant_speak` assertion flagged both with_skill and baseline emails for using "curated" and "elevated" — but those words were being *quoted from the prospect's website* as the diagnostic opener, not authored by Isaac. Both modes failed equally so the A/B signal survived, but absolute scores were understated.
**Fix:** `regex_absent` in `lib/grade.py` now defaults to `quote_aware: true` — text inside `"..."`, `'...'`, or curly quotes is stripped before matching. Skills that *should* catch quoted language (e.g. prohibiting specific customer-facing words) can opt out with `"quote_aware": false`.

**Bug 2: Re-running `grade.py` wiped qualitative grades.**
Initial grader overwrote `grading.json` with stubbed `"pending_llm_grade"` entries every run, losing the LLM-graded qualitative judgments that had taken a subagent to produce. Discovered mid-session when regenerating the outreach-email-manager benchmark.
**Fix:** `grade.py` now reads existing `grading.json` before writing and preserves any qualitative grades where `passed` is not null, keyed by `assertion_id`. Rerunning is now idempotent.

**Both fixes are in `lib/grade.py` as of 2026-04-05.** If running evals on skills from before that date, regrade qualitatively (the grader subagent handles preserve-semantics correctly).

### Baseline isolation caveat

Baseline subagents share the same Claude Code installation as with_skill subagents — all 29 personal skills are visible to both. The A/B isolation comes from **prompt instruction** ("Do NOT invoke any skills") rather than actual tool gating. This is a known limitation: a baseline subagent could in principle autonomously decide to load a relevant skill mid-task. The grader would notice if the baseline output suddenly reflected skill-specific vocabulary, but this hasn't been explicitly audited.

### Cost per eval (Opus at 1M context)

Concrete numbers from this session's subagent spawns:
- **Per eval subagent:** ~140-150K tokens, 15-60s wall clock (median ~25s)
- **Per grader subagent:** ~175-200K tokens, 2-5 min wall clock
- **Per skill (3 fixtures × 2 modes + grader):** ~1M tokens, ~5 min wall clock
- **Per skill (5 fixtures × 2 modes + grader):** ~1.6M tokens, ~8 min wall clock

Full batch of 4 skills hit the usage limit mid-way through (5pm ET on 2026-04-05). The 11-skill total burn was roughly 11-12M tokens. Budget accordingly.

### Harness scope limits

The spawn-and-grade loop **can't be fully scripted**. The `Agent` tool only works inside a Claude Code session, so the subagent-spawn step always requires a live session. Only grading + aggregation (`grade.py` + `benchmark.py`) are CLI-callable.

A future `sd-eval-skill <name>` CLI could automate everything *except* the spawn step, writing subagent prompts to a file that a Claude Code session then reads and launches. Not worth building until the skill roster stabilizes.

---

## Batch C results (2026-04-06, truncated n=2)

Four discovery-framework skills tested at n=2 fixtures each (truncated from standard n=3-5 to get directional signal cheaply). **Pattern 1 confirmed across all four: skills that enforce canonical vocabulary show clear deltas.**

### made-to-stick — ⭐ +40pp (strongest Batch C signal)
Baseline never names SUCCESs principles or Curse of Knowledge in the tagline fixture (2/5 qual). Skill enforces full framework scoring. The diagnose fixture was closer (4/5 vs 5/5) — baseline knows to critique jargon but doesn't use the SUCCESs lens. **Word bloat is significant (+125%)** because the skill produces framework scorecards.

### mom-test — ⭐ +25pp
Biggest gap on the discovery-interview fixture: baseline pitched Say Why to the interviewee (violating Rule 1) and included hypothetical questions (violating Rule 2). Skill prevented both. Critique fixture tied (both flagged all 5 questions). **Moderate word bloat (+16%).**

### jobs-to-be-done — ✅ +13pp
Skill wins on churn-analysis (baseline missed three-dimensions framework). Videography fixture tied — baseline knew JTBD vocabulary well enough. **Notable bloat (+48%).**

### storybrand-messaging — ⚠️ +10pp but concerning bloat (+248%)
One-liner fixture tied (3/5 both) — both failed on three-levels-of-problem. Homepage fixture was the skill's win (5/5 vs 4/5). **Extreme word bloat:** skill produced 518 words avg vs baseline 149. The skill's SB7 mapping ceremony adds volume without proportional qual lift. **Candidate for trimming protocol ceremony.**

### Batch C recommendations
- **Keep:** made-to-stick, mom-test — canonical vocabulary enforcement is real and baseline doesn't replicate it
- **Keep with bloat watch:** jobs-to-be-done — win is real but word bloat needs monitoring
- **Trim:** storybrand-messaging — +10pp doesn't justify +248% words. Strip the SB7 mapping scaffold; keep only the three-levels-of-problem and customer-as-hero enforcement

---

## Batch D results (2026-04-06, truncated n=2)

Four writing/ops skills tested at n=2 fixtures each. **Batch D produced the two largest deltas in the entire eval suite.**

### rubinstein-productions-coo — ⭐⭐ +90pp (strongest signal across all 19 skills)
Baseline scores 1/10 on qualitative — it knows nothing about RP pricing tiers (Mirror/Map/Territory), capacity protocols, or Say Why framing. Skill enforces the full operational architecture. **Moderate word bloat (+24%).** This is the clearest case for Pattern 1: the skill encodes an entire operational vocabulary that baseline cannot replicate.

### writing-skills — ⭐⭐ +75pp
Baseline produces generic "how to write a skill file" advice (0/4 on draft fixture). Skill enforces TDD methodology: RED-GREEN-REFACTOR, pressure scenarios, watch-it-fail-first. The evaluate fixture was closer (skill 3/4 vs baseline 1/4) — skill missed explicitly naming "TDD" in one run. **Low bloat (+21%).**

### stop-slop — ⭐ +67pp qual + +50pp prog
Strong restraint enforcement. Skill bio output scored 4/4 qual + 1/1 prog; baseline bio scored 0/4 qual + 0/1 prog (kept em-dash, passive voice, pull-quote, abstractions). Paragraph fixture was closer. **Word counts matched (~54 words) — both skills produce tight output, but skill hits more quality marks.** This is Pattern 2 (restraint) at its purest.

### humanizer — ⚠️ Tied on qual (60%/60%), +33pp prog
Both modes produced clean rewrites. Skill's only edge was programmatic: em-dash removal in the paragraph fixture (baseline kept it). The `names_specific_patterns` universal assertion failed for ALL runs — both modes just returned clean prose without annotating which AI patterns they detected. **Fixture design issue:** the "humanize this" prompt invites rewriting, not analysis. Humanizer may still add value as a checklist enforcer, but these fixtures didn't capture it.

### Batch D recommendations
- **Keep:** rubinstein-productions-coo (irreplaceable), writing-skills (TDD methodology), stop-slop (restraint discipline)
- **Revisit fixtures:** humanizer — rewrite fixtures to ask "identify AI patterns AND rewrite" to test the analytical layer, not just the output
- **Consider merging:** humanizer + stop-slop overlap significantly. stop-slop wins on both qual and prog. Humanizer's pattern-naming capability is its differentiator but current fixtures don't test it.

---

## Raw data

All per-skill benchmarks in `<skill-name>/iteration-1/benchmark.md`.
Fixtures + assertions in `<skill-name>/evals.json`.
Harness code in `lib/{grade.py,benchmark.py}`.

**Tested (19 skills):**
Batch A/B (full, n=3-5): outreach-email-manager, proposal-scope-builder, hook-writing,
content-publishing-agent, network-stewardship, finance-hub, project-management-coordinator,
brand-intake, creative-strategy-engine, review-audit, market-listener.
Batch C (truncated, n=2): mom-test, jobs-to-be-done, storybrand-messaging, made-to-stick.
Batch D (truncated, n=2): rubinstein-productions-coo, writing-skills, humanizer, stop-slop.

**All 19 testable skills evaluated.**

---

## Open questions

1. **Does network-stewardship need a rewrite to remove transactional language at the source?**
2. **Should market-listener be scoped down to signals.json plumbing only?**
3. **Do content-publishing-agent / finance-hub / hook-writing deserve to exist if baseline matches on rubrics?** Or do they enforce consistency over time in ways 3-fixture tests can't capture?
4. **Would deltas be bigger at Sonnet/Haiku?** Worth testing the top-win skills (review-audit, brand-intake, proposal-scope-builder) on weaker models to see if the skill lifts more when baseline capability drops.
5. ~~**Are the 8 remaining skills worth testing?**~~ All 19 skills tested.
6. **Should storybrand-messaging be trimmed?** +10pp doesn't justify +248% bloat. Strip SB7 mapping ceremony, keep core principles only.
7. **Does made-to-stick's +40pp hold at full n=5?** Strongest Batch C signal; worth expanding fixtures to confirm.
8. **Should humanizer + stop-slop merge?** stop-slop wins on both qual (+67pp) and prog (+50pp). Humanizer ties baseline. Consider folding humanizer's pattern-naming checklist into stop-slop.
9. **Humanizer needs fixture redesign.** Current "humanize this" prompt tests rewriting (which baseline does well). Test the analytical layer: "identify AI patterns AND then rewrite."
