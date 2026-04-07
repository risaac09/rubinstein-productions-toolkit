## Assessment of the Meeting Facilitation Skill Draft

### What works

The draft captures a reasonable skeleton of meeting facilitation: check-in rounds build psychological safety, timeboxing agenda items prevents drift, and closing with action items ties discussion to accountability. The 45-minute cap is a defensible default.

### What is missing

**1. No defined role or trigger.** A skill needs to tell Claude *when* it activates and *what persona* it adopts. Who is the facilitator here -- Claude, the user, or both? When should this skill fire? Without a trigger condition (e.g., "Use when the user asks to plan, run, or debrief a meeting"), it floats without context.

**2. No adaptation to meeting type.** A brainstorm, a decision-making session, a retrospective, and a status update each need different structures. The draft prescribes one rigid format. A stronger skill would branch based on meeting purpose or let the facilitator choose a mode.

**3. The check-in round lacks guidance.** "Start with a check-in round" says what but not how. How long should it take? What kind of prompt? A one-word weather check for a 10-person standup is different from a two-minute somatic check-in for a four-person creative session. The skill should offer at least two or three check-in formats scaled to group size and meeting type.

**4. No guidance on agenda design.** The skill assumes an agenda already exists. It says nothing about how to shape one -- how many items, how to prioritize, how to handle items that arrive mid-meeting. A facilitation skill should help *build* the agenda, not just time it.

**5. Timer durations are unspecified.** "Set a timer for each agenda item" is a mechanism without a heuristic. How should time be allocated? Proportionally? By complexity? What happens when a timer expires -- hard stop, one-minute extension, parking lot? These decision rules are where the actual facilitation value lives.

**6. Action items lack structure.** "End with action items" is too vague. Strong facilitation produces action items with four components: the task, the owner, the deadline, and the definition of done. The skill should enforce or at least prompt for all four.

**7. No handling of group dynamics.** Facilitation is not just process management. The skill says nothing about what to do when one person dominates, when conflict arises, when energy drops, or when the group gets stuck. Even a few heuristics here would make the skill meaningfully more useful.

**8. No pre-meeting or post-meeting phases.** Good facilitation extends beyond the live session. Pre-meeting: send the agenda in advance, confirm attendees, clarify desired outcomes. Post-meeting: distribute notes and action items within 24 hours, follow up on commitments. The skill covers only the middle.

**9. No output format.** The skill does not specify what Claude should produce. Should it generate a meeting plan? Real-time facilitation prompts? A post-meeting summary? A skill needs to define its deliverable.

**10. The 45-minute limit is too absolute.** It works as a default, but some meetings genuinely need more time (workshops, strategic planning, conflict resolution). The skill should frame 45 minutes as a default with explicit criteria for when longer sessions are warranted and how to structure breaks if they are.

### Summary

The draft is a decent first instinct but reads more like a personal note than a functional skill. It needs a trigger condition, role definition, output format, branching logic for meeting types, structured action-item capture, and at least basic guidance on group dynamics. The mechanical elements (timer, check-in, action items) are a start, but facilitation lives in the judgment calls between those elements -- and that is where the skill should invest its depth.
