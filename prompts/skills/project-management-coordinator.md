---
name: project-management-coordinator
description: Coordinate active projects from booking through delivery for Rubinstein Productions. Track deliverables, deadlines, client communications, and capacity limits. Use when Isaac needs to organize project workflows, prepare for shoots, communicate with clients, or assess whether to take on new work.
---

# Project Management Coordinator

## Overview

This skill helps Isaac stay organized across all active projects—from pre-production planning through final delivery—while protecting his capacity limits and ensuring professional client communication.

---

## When to Use This Skill

**Use for:**
- Setting up new projects after booking
- Pre-production planning and checklists
- Tracking project status and deliverables
- Drafting client communications
- Checking current capacity before taking new work
- Managing timelines and deadlines
- Post-project wrap-up

**Quick Triggers:**
- "I just booked a new project"
- "What do I need to prepare for this shoot?"
- "Can I take on this new project?"
- "Draft an email to [client] about [topic]"
- "Where am I on [project]?"
- "Am I overcommitted right now?"

---

## Core Workflows

### 1. New Project Setup
### 2. Pre-Production Planning  
### 3. Client Communication
### 4. Project Tracking & Status Updates
### 5. Capacity Management

---

## 1. New Project Setup

### When You Book a New Project

**Step 1: Create Project File**

Load the project tracking template:
```bash
view /home/claude/project-management-coordinator/assets/project_tracking_template.md
```

**Customize with:**
- Project ID: RP-YYYY-###
- Client name and contact info
- Service type
- Key dates (shoot, delivery)
- Financial details (total, deposit, invoices)
- Scope and deliverables

**Step 2: Send Project Confirmation Email**

Load communication templates:
```bash
view /home/claude/project-management-coordinator/references/client_communication_templates.md
```

Use "Project Confirmation Email" template—customize with:
- Contract and deposit instructions
- Kickoff call scheduling
- Timeline overview
- Next steps

**Step 3: Update Capacity Dashboard**

Check current capacity:
```bash
view /home/claude/project-management-coordinator/references/capacity_management.md
```

Add new project to active project count:
- Confirm you're still under limits (2 at 9-5, 4 full-time)
- Mark capacity status (under/at/over)
- Block calendar for shoot dates + buffer

**Step 4: Schedule Kickoff Call**

**Within 1 week of booking:**
- 30-minute call or video chat
- Review project goals, audience, vision
- Discuss timeline and expectations
- Answer client questions
- Build rapport

---

## 2. Pre-Production Planning

### Choosing the Right Checklist

Load pre-production checklists:
```bash
view /home/claude/project-management-coordinator/references/preproduction_checklists.md
```

**Available checklists:**
- Headshot Session
- Event Photography
- Standard Video Package
- Story-First Video
- Narrative Consulting + Video Package
- Universal Pre-Production (all projects)

**Select based on service type** and work through timeline:
- 2-4 weeks before: Major planning
- 1 week before: Detail finalization
- Day before: Final confirmation
- Day of: Execution

### Key Pre-Production Tasks

**Creative Planning:**
- [ ] Shot list created
- [ ] Interview questions prepared (if applicable)
- [ ] B-roll needs identified
- [ ] Narrative focus clarified

**Logistics:**
- [ ] Location confirmed (address, parking, access)
- [ ] Shoot date/time locked in
- [ ] Participants confirmed
- [ ] Weather backup plan (if outdoor)
- [ ] Call sheet created and sent

**Technical:**
- [ ] Equipment list finalized
- [ ] All gear tested day before
- [ ] Batteries charged
- [ ] Memory cards formatted
- [ ] Backup equipment ready

**Communication:**
- [ ] Client briefing document sent (3-5 days before)
- [ ] Shoot day confirmation sent (48 hours before)
- [ ] Emergency contact info exchanged
- [ ] Any special requests documented

### Creating a Call Sheet

Use template from pre-production checklists:

**Include:**
- Project name and date
- Call time and estimated wrap
- Location (address, parking, entry instructions)
- Crew and talent
- Schedule/timeline
- Shot list priorities
- Equipment summary
- Weather forecast + backup plan
- Contact information

**Send to client 2-3 days before shoot**

---

## 3. Client Communication

### Communication Principles

**Tone:**
- Professional but warm
- Clear and specific
- Proactive, not reactive
- Grateful and positive

**Response Times:**
- Client questions: Within 24 business hours
- Urgent issues: Same day
- Non-urgent updates: Within 48 hours

### Standard Communications by Phase

Load templates:
```bash
view /home/claude/project-management-coordinator/references/client_communication_templates.md
```

**Booking Phase:**
- Project confirmation email
- Contract and deposit instructions
- Welcome (for retainer clients)

**Pre-Production:**
- Kickoff call scheduling
- Shot list review
- Pre-shoot consultation
- Shoot day confirmation (48 hours before)

**Production:**
- Running late notification (if needed)
- Post-shoot thank you (same day or next day)

**Post-Production:**
- First cut delivery
- Revision complete
- Final delivery

**Wrap-Up:**
- Testimonial request
- Project close-out
- Future work outreach

**Problem Management:**
- Running behind on deadline
- Scope creep / additional work requests
- Difficult client situations
- Payment reminders

### Customizing Templates

**Always personalize:**
- Reference specific project details
- Add personal observations
- Use client's name
- Mention things from conversations
- Adjust formality based on relationship

**Don't:**
- Send copy-paste with [BRACKETS] unfilled
- Use robotic/corporate language
- Over-explain or apologize excessively
- Assume tone—read aloud before sending

---

## 4. Project Tracking & Status Updates

### Maintaining Project Status

**Update project file regularly:**

**Weekly (for active projects):**
- Mark completed tasks in timeline
- Log any client communications
- Note hours worked
- Flag any concerns or delays

**After major milestones:**
- Shoot complete → Update status
- First cut sent → Log date, wait for feedback
- Revisions done → Mark ready for delivery
- Final delivery → Move to wrap-up phase

### Project Status Categories

**Booked:**
- Contract signed, deposit received
- Shoot date scheduled but 3+ weeks away
- Minimal active work

**Pre-Production:**
- Active planning underway
- Shoot date within 3 weeks
- Creative/logistical details being finalized

**Production:**
- Shoot week (day before through day after)
- Maximum focus on this project

**Post-Production:**
- Editing/organizing in progress
- First cut being created
- Most time-intensive phase for video
- → For Resolve-specific guidance (project setup, color pipeline, audio, export), load `davinci-resolve`

**Revisions:**
- Waiting on client feedback
- Incorporating feedback
- Revision rounds in progress

**Delivered:**
- Final files sent to client
- Waiting on final payment (if applicable)
- Testimonial requested

**Complete:**
- All deliverables sent
- All payments received
- Project fully wrapped

### Weekly Status Updates (Long Projects)

For projects longer than 2 weeks, send weekly update:

**Template:**
```
[Project Name] - Week [#] Update

COMPLETED:
- [What's been done]

WORKING ON:
- [Current focus]

NEXT:
- [Upcoming milestone]: [Date]

NEED FROM YOU:
- [Any client input/decisions needed]
```

---

## 5. Capacity Management

### Current Capacity Check

**Before taking any new project, assess:**

Load capacity management reference:
```bash
view /home/claude/project-management-coordinator/references/capacity_management.md
```

**Step 1: Count Active Projects**

"Active" = Pre-production, Production, or Post-production phase

**Count:**
1. [Project Name] - [Phase]
2. [Project Name] - [Phase]

**Total:** [#] active projects

**Step 2: Check Against Limits**

**At 9-5 Job:**
- 0-1 projects: ✅ Have capacity
- 2 projects: ⚠️ At capacity (can book with timeline buffer)
- 3+ projects: 🚨 Over capacity (decline or delay)

**Full-Time RP:**
- 0-3 projects: ✅ Have capacity
- 4 projects: ⚠️ At capacity (be selective)
- 5+ projects: 🚨 Over capacity

**Step 3: Timeline Analysis**

**Check:**
- When would new project shoot/work happen?
- Is there 1+ week buffer between projects?
- Any overlap with current project deadlines?

**Example:**
```
Current Project A: Delivering March 1
New Project: Would shoot March 10
Buffer: ✅ 9 days between (good)
```

**Step 4: Energy Assessment**

**Check sacral response:**
- Immediate "hell yes"? → Good sign
- Mental "should"? → Red flag
- Dread? → Definite no

**Check recent energy:**
- Last 2 weeks averaging 7+? → Have capacity
- Last 2 weeks below 6? → Need rest

**Step 5: Make Decision**

**Decision Matrix:**

**Scenario A: Under Capacity + Good Timing + High Energy + Sacral Yes**
→ Take the project

**Scenario B: At Capacity + Tight Timing**
→ Offer later date or politely decline

**Scenario C: Over Capacity**
→ Decline immediately (don't even consider)

**Scenario D: Under Capacity BUT Low Energy**
→ Decline to protect regeneration

### Declining Projects Gracefully

**Templates from capacity management:**

**At Capacity:**
"Thanks for reaching out! I'm currently at capacity, but I'd love to work together. My next availability is [date]. Would that timing work?"

**Need Buffer:**
"I'm honored you thought of me! That timeline conflicts with current commitments. I could start [later date] if you have flexibility?"

**Just Need Rest:**
"Thanks for thinking of me! I'm at capacity through [date] to ensure quality for current clients. If timing shifts, please reach out!"

### Capacity Dashboard

**Maintain weekly:**

```
=== Capacity Dashboard ===

ACTIVE PROJECTS:
1. [Name] - [Phase] - [Hours this week]
2. [Name] - [Phase] - [Hours this week]

Total: [#] / [2 or 4]
Status: [Under / At / Over capacity]

WEEKLY HOURS: [Total]
ENERGY LEVEL: [1-10]

UPCOMING:
- [Next shoot date]
- [Next delivery deadline]

AVAILABILITY:
Next open slot: [Date]
```

---

## Integration with Calendar (Google Calendar)

### Blocking Project Time

**Use Google Calendar to visualize capacity:**

**Block these for each project:**
- **Shoot days:** Full day blocked
- **Buffer before shoot:** 1 day for prep
- **Buffer after shoot:** 1 day for backup/organization
- **Editing blocks:** 2-4 hour chunks (don't leave editing to "whenever")
- **Client review time:** 2-3 days for feedback window
- **Revision work:** 1-2 hour blocks

**Example Calendar (Video Project):**
```
March 1: Pre-production prep
March 2: Shoot day
March 3: Backup/organize files
March 4-5: Editing block (4 hours each)
March 6-8: Client review (waiting)
March 9: Revision work (2 hours)
March 10: Final delivery
```

### Finding Free Capacity in Calendar

**Use list_gcal_events tool to check upcoming:**

```
When client asks: "When's your next availability?"

1. Check current projects in calendar
2. Find 1-week windows with no project blocks
3. Offer 2-3 specific date options
```

**Example:**
"I have availability March 15-22 or April 5-12. Which works better for you?"

(Specific dates > vague "next month sometime")

---

## Project Management Best Practices

### Communication Cadence

**Immediately:**
- Respond to urgent client issues
- Confirm project bookings
- Send shoot day confirmations

**Within 24 Hours:**
- Answer client questions
- Acknowledge feedback received
- Send post-shoot thank you

**Weekly:**
- Status updates (for long projects)
- Check in on pending approvals
- Update project files

**Don't Over-Communicate:**
- Client doesn't need daily updates
- Trust them to review on their timeline
- One reminder about pending feedback is enough

### File Organization

**Create folder structure per project:**
```
RP-2025-001-ClientName-ProjectType/
├── 01-Planning/
│   ├── shot-list.md
│   ├── call-sheet.md
│   └── client-brief.pdf
├── 02-Raw/
│   ├── shoot-1/
│   └── shoot-2/
├── 03-Edits/
│   ├── first-cut/
│   ├── revision-1/
│   └── final/
├── 04-Deliverables/
│   ├── final-files/
│   └── social-cuts/
├── 05-Admin/
│   ├── contract.pdf
│   ├── invoices/
│   └── communication-log.md
└── project-tracker.md
```

### Backup Workflow

**Immediately after shoot:**
1. Import files to computer
2. Copy to external hard drive
3. Copy to cloud backup (Google Drive/Dropbox)
4. Verify files not corrupted
5. DO NOT format cards until verified

**After final delivery:**
- Keep project files for 90 days
- Archive to external drive
- Delete from primary drive (free space)
- Document archive location in project tracker

---

## Post-Project Wrap-Up

### Completion Checklist

After project fully delivered and paid:

- [ ] **Final invoice sent and paid**
- [ ] **Testimonial requested**
- [ ] **Hours logged for profitability tracking**
- [ ] **Post-project review completed**
  - What went well?
  - What could improve?
  - Was pricing right?
  - Would I work with them again?
- [ ] **Portfolio decision made**
  - Add to website?
  - Case study worthy?
- [ ] **Project files archived**
- [ ] **Thank you note sent**
- [ ] **Referral request (if appropriate)**
- [ ] **Update project status to "Complete"**
- [ ] **Remove from active capacity count**

### Post-Project Review Template

**From project tracking template:**

**What Went Well:**
[Successes to repeat]

**What Could Improve:**
[Challenges to avoid next time]

**Pricing Reflection:**
- Was price right for scope?
- Did I underestimate time?
- Should I raise prices for similar work?

**Client Experience:**
- Were they happy?
- Would I work with them again?
- Referral potential?

**Learnings:**
[Key takeaways for future projects]

---

## Troubleshooting Common Issues

### "I have too many projects and I'm overwhelmed"

**Immediate:**
1. List all active projects with status
2. Identify which have nearest deadlines
3. Communicate proactively with clients if delays expected
4. Stop taking new work immediately
5. Delegate what you can (editing, admin)
6. Work through one at a time

**Prevention:**
- Enforce capacity limits strictly
- Use 1-week buffers between projects
- Track energy levels weekly
- Say no more often

### "Client keeps requesting changes outside scope"

**Response:**
1. Load client communication templates
2. Use "Scope Creep / Additional Work Request" template
3. Clarify original scope vs. new requests
4. Offer to add for additional fee
5. Stay professional and firm

**Prevention:**
- Very clear deliverables list in proposal
- Review scope in kickoff call
- Document everything in writing

### "I'm behind on a deadline"

**Response:**
1. Assess how much delay (days? week?)
2. Send "Running Behind on Deadline" email immediately
3. Give realistic new timeline
4. Offer something extra if significant delay
5. Learn from it for future time estimation

**Prevention:**
- Build buffer into all timelines
- Block editing time in calendar
- Don't say "it'll be quick"—estimate high

### "Client not responding to review/feedback request"

**Response:**
1. Send gentle reminder after 3-4 days
2. Call if no response after 7 days
3. Set deadline: "If I don't hear back by [date], I'll assume current version is approved"
4. Don't chase indefinitely

**Prevention:**
- Set expectations in initial email
- Give clear deadline for feedback
- Build this into timeline

### "I can't remember what I promised this client"

**Response:**
- Check project tracking file
- Review original proposal
- Review contract
- Search email for confirmation

**Prevention:**
- Document everything in project tracker
- Update immediately after conversations
- Reference notes before any communication

---

## Weekly Project Management Routine

### Sunday Planning (30 minutes)

**Review week ahead:**
- [ ] Check calendar for shoot days
- [ ] Review active project statuses
- [ ] Identify deadlines approaching
- [ ] Plan editing blocks
- [ ] Check for client communications needing response

**Create week priorities:**
1. [Most urgent project task]
2. [Second priority]
3. [Third priority]

**Send any needed updates:**
- Status updates for long projects
- Follow-ups on pending items
- Schedule confirmations

### Friday Wrap-Up (15 minutes)

**Update all project files:**
- [ ] Mark completed tasks
- [ ] Log hours worked
- [ ] Note any blockers or concerns
- [ ] Update client communication log

**Prep for next week:**
- [ ] Upcoming shoots confirmed?
- [ ] Clients reminded of pending reviews?
- [ ] Equipment prep needed?

---

## Resources

### assets/
- `project_tracking_template.md` - Complete project management template

### references/
- `preproduction_checklists.md` - Checklists for all project types
- `client_communication_templates.md` - Emails for every project phase
- `capacity_management.md` - Tracking and protecting your capacity

---

## Related Skills

- `rubinstein-productions-coo` - Capacity limits, energy management, decision frameworks
- `proposal-scope-builder` - Scope carries into project tracking
- `invoice-financial-tracker` - Hours tracking, project profitability
- `davinci-resolve` - Post-production guidance for the editing, color, audio, and export phases
- Google Calendar integration - Visual capacity planning

---

## Quick Reference

**Capacity Limits:**
- At 9-5: Max 2 active projects
- Full-time: Max 4 active projects
- Buffer: 1 week between projects

**Project Phases:**
- Booked → Pre-Production → Production → Post-Production → Revisions → Delivered → Complete

**Response Times:**
- Urgent: Same day
- Questions: 24 hours
- Non-urgent: 48 hours

**Pre-Production Timeline:**
- 2-4 weeks before: Major planning
- 1 week before: Details finalized
- Day before: Final confirmation
- Day of: Execution

**Post-Shoot:**
- Thank you: Same day or next day
- Backup files: Immediately
- First cut: Per agreed timeline
- Revisions: 3-5 business days

**Wrap-Up:**
- Request testimonial after delivery
- Archive project after 90 days
- Update capacity dashboard immediately
