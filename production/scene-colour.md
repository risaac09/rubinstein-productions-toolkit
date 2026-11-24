# Scene model: the colour axis

> **Status:** built 2026-11-24. The second axis of the per-shot scene model in [scene-model.md](scene-model.md), after framing. Code: `rpresolve/scene_colour.py`; command: `scene-colour`; tests: `tests/test_scene_colour.py`.

The colour block is a join of numbers the toolkit already measures. It reads them from files and measures nothing new: no frame is decoded again.

## Run it

```
python3 resolve_workflow.py perceive SHOT.mov --out /outside/any/repo/shot1 --targets
python3 resolve_workflow.py detect SHOT.mov --json --out /outside/any/repo/detect.json
python3 resolve_workflow.py scene-colour /outside/any/repo/shot1 /outside/any/repo/shot2 \
    --detect /outside/any/repo/detect.json --out /outside/any/repo/colour.json
```

Packets are given in story order. Without `--targets`, `tint_shift_deg` is withheld; without `--detect`, `input_space` is.

## Fields

| Field | Unit | From | Mark |
|---|---|---|---|
| `cast_a`, `cast_b` | Lab a*, b* of the near-neutral midtones | `perceive.colour_numbers` `neutral_cast` | MEASURABLE + TASTE |
| `skin_hue_deg` | degrees on the vectorscope (Cb, Cr) | the largest face's `measure.skin_stats` `hue_deg` | TASTE |
| `skin_off_line_deg` | degrees from the 123 degree skin line | `skin_stats` `hue_off_skin_line_deg` | TASTE |
| `skin_lab` | Lab on the 2.4 gamma | `skin_stats` `lab` | MEASURABLE |
| `palette` | up to 8 of `{lab, hue_deg, share_pct}`; `hue_deg` is the Lab hue angle h_ab | `perceive.palette` | TASTE |
| `tint_shift_deg` | degrees, shadows minus highlights, wrapped to (-180, 180] | `perceive.band_colour` via `targets.json` | TASTE |
| `input_space` | the Resolve colour-space name | `detect` `input_color_space` | MEASURABLE |

Each block also carries `withheld` (field: reason), `taste` (the fields a person reads), `review` (the harmony question) and, where the skin read had them, `skin_warnings`. A number that could not be read is null with a reason, never zero. A source perceive tagged log, PQ, HLG or linear gets every picture number withheld, because perceive read it as display Rec.709.

Two hue wheels appear here. The skin hue is a vectorscope angle; the palette hue is the Lab hue angle. They are not comparable with each other.

## Between shots

Every shot after the first carries `from_previous`:

- `palette.de2000`: each palette entry against its nearest entry in the previous shot's palette, by CIEDE2000, weighted by share. CIEDE2000 is `color.delta_e_2000`, which follows Sharma, Wu and Dalal, "The CIEDE2000 color-difference formula", *Color Research and Application* 30(1), 2005, with kL = kC = kH = 1, and is tested against pairs from their table.
- `palette.hue_arc_deg`: the share-weighted shortest arc, 0 to 180 degrees, between the same nearest pairs, counting only pairs where both entries have Lab C* of 8 or more (perceive's near-neutral floor). A grey has no hue to compare.
- `skin_hue_arc_deg`: the shortest arc between the two skin hues.
- `cast_delta`: this shot's cast minus the previous one's.

No tolerance is applied to any of them. Whether a difference is a mismatch is a person's call, put through `media-review-loop`.

## What stays with the person

- **The skin hue.** Reported, never pushed to a target number.
- **Harmony and split toning.** No sourced tolerance was found. The one candidate is the hue-template model of harmony (Matsuda's templates, as used in Cohen-Or, Sorkine, Gal, Leyvand and Xu, "Color harmonization", *ACM Transactions on Graphics* 25(3), 2006). It is a model, not a measured tolerance, and its sector widths were seen only secondhand: **UNVERIFIED**. So harmony is the `review` question in every block, not a number.
- **How much cast a scene carries.** The cast is measured; the limit is a choice.
