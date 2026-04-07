# writing-skills — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 87.5% (7/8) | 12.5% (1/8) | +75.0pp |
| Mean word count | 876 | 724 | +152 (+21%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| draft-new-skill | with_skill | 0/0 | 4/4 | 1199 |
| draft-new-skill | baseline | 0/0 | 0/4 | 848 |
| evaluate-skill-draft | with_skill | 0/0 | 3/4 | 554 |
| evaluate-skill-draft | baseline | 0/0 | 1/4 | 600 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| References or applies the RED-GREEN-REFACTOR cycle (or test-driven dev | 1 | 2 |
| Mentions pressure scenarios, failure modes, or edge cases as the start | 0 | 2 |
| Advises starting by identifying what goes wrong WITHOUT the skill (wat | 0 | 1 |
| Includes a testing/verification step where the skill is tested against | 0 | 1 |
| Points out that there are no test cases or verification criteria to kn | 0 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py writing-skills && python3 ../lib/benchmark.py writing-skills`
