# Rubinstein Productions Toolkit

**A facilitation and measurement methodology, and the production tools that go with it, for solo practitioners who listen and film.**

Built by [Isaac Rubinstein](https://rubinsteinproductions.com) and published so other practitioners can take what is useful. The business that runs on it is not here; this is the reusable part.

Forks welcome. Pull requests are not reviewed or merged, so fork it and make it yours. See [`CONTRIBUTING.md`](CONTRIBUTING.md), and report security issues as [`SECURITY.md`](SECURITY.md) describes.

---

## What's inside

### `methodology/`
How the practice works and how to measure what it does. The method draws on polyvagal co-regulation theory, relational ontology and critical information ecology.

- **[Methodology Blueprint](methodology/methodology-blueprint.md)**: the four phases and how the other documents connect. Start here.
- **[Facilitation Protocol](methodology/facilitation-protocol.md)** and **[Session Facilitation Guide](methodology/session-facilitation-guide.md)**: nervous-system-aware, co-regulatory interviewing, phase by phase.
- **[Measurement Framework](methodology/measurement-framework.md)**: the Bilingual Dashboard, with Royal Metrics (institutional return) beside Nomadic Indicators (relational health and emergence).
- **[Measurement Instrument](methodology/measurement-instrument.md)** (draft): the framework's survey as a runnable instrument, with a response CSV, a scoring script and a worked example in `methodology/instrument/`.
- **[Nomadic Indicators Codebook](methodology/nomadic-indicators-codebook.md)**: a qualitative coding guide.
- **[Theory of Change](methodology/theory-of-change.md)** and **[Evaluation Framework](methodology/evaluation-framework.md)**: the logic model and assessment design for funded community work.
- **[Glossary](methodology/glossary.md)**, **[Source Tracking Protocol](methodology/source-tracking-protocol.md)**, **[Negative-Archive Ritual](methodology/negative-archive-ritual.md)**, **[Three-Type Architecture](methodology/three-type-architecture.md)** and **[Deliverable Outlines](methodology/deliverable-outlines.md)** (draft).

Three documents have a plain-language companion for readers with no evaluation background: `evaluation-framework-plain.md`, `theory-of-change-plain.md` and `enif-plain.md`. Every methodology document carries a status line that says how far to trust it.

### `templates/`
- [Case study template](templates/case-study-template.md), in the two registers of the Bilingual Dashboard.
- [Respondent sheet](templates/respondent-sheet.md), the measurement instrument's form.

### `production/`
Tools that work on anyone's footage, each with tests.

- **[Resolve MCP server](production/resolve-mcp.md)**: DaVinci Resolve as MCP tools for Claude Code. Reads, plus writes that are additive and show a plan first. A command-line script covers the read-only and offline commands.
- **[Resolve template spec](production/resolve-template-spec.md)**: the colour pipeline, PowerGrade template and deliver rules, including the [final-approval gate](production/resolve-template-spec.md#the-final-approval-gate).
- **[Capture standard](production/capture-standard.md)** (draft): frame rate, profile, audio, sync, slate and card naming per camera. The [capture brief](production/capture-brief.md) (draft) turns it into one page per camera kit for the shoot day.
- **[Delivery manifest](production/delivery-manifest.md)**, **[platform specs](production/platform-specs.md)**, **[credits and AI disclosure](production/credits-and-disclosure.md)**, **[consent evidence](production/consent-evidence.md)** and the **[return package](production/return-package.md)**: what ships, where to, what is said about how it was made, and what goes back to the people in it.
- **[`lutlab/`](production/lutlab/README.md)**: turns a look into a few numbers you can shift on purpose, and back into a LUT (`.cube` for Resolve, Hald CLUT PNG for stills). Six reference looks ship in `production/lutlab-looks/`.
- **[`rpstills/`](production/rpstills/README.md)**: index, cluster and cull a shoot's camera JPEGs, then build a review sheet. Faces come from macOS Vision when `swiftc` is present. Optional back half without Resolve: RAW develop with a per-frame skin solve, crops and opt-in retouch, a report-only audit, an A/B/C decision sheet and a byte-range file server for review on a phone or iPad.
- **[Media lane](production/media-lane.md)**: one page over colour, edit and sound: what each tool does, how a review with Claude Code runs, and what is still a hand step.

### `tools/`
- **[`vertcut/`](tools/vertcut/README.md)**: turns a long recording into 9:16 captioned pull-clips with two commands, `transcribe` and `cut`.
- **[`video-editor/`](tools/video-editor/README.md)**: a bounded prototype that drives video editing through Claude Code, wrapping the third-party `video-use` skill (Browser Use, MIT), which `fetch-video-use.sh` fetches at a pinned commit. Not a finished tool.
- **[`ipad-probe/`](tools/ipad-probe/README.md)**: a POSIX `sh` probe of what an iPad's a-Shell ffmpeg, ffprobe and python3 can do, printed as one Markdown table, plus four recipes: verified card copy, proxy, two-pass loudness and contact sheet. Runs under dash.

### `prompts/`
- **[`skills/`](prompts/skills/)**: Claude Code skill files. Branded deck and document builds that match a template, a prompting pattern library, and agentic-development notes. The last two are October 2025 snapshots and say so at the top.
- **[Repo atlas](prompts/repo-atlas.md)**: a ready-to-run prompt that maps how a set of repositories connect and which ones to thin down.

### `docs/`
- **[Field Guide](docs/field-guide/index.md)**: a reading order and a first working session for writers, producers and evaluators. Also published as the repository's GitHub Pages site.
- **[Deliverable portability](docs/deliverable-portability.md)** (draft): how to keep document and deck quality when the model changes.
- **[Fork guide](docs/fork-guide.md)** (draft): for someone who forks this repo. What to keep or drop, how to supply your own deployment list for the kits, the licence split, the permission rules, and what assumes macOS or Resolve.
- **[iPad lane](docs/ipad-lane.md)** (draft): what an iPad can do with no Mac at each stage from ingest to QC, what is verified, and the one-hour on-device probe that settles the rest.

### `scripts/`
The doc-link checker, the methodology status check, the site builder, `burst-gate` (mechanical gates for a worker branch) and `changelog-fragments` (one release-note fragment per pull request in [`changelog.d/`](changelog.d/README.md), compiled into [`CHANGELOG.md`](CHANGELOG.md) at tag time), each with a test script.

### `phase-zero/` and `public-kit/`
Two deployment kits, exported here from a private source. `phase-zero/` is session infrastructure for AI agents (hooks, an operating brief, model routing, a retrospective) that installs into a repository's `.claude/`. `public-kit/` is public-repository hygiene: license templates, a README shape, `CONTRIBUTING` and `SECURITY` templates, and public voice rules. Fork them freely; fixes sent here are not merged back by hand.

### `requirements.txt`
Each tool's interpreter, the pinned Python packages, and a table of every environment variable the tools read. CI installs from it.

---

## Quick start

- **Read the method:** start at the [Methodology Blueprint](methodology/methodology-blueprint.md), or let the [Field Guide](docs/field-guide/index.md) pick a reading order for your role.
- **Use a skill:** copy a file from `prompts/skills/` into your `.claude/skills/<name>/SKILL.md`.
- **Cut clips:** `tools/vertcut/vertcut transcribe <recording>`, pick spans, then `tools/vertcut/vertcut cut <recording> clips.tsv`, as its README shows.

### Testing
The production tests run on Homebrew Python 3.14 in a venv outside the repository. The Resolve MCP server stays on Xcode's Python 3.9 and has its own CI job. From the repository root:

```
/opt/homebrew/bin/python3.14 -m venv ~/.venvs/rp-tools
~/.venvs/rp-tools/bin/python -m pip install -r requirements.txt
~/.venvs/rp-tools/bin/python -m unittest $(ls production/tests/test_*.py | sed 's#/#.#; s#/#.#; s#\.py$##')
```

Off a Homebrew Mac, point the tools at your ffmpeg and skip the one test that compares colour against Homebrew's build:

```
export RPRESOLVE_FFMPEG="$(command -v ffmpeg)" RPRESOLVE_FFPROBE="$(command -v ffprobe)" LUTLAB_SKIP_RENDER_VERIFY=1
```

---

## Who this is for

- Facilitators and solo consultants who want to see how someone else built their practice's infrastructure.
- Evaluators and nonprofit staff looking for a framework that measures relational quality next to deliverables.
- Documentary filmmakers working with participant-led production, and anyone automating DaVinci Resolve.
- People building an AI-assisted practice who want to see how skills and methodology connect.

---

## License

Code (`scripts/`, `production/`, `tools/`, `phase-zero/`, `public-kit/`) is MIT, in [`LICENSE`](LICENSE). Everything else, including the methodology, docs, templates and prompts, is CC BY-SA 4.0, in [`LICENSE-CONTENT`](LICENSE-CONTENT): adapt it, and keep your adaptation as open as the original. No data of consequence lives here; if some ever does, it is CC0.

---

*Isaac Rubinstein, [Rubinstein Productions](https://rubinsteinproductions.com)*
