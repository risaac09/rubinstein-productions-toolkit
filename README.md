# Rubinstein Productions Toolkit

**Public methodology, evaluation frameworks, prompt-stack tooling, and deployment kits for AI-assisted solo practices.**

Built and used by [Isaac Rubinstein](https://rubinsteinproductions.com) to run his own facilitation-and-film practice, and published because the kit mechanism — not the business behind it — is the useful, reusable part.

Forks welcome, pull requests not reviewed or merged: this is a solo practitioner's toolkit, so fork it and make it yours rather than waiting on me. See [`CONTRIBUTING.md`](CONTRIBUTING.md), and report security issues as [`SECURITY.md`](SECURITY.md) describes.

---

## What This Is

A kit, not a company. This repo carries the methodology, the evaluation frameworks, the AI prompt/skill stack, and the deployment mechanism (`phase-zero/`, `public-kit/`) that make a solo AI-assisted practice legible and replicable. It does not carry the practice itself — strategy, pricing internals, brand voice, grant pipeline, and client-facing production tooling live in a private operations repo, `rp-intranet`, and stay there.

The methodology draws on polyvagal co-regulation theory, relational ontology, and critical information ecology. At the practice level it's careful listening and honest expression; here, it's the frameworks and tools that operationalize that.

---

## What's Inside

### `methodology/`
The intellectual core: how the practice works, why it works, and how to measure what it does.

- **Facilitation Protocol**: Nervous-system aware, co-regulatory interview methodology
- **Measurement Framework**: The "Bilingual Dashboard": Royal Metrics (institutional ROI) alongside Nomadic Indicators (relational health and emergence). Formerly ENIF; consolidated April 2026.
- **Measurement Instrument** (draft): The framework's survey items as a runnable instrument: response CSV, scoring procedure, worked example and a small scoring script in `methodology/instrument/`
- **Nomadic Indicators Codebook**: Qualitative coding guide for tracking transformation
- **Session Facilitation Guide**: Phase-by-phase guide for facilitated documentary sessions
- **Theory of Change**: Logic model for grant-funded community applications (grant-facing, maps methodology to funder evaluation frameworks)
- **Evaluation Framework**: Assessment design using the Bilingual Dashboard

Three core docs carry a plain-language companion for readers with no evaluation background: `evaluation-framework-plain.md`, `theory-of-change-plain.md`, and `enif-plain.md`. The canonical doc stays the source of truth; each companion is the teaching layer and ends with what the framework does not claim.

Licensed CC BY-SA 4.0 (practice-writing, not code — see License below).

### `templates/`
Reusable templates for the client lifecycle.

- Client onboarding template
- Case study template

### `docs/`, `architecture/`
Internal documentation and a system-map artifact. `docs/field-guide/` routes readers by role. [`docs/TRUTH-OWNERSHIP.md`](docs/TRUTH-OWNERSHIP.md) is the one-page index of which repo or note owns each domain, and what this repo only exports.

The skill-eval harnesses moved to the private `rp-intranet` repo on 2026-08-23: their prompts and fixtures are real RP business scenarios carrying balances, revenue targets, pricing bands, and a client engagement, which is the same material this README says stays private.

### `scripts/`
Generic automation: the doc-link checker, the vault/toolkit boundary drift detector, the pre-commit hook chain, and the methodology sync script. Nothing RP-specific.

### `phase-zero/`
The deployment kit for AI-agent session infrastructure (hooks, operating brief, model routing, retrospective, settings), deployed byte-identical into consuming repos' `.claude/` directories via `phase-zero/install.sh`. See that file's `CONSUMERS` allowlist for who's on it. This directory is an exported mirror: the source lives in a private repo beside the decisions and tests that warrant it, and every change here arrives as an `Export kit from stack-data` commit. Fork it freely; fixes sent here are not merged back by hand.

### `public-kit/`
A second, smaller deployment kit for public-repo hygiene: license templates, a README-shape standard, `CONTRIBUTING.md`/`SECURITY.md` templates, and the canonical public-facing voice rules. Deployed independently of `phase-zero/` by the `--public` mode of the shared installer, `phase-zero/install.sh`, to repos that are actually public. Exported from the same private source as `phase-zero/`. A repo can take either kit, both, or neither.

### `prompts/`
What remains of the prompt stack after the brand/identity/voice prompts moved to `rp-intranet` (they were positioning and business voice, not reusable methodology): agent orchestration notes, a repo atlas, session handoff notes, and `prompts/skills/` — the Cowork/Claude Code skill files covering outreach, project management, agentic development, and idea-to-pilot work.

### `production/`
Generic film/video production tooling. *(Kept as a default — see below.)*

- DaVinci Resolve command-line script: read-only and offline commands only
- [Resolve MCP server](production/resolve-mcp.md): the only write path to Resolve, as MCP tools for Claude Code, reads plus additive, plan-first writes
- PowerGrade template spec
- [Capture standard](production/capture-standard.md): frame rate, profile, audio, sync, slate and card naming per camera, with an [example shoot plan](production/examples/2026-03-31-shoot-plan.md) (draft)
- [Camera-shipping logistics](production/camera-shipping-logistics.md): the shipment lifecycle, kit and backup-gear checklist, return, and the insurance questions still open (draft)
- [Client shooting brief](production/client-shooting-brief.md): a two-page brief for the person filming, to print and put in the box (draft)
- [Design tokens](production/design-tokens.md): token names, lower-third geometry, the caption standard and a look-book stub; values stay private (draft)
- [Delivery manifest](production/delivery-manifest.md), [platform specs](production/platform-specs.md) and [credits and AI disclosure](production/credits-and-disclosure.md): what ships, to where, and what is said about how it was made (accepted 2026-10-16); the [return package](production/return-package.md) sets what comes back to the client and how it is checked. The client final-approval gate they depend on is in the [Resolve spec](production/resolve-template-spec.md#the-final-approval-gate)

### `production/lutlab/`
Turns a look into a few numbers you can shift on purpose, and back into a LUT. A look comes from a `.cube` or from image pairs (a RAW and the camera's own JPEG, or a log frame and its graded frame), and rebuilds as a `.cube` for Resolve and a Hald CLUT PNG for stills tools. Ships six reference looks in `production/lutlab-looks/`, a port between camera spaces, and a verify-render check. Python 3.9 with numpy, scipy, Pillow and OpenCV; see [its README](production/lutlab/README.md).

### `production/rpstills/`
The offline front end of the stills lane: index, cluster and cull a shoot's camera JPEGs, then build a review sheet. It decodes no RAW and talks to no Resolve; grading stays in Resolve. Faces come from macOS Vision when `swiftc` is present, and the ranking falls back to the frame alone when it is not. Look presets are in `production/stills-looks/`. See [its README](production/rpstills/README.md).

### `tools/`
- **`tools/vertcut/`**: turns a long recording into 9:16 captioned pull-clips with two commands, `transcribe` and `cut`. Bash plus one small Python file; ffmpeg, Pillow and `mlx-whisper` do the work. See [its README](tools/vertcut/README.md).
- **`tools/video-editor/`**: a bounded prototype that drives video editing through Claude Code. The Remotion lower-third in `motion/` and the notes in its [README](tools/video-editor/README.md) are Rubinstein Productions work. `video-use`, the editing skill the notes wrap, is third-party (Browser Use, MIT). It is not stored in this repo; `tools/video-editor/fetch-video-use.sh` fetches it at a pinned commit. Not a finished tool.

### `dsh/`
The deployment kit for a DeepSeek Harness agent on the OpenRouter lane: the session brief, the model routing table for that lane, and an installer that keeps the shared skill home in step with `prompts/skills/`. It is the counterpart to `phase-zero/`, which targets Claude Code. See [its README](dsh/README.md).

### `seed-bed/`
One skill, `seed-bed/SKILL.md`: an incubation space for ideas that are not yet a service, tool or practice. It holds an idea without forcing a category and brings it back on a weekly cadence.

### `requirements.txt`
Each tool's interpreter, the pinned Python packages, and a table of every environment override the tools read, in one file. CI installs from it. The commands that run the tests are under [Testing](#testing).

### `research/`
What's left after the position papers and grant-concept research moved to `stack-data`: the 64-registers thesis. (The Digital Liver offering doc is business strategy with revenue models, so it lives in the private `rp-intranet` repo.)

### Apps (external repos)
Web-based tools, each developed in its own repository. There is no `apps/` directory in this tree.

- **[Alchemy](https://github.com/risaac09/alchemy)**: Digital Liver app. Capture → Reflect → Release. The lineage that became **The Metabolizer**, the vault product.
- **Royal Metrics**: ENIF business performance dashboard (lives at `three-type-evaluation/instrument/` since 2026-07-20, standalone repo archived; the measurement framework it implements is in `methodology/`)
- **RP Lifecycle**: Videography project lifecycle manager (repo archived 2026-07; the 10-phase template it encoded lives on in stack-data's project schema)

---

## What's Not Here (and why)

This repo's own `CLAUDE.md` used to claim "nothing private lands here." An audit in August 2026 found that wasn't true, and this reorganization is the fix:

- **Grant strategy, brand/creative-strategy prompts, the identity-instantiation prompt, the content strategy, and RP-specific production tooling** (`badwords-rp/`) moved to `rp-intranet`, a private operations repo. They were business strategy and voice/positioning material, not reusable methodology.
- **Position papers and grant-concept research** moved to `stack-data`, alongside near-identical or adjacent material already living there.
- **Duplicate Total Cost of Ownership methodology files** were deleted outright; `stack-data/context/tco-framework.md` is the canonical version now.
- **Context Provenance** — the self-contained AI-attribution and provenance tool that happened to live in this tree now has its own repository: **[`risaac09/context-provenance`](https://github.com/risaac09/context-provenance)**. Its original subtree history moved with it.

A second pass on 2026-08-23 caught what the first one missed. The August reorg scoped itself to `prompts/`, `research/`, `grants/`, and `production/`, so these were never in range:

- **The skill-eval harnesses** (`evals/`) moved to `rp-intranet`. Their prompts read as synthetic but are real business scenarios: account balances, rent, monthly burn, a quarterly revenue target, a named client engagement, and the price-anchoring script with the floor and ceiling stated outright.
- **Service-tier pricing, revenue projections, unit economics, and the go-to-market plan** moved to `rp-intranet`. The projection table sat directly beneath an assumptions block naming the employer.
- **The Digital Liver offering doc and the RP COO skill** moved to `rp-intranet`. Both are business strategy carrying rates and financial models.
- **A verbatim snapshot of one machine's Claude permission allow list** was removed. Publishing it published the exact set of commands that run with no approval prompt on that machine. The audit script reads a machine-local baseline now.
- **The autoMode environment block** shipped generic. It had named every private repo and then described the privacy scanner's known blind spot, which is a target list sitting next to the gap that reaches it. Machine specifics come from a local overlay the installer merges last.

History still holds all of it. Scrubbing that is a separate decision.

One removal came later, on 2026-10-16: the `rp-*` prospect and grant command-line tools (`cli/`, its four templates and its smoke test). They tracked the same prospects and funders as the private stack-data repo, and a fact should have one home, so the record is stack-data and the tools went. The last commit that carries them is `2ffe87a`; take them from git history if you want a Markdown-file tracker to start from.

### A judgment call

Most of `production/` (everything except `badwords-rp/`, which moved) is kept here by default, not because that's a settled decision. The toolkit's own content strategy argues for keeping tooling visible as proof a solo practitioner can build real infrastructure, and the directory carries no names, numbers, or strategy — but this default hasn't been reviewed against the same bar as the rest of the reorg. Flagging it explicitly so it can be overridden.

The same flag once sat on `cli/`, the prospect and grant command-line tools. It was settled on 2026-10-16: the pipeline record is the private stack-data repo, a second tracker for the same facts was the problem, and the tools were retired. See the paragraph above and [`docs/TRUTH-OWNERSHIP.md`](docs/TRUTH-OWNERSHIP.md).

---

## License

Three lanes, matching the rest of this practice's repos:

- **Code and tooling** (`scripts/`, `production/resolve_workflow.py`, the `phase-zero/` and `public-kit/` mechanisms themselves) — **MIT**. Use it, fork it, adapt it.
- **Methodology, protocols, and practice-writing** (`methodology/`, `templates/`, `docs/`) — **CC BY-SA 4.0**. Share and adapt, with attribution, alike.
- **Data** — none of consequence lives in this repo; where it would, it's CC0.

See [`LICENSE`](LICENSE) for the code license and scope note, [`LICENSE-CONTENT`](LICENSE-CONTENT) for the methodology license, and `public-kit/` for the templates this policy is built from. Contribution terms are in [`CONTRIBUTING.md`](CONTRIBUTING.md), and the reporting channel for security issues is in [`SECURITY.md`](SECURITY.md).

---

## Quick Start

New here? The **[Field Guide](docs/field-guide/index.md)** routes writers, producers, and evaluators to a reading order and a first working session.

### Prompt Stack
The `prompts/skills/` directory contains skill files, one Markdown file per skill and the only copy of each, designed for [Claude Code](https://claude.ai/claude-code) / Cowork. Drop them into your `.claude/skills/` directory or adapt the methodology for your own AI workflow.

### Testing
The production tests run on the system Python 3.9. Install the pinned packages, then run every module in `production/tests` from the repository root, as CI does:

```
/usr/bin/python3 -m pip install -r requirements.txt
/usr/bin/python3 -m unittest $(ls production/tests/test_*.py | sed 's#/#.#; s#/#.#; s#\.py$##')
```

On a Homebrew Mac that is all. Anywhere else, point the tools at your ffmpeg and ffprobe, and skip the one test that compares colour against Homebrew's ffmpeg build:

```
export RPRESOLVE_FFMPEG="$(command -v ffmpeg)" RPRESOLVE_FFPROBE="$(command -v ffprobe)" LUTLAB_SKIP_RENDER_VERIFY=1
```

The table at the top of [`requirements.txt`](requirements.txt) lists the other variables. The survey tests need Python 3.14 and skip on 3.9.

### Methodology
Start with `methodology/facilitation-protocol.md` for the session-level practice, then `methodology/measurement-framework.md` for the measurement system.

---

## Who This Is For

The **[Field Guide](docs/field-guide/index.md)** gives each audience a reading order and a first working session:

- **[Writers](docs/field-guide/writers.md)**: the voice system, the question bank that generates client language, the editorial disciplines
- **[Producers](docs/field-guide/producers.md)**: the session methodology, the client lifecycle from prospect to case study, shoot and post workflow
- **[Evaluators](docs/field-guide/evaluators.md)**: the Bilingual Dashboard, the coding manual, the grant pipeline

If none of those labels fit, you may still be at home here:

- Solo consultants and facilitators who want to see how someone else built their practice infrastructure
- Nonprofit professionals looking for evaluation frameworks that measure relational quality alongside deliverables
- Documentary filmmakers curious about participant-led production models
- Anyone building an AI-assisted practice stack who wants to see how prompt engineering connects to methodology, or who wants a public-repo hygiene kit (`public-kit/`) they can deploy to their own projects

---

## Canonical structure

This repo and the Obsidian vault are separate stacks. Directory names like `02 Practice/`, `03 Projects/`, `00 System/` are reserved for the vault and must never appear inside the toolkit root or anywhere below it (except under `_archive/`). The full forbidden list, the regex, and the rationale are in `docs/CANONICAL-STRUCTURE.md`.

Three guards enforce this:

- **Drift detector** — `scripts/check-vault-mirror-drift.sh`. Pure bash. Run anytime.
- **Pre-commit hook** — `scripts/hooks/pre-commit-vault-mirror-check.sh`. Chains the secret scanner and the drift detector. Install on a clean checkout with `cp scripts/hooks/pre-commit-vault-mirror-check.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.
- **Daily detector** — `~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist`. Runs at 04:30 daily, writes a dated note to the vault on drift. The pre-commit hook only fires on git commits; the daily run catches misroutes from other automation.

---

*Isaac Rubinstein, [Rubinstein Productions](https://rubinsteinproductions.com)*
*Seattle, WA*
