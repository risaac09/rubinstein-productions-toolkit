# How to Create a Weekly Retro Skill

Here is a step-by-step walkthrough for building a Claude Code skill that facilitates weekly retrospectives with your team.

## 1. Create the skill file

Skills live in `~/.claude/skills/`. Create a new markdown file for the skill:

```bash
touch ~/.claude/skills/weekly-retro.md
```

## 2. Write the YAML frontmatter

Every skill file starts with frontmatter that tells Claude when and how to use it. Open the file and add:

```yaml
---
name: weekly-retro
description: >
  Facilitate weekly team retrospectives. Use when Isaac says
  "run retro", "weekly retro", "team retro", or "what went well this week".
---
```

The `description` field is what Claude reads to decide whether to activate the skill, so pack it with the trigger phrases you actually use.

## 3. Write the skill body

Below the frontmatter, write the instructions in plain markdown. Think of this as a prompt you are handing to Claude at the moment the skill fires. A solid retro skill has three sections: context gathering, facilitation, and output.

Here is a complete example body:

```markdown
# Weekly Retro Facilitator

You are facilitating a weekly retrospective for Isaac's team.

## Step 1 — Set the frame

Ask the user:
1. Who is in the retro today? (names or roles)
2. What was the time period? (default: the past 7 days)
3. Any specific project or theme to focus on?

## Step 2 — Collect input using three columns

Walk through each column one at a time. For each column, ask open-ended
questions and record responses:

**What went well**
- Ask: "What worked this week that we should keep doing?"
- Probe for specifics: names, decisions, moments.

**What could improve**
- Ask: "Where did we feel friction, confusion, or slowdown?"
- Keep it observational, not blame-oriented.

**Action items**
- Ask: "What is one concrete thing we will change next week?"
- Each action item must have an owner and a deadline.

## Step 3 — Synthesize and output

Produce a retro summary in this format:

### Retro — [date range]
**Attendees:** [names]

#### Went Well
- [item]

#### Could Improve
- [item]

#### Action Items
- [ ] [action] — owner: [name], due: [date]

## Step 4 — Store the record

Save the summary to a file at:
`~/rubinstein-productions-toolkit/retros/retro-YYYY-MM-DD.md`

Create the `retros/` directory if it does not exist.

## Facilitation notes

- Keep the tone warm and direct. No corporate jargon.
- If someone raises a sensitive interpersonal issue, acknowledge it
  and suggest taking it offline rather than recording it in the notes.
- Time-box each column to roughly 5 minutes of conversation.
- End by reading back the action items and confirming owners agree.
```

## 4. Save and test

Save the file. Then open a new Claude Code session and say:

```
run retro
```

Claude should pick up the skill from the description match and begin the facilitation flow. If it does not trigger, check that the file is in `~/.claude/skills/` and that the description contains your trigger phrase.

## 5. Iterate on the prompt

After your first retro, review what worked and what felt off. Common adjustments:

- **Add more probing questions** if the team gives shallow answers.
- **Change the output format** if markdown is not where you want the record (for example, append to a Notion page or a Google Doc via MCP).
- **Add a "check previous retro" step** at the top so Claude reads last week's action items and asks whether they were completed.
- **Integrate with calendar** by having the skill check Google Calendar for what meetings/events happened that week to jog memory.

## 6. Optional: wire it into a scheduled task

If you want a reminder or automatic kick-off every Friday, you can create a scheduled task that prompts you to start the retro:

```
/schedule create weekly-retro-reminder --cron "0 15 * * 5" --prompt "It's Friday at 3pm. Time for the weekly retro. Run the weekly-retro skill."
```

## Anatomy recap

A skill is just a markdown file with:

| Part | Purpose |
|---|---|
| `name` in frontmatter | Human-readable identifier |
| `description` in frontmatter | Trigger-matching text Claude uses to decide when to activate |
| Body (markdown) | The full prompt Claude follows when the skill is active |

There is no build step, no registration command, no config file to update. Drop the `.md` file in the skills directory and it is live.

## Tips for writing good skill prompts

1. **Be specific about output format.** If you want a checklist, show the exact checklist shape. Claude follows examples better than abstract descriptions.
2. **Use numbered steps.** Claude executes sequential instructions more reliably than a wall of prose.
3. **Name your audience.** "You are facilitating a retro for Isaac's team" gives Claude a role and a context.
4. **Include failure modes.** Tell Claude what to do when something goes sideways (someone raises a sensitive topic, nobody has input for a column, etc.).
5. **Keep it under 500 lines.** If the skill file is getting long, you probably need two skills, not one.
