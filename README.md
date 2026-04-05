# Rubinstein Productions Toolkit

**The complete operational stack behind a solo facilitation and film practice — methodology, evaluation, outreach automation, prompt engineering, and production tools.**

Built by [Isaac Rubinstein](https://rubinsteinproductions.com). I built this for myself. It works. Take what's useful.

---

## What This Is

This is everything I use to run Rubinstein Productions — a facilitation and film practice that helps mission-driven professionals say what's true about their work.

I got obsessed with building the infrastructure behind my own practice: the session methodology, the measurement frameworks, the AI prompt stack, the CLI tools for managing pipelines. All the stuff that usually lives in someone's head or scattered across a dozen folders. I built it because I enjoy building it, and I figured — why keep it proprietary?

**How it works:** I facilitate. I ship a camera to the participant. They film themselves. Everything is returned. The methodology draws on polyvagal co-regulation theory, relational ontology, and critical information ecology — but at the practice level, it's just careful listening and honest expression.

---

## Services

Three engagement levels, each building on the last:

- **The Mirror** — A single facilitation session. Listen, reflect, deliver a written narrative. ($500–1,500)
- **The Map** — The full "Say The Thing" package. Facilitation, film, Bilingual Dashboard. ($5,000–12,000)
- **The Territory** — Ongoing creative partnership. Monthly retainer for organizations doing sustained narrative work. ($4,000–8,000/month)

---

## What's Inside

### `methodology/`
The intellectual core. How the practice works, why it works, and how to measure what it does.

- **Facilitation Protocol** — Nervous-system aware, co-regulatory interview methodology
- **Emergent Narrative Impact Framework (ENIF)** — What I call the "Bilingual Dashboard": Royal Metrics (institutional ROI) alongside Nomadic Indicators (relational health and emergence)- **Nomadic Indicators Codebook** — Qualitative coding guide for tracking transformation
- **Session Facilitation Guide** — Phase-by-phase guide for facilitated documentary sessions
- **Theory of Change** — Logic model for grant-funded community applications (grant-facing — maps methodology to funder evaluation frameworks)
- **Evaluation Framework** — Assessment design using the Bilingual Dashboard

### `prompts/`
The prompt stack — AI skill files that encode the methodology, brand voice, and operational logic.

- **15+ Cowork/Claude Code skills** covering facilitation, outreach, proposals, financial tracking, coaching, content publishing, and more
- **Brand context** — Complete positioning, voice guidelines, and creative constraints
- **Instantiation prompt** — The system prompt that bootstraps the full practice

### `cli/`
Shell scripts for outreach and grant pipeline management. Obsidian-native (markdown + YAML frontmatter). No external dependencies, no API keys, no cost.

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
Frameworks and position papers.

- Digital Liver framework
- Fractal Enclosure framework
- Say Why grant concept

---

## Why Open Source

I built this because I enjoy building it. The infrastructure is as interesting to me as the client work — maybe more so. And the question of how a solo practitioner can encode their entire methodology into replicable tools felt worth answering publicly.

There's no product here. No platform, no SaaS, no waitlist. Just how I actually work — the real prompts, the real evaluation frameworks, the real CLI tools I run every week. If any of it is useful to your practice, take it.

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

- Solo consultants and facilitators who want to see how someone else built their practice infrastructure
- Nonprofit professionals looking for evaluation frameworks that measure relational quality alongside deliverables
- Documentary filmmakers curious about participant-led production models
- Anyone building an AI-assisted practice stack who wants to see how prompt engineering connects to methodology

---

## License

MIT License. Use it, fork it, adapt it.

---

*Isaac Rubinstein — [Rubinstein Productions](https://rubinsteinproductions.com)*
*Seattle, WA*