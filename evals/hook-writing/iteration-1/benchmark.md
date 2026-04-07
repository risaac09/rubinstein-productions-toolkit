# hook-writing — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (10/10) | 100.0% (10/10) | +0.0pp |
| Qualitative | 93.3% (14/15) | 93.3% (14/15) | +0.0pp |
| Mean word count | 224 | 165 | +59 (+36%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| problem-aware-sleep-supplement | with_skill | 3/3 | 4/5 | 177 |
| problem-aware-sleep-supplement | baseline | 3/3 | 5/5 | 169 |
| solution-aware-project-mgmt-saas | with_skill | 3/3 | 5/5 | 180 |
| solution-aware-project-mgmt-saas | baseline | 3/3 | 4/5 | 100 |
| unaware-therapy-for-founders | with_skill | 4/4 | 5/5 | 315 |
| unaware-therapy-for-founders | baseline | 4/4 | 5/5 | 225 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Hooks SHOW the pain (scene, moment, sensation) rather than TELLING it  | 1 | 0 |
| Output uses VARIETY of psychological trigger types (not all the same c | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py hook-writing && python3 ../lib/benchmark.py hook-writing`
