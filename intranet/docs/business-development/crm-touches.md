# Contact Touches — Lightweight CRM in contacts.json

RP does not run a third-party CRM. Touch history and next-action reminders live directly in `contacts.json` as an append-only `touches[]` array on each contact entry.

This is the minimum-viable structure that prevents the two failure modes:
1. *"When did I last talk to them?"* — can't answer → relationship drifts.
2. *"I meant to follow up."* — no reminder surface → follow-ups drop.

---

## Schema

See `stack-data/schemas/contacts.schema.json`. Each contact has:

```json
"touches": [
  {
    "date": "2026-04-04",
    "type": "email-sent",
    "note": "Sent the proposal for the Map engagement. Flagged the March availability window.",
    "nextAction": "Follow up if no response by 2026-04-11",
    "nextActionDue": "2026-04-11"
  }
]
```

### Fields
- **date** (required) — ISO date of the touch
- **type** (required) — enum: `email-sent | email-received | call | meeting | session | referral-given | referral-received | stewardship-touch | proposal-sent | other`
- **note** — 1–3 sentences, plain text. What happened, what was said.
- **nextAction** — what's owed next, in plain language. Null if nothing is owed.
- **nextActionDue** — ISO date. Null if no deadline.

### Rules
- **Append-only.** Never edit or delete past touches. If something was wrong, add a correction touch.
- **One touch per real interaction.** Not one per email in a thread — one per meaningful exchange.
- **Log within 24 hours** of the touch. Memory fades fast.

---

## Usage

### Adding a touch
Until a `sd-touch` CLI exists, add directly to `contacts.json` and run `bash scripts/validate.sh`.

### Querying
| Question | Query shape |
|---|---|
| Who am I overdue to follow up with? | `jq` over all contacts, filter `touches[]` where `nextActionDue < today` |
| When did I last touch contact X? | Read last entry of contact's touches array by date |
| Who's gone quiet for 90+ days? | Max touch date per contact, filter where >90 days old |
| All stewardship touches this quarter | Filter touches by type=stewardship-touch and date range |

Build these as sd-touches CLI queries when the volume justifies the tooling. Current volume: do it by hand.

---

## Integration with other systems

| System | Relationship |
|---|---|
| `outreach-email-manager` skill | Should append a touch entry after each send (email-sent type) |
| `network-stewardship` skill | Should append stewardship-touch entries; queries "quiet contacts" |
| `referral-rhythm.md` quarterly cycle | All outreaches from that cycle logged as stewardship-touch or referral-given/received |
| Discovery calls (`operations/discovery-call.md`) | After-call step should log a call-type touch with disposition in nextAction |

---

## What this is NOT

- A full CRM. No pipelines, no deal stages, no scoring. (Pipeline stage is separate on the contact — see `pipelineStage` field.)
- A communication log. Not every email. Not every text. Meaningful exchanges only.
- A task manager. nextAction is a reminder, not a task list. If it becomes a task list, move it elsewhere.

---

## Growth path

If touch volume exceeds what's manageable in contacts.json (likely trigger: Organizational Embedding capacity across 3+ retainers, or active eval-side RFP pipeline), options:

1. **Extract to separate `touches.json` entity** with contactId foreign key
2. **Move to external tool** (Airtable, Notion) with stack-data remaining source of truth via export
3. **Status quo + better CLI** — sd-touches CLI does the heavy querying, touches stay in contacts.json

Not a decision for today. This shape holds for current practice volume.

---

*"The relationship is the product. The log is the memory."*
