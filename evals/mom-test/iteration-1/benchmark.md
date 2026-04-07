# mom-test — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (2/2) | 100.0% (2/2) | +0.0pp |
| Qualitative | 87.5% (7/8) | 62.5% (5/8) | +25.0pp |
| Mean word count | 1144 | 986 | +158 (+16%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| critique-leading-questions | with_skill | 1/1 | 3/4 | 984 |
| critique-leading-questions | baseline | 1/1 | 3/4 | 810 |
| plan-discovery-interview | with_skill | 1/1 | 4/4 | 1303 |
| plan-discovery-interview | baseline | 1/1 | 2/4 | 1163 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Explicitly references the three Mom Test rules (their life not your id | 1 | 1 |
| Frames questions around what the person has actually done in the past, | 0 | 1 |
| Does not include any interview moment where Isaac pitches or describes | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py mom-test && python3 ../lib/benchmark.py mom-test`
