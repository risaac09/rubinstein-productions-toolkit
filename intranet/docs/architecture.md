# Digital Architecture

Rubinstein Productions is a solo practice. This page models it as a company — not because it is one, but because the architecture makes the practice's scope, seams, and self-governance visible.

The goal: every part of the work has a home. Nothing important is orphaned. Nothing is duplicated in two departments.

---

## Org chart (as a company)

```
                 ┌──────────────────────────────┐
                 │    EXECUTIVE / STRATEGY      │
                 │  mission, theory of change,  │
                 │  phase roadmap, positioning  │
                 └──────────────┬───────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
  ┌──────▼─────┐         ┌──────▼──────┐        ┌──────▼──────┐
  │ R&D /      │         │  DELIVERY / │        │  PEOPLE &   │
  │ INNOVATION │◀───┐    │  PRODUCTION │        │  PRACTICE   │
  │            │    │    │             │        │ (solo HR)   │
  │ originality│    │    │ engagement  │        │             │
  │ factor,    │    │    │ lifecycle,  │        │ capacity,   │
  │ experiments│    │    │ filmmaking, │        │ inner work, │
  └────────────┘    │    │ facilitation│        │ coaching    │
         ▲          │    └──────┬──────┘        └─────────────┘
         │          │           │
         │    ┌─────▼─────┐     │
         │    │ MARKETING │     │
         │    │ & CONTENT │     │
         │    │           │     │
         │    │ publishing│     │
         │    │ creative  │     │
         │    │ stack,    │     │
         │    │ hook voice│     │
         │    └─────┬─────┘     │
         │          │           │
         │    ┌─────▼──────┐    │
         │    │ BUSINESS   │    │
         │    │ DEVELOPMENT│◀───┘
         │    │            │
         │    │ outreach,  │
         │    │ proposals, │
         │    │ network    │
         │    └────────────┘
         │
  ┌──────┴──────┐              ┌─────────────┐
  │  FINANCE    │              │     IT      │
  │             │              │             │
  │ invoices,   │              │ stack-data, │
  │ KPIs, ROI,  │◀────────────▶│ agents,     │
  │ pricing     │              │ runbooks,   │
  │             │              │ secrets     │
  └─────────────┘              └─────────────┘
```

R&D feeds every client-facing department (originality engine → what gets delivered, how it's marketed, what's sold). IT and Finance are horizontal services that cross all departments.

---

## Department map

Each department has a primary artifact, a set of supporting skills/agents, and a home in this intranet or in the toolkit.

### Executive / Strategy

**What it does:** Defines what the practice is for, who it serves, how it grows.

| Artifact | Location |
|---|---|
| Mission & positioning | `strategy/mission-and-positioning.md` |
| Theory of change | `strategy/theory-of-change.md` |
| Phase roadmap | `strategy/phase-roadmap.md` |
| Brand identity | `strategy/brand-identity.md` |
| Competitive landscape | `strategy/competitive-landscape.md` |

**Governing skill:** `rubinstein-productions-coo`

---

### R&D / Innovation

**What it does:** Evolves methodology, tools, and products while protecting the originality factor. Runs experiments, tracks research, kills failed directions.

| Artifact | Location |
|---|---|
| Originality factor (core DNA) | `innovation/originality-factor.md` |
| Active experiments | `innovation/experiments.md` |
| Creative research log | `innovation/research-log.md` |
| Methodology evolution | `learning/four-phases.md`, `learning/bilingual-dashboard.md` |

**Governing skills:** `information-alchemist-os`, `quantum-mirror-inner-practice`

---

### Delivery / Production

**What it does:** Executes client work — facilitation sessions, film production, Say The Thing packages, Territory retainers.

| Artifact | Location |
|---|---|
| Engagement lifecycle SOPs | `operations/engagement-lifecycle.md` |
| Equipment & logistics | `operations/equipment-logistics.md` |
| Decision frameworks | `operations/decision-frameworks.md` |
| Project lifecycle template | `rp-lifecycle` PWA |

**Governing skills:** `project-management-coordinator`, `rubinstein-productions-agent`

---

### Marketing & Content

**What it does:** Publishes content, builds distribution, shapes voice, measures engagement.

| Artifact | Location |
|---|---|
| Content strategy | toolkit root: `CONTENT-STRATEGY.md` |
| Creative stack pipeline | `~/.claude/skills/` (8 skills: brand-intake → review-audit) |
| Published content log | `stack-data/data/content.json` |
| Weekly engagement reports | `stack-data/reports/YYYY-MM-DD-content.md` |
| Visual assets | `intranet/docs/visual-assets.md` |

**Governing skills:** `content-publishing-agent`, `hook-writing`, `hook-tactics`, `hook-voice-patterns`, `creative-strategy-engine`, `brand-intake`, `review-audit`, `creative-mechanics`, `visual-formats`

---

### Business Development

**What it does:** Stewards relationships, writes proposals, manages the lead pipeline.

| Artifact | Location |
|---|---|
| Contacts (clients/leads/collaborators) | `stack-data/data/contacts.json` |
| Pricing guardrails | `operations/pricing-guardrails.md` |
| Client communication standards | `operations/client-communication.md` |
| Client briefs | `stack-data/briefs/` |

**Governing skills:** `outreach-email-manager`, `proposal-scope-builder`, `network-stewardship`

---

### People & Practice (solo HR)

**What it does:** Capacity management, inner work, sustainability, self-direction. The "staff development" of a one-person practice.

| Artifact | Location |
|---|---|
| Capacity management | `operations/capacity-management.md` |
| Facilitation principles | `learning/facilitation-principles.md` |
| Activities log (training, recovery) | `stack-data/data/activities.json`, ultraflow PWA |

**Governing skills:** `psychotherapeutic-coach`, `quantum-mirror-inner-practice`, `cognitive-debugger`

---

### Finance

**What it does:** Tracks revenue, expenses, contracts, ROI. Owns pricing decisions jointly with BD.

| Artifact | Location |
|---|---|
| Financial operations | `operations/financial-operations.md` |
| Pricing guardrails | `operations/pricing-guardrails.md` |
| Transactions ledger | `stack-data/data/financials.json` |
| KPI dashboard | `royal-metrics` PWA |

**Governing skill:** `invoice-financial-tracker`

---

### IT

**What it does:** Runs the digital infrastructure the other departments depend on.

| Artifact | Location |
|---|---|
| Charter & service catalog | `it/index.md` |
| Systems & runbooks | `it/systems.md` |
| Security & continuity | `it/security.md` |
| Roadmap (budget tiers) | `it/roadmap.md` |
| Incident log | `it/incidents.md` |

**Governing agents:** `weekly-ig-scrape.yml`, pre-commit hooks, `stack-data` CLI

---

## Cross-department flows

The architecture is only useful if the seams work. Key flows:

### Lead → engagement → revenue

```
BD (outreach) → Delivery (Mirror/Map/Territory) → Finance (invoice)
                      ↓
                Marketing (case study)
                      ↓
                R&D (methodology refinement)
```

### Scrape → report → action

```
IT (weekly IG scrape) → stack-data/content.json
                              ↓
                    Marketing (weekly report agent)
                              ↓
                    BD (re-engage quiet clients)
```

### Experiment → learning → canon

```
R&D (experiment) → Delivery (pilot with a client)
                          ↓
                    R&D (kill / canonize)
                          ↓
                    Executive (update methodology)
                          ↓
                    Marketing (publish)
```

---

## Department-ownership matrix

Who owns what decision, across departments. Everything has one primary owner.

| Decision | Primary | Consulted |
|---|---|---|
| New service tier pricing | Finance | Executive, BD |
| Which clients to pursue | BD | Executive, People (capacity) |
| Whether to take on a project | Delivery | People (capacity), Finance |
| What to publish publicly | Marketing | Executive, R&D |
| New tool or platform adoption | IT | Executive, Finance |
| Methodology changes | R&D | Executive, Delivery |
| Incident response | IT | — |
| Year-end review | Executive | all |

---

## Where this architecture is incomplete

Honest about gaps. If you're reading this and a department is thin, it's because the practice hasn't needed it yet.

- **Legal / Compliance:** Currently implicit. Contracts live in individual project files. At Territory-tier retainer volume, may warrant a `legal/` section (standard contracts, release forms, IP policy).
- **Customer Success:** Not a separate department. Post-delivery follow-up is folded into BD's network stewardship.
- **Learning & Development:** Exists in intranet (`learning/`) as methodology deep-dives, but not mapped as a department because it's closer to R&D + People in practice.
