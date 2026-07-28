# Orchestration Model (canonical)

One model for how work moves through the RP system. This reconciles five
artifacts that were each describing a piece of the same machine in a different
vocabulary. Nothing here is new. It names which layer owns what, marks where two
names meant one thing, and retires the duplicates so the system converges instead
of accreting.

## The five that are now one

| Artifact | Was describing | Now lives as |
|---|---|---|
| `agentic-orchestrator` (toolkit PR #3) | the runtime loop | **the spine** (below) |
| `rubinstein-productions-agent` skill | the business orchestrator | the spine's instance for RP operations |
| `idea-to-pilot` (toolkit PR #5) | new-product lifecycle | one of three lifecycles the spine drives |
| SHIP (stack-data PR #29) | record movement through PM | the data layer's movement protocol |
| the lab (stack-data PR #29) | eval loop | the QA gate's measurement engine |
| circle governance (stack-data PR #28) | human consent + AI synthesis | the governance layer |
| PKM 18 prompts (stack-data PR #15), `all-systems-go` | dispatchable runs | the trigger library |

## The spine (runtime)

Every prompt runs the same loop. This is `agentic-orchestrator`, kept as the
canonical runtime.

```
prompt (Isaac's words)
  -> orchestrator        decompose, blast radius, candidate routing
  -> strategic coordinator (digital twin)   reads stack-data ground truth + guardrails, commits which agents deploy
  -> diverge             parallel specialists on tight briefs; mini-arenas for contested calls
  -> converge            one ranked proposal, alternatives kept visible
  -> QA gate (>= 1)      adversarial pass; fail loops back to diverge
  -> ship
```

Names that collapse here:
- The **strategic coordinator** and the **digital twin** are one pass, not two. It
  stays folded into the orchestrator as Pass 3. That answers the open question in
  PR #3: no separate digital-twin skill.
- `rubinstein-productions-agent` is this spine instantiated for the business. It
  is not a different orchestrator. `all-systems-go` is one triggered run of it.
- **diverge** and **converge** are together "the balloon" in `agentic-orchestrator`:
  jump out to parallel agents, jump back to one proposal. Same engine phase, two
  names; the spine keeps diverge and converge.

## The three lifecycles the spine drives

The spine is the same. What is moving differs. There are three things that move,
so three lifecycles, and they share their last move.

### 1. A business record (the money path)
A lead becoming a proposal becoming a project becoming an invoice. The domain
skills already own the stages: `outreach-email-manager`, `project-management-coordinator`, `invoice-financial-tracker`. Underneath, the
record moves through **SHIP**: Submit, Hold, Integrate, Promote. SHIP is the data
layer; the stage skills are the domain layer over it.

### 2. A new product or idea
`idea-to-pilot`: Spec, Shape, Build, Harden, Ship, Graduate. This is the
procedural middle between an approved idea and a shipped pilot. It hands off from
`seed-bed` and graduates to a backend only when paid or shared data is real.

### 3. A prompt or agent getting better
The **lab** loop: define experiment, run candidate, grade, record, aggregate,
detect regression, promote the winner. This is also where the spine's QA gate
gets its teeth. The QA gate calls graders; the graders live in the lab and are
themselves scored.

### The shared last move
SHIP's **Promote**, idea-to-pilot's **Graduate**, and the lab's **promote the
winner** are the same gesture at three layers: a candidate that cleared its gate
becomes the new default, logged. Call it Promote everywhere.

## The gate between stages: Definition of Ready

The lifecycle stages are separated by one gate, the Definition of Ready (the JIRA
sense). A unit is Ready to advance when it is described in one line, has acceptance
criteria, is sized to finish in one work block, has no blocking dependency, and
names its owner. SHIP's **Hold** is the same gate seen from the data side: a record
with no owner and no dated next action is not Ready, it is about to be lost.

## The cross-cutting substrates

These are not lifecycles. They are the ground the spine runs on.

- **Data.** `stack-data` plus the `skill-contracts.yaml` registry. SHIP is how
  records move; the registry says which agent may write what, through which CLI.
- **Eval / QA.** The lab. The spine's QA gate is not vibes; it is a graded run.
- **Governance.** Two parts that were named twice:
  - **COO enforcement** (`rubinstein-productions-coo`): pricing floors and scope
    envelopes gate any Promote toward booked money.
  - **Circle consent** (PR #28): decisions that need human rounds go through
    consent, not a single actor. The "AI synthesis layer" in that PR is the same
    rule as the spine's: the AI reads logbooks and drafts, it never decides or
    acts outward. One rule, stated once: **the system surfaces and drafts; a human
    sends and decides.**
- **Trigger library.** The PKM 18 prompts and `all-systems-go` are saved runs of
  the spine. They are how Isaac starts a loop without writing it from scratch.

## Where the brand decision lands

One hub, two lanes (decided 2026-06-17). Rubinstein Productions is the canonical
hub and `isaac@rubinsteinproductions.com` is the one address. The evaluation
practice stays a visible, distinct lane under the hub, not a second front door and
not erased. The spine routes evaluation-lane work through its own stage skills but
under one identity. The hard wall folds to a lane divider.

## What this retires

- The separate digital-twin skill (folded into Pass 3).
- The second brand front door (one hub, evaluation as a lane).
- Four vocabularies for the QA step. There is one: the lab.
- The belief that Promote needs more than one name. SHIP Promote equals Graduate equals lab-promote. One move.

## What still needs building

Naming the model does not build it. The open work, in order:
1. Wire the spine's QA gate to actually call the lab (today it is a described
   gate; the lab actually runs candidates). One adapter.
2. Add the LLM-judge grader to the lab so the QA gate covers what voice-lint
   cannot. The judge needs its own eval; an ungraded grader is a leak.
3. Put SHIP's `applications` and `projects` Promote moves behind the COO gate in
   code, not just in the registry note.
