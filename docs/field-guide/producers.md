# For Producers

**Status:** operational.

You make film or video, run client engagements, or facilitate recorded sessions. This path covers the session methodology and the shoot and post workflow.

## First hour

1. **[Facilitation Protocol](../../methodology/facilitation-protocol.md)**. The core method: a four-phase workflow that treats the camera as a co-regulatory witness rather than an extraction device, grounded in polyvagal theory. Phase 0 is the facilitator regulating their own nervous system before anyone else arrives. Start there.
2. **[Session Facilitation Guide](../../methodology/session-facilitation-guide.md)**. The tactical companion: the four session types, pre-session and closing protocols, the question bank, interventions for difficult moments, and a five-level skill progression for building the craft.

## Delivery and close

- **Delivery**: the blueprint's four phases define what the client experiences and what ships at each step.
- **Close**: the [Case Study template](../../templates/case-study-template.md) captures the engagement in both measurement registers, with consent tiers recorded per participant. [Consent evidence](../../production/consent-evidence.md) names the one artifact that proves each tier and lists what appearance, location and talent releases must record (draft).

## Shoot and post

- **[Capture standard](../../production/capture-standard.md)**. How every camera shoots: frame rate and shutter, profile and codec, resolution, audio, sync, slate, and card naming. Draft.
- **Delivery**: the [final-approval gate](../../production/resolve-template-spec.md#the-final-approval-gate) sits before any deliverable is queued. The [delivery manifest](../../production/delivery-manifest.md) records what shipped, [platform specs](../../production/platform-specs.md) gives one row per destination class, and [credits and AI disclosure](../../production/credits-and-disclosure.md) says who is named and what is stated about machine-assisted steps. Each of the four carries the status word in its own header, and all four read accepted: the gate is accepted in the Resolve spec, and the manifest, platform specs and credits are accepted in theirs. No tool enforces the gate or writes the manifest. The [return package](../../production/return-package.md) (draft) is the standard for what goes back to the client at the end: originals, finals, captions, the project file and a checksummed manifest.
- **[Resolve Template Spec](../../production/resolve-template-spec.md)** and the **[Resolve MCP server](../../production/resolve-mcp.md)**. A DaVinci Resolve project template and the MCP server that automates it for Claude Code through the Resolve scripting API, with every write planned first and additive only, with four named exceptions (`set_color_management` on a fresh project, `relink` of a clip already offline, and `stabilize` and `reframe_apply` on ` [auto]` timelines only). [resolve_workflow.py](../../production/resolve_workflow.py) keeps the read-only and offline commands and no longer writes to Resolve. Two caveats: these document the author's personal content workflow rather than client delivery, and anything that touches Resolve needs Resolve Studio running, since it drives a live instance.

## The ethics layer

The methodology's distinguishing commitment is anti-extraction: visible consent loops during filming, a decompression window after, reciprocity in what gets returned to the participant, and consent tiers that follow the footage into publication. The [Glossary](../../methodology/glossary.md) defines each term with sources. If you adopt one thing from this path, adopt the consent loop.

## First working session

Take one engagement you already ran, or one you are about to quote. Walk it through the blueprint's four phases: Container, Excavation, Performance, Translation. Write down, for each phase, where the record of it lives today. The step with no answer is the first thing to fix, before you touch the methodology.
