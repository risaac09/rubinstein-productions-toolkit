# Edit methods

> **Status:** draft 2026-11-09. Six methods, the loader and validator, `to_manifest`, the draft farm, the MCP `edit_methods` and `apply_effect` tools, and their tests are in place. No method has yet been built end to end on a real shoot.

An edit method is a reusable short-form edit recipe: a timed list of slots, one per shot, each a role to fill from a new shoot with its move, transition, effect and scale. The methods were written from timing and effect breakdowns of short travel and outdoor edits; they keep the technique and none of the source material.

The job, per shoot:

1. `edit_methods` lists the methods. Pick one whose `fits` tags match what was filmed.
2. Fill each slot with a clip: one `{source_path, in, out}` per slot, in seconds. The slot's `query` says what to look for.
3. `methods.to_manifest(method, fills)` returns a version 2 cut manifest. Write it to `PLAN_DIR/manifest.json`, outside any git tree.
4. Drafts first: `python3 -m rpresolve.drafts PLAN_DIR [PLAN_DIR ...] --out DIR` renders each plan as a 720p proxy and one lettered sheet; the pick is the plan that gets built ([Drafts](#drafts)).
5. `python3 -m rpresolve.interchange MANIFEST --format fcpxml --out FILE`, then the MCP `import_timeline` tool lands it as an ` [auto]` timeline with the cuts, dissolves and static scales ([interchange.md](interchange.md)).
6. For each entry in the manifest's `effects`, `apply_effect` with that macro on item `span_index + 1` of V1 ([fusion-macros.md](fusion-macros.md)).
7. Run the manifest's `method.ui_steps` through computer use, after asking for access ([UI steps](#ui-steps)), then `apply_grade` with the method's `look`, `create_captions` and `queue_render` as usual.

Run from `production/`; stdlib only.

## The format

`production/edit-methods/<id>.json`, the file name the id. Every field is required; no other field is allowed.

| Field | Meaning |
|---|---|
| `edit_method` | `1`, the format version |
| `id` | lowercase words joined by hyphens, the file name |
| `name`, `summary` | a title and one line |
| `fits` | tags from `travel`, `event`, `outdoor`, `people`, `food`, `night`, `motion`, `portrait`, `nature`: what footage the method suits |
| `aspect` | `"16:9"` or `"9:16"` |
| `fps` | the frame rate the timing was written at |
| `length_s` | the slots' seconds plus the end black, within one frame |
| `bpm` | `[low, high]`, the music tempo the cuts suit, or `null` |
| `look` | a look in `production/lutlab-looks/` by file name (`print-teal`), or `null` |
| `rules.reuse` | whether two slots may play the same source frames |
| `rules.min_shot_s` | the shortest slot allowed |
| `rules.snap_to_beat` | whether the cuts should be moved onto the music's beats |
| `rules.end` | `"black:S"`, S seconds of black after the last slot, or `null` |
| `slots[]` | in play order |

A slot:

| Field | Meaning |
|---|---|
| `role` | what the shot does in the edit (`establishing`, `burst`, `word`) |
| `seconds` | its length on the timeline |
| `query` | a search line for the footage: what is in frame, generic, no place names |
| `move` | `push-in`, `static`, `tilt-up`, `pan`, `handheld` or `forward`: the camera move to look for |
| `speed` | 1.0 is real time; anything else is a retime |
| `transition_out` | `{type, frames}`: `cut` (0 frames), `dissolve`, `dip` or `wipe` into the next slot; the last slot's is a cut |
| `effect` | a macro id from [fusion-macros.md](fusion-macros.md), or `null` |
| `scale` | `[start, end]` zoom, or `null` |
| `text` | `"word"` when the slot carries a word, else `null` |

`validate(m)` returns every departure from this as a sentence; `load(id_or_path)` refuses a method that has any.

## to_manifest

`to_manifest(method, fills)` writes one clip named after the method, one span per slot. Each span starts at its fill's `in` and runs the slot's `seconds`: the method's timing wins, so a fill must hold at least that much (half a frame of slack). Sources are numbered `s1`, `s2`, ... in first use. Two fills that overlap in one source are refused unless `rules.reuse` is true, which marks the later span `reuse`.

| Method field | In the manifest |
|---|---|
| `transition_out` dissolve of N frames | the next span's `transition` `{"type": "dissolve", "frames": N}` (interchange needs N/2 frames of source either side of the cut) |
| `transition_out` dip or wipe | a cut, and a UI step for the transition |
| `scale` with start equal to end (not 1) | the span's `scale` |
| `scale` that changes | nothing when the slot's effect is `zoom-push` or `scale-window` (the macro animates it); otherwise a UI step for Zoom keyframes |
| `speed` other than 1 | a UI step for Retime Controls (FCPXML retimes import broken in Resolve 21.2) |
| `text: "word"` | a UI step: set the word in the comp |
| `effect: person-matte-text` | also a UI step: replace the placeholder matte with a tracked Magic Mask |
| `effect` | `effects[]` `{span_index, effect}`, span_index from 0 |
| `rules.end` | `method.end`, and a UI step for the black after the last item |
| a `.braw` source | a UI step: Camera Raw Decode Using Clip on its items ([BRAW](#braw-decodes-from-the-clip)) |

The manifest is `"speech": false`, so `cutlist.validate_manifest(m, allow_no_speech=True)` reads it, and `interchange` writes it. `effects` and `method` sit beside the manifest's own fields; the tools that read manifests ignore them.

`effects` and `method` sit beside the manifest's own fields, as above. `method` is `{id, aspect, look, end, ui_steps}`.

## UI steps

Anything the scripting API cannot do is a UI step: a computer-use recipe the session runs in Resolve after asking for access, never a note left for the operator. Each is `{target, page, panel, control, value, why}`, for example `{"target": "span 7", "page": "Edit", "panel": "Retime Controls", "control": "Speed", "value": "70 percent, Optical Flow", ...}`. `methods.ui_step` makes one.

### BRAW decodes from the clip

Every BRAW clip a method brings onto a timeline decodes with Camera Raw **Decode Using: Clip**, so it takes its own metadata; the project-level Camera Raw settings are never changed. No documented API sets it in Resolve 21.2 (no clip property; the one BRAW call is `TimelineItem.UpdateSidecar`), so `to_manifest` puts one UI step first per `.braw` source: Color page, Camera Raw palette, Decode Using, Clip, then Update Sidecar.

## Drafts

```sh
python3 -m rpresolve.drafts PLAN_DIR [PLAN_DIR ...] --out DIR
    [--camera Pyxis] [--lut-root DIR] [--proxy-dir DIR] [--lanes N] [--no-hw]
```

Before anything is built in Resolve, each plan renders as a 720p H.264 proxy (1280x720 or 720x1280, the method's rate, silent, the end black included) so several can be compared at once. A plan directory holds `manifest.json` from `to_manifest` and, optionally, `method.json` (else the method is loaded by id).

- Every span is its own ffmpeg job: VideoToolbox decode and `h264_videotoolbox` encode where this ffmpeg has them, fill-and-crop, the span's static scale, the look through `lut3d`, and approximations of `zoom-push`, `crop-window`, `dip-to-black`, `exposure-flash` and `text-word` (a white box where the word goes). The spans are joined without re-encoding.
- The look is `<look name> [<camera>].cube` found under `--lut-root` (Resolve's `LUT/lutlab` folder) for `--camera`. Without it the draft is ungraded and says so.
- BRAW plays from its proxy, `<proxy-dir>/<name>.mov`; a BRAW source with none is refused (ffmpeg cannot decode BRAW).
- Lanes: one job is timed alone, then each wave of two jobs per lane adds a lane while throughput rises by 5 percent, up to the core count; the best count runs the rest. `--lanes N` fixes it. Short spans make the probe noisy; from 4K HEVC sources on an Apple silicon laptop, three method drafts (44 s of draft) took 25 s at 2 lanes.
- Output in `DIR`: `<plan>.mp4`, `<plan>.notes.txt` naming everything the draft does not show (an effect with no approximation, a dissolve drawn as a cut, a retime, a missing look), `drafts.json`, and for two or more drafts a lettered `rpstills absheet` sheet of their midpoints in `DIR/sheet` with the key sealed in `DIR/sheet.key`. `DIR` inside a git tree, or not empty, is refused.

Each span's length is a whole number of frames, as `interchange` counts it, so a draft can run a frame or two longer than `length_s` when the method's times are not on the frame grid.

## The methods

| id | Aspect, length | Shape | From |
|---|---|---|---|
| `punch-montage` | 16:9, 13.46 s, 15 slots | A 1.7 s establishing push, the same take held tighter after a 1.15 punch-in, a 3 s word plate, an accelerating burst of 4 to 5 frame shots, two breaths (one slowed to 70 percent, one tilt), a flash entry, a final burst of 3 to 5 frame shots and 0.7 s of black. Exact cut times kept. | one breakdown |
| `beat-burst` | 9:16, 12.55 s, 30 slots | A 1.85 s title behind the people in a wide, then quarter-second shots on a 120 to 135 BPM grid with hero holds of 0.45 to 0.8 s, one flash entry, two crop windows, a held last wide and a short black. | two breakdowns |
| `slow-cinematic` | 16:9, 18.04 s, 7 slots | Seven 1.3 to 4.3 s shots of one person: a flash-in close, cuts on matching action, a 29-frame hard-edge wipe between two wides (a UI step), a dip to black on the closing wide. | one breakdown |
| `scale-window-stack` | 16:9, 28.58 s, 7 slots | An 11 s performance hold, a hard cut, four shots that each grow from a small window and snap full on the beat (about 77 BPM), a still hold to end. | one breakdown |
| `cutout-clones` | 16:9, 23.54 s, 13 slots | One person through haze and open ground: flash entries, a clone of the person in a locked-off plate, a copy that grows into the next shot, words on the beat, a long fade. | one breakdown |
| `word-burst` | 16:9, 18.67 s, 48 slots | A lyric edit: one word per cut over one person looking out (18 slots), a 23-shot burst of 0.15 to 0.2 s with three 2-frame dissolves, an outro of 7 slots that reuses opening shots, and 1.67 s of black for a last word. | two breakdowns |

## What a method cannot carry yet

- Dips, wipes, retimes and words are UI steps: FCPXML carries cuts, dissolves, static scales and lanes only. Letterbox moves, typewriter text and the clone in `cutout-clones` (two takes of one locked-off frame, or a `cutout` layer, [reel.md](reel.md)) have no macro and no step yet.
- `rules.snap_to_beat` is advice: `to_manifest` keeps the method's own times. `reel plan` snaps a talking-head cut to beats; a method has no music yet.
- Three macros need a person's eye before delivery: `person-matte-text` (a placeholder matte), `exposure-flash` (its strength) and `text-word` (font and colour). See [fusion-macros.md](fusion-macros.md#needs-a-persons-eye).
- The transitions that show transparency (`scale-window`, `crop-window`, `cross-dissolve-short`) show black on one track; an imported method timeline is one track, so the incoming item moves to V2 to show the outgoing shot through it.

## Checks

`tests/test_methods.py`: every shipped method loads and validates, its file name is its id and its effects are macros; `validate` names each kind of fault; every method's `to_manifest` output validates as a version 2 manifest and writes as FCPXML with the method's frame count, one transition per dissolve and the effects list; short, relative and overlapping fills are refused; `reuse` marks the later span; `edit_methods` lists and checks; no method or macro holds a link, a handle or a home path. `tests/test_effect.py` covers `apply_effect`; `tests/test_drafts.py` renders synthetic plans at both shapes, checks length, rate, notes, the look cube, the sheet, BRAW proxies, the git refusal and the lane tuner.
