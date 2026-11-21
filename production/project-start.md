# Starting a project from a card

**Status:** operational. Code: `production/rpresolve/project.py`, `cmd_project.py`. Example config: `production/project.example.json`.

`project start` takes a camera card and runs the footage-first chain as far as it can without a person, then says what it needs. Each step is an existing command; this one only orders them and keeps the state.

```
project start CARD [--project SLUG] [--new-project] [--cam CAM] [--apply]
project answer SLUG [--squeeze F [--clip ID]... [--replace]] [--resolved] [--apply]
project answer SLUG [--piece NAME] [--shape WxH] [--look LUT] [--destination KEY --name-part K=V ...] [--apply]
project answer SLUG [--piece NAME] --pick MANIFEST [--ledger DRAFTS [--draft PLAN]] [--exclude LIST] [--apply]
project build SLUG [--piece NAME] [--manifest PATH] [--name NAME] [--apply]
project status SLUG
```

`CARD` is a card volume (copied with `offload.sh copy`, which needs `--cam`) or a card folder that already has its record (not copied again). The project is the folder a card sits in under the work root: a card in `<work root>/X/` belongs to project `X` (a slug), `--project` may be left out, and a different `--project` is refused unless `--new-project` is given, so the state never lands in a second folder. A card elsewhere (the landing root) needs `--project`. Every step that writes is a dry run until `--apply`: a dry run runs the read-only commands and prints what the rest would do. Nothing is deleted.

## The nine steps

| | Step | Runs | Stops when |
|---|---|---|---|
| 1 | copy and verify | `offload.sh copy`, then the record | a volume has no `--cam` or no landing root; a dry run stops after naming the copy |
| 2 | identify | `card set --project SLUG`, `detect --json` | `detect` fails. Clips it marks review or corrupt are listed in `open` |
| 3 | open questions | `card load`, then `sheets --only` for the clips in question | any clip has no squeeze answer (none recorded, no BRAW tag). Answer with `project answer`; a clip that already holds an answer is refused unless `--replace` |
| 4 | index | the configured indexer, `index --card CARD` | the indexer fails; skipped with a note when none is configured |
| 5 | contact sheets | `sheets CARD -o <sheets root>/<slug>/sheets` | sheets fail; skipped when no sheets root |
| 6 | Resolve colour | prints the `set_color_management` call with preset `managed-dwg` | always, until a person creates the empty project (the API cannot) and runs it; then `project answer SLUG --resolved --apply` |
| 7 | shot list, drafts, watch, pick | nothing; `project status` prints the next command from [edit-methods.md](edit-methods.md) | by hand, up to the picked manifest |
| 8 and 9 | build the timeline, queue the render | `project build` (below) | a missing answer, or the first tool that fails; a person then starts the render |

## The last stretch: build

Three answers are recorded once each, in the project state with who and when: `--shape WxH` (the delivery shape), `--look LUT` (a path as `apply_grade` takes it), and `--destination KEY` with `--name-part K=V` for its naming fields (client and slug, or show, episode, guest, index). A second answer to the same question is refused; `project status` prints each answer as recorded and lists the ones still open.

A second piece from the same card records only what differs: `--piece NAME` (a slug) records an answer for that piece alone, and `project build SLUG --piece NAME` reads the piece's answers over the project's, naming the timeline after the piece unless `--name` is given.

The picked manifest is an answer too: `project answer SLUG --pick MANIFEST [--ledger DRAFTS [--draft PLAN]] [--exclude LIST] --apply` records it once, with the draft farm's `drafts.json` (and the picked plan when it holds several) and the exclude list. `project build` then needs no `--manifest`, and passes the ledger and exclude list to `assemble_timeline`. A `--manifest` given at build overrides the pick; the exclude list still goes with it, the pick's ledger does not.

`project build SLUG [--manifest PATH] [--name NAME]` runs the MCP tools' own handlers in this order and stops at the first failure: `assemble_timeline` at the recorded shape, `apply_effect` for each entry of the manifest's `effects`, `apply_grade` with the look on every item of the track the assemble step reports (V1 as well when the montage sits above its plates), then `queue_render` for the destination. It refuses, naming what is missing, when an answer is not recorded, and before anything else when a manifest with its own `music` meets a recorded music pick (see below). The render folder is the destination's `target_dir` (read as `queue_render` reads it: `resolve-config.json` with the `$RPRESOLVE_CONFIG` overlay); a destination with none renders to `<work root>/<slug>/renders`, and the file lands in that folder's destination subfolder. The build and `project status` name the folder; it is made on `--apply` only, so a dry run stops short of `queue_render` while it does not yet exist. A build also refuses first when a name part is missing for the destination, or when another piece's answers resolve to the same output file. A dry run prints each step's dry run as one plan (steps after the assemble, which need the timeline to exist, say so when it does not yet); `--apply` runs each step with the `plan_sha` of its own dry run, records the timeline and job in the state, and leaves step 9 as "queued, waiting for a person to start the render". It connects to Resolve and checks the open project by name before each write; renders are queued, never started.

Music is optional and a person's pick. `music candidates DIR --manifest PATH --out PACKET [--feel F] [-n 3]` reads the cut's length and its method's tempo range (a shot-list cut has none: then any tempo, unless `--bpm LO-HI` narrows it), lists library tracks inside the range (half and double time count) with a steady pulse first and none shorter than the cut, and writes lettered, level-matched excerpts `A.m4a`, `B.m4a` ... and `key.json` to PACKET for the `media-review-loop`. It suggests and never picks. After the person's letter, `project answer SLUG --music-key PACKET --music-pick LETTER [--music-in SEC] --apply` records the track once in the project state, and `project build` builds from a copy of the manifest (`build-manifest.json` beside the state) with that `music {path, in}`. A manifest that already has `music` and a recorded pick is refused, naming both tracks, unless the manifest's music is that very track and start; the choice is made once: remove the music from the manifest (then the pick goes in), or clear the pick with `project answer SLUG [--piece NAME] --clear-music --apply` (for a piece that inherits the project's pick, this records no music for that piece). A piece's own `--pick` keeps the project's exclude list unless it records its own. The picked manifest is never edited. The library file's shape is in the `rpresolve/music.py` docstring.

`project start` again resumes: a finished step, `card set` and `detect` among them, is not repeated; the questions are asked afresh from the record. Each step prints a line when it starts; `detect` prints a count every 10 clips, and any long command a line every 30 seconds, to stderr.

## State and config

State is `<work root>/<slug>/project.json`: the steps with their status, the card folders by path and `open`. Paths come from the JSON file named by `RP_PIPELINE_CONFIG` (`landing_root`, `work_root`, `indexer`, `offsite_remote`, `sheets_root`); each can be given on the command line instead (`--landing`, `--work-root`, `--indexer`, `--remote`, `--sheets-root`). With no config, give every path on the command line; a missing indexer or sheets root skips its step with a note. `indexer` is a command, or `{"argv": [...], "env": {...}}`; it is run as `<indexer> index --card CARD`.

## Status

`project status SLUG` lists the nine steps with their state, `open` questions, each build answer as recorded and the ones not yet recorded, a destination with no render folder, the next command for steps 7 to 9 (step 8 names only the build once shape, look and destination are recorded; the music line goes once a track is picked), and whether the card may be formatted: yes only when an `offsite.txt` names it (`card check` standard S4).
