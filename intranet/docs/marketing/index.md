# Marketing & Content

Marketing at RP is not promotion. It is the public face of the practice — published evidence of the work, articulated clearly enough that the right people recognize themselves and reach out.

The department has three jobs: **publish**, **measure**, **refine voice**. It does not chase virality, run ads, or optimize for algorithmic attention.

---

## Charter

1. **Publish what is already true.** Content documents real work, real thinking, real moments — not aspirational positioning.
2. **Refuse slop.** See the anti-slop core item in `innovation/originality-factor.md`. If a piece of content could have been made by a generic creative studio, it does not ship.
3. **Measure what matters.** Engagement metrics are diagnostic, not performance-tracking. The question is "does this reach the people it was for?" — not "did this go viral?"
4. **Protect the voice.** RP's writing has a recognizable cadence. Every publishing decision reinforces or dilutes it.

---

## Artifacts

| Artifact | Location |
|---|---|
| Content strategy | toolkit root: `CONTENT-STRATEGY.md` |
| Published content log | `stack-data/data/content.json` (IG + YouTube) |
| Weekly engagement reports | `stack-data/reports/YYYY-MM-DD-content.md` |
| Visual assets catalog | `intranet/docs/visual-assets.md` |
| Case study template | `templates/case-study-template.md` |
| Newsletter dispatch | `intranet/docs/marketing/newsletter.md` |
| Content archive strategy | `intranet/docs/marketing/content-archive.md` |
| Brand identity | `intranet/docs/strategy/brand-identity.md` |

**Data loop:** IG scraped weekly via GH Actions → merged into `content.json` → weekly-content-report agent summarizes → action taken (or explicitly not).

---

## Governing skills (Creative stack pipeline)

The creative skills are arranged as a pipeline. Use them in order for a new content initiative:

```
brand-intake
    ↓
creative-strategy-engine
    ↓
    ├── hook-writing ─── (pulls: hook-tactics, hook-voice-patterns)
    └── creative-mechanics ─── (pulls: visual-formats)
    ↓
review-audit
```

| Skill | Role |
|---|---|
| `brand-intake` | First touch: what is this client/project actually about |
| `creative-strategy-engine` | Turn intake into a publishing thesis |
| `hook-writing` | Draft the opener |
| `hook-tactics` | Tactical patterns for openers that land |
| `hook-voice-patterns` | Voice calibration — is this recognizably RP |
| `creative-mechanics` | Structure the piece |
| `visual-formats` | Visual grammar + platform constraints |
| `review-audit` | Final check against originality factor |

Plus: `content-publishing-agent` for the publish/schedule step.

See `~/.claude/projects/-Users-isaacrubinstein/memory/reference_creative_stack.md` for full documentation.

---

## Voice firewall (HARD)

Marketing serves two practices with two distinct voices. See [Practices](../practices/index.md) for the full firewall.

| Practice | Voice | Channels |
|---|---|---|
| **Say Why** | Facilitator/filmmaker register; "Say the thing"; embodied/somatic vocabulary; Information Alchemist meta-identity | rubinsteinproductions.com, IG @rubinsteinproductions, YouTube @risaac09 |
| **Independent Evaluation** | Plain MPH-credentialed evaluation register; methodology-grounded; no Say Why brand language | isaacrubinstein.com, eval-side LinkedIn (when reactivated), capabilities statements, RFP submissions |

**No mixing.** A nonprofit ED scanning eval RFPs should not see RP's voice; a Say Why client should not see institutional eval language. Drift between the two registers is the highest reputation risk to either practice. Apply `stop-slop` discipline both ways.

---

## Channels

| Channel | Practice | Status | Purpose |
|---|---|---|---|
| Instagram (@rubinsteinproductions) | Say Why | Primary, active | Portfolio + practice documentation |
| YouTube (@risaac09) | Say Why | Active | Long-form (training, methodology, client films) |
| rubinsteinproductions.com | Say Why | Live | Marketing site / front door |
| isaacrubinstein.com | Eval | In build (Phase 0 deliverable) | Eval-side positioning + capabilities |
| Email list (newsletter) | Say Why | Building — monthly cadence | Owned audience insurance; see `marketing/newsletter.md` |
| LinkedIn | Eval (primary) | Dormant | Reactivate when eval BD volume justifies it |
| TikTok | — | Not active | Explicit non-goal |

**Platform diversification strategy:** IG + YouTube is the current stack. If algorithmic cooling happens on either, shift weight. An owned channel (email) is the planned insurance.

---

## Operating principles

**One publishing intention per piece.** If a post is doing three things, it's doing none well.

**Weekly commitment: one post minimum.** The rhythm is one published piece per week. If the week produces nothing worth saying, the post documents that honestly — a quiet dispatch about what's alive in the practice, not filler. The cost of slop is audience erosion; the cost of silence is invisibility. Neither is free — the weekly rhythm forces the question every week.

**Attribute real people.** Clients, collaborators, subjects get tagged, named, credited. Content is relational.

**Metrics are signals, not scores.** Low engagement on a specific post = data about fit, not judgment of the work.

**Weekly rhythm:**
- Monday: IG scrape runs (GH Actions)
- Weekly: read the generated report, make one decision from it
- Monthly: re-read recent posts as a batch, check voice drift

---

## Metrics that matter

| Metric | Why | Where |
|---|---|---|
| Engagement rate (likes+comments ÷ views) | Fit signal | weekly-content-report |
| Top performer per 4-week window | What is landing now | weekly-content-report |
| Quiet accounts | Relationships going cold | weekly-content-report |
| Post cadence (posts/month) | Practice activity signal | stack-data queries |
| Warm leads attributable to content | Content → BD conversion | contacts.json, anecdotal |

**Counter-metrics (explicitly not tracked):** follower count, view count as vanity, posting streaks, "reach" divorced from engagement.

---

## Relationships to other departments

| Dept | Flow |
|---|---|
| **Executive** | Receives: positioning that content should reflect. Sends: emerging voice or topics. |
| **R&D** | Receives: new language, new formats worth publishing. Sends: which experiments are publicly ready. |
| **Delivery** | Receives: engagements worth documenting. Sends: case-study candidates. |
| **BD** | Sends: warm signals (who's engaging repeatedly, who's quiet). Receives: which relationships need content attention. |
| **People** | Sends: capacity signals ("no new shoots this week"). Gates output when depleted. |

---

## Gaps / open questions

