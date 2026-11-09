# Fusion macros

> **Status:** operational 2026-11-09. Eight macros, each built and checked by eye in a sandbox project on Resolve Studio 21.2.0.6, and each applied there through `apply_effect`, read back with every tool, keyframe and link in place. Three need a person's eye before delivery (below).

`production/fusion-macros/<id>.setting` holds the tools of one short-form effect without MediaIn or MediaOut, so it fits any clip's Fusion comp. The MCP `apply_effect` tool puts one on items of an ` [auto]` timeline; by hand, drag the `.setting` onto a clip's Fusion node area and wire it as the table says. `python3 -m rpresolve.cmd_effect` lists every macro with its wiring and checks that each parses.

Values are normalised Fusion values: positions and sizes are 0 to 1 of the frame, centre 0.5/0.5. Keyframes are comp frames counted from the item's first frame, written for 24 fps; scale them for 25 or 30.

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

The three transitions (`cross-dissolve-short`, `scale-window`, `crop-window`) show transparency around or under the incoming clip. On one video track that is black. To see the outgoing shot through it, put the incoming item on V2 over the end of the outgoing one.

## Needs a person's eye

- **`person-matte-text`**: the matte is a placeholder rectangle that fitted one test frame. Replace it with a Magic Mask (Person, Better) tracked on the item, or a Magic Mask node in Fusion, edge softness 2 to 5 px.
- **`exposure-flash`**: +1 stop may read too weak beside the edits it came from. Try gain 3.0, or a white dip.
- **`text-word`** (and the text in `person-matte-text`): Arial Bold stands in for a heavy or extended face (the font family "Arial Black" set by script renders nothing; pick the real face in the Inspector), and the red-orange reads more orange through a colour-managed output transform.

## How apply_effect builds a comp

`apply_effect` adds a new Fusion comp to each item (`AddFusionComp`, renamed `rp-fx <id>`), then builds the macro's tools in it with `AddTool` under their macro names, sets each input's value, adds a `BezierSpline` or `XYPath` modifier for each animated input with the macro's keyframes, connects the links, and wires the chain between MediaIn1 and MediaOut1. It reads the comp back: every tool and modifier present, each animated input's keyframes on the macro's frames, its value halfway between two keys within those keys' values, and the wiring.

Two things found live (Resolve 21.2.0.6) shaped that:

- `Composition.Paste()` with the macro's table, or with Fusion's own `readfile` table, returns False on a comp that is not open on the Fusion page, and the page's current comp is not reliable from a script. `AddTool` works on any comp object.
- `SetKeyFrames` reads a key's `LH` and `RH` handles as offsets from the key, while a `.setting` file stores them as absolute frame and value. Passed through unchanged, the zoom-push handles overshot to 1.70 at frame 24; converted to offsets it reads 1.075. The midpoint check in the read-back catches the first case.

`exposure-flash`'s `ColorRanges` curve (the default shadow and highlight ranges) is not carried and is reported under `skipped`; the gain does not depend on it.
