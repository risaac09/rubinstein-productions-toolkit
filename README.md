# Rubinstein Productions Toolkit

**An open-source stack for anti-extractive consultancy — methodology, evaluation, outreach automation, prompt engineering, and film production tools.**

Built by [Isaac Rubinstein](https://rubinsteinproductions.com) for solo practitioners and small teams doing mission-driven facilitation, documentary, and social impact work.

---

## What This Is

This is the complete operational infrastructure behind Rubinstein Productions — a facilitation and film practice that helps mission-driven professionals say what's true about their work.

The methodology is grounded in relational ontology, polyvagal co-regulation theory, and critical information ecology. The tools are built for a solo practitioner who cares about the work more than the scale.

**The core belief:** Systems optimize expression until the honest version is too expensive to say. The work is to create conditions where honesty becomes possible again — between people, not inside them.

**The core mechanism:** A camera ships to the participant. They film themselves. Everything is returned. Nothing is extracted.

---

## What's Inside
### `methodology/`
The intellectual core. How the practice works, why it works, and how to measure what it does.

- **Facilitation Protocol** — Nervous-system aware, co-regulatory interview methodology
- **Emergent Narrative Impact Framework (ENIF)** — Bilingual measurement: Royal Metrics (institutional ROI) + Nomadic Indicators (relational health and emergence)
- **Nomadic Indicators Codebook** — Qualitative coding guide for tracking transformation
- **Session Facilitation Guide** — Phase-by-phase guide for facilitated documentary sessions
- **Theory of Change** — Logic model for grant-funded community applications (Say Why)
- **Evaluation Framework** — Grant-legible assessment design using the Bilingual Dashboard

### `prompts/`
The prompt stack — AI skill files that encode the methodology, brand voice, and operational logic.

- **15+ Cowork/Claude Code skills** covering facilitation, outreach, proposals, financial tracking, psychotherapeutic coaching, content publishing, and more
- **Brand context** — Complete positioning, voice guidelines, and creative constraints
- **Instantiation prompt** — The system prompt that bootstraps the full practice

### `cli/`
Shell scripts for outreach and grant pipeline management. Obsidian-native (markdown + YAML frontmatter). No external dependencies.

- `rp-prospect` — Create and track prospects (interactive or CLI args)
- `rp-pipeline` — View outreach pipeline by status
- `rp-update` — Update prospect status and log touches
- `rp-draft` — Draft emails and open Gmail compose
- `rp-followup` — Surface overdue follow-ups
- `rp-grant` — Grant funder tracking, deadlines, and status management
- `install.sh` — Add to PATH in one step
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

### `apps/`
Web-based tools (links to standalone repos).

- **[Alchemy](https://github.com/risaac09/alchemy)** — Digital Liver app. Capture → Reflect → Release.
- **[Royal Metrics](https://github.com/risaac09/royal-metrics)** — ENIF business performance dashboard
- **[RP Lifecycle](https://github.com/risaac09/rp-lifecycle)** — Videography project lifecycle manager

### `production/`
Film and video production tools.

- DaVinci Resolve workflow script
- Color grading powergrades
- iPhone filming guide for the camera-shipping model

### `research/`
Published frameworks and position papers.

- Digital Liver framework
- Fractal Enclosure framework
- Say Why grant concept

---

## Philosophy
This toolkit exists because the infrastructure for honest, relational, anti-extractive work shouldn't be proprietary. The methodology, the measurement frameworks, the outreach tools, the prompt engineering — all of it was built to solve a specific problem: how does a solo practitioner do deep facilitation and film work without the operational overhead crushing the relational quality?

The answer turned out to be: encode the philosophy into the tools. The CLI scripts have a sacral check built in. The evaluation framework measures relational quality alongside deliverables. The prompt stack carries the brand voice and creative constraints so every AI-assisted output sounds like the practice, not like a consultant.

Take what's useful. Adapt it. If you build something with it, the only ask is that you keep the anti-extractive principle: return more than you take.

---

## Quick Start

### Outreach + Grant CLI
```bash
# Clone the repo
git clone https://github.com/risaac09/rubinstein-productions-toolkit.git

# Add CLI tools to your PATH
echo 'export PATH="$HOME/rubinstein-productions-toolkit/cli:$PATH"' >> ~/.zshrc
source ~/.zshrc

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

- Solo consultants and facilitators doing relational, mission-driven work
- Nonprofit professionals building evaluation frameworks that don't flatten what matters
- Documentary filmmakers interested in anti-extractive production models
- Anyone building an AI-assisted practice stack who wants to see how the prompt engineering connects to the methodology

---

## License

MIT License. Use it, fork it, adapt it. Keep the spirit.

---

*Isaac Rubinstein — [Rubinstein Productions](https://rubinsteinproductions.com)*
*Seattle, WA*