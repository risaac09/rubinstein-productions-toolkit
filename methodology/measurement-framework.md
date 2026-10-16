> **Canonical source:** `00 Canonical/RP Measurement Framework.md` in vault, last synced 2026-10-16. This file replaces the original 780-line ENIF with the April 2026 consolidated operational version (aspirational AI/agent-based material cut; revisit at 25+ client engagements). Edit vault file; this is a snapshot. Renamed from `enif.md` in July 2026 to match the framework's current name. For qualitative coding, the [Nomadic Indicators Codebook](nomadic-indicators-codebook.md) is canonical; Section 3 here is a pointer to it, and the vault canonical carries the same cut as of 2026-09-07. **Status:** operational.

# Rubinstein Productions - Measurement Framework
*The Bilingual Dashboard: Tracking what counts and what emerges*
*Consolidated April 2026 | Read alongside: Say Why - Canonical Positioning & Skill Embedding*

---

## Purpose

This framework tracks client transformation in two registers simultaneously. Most agencies force a choice between art (unmeasurable) and ROI (soulless). RP speaks both languages.

**Register 1, Institutional Metrics:** The language the CFO and board already trust. Quantifiable, reportable, satisfies "how will we know this worked?"

**Register 2, Relational Indicators:** What emerged that wasn't predicted. The quality of becoming. What matters but cannot be standardized.

These two registers are internal analytical categories. They are not client-facing language. When presenting to clients, use the Bilingual Dashboard format (Section 5) which integrates both registers into a single report.

---

## 1. Register 1: Institutional Metrics

### Financial Performance
- **Customer Acquisition Cost (CAC) Impact:** Referral rates and warm introductions post-engagement
- **Customer Lifetime Value (LTV):** Repeat engagement and contract expansion
- **Project Velocity:** Time from inquiry to contract signing (measures trust-building efficiency)
- **Asset Utilization Rate:** How often clients use deliverables (internal meetings, fundraising, marketing)

### Brand & Communication
- **Narrative Clarity Score:** Pre/post assessment of stakeholder understanding of mission (Likert 1-5)
- **Message Consistency Index:** Alignment between internal culture and external communications
- **Stakeholder Engagement:** Website time-on-page, video completion rates, social engagement on shared content
- **Template Adoption Rate:** Usage of narrative frameworks/talking points by client teams

### Developmental ROI
- **Decision-Making Latency:** Time reduction in reaching strategic consensus (measured conversationally)
- **Psychological Safety Index:** Team willingness to engage in vulnerable documentation
- **Authentic Expression Score:** Gap between "official narrative" and lived experience (narrowing = success)

### Client Satisfaction Scores
Collected at five assessment points: Pre-Engagement, Mid-Point, Delivery, 2-Week Follow-up, 3-Month Follow-up.

**Likert 1-5 questions:**
- "We can articulate our mission with ease and specificity" (Narrative Clarity)
- "Our lived culture matches our public narrative" (Internal Alignment)
- "The facilitation process honored my actual experience" (Feeling Heard)
- "I felt secure being authentically seen" (Safety in Vulnerability)
- "I understand my work differently through this process" (Clarity Gained)
- "This was collaborative, not extractive" (Co-Creation Quality)
- "We're proud to share this publicly" (Representative Pride)

Scale anchors (decided 2026-10-16): 1 is "strongly disagree" and 5 is "strongly agree". The middle points carry no label.

**Net Promoter Score (0-10):** "How likely are you to recommend this process to similar organizations?"

The items as a runnable instrument, with a response file, scoring procedure and worked example, are in the [Measurement Instrument](measurement-instrument.md).

---

## 2. Register 2: Relational Indicators

### Consistency Mapping (The Primary Relational Metric)

**Definition:** Not coherence (logical alignment) but *consistency*, how heterogeneous elements hang together despite contradictions. High consistency means the narrative holds complexity without flattening it.

**The Three Consistency Questions** (rated 1-10 by diverse stakeholders):
1. "This narrative holds my contradictory experience."
2. "I can locate myself in this story without erasing complexity."
3. "This makes our conflicts generative rather than destructive."

**Scoring interpretation:**
- 9-10: Exceptional, narrative functions as living container
- 7-8: Strong, holds most complexity effectively
- 5-6: Moderate, works for some stakeholders, not others
- 3-4: Low, significant gaps or fractures
- 1-2: Incoherent, narrative not functioning as unifier

**Cross-stakeholder variance:** High variance between groups (leadership vs. staff vs. beneficiaries) indicates consistency failure at intersections.

### Conversational Texture Analysis

Code recorded conversations for:
- **Pause patterns:** Contemplative (processing, integration) vs. avoidant (deflection, shutdown)
- **Self-correction frequency:** Client catches themselves mid-thought and revises toward greater truth. High frequency = active real-time integration.
- **Metaphor emergence:** Spontaneous metaphors reveal unconscious pattern recognition. Track which metaphors recur and evolve.
- **Pronoun shifts:** Movement between "I/me" and "we/us" or "they/them" indicates relational or ownership changes.

### Process Indicators

- **Deterritorialization events:** Moments where rigid organizational thinking breaks open (coded from recordings). Markers: "I never thought about it that way," questioning long-held assumptions, visible disorientation followed by relief, laughter of recognition.
- **Lines of Flight:** Insights that emerged outside the original scope, something genuinely new that opens unexpected territory. These are the gold. They indicate the work is catalyzing emergence, not just clarifying what was already known.
- **Reterritorialization quality:** Did new structures form that are more alive and resonant (generative) or that recreate old patterns (rigid)?

### Fear Inventory

Track the journey through four layers:
1. **Surface fear:** The presenting concern ("Funders won't take us seriously")
2. **Underlying fear:** What's beneath it ("I'll be seen as unprofessional")
3. **Root fear:** Usually about identity, worth, or belonging ("If people see the real me, they'll realize I don't know what I'm doing")
4. **Transformation:** The relationship to fear shifts, not that fear disappears, but it's held differently ("I still feel the fear, but now I can name it and speak anyway")

### Somatic Markers

Observable body-based signals tracked during sessions:

| Marker | Opening Signs | Closing Signs |
|---|---|---|
| **Breathing** | Deeper, slower | Shallow, rapid |
| **Shoulders** | Dropping, relaxing | Rising, tensing |
| **Eye contact** | Increasing | Decreasing |
| **Voice** | Pitch dropping, steadying | Tightening, speeding |
| **Posture** | Leaning forward, opening | Leaning back, crossing arms |
| **Breakthrough** | Tears of recognition, deep exhale, laughter, "I can feel that in my body" | (no negative marker) |

---

## 3. Qualitative Coding Guide

The canonical coding reference is Nomadic Indicators Codebook: per-code definitions, application
criteria, and worked examples across six domains (Consistency, Texture, Process, Fear, Somatic,
Relational). Code from the codebook rather than from memory of this section. Earlier versions of this
framework carried a condensed copy of the codebook, and the two drifted; the copy was cut in July 2026
from the toolkit snapshot and on 2026-09-07 from this canonical.

The workflow in brief: read or listen once without coding, then make a generous first pass with
timestamps, then look for clustering and journey arcs (fear evolution especially), then write a short
synthesis narrative. Findings land in the case study template and the client record.

---

## 4. Implementation Architecture

*Rewritten 2026-10-16. The earlier version specified a hosted database that was never built. This one describes what exists and marks what does not.*

### Principle: records are plain files

Engagement records, session records and survey responses are plain text files in a version-controlled folder: JSON for records, CSV for responses, Markdown for synthesis. Nothing requires a hosted database. Backing up is committing. The safeguards in Section 8 become mechanical: a client's raw data is a set of files that can be handed over, corrected or deleted, and the history shows what changed.

### What is captured

Three levels, from the engagement down to the person answering.

**Engagement** (one per client project). In the author's practice this is an entry in a JSON file of projects, checked against a JSON Schema by a validation script. The names below are written in snake_case; the author's file spells them in camelCase, and calls `service_type` simply `service`. Fields:

| Field | Type | Notes |
|---|---|---|
| `engagement_id` | text | Stable, never reused. |
| `service_type` | enum | `founder-story`, `program-engagement` or `org-embedding`. |
| `status` | enum | `pipeline`, `active`, `delivered`, `complete` or `cancelled`. |
| `start_date`, `delivery_date` | date or null | ISO 8601. |
| `hours_logged` | number | Feeds the effective-rate figure in Section 9. |
| `referral_source`, `industry` | text | Specified here; not yet fields in the author's file. |
| `consent_tier` | 1, 2 or 3 | The tier chosen in Section 8. Specified here; not yet a field in the author's file. |
| `deliverables` | list | Each has an id, a type (`session`, `narrative-synthesis`, `video`, `dashboard`, `review` and so on), a status and a delivery date. |

Money fields (contract value, effective hourly rate, monthly revenue, conversion rate) are not part of the engagement record. They live in the books and link to the engagement by `engagement_id`. Register 1's financial metrics are computed from there.

**Session** (one per recorded conversation). A deliverable of type `session` on the engagement, plus a transcript file.

| Field | Type | Notes |
|---|---|---|
| `session_id` | text | |
| `engagement_id` | text | |
| `date`, `duration_minutes` | date, integer | |
| `recording_ref`, `transcript_ref` | text or null | Pointers to files kept outside the records. Raw recordings are not stored in the repository. |
| `codes` | list of text | Code names exactly as the Nomadic Indicators Codebook spells them, with a timestamp for each passage. |

**Response** (one per respondent per assessment point). A row in a CSV file with a fixed header. The columns, allowed values and scoring procedure are in the [Measurement Instrument](measurement-instrument.md), which also ships a worked example and a scoring script. In short: `engagement_id`, `respondent_id` (a code, never a name), `stakeholder_group`, `assessment_point`, `response_date`, seven Likert columns (1 to 5), `nps` (0 to 10) and three consistency columns (1 to 10). Free text such as testimonials goes in a separate file keyed the same way, so the CSV never needs quoting.

**Outcomes and assets.** Two further record shapes support Register 1.

| Record | Fields |
|---|---|
| Outcome | `outcome_id`, `engagement_id`, `type` (funding secured, partnership formed, media coverage, team alignment and so on), `value` (number or null), `date`, `verified` (yes/no), `usable_in_marketing` (yes/no; only under consent tier 3). |
| Asset use | `asset_id`, `engagement_id`, `asset_type`, `delivery_date`, `usage_count`, `usage_contexts` (list), `in_use_at_3mo` (yes/no or null). |

### What exists and what does not

*As of 2026-10-16.* Built: the engagement and deliverable records, validated in the author's private data repository (every engagement field above except `referral_source`, `industry` and `consent_tier`), and a separate financial record linked to them by id. Specified and shipped as a public format: the response CSV and its scorer. Specified and not built: those three engagement fields, session records with coded passages, outcome records and asset-use records.

### Collection tools

Any transcription service for recorded conversations. Any form tool that exports CSV for the Likert, NPS and consistency items, as long as the exported columns can be renamed to match the response header. A folder of Markdown notes, with links between them, for qualitative synthesis and pattern recognition.

### Unbuilt alternative: a hosted relational base

The first design of this section used Airtable, with one table each for projects, financial metrics, outcomes, satisfaction scores and asset utilization. It was never built. It remains a reasonable choice for a practice that wants a form-and-grid interface or has several people entering data. If adopted, keep the field names above, treat the CSV export as the record of truth for scoring, and keep the Section 8 promises: a client's rows must be exportable and deletable on request.

### Reporting Cadence

**Weekly (5 min):** Update project statuses, log client outcomes, note hours worked

**Monthly (30 min):** Complete financial metrics, review pipeline/projections, calculate conversion rate, update dashboards

**Quarterly (2 hours):** Full financial analysis, NPS trend review, asset utilization audit, goal progress, pricing review

**Annually (half day):** Year-in-review, case study compilation, pricing review, next-year strategy

---

## 5. The Bilingual Dashboard (Client Deliverable)

The Bilingual Dashboard is the measurement deliverable for Tier 2+ engagements. It presents both registers in a single report.

| Category                 | Institutional Metric                   | Relational Indicator                              | Method               |
| ------------------------ | -------------------------------------- | ------------------------------------------------- | -------------------- |
| **Financial / Flow**     | CAC reduction, LTV, contract expansion | Resource flow, is energy moving freely?          | CRM / Airtable       |
| **Audience / Resonance** | Watch time, click-through rate         | "I felt seen." Qualitative resonance data.        | Analytics / feedback |
| **Internal / Culture**   | Asset utilization, hiring velocity     | Consistency, does the story hold contradictions? | Pre/post surveys     |
| **Strategic / Growth**   | Decision latency, template adoption    | Assemblage density, are silos connecting?        | Observational audit  |

### Framing for Different Audiences

**To institutionally-minded stakeholders (funders, board):**
"We use a comprehensive impact measurement system that tracks both quantitative metrics, usage rates, stakeholder engagement, decision-making efficiency, and qualitative indicators of organizational alignment. You'll receive a dashboard showing concrete ROI alongside narrative transformation markers."

**To mission-driven leaders (visionary EDs, founders):**
"This process includes structured reflection moments that help you witness your own emergence. The measurement isn't surveillance, it's a mirror that reveals patterns you're already living but haven't named yet."

---

## 6. Post-Engagement Impact Assessment

### Two-Week Integration Window
No contact period. Allow natural metabolization. Client team discusses internally: how the deliverable is being used, reception, unexpected insights, what's still settling.

### First Impact Conversation (2 Weeks Post-Delivery)
**Duration:** 20 minutes, conversational, recorded

**Inquiry domains:** Utilization, resonance, integration, emergence

**Likert assessment (1-5):** Narrative Accuracy, Functional Utility, Representative Pride, Future Willingness, Stakeholder Clarity

### Second Impact Conversation (3 Months Post-Delivery)
**Duration:** 30 minutes, conversational + scoring

**Inquiry domains:** Systemic ripples (second-order effects), organizational learning, relational shifts, long-term integration (what's still living vs. already stale)

**Institutional metrics collection:** Usage statistics, concrete outcomes, organizational efficiency, template/framework adoption

**NPS collection**

---

## 7. Monthly Synthesis Ritual (Practitioner Practice)

Last Friday of each month. 2-3 hours. Uninterrupted, contemplative space.

**Stage 1: Data Immersion (30 min)**, Review all transcripts, listen to shared voice memos, scan quantitative scores, read facilitation notes.

**Stage 2: Pattern Recognition (45 min)**, Thematic coding across all clients. Questions: What patterns cut across clients? Where are my assumptions being challenged? What's trying to emerge in the methodology? Which interventions catalyzed unexpected outcomes?

**Stage 3: Consistency Analysis (30 min)**, Average consistency scores, cross-stakeholder variance, correlation with institutional metrics, case study candidates.

**Stage 4: Generator Integration (30 min)**, Which projects energized vs. depleted? Where did I follow sacral response vs. mental override? How well did I honor latency periods? What shadows emerged?

**Output:** Synthesis memo capturing pattern discoveries, methodology refinements, ideal client signals, case study candidates.

---

## 8. Anti-Extractive Safeguards

### Consent Layering
- **Tier 1 (all clients):** Internal learning only
- **Tier 2 (opt-in):** Anonymized aggregate data for methodology refinement
- **Tier 3 (explicit permission + compensation):** Identified case studies for marketing

### Data Sovereignty
- Clients can request their raw data at any time
- Right to modify or delete reflections
- Final approval on any public sharing
- Ability to withdraw from case studies retroactively

### Reciprocal Benefit
- Clients receive monthly pattern memos (anonymized insights from broader practice)
- Access to resources developed from aggregate learning
- Discounted rates on future engagements if they contribute to case studies

### Transparent Purpose
- Clear framing in contracts: "This measurement builds my methodology, not just assessing your performance"
- Share findings proactively: "Here's what I'm learning from our work together"
- Invite client feedback on the framework itself

---

## 9. Target Benchmarks

*Starting points, adjust based on experience.*

| Metric | Year 1 Target | Mature Target |
|---|---|---|
| Average NPS | 8.0 | 9.0+ |
| Repeat/Referral Rate | 30% | 60%+ |
| Conversion Rate | 40% | 60%+ |
| Effective Hourly Rate | see `rp-intranet` pricing guardrails | |
| Asset Utilization (3 months) | 70% | 85%+ |
| Average Consistency Score | 7.0 | 8.0+ |
| Mid-Process Safety Scores | >4.0/5 | >4.5/5 |

---

### Documents This File Replaces

This document supersedes and consolidates:
- `Emergent Narrative Impact Framework.md` (780-line full framework, aspirational future-state material on AI analysis, agent-based modeling, and certification programs has been cut; revisit when client base reaches 25+ engagements)
- `Nomadic Indicators Codebook.md` (qualitative coding guide, absorbed into Section 3)
- `Royal Metrics Dashboard.md` (Airtable structure; Section 4 was rewritten on 2026-10-16 around plain-file records, and the Airtable design is kept there only as an unbuilt alternative)
