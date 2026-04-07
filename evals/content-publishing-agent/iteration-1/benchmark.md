# content-publishing-agent — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 3 fixtures × 2 modes = 6 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 100.0% (9/9) | 100.0% (9/9) | +0.0pp |
| Qualitative | 100.0% (13/13) | 100.0% (13/13) | +0.0pp |
| Mean word count | 259 | 221 | +38 (+17%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| linkedin-post-from-session | with_skill | 3/3 | 4/4 | 211 |
| linkedin-post-from-session | baseline | 3/3 | 4/4 | 172 |
| case-study-outline | with_skill | 2/2 | 5/5 | 322 |
| case-study-outline | baseline | 2/2 | 5/5 | 243 |
| essay-opening | with_skill | 4/4 | 4/4 | 243 |
| essay-opening | baseline | 4/4 | 4/4 | 249 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py content-publishing-agent && python3 ../lib/benchmark.py content-publishing-agent`
