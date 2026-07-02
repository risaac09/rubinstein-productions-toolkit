# Rubinstein Productions Toolkit

**The complete operational stack behind a solo facilitation and film practice: methodology, evaluation, outreach automation, prompt engineering, and production tools.**

Built by [Isaac Rubinstein](https://rubinsteinproductions.com) for use in an actual practice. Take what's useful.

Forks welcome, pull requests unreviewed: this is a solo practice's toolkit, so fork it and make it yours rather than waiting on me.

---

## What This Is

This is everything I use to run Rubinstein Productions, a facilitation and film practice that helps mission-driven professionals say what's true about their work.

I got obsessed with building the infrastructure behind my own practice: the session methodology, the measurement frameworks, the AI prompt stack, the CLI tools for managing pipelines. All the stuff that usually lives in someone's head or scattered across a dozen folders. I built it because I enjoy building it. Keeping it private seemed wasteful.

**How it works:** I facilitate. I ship a camera to the participant. They film themselves. Everything is returned. The methodology draws on polyvagal co-regulation theory, relational ontology, and critical information ecology. At the practice level, it's careful listening and honest expression.

---

## Services

Two engagement tiers, as priced at [rubinsteinproductions.com](https://rubinsteinproductions.com):

- **Founder Story**: Say what you've become. Facilitated session, filmed, delivered as a short piece with a written narrative. ($1,500–2,500)
- **Program Engagement**: The full arc across a program or team. Facilitation, film, and the Bilingual Dashboard. ($3,000–8,000)

A third tier for sustained organizational embedding exists and opens by conversation, not by menu.

---

## What's Inside

### `methodology/`
The intellectual core: how the practice works, why it works, and how to measure what it does.

- **Facilitation Protocol**: Nervous-system aware, co-regulatory interview methodology
- **Emergent Narrative Impact Framework (ENIF)**: What I call the "Bilingual Dashboard": Royal Metrics (institutional ROI) alongside Nomadic Indicators (relational health and emergence)
- **Nomadic Indicators Codebook**: Qualitative coding guide for tracking transformation
- **Session Facilitation Guide**: Phase-by-phase guide for facilitated documentary sessions
- **Theory of Change**: Logic model for grant-funded community applications (grant-facing, maps methodology to funder evaluation frameworks)
- **Evaluation Framework**: Assessment design using the Bilingual Dashboard

### `prompts/`
The prompt stack: AI skill files that encode the methodology, brand voice, and operational logic.

- **15+ Cowork/Claude Code skills** covering facilitation, outreach, proposals, financial tracking, coaching, content publishing, and more
- **Brand context**: Complete positioning, voice guidelines, and creative constraints
- **Instantiation prompt**: The system prompt that bootstraps the full practice

### `cli/`
Shell scripts for outreach and grant pipeline management. Obsidian-native (markdown + YAML frontmatter). No external dependencies, no API keys, no cost.

- `rp-prospect`: Create and track prospects (interactive or CLI args)
- `rp-pipeline`: View outreach pipeline by status
- `rp-update`: Update prospect status and log touches
- `rp-draft`: Draft emails and open Gmail compose
- `rp-followup`: Surface overdue follow-ups
- `rp-grant`: Grant funder tracking, deadlines, and status management
- `install.sh`: Add to PATH in one step

By default the tools file everything under an Obsidian vault path. Set `RP_OUTREACH_DIR` to point the whole pipeline anywhere else; directories are created on first use and the bundled `templates/` are used when no vault templates exist.

The tools run on macOS and Linux, every command answers `--help`, and `scripts/cli-smoke-test.sh` exercises the full prospect and grant lifecycle against a throwaway directory (CI runs it on every pull request).
### `templates/`
Reusable templates for the full client and grant lifecycle.

- Prospect tracking template (YAML frontmatter for Obsidian/Dataview)
- Funder tracking template
- Client onboarding template
- Case study template
- Pipeline dashboards (outreach + grants)

### `grants/`
Grant infrastructure for the Say Why social impact initiative.

- Theory of Change
- Evaluation Framework
- Fiscal Sponsorship strategy
- Funder research templates

### Apps (external repos)
Web-based tools, each developed in its own repository. There is no `apps/` directory in this tree.

- **[Alchemy](https://github.com/risaac09/alchemy)**: Digital Liver app. Capture → Reflect → Release. The lineage that became **The Metabolizer**, the vault product.
- **Royal Metrics**: ENIF business performance dashboard (private repo; the measurement framework it implements is in `methodology/`)
- **RP Lifecycle**: Videography project lifecycle manager (repo archived 2026-07; the 10-phase template it encoded lives on in stack-data's project schema)

### `production/`
Film and video production tools.

- DaVinci Resolve workflow script
- Color grading powergrades
- iPhone filming guide for the camera-shipping model
### `research/`
Frameworks and position papers.

- Dual-architecture and frontier-lab-blueprint position papers
- Say Why grant concept and the 64-registers thesis
- rp-shared foundation spec, profile consolidation analysis, evaluation-forward hub framing

(The Digital Liver offering doc lives in `methodology/digital-liver-offering.md`, not here.)

---

## Why Open Source

I built this because I enjoy building it. The infrastructure is as interesting to me as the client work, maybe more so. The question of how a solo practitioner can encode their entire methodology into replicable tools felt worth answering publicly.

There's no product here. No platform, no SaaS, no waitlist. Just how I actually work: the real prompts, the real evaluation frameworks, the real CLI tools I run every week. If any of it is useful to your practice, take it.

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
Start with `methodology/facilitation-protocol.md` for the session-level practice, then `methodology/enif.md` for the measurement system.

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
- Anyone building an AI-assisted practice stack who wants to see how prompt engineering connects to methodology

---

## Canonical structure

This repo and the Obsidian vault are separate stacks. Directory names like `02 Practice/`, `03 Projects/`, `00 System/` are reserved for the vault and must never appear inside the toolkit root or anywhere below it (except under `_archive/`). The full forbidden list, the regex, and the rationale are in `docs/CANONICAL-STRUCTURE.md`.

Three guards enforce this:

- **Drift detector** — `scripts/check-vault-mirror-drift.sh`. Pure bash. Run anytime.
- **Pre-commit hook** — `scripts/hooks/pre-commit-vault-mirror-check.sh`. Chains the secret scanner and the drift detector. Install on a clean checkout with `cp scripts/hooks/pre-commit-vault-mirror-check.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.
- **Daily detector** — `~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist`. Runs at 04:30 daily, writes a dated note to the vault on drift. The pre-commit hook only fires on git commits; the daily run catches misroutes from other automation.

---

## License

MIT License. Use it, fork it, adapt it.

---

*Isaac Rubinstein, [Rubinstein Productions](https://rubinsteinproductions.com)*
*Seattle, WA*