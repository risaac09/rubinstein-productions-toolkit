# Systems Inventory & Runbooks

Every system the practice runs on, where it lives, and how to fix it when it breaks.

---

## Hardware

| Device | Role | Backup strategy |
|---|---|---|
| M2 Pro MacBook (primary) | Development, filmmaking, everything | Time Machine (local) + iCloud + git remotes |
| Panasonic GH7 | Filming | SD cards archived to external SSD per project |

---

## Repositories

| Repo | Visibility | Purpose | Deploy target |
|---|---|---|---|
| `rubinstein-productions-toolkit` | Public | Methodology, skills, CLI, intranet | — (mkdocs site local-only) |
| `stack-data` | Private | Unified data layer | — (consumed by sync-to-pwa.sh) |
| `rp-lifecycle` | Public | PWA | GH Pages |
| `royal-metrics` | Public | PWA | GH Pages |
| `ultraflow-companion` | Public | PWA | GH Pages |
| `the-metabolizer` | Not git | Obsidian vault product | Gumroad |

---

## Secrets

All secrets live in `~/.secrets/*.env`, sourced by `.zshrc`. Pre-commit hook blocks accidental commits.

| File | Contents | Used by |
|---|---|---|
| `~/.secrets/apify.env` | `APIFY_TOKEN`, `APIFY_USER_ID` | Apify scripts, GH Actions |
| `~/.secrets/anthropic.env` | `ANTHROPIC_API_KEY` | All Claude API agents |

**GitHub Actions secrets** (set via `gh secret set`):

- `APIFY_TOKEN` on `risaac09/stack-data` — required for weekly-ig-scrape workflow

---

## Runbooks

### IG scrape fails

**Symptom:** GH Actions "Weekly IG scrape" workflow red, no new posts in `data/content.json`.

1. Check Apify credits: https://console.apify.com/billing
2. Check workflow logs: `gh run list -R risaac09/stack-data --workflow=weekly-ig-scrape.yml`
3. If token invalid, regenerate at console.apify.com → Settings → Integrations, then:
   ```
   gh secret set APIFY_TOKEN -R risaac09/stack-data
   ```
4. Re-run: `gh workflow run weekly-ig-scrape.yml -R risaac09/stack-data`

### Pre-commit hook blocks a legitimate commit

**Symptom:** Commit rejected with `[SECRET DETECTED]` but the flagged line is a test fixture or documentation example.

1. Read the hook output — note the pattern that matched.
2. If the match is a false positive, rewrite the line to not match (e.g., use `sk-ant-REDACTED` or `xxxxxxxx` placeholders).
3. Do **not** use `--no-verify`. If the pattern is consistently wrong, edit `~/scripts/git-hooks/pre-commit-secret-scan` to tighten it.

### stack-data out of sync with a PWA

**Symptom:** PWA shows stale contacts or missing project.

```bash
cd ~/stack-data
bash scripts/sync-to-pwa.sh rp-lifecycle      # or royal-metrics, ultraflow-companion
cd ~/<pwa-name>
git add data/ && git commit -m "Sync stack-data" && git push
```

**Note:** `sync-to-pwa.sh` sanitizes — contacts lose email/phone/notes, projects lose contractValue/hoursLogged for non-royal-metrics. financials.json is **only** copied to royal-metrics.

### A Claude agent returns garbage

**Symptom:** `client-brief` or `weekly-content-report` produces generic slop or hallucinates facts.

1. Check the prompt in the `.md` runbook alongside the `.sh` — is it still accurate?
2. Check the input payload: `jq '.' data/contacts.json | grep <id>` — does the record have enough to work from?
3. If the record is thin, add detail via `sd-add-contact` or edit the JSON directly. Agents can only be as specific as their input.
4. If prompts drift, update the system prompt in the `.sh` file.

### SW cache stuck on a PWA

**Symptom:** Code changes deployed but users see old version.

1. Bump `const CACHE = 'pwa-name-vN'` in `sw.js` (increment N).
2. Add any new assets to the `ASSETS` array.
3. Commit and push. Next visit auto-updates.

### Data layer corruption

**Symptom:** `bash scripts/validate.sh` fails in stack-data.

1. Check last good commit: `git log --oneline data/`
2. Identify the bad file: validate.sh tells you which one.
3. Fix by hand if obvious, or: `git checkout <good-sha> -- data/<file>.json` then re-apply the needed change.
4. Always run `validate.sh` **before** committing. Add it as a local pre-commit step if this happens more than once.

---

## CLI reference

All `sd-*` scripts live in `~/stack-data/scripts/` and are on PATH (via `.zshrc`).

| Command | Purpose |
|---|---|
| `sd-add-contact` | Append a contact record |
| `sd-add-project` | Append a project record |
| `sd-add-activity` | Append an activity (run, strength, etc.) |
| `sd-add-financial` | Append an invoice/expense/payment |
| `sd-link` | Bidirectional content ↔ project link |
| `sd-list` | List entities with filters |
| `sd-report` | Engagement summary |

All scripts validate after writing. Runs interactively with no args, or with positional args for scripting.

---

## Schedules

| Job | Cadence | Host |
|---|---|---|
| Weekly IG scrape | Mon 13:00 UTC (~8am ET) | GH Actions |
| Weekly content report | Manual (upgrade to cron at $500 tier) | Local |
| Client brief (per contact) | Manual | Local |
| Training ↔ content correlator | Manual | Local |
