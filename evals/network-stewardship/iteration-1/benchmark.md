# network-stewardship — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 66.7% (4/6) | 100.0% (6/6) | -33.3pp |
| Qualitative | 91.7% (11/12) | 83.3% (10/12) | +8.4pp |
| Mean word count | 928 | 681 | +247 (+36%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| who-to-reach-out-to | with_skill | 1/2 | 3/4 | 903 |
| who-to-reach-out-to | baseline | 2/2 | 2/4 | 671 |
| reconnect-after-long-gap | with_skill | 2/2 | 4/4 | 1060 |
| reconnect-after-long-gap | baseline | 2/2 | 4/4 | 948 |
| gratitude-practice | with_skill | 1/2 | 4/4 | 822 |
| gratitude-practice | baseline | 2/2 | 4/4 | 424 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Does not frame relationships as metrics (engagement, conversion, ROI) | 2 | 0 |
| Does NOT frame Maya or Priya as potential client/referral opportunitie | 1 | 1 |
| Frames the task relationally (care, curiosity, gratitude) not transact | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py network-stewardship && python3 ../lib/benchmark.py network-stewardship`
