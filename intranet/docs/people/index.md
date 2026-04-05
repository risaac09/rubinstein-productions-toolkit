# People & Practice

In a traditional company, HR manages staff. In a solo practice, there is no staff — only **Isaac as the instrument.** The condition of the instrument determines the quality of every engagement, every piece of content, every decision.

This department exists because the instrument requires tending that the other departments will not do. Without explicit ownership, capacity becomes someone else's problem — which in a solo practice means it becomes no one's problem.

---

## Charter

1. **Protect the instrument.** Isaac's nervous system, body, and attention are the practice's most fragile and most essential asset.
2. **Capacity is the binding constraint.** Not budget, not time. The real limit is metabolic and somatic. Every commitment must be capacity-checked.
3. **Rest is work.** Recovery is not absence of practice; it is a phase of it. The work is done in cycles, not hours.
4. **Inner practice before output.** Facilitation of others is downstream of Isaac's own regulation. Skip the inner work and the outer work degrades.

---

## Artifacts

| Artifact | Location |
|---|---|
| Capacity management rules | `intranet/docs/operations/capacity-management.md` |
| Facilitation principles | `intranet/docs/learning/facilitation-principles.md` |
| Activities log (runs, training, recovery) | `stack-data/data/activities.json` |
| Ultrarunning companion PWA | `ultraflow-companion` |
| Training ↔ content correlator | `stack-data/agents/training-content-correlator.sh` |

---

## Governing skills

| Skill | Role |
|---|---|
| `psychotherapeutic-coach` | Self-coaching, emotional processing, decision support |
| `quantum-mirror-inner-practice` | Interior practice, meditation, state-work |
| `cognitive-debugger` | Untangling thinking when stuck — distinguish signal from rumination |

---

## Hard capacity limits

Copied from `operations/capacity-management.md` for prominence:

| Constraint | Limit | Rationale |
|---|---|---|
| Active Territory partnerships | Max 2 | More than 2 degrades relational depth |
| Map engagements per quarter | Max 4 | Each sprint: 3–5 days intensive + prep + follow-up |
| Mirror sessions | Unlimited (but protected) | Must not displace deep work |
| Deep work mornings | Min 3/week | No calls before noon |
| Filming sessions | 24hr prep + 5-day post-latency | Filming consumes more energy than it appears to |

Violations of these limits are **not** emergencies to manage. They are signals to decline or reschedule.

---

## Embodied practice as infrastructure

Running, strength, mobility, breath work are not hobbies separate from the practice. They are:

- **Nervous-system calibration** — teaches co-regulation from the inside out
- **Pattern recognition for facilitation** — the runner's ability to read effort maps to reading a room
- **Sustainability** — the body is the throughput constraint
- **Credibility** — the ultrarunning lineage is part of the originality factor (see `innovation/originality-factor.md`)

Activities are logged in `stack-data/data/activities.json` via `sd-add-activity`. The `training-content-correlator` agent looks for patterns between training state and content output.

---

## Operating principles

**Weekly rhythm has to exist.** Running, strength, mobility, meditation — some cadence. The specific plan flexes; the non-negotiable is that there is one.

**Track somatic signal, not just effort.** The `feel` field in activities (`heavy / neutral / light / flow`) is more diagnostic than duration or distance.

**Decline when heavy.** "Heavy" feel for more than 3 consecutive activities is a signal. Something is off. Do less, not more.

**Sabbatical days.** 1 day/week with no work obligation. Non-negotiable.

**Unplugged windows.** No phone/screens in the first hour after waking or the last hour before sleep. Governs attention quality.

**Inner practice log.** Any sustained psychotherapeutic-coach or quantum-mirror session gets a light note (private, not in this repo) — tracking interior continuity across months.

---

## Signals that capacity is compromised

Noticing these early prevents burnout spirals:

- 3+ "heavy" feel ratings in a week
- Skipping planned recovery (rest days, mobility)
- Resistance to opening the laptop in the morning
- Short-fuse in client communication
- Snackable-content consumption replacing study
- Resentment toward specific engagements
- Sleep quality degrading without obvious cause

When 2+ of these are present, the move is: **reduce commitment surface area.** Not push through.

---

## Metrics that matter

| Metric | Why | Where |
|---|---|---|
| Activities per week (by type) | Practice consistency | activities.json |
| `feel` distribution over rolling 4 weeks | Capacity trend | activities.json queries |
| Deep-work mornings honored (out of min 3) | Schedule integrity | Calendar review |
| Mirror sessions / month | Surge-load indicator | stack-data |
| Active Territory count | Hard-cap monitor | projects.json |

**Counter-metric:** hours worked. Hours are not a signal of quality in this practice.

---

## Relationships to other departments

| Dept | Flow |
|---|---|
| **Delivery** | Gates new engagements — People says yes/no on capacity before a signing. |
| **BD** | Sends: capacity status (can a new Territory be taken?). Receives: incoming commitment requests. |
| **Executive** | Sends: sustainability signals that shape strategy (if the practice is not sustainable as currently scoped, strategy must change). |
| **R&D** | Partners on what experiments are sustainable. Sends: friction signals ("this new format is exhausting"). |
| **Finance** | Partners on tier decisions — hiring help/delegation is a capacity decision. |

---

## Gaps / open questions

- **Sabbatical policy** — 1 day/week is rule; longer sabbaticals (quarterly week off, annual month) undefined.
- **Delegation threshold** — at what point does hiring a VA or sub-contractor become a capacity necessity? Named in roadmap but no trigger.
- **Illness / contingency** — what happens to client obligations when Isaac is sick? No formal policy.
- **Training periodization** — running has implicit race-cycle periodization; other embodied practice doesn't.
- **Private inner-practice log** — mentioned but no system. Consider: encrypted note, voice memo cadence, or Obsidian vault section (private).
