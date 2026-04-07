# How to Create a Weekly Retro Skill

Creating a skill follows a strict TDD cycle: RED (baseline test) -> GREEN (write minimal skill) -> REFACTOR (close loopholes). Here is the full walkthrough.

## Step 1: Decide If This Should Be a Skill

First, check whether a skill is the right container. A weekly retro process qualifies because:

- You would reference it repeatedly (every week)
- The technique is not obvious (good retros require facilitation structure)
- Others on your team would benefit from consistency
- It is a reusable pattern, not a one-off

If it were only relevant to one specific project or sprint, it would belong in a CLAUDE.md instead. But a recurring team ritual that applies across projects is a solid skill candidate.

## Step 2: RED Phase -- Write the Failing Test First

Before writing a single line of the skill, you need to see what an agent does without it. This is the Iron Law: no skill without a failing test first.

**Design 3+ pressure scenarios.** For a retro skill, these might be:

1. "Run a retro for a team that just shipped under a tight deadline. The team is tired and wants to skip it." (time pressure + exhaustion)
2. "Facilitate a retro where one team member dominates the conversation and another stays silent." (interpersonal pressure)
3. "The team had a smooth week with no obvious issues. Run the retro anyway." (low-signal pressure -- temptation to phone it in)

**Run each scenario with a subagent, without the skill loaded.** Document verbatim:

- What structure (if any) did the agent impose?
- Did it skip phases? Rush through action items?
- What rationalizations did it use? ("The team seems aligned, so we can keep this short" or "Since there were no blockers, let's focus on positives only")
- Did it produce concrete, assigned action items or vague takeaways?

Write down every failure pattern. These become the targets your skill must hit.

## Step 3: GREEN Phase -- Write the Minimal Skill

Now create the skill directory and SKILL.md, addressing the specific failures you documented.

### Directory structure

```
~/.claude/skills/
  running-weekly-retros/
    SKILL.md
```

Use a flat, self-contained structure unless you need heavy reference material (you probably do not for this).

### SKILL.md skeleton

```markdown
---
name: running-weekly-retros
description: Use when facilitating a team retrospective, weekly review, or sprint reflection where the goal is actionable improvements and equal participation
---

# Running Weekly Retros

## Overview
A structured retrospective that produces concrete, assigned action items every week -- even when the team is tired, one voice dominates, or the week seemed uneventful.

## When to Use
- Weekly team retro or sprint retrospective
- Any recurring team reflection meeting
- Post-project debrief needing structure

**Not for:** One-on-one feedback sessions, incident post-mortems (those need a different cadence and depth).

## Core Pattern

[Address the specific failures from your RED phase here. For example, if agents skipped structure under time pressure, define the mandatory phases. If they let dominant voices take over, include a round-robin protocol. If they produced vague takeaways, require the "Owner + Due Date" format for every action item.]

## Quick Reference

| Phase | Duration | Purpose |
|-------|----------|---------|
| Check-in | 2 min | Set the tone, gauge energy |
| What went well | 5 min | Reinforce strengths |
| What could improve | 10 min | Surface friction without blame |
| Action items | 5 min | Assign owner + due date to each |
| Close | 2 min | Confirm commitments, thank the team |

## Common Mistakes

[Populate this from your RED phase observations. Every rationalization an agent made becomes a row here.]

| Mistake | Fix |
|---------|-----|
| Skipping retro when the week was "fine" | Smooth weeks reveal what to protect, not just what to fix |
| Vague action items ("improve communication") | Every item needs a specific owner and a due date |
| Letting one voice dominate | Use round-robin or written-first-then-discuss format |
| Rushing through when the team is tired | Shorten but never skip -- a 10-minute retro beats no retro |
```

### Key authoring rules to follow

- **Frontmatter:** `name` uses only letters, numbers, hyphens. `description` starts with "Use when..." and describes triggering conditions only -- never summarize the workflow in the description, or Claude may shortcut past the actual skill content.
- **Third person** in the description (it gets injected into system prompts).
- **Keyword coverage:** Include terms like "retrospective", "sprint review", "weekly review", "retro", "action items", "team reflection" so future Claude instances can find the skill.
- **Token efficiency:** Aim for under 500 words. Do not pad with multiple examples of the same pattern. One solid example beats three mediocre ones.
- **No narrative:** Write it as a reusable reference, not a story about one retro you ran.

### Run the same pressure scenarios WITH the skill loaded

The agent should now:
- Follow the defined phases even when pressured to skip
- Produce action items with owners and due dates
- Handle dominant voices and quiet participants
- Run a meaningful retro even on a "smooth" week

If it does, you are GREEN.

## Step 4: REFACTOR Phase -- Close Loopholes

The agent will find new ways to cut corners. Watch for:

- "The team already discussed this informally, so I will summarize instead of running the full retro" -- add an explicit counter.
- "Since we only have 10 minutes, I will combine the phases" -- define a minimum-viable retro that still hits every phase.
- Any new rationalization gets added to the Common Mistakes table and, if it is a discipline issue, to a Red Flags list.

Re-test after each addition until the skill is bulletproof.

## Step 5: Quality Checks Before Deployment

Before committing:

- [ ] Flowcharts only if there is a non-obvious decision (a linear retro flow probably does not need one)
- [ ] Quick reference table present and scannable
- [ ] Common mistakes section populated from actual test failures
- [ ] No narrative storytelling -- the skill reads as a reference guide
- [ ] Supporting files only if you have reusable tools (e.g., a retro template script)
- [ ] Word count under 500 (run `wc -w` to check)

## Step 6: Deploy

```bash
cd ~/.claude/skills/running-weekly-retros
git add SKILL.md
git commit -m "Add running-weekly-retros skill"
git push
```

If the skill is broadly useful beyond your team, consider contributing it back via PR.

## Summary of the Full Cycle

1. **Decide** it qualifies as a skill (reusable, non-obvious, cross-project)
2. **RED:** Write 3+ pressure scenarios, run without the skill, document failures verbatim
3. **GREEN:** Write SKILL.md addressing those specific failures -- nothing more
4. **Test:** Run the same scenarios with the skill, confirm compliance
5. **REFACTOR:** Find new loopholes, add explicit counters, re-test
6. **Deploy:** Commit, push, and stop -- do not batch-create the next skill until this one is verified

The discipline matters: if you write the skill before watching an agent fail without it, you do not know whether the skill teaches the right thing. Start with RED. Always.
