# Rubinstein Productions Toolkit

**Public methodology, evaluation frameworks, prompt-stack tooling, and deployment kits for AI-assisted solo practices.**

Built and used by [Isaac Rubinstein](https://rubinsteinproductions.com) to run his own facilitation-and-film practice, and published because the kit mechanism — not the business behind it — is the useful, reusable part.

Forks welcome, pull requests unreviewed: this is a solo practitioner's toolkit, so fork it and make it yours rather than waiting on me.

---

## What This Is

A kit, not a company. This repo carries the methodology, the evaluation frameworks, the AI prompt/skill stack, and the deployment mechanism (`phase-zero/`, `public-kit/`) that make a solo AI-assisted practice legible and replicable. It does not carry the practice itself — strategy, pricing internals, brand voice, grant pipeline, and client-facing production tooling live in a private operations repo, `rp-intranet`, and stay there.

The methodology draws on polyvagal co-regulation theory, relational ontology, and critical information ecology. At the practice level it's careful listening and honest expression; here, it's the frameworks and tools that operationalize that.

---

## What's Inside

### `methodology/`
The intellectual core: how the practice works, why it works, and how to measure what it does.

- **Facilitation Protocol**: Nervous-system aware, co-regulatory interview methodology
- **Measurement Framework**: The "Bilingual Dashboard": Royal Metrics (institutional ROI) alongside Nomadic Indicators (relational health and emergence). Formerly ENIF; consolidated April 2026.
- **Nomadic Indicators Codebook**: Qualitative coding guide for tracking transformation
- **Session Facilitation Guide**: Phase-by-phase guide for facilitated documentary sessions
- **Theory of Change**: Logic model for grant-funded community applications (grant-facing, maps methodology to funder evaluation frameworks)
- **Evaluation Framework**: Assessment design using the Bilingual Dashboard

Three core docs carry a plain-language companion for readers with no evaluation background: `evaluation-framework-plain.md`, `theory-of-change-plain.md`, and `enif-plain.md`. The canonical doc stays the source of truth; each companion is the teaching layer and ends with what the framework does not claim.

Licensed CC BY-SA 4.0 (practice-writing, not code — see License below).

### `templates/`
Reusable templates for the client and grant lifecycle.

- Prospect tracking template (YAML frontmatter for Obsidian/Dataview)
- Funder tracking template
- Client onboarding template
- Case study template
- Pipeline dashboards (outreach + grants)

### `docs/`, `architecture/`
Internal documentation and a system-map artifact. `docs/field-guide/` routes readers by role.

The skill-eval harnesses moved to the private `rp-intranet` repo on 2026-08-23: their prompts and fixtures are real RP business scenarios carrying balances, revenue targets, pricing bands, and a client engagement, which is the same material this README says stays private.

### `scripts/`
Generic automation: the doc-link checker, the vault/toolkit boundary drift detector, the pre-commit hook chain, and the methodology sync script. Nothing RP-specific.

### `phase-zero/`
The deployment kit for AI-agent session infrastructure (hooks, operating brief, model routing, retrospective, settings) — deployed byte-identical into consuming repos' `.claude/` directories via `phase-zero/install.sh`. See that file's `CONSUMERS` allowlist for who's on it.

### `public-kit/`
A second, smaller deployment kit for public-repo hygiene: license templates, a README-shape standard, `CONTRIBUTING.md`/`SECURITY.md` templates, and the canonical public-facing voice rules. Deployed independently of `phase-zero/` via `public-kit/install.sh` (or the `--public` mode of the shared installer — see that script) to repos that are actually public. A repo can take either kit, both, or neither.

### `prompts/`
What remains of the prompt stack after the brand/identity/voice prompts moved to `rp-intranet` (they were positioning and business voice, not reusable methodology): agent orchestration notes, a repo atlas, session handoff notes, and `prompts/skills/` — the Cowork/Claude Code skill files covering outreach, project management, agentic development, and idea-to-pilot work.

### `cli/`
Shell scripts for outreach and grant pipeline management. Obsidian-native (markdown + YAML frontmatter). No external dependencies, no API keys, no cost. *(Kept here as a default, not a settled call — see "A judgment call" below.)*

- `rp-prospect`: Create and track prospects (interactive or CLI args)
- `rp-pipeline`: View outreach pipeline by status
- `rp-update`: Update prospect status and log touches
- `rp-draft`: Draft emails and open Gmail compose
- `rp-followup`: Surface overdue follow-ups
- `rp-grant`: Grant funder tracking, deadlines, and status management
- `install.sh`: Add to PATH in one step

By default the tools file everything under an Obsidian vault path. Set `RP_OUTREACH_DIR` to point the whole pipeline anywhere else; directories are created on first use and the bundled `templates/` are used when no vault templates exist.

The tools run on macOS and Linux, every command answers `--help`, and `scripts/cli-smoke-test.sh` exercises the full prospect and grant lifecycle against a throwaway directory (CI runs it on every pull request).

### `production/`
Generic film/video production tooling. *(Also kept as a default — see below.)*

- DaVinci Resolve workflow script
- PowerGrade template spec
- iPhone filming guide for the camera-shipping model

### `research/`
What's left after the position papers and grant-concept research moved to `stack-data`: the 64-registers thesis. (The Digital Liver offering doc is business strategy with revenue models, so it lives in the private `rp-intranet` repo.)

### Apps (external repos)
Web-based tools, each developed in its own repository. There is no `apps/` directory in this tree.

- **[Alchemy](https://github.com/risaac09/alchemy)**: Digital Liver app. Capture → Reflect → Release. The lineage that became **The Metabolizer**, the vault product.
- **Royal Metrics**: ENIF business performance dashboard (lives at `three-type-evaluation/instrument/` since 2026-07-20, standalone repo archived; the measurement framework it implements is in `methodology/`)
- **RP Lifecycle**: Videography project lifecycle manager (repo archived 2026-07; the 10-phase template it encoded lives on in stack-data's project schema)

---

## What's Not Here (and why)

This repo's own `CLAUDE.md` used to claim "nothing private lands here." An audit in August 2026 found that wasn't true, and this reorganization is the fix:

- **Grant strategy, brand/creative-strategy prompts, the identity-instantiation prompt, the content strategy, and RP-specific production tooling** (`badwords-rp/`) moved to `rp-intranet`, a private operations repo. They were business strategy and voice/positioning material, not reusable methodology.
- **Position papers and grant-concept research** moved to `stack-data`, alongside near-identical or adjacent material already living there.
- **Duplicate Total Cost of Ownership methodology files** were deleted outright; `stack-data/context/tco-framework.md` is the canonical version now.
- **Context Provenance** — the self-contained AI-attribution and provenance tool that happened to live in this tree now has its own repository: **[`risaac09/context-provenance`](https://github.com/risaac09/context-provenance)**. Its original subtree history moved with it.

A second pass on 2026-08-23 caught what the first one missed. The August reorg scoped itself to `prompts/`, `research/`, `grants/`, and `production/`, so these were never in range:

- **The skill-eval harnesses** (`evals/`) moved to `rp-intranet`. Their prompts read as synthetic but are real business scenarios: account balances, rent, monthly burn, a quarterly revenue target, a named client engagement, and the price-anchoring script with the floor and ceiling stated outright.
- **Service-tier pricing, revenue projections, unit economics, and the go-to-market plan** moved to `rp-intranet`. The projection table sat directly beneath an assumptions block naming the employer.
- **The Digital Liver offering doc and the RP COO skill** moved to `rp-intranet`. Both are business strategy carrying rates and financial models.
- **A verbatim snapshot of one machine's Claude permission allow list** was removed. Publishing it published the exact set of commands that run with no approval prompt on that machine. The audit script reads a machine-local baseline now.
- **The autoMode environment block** shipped generic. It had named every private repo and then described the privacy scanner's known blind spot, which is a target list sitting next to the gap that reaches it. Machine specifics come from a local overlay the installer merges last.

History still holds all of it. Scrubbing that is a separate decision.

### A judgment call

`cli/` and most of `production/` (everything except `badwords-rp/`, which moved) are kept here by default, not because that's a settled decision. The toolkit's own content strategy argues for keeping tooling visible as proof a solo practitioner can build real infrastructure, and neither directory carries names, numbers, or strategy — but this default hasn't been reviewed against the same bar as the rest of the reorg. Flagging it explicitly so it can be overridden.

---

## License

Three lanes, matching the rest of this practice's repos:

- **Code and tooling** (`cli/`, `scripts/`, `production/resolve_workflow.py`, the `phase-zero/` and `public-kit/` mechanisms themselves) — **MIT**. Use it, fork it, adapt it.
- **Methodology, protocols, and practice-writing** (`methodology/`, `templates/`, `docs/`) — **CC BY-SA 4.0**. Share and adapt, with attribution, alike.
- **Data** — none of consequence lives in this repo; where it would, it's CC0.

See `LICENSE` for the full texts and scope note, and `public-kit/` for the templates this policy is built from.

---

## Quick Start

New here? The **[Field Guide](docs/field-guide/index.md)** routes writers, producers, and evaluators to a reading order and a first working session.

### Outreach + Grant CLI
```bash
# Clone the repo
git clone https://github.com/risaac09/rubinstein-productions-toolkit.git

# Add CLI tools to your PATH
echo 'export PATH="$HOME/rubinstein-productions-toolkit/cli:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Optional: choose where pipeline files live (defaults to an Obsidian vault path)
echo 'export RP_OUTREACH_DIR="$HOME/rp-pipeline"' >> ~/.zshrc

# Create your first prospect
rp-prospect

# View your pipeline
rp-pipeline

# Manage grant funders
rp-grant help
```
### Prompt Stack
The `prompts/skills/` directory contains `.skill` and `.md` files designed for [Claude Code](https://claude.ai/claude-code) / Cowork. Drop them into your `.claude/skills/` directory or adapt the methodology for your own AI workflow.

### Methodology
Start with `methodology/facilitation-protocol.md` for the session-level practice, then `methodology/measurement-framework.md` for the measurement system.

---

## Who This Is For

The **[Field Guide](docs/field-guide/index.md)** gives each audience a reading order and a first working session:

- **[Writers](docs/field-guide/writers.md)**: the voice system, the question bank that generates client language, the editorial disciplines
- **[Producers](docs/field-guide/producers.md)**: the session methodology, the client lifecycle from prospect to case study, shoot and post workflow
- **[Evaluators](docs/field-guide/evaluators.md)**: the Bilingual Dashboard, the coding manual, the grant pipeline

If none of those labels fit, you may still be at home here:

- Solo consultants and facilitators who want to see how someone else built their practice infrastructure
- Nonprofit professionals looking for evaluation frameworks that measure relational quality alongside deliverables
- Documentary filmmakers curious about participant-led production models
- Anyone building an AI-assisted practice stack who wants to see how prompt engineering connects to methodology, or who wants a public-repo hygiene kit (`public-kit/`) they can deploy to their own projects

---

## Canonical structure

This repo and the Obsidian vault are separate stacks. Directory names like `02 Practice/`, `03 Projects/`, `00 System/` are reserved for the vault and must never appear inside the toolkit root or anywhere below it (except under `_archive/`). The full forbidden list, the regex, and the rationale are in `docs/CANONICAL-STRUCTURE.md`.

Three guards enforce this:

- **Drift detector** — `scripts/check-vault-mirror-drift.sh`. Pure bash. Run anytime.
- **Pre-commit hook** — `scripts/hooks/pre-commit-vault-mirror-check.sh`. Chains the secret scanner and the drift detector. Install on a clean checkout with `cp scripts/hooks/pre-commit-vault-mirror-check.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.
- **Daily detector** — `~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist`. Runs at 04:30 daily, writes a dated note to the vault on drift. The pre-commit hook only fires on git commits; the daily run catches misroutes from other automation.

---

*Isaac Rubinstein, [Rubinstein Productions](https://rubinsteinproductions.com)*
*Seattle, WA*
