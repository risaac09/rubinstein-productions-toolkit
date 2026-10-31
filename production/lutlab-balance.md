# lutlab balance

**Status:** operational.

Per-shot exposure and white balance for video, onto a neutral reference or a skin target, with a CDL out that Resolve and ffmpeg can apply and a table of how well the set agrees.

The rule is the one `rpstills develop` follows for stills: **neutralise on a neutral reference, set exposure on the skin, never push skin hue to a number.** A white or grey garment, or a grey card, tells the white balance; the cheek's L\* sets the exposure; the complexion keeps its own hue.

```bash
cd production
~/.venvs/rp-tools/bin/python -m lutlab balance SHOT [SHOT ...] --camera pyxis [--frames 8] \
    [--neutral-box X0 Y0 X1 Y1] [--face X Y W H] [--wb-from SHOT|FILE.json:SHOT] \
    [--matte vision|grabcut|FILE|FOLDER] [--skin-width 24] \
    [--target-lab L a b | --targets FILE.json] [--target-L 64] [--hero SHOT] --out DIR
```

## Input

- A shot is a video file or a folder of frames (PNG, TIFF, JPEG) in the camera's code values. `--camera` takes a house camera, `--space` any `lutlab.spaces` name (`vlog`, `bmdfilm5`, `logc3`, `logc4`, `di`).
- `--frames N` samples N frames: evenly through the middle 80% of a clip, or evenly through the sorted folder. Results are medians over the frames.
- The face comes from Vision on each sampled frame (macOS), or `--face X Y W H` (normalised), one box for the shot, read as a box sample. A frame with no face is left out and listed in the row.
- The neutral is `--neutral-box` (normalised) in every frame, else the garment or card below the face (`rpstills.skin.neutral_mask`), searched only inside the person matte when `--matte` is given.
- A single PNG, TIFF or JPEG is a shot of one frame, so a still balances the same way as a clip.

## Modes

| | solves | for |
|---|---|---|
| neutral (default) | cheek L\* onto `--target-L` (64), the neutral's a\*, b\* onto 0, 0 | a set shot in known light |
| `--wb-from SHOT` | white balance of another shot held, EV alone onto the cheek L\* | a shot with no neutral, in the same light as one that has it |
| `--target-lab L a b`, `--targets FILE.json` | the cheek's whole Lab onto the target | matching shots to one hero look |

A shot with no neutral and no `--wb-from` is refused (`no_neutral`), not solved on skin. `--wb-from` takes a shot of the same run (solved first) or a converged neutral row of an earlier `balance.json` (`FILE.json:SHOT`); if the reference fails, so do the shots that lean on it.

`FILE.json` is plain JSON, `{"default": [L, a, b], "<shot name>": [L, a, b]}`. Targets from `perceive` plug in through `balance.TARGET_SOURCES`, which returns the same `{name: [L, a, b]}`.

## The correction

Three numbers, solved with `rpstills.develop.solve`:

- `ev`: stops, drives L\*.
- `mired`: a shift from 6500 K along the Planckian locus, drives b\*. `temp_k` is the Kelvin the correction moves a 6500 K white to.
- `tint`: green-magenta, positive toward magenta, 1 unit = 1/50 stop of green; drives a\*.

They become a gain per channel on the camera's *linear* channels. The skin and neutral are measured as Lab of the corrected linear picture rendered as plain sRGB with no tone curve, so L\* 64 is a number to compare shots by, not a look. The solve and its bounds are `ev` ±3, `mired` ±100, `tint` ±40; an axis that reaches a bound is `pinned`.

## Output

Never inside a git tree.

- `balance.json`: a row per shot (ev, mired, temp_k, tint, gains, before and after as skin Lab, neutral a\* b\* and delta_e to the target, status, frames used, cdl, cdl_error) and the set table.
- `balance.md`: the set table.
- `NAME.cc` per shot: an ASC CDL in the camera's code values, for a Resolve CDL import. `NAME.cube`: the same CDL baked (33 points), for ffmpeg `lut3d` or a Resolve LUT.

Status is `converged`, `pinned`, `not_converged`, or a refusal: `no_neutral`, `no_face`, `no_target`, `unreadable`. Exit status 1 when any shot is not `converged`.

A gain in linear light is an offset in a log curve, but not exactly: V-Log and the Blackmagic curve have a toe and an offset term. So the CDL is a least-squares fit of slope and offset per channel (power 1, saturation 1) over the camera's range, and `cdl_error` is its CIEDE2000 against the exact correction on a grey ramp and a skin tone (mean, max). Check it before trusting a shot with a large cast.

## Person matte

`--matte` cuts the room out of the solve. The neutral is searched only where the matte is 0.5 or more, so a pale warm wall beside the shirt, or another person's white shirt, cannot become the reference; the cheek is unchanged. The row gets `matte` (source, `person_share`) and `matte.skin_sector`: the hue slice a Colour Slice or HSL skin sector should take for the shot, centred on the corrected cheek's hue, `--skin-width` degrees wide (default 24, narrow), from half the cheek's chroma up, with the share of the matte's background and of its person the slice catches. In Resolve, set the Colour Slice skin sector to those numbers and gate it by the same matte by hand (the scripting API cannot gate a grade to a matte).

| `--matte` | What | Needs |
|---|---|---|
| `vision` | the person under the face, from macOS Vision's foreground instance (`rpstills/person_matte.swift`) | macOS and swiftc, like the face finder |
| `grabcut` | `rpstills.background.subject_mask`, seeded and repeatable | OpenCV only |
| `FILE` | one grey image (or its alpha) for every frame of a locked-off shot | none |
| `FOLDER` | one image per frame, matched by file stem (a thresholded Resolve Depth Map export, say) | none |

A matte that cannot be made is a refusal (`no_matte` in the row, or a usage error for a bad source), never a silent fall back to no matte.

Six real portraits, indoor and outdoor, at 2400 px, mattes compared:

| Matte | Headless | Time per frame | Repeats | Hair edge and white garment |
|---|---|---|---|---|
| Vision person instance | macOS only | 0.25 s | byte-identical | follows the image edge, takes hair and a white shirt whole; two people who touch come back as one (1 of 6) |
| GrabCut `subject_mask` | any OS | 0.4 to 2.8 s | yes (seeded) | soft, blurry edge band; leaks into a wall or banner and drops a second torso; agrees with Vision at IoU 0.36 to 0.97 |
| Resolve Depth Map, thresholded at 0.8 of the face's depth | no (Resolve Studio; the node is added by hand, the preview exported as a still) | 0.3 to 0.7 s once the node exists | not tested | a soft depth, not a segmentation: a nearer person is brighter than the subject; IoU with Vision 0.30 to 0.92 |
| Resolve Magic Mask (person) | no | a click per clip | not tested | holes in beard, tie and face in Faster mode; cannot be exported through the API |

Vision is the default to reach for. On those six portraits the matte left the solve where it was (the garment finder had already found the right garment on five, none existed on the sixth); where it earns its keep is a warm wall in the neutral's row. Put a pale beige wall (chroma 16, a hue away from skin) beside the garment in the same six frames and the unmatted solve of the two portraits that kept a neutral refused, while the matted solve held the garment at the same neutral error as before. Narrowing the sector from 60 to 24 degrees cut the share of the person it covers by half and the background it catches by a few points; the matte is what takes the background out of it.

## Set consistency

The table is `rpstills audit`'s: each shot's skin after the correction against the set's median (or `--hero SHOT`), as `d_E00`, `d_L`, `d_hue_deg`, with `d_E00_before` beside it to show what the correction did, and the neutral's a\*, b\* after. `spread` carries mean, sd and range of skin L\*, a\*, b\*.

## Known limits

- A neutral far brighter than the cheek (a white shirt in sun, a face in shade) clips at the cheek's exposure. The neutral is then read at the highest exposure where its brightest 1% stays unclipped, so the white balance comes from the garment and not from the few dark pixels the clip leaves. A neutral that clips at every pixel even there is still `not_converged`.
- Vision (the face, and `--matte vision`) needs macOS; elsewhere pass `--face`, and `--matte grabcut` or a matte file.
- A Vision instance can merge two people who overlap; check `matte.person_share` (0.70 where one person alone filled about a quarter of the frame).
- The neutral finder has `neutral_mask`'s limits: a coloured shirt, a print or no garment gives no neutral. A strong cast can push a white garment past its chroma limit; pass `--neutral-box`.
- The gains are per channel in the camera's gamut, but the mired axis is defined on Rec.709 white points. Treat `mired` and `temp_k` as a consistent scale for comparing shots, not as scene Kelvin.
- One correction per shot. A shot whose light changes needs to be cut.
