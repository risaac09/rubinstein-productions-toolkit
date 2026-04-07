# review-audit — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 100.0% (18/18) | 61.1% (11/18) | +38.9pp |
| Mean word count | 793 | 599 | +194 (+32%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| single-product-reviews | with_skill | 0/0 | 6/6 | 819 |
| single-product-reviews | baseline | 0/0 | 3/6 | 639 |
| multi-product-reviews | with_skill | 0/0 | 6/6 | 894 |
| multi-product-reviews | baseline | 0/0 | 4/6 | 545 |
| ad-ready-language | with_skill | 0/0 | 6/6 | 666 |
| ad-ready-language | baseline | 0/0 | 4/6 | 612 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Organizes output into the 5 canonical buckets (pain points, trigger mo | 0 | 3 |
| References scoring reviews 1-5 for quality before analysis | 0 | 3 |
| Scores each review 1-5 explicitly | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py review-audit && python3 ../lib/benchmark.py review-audit`
