# rubinstein-productions-coo — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 100.0% (10/10) | 10.0% (1/10) | +90.0pp |
| Mean word count | 708 | 571 | +137 (+24%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| opportunity-triage | with_skill | 0/0 | 5/5 | 783 |
| opportunity-triage | baseline | 0/0 | 1/5 | 682 |
| capacity-pricing-check | with_skill | 0/0 | 5/5 | 633 |
| capacity-pricing-check | baseline | 0/0 | 0/5 | 460 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| References specific RP pricing tiers (Mirror / Map / Territory) with d | 0 | 2 |
| Frames RP as Say Why / narrative practice, NOT as a videograp | 0 | 2 |
| Maps the opportunity to a specific RP tier (likely Map) and checks it  | 0 | 1 |
| Flags the 'marketing video' framing as a potential mismatch with RP's  | 0 | 1 |
| Uses canonical audience segments (ED, Founder, Org-in-transition) not  | 0 | 1 |
| Explicitly evaluates current capacity against a limit or guideline bef | 0 | 1 |
| Derives pricing from the RP tier/offer ladder structure, not ad-hoc ma | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py rubinstein-productions-coo && python3 ../lib/benchmark.py rubinstein-productions-coo`
