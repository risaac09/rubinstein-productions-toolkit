# storybrand-messaging — Eval Results (iteration-1)

**Date:** 2026-04-06
**Sample:** 2 fixtures × 2 modes = 4 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 0% (0/0) | 0% (0/0) | +0.0pp |
| Qualitative | 80.0% (8/10) | 70.0% (7/10) | +10.0pp |
| Mean word count | 518 | 149 | +369 (+248%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| rp-one-liner | with_skill | 0/0 | 3/5 | 372 |
| rp-one-liner | baseline | 0/0 | 3/5 | 52 |
| homepage-hero-copy | with_skill | 0/0 | 5/5 | 664 |
| homepage-hero-copy | baseline | 0/0 | 4/5 | 246 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Names or applies the three levels of problem: external, internal, phil | 1 | 2 |
| Positions the guide with both empathy and authority (competence) | 1 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py storybrand-messaging && python3 ../lib/benchmark.py storybrand-messaging`
