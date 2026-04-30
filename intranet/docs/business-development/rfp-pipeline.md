# RFP Pipeline (Independent Evaluation)

> **Practice:** [Independent Evaluation](../practices/evaluation.md). The Say Why practice does not respond to RFPs.
>
> **Voice:** Plain MPH-credentialed evaluation register. See practice canonical for hard voice rules.

The eval practice generates work primarily through formal RFP/RFQ/grant solicitations. Unlike RP's warm-only BD model, the eval practice operates a real pipeline with weekly cadence, fit-scoring, and explicit go/no-go decisions.

## Operating cadence

**Monday morning — pipeline review:**

1. Open the current week's pipeline review at `03 Projects/Evaluation Consultancy/04 Pipeline/eval-pipeline-{{date}}.md`.
2. Triage URGENT (deadline ≤ 2 weeks), APPROACHING (2–6 weeks), and HORIZON columns.
3. For each new opportunity, run the `eval-rfp-analyzer` skill to produce a fit score and go/no-go recommendation.
4. Write the new pipeline file for the week, drawing from the previous week + new scans.
5. Identify the 1–3 opportunities to actively pursue this week.

**Throughout the week:**

- Draft proposals for GO opportunities using the `eval-proposal-builder` skill.
- Tailor capabilities statements per recipient using `capabilities-statement-generator`.
- Submit before the deadline; never the day-of.
- Log every submission in the contacts/touches system with the canonical RFP reference.

**Weekly volume target:** 1–3 substantive applications/week (varies by available RFPs and fit-scoring outcomes).

## Skills (eval-side BD stack)

| Skill | Role |
|---|---|
| `eval-rfp-analyzer` | Score fit, draft abstract paragraphs in plain eval voice, append to active pipeline |
| `eval-proposal-builder` | Generate styled proposal markdown + Python script for PDF generation |
| `capabilities-statement-generator` | Tailored one-page capabilities PDF for each recipient |
| `case-study-extractor` | Extract portfolio-quality case studies from completed engagements |
| `outreach-email-manager` | Submission emails, follow-ups, subcontractor outreach |
| `eval-rfp-scanner` (scheduled) | Routine cron-driven scan of funder/agency feeds |

## Fit scoring (the gate)

Every opportunity is scored on:

1. **Mission fit** — health equity, community health, behavioral health, public health workforce, cross-cultural / community-based programs?
2. **Methodology fit** — mixed-methods, participatory, developmental, MEAL framework, logic model / theory of change?
3. **Eligibility** — sole proprietor eligible? Subcontractor route required?
4. **Scope match** — sized to single-evaluator capacity? Or large enough to require teaming?
5. **Voice/register** — funder language compatible with the practice's positioning?
6. **Geography** — US-based now; Nordic/European procurement is Phase 3+ target.

Output: `GO`, `MAYBE` (needs partner / clarification), or `NO-GO` with reasoning logged.

## Phase 0 deliverables (gate every GO recommendation)

The `eval-rfp-analyzer` skill flags Phase 0 readiness gaps. As of 2026-04, the deliverables that gate every GO recommendation:

- Capabilities statement PDF, tailored per recipient (in build via `capabilities-statement-generator` skill)
- 2–3 portfolio case studies (Workday SCH 2026, RIPCA, ONB / CPU, Seattle Children's Mental Health Referral Service) — in build via `case-study-extractor` skill
- W-9 with EIN (in lieu of SSN) — open
- isaacrubinstein.com — ✅ live (SSL cert provisioning may still be completing)

Until these are in place, GO recommendations may be downgraded to MAYBE pending readiness.

## Active pipeline reference

Current and historical pipeline reviews:

```
03 Projects/Evaluation Consultancy/04 Pipeline/
├── eval-pipeline-2026-04-21.md  (most recent)
├── eval-pipeline-2026-04-20.md
└── ...
```

Active prospect dossiers:

```
03 Projects/Evaluation Consultancy/02 Prospects/
├── CampusCompact-CAP-2026/
└── RIPCA-HS1-2026/
```

## Pricing reference (eval side only)

| Service | Range | Floor |
|---|---|---|
| Subcontracting (hourly) | $125–175/hr | $125/hr |
| Direct client hourly | $150–200/hr | $150/hr |
| Smallest project engagement | — | $2,500 |
| External evaluation (small program, 1 yr) | $15,000–30,000 | — |
| External evaluation (multi-site, multi-year) | $40,000–100,000+ | — |
| Evaluation plan development | $3,000–8,000 | — |

Floor rates are HARD — no quotes below without explicit override. See [Independent Evaluation practice](../practices/evaluation.md).

## What does NOT belong in this pipeline

- Say Why facilitation engagements (those are RP's warm pipeline, contacts.json + outreach-email-manager → proposal-scope-builder)
- Cold-outreach prospecting for evaluation work (eval BD is RFP-driven and warm-network-driven, never cold)
- Opportunities that require evaluation voice + RP voice in a single deliverable (qualify out)
- Engagements below the floor rate without explicit override and a documented reason

## Cross-practice firewall

This pipeline operates entirely in eval voice. Submissions, capabilities statements, proposals, and follow-up correspondence use the canonical evaluation register. No "embodied," "metabolize," "alchemy," or Say Why brand language ever appears in a submitted document. Drift is the highest reputation risk to either practice.

## Related

- [Independent Evaluation practice](../practices/evaluation.md)
- [Practices index](../practices/index.md)
- [BD charter](index.md) (cross-cutting, both practices)
- `00 Canonical/Independent Evaluator - Positioning.md`
