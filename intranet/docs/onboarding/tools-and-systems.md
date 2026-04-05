# Tools & Systems

## Knowledge Management

### Obsidian (Second Brain)

The primary knowledge system. Everything lives here: methodology docs, client notes, prospect pipeline, grant tracking, personal practice.

**Vault location:** `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/`

**Key folders:**

| Folder | Contents |
|--------|----------|
| `Practice/Rubinstein Productions/` | All RP-related docs, brand, methodology, operations |
| `Practice/Rubinstein Productions/Outreach/` | Prospect pipeline, outreach scripts, grant applications |
| `Practice/Rubinstein Productions/Methodology/` | ENIF, Nomadic Indicators, facilitation protocol |
| `Practice/Rubinstein Productions/Brand(s)/` | Brand kit, creative strategy, content calendar |

**Plugins used:** Dataview (pipeline dashboards), Templater (templates), Calendar, Daily Notes.

### GitHub Repository

The public-facing toolkit: [github.com/risaac09/rubinstein-productions-toolkit](https://github.com/risaac09/rubinstein-productions-toolkit)

Contains: methodology docs, CLI tools, prompt stack, templates, grant infrastructure.

---

## Pipeline & CRM

### CLI Tools (Obsidian-native)

Zero external dependencies. All tools create and manage markdown files with YAML frontmatter.

| Command | Purpose |
|---------|---------|
| `rp-prospect` | Create a new prospect file |
| `rp-pipeline` | View prospect pipeline by status |
| `rp-update` | Update prospect status and log touches |
| `rp-draft` | Draft outreach email and open Gmail compose |
| `rp-followup` | Surface overdue follow-ups |
| `rp-grant` | Grant funder tracking and deadlines |

**Installation:** `source ~/.zshrc` after running `install.sh` from the toolkit repo.

### Airtable

Used for outreach activity logging (integrated with the `outreach-email-manager` skill). Not the primary CRM — Obsidian is.

---

## AI Tools

### Claude Code / Cowork Skills

The prompt stack that operationalizes the entire practice. 15+ skills covering:

| Skill | What It Does |
|-------|-------------|
| `rubinstein-productions-agent` | Master orchestrator — routes all RP tasks |
| `rubinstein-productions-coo` | Pricing, capacity, strategic decisions |
| `proposal-scope-builder` | Draft proposals and scope documents |
| `outreach-email-manager` | Draft and log outreach emails |
| `project-management-coordinator` | Track active projects and deliverables |
| `invoice-financial-tracker` | Invoicing, expenses, tax tracking |
| `content-publishing-agent` | LinkedIn, essays, case studies |
| `network-stewardship` | Relationship management |
| `cognitive-debugger` | Bias detection and metacognition |
| `psychotherapeutic-coach` | Inner practice and personal development |

**Location:** `~/.claude/skills/` (or as Cowork plugin skills)

---

## Production

### DaVinci Resolve Studio

Primary editing, color grading, and audio tool. Used for all video production.

- **Workflow:** Import → organize → rough cut → color (V-Log) → audio (Fairlight) → export
- **Export specs:** H.264, 4K master + 1080p web delivery

### Camera Equipment

See [Equipment & Logistics](../operations/equipment-logistics.md) for full kit inventory.

- Primary camera: iPhone 15 Pro (ProRes, 4K/24fps)
- Audio: Rode SmartLav+ (wired) / Rode Wireless GO II
- Lighting: Aputure MC (compact LED)

---

## Communication

| Tool | Used For |
|------|----------|
| **Gmail** | Client correspondence, proposals, invoices |
| **Zoom** | Remote sessions, client calls |
| **Phone/text** | Quick client questions, filming support |

No Slack, no project management software, no ticketing system. Simplicity is intentional — every tool adds cognitive load.

---

## Financial

| Tool | Used For |
|------|----------|
| **Spreadsheet** | Monthly P&L, revenue tracking, tax reserve |
| **Invoice template** | Client invoicing (docx or PDF) |
| **Separate bank account** | Business income and tax reserve |

See [Financial Operations](../operations/financial-operations.md) for cadence and procedures.

---

## Website

[rubinsteinproductions.com](https://rubinsteinproductions.com) — Client-facing site. Keep messaging aligned with the [Brand Identity](../strategy/brand-identity.md) guidelines.
