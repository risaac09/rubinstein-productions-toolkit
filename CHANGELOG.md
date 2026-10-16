# Changelog

What changed in this repo, newest first. There are no version numbers, tags or releases yet, so entries are dated and carry the pull request when there is one. Consumers who need to pin a state can pin a commit. The phase-zero and public-kit exports are listed once by their `Export kit from stack-data <sha>` commits and not itemized here. History before 2026-08 is in `git log`.

Written 2026-10-15 from the git history. Add an entry in the pull request that makes the change.

## 2026-10-16

- Added `production/client-shooting-brief.md` (draft), a two-page brief written to the client for the camera-shipping model, which settles Amendment 2, item 15.
- Added `production/consent-evidence.md` (draft): the one artifact that proves each consent tier, checklists for appearance, location and talent releases, and a per-engagement licence register for music and fonts. The case-study template has a required "Consent evidence" line and a rule that a case study with no tier 3 evidence id is not published. Amendment 2, item 18.
- Added `production/capture-standard.md` (draft), settling Amendment 2, item 14: frame rate, profile, resolution, audio, sync, slate and card naming per camera. The March 31 shoot plan moved to `production/examples/` and `production/filming-guide.md` is now a pointer.
- The `rp-*` prospect and grant CLI is retired, which settles Amendment 2, item 12: `cli/`, its four templates, the smoke test and the CLI text in the field guide are removed, and the CI job that ran the smoke test is now `hygiene-checks`. The record for prospects and funders is the private stack-data repo. The last commit that carries the tools is `2ffe87a`. `docs/TRUTH-OWNERSHIP.md` gained a "Who owns which job" table.
- Measurement framework section 4 rewritten around plain-file records (engagement, session, response, outcome, asset use) with Airtable kept as an unbuilt alternative. The edit was made in the corpus canonical and synced here.
- Added `methodology/measurement-instrument.md` (draft), `methodology/instrument/` (response CSV example, expected output, `score.sh`) and `scripts/test-instrument-score.sh`, run in CI.
- ROADMAP Amendment 2, items 10 and 11, carry status lines.
- Drafted the producer gaps from Amendment 2, items 13 and 20: `production/camera-shipping-logistics.md`, `production/delivery-manifest.md`, `production/platform-specs.md`, `production/credits-and-disclosure.md`, and a final-approval gate in the Resolve spec's Deliver section. All are marked draft and end in a "Needs Isaac" list.

## 2026-10-15

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

## 2026-10-07 to 2026-10-12

- Resolve MCP: `stabilize`, `relink`, `set_color_management` and `timeline_from_clips` (#114, #115, #117). `queue_render` takes a delivery destination only and the four legacy render presets are gone (#110). The write commands in `resolve_workflow.py` are retired, so the MCP server is the only write path (#109). The settings guard is opt-in.
- rpstills: offline index, cluster, cull and review sheet for stills (#107).
- The kit source moved to the private stack-data repo; this repo carries an export (2026-10-07).
- Translation phase aligned to the canonical positioning (#122). Methodology snapshots made single-source, with `sync-methodology.sh --check` (#113).

## 2026-09-26 to 2026-10-01

- The Resolve MCP server, built in eight steps: shared library and lock, core and `resolve_status`, offline tools, read tools, the first write tools, the remaining writes, and the docs with a live sandbox check (#70 to #77).
- rpresolve: survey of Resolve projects on disk, per-clip grades with read-back, camera detection, ingest, measure, cut manifests and endcheck (#64 to #69).
- Deliver: destination presets, house file names and a check of every rendered file (#85 to #87). Captions by frame shape and a TTML-to-SRT sidecar (#88, #92). Face-centred 9:16 and 1:1 reframes (#90). Dual-system sound sync and trim review (#89, #91).
- The field guide went live as the site's front door (2026-10-01).

## 2026-08 to 2026-09

- vertcut: a vertical-clip workflow, two commands (2026-09-04).
- The toolkit moved off the iCloud vault (#56).
- Pricing, the business plan and machine internals removed from the public repo (#51, #52).
- public-kit and dsh added (2026-08-21, 2026-08-22). README and CLAUDE.md rewritten for the narrower scope; methodology relicensed CC BY-SA 4.0.
- Branded deck and document build skills added, visible to Codex through `.agents/skills/` (#44, #45).
