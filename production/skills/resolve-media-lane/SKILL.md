---
name: resolve-media-lane
description: 'Route media-lane work (stills, colour, edit, sound, card offload, DaVinci Resolve) to the right tool in this toolkit and keep its safety rules. Use for "develop these RAWs", "build a LUT or a shot-match", "balance the shots", "mix this dialogue", "export an EDL or FCPXML", "offload this card", or any Resolve MCP tool call. Not for generated video or for client sends.'
---

# Resolve media lane

Pick the tool, run it the safe way, point at the doc. This skill does not restate the docs: when a flag or a number matters, open the doc named below. Paths are relative to the toolkit's `production/` folder.

## Route the task

| The task | Tool | Read |
|---|---|---|
| Copy a camera card and prove the copy | `offload.sh` (`id`, `copy`, `safe`) | the header of `offload.sh` |
| RAW stills: develop, finish, audit, A/B sheet | `python -m rpstills` | `rpstills-develop.md`, `rpstills-finish.md`, `rpstills-audit.md` |
| Colour: camera log to a working space, per-shot balance, looks, an iPad LUT pack | `python -m lutlab` (`cst`, `balance`, `shot-match`, `pack`) | `lutlab/README.md`, `lutlab-cst.md`, `lutlab-balance.md` |
| Sound: room tone, ducking, metrics, a mix | `rpresolve.roomtone`, `duckplan`, `dxmetrics`, `mixbuild` | `sound-assist.md`, `media-lane-quickstart.md` |
| Cut files for another editor (EDL, FCPXML, OTIO) | `rpresolve.interchange` | `interchange.md`, `manifest.md` |
| Edit assists and checks (word snap, shots, beats, captions lint, pacing, best take) | `rpresolve.wordsnap`, `shots`, `beats`, `captionlint`, `pacing`, `besttake` | `edit-assist.md`, `besttake.md` |
| Anything inside DaVinci Resolve | the Resolve MCP tools below | `resolve-mcp.md` |
| A colour, cut or mix choice that needs a person's eye or ear | stop and use the review loop | `media-review-loop.md` |

The whole lane in order, and what stays a hand step: `media-lane.md`. A copy-paste first run: `media-lane-quickstart.md`.

Read-only checks also run as `resolve_workflow.py` commands, one module each in `production/rpresolve/`: `cmd_detect`, `cmd_measure`, `cmd_endcheck`, `cmd_selects`, `cmd_deliver_check`, `cmd_qc`, `cmd_pacing`, `cmd_besttake`, `cmd_captionlint`, `cmd_interchange`. List them with `resolve_workflow.py --help`.

## MCP tools

Call `resolve_status` first; Resolve is edited live by a person, so never assume which project is open.

Reads, which change nothing: `resolve_status`, `list_timelines`, `timeline_items`, `media_pool`, `render_queue_status`.

Offline, which never connect to Resolve and take absolute paths: `detect`, `survey`, `measure`, `endcheck`, `selects`, `reframe_plan`, `deliver_check`, `deliver_captions`, `sync_measure`, `trim_review`.

Writes, each a dry run first: `ingest`, `set_color_management`, `timeline_from_clips`, `duplicate_timeline_auto`, `cut`, `apply_grade`, `create_captions`, `queue_render`, `sync`, `relink`, `stabilize`, `reframe_apply`, `trim_review_markers`.

Which to reach for:

- New project: a person makes it; `set_color_management`, then `ingest`.
- Know the footage before touching it: `detect`, `measure`, `sync_measure`.
- Build a timeline: `timeline_from_clips` for an ordered pull, `cut` for a manifest, `duplicate_timeline_auto` for a safe copy, `sync` for dual-system sound.
- Grade and frame: `apply_grade`, `reframe_plan` then `reframe_apply`, `stabilize` on a sample first.
- Captions and delivery: `create_captions`, `queue_render`, then after a person renders, `deliver_captions` and `deliver_check`.
- Trim review: `trim_review`, then `trim_review_markers`.
- Offline clips: `relink`.

## Safety rules (the server enforces them; do not argue with them)

1. **Dry run, then `plan_sha`.** Every write defaults to a dry run. Show the plan, then run for real with the `plan_sha` it returned. A changed plan is refused.
2. **Name the project.** `project` must match the open project exactly, so switching projects mid-run stops the write.
3. **Additive only.** Nothing that existed is modified. Grades, stabilize and reframe go only onto timelines whose name ends ` [auto]`. The named exceptions (`set_color_management`, `relink`, `stabilize`, `reframe_apply`) are listed in `resolve-mcp.md`.
4. **Renders are queued, never started.** A person starts them.
5. **Outputs never go inside a git repository.** Most tools here refuse such a folder (`media-lane.md` lists which); give every one an output path outside any clone.
6. **Tool results stay private.** They name media, people and transcripts. Never paste them into a commit, a pull request, an issue or anything public.
7. **Numbers are not taste.** No tool here is an ear or eye check; hand those decisions to `media-review-loop.md`.

## When it fails

Read the reason the tool printed before retrying. A refusal (a git-tree output, a stale `plan_sha`, a wrong project name) is the rule working: fix the input, never route around it.
