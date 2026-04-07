# made-to-stick — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 100.0% (10/10) | 60.0% (6/10) | +40.0pp |
| Mean word count | 827 | 367 | +460 (+125%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| diagnose-weak-message | with_skill | 0/0 | 5/5 | 1219 |
| diagnose-weak-message | baseline | 0/0 | 4/5 | 653 |
| craft-sticky-tagline | with_skill | 0/0 | 5/5 | 435 |
| craft-sticky-tagline | baseline | 0/0 | 2/5 | 81 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| References or applies the Curse of Knowledge concept | 0 | 2 |
| Explicitly names the SUCCESs principles (at least 4 of the 6: Simple,  | 0 | 1 |
| Pushes concrete sensory language over abstract jargon | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py made-to-stick && python3 ../lib/benchmark.py made-to-stick`
