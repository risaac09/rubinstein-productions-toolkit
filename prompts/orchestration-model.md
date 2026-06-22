# The orchestration model, explained: one system, six layers

> **Canonical spec:** [`methodology/orchestration-model.md`](../methodology/orchestration-model.md). This is the plain-language explainer of that model, cut as six layers. The spec is the source of truth for the brand decision, what the model retires, and the open build work; this is the teaching layer.

Five orchestration vocabularies grew in parallel across stack-data and this toolkit. Left alone they read as five competing frameworks. They are not competing. They operate at different altitudes. This document explains the canonical model in `methodology/orchestration-model.md` by stacking those vocabularies rather than merging them. Nothing here invents a new vocabulary; it places the ones that exist.

## The five that accreted

- `agentic-orchestrator` (toolkit skill): how a single request runs.
- `idea-to-pilot` (toolkit skill): how an idea matures across many runs.
- SHIP (stack-data `context/ship-protocol.md`): how the record of work moves through project management.
- the lab (stack-data `lab/`): how an agent's output gets graded.
- circle-governance (stack-data `context/circle-governance*`): how a decision gets made when more than one human holds it.
- plus the catalog: the 18 PKM orchestration prompts (`context/pkm-system-design.md`), the menu of dispatchable work.

## The stack

```
 CATALOG     18 PKM prompts            what work can be dispatched (the menu)
 LIFECYCLE   idea-to-pilot             how an idea matures: Spec, Shape, Build,
                                       Harden, Ship, Graduate (the macro arc)
 RUN         agentic-orchestrator      how one request runs: prompt, orchestrator,
                                       twin, diverge, converge, QA, ship (the engine)
 EVAL        the lab                   how output is graded (voice-draft, leaderboard)
 LEDGER      SHIP                      how the record moves: Submit, Hold,
                                       Integrate, Promote (the data discipline)
 GOVERNANCE  circle-governance         how a multi-party decision is made (consent)
```

Read it top to bottom. The catalog says what can be done. The lifecycle says where an idea is in its arc. The run is the engine that executes one step of that arc. The eval grades what the run produced. The ledger records the move so it does not fall on the floor. Governance is the separate layer that applies only when a decision belongs to more than one person.

## How a unit of work travels the stack

1. A prompt enters from the catalog, or as a raw ask.
2. The lifecycle places it: is this Spec, Build, or Ship.
3. The run (`agentic-orchestrator`) executes that step: Pass 0 sync, spine, balloon, QA gate.
4. The eval (the lab) grades the output where a grader exists (voice first).
5. The ledger (SHIP) records the move: a record at Hold has an owner and a dated next action, an Integrated record links into the SSOT, a Promoted record advances toward a terminal decision.
6. Governance enters only if the decision is not Isaac's alone.

## The one gate between layers: Definition of Ready

The lifecycle stages are separated by a single gate, the Definition of Ready (the JIRA sense). A unit is Ready to move from Refined to In Progress when: it is described in one line, it has acceptance criteria, it is sized to finish in one work block, it has no blocking dependency, and it names its owner. The readiness board applies this gate to every open item. SHIP's Hold is the same gate seen from the data side: a record with no owner and no next action is not Ready, it is about to be lost.

## What this resolves

- No design is discarded. Each keeps its job and loses its claim to be the whole.
- `agentic-orchestrator` is the run-time engine, not the lifecycle and not the ledger.
- SHIP is the ledger discipline, not a second orchestrator.
- The lab is the measurement under all of it, not a separate program.
- circle-governance is the multi-party decision layer, dormant until a decision is shared.
- The 18 prompts are the catalog the lifecycle draws from.

## Where this lives

The canonical spec (`methodology/orchestration-model.md`) lives in the toolkit because it reconciles toolkit skills, and this explainer sits beside it. The data layers it names (SHIP, the lab, the registry) stay in stack-data. The freshness gate (Pass 0 of `agentic-orchestrator`) is what keeps any run over this stack reading current state rather than a stale clone.
