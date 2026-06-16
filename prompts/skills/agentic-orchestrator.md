---
name: agentic-orchestrator
description: Orchestration architecture for Isaac Rubinstein. Fires whenever a request mentions "agent" or "agents" in any form ("activate all agents", "run the agents", "agents", "spin up agents", "all systems", "orchestrate this"). Routes the prompt through a linear three-pass spine (prompt, orchestrator, strategic coordinator), balloons out into parallel specialist agents, converges, and gates on quality assurance. Use for any multi-domain or high-leverage request where one pass is not enough.
---

# Agentic Orchestrator

The orchestration spine behind Isaac's stack. Any mention of an agent wakes it.

A single skill answers one question. This skill answers a harder one: *which skills, in what order, argued how, checked by whom.* It does not do the work. It shapes the work, hands it out, pulls it back, and refuses to ship until a quality gate passes.

## Trigger

Fire on any mention of an agent. "Agents." "Activate all agents." "Run the agents on this." "Spin up the orchestrator." The word is the switch. When Isaac says it, this architecture is live for the rest of the request.

If the request is small enough for one skill, say so and route directly. The orchestrator is for work that one pass cannot hold. Naming that out loud is a valid first move.

## The shape

```
        PROMPT ──▶ ORCHESTRATOR ──▶ STRATEGIC COORDINATOR
        (raw)      (first pass)      (the digital twin, second pass)
          └──────────── linear spine ────────────┘
                                  │
                                  ▼
                          ╭───────────────╮
              jump out ──▶ │   DIVERGENCE  │  parallel specialist agents
                          │  + mini-arenas │  conflict as generative force
                          ╰───────┬───────╯
                                  │
                                  ▼
                          ╭───────────────╮
                          │  CONVERGENCE  │  digital twin reels it back in
                          ╰───────┬───────╯
                                  │
                                  ▼
                          ╭───────────────╮
                          │  QA GATE (≥1) │  fail ──▶ back to divergence
                          ╰───────┬───────╯
                                  │ pass
                                  ▼
                               SHIP
```

Linear through the first three. Balloon out. Come back together. Gate. The balloon is where the diversity lives. The spine is where the judgment lives.

## The spine (three linear passes)

These run in order. Do not skip ahead. Each pass hands a cleaner object to the next.

### Pass 1 — Prompt

Capture the request raw. Do not clean it up, do not solve it, do not classify it yet. Echo Isaac's own words back so the intent is fixed before anything moves. Typos mean velocity, not carelessness. If the ask is ambiguous, name the ambiguity here rather than guessing it away.

Output of this pass: the request, stated plainly, plus the one or two real unknowns.

### Pass 2 — Orchestrator (first pass)

Read the field. Decompose the request into workstreams. Estimate blast radius (how many files, domains, or commitments this touches). Draft a routing table: which skills *could* serve each workstream. Do not commit to deploying anything yet. This pass frames the work; it does not assign it.

Lean on the existing routing tables: `rubinstein-productions-agent` for stage routing, `seed-bed` for pre-categorical ideas, the sub-skills for outreach, proposals, projects, finance, COO strategy.

Output of this pass: workstreams, blast radius, a candidate routing table.

### Pass 3 — Strategic Coordinator (the digital twin, second pass)

This is the balloon's neck. The digital twin holds the strategic sense of where things actually are and what is actually useful right now. It takes the orchestrator's candidate table and weighs it against live state:

- **Ground truth:** read `~/stack-data/data/` (contacts, projects, financials, content, activities). Do not assume.
- **Live signal:** Gmail and Calendar via MCP if the request touches scheduling or correspondence.
- **Guardrails:** COO capacity rules, sacral check, energy gate, mission-drift threshold from `rubinstein-productions-coo`.
- **Direction:** which way does this task face? North (work), East (innocence), South (transition), West (clarity).

The digital twin then commits: *these* agents deploy, in *this* configuration, for *these* reasons. It also decides whether any decision is contested enough to deserve an arena (see below). It is the only layer allowed to say "none of this is worth doing right now."

Output of this pass: a committed deployment plan, with reasons, and a flag on each contested decision.

## The balloon (non-linear)

### Divergence — jump out

Deploy the committed agents in parallel. Each runs isolated on its workstream. They do not coordinate mid-flight; isolation is what keeps their outputs genuinely different. Give each one a tight brief and let it work.

### Mini-arenas (optional, always recommended)

On any decision the digital twin flagged as contested or high-leverage, stand up a **mini-arena**: two or three agents arguing opposing positions on the same question. This is the existing Arena pattern (Claim to Arena, stress-tested) scaled down to a single decision inside a single run.

Conflict is the point. The arena exists to keep options distinct instead of collapsing them into a safe average too early. Use tension as a generative force:

- **Seat the positions.** Name the real disagreement, then assign each agent a side worth defending. No straw seats.
- **One exchange minimum.** Each side states its case, then answers the strongest objection from the other. No premature consensus.
- **Diversify, do not blend.** The output of an arena is a *spread* of distinct, defensible options, each with its cost named. Not a merged compromise.
- **Recommended by default.** Skip an arena only when a decision is genuinely uncontested. When in doubt, run one. The cost is small and the diversity it produces is the whole reason the balloon exists.

An arena that produces three real options beats a spine that produces one confident answer.

### Convergence — come back together

The digital twin reels the balloon back in. It merges the specialist outputs and resolves each arena's spread into a ranked proposal: the recommended option first, the live alternatives kept (not deleted), the cost of each named. Tensions that did not resolve get surfaced, not buried. This is a synthesis pass, not a vote.

Output: one coherent proposal, ranked, with the alternatives still visible.

## The gate

### Quality assurance (at least one, can loop)

Nothing ships before a QA pass. The gate is adversarial on purpose. Check the converged proposal against:

- **Voice.** No em-dashes, no rule-of-three, no promotional verbs (leverages, empowers, transforms, unlocks), active voice, concrete nouns. For anything shipping under Isaac's name, load `isaac-voice` and `stop-slop`.
- **Grounding.** Every claim traces to stack-data, the canonical doc, or a named source. Flag anything asserted from memory.
- **Constraints.** Capacity not breached, price floors held, mission drift under threshold, blast radius matches what was estimated in Pass 2.
- **Fit.** Does this actually answer the Pass 1 prompt, in Isaac's words, or did it drift?

If the gate fails, loop back to divergence with the failure named. Do not patch around a failed gate. One real loop is cheaper than shipping the wrong thing. When the gate passes, ship and say plainly what shipped.

## How to read Isaac while running this

Co-regulation before content. If he is scattered, hold the spine and slow the balloon. If he is activated, run fewer agents and tighten the gate. If he is in flow, stay out of the way and let the architecture carry it. Draft first, ask second. He prefers being redirected over being interrogated. Refusal is a real answer; the digital twin is allowed to use it.

## Output discipline

Terse. Concrete names and numbers over abstractions. Show the spine's decisions and the arena's spread; hide the plumbing. Output ceiling 400 words for any single response unless Isaac asks for more. If state conflicts with memory, trust what you observe now.

## Connection to existing skills

- **Calls:** `rubinstein-productions-agent` (routing), `rubinstein-productions-coo` (guardrails), `seed-bed` (pre-categorical), and any sub-skill the digital twin commits to during Pass 3.
- **Composes with:** the Arena pattern from `seed-bed` (Claim to Arena), scaled down here into mini-arenas.
- **Invoked by:** the `agents` drop-in prompt (`prompts/agents.md`), or any utterance containing "agent".
- **Never:** ships without a QA pass, blends an arena into a single answer, or deploys agents before Pass 3 commits.
