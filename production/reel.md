# Reel: a talking-head cut on the beats, as a Resolve timeline

**Status:** operational (plan and preview); the Resolve leg runs through the MCP `import_timeline` tool.

`rpresolve.reel` takes a cut manifest of one talking head (spans in order, the hook first, words attached) and a music track with its beat map, and writes a plan in which every cut lands on a beat without cutting into a word. The plan is a cut manifest, so `endcheck`, `pacing` and `interchange` read it unchanged; `interchange --format fcpxml` turns it into the FCPXML that `import_timeline` lands in Resolve as an ` [auto]` timeline with the dissolves, punch-ins, music lane and cutout layers in place. A preview through ffmpeg stands in for the Resolve render during the A/B.

Run from `production/`; Python 3.9, stdlib for the plan, ffmpeg for the preview.

## The job

1. Transcribe the master and snap the word edges: `transcribe`, then [`wordsnap`](edit-assist.md).
2. Pick the spans: `reel-spans` or `selects` into a cut manifest ([manifest.md](manifest.md)) whose `sources[].words` points at the snapped JSON. The hook goes first; a hook taken from later in the take needs a version 2 manifest, since version 1 spans run forward.
3. The beat map: `python3 -m rpresolve.beats MUSIC --out beats.json` ([edit-assist.md](edit-assist.md#beats)).
4. The plan: `python3 -m rpresolve.reel plan MANIFEST --music MUSIC --beats beats.json --out DIR`.
5. Subject cutouts where the plan marks them: `cutout` writes a ProRes 4444 clip per span; put its path in the span's `layer` ([interchange.md](interchange.md#reel-fields)).
6. The preview: `python3 -m rpresolve.reel preview DIR/reel.json --out DIR/preview.mp4`, then `qc --destination social_9x16` on it and the A/B through `reel_ab`.
7. Resolve: `python3 -m rpresolve.interchange DIR/reel.json --format fcpxml --out DIR/reel.fcpxml`, then the MCP `import_timeline` tool ([resolve-mcp.md](resolve-mcp.md)), `apply_grade` for the look, `create_captions`, `queue_render`.

## plan

```sh
python3 -m rpresolve.reel plan MANIFEST --music TRACK --beats BEATS.json --out DIR
    [--max-s 45] [--transition cut|dissolve:N] [--punch 1.0,1.2] [--cutout 2,5]
    [--downbeat-offset 0] [--music-in 0] [--clip NAME] [--fps RATE]
```

The spans play in manifest order from record 0, and record 0 is the music's time 0 (less `--music-in`). Each span has a range of lengths the words allow: its out-point may sit anywhere in the pause after its last word (from the word's end plus 10 ms to the next word's start less 10 ms, or 1 s past the word when none follows), and its in-point may move earlier, down to the previous word's end plus 10 ms, never later; two consecutive spans either side of one pause split it, so their edges cannot cross, while a dissolve's handles may still overlap there. A dynamic programme over (span, beat) picks the beat for each cut that changes the original lengths least; the out-point moves first and the in-point only when the out-point cannot reach. The first cut, the end of the hook, must be a downbeat: every fourth beat of the map from `--downbeat-offset`, since the beat map carries no bar phase (the report says which beat it took). A span with no beat in its range stops the plan with its first and last words named (exit 2); nothing is trimmed into a word. The last span is the exception: the reel's end is not a cut, so with no beat in range it ends where its words allow, with a warning. An out-point never passes the end of the source file. Over `--max-s`, whole trailing spans are dropped with a warning.

Dressing goes into the fields `interchange` writes ([reel fields](interchange.md#reel-fields)):

- `--transition dissolve:N`: an N-frame Cross Dissolve on every cut whose pauses hold half of N on both sides. A cut without that room stays a cut, and the report says so.
- `--punch a,b[,c]`: a static scale, alternating over the spans that take one. Resolve reads it as Zoom.
- `--cutout 2,5`: marks those spans `cutout: true` for the cutout tool. A cutout span takes no punch-in.
- `--music`: the bed, as `{path, in}`; `interchange` puts it on the lane below.

Output, in a folder outside any git tree: `reel.json` (the manifest with the moved spans and the new fields, plus a `reel` block: tempo, beats file, downbeat offset, length, one row per cut, `proposal: true`) and `reel-plan.tsv` (span, in, out, record, cut, beat, offset in ms, length change in frames, transition, scale, cutout, the words at the cut). Beats are rounded to the frame grid, so `offset_ms` shows how far the frame sits from the beat: half a frame at most.

## preview

```sh
python3 -m rpresolve.reel preview REEL.json --out FILE.mp4 [--lufs -14] [--crf 18] [--clip NAME]
```

1080x1920 H.264 at the source's rate (capped at 30): each span filled and cropped to 9:16 and scaled by its punch-in, cuts joined by concat and dissolves by xfade over the same handles the FCPXML uses, so record positions match the timeline frame for frame; a layer with alpha overlaid where a span names one; the music bed ducked under the dialogue by `sidechaincompress`; `loudnorm` to `--lufs` (-14 for the feeds, -16 for LinkedIn); AAC 320k 48 kHz stereo; bt709 tags; faststart. No captions: those come from Resolve's `create_captions` after the import, or from `vertcut`.

## Checks

`tests/test_reel.py`: on a synthetic transcript and a 120 BPM grid, every cut sits on a beat (within half a frame) and outside every word, the hook ends on a downbeat, `--downbeat-offset` moves it, a dissolve too long for its pause falls back to a cut with a warning, `--max-s` drops trailing spans, a span with no beat in range is named, the last span may end off the beat, `--music-in` moves the grid, two spans either side of one pause split it, an out-point stays inside the source, writes refuse a git tree, and the preview (lavfi fixtures with an odd dissolve, a cut into a dissolve, a punch-in under 1 and an alpha layer) comes out 1080x1920, 48 kHz stereo, bt709, at the planned length, from a plan that also writes as FCPXML with its two transitions and the music lane.

## Not done

Speed ramps and retimes (Resolve 21.2 imports a `timeMap` broken, resolve-mcp.md), B-roll cutaways, animated captions, a bar phase in the beat map (set `--downbeat-offset` by ear), a music edit (the bed starts at `--music-in` and runs the reel's length, no outro).
