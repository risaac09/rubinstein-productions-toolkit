# Handoff — M2 Pro continues the agentic-orchestrator

Drop-in for a fresh Claude Code session on the M2 Pro. The architecture was
built in an ephemeral web session with no access to live state, the vault, or
MCP. This machine has all three. Pick up where that session went blind.

---

Pick up the agentic-orchestrator work started in a web session.

Branch: `claude/agentic-orchestrator-digital-twin-5xot4c` (draft PR #3) in
`~/rubinstein-productions-toolkit`. Check it out and pull.

What's already there:
- `prompts/skills/agentic-orchestrator.md` — the architecture. Trigger word
  "agent(s)". Linear spine (Prompt -> Orchestrator -> Strategic Coordinator,
  the digital twin), then balloon out to parallel agents with optional
  mini-arenas, converge, gate on QA.
- `prompts/agents.md` — the drop-in trigger, mirrors `all-systems-go.md`.

The web session was blind to live state. You aren't. Do the three things it
couldn't:

1. **Ground it.** Read `~/stack-data/data/` (contacts, projects, financials,
   content, activities) and confirm Pass 3 (the digital twin) reaches the
   right ground truth. Cross-check the COO guardrails in
   `rp-intranet` `docs/operations/rubinstein-productions-coo-skill.md`. Fix any path or skill name
   that doesn't resolve on this machine.

2. **Resolve the open question** in PR #3: does the digital twin stay folded
   into the orchestrator as Pass 3, or become its own skill file? Decide on
   the evidence, then make the change.

3. **Dry-run it.** Take one real request (something live in stack-data right
   now) and walk the full spine out loud: spine, one mini-arena, convergence,
   QA gate. See where the architecture drags or skips. Tighten the skill from
   what you observe, not from what reads well.

Voice: no em-dashes, no rule-of-three, no promotional verbs, active voice.
Commit to the same branch, push, update PR #3. Leave it in draft; Isaac takes
it out of draft after he reads it.

If stack-data or the vault conflicts with what the skill assumes, trust what
you observe now and flag the conflict.
