# proposal-scope-builder — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (12/12) | 100.0% (12/12) | +0.0pp |
| Qualitative | 100.0% (23/23) | 82.6% (19/23) | +17.4pp |
| Mean word count | 1160 | 975 | +185 (+19%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| mirror-tier-founder | with_skill | 4/4 | 7/7 | 955 |
| mirror-tier-founder | baseline | 4/4 | 6/7 | 848 |
| map-tier-org-sprint | with_skill | 4/4 | 8/8 | 1119 |
| map-tier-org-sprint | baseline | 4/4 | 6/8 | 1094 |
| territory-tier-retainer | with_skill | 4/4 | 8/8 | 1405 |
| territory-tier-retainer | baseline | 4/4 | 7/8 | 982 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Follows the 6-section structure (Opening, Understanding, Proposed Enga | 0 | 3 |
| Names the Bilingual Dashboard as a deliverable | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py proposal-scope-builder && python3 ../lib/benchmark.py proposal-scope-builder`
