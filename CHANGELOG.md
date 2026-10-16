# Changelog

What changed in this repo, newest first. Each entry carries the pull request when there is one.

## Versions

Versions are `vMAJOR.MINOR.PATCH`. MINOR goes up for a new document or tool. PATCH goes up for fixes and wording. A removed or changed tool also raises MINOR while the major number is 0, and MAJOR after 1.0. While the major number is 0, shapes may still move.

The first tag will be `v0.1.0`, the state after the open Amendment 2 pull requests merge. Until that tag exists, consumers who need a fixed state pin a commit. The maintainer creates each tag after merge; a pull request never does.

Each pull request adds its entry under `## Unreleased`. At tag time the maintainer renames that heading to the version and date, and a new empty `## Unreleased` starts above it. The phase-zero and public-kit exports are listed once by their `Export kit from stack-data <sha>` commits and not itemized here. History before 2026-08 is in `git log`.

The Resolve spec keeps its own document revision in its footer. It is not the repository's version.

## Unreleased

- ROADMAP gains Amendment 3, a second cross-discipline audit with a fifth reader, the client: 29 verified gaps, filed and not yet fixed. (#157)
- Sibling documents from Amendment 2, items 14 to 19, cite each other as relative links, including the Colour Pipeline of Record section of the Resolve spec, and the 2026-10-16 list reads newest first in merge order with no duplicate bullets (#155).
- Untracked `tools/video-editor/video-use/` (third-party Browser Use `video-use`, MIT), which settles Amendment 2, item 3. `tools/video-editor/fetch-video-use.sh` fetches it at the pinned commit. `motion/` and the video-editor README stay. History still carries the files, so the clone size is unchanged. `.gitignore` now ignores `tools/video-editor/video-use/` instead of the whole `tools/video-editor/` tree.
- CI now runs shellcheck at warning level over the first-party shell outside phase-zero. The file list comes from the tracked files, so new scripts are picked up. Three scripts needed `cd ... || exit`; behavior is unchanged. Review item 5 remainder.
- The Likert anchors decided 2026-10-16 (1 "strongly disagree", 5 "strongly agree", middle unlabeled) are in the corpus canonical measurement framework and synced here; the other methodology snapshots carry the new sync date only.
- Added the version scheme and this changelog shape (Amendment 2, item 8). The Resolve spec's footer now says "Document revision" so it is not mistaken for the repository's version. ROADMAP item 12 records that the `cli-final` tag exists. (#148)

### 2026-10-16

- Added `production/spikes/colour-space-api-spike.md`, a plan for a sandbox spike on whether the scripting API can set the timeline and output colour spaces and the tone and gamut mapping, so that `set_color_management` could set more than `colorScienceMode`. It records what Resolve's README and type stubs confirm and leave unconfirmed, and changes no tool behaviour. Linked from `production/resolve-mcp.md`.
- Promoted the public-kit templates to the repo root as `CONTRIBUTING.md` and `SECURITY.md`, which settles the second half of Amendment 2, item 4 ([#150](https://github.com/risaac09/rubinstein-productions-toolkit/pull/150)). Pull requests stay unreviewed, issues are read, and security reports go through private vulnerability reporting only.
- The final-approval gate in the Resolve spec is accepted and its open points are settled: both the participant and a separate commissioner must approve, no round count in the document (each scope writes `R`), a SHA-256 in the approval message, no standalone verbal approval, and queueing without a record flagged, not blocked. ROADMAP Amendment 2, item 20, carries the status line.
- vertcut's captions now follow the caption standard in `production/design-tokens.md`: a bar at 88% opacity instead of the outline, text and bar colours and the face from options or an untracked `captions.local.json`, 16 characters a line and two lines at most, and `--caption-y` as the bar's bottom edge. The bar opacity is decided. Amendment 2, item 17.
- Added `production/design-tokens.md` (draft), which settles Amendment 2, item 17 as far as the public repo can: token names without values, lower-third geometry as fractions of frame size, one caption standard, how vertcut's captions differ (follow-up open), and a look-book stub. The Resolve spec's colour names now use token names.
- Added `production/consent-evidence.md` (draft): the one artifact that proves each consent tier, checklists for appearance, location and talent releases, and a per-engagement licence register for music and fonts. The case-study template has a required "Consent evidence" line and a rule that a case study with no tier 3 evidence id is not published. Amendment 2, item 18.
- Added `production/return-package.md` (draft), the return-package spec and folder standard, which settles Amendment 2, item 19: contents, medium, `<YYYY-MM>_<engagement-id>/` layout, SHA-256 checksums with make and verify commands, client confirmation and RP's 90-day retention. Linked from the README and the producers guide.
- The Resolve spec's colour section is now the Colour Pipeline of Record (Amendment 2, item 16): Resolve Color Management v2 on a DaVinci Wide Gamut timeline, one table per camera, lutlab's `[RCM]` looks as the shared look, node-1 camera LUTs kept as the unmanaged alternative. Its two colour Open Questions are closed.
- Added `production/client-shooting-brief.md` (draft), a two-page brief written to the client for the camera-shipping model, which settles Amendment 2, item 15.
- Applied Isaac's answers to the camera-shipping, delivery-manifest, platform-specs and credits drafts and the measurement-instrument open points (#145). The delivery standards are accepted, not drafts.
- Added `production/capture-standard.md` (draft), settling Amendment 2, item 14: frame rate, profile, resolution, audio, sync, slate and card naming per camera (#139). The March 31 shoot plan moved to `production/examples/` and `production/filming-guide.md` is now a pointer.
- The `rp-*` prospect and grant CLI is retired, which settles Amendment 2, item 12 (#138): `cli/`, its four templates, the smoke test and the CLI text in the field guide are removed, and the CI job that ran the smoke test is now `hygiene-checks`. The record for prospects and funders is the private stack-data repo. The last commit that carries the tools is `2ffe87a`. `docs/TRUTH-OWNERSHIP.md` gained a "Who owns which job" table.
- Drafted the producer gaps from Amendment 2, items 13 and 20: `production/camera-shipping-logistics.md`, `production/delivery-manifest.md`, `production/platform-specs.md`, `production/credits-and-disclosure.md`, and a final-approval gate in the Resolve spec's Deliver section (#135). All were drafted with a "Needs Isaac" list.
- Measurement framework section 4 rewritten around plain-file records (engagement, session, response, outcome, asset use) with Airtable kept as an unbuilt alternative. The edit was made in the corpus canonical and synced here (#136).
- Added `methodology/measurement-instrument.md` (draft), `methodology/instrument/` (response CSV example, expected output, `score.sh`) and `scripts/test-instrument-score.sh`, run in CI (#136).
- ROADMAP Amendment 2, items 10 and 11, carry status lines.

### 2026-10-15

- Added `requirements.txt`: each tool's interpreter and pinned packages in one file.
- CI now runs all 34 production test modules on Python 3.9 with those packages, and `resolve_survey` in full on 3.14. `test_resolve_survey` skips per test instead of aborting a batched run.
- `rp-draft` encodes the subject, address and body safely, requires `python3`, and reads its signature from `~/.config/rp/signature`. The smoke test gained eight cases for it.
- README "What's Inside" covers lutlab, rpstills, `tools/`, `dsh/` and `seed-bed/`.
- Deleted the two stale `.skill` bundles; `prompts/skills/<name>.md` is the one source for each skill.
- Every methodology document carries a `**Status:**` line, and CI checks it. The blueprint's dated claim about the first camera-shipping journey is marked as of April 2026.
- Added `docs/TRUTH-OWNERSHIP.md`, the index of which repo or note owns each domain.
- ROADMAP gained Amendment 2, the cross-discipline gap audit.
- lutlab: Pyxis and S5II spaces, port, looks, RCM set, library, diff, apply, verify-render, an owner brief and an Ice Blue that holds skin (#126 to #129).
- reframe_plan and rpstills: derived scale, dark scenes kept, subject steering, frame ranking when no face is found (#124).

### 2026-10-07 to 2026-10-12

- Resolve MCP: `stabilize`, `relink`, `set_color_management` and `timeline_from_clips` (#114, #115, #117). `queue_render` takes a delivery destination only and the four legacy render presets are gone (#110). The write commands in `resolve_workflow.py` are retired, so the MCP server is the only write path (#109). The settings guard is opt-in.
- rpstills: offline index, cluster, cull and review sheet for stills (#107).
- The kit source moved to the private stack-data repo; this repo carries an export (2026-10-07).
- Translation phase aligned to the canonical positioning (#122). Methodology snapshots made single-source, with `sync-methodology.sh --check` (#113).

### 2026-09-26 to 2026-10-01

- The Resolve MCP server, built in eight steps: shared library and lock, core and `resolve_status`, offline tools, read tools, the first write tools, the remaining writes, and the docs with a live sandbox check (#70 to #77).
- rpresolve: survey of Resolve projects on disk, per-clip grades with read-back, camera detection, ingest, measure, cut manifests and endcheck (#64 to #69).
- Deliver: destination presets, house file names and a check of every rendered file (#85 to #87). Captions by frame shape and a TTML-to-SRT sidecar (#88, #92). Face-centred 9:16 and 1:1 reframes (#90). Dual-system sound sync and trim review (#89, #91).
- The field guide went live as the site's front door (2026-10-01).

### 2026-08 to 2026-09

- vertcut: a vertical-clip workflow, two commands (2026-09-04).
- The toolkit moved off the iCloud vault (#56).
- Pricing, the business plan and machine internals removed from the public repo (#51, #52).
- public-kit and dsh added (2026-08-21, 2026-08-22). README and CLAUDE.md rewritten for the narrower scope; methodology relicensed CC BY-SA 4.0.
- Branded deck and document build skills added, visible to Codex through `.agents/skills/` (#44, #45).
