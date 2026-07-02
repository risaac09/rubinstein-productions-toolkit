# For Evaluators

You measure impact, design assessments, or write and manage grants. This path maps the measurement corpus, which overlaps in ways the filenames do not explain, and then covers the working tools.

## The map, before the reading

Four documents share the same core idea and serve different jobs. The core idea is the Bilingual Dashboard: every engagement is measured in two registers at once. One register speaks to institutions (outputs, reach, financial accountability, satisfaction scores). The other tracks relational and emergent change (consistency between stated values and observed behavior, conversational texture, fear surfacing and transforming, somatic markers).

| Document | Job | Use it when |
|---|---|---|
| [RP Measurement Framework](../../methodology/measurement-framework.md) | The operational framework for a running practice: both registers, reporting cadence, data safeguards | You are measuring ongoing client work |
| [Evaluation Framework](../../methodology/evaluation-framework.md) | Assessment design for grant-funded cohorts, written to be funder-legible | You are designing evaluation for a funded program |
| [Nomadic Indicators Codebook](../../methodology/nomadic-indicators-codebook.md) | The coding manual: per-code definitions, examples, and markers across six domains | You are coding transcripts, recordings, or field notes |
| [Theory of Change](../../methodology/theory-of-change.md) | The logic model connecting the methodology to public-health outcomes | You are writing the grant narrative |

One note on provenance: the codebook is the canonical coding reference. The framework document points to it rather than carrying its own copy of the codes, and the `grants/` directory holds pointer stubs to the `methodology/` versions of the theory of change and evaluation framework.

## First hour

Read the [RP Measurement Framework](../../methodology/measurement-framework.md) end to end. It is the consolidation point: the institutional register, the relational register, the monthly synthesis ritual, and the consent tiers that govern what data can be collected and published. Then open the [Codebook](../../methodology/nomadic-indicators-codebook.md) beside a real transcript and try coding one page. The codes are learnable in a sitting; reliability comes with practice.

## Working with the data

- **Consent tiers.** Three tiers govern every piece of participant data, set at intake and recorded in the [Case Study template](../../templates/case-study-template.md). Publication is governed by the tier the participant chose.
- **[Source Tracking Protocol](../../methodology/source-tracking-protocol.md)**. Provenance categories and claim-level tracing for anything externally cited. Grant reports that survive scrutiny are built this way.
- **Reporting cadence.** The frameworks specify who hears what and when: funders quarterly in their register, participants after engagement in theirs, the field annually.

## The grant pipeline

`rp-grant` tracks funders as markdown files with YAML frontmatter: `add` for intake, `list` by status, `deadlines` sorted by date, `update` and `log` as the relationship moves. Statuses run research, loi-prep, submitted, reviewed, awarded or declined. Run `rp-grant --help` for the full command set, and see the [Funder template](../../templates/funder-template.md) for the fields.

## First working session

Take one engagement you already ran, under any methodology. Fill in the Royal Metrics and Nomadic Indicators sections of the [Case Study template](../../templates/case-study-template.md) from memory and existing records. The gaps you cannot fill are your data-collection plan for the next engagement.

## What to adapt rather than adopt

The specific codes and benchmarks assume facilitated documentary sessions. The transferable layer is the two-register structure itself: report institutional legibility and relational change side by side, and refuse to collapse either into the other.
