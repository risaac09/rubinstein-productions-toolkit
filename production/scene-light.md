# Scene model: light

> **Status:** operational, 2026-11-24. The second axis of the per-shot scene model (the first, framing, is in `rpresolve/framing_rules.py`). Code: `rpresolve/scene_light.py`; command: `scene-light`.

The light block turns one shot's `measure` report into named numbers a shot-choice or story-order tool can read. It measures nothing new: every number comes from a function in `rpresolve/measure.py`, and this module only renames and bands them.

## Run it

```bash
python3 resolve_workflow.py scene-light SHOT.mov OTHER.mov --json light.json
```

Each shot is a video or still (measured as `measure` does, faces included) or a report saved earlier with `measure --json`. One line prints per shot; `--json` writes a list of `{file, light, taste, review}`.

## Fields

| Field | Unit | From |
|---|---|---|
| `ratio_stops` | stops, log2 of the lit over the shadow cheek | `measure.pooled_lighting` `stops` |
| `ratio` | (key + fill) : fill | `pooled_lighting` `ratio` |
| `lit_side` | `left` or `right` | `pooled_lighting` `lit_side` |
| `key` | `high`, `mid` or `low` | `luma_stats` `p50`, banded on the house thresholds below |
| `skin_l_srgb` | L* on the sRGB curve | `skin_stats` `L_srgb`, median over frames |
| `shadow_readable_pct`, `shadow_lost_pct` | percent of 8x8 tiles | `shadow_classes`, mean over frames |
| `withheld` | text or null | why any field above is null |

A missing number is null with a reason in `withheld`, never zero. A clipped cheek nulls `skin_l_srgb`: its L* is a limit, not a reading. Other skin warnings (no landmarks, a cheek off the tone box) leave the number in place; they are in the `measure --json` report. A file whose transfer tag is log, PQ, HLG or linear (`measure.transfer_of`) withholds every field: its luma is not a display reading, so neither key, ratio, shadow nor skin L* means what the field says.

## Key thresholds (house defaults)

| Constant | Value | Band |
|---|---|---|
| `KEY_HIGH_P50_IRE` | 60.0 | `high` at or over |
| `KEY_LOW_P50_IRE` | 30.0 | `low` at or under; `mid` between |

These are a starting point, not a standard. The p50 counts letterbox and pillarbox bars, so a boxed shot reads darker than its picture. A person sets them, in `scene_light.py`, after looking at shots they call high key and low key.

## What is taste

Each record carries `taste`, naming the fields that are a person's call:

- `key`: the thresholds are house defaults.
- `ratio`: the number is measured; whether flat or dramatic suits the scene is not.
- `lit_side`: measured; whether the light is motivated is not.

And `review`, the question put to a person through the media-review loop: is this shot's key and ratio the look the scene wants. The skin L* and shadow shares are measurements; the floors that reject a take on them belong to shot choice, not here.
