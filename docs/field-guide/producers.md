# For Producers

You make film or video, run client engagements, or facilitate recorded sessions. This path covers the session methodology, the client lifecycle from prospect to case study, and the shoot and post workflow.

## First hour

1. **[Facilitation Protocol](../../methodology/facilitation-protocol.md)**. The core method: a four-phase workflow that treats the camera as a co-regulatory witness rather than an extraction device, grounded in polyvagal theory. Phase 0 is the facilitator regulating their own nervous system before anyone else arrives. Start there.
2. **[Session Facilitation Guide](../../methodology/session-facilitation-guide.md)**. The tactical companion: the four session types, pre-session and closing protocols, the question bank, interventions for difficult moments, and a five-level skill progression for building the craft.

## The client lifecycle

The engagement has a defined shape from first contact to published case study.

- **Prospecting**: the [CLI tools](../../cli/) move prospects through research, ready, contacted, and responded states. `rp-prospect` creates the file, `rp-update` logs each touch, `rp-followup` surfaces what is due. Every tool answers `--help`. Set `RP_OUTREACH_DIR` and the whole pipeline lives wherever you point it.
- **Intake**: the [Client Onboarding template](../../templates/client-onboarding.md) structures a recorded discovery session before any proposal: current narrative state, organizational context, readiness, and an honest fit check. Note it uses legacy tier names; the current ones are in the [Methodology Blueprint](../../methodology/methodology-blueprint.md).
- **Delivery**: the blueprint's four phases define what the client experiences and what ships at each step, tier by tier.
- **Close**: the [Case Study template](../../templates/case-study-template.md) captures the engagement in both measurement registers, with consent tiers recorded per participant.

## Shoot and post

- **[Filming Guide](../../production/filming-guide.md)**. A concrete one-day shoot plan at iPhone level: setup, shot lists, and time budget for two short pieces. The camera-packing sequence is the signature visual of the camera-shipping model, where the participant films themselves and everything is returned.
- **[Resolve Template Spec](../../production/resolve-template-spec.md)** and **[resolve_workflow.py](../../production/resolve_workflow.py)**. A DaVinci Resolve project template and a CLI that automates it through the Resolve scripting API. Two caveats: these document the author's personal content workflow rather than client delivery, and the script needs Resolve Studio running, since it drives a live instance.

## The ethics layer

The methodology's distinguishing commitment is anti-extraction: visible consent loops during filming, a decompression window after, reciprocity in what gets returned to the participant, and consent tiers that follow the footage into publication. The [Glossary](../../methodology/glossary.md) defines each term with sources. If you adopt one thing from this path, adopt the consent loop.

## First working session

Install the CLI (`cli/install.sh`), set `RP_OUTREACH_DIR` to a scratch directory, and run one prospect through the full cycle: create, update to contacted, draft, follow up. Twenty minutes, and you will know whether the pipeline fits your practice before you touch the methodology.
