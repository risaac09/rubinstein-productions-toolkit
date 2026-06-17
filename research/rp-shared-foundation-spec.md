# rp-shared + rp-api: the foundation pilots inherit
*Working Document | Created: 2026-06-17*
*Purpose: Specify the thin shared foundation so a pilot that graduates past
single-user inherits auth, data validation, consent, and payments instead of
rewriting them. This is the blueprint the `idea-to-pilot` Graduate gate points
at. It is a spec, not a deployment.*

---

## Why this exists

Every PWA references `~/rp-shared/` and a `rp-api` Cloudflare Worker. Neither is
deployed. `rp-shared/` does not exist on the machine, the worker's
`wrangler.toml` carries placeholder KV ids, and no Stripe keys are set. So each
app duplicates design tokens and has no real backend. The result: single-user
apps work, anything multi-device is blocked, and "scalable" and "secure" are
claims the stack cannot yet back.

This spec defines the smallest foundation that makes those claims true, and the
contract a pilot consumes to inherit it.

## Layout

```
rp-shared/
├── lib/
│   ├── design-system.css   shared tokens: color, type, spacing, the house aesthetic
│   ├── sw.js               service-worker template (cache version injected per app)
│   ├── api-client.js       fetch wrapper for rp-api, handles base URL + JWT
│   ├── schema.js           load-time state validation (validate/migrate/reject)
│   ├── consent.js          consent record + data-export helper
│   └── paywall.js          Stripe checkout + entitlement check
├── worker/                 the rp-api Cloudflare Worker
│   ├── index.js            routes
│   └── wrangler.toml       KV namespaces, secrets (real ids, not placeholders)
└── scripts/
    └── sync-shared.sh      copies lib/ into a pilot, stamps the cache version
```

## What each piece gives a pilot

| Piece | The pilot inherits | Replaces today's |
|---|---|---|
| `design-system.css` | One set of tokens and the house aesthetic | CSS rewritten per app |
| `sw.js` | Offline cache with a stamped version | Hand-rolled service worker per app |
| `schema.js` | Load-time validation of localStorage state | Unvalidated state, trusts whatever is stored |
| `consent.js` | A consent record and a data-export path | A privacy claim with no mechanism |
| `api-client.js` | Auth-aware calls to the backend | Per-app fetch and a dead `rp-api.workers.dev` fallback |
| `paywall.js` | Stripe checkout and entitlement gating | A designed-but-dead paywall |

## rp-api routes (the contract)

The worker is the only backend. Pilots call it through `api-client.js`.

- `POST /api/jwt/for-session` — mint a short-lived session token. The auth root.
- `POST /api/stripe/checkout` — start a checkout. `POST /api/stripe/webhook` — record entitlement in KV.
- `GET  /api/entitlement` — does this session own the paid tier.
- `POST /api/saywhy/transcribe` — Claude API audio transcription (paid).
- `POST /api/saywhy/format-pdf` — server-rendered branded PDF (paid).

Secrets (Stripe keys, the Anthropic key) live in worker env, never in a repo,
never in client code. The same `LLM_BASE_URL`-over-`ANTHROPIC_API_KEY` switch
the `stack-data` sync uses applies here.

## The data and consent rule

- **Client state** stays validated by `schema.js` at load. Bad state is migrated or rejected, never trusted.
- **Server state** in KV holds only what a paid feature needs: entitlement and session, not content. The Performance-phase "zero-measurement" rule from the methodology holds in code: the backend does not store the user's words.
- **Consent** is a record the user can see and a button that exports their data. The privacy claim becomes verifiable.

## Minimal first deploy (the smallest real thing)

Do not build all six lib files at once. The smallest deploy that unblocks the
Graduate gate:

1. Create the worker with a real `wrangler.toml` (one KV namespace for entitlement).
2. Ship `/api/jwt/for-session` and `/api/entitlement` only. Auth and gating, nothing else.
3. Wire `api-client.js` + `paywall.js` into one pilot (Say Why is the readiest, its paywall is already designed).
4. Set Stripe keys as worker secrets. Test one real $49 checkout end to end.
5. Only then add transcribe and format-pdf.

That sequence turns "designed" into "deployed" for the one path that has a buyer,
before generalizing.

## Honest current state

Specified here, not built. The worker code exists in a form but has never run
with real ids or keys. `rp-shared/` needs creating. This document is the target;
the `idea-to-pilot` skill's Graduate gate stays closed until step 4 above passes
once.
