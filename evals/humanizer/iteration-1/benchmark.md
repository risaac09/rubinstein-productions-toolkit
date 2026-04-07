# humanizer — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (3/3) | 66.7% (2/3) | +33.3pp |
| Qualitative | 60.0% (3/5) | 60.0% (3/5) | +0.0pp |
| Mean word count | 116 | 118 | -2 (-2%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| clean-ai-paragraph | with_skill | 2/2 | 1/2 | 95 |
| clean-ai-paragraph | baseline | 1/2 | 1/2 | 80 |
| clean-ai-bio | with_skill | 1/1 | 2/3 | 137 |
| clean-ai-bio | baseline | 1/1 | 2/3 | 156 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Names specific AI writing patterns by category (e.g. 'rule of three',  | 2 | 2 |
| Removes or replaces the em dash construction | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py humanizer && python3 ../lib/benchmark.py humanizer`
