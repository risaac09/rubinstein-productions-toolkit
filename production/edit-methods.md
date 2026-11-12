# Edit methods

> **Status:** draft 2026-11-09. Eight methods, the loader and validator, `to_manifest`, the draft farm, the MCP `edit_methods`, `apply_effect`, `assemble_timeline` and `vertical_apply` tools, and their tests are in place. Five methods have been planned and drafted on two shoots and `assemble_timeline` has built a 48-cut draft with its dissolves and music on a sandbox timeline. Two methods have now been built from a reviewed pick through to a rendered file (2026-11-12): one at 9:16 with a tracked Magic Mask and one at 4K, each passing `deliver_check`, the 9:16 one including the music fade tail and the not-log-flat check. On 2026-11-11 the plates, the music bed, the dissolve counting, the 9:16 finish and the draft farm's hardware path were each run live in the sandbox (the sections below say which parts); the music bed's tail and a 9:16 window were then checked on rendered files, and the checks the sections below mark as waiting on a render still are.

An edit method is a reusable short-form edit recipe: a timed list of slots, one per shot, each a role to fill from a new shoot with its move, transition, effect and scale. The methods were written from timing and effect breakdowns of short travel and outdoor edits; they keep the technique and none of the source material.

The job, per shoot:

1. `edit_methods` lists the methods. Pick one whose `fits` tags match what was filmed.
2. Fill each slot with a clip: one `{source_path, in, out}` per slot, in seconds. The slot's `query` says what to look for.
3. `methods.to_manifest(method, fills)` returns a version 2 cut manifest. Write it to `PLAN_DIR/manifest.json`, outside any git tree.
4. Drafts first: `python3 -m rpresolve.drafts PLAN_DIR [PLAN_DIR ...] --out DIR` renders each plan as a 720p proxy, one lettered sheet and the same drafts lettered in `DIR/send`; the pick is the plan that gets built ([Drafts](#drafts)).
5. The MCP `assemble_timeline` tool builds the manifest as a new ` [auto]` timeline: each span from its clip at the clip's own frame rate, static scales, the dissolves, and the music on A1. When every source is at the timeline's rate, `python3 -m rpresolve.interchange MANIFEST --format fcpxml --out FILE` and `import_timeline` do the same from FCPXML ([interchange.md](interchange.md)).
6. For each entry in the manifest's `effects`, `apply_effect` with that macro on `spans: [span_index]` of V1, or on `items: [span_index + 1]`, the clip number ([fusion-macros.md](fusion-macros.md)). Both count clips, not the dissolves between them (`T29`); give one of the two, not both.
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
| `role` | what the shot does in the edit, in words any shoot can fill: `establishing`, `hero`, `figure`, `face`, `group`, `detail`, `movement`, `place` or `closer` |
| `seconds` | its length on the timeline |
| `query` | a search line for the footage: shot size, who or what is in frame and the light. Generic: no place names, and nothing only the source video had |
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
| `speed` other than 1 | `effects[]` `{span_index, effect: "retime", params: {speed}}`, which `apply_effect` builds as a Fusion comp (FCPXML retimes import broken in Resolve 21.2). An item holds one comp, so the validator refuses a slot with both a speed and an effect |
| `text: "word"` | `params.text` on the slot's text effect when the fill carries a `text` word; with no word in the fill, a UI step |
| `effect: person-matte-text` | also a UI step: replace the placeholder matte with a tracked Person Magic Mask, by computer use ([recipe](fusion-macros.md#person-matte-the-magic-mask-recipe), untested live) |
| `effect: exposure-flash`, and the fill carries a `gain` (1 to 20) | `params.gain` on that entry; the draft draws the same number ([T28](resolve-traps.md)). A `gain` on a fill for any other slot is refused |
| `effect` | `effects[]` `{span_index, effect, params?}`, span_index from 0 |
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
python3 -m rpresolve.drafts PLAN_DIR [PLAN_DIR ...] --out DIR [--exclude FILE] [--crops FILE]
    [--camera Pyxis] [--lut-root DIR] [--proxy-dir DIR] [--lanes N] [--no-hw]
```

Before anything is built in Resolve, each plan renders as a 720p H.264 proxy (1280x720 or 720x1280, the method's rate, the end black included) so several can be compared at once. A plan directory holds `manifest.json` from `to_manifest` and, optionally, `method.json` (else the method is loaded by id). When the manifest carries `music` `{path, in}`, that track is laid under the draft from `in` seconds and faded out at the end, and the notes say to check its licence; without it the draft is silent.

- Every span is its own ffmpeg job: VideoToolbox decode and `h264_videotoolbox` encode where this ffmpeg has them, fill-and-crop, the span's static scale, the look through `lut3d`, and an ffmpeg stand-in for each effect it can draw. The spans are joined without re-encoding.
- What the draft draws differently, or not at all. `zoom-push` and `retime` (its speed and `speed_to`) are drawn as the macro does. The rest are stand-ins, and each says so in the notes and the ledger:
  - `exposure-flash`: the fill's `gain` (default 2) falling to 1 over 12 frames, as the macro's multiply, each channel times gain^(1/2.4) about video black and clipped at white ([T28](resolve-traps.md)); it draws the look (when a cube exists for the camera) before the flash but no colour management, so its absolute levels sit below the build's.
  - `dip-to-black`: the macro's even-stop keys (1, 0.5, 0.25, 0.125, 0.06, 0.02, 0 every 3 frames), black on the last frame, drawn as a multiply of the luma fitted on one dark frame ([T28](resolve-traps.md)), not the flash's per-channel one.
  - `crop-window`: the box opens and closes at a constant rate; the macro eases both ends. Outside the box it shows the previous shot playing on from its out point (the same plate as a `scale-window`), and black on the first span, which has none.
  - `scale-window`: the window stays centred; the macro's centre drifts about 0.05. It grows over black on the first span and over the previous shot playing on from its out point after it (not a held frame; that shot's own effects are not drawn on it); the notes and the ledger give each span the matching wording. Live-tested 2026-11-11 on real footage: a plate starts on the frame after the previous span's last frame (the same scene one to two frames of motion on, not a repeat) and keeps playing through the window. The window layer sits on the draw rate's grid from frame 0, and over a plate so does the plate, so a window on a later span is on screen from the first frame at its starting size and one on the first span (over black) opens on frame 0 as well; either snaps to full frame on the macro's frame (37 for a `scale-window`, 26 for a `crop-window`). A draft made before 2026-11-12 had the window a frame late, or a `crop-window` a frame early ([T32](resolve-traps.md)). A draft is for picking, not for timing a snap to the frame.
  - `text-word`: a white box where the word goes. `person-matte-text`: the same, in the macro's red-orange, at the macro's position and size; it sits in front of the subject and the matte is not drawn, so a pick on `matte-title` or `beat-burst` judges the word's place, never the word going behind anyone. `reframe-pan`: a crop that moves from `pan_from` to `pan_to` as a share of the source width, not Resolve's Pan arithmetic.
  - A dissolve between spans is drawn as a cut.
  - Not drawn: `cross-dissolve-short`, which has no stand-in, and the UI steps (dips and wipes, animated scales, durations). A look with no cube for the camera is not applied, and the notes say so.
- 9:16 crops. A 9:16 plan is drawn as a centre crop of each span. With `--crops FILE`, the `crops.json` that `vertical plan` writes, each span is cropped at its own centre (item N of the file is span N - 1, so a file with another item count, or an item for another source file, is refused), and a window's plate at the centre of the shot it continues, as `vertical_apply` frames the V1 plate. A span that carries a `reframe-pan` keeps that pan. The notes say which spans used which centre, and `drafts.json` keeps them per draft in `crops` (`{span, cx, used}`). A 16:9 plan ignores the file and says so; without the option nothing changes. The file need not be reviewed. Live-tested 2026-11-11 on a 30-span plan with a real crops file: for 26 spans without effects, a pixel comparison found the planned centre exactly on 23 and within 0.01 on 3, against errors of 15 to 65 grey levels at a centre of 0.5, and a crop-window's plate sat at the centre of the shot it continues. The farm checks only the count and the source paths, so item in and out points that differ from the spans' by a few hundredths of a second are not refused or noted.
- The ledger. `drafts.json` gives each draft an `effects` object built from the manifest: `drawn`, `approximated` (with the note) and `not_drawn`, each a list of `{effect, spans}` with the span indices from 0. Read it before a pick becomes a build: an effect under `not_drawn` was not on the draft the person chose from, so it goes back to them before the build applies it.
  The build enforces it: pass `apply_effect` and `assemble_timeline` the plan's `drafts.json` as `ledger`, with `draft` naming the picked plan when the file holds several. The dry run flags every effect the draft marked `not_drawn` (`ledger.flagged`, with the spans). The real run refuses until `override`, a list of effect names, names each flagged effect, so a skipped effect is a decision and not an accident; `override` and the ledger check are part of the `plan_sha`, so name the override in the dry run too. `assemble_timeline` also refuses a ledger whose effects and spans are not this manifest's. With no `ledger`, the build behaves as before and the result says no ledger was checked.
- The look is `<look name> [<camera>].cube` found under `--lut-root` (Resolve's `LUT/lutlab` folder) for `--camera`. Without it the draft is ungraded and says so.
- BRAW plays from its proxy, `<proxy-dir>/<name>.mov`; a BRAW source with none is refused (ffmpeg cannot decode BRAW).
- Hardware path, live-tested 2026-11-11: a 36 s, 871-frame, 1280x720 draft with window plates took 9.9 s with `h264_videotoolbox` on one lane, and the software encode gave the same frame counts and window timing.
- Lanes: one job is timed alone, then each wave of two jobs per lane adds a lane while throughput rises by 5 percent, up to the core count; the best count runs the rest. `--lanes N` fixes it. Short spans make the probe noisy; from 4K HEVC sources on an Apple silicon laptop, three method drafts (44 s of draft) took 25 s at 2 lanes.
- Output in `DIR`: `<plan>.mp4`, `<plan>.notes.txt` naming everything the draft does not show (an effect with no stand-in, each stand-in and how it differs, a dissolve drawn as a cut, a missing look), `drafts.json` with the ledger, and for two or more drafts a lettered `rpstills absheet` sheet of their midpoints in `DIR/sheet` with the key sealed in `DIR/sheet.key`. `DIR/send` holds the same drafts as `A.mp4`, `B.mp4` ... in the sheet's letters, each with `A.notes.txt`, `B.notes.txt` ... whose first line is its note count (`4 notes`, or `everything drawn`), so nothing is lettered by hand. Send that folder and the sheet. The letter-to-plan mapping is only in the sealed key: `drafts.json` and the printed summary name plans, `send/` has letters, and nothing pairs them, so read the key after the answer comes back. `DIR` inside a git tree, or not empty, is refused.

`--exclude FILE` is the operator's exclude list, a JSON object whose `takes` list names the takes ruled out (a file stem, a file name or a path suffix, ignoring case); a file with no `takes` list is refused, and a `shots` key is reported as not checked here. A plan whose manifest names an excluded take, or whose music file a take names, is refused before any ffmpeg job runs. Without it, each draft's notes say `no exclude list given`.

Each span's length is a whole number of frames, as `interchange` counts it, so a draft can run a frame or two longer than `length_s` when the method's times are not on the frame grid.

## Evidence keys

The three sections below state rules, and every rule cites what it rests on.

- `B1` to `B10` are ten shot-by-shot breakdowns of published short edits, with the line the fact sits on (`B6 L46`). The breakdowns and the key that names them stay private. Every tempo in them is read from cut spacing, since nobody heard the audio.
- `T1` to `T33` are entries in [resolve-traps.md](resolve-traps.md), each measured in Resolve.
- `R` is a logged answer from the person whose edit it is, in a project's `review/events.jsonl`, cited by date and step. One answer is one answer: a preference becomes a rule after five consistent ones ([media-review-loop.md](media-review-loop.md)).
- `M` is a number measured on a real shoot during the first runs, 2026-11-09.

## Choosing clips

A method's slots are filled from a footage index. The toolkit ships no index; the rules hold for any index that keeps a keyframe, an image embedding and a motion value per shot.

1. **Gate first, then match.** The nearest match to a slot's query has no quality floor. On the first run it returned out-of-focus frames, smear, black, blank sky, sideways clips and one shot twice (`M`). Every keyframe passes a gate before it can be a candidate. Copies are folded first, so a proxy or a second export cannot move a threshold: on one festival shoot 9,899 indexed keyframes are 2,620 distinct shots and 1,180 pass; on a three-day trip shot on one camera, 245 of 687 pass (`M`).
2. **The gate.** Thresholds are relative to the shoot, so a dark shoot is judged against itself. Read the pass and reject samples by eye on each new shoot before trusting them.

   | Measure | Passes when |
   |---|---|
   | Sharpness: variance of the Laplacian over mean brightness, log, z-scored in the shoot | above the shoot's 35th percentile |
   | Exposure: mean luma, 0 to 1 | between 0.10 and 0.90 |
   | Motion: frame-to-frame difference | below the shoot's 92nd percentile |
   | Keeper: embedding similarity to "a good photograph" minus the highest of four fault probes, z-scored | above -0.3 |
   | Black area: share of pixels under 0.04 | at most 0.55 |
   | Sideways: similarity to "rotated 90 degrees", z-scored | below 0.8 |

   The sharpness cut fails 35 percent of every shoot by construction. The sideways probe also fails people lying down and frames with strong vertical lines, and the black-area rule exists because a take shot through a doorway passed every other measure with 60 percent of the frame black (`M`).
3. **One take, once.** A take has copies: a camera original, a graded export, a low-resolution proxy. Identity is the camera's own take id in the file name, with matching duration and matching keyframe embeddings as the fallback (`M`: the duplicate in the first drafts was two copies of one take in two folders). Reuse is a method's decision, `rules.reuse`, as when an outro returns to opening shots (`B8 L83`, `B2 L28`, `B10 L64`). It is never an accident of the search.
4. **Slots name roles any shoot can fill.** The first method files carried the source video's subjects in their queries, so they only fit that video (`M`). A slot's role is one of nine words, and the validator refuses any other: `establishing` (a wide with room for a title, `B10 L16`), `hero` (the person doing what the shoot is about, held, `B10 L84`), `figure` (a person as a shape: small in a wide, in silhouette, from behind), `face`, `group`, `detail`, `movement`, `place` (a breather with nobody in it, `B4 L28`, `B1 L24`) and `closer`. A role is scored by image probes for shot size and subject, since captions cover few shots. The role names its probe before the query's words do, and a fill must sit half a standard deviation above the shoot's mean on that probe. Without that floor a themed variant filled a face slot with plates of food (`M`). A `face` slot also wants a detected face whose box covers at least 2 percent of the frame, while such a shot is left: the close-up probe alone passed a torso with a passer-by's face behind it, and the faces it got right measured 2.5 percent and up (`M`).
5. **Variety is counted.** The edits that hold attention change shot size, pose and place from cut to cut (`B10 L16-L66`, `B2 L31`, `B3 L16-L22`: 14 mm to 135 mm in seven shots). A pick's embedding stays under 0.80 cosine to every earlier pick, and a pick close to the one before it is penalised. The 0.80 rule gives way when it would leave a slot empty: a 48-slot method ran out of unlike shots at slot 47 on a shoot of 1,180 gated shots (`M`). Picks are spread across shot sizes and across the people and places the index can tell apart. Two methods want the opposite, one protagonist throughout (`B8 L10`, `B4 L12`); they say so in their slots. Variety also holds across the videos of one shoot: a take that another method's plans already use counts as used once. Planned apart, two methods' best plans shared five of their seven takes (`M`).
6. **One colour state per edit.** Every breakdown with a look applies one look over the whole timeline (`B1 L44`, `B10 L77`, `B4 L45`, `B8 L10`). An edit draws on graded finals or on camera originals through one transform, never both, and among originals one camera profile (`M`: the first drafts mixed them). The build checks what it can (T30): `assemble_timeline` runs `detect` on every source, on a colour-managed project tags the clips it imports with the input colour space `detect` found, reports a source already in the pool that is log by `detect` and reads Rec.709, and notes a manifest whose sources carry more than one input colour space. It does not choose a look cube: the draft uses the camera cube (`<look> [<camera>].cube`) and the colour-managed build takes the look's `[RCM]` cube.
7. **Identifiable minors.** A probe ranks picks by how likely a child is in frame. The probe clears nothing: someone reads every pick at full size. An identifiable minor is swapped out and the shot goes on a list of takes the operator rules out for that shoot, which whatever planner builds the plan must honour. The operator's exclude list is passed to the drafts (`--exclude`) and to `assemble_timeline`, `cut` and `import_timeline` (`exclude`), which refuse a manifest that names an excluded take (a span's source or, for `assemble_timeline`, the music file) or an FCPXML file with a clip from one, so a hand-built composite is checked too; a take that is mostly children goes on it whole, and an excluded shot takes the take's other copies with it. On a festival shoot the probe ranked a plainly identifiable child fourth of seven picks, so its order only says where to start reading (`M`). Where a shoot has many children, read every gated shot once before planning: a small model reading the thumbnails flagged 425 of 1,180, and a full-size read of the flags it called a face, with one sample from each heavily flagged take, confirmed five shots and left four as a question about one person's age (`M`). A baby in a six-frame slot passed the first draft review (`M`), and a face planner once centred a vertical crop on a child at the frame edge (`T16`). A person whose age cannot be judged is a question for whoever was at the shoot.
8. **A shoot has a take budget.** Variants of one method must differ in at least half their takes, so N variants of an S-slot method need S × (1 + (N − 1)/2) distinct gated takes, half that when the method reuses. On a trip shot on one camera, 43 takes passed the gate: one 30-slot variant filled, the second stopped at 17 and the third at 16, while a 13-slot reuse method filled all three (`M`). The planner says when the budget is short; the answer is fewer variants, a shorter method or a reuse method, never a looser gate.
9. **Taste orders, the person picks.** Candidates and drafts are ordered by the taste profile and the review log, and shown without a recommendation. So far the log holds three answers: the long hold with a punch-in over the even burst and the slow seven-shot cut (`R` 2026-11-09 `method-draft`); a slow seven-shot cut answered not with a letter but with six shots named across the three drafts, built as a composite (`R` 2026-11-10 `slow-cinematic-draft`); and a refusal of a window growing out of black (`R` 2026-11-10). Three of five, and the second says the question should also allow a composite.

## Editing inside a clip

1. **The hard cut is the default.** Fifty of 52 cuts in one edit (`B10 L71`), and the majority in seven more (`B2 L21`, `B8 L89`, `B4 L36`, `B6 L33`, `B7 L14`, `B3 L7`, `B5 L50`). One edit is built on windows instead (`B1 L41`). A dissolve of two or three frames, a wipe or a window is an accent a method places on purpose (`B8 L94`, `B7 L24`, `B3 L28`).
2. **A burst slot centres on the action.** Under half a second there is room for one event, so the slot sits on the peak of the clip's motion: the jump at its top, the hand at the cap (`B3 L16`, `B7 L27-L28`, `B3 L19`). The peak is the subject's, not the camera's: in the first beat-burst drafts five of thirty bursts per variant landed on a whip pan, their centres measuring 0.078 to 0.172 on the motion curve where every readable burst measured 0.073 or less, so a burst takes the highest peak under 0.07 and a window entirely above it takes its stillest frame (`M`).
3. **A hold opens on a settled frame.** The camera in these edits is locked or close to it and the motion is the subject's (`B1 L20-L26`, `B7 L22-L32`, `B10 L17-L66`, `B8 L22-L39`). A hold starts after any camera move has ended and does not end mid-smear. Its out point is the beat (`B1 L31`).
4. **Keep away from the ends of a take.** In and out stay 0.2 s inside the take, and a clip must be 0.4 s longer than its slot. The gate reads one keyframe per shot, and the first and last frames of a take are where the camera is still being raised or lowered.
5. **Shot length follows the method's grid.**

   | Kind | Length | Evidence |
   |---|---|---|
   | Burst | 0.1 to 0.3 s, an eighth note at 120 to 150 BPM | `B10 L6`, `B2 L11`, `B6 L47`, `B8 L9` |
   | Hero hold in a burst | 0.4 to 0.8 s | `B10 L84` |
   | Hold | the beat times a power of two: 0.5, 1, 2, 4 s at 120 BPM | `B7 L16`, `B4 L7` |
   | Opening hold | about 2 to 5 s | `B10 L16`, `B6 L13`, `B7 L22`, `B2 L19` |
   | Breath | 1 to 1.4 s inside a burst | `B10 L59`, `B6 L46` |

   The shape that recurs is hold, burst, breath, end (`B6 L46`, `B10 L84`, `B8 L8`, `B2 L19-L21`, `B7 L22-L23`). With music, a slot is counted in beats of the method's own tempo, to the half beat, and laid on the track's beats from a chosen entry beat. When the track runs nearer double or half the method's tempo, every other beat or the half beats are used. Each cut is then rounded to the frame grid from the edit's first frame, so frames do not drift, and the in point absorbs the difference. Snapping each cut to the nearest quarter beat instead doubled the length of every burst (`M`).
6. **Speed is real time, with one or two exceptions.** Three edits retime nothing (`B1 L13`, `B2 L21`, `B7 L22-L32`) and three more slow or ramp one to three shots (`B6 L20`, `B3 L31`, `B10 L33`). When a method slows a shot it is to 50 to 80 percent (`B6 L60`, `B10 L33`). In Resolve the retime is the `retime` macro (`T5`); the Edit page's own speed has no script call.
7. **Reframing is an accent.** A punch-in goes from 1.00 to 1.15 over five frames, eased out, inside one take (`B6 L14`). A push goes to 1.12 over two seconds (`B2 L28`). The pivot sits on the crop centre, never the frame centre (`T7`, `T15`). When the subject crosses the frame in a vertical crop, the crop follows it with a keyed pan (`T14`).
8. **The opening is one of three forms, named in slot 1.** A word or title on screen inside the first third of a second (`B8 L22`, `B7 L22`). A flash from white that resolves in half a second (`B3 L16`, `B4 L18`). Or a strong image held with no device at all, for 1.7 to 11 s (`B6 L13`, `B1 L20`). The breakdowns do not agree, so each method states which it uses and the person's picks decide between them (`R` 2026-11-09 `method-draft`: the held image with a punch, one of five).
9. **The ending leaves on black.** Seven of ten end on a fade or a cut to black, from a 0.4 s fade (`B10 L67`) to 3 s (`B4 L30`), or a hard cut with 0.7 to 3 s of black after it (`B6 L27`, `B7 L33`). Two hold the last image (`B1 L26`, `B5 L24`). `rules.end` carries it. The picture does not fade by script: a dip to black is the `dip-to-black` macro or a transition from `ui_steps`. The music fades in its bed, one second baked in by ffmpeg (`T18`): the fade is the last second of the bed, which is the end black plus, when the black is under a second (`B6 L27`, `B7 L33`), the end of the picture, and a method that holds its last image fades over the last second of picture.

## Resolve by page

The pick is built at 16:9 first and finished at 9:16 as a second step, on ` [auto]` timelines. Renders are queued and never started.

**Edit page.** `assemble_timeline` builds the manifest by appending each span from its clip, so sources at 24, 29.97, 30 and 60 fps sit on one timeline (`T1`), with static scales, each dissolve added on the outgoing item by `AddTransition` and read back (`T24`), and the music under it as a bed cut by ffmpeg with a one second fade out baked in (`T18`). `import_timeline` lands the same manifest from FCPXML with its dissolves and its layers on V2, and needs every source at the timeline's rate (`T10`). `Scaling`, Pan, Tilt and Zoom are item properties a script sets and reads back. `SetFades` reads back too and is not trusted for sound (`T18`). The Edit page cannot be scripted for speed, speed ramps, Inspector keyframes or Dynamic Zoom rectangles; each has a Fusion equivalent below.

<a id="music-bed-check"></a>To check a music bed on a build: read the audio track with `timeline_items` (`track_type` audio) and expect one item on A1 starting at the timeline's first frame with a duration equal to the picture plus end black; ffprobe the WAV beside the manifest (24-bit 48 kHz stereo); `volumedetect` on 0.1 s windows shows the fade, about 24 dB lower at 16.5 s than at 14.5 s on a 16.6 s bed. Live-tested 2026-11-11 on a 399-frame 24 fps build: all of that held. Whether the render keeps the fade is proved only by `deliver_check` on the rendered file (its `tail` row), because the fade lives in the WAV and the tools never start the render. Live-tested 2026-11-11 on the rendered file: the `tail` row passed and an independent decode showed the one second fade ending in zeros, 27 to 33 dB under the reference window over the last 100 ms, no hard cut; the row's own figure then (64 dB) overstated the fade because it measured the file's end, codec padding included; the row now measures the 100 ms ending at the last video frame, and no render has been re-checked with that window yet ([T18](resolve-traps.md)).

**Fusion page.** Everything animated lives in a Fusion comp on the item, applied by `apply_effect` from a macro ([fusion-macros.md](fusion-macros.md)): the punch and push, the word, the flash, the dip, windows, the retime (`T5`) and the moving pan (`T14`). Comp time counts source frames, so `apply_effect` scales a macro's keys by the item's source frames over its timeline frames (`T4`); handles are offsets (`T13`), and every comp is refreshed after wiring or Resolve renders the old picture (`T3`). A person matte comes from the offline `cutout` tool as a clip with alpha on the track above, so a title sits behind a person with no click in Resolve. Trackers run only from the UI.

**Color page.** A script puts a LUT on a node (`apply_grade`) and sets a CDL. It cannot add a node, a window or a qualifier. A look that needs any of those is graded once by hand, captured as a `.drx`, and applied by script from then on (`T19`). Magic Mask needs a stroke drawn first (`T21`). BRAW Decode Using Clip is a computer-use step (`T20`).

| Step | Script | A `.drx` captured once | Computer use |
|---|---|---|---|
| Cuts, dissolves, static scales, music | `assemble_timeline` | | |
| Layers on V2 | `import_timeline`, sources at the timeline's rate | | |
| A dip to black or a wipe at a cut | | | Effects > Video Transitions, from the manifest's `ui_steps` |
| Music fade out | `assemble_timeline`, baked into the bed (`T18`); [how to check it](#music-bed-check) | | |
| Picture fade in and out | | | menu fallback (`T11`) |
| Punch, push, word, flash, dip, windows | `apply_effect` | | |
| Constant speed and ramps | `retime` macro | | Edit page Retime Curve, if the Fusion retime will not do |
| Pan that follows a subject | `reframe-pan` macro | | |
| Title behind a person | `cutout` layer plus `text-word` | | Magic Mask, if the matte is poor |
| A look from a LUT | `apply_grade` | | |
| A look with windows or qualifiers | apply the `.drx` | grade it once by hand | drawing the windows |
| BRAW decode from the clip | | | Camera Raw palette |
| 9:16 crops | `vertical plan`, `vertical draft`, `vertical review`, then the `vertical_apply` tool | | a crop centre set by hand goes in the overrides file |

**The vertical finish.** A 9:16 version is a copy of the built timeline, never a re-edit. `vertical plan` chooses a crop centre per item, in this order: a centre set by hand in the overrides file always wins; else the face planner's, only when run with `--centre face`; else the edge rule, the 16:9 crop carried over to the portrait window's range, or the middle when the build had no crop. `--centre edge`, the default, skips the face planner. The face planner is not the default because on the first built pick it passed three of seven items and every one of its centres was wrong for the edit (the post beside a face, a press beside the man, the gap between two dancers, a bystander instead of the bucket), so all seven were set by hand from the sheet and the plan re-run without the face pass (`M`). The planner names a face; the edit names a subject. `--out` is a folder that must not exist yet; the command makes it and puts `crops.json` and the review sheets in it. The sheets are `review-01.jpg`, `review-02.jpg` and so on, six items a sheet, each item's frames with its crop box drawn and its number in the corner. Someone reads them (`T16`). Then `vertical draft crops.json` cuts `preview.mp4` into the same folder: a silent 540x960 cut of every item at its centre, in order, with the item number in the corner. An item whose window opens over the shot before it carries `"window": "scale-window"` or `"crop-window"` in the items file given to `vertical plan` (it is copied into `crops.json`, so a changed window makes `vertical review` ask for a new draft); the preview then draws that item as the draft farm draws a window, over the previous item's shot playing on from its out point and framed with that item's crop, the plate the build puts on V1, and refuses a previous shot with less handle than the plate needs. The plate's frame counts are the macro's, read at the preview's 30 fps. The operator watches that preview before anything is written to Resolve, on every build. `vertical review` refuses until `vertical draft` has written `preview.mp4` (live-tested 2026-11-11). The preview needs a source ffmpeg can read: a BRAW item is drawn from a proxy, the item's `proxy` in `crops.json` or `vertical plan` and `vertical draft` with `--proxy-map FILE` (source path to proxy path), and with no proxy the command refuses and names the two ways forward ([T31](resolve-traps.md)). A retimed item carries `speed` in `crops.json` (the manifest's retime `params.speed`, set in the items file given to `vertical plan`, 1 when absent) and is drawn at that speed over its `out - in`, so the cut length matches the build. Edge-clamped centres are already inside the plan, so the preview box and the applied Pan end flush with the frame edge and show no black (by the Pan arithmetic, [T15](resolve-traps.md); live-tested 2026-11-11 on a render: no black on the clamped items and every centre within 0.1 px of the plan). Items they want moved go into the overrides file and `vertical plan` runs again into a new folder. `vertical review crops.json` marks the plan reviewed once the sheets are read and the preview is watched, and refuses unless `preview.mp4` sits beside `crops.json` and was drawn by `vertical draft` from the current crops (draft records a hash of the items in `preview.json`, so a centre changed after the draft makes review say to run `vertical draft` again); `vertical_apply` refuses a plan that is not marked. `vertical_apply` then sets fill on each item (`T12`), the Pan (`T15`), the punch pivot and the title's centre and size, and refreshes the comps, and everything reads back. Only the `zoom-push`, `text-word`, `person-matte-text`, `scale-window` and `crop-window` comps move; any other comp on an item stays where it was built, and the result's `unmoved_comps` names each item and comp so it can be moved by hand. A `crop-window` has `Rectangle1.Center` set to the crop centre; a `scale-window` has `Transform1.Pivot` set there and nothing else (its Center keys are left alone), both read back ([fusion-macros.md](fusion-macros.md#at-916)). A `person-matte-text` comp has its word centred on the crop (`Text1.Center` x = the crop centre, y kept) and refitted in size; its matte is left where it is, because the matte sits on the subject in the source frame and the crop does not move the subject. The stand-in rectangle is still one frame's fit: the dry run says so in that comp's `notes`, and the tracked Magic Mask replaces it before delivery. Pan is written with the 9:16 timeline current, because an item's Pan is in the current timeline's pixels (`T27`): on the second build every pan landed at 1080/3840 of the shift and its read-back still passed. A still cannot prove the result (`T26`); a timeline render through Resolve's own MCP can, and a template match of it inside the source frame found each window within 0.003 of its planned centre. When the montage is on V2 over window plates, `vertical_apply` with that track frames each plate with the crop of the item it continues (the one just before the window span), so the outgoing shot holds still at the cut; the plates are found on the timeline, and anything else on V1 is left alone and named. Live-tested 2026-11-11 on a 7-item montage with two plates (37 and 26 frames): each V1 plate started on the same frame as its V2 item, and its Pan equalled the Pan of the item it continues, not its own window item's; the plan, the real run's `plate_results` and a `timeline_items` read agreed. Rendered 2026-11-11: the outgoing shot held its framing across both cuts, but both windows opened off the crop centre of the portrait item ([T34](resolve-traps.md), [vertical_apply](resolve-mcp.md)). A music montage with no speech goes out through `social_9x16_music` (`T17`).

**What holds attention differs by shape.** At 16:9 the wide carries the edit and a title has room beside or above the subject (`B10 L16`). At 9:16 the crop keeps a third of the width, so the subject must sit inside it for the whole slot, a title shrinks to about a third of its size (`T15`), and a shot whose subject crosses the frame needs the pan or a different clip.

## The methods

| id | Aspect, length | Shape | From |
|---|---|---|---|
| `punch-montage` | 16:9, 13.46 s, 15 slots | A 1.7 s establishing push, the same take held tighter after a 1.15 punch-in, a 3 s word plate, an accelerating burst of 4 to 5 frame shots, two breaths (one slowed to 70 percent, one tilt), a flash entry, a final burst of 3 to 5 frame shots and 0.7 s of black. Exact cut times kept. | one breakdown |
| `beat-burst` | 9:16, 12.55 s, 30 slots | A 1.85 s title behind the people in a wide, then quarter-second shots on a 120 to 135 BPM grid with hero holds of 0.45 to 0.8 s, one flash entry, two crop windows, a held last wide and a short black. | two breakdowns |
| `slow-cinematic` | 16:9, 18.04 s, 7 slots | Seven 1.3 to 4.3 s shots of one person: a flash-in close, cuts on matching action, a 29-frame hard-edge wipe between two wides (a UI step), a dip to black on the closing wide. | one breakdown |
| `scale-window-stack` | 16:9, 28.58 s, 7 slots | An 11 s performance hold, a hard cut, four shots that each grow from a small window and snap full on the beat (about 77 BPM), a still hold to end. | one breakdown |
| `cutout-clones` | 16:9, 23.54 s, 13 slots | One person through haze and open ground: flash entries, a clone of the person in a locked-off plate, a copy that grows into the next shot, words on the beat, a long fade. | one breakdown |
| `tempo-ladder` | 16:9, 16.5 s, 18 slots | Holds that halve on the beat grid: one of 4 s, two of 2 s, two of 1 s, four of 0.5 s, eight of 0.25 s, then a 2 s breath that dips to black. Plain cuts throughout. | four breakdowns |
| `matte-title` | 16:9, 15.5 s, 8 slots | One word held behind the people in a 4 s opening wide, hard on and hard off, then holds of 1 and 2 s and a 3 s closing wide into black. | three breakdowns |
| `word-burst` | 16:9, 18.67 s, 48 slots | A lyric edit: one word per cut over one person looking out (18 slots), a 23-shot burst of 0.15 to 0.2 s with three 2-frame dissolves, an outro of 7 slots that reuses opening shots, and 1.67 s of black for a last word. | two breakdowns |

## What a method cannot carry yet

- A dip or a wipe between two shots is a UI step: FCPXML carries cuts, dissolves, static scales and lanes only. Retimes, words, the punch and the closing dip are `apply_effect` comps. Letterbox moves, typewriter text and the clone in `cutout-clones` (two takes of one locked-off frame, or a `cutout` layer, [reel.md](reel.md)) have no macro and no step yet.
- An item holds one Fusion comp. A slot takes one effect, a retimed slot takes none, and a 9:16 moving pan cannot go on a slot that already has an effect.
- `to_manifest` keeps the times of the method it is given. Laying slots on a track's beats is the planner's job: it hands `to_manifest` a copy of the method with each slot's `seconds` re-timed, and records where the music starts.
- Three macros need a person's eye before delivery: `person-matte-text` (a placeholder matte), `exposure-flash` (its strength) and `text-word` (font and colour). See [fusion-macros.md](fusion-macros.md#needs-a-persons-eye).
- The windows (`scale-window`, `crop-window`) leave the picture around them transparent, which is black on one track, and a window must arise out of the image before it, never out of black: the first reviewer refused `scale-window-stack` on sight for that. The rule is that the outgoing shot keeps playing under the window. `assemble_timeline` builds it: the montage goes on V2 and the previous shot carries on from its out point on V1 under each window, for 37 frames (`scale-window`) or 26 (`crop-window`), and a source with less handle than that is refused. The draft farm draws the same plate, so a draft and a build show one thing. Pass the result's `track` (2) to `apply_effect`, `apply_grade`, `vertical_apply` and `reframe_apply`, and grade V1 too ([fusion-macros.md](fusion-macros.md)). `scale-window-stack` is no longer parked for this. Live-tested 2026-11-11: it was re-drafted on real footage and the notes say "over the previous shot playing on"; the build was run on a 7-span manifest with a `scale-window` and a `crop-window`, and the montage read back on V2 as spans 1 to 7 at their frames, with V1 holding two plates (37 and 26 frames), each starting on the first source frame after the previous span's last. Frames read through Resolve's image tool at the start, middle and end of each window showed the outgoing shot playing on around the window. A 9:16 copy of that build was rendered 2026-11-11: the plate held the outgoing shot's framing across the cut, and the windows opened off the crop centre ([T34](resolve-traps.md)); `vertical_apply` now centres both, a fix a render has not yet confirmed. `cross-dissolve-short` still needs its incoming item moved to V2 by hand.

## Checks

`tests/test_methods.py`: every shipped method loads and validates, its file name is its id, its roles are in the vocabulary and its effects are macros; `validate` names each kind of fault; every method's `to_manifest` output validates as a version 2 manifest and writes as FCPXML with the method's frame count, one transition per dissolve and the effects list; short, relative and overlapping fills are refused; `reuse` marks the later span; `edit_methods` lists and checks; no method or macro holds a link, a handle or a home path. `tests/test_effect.py` covers `apply_effect`, its params, the key scaling and the refresh; `tests/test_vertical.py` covers the 9:16 plan, the review gate and `vertical_apply`; `tests/test_assemble.py` covers the span arithmetic at mixed rates, the music and its fade, the read-back and the refusals of `assemble_timeline`; `tests/test_drafts.py` renders synthetic plans at both shapes, checks length, rate, notes, the look cube, the sheet, BRAW proxies, the git refusal and the lane tuner.
