# Fusion macros

> **Status:** operational 2026-11-09 for the first eight macros, each built and checked by eye in a sandbox project on Resolve Studio 21.2.0.6, and each applied there through `apply_effect`, read back with every tool, keyframe and link in place. Three need a person's eye before delivery (below). `retime` and `reframe-pan` are new and covered by unit tests only until they are applied in a sandbox project.

`production/fusion-macros/<id>.setting` holds the tools of one short-form effect without MediaIn or MediaOut, so it fits any clip's Fusion comp. The MCP `apply_effect` tool puts one on items of an ` [auto]` timeline; by hand, drag the `.setting` onto a clip's Fusion node area and wire it as the table says. `python3 -m rpresolve.cmd_effect` lists every macro with its wiring and checks that each parses.

Values are normalised Fusion values: positions and sizes are 0 to 1 of the frame, centre 0.5/0.5. Keyframes are written in timeline frames at 24 fps, counted from the item's first frame. A comp on a timeline item counts source frames, not timeline frames, so `apply_effect` multiplies every key time by the item's source frames over its timeline frames (1.25 for a 30 fps clip on a 24 fps timeline) and reports the factor as `key_scale`. Pasted by hand, a macro keyed over 48 frames lasts 48 source frames of the clip.

| id | What it does | Key inputs | Wiring |
|---|---|---|---|
| `zoom-push` | A slow push-in on the whole picture. | `Transform1.Size` 1.0 at frame 0 to 1.15 at frame 48, eased in and out. For a punch, move the second key to frame 5. | MediaIn1 → `Transform1` → MediaOut1 |
| `text-word` | One word over the picture, popping in. | `Text1`: StyledText (one word), Size 0.14, Center 0.5/0.62, CharacterSpacing 1.2, white at alpha 0.75, Arial Bold. `Transform1.Size` 0.6 at frame 0 to 1.0 at frame 5, ease-out. | MediaIn1 → `Merge1.Background`; `Text1` → `Transform1` → `Merge1.Foreground`; `Merge1` → MediaOut1 |
| `person-matte-text` | A word behind the subject: the text sits on the picture, then the subject is laid back over it through a matte. | `Text1`: one word, Size 0.32, Center 0.55/0.55, red-orange (0.843, 0.216, 0.122), Arial Bold. `SubjectMatte_PLACEHOLDER`: a static rectangle, Width 0.05, Height 1, Center 0.623/0.5. | MediaIn1 → `Merge1.Background` and `Merge2.Foreground`; `Text1` → `Merge1.Foreground`; `Merge1` → `Merge2.Background`; matte → `Merge2.EffectMask`; `Merge2` → MediaOut1 |
| `exposure-flash` | A flash into the shot: the picture starts bright and settles. | `ColorCorrector1.MasterRGBGain` 2.0 at frame 0 to 1.0 at frame 12, ease-out (+1 stop). | MediaIn1 → `ColorCorrector1` → MediaOut1 |
| `dip-to-black` | The outgoing half of a dip: the picture falls to black over its last 18 frames. | `BrightnessContrast1.Gain` 1.0, then 0.5, 0.25, 0.125, 0.06, 0.02, 0 every 3 frames (even in stops, since gain acts on linear light in a colour-managed project; a straight 1-to-0 ramp reads as a late snap). The macro's keys sit at frames 149 to 167; `apply_effect` moves them so the last lands on the item's last frame. | MediaIn1 → `BrightnessContrast1` → MediaOut1 |
| `cross-dissolve-short` | A 3-frame fade-in of the incoming clip over the outgoing one. | `Merge1.Blend` 0 at frame 0 to 1 at frame 3, linear; `Background1` transparent. | MediaIn1 → `Merge1.Foreground`; `Background1` → `Merge1.Background`; `Merge1` → MediaOut1 |
| `scale-window` | The incoming clip grows from a small window and snaps to full frame on the beat. | `Transform1.Size` 0.22 at frame 0, linear to 0.72 at frame 36, 1.0 at frame 37 (the one-frame snap). `Transform1.Center` on a path drifting about ±0.05 around the centre over frames 0 to 36. Hard edge, no border. | MediaIn1 → `Transform1` → MediaOut1 |
| `crop-window` | The clip shown in a narrow rectangle that opens, holds, then opens to full frame. | `Rectangle1.Width` 0 at frame 0 to 0.25 at frame 8 (ease-out), held to 20, 1.0 at 26; `Height` 0.6 to 1.0 over 20 to 26; SoftEdge 0; `Background1` transparent. | MediaIn1 → `Merge1.Foreground`; `Background1` → `Merge1.Background`; `Rectangle1` → `Merge1.EffectMask`; `Merge1` → MediaOut1 |
| `retime` | A speed change for the item, as a Fusion comp, since no script call sets Edit page speed. | `TimeStretcher1.SourceTime` on a `BezierSpline`, `InterpolateBetweenFrames` 0. The keys are built per item from the params: source time 0 at frame 0 and `speed` x N at frame N, N being the item's length in source frames. With `speed_to`, the speed changes linearly and the end value is the mean speed x N. The `.setting` holds the tool and its static input only. Do not use `TimeSpeed`: it anchors on the clip's source frame 0. | MediaIn1 → `TimeStretcher1` → MediaOut1 |
| `reframe-pan` | A crop that follows a subject across the frame. | `Transform1.Center` on an `XYPath` whose X is a `BezierSpline` keyed linearly from 0.5 + `pan_base` - `pan_from` at frame 0 to 0.5 + `pan_base` - `pan_to` at frame N; Y fixed at 0.5. `pan_base` is the crop centre the item's own Pan already holds (default 0.5). | MediaIn1 → `Transform1` → MediaOut1 |

The three transitions (`cross-dissolve-short`, `scale-window`, `crop-window`) show transparency around or under the incoming clip. On one video track that is black. To see the outgoing shot through it, put the incoming item on V2 over the end of the outgoing one.

## Params

`apply_effect` takes an optional `params` object. Only these keys are accepted, and only for the macros listed; any other key, or a value out of range, is refused before anything is written. The params are part of the plan, so they change its `plan_sha`.

| key | macros | Meaning |
|---|---|---|
| `text` | `text-word`, `person-matte-text` | The word, set on `Text1.StyledText`. |
| `size` | `text-word`, `person-matte-text` | `Text1.Size`, above 0. |
| `centre` | `zoom-push`, `text-word` | 0 to 1 across the frame. Sets `Transform1.Pivot` (zoom-push) or `Merge1.Center` (text-word) to this x and 0.5. Use the crop centre, because Fusion comp space is the whole source frame and a vertical crop moves only the picture. |
| `speed`, `speed_to` | `retime` | `speed` is required; `speed_to` makes a linear ramp. |
| `pan_from`, `pan_to`, `pan_base` | `reframe-pan` | `pan_from` and `pan_to` are required. |

Each value is read back and reported under `params` in the item's result.

## Needs a person's eye

- **`person-matte-text`**: the matte is a placeholder rectangle that fitted one test frame. Replace it with a Magic Mask (Person, Better) tracked on the item, or a Magic Mask node in Fusion, edge softness 2 to 5 px.
- **`exposure-flash`**: +1 stop may read too weak beside the edits it came from. Try gain 3.0, or a white dip.
- **`text-word`** (and the text in `person-matte-text`): Arial Bold stands in for a heavy or extended face (the font family "Arial Black" set by script renders nothing; pick the real face in the Inspector; "Arial" with Style "Bold" works), and the red-orange reads more orange through a colour-managed output transform.

## How apply_effect builds a comp

`apply_effect` adds a new Fusion comp to each item (`AddFusionComp`, renamed `rp-fx <id>`), then builds the macro's tools in it with `AddTool` under their macro names, sets each input's value, adds a `BezierSpline` or `XYPath` modifier for each animated input with the macro's keyframes, connects the links, and wires the chain between MediaIn1 and MediaOut1. It reads the comp back: every tool and modifier present, each animated input's keyframes on the macro's frames, its value halfway between two keys within those keys' values, and the wiring.

Two things found live (Resolve 21.2.0.6) shaped that:

- `Composition.Paste()` with the macro's table, or with Fusion's own `readfile` table, returns False on a comp that is not open on the Fusion page, and the page's current comp is not reliable from a script. `AddTool` works on any comp object.
- `SetKeyFrames` reads a key's `LH` and `RH` handles as offsets from the key, while a `.setting` file stores them as absolute frame and value. Passed through unchanged, the zoom-push handles overshot to 1.70 at frame 24; converted to offsets it reads 1.075. The midpoint check in the read-back catches the first case.

A comp built by script reads back whole and can still render the old picture. After the build, `apply_effect` sets `TOOLB_PassThrough` on and then off on every added tool (`refresh` in `cmd_effect.py`; not MediaIn, MediaOut or the spline and path modifiers), and the read-back fails an item if any of those tools still reads pass-through.

`exposure-flash`'s `ColorRanges` curve (the default shadow and highlight ranges) is not carried and is reported under `skipped`; the gain does not depend on it.
