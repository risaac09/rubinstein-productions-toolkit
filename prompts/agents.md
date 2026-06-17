# Agents — Orchestrator Trigger

Drop-in prompt. Says the word that wakes the orchestration spine.
Activates the full architecture, runs it end to end, gates on quality before shipping.

---

Activate the `agentic-orchestrator` skill. Run this request through the full spine.

**Spine (linear, in order):**
1. **Prompt.** Echo my request back raw, in my words. Name the one or two real unknowns. Do not solve yet.
2. **Orchestrator.** Decompose into workstreams. Estimate blast radius. Draft a candidate routing table from the existing skills. Do not deploy yet.
3. **Strategic coordinator (the digital twin).** Read `~/stack-data/data/` for ground truth. Pull Gmail/Calendar via MCP only if the request touches them. Apply COO guardrails. Commit: which agents deploy, in what configuration, why. Flag every contested decision.

**Balloon (jump out, then come back):**
4. **Diverge.** Deploy the committed agents in parallel, each on a tight brief.
5. **Mini-arenas (recommended).** For each contested decision, stand up two or three agents arguing opposing sides. One exchange minimum. Output a spread of distinct options with costs named, not a blend. Skip only when a decision is genuinely uncontested.
6. **Converge.** Reel it back in. Merge into one ranked proposal: recommended first, alternatives kept visible, cost of each named.

**Gate:**
7. **QA (at least one).** Check voice, grounding, constraints, and fit against the Pass 1 prompt. If it fails, loop back to step 4 with the failure named. Ship only on pass.

Constraints: terse, concrete names and numbers, 400-word ceiling unless I ask for more. Don't send, draft externally, or modify anything without surfacing it first. If state conflicts with memory, trust what you observe now.
