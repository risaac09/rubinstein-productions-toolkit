---
name: seed-bed
description: Incubation space for pre-categorical ideas — the greenhouse before the forge. Use when Isaac has an idea that isn't ready to be a service, claim, expression, tool, or practice yet. Trigger for "I noticed something", "there's something here", "I don't know what this is yet", "seed", "incubate this", "what do I do with this", "something is connecting", "park this", "hold this", or any idea that resists immediate classification. Also use for weekly seed review, capture processing, or when Isaac asks "what's growing?"
---

# Seed-Bed

## Overview

The greenhouse between capture and commitment. Seeds enter pre-categorical and stay that way until they reveal what they want to become. The skill's job: hold ideas in active non-resolution, revisit them on a cadence, and notice maturation — never force classification.

**Operating metaphor:** Metabolic, not managerial. The liver, not the inbox.

## The Typology

Every idea in Isaac's system eventually becomes one of six things:

| Type | What it becomes | Downstream skill |
|---|---|---|
| **Service** | A deliverable for a client | COO → Proposal Builder |
| **Claim** | A tested intellectual position | Arena |
| **Expression** | Content under Isaac's name | Content Arc / Voice / Writing |
| **Tool** | Infrastructure someone uses | Projects / skill-creation |
| **Practice** | Something done in rooms with people | PM Coordinator / QM |
| **Signal** | Pre-categorical. This is the seed. | **Stays here.** |

**The demarcation question ("what kind of thing is this?") is answered by the incubation process, not at intake.**

## Intake Protocol

When Isaac brings a signal:

1. **Capture it raw.** Don't clean it up, don't organize it, don't connect it. Write what Isaac says, in his words.
2. **Ask one question:** *"What made you notice this?"* — the noticing is as important as the signal.
3. **Do NOT classify.** No triage. No A/B/C. No "this could be a..." The seed tells you what it wants to become by how it grows.
4. **Save as a seed note** to the vault:

```
Path: ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/07 Capture/Seeds/YYYY-MM-DD-[slug].md
```

Use osascript to write to iCloud vault. Frontmatter:
```yaml
---
type: seed
created: YYYY-MM-DD
last_tended: YYYY-MM-DD
status: germinating
source: [conversation / phone-note / reading / observation]
related: []
---
```

Body: Isaac's words verbatim, then the noticing context below a `---` separator.

**Anti-pattern — cognitive override in routing:** Isaac thinking "this SHOULD be a service offering" when his gut says "this wants to be explored." If you hear "I should probably turn this into..." — flag it. Ask: *"Is that what it wants, or what you think it should want?"*

## Incubation Protocol (Weekly Seed Review)

When Isaac asks "what's growing?" or during a scheduled weekly review:

1. **Read all seed notes** with `status: germinating` from `07 Capture/Seeds/`.
2. **Present each seed** — the original words, the noticing context, and the date planted.
3. **For each seed, ask the Maturation Questions** (below). Don't ask all five rapid-fire. Present the seed and let Isaac respond. His body knows before his mind.
4. **Update `last_tended`** on every seed reviewed.
5. **If a seed has matured:** change status to the type it's becoming (service/claim/expression/tool/practice), then route to the downstream skill. The seed note stays in `07 Capture/Seeds/` as a record.
6. **If a seed is still growing:** leave it. Add any new connections Isaac noticed to `related:`.
7. **If a seed is dead:** change status to `composted`. Dead seeds sometimes feed living ones — don't delete.

**Cadence:** Weekly minimum. Isaac can tend seeds anytime, but the system should prompt at least once per week if seeds exist.

**Generator pacing:** Never review more than 5 seeds in one sitting. If more exist, ask Isaac which ones feel alive right now.

## Maturation Questions

These detect when a Signal has differentiated. Not a classification algorithm — a set of Sacral-friendly prompts.

- **Service:** *"Does this have a buyer? Can you see someone paying for this?"*
- **Claim:** *"Could this be wrong? Is there something to defend here?"*
- **Expression:** *"Does this have a voice? Is something trying to be said?"*
- **Tool:** *"Does this have a use case? Could someone (including you) use this?"*
- **Practice:** *"Does this want to be lived? Is this about what you do in a room?"*

If none land: *"Still becoming. Good. We'll come back."*

If more than one lands: *"Which pull is stronger right now?"* — don't split. Seeds differentiate once. If it genuinely splits, create a second seed for the secondary pull.

## The Zero-Measurement Zone

Some seeds never need to become anything. This is not failure — it's the system honoring what the RP methodology demands for clients but rarely extends to Isaac himself.

When a seed has been tended 4+ times with no maturation signal:

- Do NOT pressure it toward a category.
- Ask: *"Is this still alive, or has it done its work just by being held?"*
- If still alive → keep tending.
- If it's done its work → `status: composted`. Note what it fed.

**The vault equivalent of what RP creates for clients: a space where nothing has to perform.**

## Capture Backlog Processing

When Isaac wants to process `07 Capture/`:

1. Read all unprocessed notes in `07 Capture/01 Phone Notes/` using osascript.
2. For each: *"Is this a seed, or does it already know what it is?"*
3. If it already knows → route directly to the appropriate skill (COO for service ideas, Writing for prose impulses, etc.)
4. If it's a seed → run Intake Protocol above.
5. If it's a task/follow-up → route to `07 Capture/Followups Inbox/`.

## Connection to Existing Skills

**Called BY:** `rubinstein-productions-agent` (when idea doesn't fit existing routing table)
**Feeds INTO:** Any downstream skill when a seed matures — COO, Arena, Content Arc, Projects, PM Coordinator, Writing, Quantum Mirror
**Never calls:** COO triage, Proposal Builder, or any output-oriented skill during incubation. The seed-bed is pre-output.

**Vault interaction (all via osascript for iCloud access):**
- Reads/writes: `07 Capture/Seeds/` (primary)
- Reads: `07 Capture/01 Phone Notes/` (backlog processing)
- Reads: `07 Capture/Followups Inbox/` (routing non-seeds)
- Never writes to 02 Research/, 03 Projects/, or 01 Writing/ directly — matured seeds are handed to the downstream skill, which does its own intake.

## Quick Reference

| Situation | Action |
|---|---|
| New idea, can't classify | Intake Protocol → save seed |
| "What's growing?" | Weekly Seed Review |
| Seed has a buyer | → Service → COO |
| Seed can be wrong | → Claim → Arena |
| Seed has a voice | → Expression → Content/Writing |
| Seed has a use case | → Tool → Projects |
| Seed wants to be lived | → Practice → PM/QM |
| Seed won't differentiate | Zero-Measurement Zone |
| Backlog of phone notes | Capture Backlog Processing |
| "I should turn this into..." | Flag cognitive override |
