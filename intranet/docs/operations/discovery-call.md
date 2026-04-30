# Discovery Call Structure

A standard shape for first real conversations with a qualified contact. Not a script — a container. The goal is to leave with a clear decision: fit, not fit, or needs another conversation.

This operationalizes the `mom-test` skill. Read that skill first if you haven't; this doc is the checklist form.

---

## Before the call

- [ ] Read the contact entry in `stack-data/data/contacts.json`
- [ ] Generate a brief via client-brief agent (if not already present in `stack-data/briefs/`)
- [ ] Identify the single most important thing you don't know about them yet
- [ ] Decide what would disqualify the fit — write it down before the call

---

## The call (45 min default)

### 1. Orient (5 min)
Not small talk. Ground the conversation.
- "What made you want to talk today?"
- Listen. Don't fill silence.

### 2. Past specifics (15 min)
Mom Test rules: ask about their life, not the idea. Past behavior, not future intent.
- "Walk me through the last time [the thing they're struggling with] happened."
- "What did you try? What happened?"
- "Who else was involved?"

Flags to listen for:
- They describe a specific moment in detail → real problem
- They describe it abstractly / in principles → may not be real, or not yet painful enough
- They mention what they *should* do → they're performing, not sharing

### 3. Current shape (10 min)
- "What's the current workaround?"
- "What's it costing you that it stays this way?"
- "What would have to be true for you to stop working on this?"

### 4. Constraints (8 min)
Don't skip this. Constraints name themselves here or they surprise you later.
- Timeline / deadline pressure
- Budget range (ask — don't infer)
- Who else decides
- What's already been tried with someone else

### 5. Close (7 min)
- Reflect back what you heard — specifically. "Here's what I'm hearing. Tell me where I'm wrong."
- Name the fit honestly: "This sounds like a Founder Story / Program Engagement / Organizational Embedding" OR "I don't think this is for us — here's who might be better."
- If fit: describe next step concretely (proposal by X, call #2 on Y).
- If not fit: say so on the call. Don't ghost-then-decline later.

---

## After the call

- [ ] Update contact `status` in contacts.json
- [ ] Log call outcome in contact notes (1–3 sentences — what they need, the fit call, next step)
- [ ] If fit: `proposal-scope-builder` within 48 hours
- [ ] If not fit: send the short "this isn't for us right now" email within 24 hours
- [ ] If ambiguous: schedule a specific follow-up, don't leave it open

---

## Disqualifiers (name them fast)

- They want deliverables but can't name the change they're trying to produce
- Budget is more than 30% below the tier they're asking for, with no flexibility
- Decision-maker isn't on the call and can't be in the next one
- They're comparison-shopping three vendors in the same tier — RP's offer is not a commodity

Qualifying out is a service to both parties. See `business-development/index.md` charter item 3.

---

## What this is NOT

- A pitch. You are not selling on this call.
- A briefing. They're not hiring you to receive information.
- A performance of expertise. If you're demonstrating, you're not listening.

*"The call is diagnosis. The proposal is articulation."*
