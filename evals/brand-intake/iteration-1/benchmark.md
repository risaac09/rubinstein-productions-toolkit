# brand-intake — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (3/3) | 66.7% (2/3) | +33.3pp |
| Qualitative | 100.0% (15/15) | 86.7% (13/15) | +13.3pp |
| Mean word count | 477 | 776 | -299 (-39%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| rp-prospect-intake | with_skill | 1/1 | 5/5 | 533 |
| rp-prospect-intake | baseline | 0/1 | 4/5 | 725 |
| paid-ads-brand-intake | with_skill | 1/1 | 5/5 | 281 |
| paid-ads-brand-intake | baseline | 1/1 | 5/5 | 916 |
| partial-info-intake | with_skill | 1/1 | 5/5 | 617 |
| partial-info-intake | baseline | 1/1 | 4/5 | 688 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Avoids generic discovery cliches | 0 | 1 |
| Includes the 3 Say Why questions (voice integrity, inside version, jar | 0 | 1 |
| Includes Say Why module since this is an RP client | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py brand-intake && python3 ../lib/benchmark.py brand-intake`
