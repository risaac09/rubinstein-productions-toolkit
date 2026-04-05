# Business Development

BD at RP is the care and feeding of the relational network that generates work. It is not sales. It does not convert. It **stewards**.

A solo practice grows through the integrity of Isaac's relationships over time. BD is the function that keeps that network alive, warm, and accurate in its understanding of what RP actually does.

---

## Charter

1. **Stewardship over pursuit.** Relationships are maintained independent of whether they currently yield work.
2. **Clarity over courtship.** Every interaction should leave the other person with a clearer understanding of RP's work. Vague interest becomes informed decision.
3. **Fit first.** A wrong-fit engagement harms both parties. Qualifying out is more valuable than closing in.
4. **Proposals are shared thinking.** A proposal is an articulation of what Isaac *sees* about the client's situation, not a pitch deck.

---

## Artifacts

| Artifact | Location |
|---|---|
| Contacts (clients, leads, collaborators) | `stack-data/data/contacts.json` |
| Client briefs (AI-generated per contact) | `stack-data/briefs/` |
| Pricing guardrails | `intranet/docs/operations/pricing-guardrails.md` |
| Client communication standards | `intranet/docs/operations/client-communication.md` |
| Engagement lifecycle (handoff to Delivery) | `intranet/docs/operations/engagement-lifecycle.md` |
| Discovery call structure | `intranet/docs/operations/discovery-call.md` |
| Retainer renewal cadence | `intranet/docs/operations/retainer-renewal.md` |
| Referral rhythm (quarterly stewardship) | `intranet/docs/business-development/referral-rhythm.md` |
| Contact touches / lightweight CRM | `intranet/docs/business-development/crm-touches.md` |

---

## Governing skills

| Skill | Role |
|---|---|
| `outreach-email-manager` | Drafts first-touch and follow-up emails; logs the send |
| `proposal-scope-builder` | Structures scope, deliverables, timeline, pricing into a proposal |
| `network-stewardship` | Recurring touchpoints with warm contacts |
| `mom-test` | Discovery framework — learn what clients actually need, not what they say they need |
| `jobs-to-be-done` | Frame new engagements by the job the client is hiring RP to do |
| `storybrand-messaging` | Structure client-facing language when it drifts abstract |

---

## Service tiers (the offering)

| Tier | Shape | Price range | Purpose |
|---|---|---|---|
| **The Mirror** | Single facilitation session | $500–1.5k | Entry point, high clarity |
| **The Map** | Say The Thing package | $5–12k | Articulating the hard-to-name thing |
| **The Territory** | Ongoing retainer | $4–8k/mo | Deep, continuous relationship |

See `operations/pricing-guardrails.md` for the rules behind these numbers.

---

## Pipeline stages

Current contacts schema uses `status` field. Proposed stage progression:

```
new-contact → qualified → proposal-sent → engaged → active-client → past-client
                                              ↓
                                         declined / wrong-fit
```

**Transitions are manual.** No automation; each stage change is a conscious judgment.

| Stage | Definition | What it needs |
|---|---|---|
| new-contact | Met, not yet a conversation | Intro, listen, note in contacts.json |
| qualified | There's a real possible fit | Brief generated via client-brief agent |
| proposal-sent | Scope articulated and sent | `proposal-scope-builder` output |
| engaged | Signed, scheduled | Handoff to Delivery (engagement lifecycle) |
| active-client | In a Mirror/Map/Territory | — |
| past-client | Engagement delivered | Network stewardship rhythm |

---

## Operating principles

**No cold outreach.** All BD is warm — either directly met, mutually connected, or followed someone's public work and have something specific to offer.

**Qualify out fast.** The sooner a wrong fit is named, the more respect both parties retain. "This isn't for us right now" is a complete sentence.

**Budget is discussed early.** Pricing guardrails are guardrails; the conversation is real. No "custom quotes" as stalling.

**Follow-up is not pressure.** A follow-up email is a signal of continued care, not a request to advance the deal.

**Network stewardship rhythm:** Monthly — review past clients, reach out to 2–3 who are overdue for a touchpoint, independent of whether there's new work to offer.

---

## Metrics that matter

| Metric | Why | Where |
|---|---|---|
| Active pipeline count (by stage) | Forward visibility | contacts.json queries |
| Conversion to paid by source | Which channels produce fit | Anecdotal + contact `source` field |
| Time-to-first-response | Responsiveness discipline | Manual tracking |
| Qualifying-out rate | Health check — are bad fits being filtered? | Anecdotal |
| Repeat engagement rate | Work quality signal | financials.json + projects.json |

**Counter-metric:** Lead volume. More leads is not better. Better-fit leads is better.

---

## Relationships to other departments

| Dept | Flow |
|---|---|
| **Executive** | Receives: who RP is for, positioning. Sends: market feedback (who's asking, what they're asking for). |
| **Marketing** | Receives: warm-engagement signals from content. Sends: what content would help close specific deals (rarely — content is not a BD instrument). |
| **Delivery** | Sends: signed engagements. Receives: client realities that reshape future pricing/scoping. |
| **Finance** | Partners on pricing at the deal level. Finance sets guardrails; BD negotiates within them. |
| **People** | Gated by: capacity. A signed Territory engagement requires People sign-off. |
| **R&D** | Pilots live here — R&D experiments often first meet reality through a BD conversation. |

---

## Gaps / open questions

- **Pipeline stage automation** — schema now validates `pipelineStage` enum (see `contacts.schema.json`). No state-machine transition enforcement yet. Acceptable for current volume.
- **CRM beyond contacts.json** — `touches[]` array added to contact schema; manual append until sd-touches CLI justified. See `crm-touches.md`.
