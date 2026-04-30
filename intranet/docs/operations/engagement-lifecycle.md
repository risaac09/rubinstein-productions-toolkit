# Engagement Lifecycle SOPs

This document maps every phase of a client engagement from first contact through offboarding. Each phase has a standard operating procedure, deliverables, and handoff criteria.

<div class="visual-embed">
<iframe src="../assets/diagrams/client-journey-map.html" width="100%" height="600" frameborder="0" style="border: 1px solid #f2ece4; border-radius: 4px;"></iframe>
<p style="font-size: 12px; color: #a89e93; margin-top: 4px;">↗ <a href="../assets/diagrams/client-journey-map.html" target="_blank">Open full journey map</a></p>
</div>

---

## Stage 1: Recognition

**What happens:** A prospect finds RP — through the toolkit, content, referral, or direct outreach.

### SOP

1. Log the prospect in Obsidian using `rp-prospect` CLI tool
2. Record source (referral, organic, content, event)
3. Assess initial fit using the Client Acceptance Filter (see [Decision Frameworks](decision-frameworks.md))
4. If fit looks plausible, respond within 48 hours with a warm, brief message

### Deliverable to Client

- Welcome Packet (`01-welcome-packet.docx`) — send only if they express interest in learning more

### Handoff Criteria → Stage 2

Prospect responds positively. A conversation is scheduled.

---

## Stage 2: Conversation

**What happens:** An informal discovery conversation. No agenda, no pitch. The goal is mutual assessment of fit.

### SOP

1. 30–60 minute conversation (phone, Zoom, or in-person)
2. Listen for: what's the real problem, who's closest to the work, what would success look like, what are the unspoken constraints
3. After the call, update prospect status via `rp-update`
4. Within 24 hours, send a brief follow-up email acknowledging what you heard

### Deliverable to Client

- Nothing formal. Clarity on fit.
- If proceeding: send Founder Story Prep Guide (`02-founder-story-prep.docx`)

### Handoff Criteria → Stage 3

Both parties agree a Founder Story session would be valuable. Date scheduled.

---

## Stage 3: Founder Story Session

**What happens:** A 90-minute facilitated session — the entry-tier engagement. Isaac listens deeply and reflects back what he hears. Methodology-wise, this is Container + light Performance/Translation in one pass (per canonical). Not consulting — witnessing.

### SOP

**Pre-session:**

1. Send Founder Story Prep Guide 3–5 days before
2. Confirm logistics (location, Zoom link, who's attending)
3. Internal prep: review prospect notes, set intention, do nervous-system regulation (breathing, grounding)

**During session:**

1. Follow the facilitation protocol (see [Facilitation Principles](../learning/facilitation-principles.md))
2. Take minimal notes — stay present
3. Note: body language shifts, energy changes, what's said easily vs. what requires effort, where language feels borrowed vs. natural

**Post-session:**

1. Allow 5-day integration window before writing
2. Write the Founder Story narrative
3. Send Founder Story narrative (`03-founder-story-narrative.docx`) to client

### Deliverable to Client

- Founder Story narrative — a written synthesis of what was observed, the inside version of their story, and open questions
- For most Founder Story tier engagements, this completes the deliverable cycle (the engagement ends here)
- This is theirs to keep regardless of whether they continue

### Handoff Criteria → Stage 4

Client reads the narrative, confirms it resonates, expresses interest in a deeper engagement (Program Engagement or, eventually, Organizational Embedding).

!!! tip "5-Day Latency Rule"
    Never write the Founder Story narrative within 5 days of the session. Integration time produces better synthesis. This is a hard rule, not a guideline.

---

## Stage 4: Engagement Design

**What happens:** Co-design a Program Engagement (all four phases, embedded in the client's program) or Organizational Embedding (repeated cycles, 3+ months) based on what emerged from the Founder Story.

### SOP

1. Review Founder Story narrative with client — what landed, what didn't, what questions remain
2. Co-scope the engagement: what are the actual deliverables, timeline, and measurement approach
3. Define the Bilingual Dashboard metrics (institutional + relational; internal Royal/Nomadic analytical tool) for this specific engagement
4. Draft Engagement Proposal (`04-engagement-proposal.docx`)
5. Draft Project Roadmap (`05-project-roadmap.docx`)
6. Set pricing using the [Pricing Guardrails](pricing-guardrails.md) — Program Engagement $3–8K; Organizational Embedding $4–8K/mo (held)
7. Send both documents to client

### Deliverable to Client

- Engagement Proposal — scope, timeline, investment, terms
- Project Roadmap — phase-by-phase plan with milestones

### Handoff Criteria → Stage 5

Proposal signed. First payment received. Calendar blocked.

---

## Stage 5: Integration

**What happens:** The core work — facilitation sessions, interviews, filming, data collection.

### SOP

**Kickoff:**

1. Send Session Prep Guide (`07-session-prep-guide.docx`) before each session
2. If filming is involved, send Camera Kit Guide (`06-camera-kit-guide.docx`)
3. Set communication cadence (weekly/biweekly check-ins)

**During:**

1. Facilitate according to the methodology (Container → Excavation → Performance)
2. Track both Royal Metrics and Nomadic Indicators per the Bilingual Dashboard
3. Send Progress Check-In (`08-progress-checkin.docx`) at agreed intervals
4. Monitor scope — flag any drift early (see [Decision Frameworks](decision-frameworks.md))

**Equipment:**

1. Ship camera kit per [Equipment & Logistics](equipment-logistics.md)
2. Confirm receipt and answer technical questions
3. Collect footage upon return

### Deliverable to Client

- Session Prep Guides (per session)
- Camera Kit Guide (if applicable)
- Progress Check-Ins (at agreed intervals)

### Handoff Criteria → Stage 6

All sessions complete. All footage collected. All data gathered.

---

## Stage 6: Story Travels

**What happens:** Post-production — editing, review cycles, report writing, final delivery.

### SOP

1. Edit footage (DaVinci Resolve workflow — see production docs)
2. Write Bilingual Dashboard Report (`09-bilingual-dashboard-report.docx`)
3. Assemble all deliverables into the Final Delivery Package (`10-final-delivery.docx`)
4. Client review cycle: rough cut → feedback → final cut
5. Transfer all materials to client (footage, transcripts, reports, assets)

!!! warning "5-Day Latency"
    Apply the 5-day latency rule between receiving feedback and delivering revisions. Rushed edits produce worse work.

### Deliverable to Client

- Bilingual Dashboard Report — full outcomes in both Royal and Nomadic registers
- Final Delivery Package — complete inventory of everything being transferred
- All raw footage, transcripts, session notes, photo assets

### Handoff Criteria → Stage 7

All materials delivered. Final payment received.

---

## Stage 7: Return

**What happens:** Formal close of the engagement. The relationship continues; the contract ends.

### SOP

1. Write and send Thank You + Offboarding Letter (`11-thank-you-offboarding.docx`)
2. Include 2–3 specific sustainability suggestions grounded in what you observed
3. Ask about portfolio permission (can you reference this project publicly?)
4. Invite referrals — specific, not generic: "If you know someone else doing this kind of work..."
5. Update CRM: move to "completed" status
6. Write internal case study using `case-study-template.md`
7. Schedule 90-day check-in (calendar reminder)

### Deliverable to Client

- Thank You + Offboarding Letter
- An open door

### Post-Engagement

- 90-day follow-up: brief email checking in, asking how the materials are being used
- If case study permission granted: write and publish
- Add to quarterly review pipeline analysis
