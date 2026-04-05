# IT Department

RP is a solo studio. "IT" is not a team — it's the system that prevents Isaac from having to think about plumbing while doing the work. This section is its charter, org chart, and service catalog.

The constraint: every component must be either (a) owned and operable by Isaac alone, or (b) fully delegated to a scheduled agent. Nothing lives in the middle. "Middle" is where tech debt colonizes a solo practice.

---

## Charter

The IT department exists to:

1. **Keep the work reachable** — data, content, and contacts are queryable from one source of truth, not scattered across tools.
2. **Make repetition cheap** — anything done twice gets a runbook or a script. Anything done weekly gets a scheduled agent.
3. **Preserve the off-switch** — no component may require a paid service that cannot be downgraded to free tier within 10 minutes. Sovereignty over convenience.
4. **Stay legible** — every system is documented here. If it's not documented, it's not a system; it's a liability.

---

## Org chart

Isaac is the entire staff. Claude agents and scheduled workflows are delegated functions, not teammates.

| Role | Held by | Responsibilities |
|---|---|---|
| **CTO / Architect** | Isaac | Technology choices, budget tier, vendor selection |
| **Ops / SRE** | Isaac + Claude Code | Runbooks, incident response, backups |
| **Data engineer** | `stack-data` repo + scheduled workflows | Scrape → merge → validate → commit loop |
| **Research assistant** | `client-brief` agent (Claude API) | Generate per-contact briefs from stack-data |
| **Analyst** | `weekly-content-report` agent (Claude API) | Weekly engagement summaries |
| **Maintenance** | Pre-commit hooks, GH Actions | Secret scanning, schema validation, weekly scraping |

**Delegation rule:** A function becomes an agent when (a) it runs on a schedule, or (b) it has a clear input → output contract that Claude can execute deterministically. Everything else stays with Isaac.

---

## Service catalog

What runs, where it lives, what it costs, who depends on it.

### Data layer

| Service | Location | Cost | Consumers |
|---|---|---|---|
| `stack-data` (private repo) | github.com/risaac09/stack-data | Free | All PWAs, all agents |
| IG scrape workflow | GH Actions, Mondays 13:00 UTC | ~$5/mo Apify credits | stack-data |
| Pre-commit secret scanner | `~/scripts/git-hooks/` (symlinked to all repos) | Free | All repos |

### PWAs (public, GH Pages)

| PWA | Purpose | Data source |
|---|---|---|
| `rp-lifecycle` | 10-phase videography project template | `data/` synced from stack-data (sanitized) |
| `royal-metrics` | Client ROI + business KPI dashboard | `data/` synced from stack-data (incl. financials) |
| `ultraflow-companion` | Ultrarunning recovery/ease tracker | stack-data activities (pending wire-up) |

### Agents

| Agent | Trigger | API cost | Output |
|---|---|---|---|
| `weekly-content-report` | Manual / cron | ~$0.05/run | `reports/YYYY-MM-DD-content.md` |
| `client-brief` | Manual | ~$0.05/run | `briefs/{contact-id}.md` |
| `training-content-correlator` | Manual | Free (jq only) | `reports/YYYY-MM-training-content.md` |

### External services

| Service | Purpose | Tier | Secret location |
|---|---|---|---|
| Anthropic Claude API | Agents | Pay-as-you-go | `~/.secrets/anthropic.env` |
| Apify | IG + YouTube scraping | Free tier (~$5/mo credits) | `~/.secrets/apify.env` |
| GitHub | Repos + Actions + Pages | Free | `gh auth` |
| MCP connectors | Gmail, Calendar, Figma, Canva, Jotform, PayPal | Free (Claude.ai) | Managed by Claude Code |

---

## Budget tiers

The stack was built to scale down as gracefully as up. Current tier is the floor.

| Tier | Monthly | What it unlocks |
|---|---|---|
| **$100 (current)** | Claude Max + Apify free + GH free | Full stack as documented above |
| **$500** | + Plausible analytics + CF Workers + real domain | Public agent endpoints, traffic data |
| **$1–1.2k** | + scheduled Claude API runs + Linear/Notion integrations | Autonomous daily briefing, calendar-driven ops |

See [roadmap.md](roadmap.md) for upgrade sequence.

---

## Governance rules

1. **No component without a runbook.** If a service can break, `systems.md` must say how to fix it.
2. **Secrets never in code.** Pre-commit hook enforces. Secrets live in `~/.secrets/*.env`.
3. **Public repos are public.** Assume anything in a public repo will be read. No client data, no financials, no tokens.
4. **Destructive commands require review.** `rm -rf`, `git push --force`, `reset --hard` — always pause.
5. **Vendors are disposable.** Every vendor has an exit plan in [security.md](security.md).
