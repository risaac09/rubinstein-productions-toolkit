# project-management-coordinator — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 66.7% (2/3) | 66.7% (2/3) | +0.0pp |
| Qualitative | 100.0% (18/18) | 94.4% (17/18) | +5.6pp |
| Mean word count | 1061 | 791 | +270 (+34%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| new-booking-workflow | with_skill | 0/1 | 6/6 | 1407 |
| new-booking-workflow | baseline | 0/1 | 6/6 | 1073 |
| pre-shoot-prep | with_skill | 1/1 | 6/6 | 1497 |
| pre-shoot-prep | baseline | 1/1 | 6/6 | 1025 |
| phase-transition-comms | with_skill | 1/1 | 6/6 | 280 |
| phase-transition-comms | baseline | 1/1 | 5/6 | 275 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Avoids PM jargon (stakeholders, alignment, touchpoints, deliverables-c | 1 | 1 |
| Names something specific from the 3 excavation sessions (honors what w | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py project-management-coordinator && python3 ../lib/benchmark.py project-management-coordinator`
