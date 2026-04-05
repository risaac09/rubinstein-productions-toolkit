# All Systems Go — RP Operations Sweep

Drop-in prompt for a full operational triage across Rubinstein Productions.
Activates the full stack, surfaces the field, proposes actions — does not act.

---

Activate the `rubinstein-productions-agent` skill as orchestrator. Run a full operational sweep across the practice and surface what needs my attention today.

**1. Ground the state (read, don't assume):**
- Read `~/stack-data/data/` — contacts, projects, financials, content, activities — to establish current ground truth.
- Check Gmail (unread + threads awaiting my reply) and Google Calendar (today + next 7 days) via MCP.
- Note any stale data (items not touched in >30 days that should be active).

**2. Triage across domains:**
- **Pipeline / outreach** (`outreach-email-manager`, `network-stewardship`): who's waiting on me, who's gone cold, who to re-engage this week.
- **Active projects** (`project-management-coordinator`): deliverables due, blockers, client comms owed, capacity check.
- **Financials** (`invoice-financial-tracker` → finance-hub): unpaid invoices, upcoming expenses, revenue vs. plan.
- **Content** (`content-publishing-agent`): last post date, engagement trends from `content.json` metricsHistory, next piece to ship.
- **COO layer** (`rubinstein-productions-coo`): pricing discipline, capacity, opportunity triage — flag anything off-pattern.

**3. Synthesize — give me:**
- **Top 3 actions for today** (highest leverage, ranked).
- **This week's critical path** (what must move).
- **Risks / drift** (capacity breaches, cold leads, stale commitments, pricing slips).
- **One strategic question** worth sitting with.

**4. Ask before acting.** Don't send, draft, or modify anything yet — just surface the field. I'll direct from there.

Constraints: terse output, no filler, concrete names/numbers over abstractions. If stack-data or MCP state conflicts with memory, trust what you observe now.
