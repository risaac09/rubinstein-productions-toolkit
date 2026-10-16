# For Producers

You make film or video, run client engagements, or facilitate recorded sessions. This path covers the session methodology, the client lifecycle from prospect to case study, and the shoot and post workflow.

## First hour

1. **[Facilitation Protocol](../../methodology/facilitation-protocol.md)**. The core method: a four-phase workflow that treats the camera as a co-regulatory witness rather than an extraction device, grounded in polyvagal theory. Phase 0 is the facilitator regulating their own nervous system before anyone else arrives. Start there.
2. **[Session Facilitation Guide](../../methodology/session-facilitation-guide.md)**. The tactical companion: the four session types, pre-session and closing protocols, the question bank, interventions for difficult moments, and a five-level skill progression for building the craft.

## The client lifecycle

The engagement has a defined shape from first contact to published case study.

- **Prospecting**: a prospect moves through research, ready, contacted, and responded. This repo carries no tracker for it: in the practice that built this toolkit the record is a contact entry in a private data repo, and a stranger's equivalent is whatever contact list they already trust. [`TRUTH-OWNERSHIP.md`](../TRUTH-OWNERSHIP.md) says where each fact lives. A command-line tracker used to ship here; it was retired on 2026-10-16 and is in git history.
- **Intake**: the [Client Onboarding template](../../templates/client-onboarding.md) structures a recorded discovery session before any proposal: current narrative state, organizational context, readiness, and an honest fit check, and records the consent tier offered. The [Scope Checklist](../../templates/scope-checklist.md) lists every field other documents assign to the signed scope. Note it uses legacy tier names; the current ones are in the [Methodology Blueprint](../../methodology/methodology-blueprint.md).
- **Delivery**: the blueprint's four phases define what the client experiences and what ships at each step, tier by tier.
- **Close**: the [Case Study template](../../templates/case-study-template.md) captures the engagement in both measurement registers, with consent tiers recorded per participant. [Consent evidence](../../production/consent-evidence.md) names the one artifact that proves each tier and lists what appearance, location and talent releases must record (draft).

## Shoot and post

- **[Capture standard](../../production/capture-standard.md)**. How every camera shoots: frame rate and shutter, profile and codec, resolution, audio, sync, slate, and card naming. Draft. A worked one-day shoot plan at iPhone level sits beside it as an [example](../../production/examples/2026-03-31-shoot-plan.md); its camera-packing sequence is the signature visual of the camera-shipping model.
- **[Camera-shipping logistics](../../production/camera-shipping-logistics.md)**. The draft standard for sending the camera to a client and getting it back: lifecycle in relative steps, kit and backup-gear checklist, return, insurance as a list of questions, and damage and loss handling.
- **[Client shooting brief](../../production/client-shooting-brief.md)**. A two-page brief written to the client: what to film, how to set up, a footage checklist and how the cards come back. Print it and put it in the box.
- **Delivery**: the [final-approval gate](../../production/resolve-template-spec.md#the-final-approval-gate) sits before any deliverable is queued. The [delivery manifest](../../production/delivery-manifest.md) records what shipped, [platform specs](../../production/platform-specs.md) gives one row per destination class, and [credits and AI disclosure](../../production/credits-and-disclosure.md) says who is named and what is stated about machine-assisted steps. All four are accepted. The [return package](../../production/return-package.md) is the standard for what goes back to the client at the end: originals, finals, captions, the project file and a checksummed manifest. [Design tokens](../../production/design-tokens.md) names the colours and type a deliverable refers to, with the lower-third geometry and the caption standard.
- **[Resolve Template Spec](../../production/resolve-template-spec.md)** and the **[Resolve MCP server](../../production/resolve-mcp.md)**. A DaVinci Resolve project template and the MCP server that automates it for Claude Code through the Resolve scripting API, with every write planned first and additive only (the one exception re-points a clip that is already offline at a file verified as the same take). [resolve_workflow.py](../../production/resolve_workflow.py) keeps the read-only and offline commands and no longer writes to Resolve. Two caveats: these document the author's personal content workflow rather than client delivery, and anything that touches Resolve needs Resolve Studio running, since it drives a live instance.

## The ethics layer

The methodology's distinguishing commitment is anti-extraction: visible consent loops during filming, a decompression window after, reciprocity in what gets returned to the participant, and consent tiers that follow the footage into publication. The [Glossary](../../methodology/glossary.md) defines each term with sources. If you adopt one thing from this path, adopt the consent loop.

## First working session

Take one engagement you already ran, or one you are about to quote. Walk it through the four lifecycle steps above: first contact, intake, delivery, close. Write down, for each step, where the record of it lives today. The step with no answer is the first thing to fix, before you touch the methodology.
