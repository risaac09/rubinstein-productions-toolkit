# CLAUDE.md

## What this repo is
The public toolkit: methodology, templates, docs, generic CLI tools, and the two deployment kit sources (`phase-zero/`, `public-kit/`). Take what's useful is the license posture. Business strategy, brand/voice prompts, and RP-specific production tooling live in the private `rp-intranet` — an August 2026 audit found this repo's older "nothing private lands here" claim wasn't actually true and moved that content out. A second pass on 2026-08-23 removed what that audit's scope missed: the eval corpus, service-tier pricing, revenue projections, and a snapshot of the local permission allow list. See README.md's "What's Not Here" for the full accounting. Before adding anything here, assume public means public.

## The one guardrail
This repo is the kit source, now for two independent kits sharing one installer, `phase-zero/install.sh`.

**phase-zero** (AI-agent session infrastructure): edit `phase-zero/` here, then redeploy with `phase-zero/install.sh --all <parent-dir>`. The deployed `.claude/` copies in the consuming repos are byte-identical output; never edit one in place, the next install overwrites it and the edit dies silently. Consuming repos: the `CONSUMERS` allowlist in `phase-zero/install.sh` is the list of record (history of additions and removals: git log and stack-data `docs/DECISIONS.md`). A direct one-repository install remains available for an intentional exception.

**public-kit** (public-repo hygiene: license templates, README shape, CONTRIBUTING/SECURITY templates, public voice rules), added 2026-08-21: edit `public-kit/` here, then redeploy with `phase-zero/install.sh --public --all <parent-dir>`. Deploys into `.claude/public-kit/`, never repo root — the LICENSE/README/CONTRIBUTING/SECURITY templates are references a human promotes deliberately, not auto-applied files. Separate allowlist, `PUBLIC_CONSUMERS`, scoped to repos that are actually public: alchemy, statehouse-dashboard, gene-keys-data, rubinsteinproductions, risaac09, three-type-evaluation (its public paper side only), isaacrubinstein.com, and this repo itself. Fully independent of `CONSUMERS` — `rp-intranet` takes phase-zero without public-kit (private, no public-hygiene need), `risaac09` takes public-kit without phase-zero (explicitly not a phase-zero consumer), some repos take both, most take neither.

## Routing
- Tier: none, the kit source, not a data store. Phase-zero triggers, session close, and research routing come from the deployed `.claude/` kit (source: `phase-zero/` in this repo).
