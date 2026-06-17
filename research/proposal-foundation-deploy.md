# Governance Proposal: Deploy the minimal pilot foundation
*Working Document | Created: 2026-06-17*
*Routed through: `rubinstein-productions-coo` (capacity governance), the
security and data-governance lens, `methodology/source-tracking-protocol.md`,
and `docs/CANONICAL-STRUCTURE.md`. Orchestrator: `rubinstein-productions-agent`.*

*Source note (per source-tracking-protocol): the two verdicts below are model
synthesis from this session, grounded in reads of the COO skill, the financials
and projects in stack-data, the foundation spec, and the methodology docs. The
specific security best-practices (JWT expiry, webhook dedup, rate limits) are
model synthesis, not yet traced to a primary source. Treat as advisory until
verified.*

---

## The ask

Deploy the minimal shared foundation from `research/rp-shared-foundation-spec.md`:
JWT plus entitlement plus one live $49 Stripe checkout on Say Why, then
generalize. This is what the `idea-to-pilot` Graduate gate depends on.

## Governance review

### COO and capacity lens, verdict: DEFER

- **Capacity looks open, but it is the wrong kind.** Zero active Founder Story and zero Program Engagements this week reads as availability, but infrastructure work is deep linear focus, not the responsive facilitation energy the practice runs on. Estimated 40 to 60 hours.
- **Cost exceeds the near-term return.** At the $500/hr floor that is $20K to $30K of Isaac's time, against $0 booked and a $49 path that nobody has bought yet. The $49 tier is a proof of concept, not a deal.
- **Opportunity cost is the real flag.** The warm money this week is the evaluation pipeline (McEvoy, Grossman, RIPCA, plus the RIDOH intro), $105K to $160K in play. None of it requires this foundation. Building now competes directly with closing it. This is the named anti-pattern: building systems instead of shipping.
- **Override clause.** If Isaac feels a genuine Sacral yes on the build, that overrides the COO. The COO lens only argues from capacity and revenue, and from there the answer is defer.

### Security and data lens, verdict: CONDITIONALLY APPROVABLE

Sound in concept (it names zero-measurement, consent, export, secret isolation),
underspecified in governance. Before any deploy can be called governed:

**Minimum bar**
- Secrets only in worker env, never in a repo. The pre-commit hook must reject API keys.
- Schema validation at load. Corrupt state is migrated or rejected, never trusted.
- Zero-measurement enforced in code. KV holds session, entitlement, and an audit record only. Never the user's words.
- Consent is a visible record with a working data-export button.
- JWT carries no content and has a short expiry, enforced on client and server.
- Stripe webhook verifies the signature and deduplicates by event id.

**Deploy red lines (any one blocks)**
- An API key anywhere in git history.
- Unvalidated state accepted on load.
- A Stripe webhook without signature verification.
- Session tokens without expiry.
- Any user content in KV.

### Canonical-structure note
The foundation docs live in `research/`, never in the vault. If `rp-shared/` and
the worker are created, they must not write to vault paths; the existing
pre-commit vault-mirror hook will catch it. A `SECURITY.md` (secret rotation,
incident response) is required before the foundation is marked canonical.

## Orchestrator recommendation: the both/and

Two decisions, not one. They do not compete.

1. **Defer the build.** The COO lens is right about this moment. The warm
   evaluation pipeline gets Isaac's focus. The foundation does not get built
   this week.
2. **Approve hardening the spec now.** Folding the security minimum bar and red
   lines into `rp-shared-foundation-spec.md` costs no build hours and no
   opportunity cost. It makes the spec shovel-ready, so the day a trigger fires
   the build starts governed rather than improvised.

This keeps the work alive without spending the energy the pipeline needs.

## Decision (for Isaac to record)

- [ ] **Defer the build, harden the spec now** (orchestrator recommendation), or
- [ ] **Build now** (Sacral override, accept the opportunity cost against the pipeline), or
- [ ] **Shelve entirely** (the spec waits untouched).

## Triggers to revisit a build

Any one of these flips defer to take:
- An evaluation or program deal closes that needs multi-device state or consent tracking. The foundation becomes a delivery dependency.
- A real Say Why buyer appears. The $49 path becomes a revenue case and auth becomes blocking.
- Isaac lands the first paying Founder Story clients and the GDC case study ships. Phase 1 is confirmed and the foundation is the next milestone.
