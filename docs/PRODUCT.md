# rubinstein-productions-toolkit

The public, MIT-licensed operational stack behind Rubinstein Productions: methodology docs, AI skill files and prompts, outreach and grant CLI tools, a skill eval harness, production tooling, and the phase-zero kit source that `phase-zero/install.sh` deploys into ten sibling repos. It carries no tier. It is the kit source, not a data store; the spine is stack-data, Tier 1, the operational source of truth, a sibling clone at `../stack-data`. License posture: take what's useful. Nothing private lands here; private material went to rp-intranet.

## What it is (technical)

Flat directories of markdown, shell, and static HTML. No build step anywhere except the eval harness's Python helpers. The full top-level map, which `README.md`'s What's Inside section only partially covers:

- `methodology/`: the practice's intellectual core. Six files are vault snapshots with `Canonical source:` headers, synced by `scripts/sync-methodology.sh` (enif.md, session-facilitation-guide.md, digital-liver-offering.md, nomadic-indicators-codebook.md, theory-of-change.md, evaluation-framework.md). The rest, including facilitation-protocol.md, glossary.md, and methodology-blueprint.md, are toolkit-authoritative and edited here. `services.yaml` is the machine-readable service tier binding.
- `prompts/` (+ `skills/`): 15 skill files encoding the methodology, plus `brand-context.md`, `agents.md`, `all-systems-go.md`, and the orchestration-model explainer.
- `cli/`: outreach and grant pipeline scripts (rp-prospect, rp-pipeline, rp-update, rp-draft, rp-followup, rp-grant) over markdown + YAML frontmatter. `RP_OUTREACH_DIR` overrides the default vault path.
- `templates/`: prospect, funder, onboarding, case study, and dashboard templates the CLI falls back to.
- `grants/`: theory of change, evaluation framework, fiscal sponsorship strategy for the Say Why initiative.
- `research/`: position papers, including dual-architecture.md and the 2026-06-17 strategic sweep.
- `production/` (+ `badwords-rp/`): Resolve workflow script, powergrades, filming guide, and the BadWords config layer (see `production/badwords-rp/README.md`).
- `evals/`: the with-skill-vs-baseline harness, 17 skill directories, `lib/grade.py` and `lib/benchmark.py`. See `evals/README.md`.
- `context-provenance/`: four-channel provenance model and Python CLI. See `context-provenance/README.md`.
- `seed-bed/`: the pre-categorical incubation skill (`seed-bed/SKILL.md`).
- `architecture/`: the RP System Map (`index.html`), deployed to GitHub Pages by the repo's only GitHub Action.
- `phase-zero/`: the kit source. Deploys into ten repos. See "How it runs" below and `phase-zero/README.md`.
- `scripts/`: sync-methodology.sh, check-vault-mirror-drift.sh, hooks/, proposal-builder/.
- `docs/`: CANONICAL-STRUCTURE.md (the vault/toolkit boundary and its guards) and this file.
- `.claude/`: the deployed kit copy for this repo itself. Byte-identical output of `phase-zero/`. Never edited in place.
- `.github/workflows/`: deploy-architecture.yml only.

Gap: `architecture/index.html` is a deployed system map with no companion prose explaining what it depicts or why it is published; the answer would come from Isaac or a short note beside it.

## How it runs (operational)

The kit-source rule is the one guardrail (`CLAUDE.md`): edit `phase-zero/` here, redeploy with `phase-zero/install.sh --all <parent-dir>`, never edit a deployed `.claude/` copy. The ten consuming repos: stack-data, second-brain-mirror, second-brain-v2, saywhy-app, royal-metrics, rubinsteinproductions, rp-intranet, gene-keys-data, three-type-evaluation, and this repo itself. Per-machine install: `phase-zero/global/install-global.sh`. Hook mechanics, the jq settings merge, and the three-level awareness fallback are in `phase-zero/README.md`. The hook's six-phrase case pattern is canonical; when a doc and the hook disagree, the hook wins.

Gap: no written procedure verifies kit-deploy consistency across the ten consumers after `install.sh --all`. In practice a diff of each consumer's `.claude/phase-zero.md` against `phase-zero/phase-zero.md` does it; the SOP would come from Isaac writing that check down, here or in `phase-zero/README.md`.

Three guards hold the vault/toolkit boundary (`docs/CANONICAL-STRUCTURE.md`): the on-demand drift detector, the pre-commit hook chain, and the daily launchd job. Incident response when the detector fires lives in that doc.

Gap: the launchd plist `com.rubinsteinproductions.toolkit-mirror-check.plist` is referenced by CANONICAL-STRUCTURE.md but its source is not versioned anywhere in this repo, so a clean machine cannot recreate the daily guard from a checkout; the fix would be committing the plist under `scripts/` or documenting its full contents.

## Why it exists (intellectual)

Isaac built the infrastructure behind his own practice and judged keeping it private wasteful (`README.md`, Why Open Source). There is no product, no SaaS, no waitlist. The organizing metaphor is the Digital Liver, process and express, applied to the whole toolkit in `CONTENT-STRATEGY.md`. The philosophical spine sits in `methodology/digital-liver-offering.md`, `methodology/negative-archive-ritual.md`, and `methodology/total-cost-of-ownership.md` with their source and claim-trace companions. `research/dual-architecture.md` names the fractal relationship between the two bodies of work. The Lanier lineage behind four-channel attribution is in `context-provenance/README.md`. Identity frame, the four directions, and delegation philosophy live in `phase-zero/phase-zero.md`.

## How it works (methodological)

Two methods live here, the practice's and the repo's own.

The practice: `methodology/facilitation-protocol.md` (nervous-system-aware sessions, toolkit-authoritative) and the Bilingual Dashboard measurement system (enif.md, nomadic-indicators-codebook.md, evaluation-framework.md, session-facilitation-guide.md, all vault snapshots). `methodology/methodology-blueprint.md`, `glossary.md`, and `source-tracking-protocol.md` describe how the practice itself is built and maintained. `grants/theory-of-change.md` and `grants/evaluation-framework.md` map the methodology to funder evaluation frameworks.

The repo's own method: skills are evaluated, not assumed. `evals/README.md` documents the with-skill-vs-baseline loop (write evals.json, spawn subagents, grade, benchmark, revise); `evals/ANALYSIS.md` holds the cross-skill findings, 19 skills, 104 runs, four named patterns. `seed-bed/SKILL.md` holds the pre-categorical incubation method and six-type idea typology. `methodology/orchestration-model.md` is the canonical orchestration spec; `prompts/orchestration-model.md` is its plain-language explainer.

## How it speaks (marketing and comms)

Two audiences, practitioners and funders. `CONTENT-STRATEGY.md` holds the pillars, flywheel, launch sequence, platform table, metrics, and the binding What NOT to Publish list. `prompts/brand-context.md` carries the full positioning and voice under the Digital Liver lens. `research/profile-consolidation-analysis.md` sets the one-public-profile directive with rubinsteinproductions.com as hub. Voice rules for anything public here: no em-dashes, no rule-of-three, no promotional verbs, active voice, concrete nouns, short sentences (`phase-zero/phase-zero.md`).

Gap: CONTENT-STRATEGY.md is an April 2026 pre-launch plan with 30-day and 90-day targets and no shipped-vs-planned record as of July 2026; the reconciliation would come from Isaac reviewing it against what shipped.

## Where it goes (strategic)

No tier; the kit source sits beside the stack rather than inside it, and stack-data remains the spine. The 2026-06-17 sweep's open strategic questions live in `research/evaluation-forward-hub-framing.md`, `research/frontier-lab-blueprint.md`, `research/proposal-foundation-deploy.md`, and `research/rp-shared-foundation-spec.md`. `evals/ANALYSIS.md` issued per-skill revision recommendations (2026-04-06), including the network-stewardship transactional-leak flag.

Gap: no status ledger records which ANALYSIS.md recommendations were acted on; the answer would come from diffing the skills against the recommendations or from Isaac's session log in stack-data.

## Workflows

Automated:

- `.github/workflows/deploy-architecture.yml`. Trigger: push to main touching `architecture/**` or the workflow file, plus workflow_dispatch. Deploys `architecture/` to GitHub Pages (the RP System Map). Permissions: contents read, pages write, id-token write. No secrets beyond the built-in GITHUB_TOKEN.
- launchd, `~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist`. Daily at 04:30 local, runs `scripts/check-vault-mirror-drift.sh --emit-vault-report`; on drift writes a dated note to the vault's `00 System/Maintenance/` and logs to `~/Library/Logs/toolkit-mirror-check.log`. Plist source unversioned (see the gap above).
- Git pre-commit hook, installed per clone from `scripts/hooks/pre-commit-vault-mirror-check.sh`. Chains the system-wide secret scanner with the vault-mirror drift detector.
- `.claude/settings.json` UserPromptSubmit hook, `phase-zero-trigger.sh`. Loads phase zero on the six trigger phrases and the retrospective on "log learnings", "retro this chat", or "session retrospective". Resolution order: `scripts/phase-zero`, then `PHASE-ZERO.md`, then `.claude/phase-zero.md`.

Manual:

- Kit redeploy. When: after any edit under `phase-zero/`. Command: `phase-zero/install.sh <repo>` or `--all <parent-dir>`. Good looks like byte-identical `.claude/` copies in all ten consumers.
- Per-machine global hook. When: setting up a new machine. Command: `phase-zero/global/install-global.sh`, optionally with `STACK_DATA_DIR=...`.
- Methodology sync. When: after editing any of the six vault canonicals. Command: `bash scripts/sync-methodology.sh`, then review `git diff methodology/` and commit. Good looks like only the intended snapshot changed and its "last synced" date updated.
- Drift scan on demand. Command: `scripts/check-vault-mirror-drift.sh`, exit 1 on findings. Two-outcome incident SOP in `docs/CANONICAL-STRUCTURE.md`.
- Pre-commit hook install on a clean checkout. Command per README: `cp scripts/hooks/pre-commit-vault-mirror-check.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit` (the hook header prefers `ln -sf`; see Known drift).
- CLI install. `cli/install.sh` symlinks rp-prospect, rp-pipeline, rp-update, rp-draft, and rp-followup into `/usr/local/bin`; README's Quick Start uses a PATH export instead (see Known drift).
- Skill eval loop. When: revising or adding a skill. Write evals.json, spawn with_skill and baseline subagents from a Claude Code session, run `python3 evals/lib/grade.py <skill>`, use a grader subagent for qualitative assertions, run `python3 evals/lib/benchmark.py <skill>`, edit the skill, run iteration N+1. Full workflow in `evals/README.md`.
- Session rituals. The six phase-zero trigger phrases open a session; "log learnings" closes it with the retrospective from the deployed kit.

## Known drift

Listed for Isaac to rule on; none is fixed by this doc.

- `evals/README.md` skills table lists 3 skills (2026-04-05); `evals/ANALYSIS.md` (2026-04-06) covers 19 and the tree holds 17 skill directories. ANALYSIS.md supersedes the table.
- `evals/RESUME_NEXT_SESSION.md` describes Batch A mid-run and B/C/D fixtures unwritten; all batches ran and were graded. The file describes a superseded state with no supersession note.
- `cli/install.sh` links five tools and omits rp-grant, while `README.md` lists rp-grant in the set and says install.sh installs in one step. README's Quick Start also uses a PATH export while install.sh uses /usr/local/bin symlinks; two install stories diverge.
- `README.md` line 81 says Alchemy became The Metabolizer; the alchemy repo states the reverse as of 2026-06-22 (Alchemy absorbs The Metabolizer).
- `docs/CANONICAL-STRUCTURE.md`'s legitimate-structure table lists entries absent from the current tree (Rubinstein Productions/, dist/, _archive/, research/arena/) and misses real ones (phase-zero/, architecture/, .claude/, .github/).
- `docs/CANONICAL-STRUCTURE.md` gives `cp` as the hook install method; the hook header gives `ln -sf` as primary.
- `CONTENT-STRATEGY.md` reads as current strategy but is an unreconciled April 2026 plan.
- `README.md`'s What's Inside omits phase-zero/, evals/, architecture/, context-provenance/, and seed-bed/; the map above covers the full tree.
