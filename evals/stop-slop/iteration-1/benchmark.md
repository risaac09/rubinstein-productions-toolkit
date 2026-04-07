# stop-slop — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (2/2) | 50.0% (1/2) | +50.0pp |
| Qualitative | 83.3% (5/6) | 16.7% (1/6) | +66.6pp |
| Mean word count | 54 | 54 | +0 (+0%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| clean-ai-paragraph | with_skill | 1/1 | 2/3 | 50 |
| clean-ai-paragraph | baseline | 1/1 | 1/3 | 31 |
| clean-ai-bio | with_skill | 1/1 | 3/3 | 57 |
| clean-ai-bio | baseline | 0/1 | 0/3 | 78 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Breaks or rewrites at least one three-item list into a different rhyth | 1 | 1 |
| Rewrites passive constructions into active voice with human subjects | 0 | 2 |
| Output contains no em dashes | 0 | 1 |
| Replaces at least one vague abstraction ('transformative experience',  | 0 | 1 |
| Removes or rewrites pull-quote-sounding lines like 'Her work doesn't j | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py stop-slop && python3 ../lib/benchmark.py stop-slop`
