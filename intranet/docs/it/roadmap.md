# Roadmap

The stack scales in budget tiers. Each tier is stable on its own — no tier requires the next. Upgrade only when a specific constraint bites.

---

## Current tier: $100/mo

**Baseline.** Everything in the service catalog runs here.

| Cost | Provider | Line item |
|---|---|---|
| $100 | Anthropic | Claude Max (includes Claude Code, generous usage) |
| ~$5 | Apify | IG + YouTube scraping (free credits usually cover) |
| $0 | GitHub | Repos, Actions, Pages (all free tier) |
| $0 | MCP connectors | Gmail, Calendar, Figma, Canva, Jotform, PayPal |

**What this tier can do:**

- Full stack-data loop (scrape → merge → validate → commit, weekly)
- On-demand Claude agents (client-brief, weekly-content-report)
- 3 deployed PWAs
- Pre-commit security, schema validation, referential integrity checks

**What this tier cannot do:**

- Scheduled agent runs against Claude API (requires budget for autonomous runs)
- Traffic analytics
- Custom domain
- Public agent endpoints (API-as-a-service)

---

## Next tier: $500/mo

**Trigger to upgrade:** When "I wish this ran by itself" becomes weekly.

| Add | Why | Replaces |
|---|---|---|
| $50/mo budget for Claude API | Cron-driven daily briefs, autonomous weekly reports | Manual agent invocation |
| $9/mo Plausible | Privacy-respecting traffic analytics for PWAs | Flying blind |
| $5/mo CF Workers | Serverless backend for public agent endpoints | No way to expose agents to clients |
| $12/yr real domain | rubinsteinproductions.com points to infrastructure, not just marketing site | Disparate GH Pages subdomains |
| Buffer | Vendor price bumps, experiments | — |

**What this tier unlocks:**

- **Daily briefing** — morning digest of calendar, IG engagement deltas, overdue invoices.
- **Client-facing agents** — public URL a prospect can visit to get a custom strategy memo.
- **Traffic data** — know which PWA is actually used and when.
- **One domain, one brand.**

---

## Aspirational tier: $1–1.2k/mo

**Trigger to upgrade:** When two clients pay Territory-tier retainers (~$4–8k/mo each) and capacity management becomes the binding constraint, not budget.

| Add | Why |
|---|---|
| Linear or Notion | Actual project management across multiple active engagements |
| Custom MCP server | Bespoke tool access for client-specific workflows |
| Scheduled Claude API runs at ~$200/mo | Fully autonomous weekly ops cycle |
| Analytics stack upgrade | Plausible → PostHog or similar for product analytics |
| Possibly VA retainer | Delegate scheduling, invoice chasing |

**What this tier unlocks:**

- Autonomous ops: stack runs weekly ops cycle without Isaac invoking anything.
- Actual multi-client capacity: >1 Territory client sustainable.
- Client-specific agent personalities via custom MCP.

---

## Upgrade sequence (when the time comes)

1. **First add: Plausible** — it's cheap and tells you what matters. ($9/mo)
2. **Then: Claude API budget** — set to $50/mo cap. Autonomous agents start earning their keep. ($50/mo)
3. **Then: Real domain** — once agents have public endpoints worth pointing at. ($12/yr)
4. **Then: CF Workers** — when an agent endpoint needs to exist as a URL someone else can visit. ($5/mo)
5. **Only then: Linear/Notion** — once two concurrent engagements make JSON + markdown insufficient.

---

## Known future decisions

- **Push notifications** for PWAs (requires a backend — waits for CF Workers).
- **Public mirror of stack-data** — currently private; may publish a sanitized fork for public PWA consumption via CDN.
- **Centralized secret vault** — right now `.env` files + Time Machine is sufficient; may migrate to 1Password CLI if team ever grows past solo.
- **Ultraflow Companion** wire-up to stack-data activities — pending, low priority until training log becomes a product input.
