# Who owns what

This repo is the public projection of a larger private stack. It is not a data store and it is not the single source of truth for most things it mentions. When this repo and an owner below disagree, the owner is right and this repo is stale.

Dated 2026-10-15. If a row here is wrong, fix the row; the table is the one place the claim is made.

## The owners

| Domain | Owner | What this repo holds |
|---|---|---|
| Prices and service-tier terms | `rp-intranet` (private), `docs/operations/services.yaml` | `methodology/services.yaml`: the publicly offered tier names, segments and phases, with no prices. |
| Positioning, brand and voice prompts | `rp-intranet` (private) | Nothing. They moved out in August 2026. |
| Pipeline: prospects, contacts, projects, activities, financials | `stack-data` (private), the `data/` entity files | The `rp-*` CLI, which keeps prospect and grant files in a Markdown folder. Which of the two is the record is still undecided; see [ROADMAP](ROADMAP.md), Amendment 2, item 12. |
| Decisions of record | `stack-data`, `docs/decisions/` (one file each since 2026-10-05; the older ledger is frozen) | Nothing. Docs here cite a decision by title and date. |
| Kit source: `phase-zero/` and `public-kit/` | `stack-data`, `kit/phase-zero/` and `kit/public-kit/` | An export, mirrored byte for byte. Never edited here. |
| Methodology canonicals | The private corpus, `00 Canonical/` | Snapshots; see below. |
| Skill files | This repo, `prompts/skills/<name>.md`, one file per skill | The source. Which machine installs which skill is recorded in `stack-data`. |
| Production tooling: Resolve MCP server, `lutlab`, `rpstills`, `vertcut`, the CLI | This repo | The source. Tests and CI are here. |
| Interpreters and packages | This repo, [`requirements.txt`](../requirements.txt) | The source. |

## Methodology: who is authoritative per document

The corpus owns six files, which `scripts/sync-methodology.sh` copies here with the vault markup stripped. Edit them in the corpus; an edit here is overwritten by the next sync.

- `measurement-framework.md`
- `session-facilitation-guide.md`
- `nomadic-indicators-codebook.md`
- `theory-of-change.md`
- `evaluation-framework.md`
- `three-type-architecture.md`

This repo owns these, and a search of the corpus on 2026-10-15 found no live copy of any of them: `facilitation-protocol.md`, `glossary.md`, `methodology-blueprint.md`, `negative-archive-ritual.md`, `orchestration-model.md`, `source-tracking-protocol.md` and `second-brain-v2-opening-and-process.md`. The last one names a corpus path in its frontmatter that does not exist, so that line is a stale pointer and this file is the only copy.

The three `*-plain.md` files are plain-language companions to their canonical document and follow it.

Each document's `**Status:**` line says how far to trust it: operational, draft, aspirational or superseded. `scripts/check-methodology-status.sh` enforces that every one has it.

## What is exported here

Two directories and one deployed folder arrive by export from the private source, never by hand:

- `phase-zero/` and `public-kit/`, produced by the source repo's `sd-kit-export`.
- This repo's own `.claude/`, redeployed by the same step.

Every such change lands as a commit and pull request titled `Export kit from stack-data <sha>`. Any other commit that touches those two directories is a mistake to undo. The deployed copies in other repos are likewise byte-identical output of the installer, so a fix goes to the source and the installer is re-run.

## When two sources disagree

1. The owner in the first table wins.
2. For a methodology document, the Status line says whether to trust the text as written.
3. Prices are never in this repo. A number that looks like a price here is either a leftover to report or an example.
