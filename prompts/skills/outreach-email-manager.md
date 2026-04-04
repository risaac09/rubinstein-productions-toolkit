---
name: outreach-email-manager
description: Draft outreach emails (cold outreach, follow-ups, proposals) for Rubinstein Productions Voice Liberation consultancy, create them as Gmail drafts, and log all outreach activity to Airtable. Use this skill when Isaac needs to reach out to potential clients, follow up after conversations, or submit proposals — and wants the contact tracked in the outreach pipeline. Trigger for "draft an outreach email", "follow up with [name]", "reach out to [org]", "write an email to", or "log this contact".
---

# Outreach Email Manager

Comprehensive workflow for creating, drafting, and tracking outreach emails for Rubinstein Productions — a Voice Liberation consultancy helping mission-driven organizations excavate and amplify their authentic narrative.

## Overview

This skill handles the complete outreach email workflow:

1. Gather context (organization, contact, angle, stage)
2. Draft the email content — Voice Liberation framing, not generic consultant pitch
3. Create a Gmail draft for review
4. Log the outreach to Airtable for tracking

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

**Voice Liberation email principles:**
- Lead with *their* work, not ours
- Name something specific — not "I love what you do" but "I noticed that your annual report leads with data and your website leads with story — there's a gap worth exploring"
- Brief: 3-4 short paragraphs for cold outreach
- Single CTA — one clear ask, not a menu of options
- Never pitch photography or videography as the offer. The offer is narrative translation.

**Email framing by type:**

*Cold outreach:* Problem-first. Name the Narrative Dysregulation pattern you see in their public materials before offering anything.

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
Rubinstein Productions | Voice Liberation
[contact info]
```

---

### Step 4: Log to Airtable

Log the contact after drafting:

```bash
python3 scripts/log_to_airtable.py <<EOF
{
  "organization_name": "Organization Name",
  "website": "https://organization.com",
  "decision_maker": "Contact Name",
  "title": "Their Title",
  "email": "contact@org.com",
  "outreach_status": "Ready",
  "mission_statement": "Optional",
  "email_subject": "Subject",
  "email_body": "Body",
  "notes": "Research notes + personalization rationale"
}
EOF
```

**Status progression:**
- **Research** → gathering info, not yet ready
- **Ready** → draft exists, not sent
- **Contacted** → sent, awaiting response
- **Responded** → they replied

---

### Step 5: Next Steps

1. Confirm draft created
2. Confirm Airtable logged
3. Recommend follow-up timing (Day 3 / 7 / 14 cadence)
4. Flag if this should route to `proposal-scope-builder` next

---

## Generator Outreach Principle

Outreach should feel like *response* even when it's initiation. The best outreach happens when Isaac has genuinely noticed something about the org — not when working a list. If the email doesn't feel genuine, it probably isn't. Flag this to Isaac before sending.

**Sacral check before sending:** "Does reaching out to this person feel like a yes or a 'should'?"

---

## Related Skills

- `rubinstein-productions-agent` — lifecycle master
- `proposal-scope-builder` — next stage after interest confirmed
- `rubinstein-productions-coo` — for fit/capacity checks before investing in outreach