# Lock handoff folder (VP-145)

> **Status:** proposed 2026-10-20, a spec with no code yet. Held until the toolkit freeze lifts on 2026-11-01. Gate G6 of the card-to-delivery pipeline.

When a cut is locked, picture stops moving and three lanes start reading it: colour, sound and titles. They read one folder, written once, so none of them invents its own export. This page says what the folder holds, how each file is named and checksummed, and what counts as a change after lock. The checker that enforces it is a later item; the acceptance test is stated at the end so the spec and the checker agree.

## One folder per lock version

```
<project-slug>_lock_v01/
  lock.json
  SHA256SUMS
  timeline.otio
  timeline.edl
  paper-edit.tsv
  markers.tsv
  cue-sheet.tsv
  consent-list.tsv
  sound-handoff.json
  guide.mov            (optional)
```

- The folder name is the project slug, `_lock_v`, and a two-digit version. It never sits inside a git working tree.
- Every file name is fixed. A lane reads a file by name, never by searching.
- `SHA256SUMS` is in `sha256sum` format (lowercase hex, two spaces, file name) and covers every other file in the folder, `lock.json` included. `lock.json` also records each file's hash, so a reader can check one file without the sums file.
- A file with nothing to say is still written, header only, so a missing file always means a broken folder.

## The files

| File | What it is | Read by |
|---|---|---|
| `lock.json` | The manifest: version, timeline facts, the items hash, each file's hash. Schema below | every reader, first (carries file 6, the items hash) |
| `timeline.otio` | The locked timeline as OpenTimelineIO JSON, with clip names and source clip ids in the clip metadata | grade, sound (file 1) |
| `timeline.edl` | The same cut as a CMX 3600 EDL, video track 1 (V1 only; other tracks are in the OTIO), frame-accurate timecode at the timeline's rate | grade (file 1) |
| `paper-edit.tsv` | The final paper edit: one row per kept span, in order. Columns `order`, `clip_id`, `source_in`, `source_out`, `beat`, `note`. Times are source timecode | grade, titles (file 2) |
| `markers.tsv` | Every timeline marker, in the schema of [the cue, marker and sync contract](cue-marker-sync.md#the-marker-tsv) | sound (file 3) |
| `cue-sheet.tsv` | Music and sound cues as placed: `cue_id`, `kind`, `title`, `timeline_frame`, `duration_frames`, `source`, `licence_row`. `licence_row` names the row in the [licence register](licence-register.md) | file 4; sound's own addition |
| `consent-list.tsv` | The list the approver ticks before lock: `subject`, `kind` (child, close face, on-screen name, cover song, unreleased performer), `timeline_frame`, `release_ref`, `approved` (`yes` or `no`), `approved_by`, `approved_on` | grade (file 5); a row with `approved` not `yes` blocks lock |
| `sound-handoff.json` | Sound's fields: track layout, stems plan, frame rate, start timecode. Schema below | sound, its own addition |
| `guide.mov` | A low-resolution guide render of the locked picture, burned-in timecode, for sound to work against. Optional, but if present it is hashed like the rest | sound, with files 1 and 3 |

Times in the TSVs are timeline frames counted from zero, or source timecode where the column says so. Nothing is typed twice: a value that exists in `timeline.otio` (duration, frame rate, start timecode) appears in `lock.json` as a copy the checker compares, never as a second source.

## `lock.json`

```json
{
  "schema": {"name": "shared.lock-handoff", "version": 1},
  "project": "example-slug",
  "lock_version": 1,
  "locked_at": "2026-11-02T15:00:00Z",
  "timeline": {
    "name": "example-slug_v12",
    "fps": "24000/1001",
    "start_timecode": "01:00:00:00",
    "duration_frames": 4320
  },
  "items_sha256": "<64 hex>",
  "files": {
    "timeline.otio": "<64 hex>",
    "timeline.edl": "<64 hex>"
  }
}
```

`files` lists every file in the folder except `lock.json` and `SHA256SUMS`. `locked_at` is ISO 8601 UTC.

### The items hash

`items_sha256` is the SHA-256 of the timeline's item list, so a change to the picture shows up even when someone re-exports the other files by hand. Build the list from the Resolve `timeline_items` read, one object per video item:

```
{"track": 1, "index": 3, "name": "...", "record_in": 240, "record_out": 312,
 "clip_id": "S5II-a1b2c3/P1039630", "source_in": 1200, "source_out": 1272}
```

`record_*` and `source_*` are frames, `clip_id` follows the [sidecar envelope](https://github.com/risaac09/rubinstein-productions-toolkit/pull/223) rule (`<card folder>/<stem>`). Sort by `track`, then `record_in`; serialise as JSON with sorted keys and no spaces; hash the UTF-8 bytes. Audio items are not in the hash: sound's own edits after lock do not move picture.

## `sound-handoff.json`

```json
{
  "schema": {"name": "sound.handoff", "version": 1},
  "timeline": "example-slug_v12",
  "fps": "24000/1001",
  "start_timecode": "01:00:00:00",
  "duration_frames": 4320,
  "track_layout": [{"track": "A1", "role": "dialogue"}, {"track": "A2", "role": "music"}],
  "stems_plan": ["dialogue", "music", "effects"]
}
```

`timeline`, `fps`, `start_timecode` and `duration_frames` must equal the same fields in `lock.json`. Markers are not repeated here; they are in `markers.tsv`. A 5.1 layout is out of scope for v1.

## What changes after lock

Locked means the folder is read-only. Any change to picture after lock, however small, makes a new folder: version 02, with every file re-emitted, not only the one that changed. The old folder stays. A change that touches only sound or titles (a new cue, a corrected marker note) is also a new version, so a reader holding v01 can tell it is stale by the folder name alone.

## The checker's job (acceptance)

A later item writes the checker. It passes a complete folder and fails, with the first mismatch named, on each of:

- a missing file, or a file not in `SHA256SUMS` or `lock.json`;
- a hash that does not match the bytes;
- `duration_frames` that disagrees between `lock.json`, `timeline.otio`, the last record-out in `timeline.edl` and `sound-handoff.json`; a duration that moved by one frame fails;
- `items_sha256` that does not match the items rebuilt from `timeline.otio`;
- a `consent-list.tsv` row whose `approved` is not `yes`;
- a `markers.tsv` that fails the marker schema.

## Open

- The readers column follows the craft lanes index: grade reads the timeline files (OTIO plus EDL), the paper edit and the consent list; sound reads the timeline files and the markers plus a guide render; titles read the paper edit. `sound-handoff.json` and the cue sheet are sound's own additions to the folder.
- The marker colour per lane is not fixed here; a lane proposes one and the operator signs it.
