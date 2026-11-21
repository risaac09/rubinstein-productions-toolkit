# Build diff: the planned edit against the built timeline

> **Status:** draft 2026-11-21. `python3 -m rpresolve.build_diff` compares a cut manifest with a saved `timeline_items` read and prints one table. It is offline and tested on made-up items only; it has not been run against a live Resolve build yet.

`cut`, `assemble_timeline`, `import_timeline` and `timeline_from_clips` each read their own build back while it is made. None of them checks a timeline afterwards, offline, against the manifest. This does, so a timeline that was moved by hand, or built by an agent, can be checked from its saved read.

## Usage

Run from `production/`:

```
python3 -m rpresolve.build_diff --manifest MANIFEST.json --items ITEMS.json [--clip NAME]
```

- `MANIFEST.json` is a cut manifest, loaded by `cutlist.load_manifest`. `--clip` names the clip when the manifest holds more than one.
- `ITEMS.json` is saved `timeline_items` output: one result, or a list of results (one with `track_type` video, one with audio). Save every page; a result with a `next_offset` is refused.
- Exit 0 when every row passes, 1 on any FAIL, 2 on a usage error or input it cannot read. Nothing is written.

## The plan

The clip's spans laid end to end on V1 from the timeline's start frame, as `cut` lays them. Frames are counted as `cut` counts them: `cutlist.frames` at the manifest's own fps number, rounding half up. A span plays source frames from `frame(in)` for `frame(out) - frame(in)` frames, and an item must hold that start and length exactly. The source end is not compared; the length gives it.

## The table

FAIL rows first.

| Row | FAIL when |
|---|---|
| `frame rate` | the timeline's rate differs from the manifest's by more than 0.01 |
| `item count` | V1 holds a different number of clip items than the plan has spans |
| `span N` | no item starts at the span's record frame, or its length, source start or media file differs |
| `gaps`, `overlaps` | V1 items leave frames uncovered or cover them twice; the plan has neither |
| `audio under video` | a placed video item has no audio item covering it with the same media, or the sound is not on A1 |
| `extra items` | a V1 item matched no span, or any clip sits on V2 and above |

Transition items (a `clip` of null) are not counted and are named in the count row.

## Limits

- A manifest holds one frame rate, so there is no per-source rate to compare.
- Sound is planned unless the manifest sets `camera_sound` to false. An audio item must cover its video item whole, so a J or L cut (`audio_lead`, `audio_tail`) reads as a FAIL until the check learns them.
- Audio source ranges are not compared; only presence, cover, media and track.
- Audio items under no video item (a music bed) are not judged.
