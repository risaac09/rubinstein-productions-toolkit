---
name: agentic-orchestrator
description: Orchestration architecture for Isaac Rubinstein. Fires whenever a request mentions "agent" or "agents" in any form ("activate all agents", "run the agents", "agents", "spin up agents", "all systems", "orchestrate this"). Routes the prompt through a linear three-pass spine (prompt, orchestrator, strategic coordinator), balloons out into parallel specialist agents, converges, and gates on quality assurance. Use for any multi-domain or high-leverage request where one pass is not enough.
---

# Agentic Orchestrator

The orchestration spine behind Isaac's stack. Any mention of an agent wakes it.

A single skill answers one question. This skill answers a harder one: *which skills, in what order, argued how, checked by whom.* It does not do the work. It shapes the work, hands it out, pulls it back, and refuses to ship until a quality gate passes.

## Trigger

Fire on any mention of an agent. "Agents." "Activate all agents." "Run the agents on this." "Spin up the orchestrator." The word is the switch. When Isaac says it, this architecture is live for the rest of the request.

The trigger has a hard off-ramp. The word "agents" is often colloquial ("run the agents on the lane" means "do the work," not "instantiate seven steps"). So before anything moves: if the blast radius is two records or fewer, no files get written, and one skill covers it, say "one-skill job, routing to X" and stop. The orchestrator is for work one pass cannot hold. Naming a small ask as small and routing it directly is the correct first move, not a failure to orchestrate.

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

These run in order. Do not skip ahead. Each pass hands a cleaner object to the next. On a small request the off-ramp fires first and the spine collapses to a single frame-and-route move; the full three passes are for work that earns them.

### Pass 1: Prompt

Capture the request raw. Do not clean it up, do not solve it, do not classify it yet. Echo Isaac's own words back so the intent is fixed before anything moves. Typos mean velocity, not carelessness. If the ask is ambiguous, name the ambiguity here rather than guessing it away.

Output of this pass: the request, stated plainly, plus the one or two real unknowns.

### Pass 2: Orchestrator (first pass)

Read the field. Decompose the request into workstreams. Estimate blast radius (how many files, domains, or commitments this touches). Draft a routing table: which skills *could* serve each workstream. Do not commit to deploying anything yet. This pass frames the work; it does not assign it.

Lean on the existing routing tables: `rubinstein-productions-agent` for stage routing, the sub-skills for outreach, proposals, projects, finance, COO strategy. If the request is a raw idea with no home yet, consider `seed-bed` (a toolkit pattern, not an installed skill on every machine; it holds the idea pre-categorical and routes a matured Claim onward to the Arena).

Output of this pass: workstreams, blast radius, a candidate routing table.

### Pass 3: Strategic Coordinator (the digital twin, second pass)

This is the balloon's neck, and it does not re-implement the twin. It calls it.

Invoke the `isaac-twin` skill, task-scoped. It loads the operating model (identity kernel, decision rules, business OS, active loops, voice, routing), reads live state from `~/stack-data/data/`, and pulls Gmail and Calendar via MCP only if the request touches scheduling or correspondence. It applies the real COO guardrails it already carries: Pricing Guardrails (price floors), Capacity Management (Generator-aligned capacity rules), the Sacral check ("hell yes" versus "probably"), and the not-the-buyer-yet tier hold (large $50K-plus org budgets sit in Organizational Embedding, held until one such engagement exists). The Sacral check is somatically non-delegable: the twin marks the decision pending Isaac's own sacral response and never simulates one. If `isaac-twin` cannot boot, stop and say so rather than guessing its judgment.

Then do the orchestration-specific work the twin does not own. Read direction: North (work), East (innocence), South (transition), West (clarity). Commit the deployment plan: *these* agents deploy, in *this* configuration, for *these* reasons. Flag whether any decision is contested enough to deserve a mini-arena. The twin is the only layer allowed to say "none of this is worth doing right now."

Output of this pass: a committed deployment plan, with reasons, and a flag on each contested decision.

## The balloon (non-linear)

### Divergence: jump out

Deploy the committed agents in parallel, one per workstream, each isolated. They do not coordinate mid-flight; isolation is what keeps their outputs genuinely different. Give each one a tight brief and let it work.

Make the balloon conditional on workstream count. With one real workstream there is nothing to isolate, so skip divergence and run spine-only to the gate. Reserve parallel deployment for two or more genuinely independent workstreams. Forcing a single brief into a parallel frame is motion for the look of orchestration, not the thing itself.

### Mini-arenas (first-class, run on any contested call)

On any decision the digital twin flagged as contested or high-leverage, stand up a **mini-arena**: two or three agents arguing opposing positions on the same question. This borrows the Arena pattern, the vault's adversarial-dialogue engine where a tested Claim gets stress-tested, scaled down to a single decision inside a single run. The mini-arena can run directly off Pass 3 even when divergence is skipped. On real requests this is the most load-bearing move in the balloon, not the most skippable one, so reach for it before the rote parts of the spine.

Conflict is the point. The arena exists to keep options distinct instead of collapsing them into a safe average too early. Use tension as a generative force:

- **Seat the positions.** Name the real disagreement, then assign each agent a side worth defending. No straw seats.
- **One exchange minimum.** Each side states its case, then answers the strongest objection from the other. No premature consensus.
- **Diversify, do not blend.** The output of an arena is a *spread* of distinct, defensible options, each with its cost named. Not a merged compromise.
- **Run it by default.** Skip an arena only when a decision is genuinely uncontested. When in doubt, run one. The cost is small and the diversity it produces is the whole reason the balloon exists.

An arena that produces three real options beats a spine that produces one confident answer.

### Convergence: come back together

The digital twin reels the balloon back in. It merges the specialist outputs and resolves each arena's spread into a ranked proposal: the recommended option first, the live alternatives kept (not deleted), the cost of each named. Tensions that did not resolve get surfaced, not buried. This is a synthesis pass, not a vote.

Output: one coherent proposal, ranked, with the alternatives still visible.

## The gate

### Quality assurance (at least one, can loop)

Nothing ships before a QA pass. The gate is adversarial on purpose. Check the converged proposal against:

- **Voice.** No em-dashes, no rule-of-three, no promotional verbs (leverages, empowers, transforms, unlocks), active voice, concrete nouns. For anything shipping under Isaac's name, load `isaac-voice` and `stop-slop`.
- **Grounding.** Every claim traces to stack-data, the canonical doc, or a named source. Flag anything asserted from memory. Any external asset cited in a deliverable gets re-read this session, not paraphrased from a memory note.
- **Constraints.** Price floors held, capacity not breached, the not-the-buyer-yet tier hold respected, blast radius matches what was estimated in Pass 2.
- **Fit.** Does this actually answer the Pass 1 prompt, in Isaac's words, or did it drift?

If the gate fails, loop back to divergence with the failure named. Do not patch around a failed gate. One real loop is cheaper than shipping the wrong thing. When the gate passes, ship and say plainly what shipped.

## How to read Isaac while running this

Co-regulation before content. If he is scattered, hold the spine and slow the balloon. If he is activated, run fewer agents and tighten the gate. If he is in flow, stay out of the way and let the architecture carry it. Draft first, ask second. He prefers being redirected over being interrogated. Refusal is a real answer; through `isaac-twin`, the digital twin is allowed to use it.

## Output discipline

Terse. Concrete names and numbers over abstractions. Show the spine's decisions and the arena's spread; hide the plumbing. Output ceiling 400 words for any single response unless Isaac asks for more. If state conflicts with memory, trust what you observe now.

## Connection to existing skills

- **Calls:** `isaac-twin` (state and judgment, Pass 3 and Convergence), `rubinstein-productions-agent` (routing), `rubinstein-productions-coo` (guardrails), and any sub-skill the digital twin commits to during Pass 3.
- **Composes with:** the vault's Arena pattern (adversarial dialogue), scaled down here into mini-arenas; and `seed-bed` as a conditional pattern for raw ideas with no home yet (it routes a matured Claim onward to the Arena).
- **Does not duplicate:** the digital twin. `isaac-twin` already holds the identity kernel, decision rules, guardrails, voice, and the sacral safeguard. Pass 3 names that skill and passes it the task; it never re-states the twin's charter inline. If anyone re-pastes the charter into Pass 3 to make it self-explanatory, that is the duplication returning.
- **Invoked by:** the `agents` drop-in prompt (`prompts/agents.md`), or any utterance containing "agent".
- **Never:** ships without a QA pass, blends an arena into a single answer, or deploys agents before Pass 3 commits.
