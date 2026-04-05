# Security & Continuity

Security for a solo studio is not about enterprise compliance. It's about three things:

1. **Don't leak credentials.**
2. **Don't lose client data.**
3. **Don't get locked into a vendor you can't leave.**

---

## Credentials

### Where secrets live

All secrets in `~/.secrets/*.env`. File permissions `chmod 600`. Sourced by `.zshrc`. Never in code, never in git, never in Obsidian, never in a chat transcript.

### Pre-commit hook

`~/scripts/git-hooks/pre-commit-secret-scan` runs on every commit across all repos (symlinked into each repo's `.git/hooks/`). Patterns scanned:

- Anthropic keys (`sk-ant-*`)
- OpenAI keys (`sk-*` 40+ chars)
- Apify tokens (`apify_api_*`)
- AWS access keys
- GitHub tokens (`ghp_*`, `gho_*`, `ghs_*`)
- Private key headers (`-----BEGIN * PRIVATE KEY-----`)
- Generic `.env` contents with `=` patterns

### Key rotation

Rotate when:

- A key is exposed (accidentally committed, pasted in an untrusted place, used on a shared machine).
- A vendor notifies of a breach.
- Quarterly, as baseline hygiene.

**Anthropic:** console.anthropic.com → API Keys → Revoke + regenerate. Update `~/.secrets/anthropic.env`.

**Apify:** console.apify.com → Settings → Integrations → regenerate. Update `~/.secrets/apify.env` AND `gh secret set APIFY_TOKEN -R risaac09/stack-data`.

**GitHub:** `gh auth refresh` or regenerate a personal access token at github.com/settings/tokens.

---

## Backups

| Asset | Primary | Backup 1 | Backup 2 |
|---|---|---|---|
| Code (all repos) | Local working tree | GitHub (origin) | Time Machine |
| stack-data | GitHub private | Time Machine | (plus sanitized copies in each PWA) |
| Film footage | External SSD per project | Dropbox / cloud archive | (originals on SD cards until project delivers) |
| Obsidian vaults (metabolizer, notes) | Local | iCloud sync | Time Machine |
| Secrets | `~/.secrets/` | Password manager (1Password/Keychain) | Printed copy in home safe (optional) |

**Recovery time objective:** 1 hour to get to a working laptop from a fresh machine (restore Time Machine or clone all repos + re-create `~/.secrets/`).

---

## Data classification

| Tier | Contents | Storage | Sharing |
|---|---|---|---|
| **Public** | Methodology, skills, published content | Public repos, website | Freely shared |
| **Internal** | Client names, project briefs, contact notes | `stack-data/` (private), `contacts.json` | Isaac only |
| **Financial** | Revenue, contract values, invoices | `stack-data/data/financials.json`, royal-metrics PWA | Isaac only |
| **Secrets** | API keys, tokens, passwords | `~/.secrets/*.env` | Isaac only, never leaves laptop |

**Rule:** A PWA copying stack-data runs through `sync-to-pwa.sh`, which strips or preserves based on destination. Financial data goes **only** to royal-metrics.

---

## Vendor exit plans

Every paid or account-locked service has a known-good exit path. If any of these vendors shut down, double pricing, or become hostile, the stack keeps running.

| Vendor | Lock-in risk | Exit path |
|---|---|---|
| **GitHub** | High (all repos, Actions, Pages) | Git is distributed — clone all repos, push to GitLab/Codeberg. Pages → Cloudflare Pages (drop-in). Actions → local cron or GitLab CI. |
| **Anthropic (Claude API)** | Medium (agent prompts) | Agents are shell scripts calling a single endpoint. Swap to OpenAI/Gemini by editing curl commands. Prompts are portable. |
| **Claude Max (Claude Code)** | Medium (workflow dependency) | Claude Code is the primary interface. If discontinued, fall back to Claude.ai web + copy-paste. Skills/memory files remain readable by any LLM. |
| **Apify** | Low | Replace with direct scraping scripts (playwright) or alternative actors. IG scraping specifically is commoditized. |
| **Gumroad** | Low | Product files are Obsidian vaults. Switch to Lemon Squeezy, Stripe Links, or self-hosted. |
| **MCP connectors** | Low | Each is a convenience layer over a public API. Drop back to direct API calls via curl or scripts. |

---

## Access control

There's one user: Isaac. Access control reduces to:

- **Laptop login:** Touch ID + strong password.
- **GitHub account:** 2FA enforced (hardware key or TOTP).
- **Anthropic account:** 2FA enforced.
- **No shared accounts.** If a collaborator ever needs access, grant via GitHub team/repo collaborator — never share credentials.

---

## Incident log

Keep a log of anything that broke + how it was fixed. Living here:

`~/rubinstein-productions-toolkit/intranet/docs/it/incidents.md` — currently empty. Start it the first time something breaks.

Template entry:

```markdown
## 2026-04-XX — Short title

**Symptom:** what went wrong, who/what noticed
**Root cause:** what actually caused it
**Fix:** what was done
**Prevention:** what changed so it won't recur
```
