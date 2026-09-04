---
name: outreach-email-manager
description: Draft outreach emails (cold outreach, follow-ups, proposals) for Rubinstein Productions Say Why facilitation and film consultancy, create them as Gmail drafts, and log all outreach activity to the stack-data drafts log plus the vault 07 Outreach staging note. Use this skill when Isaac needs to reach out to potential clients, follow up after conversations, or submit proposals, and wants the contact tracked in the outreach pipeline. Trigger for "draft an outreach email", "follow up with [name]", "reach out to [org]", "write an email to", or "log this contact".
---

> **Canonical Say Why positioning source:** `~/vault/Second Brain/00 Canonical/Say Why - Canonical Positioning & Skill Embedding.md`
> **Pull contract:** Opener patterns MUST lead with the Diagnostic layer (Sift language: "stuck between knowing and saying," "information metabolism," "the gap between inside and outside versions"), NEVER with the Methodological layer. Prospects don't care about the method until they recognize the diagnosis. Use the 60-word pitch variant as the core body. Close with one Tribal-layer line when fit is warm.

# Outreach Email Manager

Comprehensive workflow for creating, drafting, and tracking outreach emails for Rubinstein Productions — a facilitation and film practice helping mission-driven professionals express what's true about their work.

## Overview

This skill handles the complete outreach email workflow:

1. Gather context (organization, contact, angle, stage)
2. Draft the email content, RP framing, not generic consultant pitch
3. Create a Gmail draft for review
4. Log the outreach to the stack-data drafts log and the vault 07 Outreach staging note

---

## When to Use This Skill

- Cold outreach to potential clients
- Follow-up emails after meetings or calls
- Proposal submission emails
- Partnership inquiry emails
- Any outreach that needs to be tracked in the CRM

---

## Workflow

### Step 1: Understand the Outreach Context

Gather if not already provided:

**For all email types:**
- Organization name + website
- Decision maker's name and title
- Email address
- Current outreach status (Research / Ready / Contacted / Responded)

**For cold outreach:**
- What's the specific angle? (What in their work reveals a narrative gap?)
- Any connection point? (Mutual contact, event, article they published?)
- Desired call-to-action (discovery call, coffee, specific ask)

**For follow-ups:**
- When was the previous contact?
- What was discussed? Anything promised?
- What's the next logical step?

---

### Step 2: Draft the Email

**RP outreach principles:**
- Lead with *their* work, not ours
- Name something specific, not "I love what you do" but "I noticed that your annual report leads with data and your website leads with story, there's a gap worth exploring"
- Brief: 3-4 short paragraphs for cold outreach
- Single CTA, one clear ask, not a menu of options
- Never pitch photography or videography as the offer. The offer is narrative translation.

**Value-first drafting checks (run before the draft is final):**
- *Free observation:* the email gives a genuine, specific observation about their work before it asks for anything. If it asks before it gives, rewrite.
- *Public asset over offered asset:* point them at something already public and useful (a case study, the GDC film, a framework) rather than offering to make them something. Give a door, not a sales hook.
- *Distribution close over call close:* default the close to sending or sharing something of value, not to booking a call. A call ask is the exception, used only when fit is already warm.

**Email framing by type:**

*Cold outreach:* Problem-first. Name the gap between what they do and what they say publicly in their public materials before offering anything.

*Follow-up:* Connection-forward. Reference what was real in the previous conversation. Not "following up" — "I've been thinking about what you said about [X]..."

*Proposal submission:* Context + link/attachment + clear next step. Don't re-pitch in the email — the proposal does the work.

---

### Step 3: Create Gmail Draft

**If Gmail tools are available:** Use them to create the draft directly.

**If manual:** Provide formatted output ready to copy-paste:

```
TO: [email]
SUBJECT: [subject line]

[Email body]

— Isaac Rubinstein
Rubinstein Productions | Facilitation & Film
[contact info]
```

---

### Step 4: Log the Outreach

Log the contact after drafting in two places. JSON state lives in stack-data; the human-readable staging note lives in the vault.

1. **stack-data drafts log.** Append the draft to a dated drafts file at `/Users/isaacrubinstein/stack-data/outreach/YYYY-MM-DD-drafts.md` (today's date). One block per draft, capturing organization, contact, title, email, status, subject, body, and the personalization rationale.

2. **Vault 07 Outreach staging note.** Mirror the entry into the vault "07 Outreach" staging note so the pipeline has a human-readable surface. Keep it to a one-line-per-contact log: date, org, contact, status, next-step timing.

Do not use Airtable. The drafts log plus the staging note are the pipeline of record.

**Status progression:**
- **Research** → gathering info, not yet ready
- **Ready** → draft exists, not sent
- **Contacted** → sent, awaiting response
- **Responded** → they replied

---

### Step 5: Next Steps

1. Confirm draft created
2. Confirm logged to the stack-data drafts log and the vault 07 Outreach staging note
3. Recommend follow-up timing (Day 3 / 7 / 14 cadence)
4. Flag if this should route to `rubinstein-productions-coo` for scoping and pricing next

---

## Generator Outreach Principle

Outreach should feel like *response* even when it's initiation. The best outreach happens when Isaac has genuinely noticed something about the org — not when working a list. If the email doesn't feel genuine, it probably isn't. Flag this to Isaac before sending.

**Sacral check before sending:** "Does reaching out to this person feel like a yes or a 'should'?"

---

## Related Skills

- `isaac-twin` — lifecycle master (orchestrator)
- `rubinstein-productions-coo` — fit/capacity checks before outreach, and scoping/pricing of the resulting proposal
