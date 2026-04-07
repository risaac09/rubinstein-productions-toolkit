# creative-strategy-engine — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (3/3) | 100.0% (3/3) | +0.0pp |
| Qualitative | 100.0% (15/15) | 93.3% (14/15) | +6.7pp |
| Mean word count | 1689 | 1102 | +587 (+53%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| rp-strategy-map | with_skill | 1/1 | 5/5 | 2417 |
| rp-strategy-map | baseline | 1/1 | 4/5 | 1355 |
| paid-ads-pain-persona | with_skill | 1/1 | 5/5 | 1467 |
| paid-ads-pain-persona | baseline | 1/1 | 5/5 | 1252 |
| awareness-stage-mapping | with_skill | 1/1 | 5/5 | 1182 |
| awareness-stage-mapping | baseline | 1/1 | 5/5 | 698 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Uses canonical RP personas (ED / Founder / Org-in-transition) rather t | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py creative-strategy-engine && python3 ../lib/benchmark.py creative-strategy-engine`
