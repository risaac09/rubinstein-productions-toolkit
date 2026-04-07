# outreach-email-manager — Eval Results (iteration-1)

**Date:** 2026-04-05
**Sample:** 5 fixtures × 2 modes = 10 runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | 68.8% (11/16) | 68.8% (11/16) | +0.0pp |
| Qualitative | 0.0% (0/26) | 0.0% (0/26) | +0.0pp |
| Mean word count | 154 | 220 | -66 (-30%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
| cold-nonprofit-visible-gap | with_skill | 2/3 | 0/5 | 108 |
| cold-nonprofit-visible-gap | baseline | 2/3 | 0/5 | 187 |
| cold-b2b-mission-aligned | with_skill | 2/3 | 0/5 | 149 |
| cold-b2b-mission-aligned | baseline | 2/3 | 0/5 | 213 |
| follow-up-after-discovery-call | with_skill | 3/4 | 0/5 | 255 |
| follow-up-after-discovery-call | baseline | 3/4 | 0/5 | 529 |
| proposal-submission | with_skill | 2/3 | 0/5 | 98 |
| proposal-submission | baseline | 2/3 | 0/5 | 57 |
| warm-intro-from-referral | with_skill | 2/3 | 0/6 | 158 |
| warm-intro-from-referral | baseline | 2/3 | 0/6 | 116 |

---

## Failure modes (assertions that failed)

| Assertion | with_skill fails | baseline fails |
|---|---|---|
| Body is 3-4 short paragraphs, roughly 80-130 words | 2 | 2 |
| Body is short, 40-100 words | 1 | 1 |
| Body is very short, 30-80 words | 1 | 1 |
| Body is short, 60-120 words (warm intro doesn't need long pitch) | 1 | 1 |

---

Raw outputs: `iteration-1/<eval-name>/<mode>/output.md`
Per-run grading: `iteration-1/<eval-name>/<mode>/grading.json`

Regenerate: `python3 ../lib/grade.py outreach-email-manager && python3 ../lib/benchmark.py outreach-email-manager`
