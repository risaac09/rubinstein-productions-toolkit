# Fusion macros

> **Status:** operational 2026-11-09 for the first eight macros, each built and checked by eye in a sandbox project on Resolve Studio 21.2.0.6, and each applied there through `apply_effect`, read back with every tool, keyframe and link in place. Three need a person's eye before delivery (below). `retime` and `reframe-pan` came later: their keys and wiring are covered by unit tests, and the traps record the Resolve measurements they rest on (`retime` on a still, T4 and T5; the pan recipe, T14). No `apply_effect` run of either on a sandbox timeline is recorded here yet. `exposure-flash` takes a `gain` param since 2026-11-10 (T28), checked on a 5-frame item against a draft frame. The Magic Mask recipe for `person-matte-text` ([below](#person-matte-the-magic-mask-recipe)) has never been run live.

`production/fusion-macros/<id>.setting` holds the tools of one short-form effect without MediaIn or MediaOut, so it fits any clip's Fusion comp. The MCP `apply_effect` tool puts one on items of an ` [auto]` timeline; by hand, drag the `.setting` onto a clip's Fusion node area and wire it as the table says. `python3 -m rpresolve.cmd_effect` lists every macro with its wiring and checks that each parses.

Values are normalised Fusion values: positions and sizes are 0 to 1 of the frame, centre 0.5/0.5. Keyframes are written in timeline frames at 24 fps, counted from the item's first frame. A comp on a timeline item counts source frames, not timeline frames, so `apply_effect` multiplies every key time by the item's source frames over its timeline frames (1.25 for a 30 fps clip on a 24 fps timeline) and reports the factor as `key_scale`. Pasted by hand, a macro keyed over 48 frames lasts 48 source frames of the clip.

| id | What it does | Key inputs | Wiring |
|---|---|---|---|
| `zoom-push` | A slow push-in on the whole picture. | `Transform1.Size` 1.0 at frame 0 to 1.15 at frame 48, eased in and out. For a punch, move the second key to frame 5. | MediaIn1 → `Transform1` → MediaOut1 |
| `text-word` | One word over the picture, popping in. | `Text1`: StyledText (one word), Size 0.14, Center 0.5/0.62, CharacterSpacing 1.2, white at alpha 0.75, Arial Bold. `Transform1.Size` 0.6 at frame 0 to 1.0 at frame 5, ease-out. | MediaIn1 → `Merge1.Background`; `Text1` → `Transform1` → `Merge1.Foreground`; `Merge1` → MediaOut1 |
| `person-matte-text` | A word behind the subject: the text sits on the picture, then the subject is laid back over it through a matte. | `Text1`: one word, Size 0.32, Center 0.55/0.55, red-orange (0.843, 0.216, 0.122), Arial Bold. `SubjectMatte_PLACEHOLDER`: a static rectangle, Width 0.05, Height 1, Center 0.623/0.5. | MediaIn1 → `Merge1.Background` and `Merge2.Foreground`; `Text1` → `Merge1.Foreground`; `Merge1` → `Merge2.Background`; matte → `Merge2.EffectMask`; `Merge2` → MediaOut1 |
| `exposure-flash` | A flash into the shot: the picture starts bright and settles. | `ColorCorrector1.MasterRGBGain` 2.0 at frame 0 to 1.0 at frame 12, ease-out (+1 stop). | MediaIn1 → `ColorCorrector1` → MediaOut1 |
| `dip-to-black` | The outgoing half of a dip: the picture falls to black over its last 18 frames. | `BrightnessContrast1.Gain` 1.0, then 0.5, 0.25, 0.125, 0.06, 0.02, 0 every 3 frames (even in stops, since gain acts on linear light in a colour-managed project; a straight 1-to-0 ramp reads as a late snap). The macro's keys are a hold at 1.0 on frame 0 and the fall on frames 149 to 167; `apply_effect` moves all of them so the last lands on the item's last frame, which puts the hold before frame 0 on an item shorter than the macro (T25). | MediaIn1 → `BrightnessContrast1` → MediaOut1 |
| `cross-dissolve-short` | A 3-frame fade-in of the incoming clip over the outgoing one. | `Merge1.Blend` 0 at frame 0 to 1 at frame 3, linear; `Background1` transparent. | MediaIn1 → `Merge1.Foreground`; `Background1` → `Merge1.Background`; `Merge1` → MediaOut1 |
| `scale-window` | The incoming clip grows from a small window and snaps to full frame on the beat. | `Transform1.Size` 0.22 at frame 0, linear to 0.72 at frame 36, 1.0 at frame 37 (the one-frame snap). `Transform1.Center` on a path drifting about ±0.05 around the centre over frames 0 to 36. Hard edge, no border. | MediaIn1 → `Transform1` → MediaOut1 |
| `crop-window` | The clip shown in a narrow rectangle that opens, holds, then opens to full frame. | `Rectangle1.Width` 0 at frame 0 to 0.25 at frame 8 (ease-out), held to 20, 1.0 at 26; `Height` 0.6 to 1.0 over 20 to 26; SoftEdge 0; `Background1` transparent. | MediaIn1 → `Merge1.Foreground`; `Background1` → `Merge1.Background`; `Rectangle1` → `Merge1.EffectMask`; `Merge1` → MediaOut1 |
| `retime` | A speed change for the item, as a Fusion comp, since no script call sets Edit page speed. | `TimeStretcher1.SourceTime` on a `BezierSpline`, `InterpolateBetweenFrames` 0. The keys are built per item from the params: source time 0 at frame 0 and `speed` x N at frame N, N being the item's length in source frames. With `speed_to`, the speed changes linearly and the end value is the mean speed x N. The `.setting` holds the tool and its static input only. Do not use `TimeSpeed`: it anchors on the clip's source frame 0. | MediaIn1 → `TimeStretcher1` → MediaOut1 |
| `reframe-pan` | A crop that follows a subject across the frame. | `Transform1.Center` on an `XYPath` whose X is a `BezierSpline` keyed linearly from 0.5 + `pan_base` - `pan_from` at frame 0 to 0.5 + `pan_base` - `pan_to` at frame N; Y fixed at 0.5. `pan_base` is the crop centre the item's own Pan already holds (default 0.5). | MediaIn1 → `Transform1` → MediaOut1 |

### Transitions: the outgoing shot keeps playing under a window

The three transitions (`cross-dissolve-short`, `scale-window`, `crop-window`) leave the picture transparent around or under the incoming clip. On one video track that is black. The operator's rule for the two windows (2026-11-10): the outgoing shot keeps playing underneath while the window grows or opens, never black and never a frozen frame (Resolve's scripting API has no freeze frame in any case, so a V2 build over a held frame is not on offer). Higher tracks draw on top in Resolve, so the incoming item with the window comp has to sit above the outgoing shot's continuation.

`assemble_timeline` builds that. For each span after the first whose manifest `effects` name a `scale-window` or a `crop-window`, it puts a plate on V1: the previous span's clip, counted on from where that span's source stopped, placed at the window span's first frame, with the previous span's static scale and nothing else of it (no comp, no retime). The plate runs 37 frames for `scale-window` (the window is full frame at frame 37) and 26 for `crop-window` (full at frame 26), or the span's length when the span is shorter. The montage then goes on V2, every span in the order and at the numbering it always had (item N is span N on V2), and V1 holds only the plates. A manifest with no such span is built on V1 alone, as before. The result's `track` says which.

- Pass the montage track (the result's `track`, 2 when there are plates) to `apply_effect`, `apply_grade`, `vertical_apply` and `reframe_apply`. On V1 the items are plates, not spans: `apply_effect` there would number them 1, 2, 3 and put a comp on the wrong clip. The write tools' dry runs name the other video tracks that hold items (a `CHECK THE TRACK` line in the summary) when the track is not the one with the montage.
- Grade V1 as well (`apply_grade` with track 1): the plates are picture, and the draft farm draws them with the look.
- A plate needs handle. The outgoing source must run on past its out point for the plate's length; the dry run refuses a span whose source has fewer frames than that (37 or 26), reading the length with ffprobe. A file ffprobe cannot read (BRAW) is not checked in the dry run; the read-back then reports a plate that came out short, and the timeline is left in place.
- The first span has no shot before it. Its window opens over black, and the plan lists that under `not_built`.
- `vertical_apply` finishes the track it is given, and on track 2 also the plates under it: a plate is found on the timeline (it starts where montage item N starts and plays the file of item N - 1) and takes item N - 1's crop with its own zoom, so the outgoing shot does not move at the cut. Anything on V1 that is not such a plate keeps its framing and is named in `unframed_below`. Look at the first window of every 9:16 by eye.
- The draft farm draws the same plates (the previous shot playing on from its out point, through the same fill-and-crop, static scale and look), and refuses the same missing handle.
- Not yet run in a sandbox project: `AddTrack("video")`, `AppendToTimeline` with `trackIndex` 2 and `recordFrame`, and the window comps over a plate on V1 are covered by the unit fakes and the ffmpeg drafts only.

`cross-dissolve-short` still needs its incoming item moved to V2 over the end of the outgoing one by hand.

## Params

`apply_effect` takes an optional `params` object. Only these keys are accepted, and only for the macros listed; any other key, or a value out of range, is refused before anything is written. The params are part of the plan, so they change its `plan_sha`.

| key | macros | Meaning |
|---|---|---|
| `text` | `text-word`, `person-matte-text` | The word, set on `Text1.StyledText`. |
| `size` | `text-word`, `person-matte-text` | `Text1.Size`, above 0. |
| `centre` | `zoom-push`, `text-word` | 0 to 1 across the frame. Sets `Transform1.Pivot` (zoom-push) or `Merge1.Center` (text-word) to this x and 0.5. Use the crop centre, because Fusion comp space is the whole source frame and a vertical crop moves only the picture. |
| `gain` | `exposure-flash` | The flash's peak, 1 to 20; default 2 (+1 stop). Written into the first key of `ColorCorrector1MasterRGBGain`, which still settles to 1 at the macro's span end. A fill's `gain` reaches it through `to_manifest` as `params.gain`, and the draft draws the same number (T28). |
| `speed`, `speed_to` | `retime` | `speed` is required; `speed_to` makes a linear ramp. |
| `pan_from`, `pan_to`, `pan_base` | `reframe-pan` | `pan_from` and `pan_to` are required. |

Each value is read back and reported under `params` in the item's result.

## Needs a person's eye

- **`person-matte-text`**: the matte is a placeholder rectangle that fitted one test frame. Replace it with a Person Magic Mask (Better) tracked on the item, edge softness 2 to 5 px, by computer use: [the recipe](#person-matte-the-magic-mask-recipe). The draft cannot show the matte: it draws the title as a box in front of the subject, so the pick is made on the title's position and size, and the matte is first seen in the build.
- **`exposure-flash`**: +1 stop may read too weak beside the edits it came from. Try gain 3.0, or a white dip. Choose the value on the draft: put it on the fill as `gain` and the draft draws that peak falling to 1 over 12 frames, and `apply_effect` writes the same number into the macro. The draft's flash is a stand-in fitted on one dark frame (gain 2 draws about 62 where the macro read 65, gain 10 about 120 where it read 117), so it can read differently on a bright shot or after a look; compare one rendered frame of the build before committing ([T28](resolve-traps.md)).
- **`text-word`** (and the text in `person-matte-text`): Arial Bold stands in for a heavy or extended face (the font family "Arial Black" set by script renders nothing; pick the real face in the Inspector; "Arial" with Style "Bold" works), and the red-orange reads more orange through a colour-managed output transform.

## Person matte: the Magic Mask recipe

> **Status: written 2026-11-10 from the scripting docs and the Resolve release notes. Never run live.** The control names below are the ones expected in Resolve Studio 21.2, not ones seen on screen. The first run is on the sandbox project only, on a test clip, never on a project that holds client work. Where the screen differs from a step, stop, say what it showed, and do not guess. After that run, replace this paragraph with the date, the Resolve build and the labels actually seen.

`person-matte-text` lays the subject back over the word through `Merge2.EffectMask`. `apply_effect` leaves `SubjectMatte_PLACEHOLDER` there, a rectangle that fitted one frame. The real matte is a Person Magic Mask, made and tracked in the UI by computer use: the session runs these steps after asking for access, and they are never left for the operator. The scripting API cannot do it. `TimelineItem.CreateMagicMask("F")` returned False on an item nobody had drawn on ([T21](resolve-traps.md)); whether it tracks after a stroke drawn by hand, or whether `RegenerateMagicMask` helps, is untested, so these steps use the Track buttons. Magic Mask v2 has been in the Fusion page since Resolve 20.1.

The method this serves, `matte-title`, opens on a still wide held for 4 s. That is the easy case for a tracker; a moving camera will fail more often.

**Before any click**

1. `resolve_status`: the open project is the sandbox, and the timeline name ends ` [auto]`. Stop if either differs.
2. `apply_effect` has put `person-matte-text` on the item, and its read-back shows the word on `Text1`.
3. Pick three timeline timecodes in the item: its first frame, a middle frame where the subject is largest with nothing in front of them (the stroke frame), and its last frame. Add a fourth where the subject moves most.
4. Take the "before" pictures: Resolve `get_image` (source `timeline`, `timecode` each, `max_dimension` 2048) at those frames. Save them outside any git tree and write down what each shows: the word, and the placeholder's thin vertical strip.

**Access**

Ask for access to DaVinci Resolve alone, saying what for: to draw and track a Magic Mask on one clip of the sandbox project. Resolve is a full-control app; no browser or terminal is needed. If access is refused, stop: the span goes without the matte and the build says so. Take a screenshot after every step below.

**Steps**

1. Edit page. Click the item on the timeline (the clip, not an empty track) and put the playhead on the stroke frame.
2. Open the Fusion page (the page bar, or Shift+5). If the item holds more than one comp, open `rp-fx person-matte-text` from the comp selector above the node area. The nodes must be `MediaIn1`, `Text1`, `Merge1`, `Merge2`, `SubjectMatte_PLACEHOLDER` and `MediaOut1`. Stop if they are not.
3. Click `MediaIn1`, then add the tool: Effects Library > Tools > Mask > Magic Mask, or Shift+Space in the node area and type `Magic Mask`. It must take `MediaIn1` as its input. Read the node graph: `MediaIn1` must still feed `Merge1.Background` and `Merge2.Foreground`, and `Merge1` must still feed `Merge2.Background`. If the new node spliced itself into that chain, undo (Cmd+Z) and place it by clicking empty space first.
4. Show it in a viewer (select the node, press 1). In the Inspector set the mode to Person (not Features) and the quality to Better.
5. On the stroke frame, draw one stroke in the viewer down the middle of the subject, head to hips, clear of the hair, the edges and the background. Draw one stroke per person who should sit in front of the word. The mask must tint the whole person and nothing behind them.
6. Track Forward and wait for it to finish; then Track Reverse (or a both-ways button, if the node has one). Do not move the playhead or change page while it runs. It has reached both ends when the mask follows the person on the item's first and last frames.
7. Set the edge softness to 2 to 5 px, starting at 3. If the tool has no such control, judge the edge on a frame in the check below and add a small blur to the matte; do not leave a hard edge.
8. Wire it: drag from the Magic Mask output to the `EffectMask` input of `Merge2`. That replaces the link from the placeholder. Then select `SubjectMatte_PLACEHOLDER` and delete it. If Fusion refuses the link, or the word does not clip, read the screenshot and use the output the tool offers as a matte; do not guess.
9. Read the graph back before judging the picture, as `apply_effect` does for its own writes. Select `Merge2` and screenshot the node area. It passes when: no node is named `SubjectMatte_PLACEHOLDER`; the Magic Mask node is the only line into `Merge2`'s `EffectMask` input; `MediaIn1` feeds `Merge1.Background` and `Merge2.Foreground`, `Merge1` feeds `Merge2.Background`, and `Merge2` feeds `MediaOut1`. If the Fusion console (Workspace > Console, Lua) is open, `print(comp:FindTool("SubjectMatte_PLACEHOLDER"))` should print nil and `print(comp:FindTool("Merge2").EffectMask:GetConnectedOutput():GetTool().Name)` should print the Magic Mask node's name; these two lines are expected, not seen, so if one errors, fall back on the screenshot. A matte wired to nothing can still look right on a still wide, so this step is not optional.
10. Return to the Edit page, move the playhead off the item and back, and let the viewer settle. Leave nothing queued and nothing saved as a new project.

**Check with `get_image`**

After the graph read-back passes, take the same frames as the "before" pictures, at the same size, then zoom on the word with `roi_x`, `roi_y`, `roi_width` and `roi_height` and `max_dimension` 2048. It passes when all of these hold:

- Where the subject overlaps the word, no red-orange pixel shows inside the silhouette: the subject is in front.
- The word is whole everywhere the subject is not, and not cut by a box edge.
- No red-orange fringe on the subject's edge, and no ring of background cut away from the word around it.
- The word's position, size and colour match the "before" picture outside the subject.
- The same holds on the first, middle, last and fastest-moving frames.

Two failures that are not tracking: the word shows only inside the subject (the matte is inverted: tick Invert on the Magic Mask), and the "after" picture matches the "before" one exactly (a stale comp, [T3](resolve-traps.md): select `Merge2` and press Ctrl+P twice, or toggle its pass-through in the Inspector, then take the picture again).

**When tracking fails**

Symptoms: the mask drifts onto the background, flickers, drops out for a few frames, grabs a second person, or loses the subject when they turn or pass something. Try these in order, at most two attempts for one item, and write down which ran. Do not split the item to help the tracker: that renumbers the spans `apply_effect` and the ledger count by.

1. Undo back to before the track. On the first frame that went wrong, add a stroke on the part that was missed, and a stroke marked as background on what was wrongly taken. Track again from that frame in the direction that failed only.
2. Move the stroke frame to the frame where the subject is largest and clearest, and track both ways again.

If it still fails, fall back and say so in the build's notes. Make a clip with alpha with the offline `cutout` tool ([edit-assist.md](edit-assist.md#cutout)) for the span, lay it on the track above, and put the word on the picture with `text-word`; or ship the span without the matte. Never leave `SubjectMatte_PLACEHOLDER` in a build: it shows as a stray strip of picture over the word.

**After the first live run**

Replace the status paragraph with what was seen: the date, the Resolve build, which labels matched, which steps changed, how many attempts the tracking took. Add what failed to [resolve-traps.md](resolve-traps.md) as a numbered entry.

## How apply_effect builds a comp

`apply_effect` adds a new Fusion comp to each item (`AddFusionComp`, renamed `rp-fx <id>`; an item whose comps are all emptied `rp-fx` comps gets the macro built into one of those instead, loaded with `LoadFusionCompByName`, [T9](resolve-traps.md)), then builds the macro's tools in it with `AddTool` under their macro names, sets each input's value, adds a `BezierSpline` or `XYPath` modifier for each animated input with the macro's keyframes, connects the links, and wires the chain between MediaIn1 and MediaOut1. It reads the comp back: every tool and modifier present, each animated input's keyframes on the macro's frames, its value halfway between two keys within those keys' values, and the wiring.

Two things found live (Resolve 21.2.0.6) shaped that:

- `Composition.Paste()` with the macro's table, or with Fusion's own `readfile` table, returns False on a comp that is not open on the Fusion page, and the page's current comp is not reliable from a script. `AddTool` works on any comp object.
- `SetKeyFrames` reads a key's `LH` and `RH` handles as offsets from the key, while a `.setting` file stores them as absolute frame and value. Passed through unchanged, the zoom-push handles overshot to 1.70 at frame 24; converted to offsets it reads 1.075. The midpoint check in the read-back catches the first case.

A comp built by script reads back whole and can still render the old picture. After the build, `apply_effect` sets `TOOLB_PassThrough` on and then off on every added tool (`refresh` in `cmd_effect.py`; not MediaIn, MediaOut or the spline and path modifiers), and the read-back fails an item if any of those tools still reads pass-through.

`exposure-flash`'s `ColorRanges` curve (the default shadow and highlight ranges) is not carried and is reported under `skipped`; the gain does not depend on it.
