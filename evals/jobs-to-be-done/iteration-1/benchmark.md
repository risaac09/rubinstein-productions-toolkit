# jobs-to-be-done — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (2/2) | 100.0% (2/2) | +0.0pp |
| Qualitative | 100.0% (8/8) | 87.5% (7/8) | +12.5pp |
| Mean word count | 1077 | 726 | +351 (+48%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| churn-analysis | with_skill | 1/1 | 4/4 | 1025 |
| churn-analysis | baseline | 1/1 | 3/4 | 558 |
| videography-job-discovery | with_skill | 1/1 | 4/4 | 1129 |
| videography-job-discovery | baseline | 1/1 | 4/4 | 893 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Names or clearly invokes the three dimensions of a job (functional, em | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py jobs-to-be-done && python3 ../lib/benchmark.py jobs-to-be-done`
