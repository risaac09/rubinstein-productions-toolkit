# finance-hub — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 100.0% (15/15) | 100.0% (15/15) | +0.0pp |
| Mean word count | 502 | 352 | +150 (+43%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| client-payment-logged | with_skill | 0/0 | 5/5 | 375 |
| client-payment-logged | baseline | 0/0 | 5/5 | 296 |
| can-i-afford-question | with_skill | 0/0 | 5/5 | 504 |
| can-i-afford-question | baseline | 0/0 | 5/5 | 537 |
| burn-rate-check | with_skill | 0/0 | 5/5 | 626 |
| burn-rate-check | baseline | 0/0 | 5/5 | 224 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py finance-hub && python3 ../lib/benchmark.py finance-hub`
