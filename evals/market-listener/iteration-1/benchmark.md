# market-listener — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 100.0% (18/18) | 100.0% (18/18) | +0.0pp |
| Mean word count | 883 | 867 | +16 (+2%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| warm-lead-activity | with_skill | 0/0 | 6/6 | 979 |
| warm-lead-activity | baseline | 0/0 | 6/6 | 983 |
| competitor-moves | with_skill | 0/0 | 6/6 | 956 |
| competitor-moves | baseline | 0/0 | 6/6 | 929 |
| topic-trend | with_skill | 0/0 | 6/6 | 714 |
| topic-trend | baseline | 0/0 | 6/6 | 688 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py market-listener && python3 ../lib/benchmark.py market-listener`
