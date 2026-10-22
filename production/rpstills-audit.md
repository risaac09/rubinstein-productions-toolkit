# rpstills audit: the numbers of a set of finished stills

`rpstills audit` measures a folder of finished portraits and writes the numbers down. It asks one question of a set: do the files agree with each other? Skin is compared with the set's own reference (the median, or a hero frame), not with a fixed skin number, because the house rule is to neutralise on a neutral reference and leave skin hue where the complexion puts it (see [rpstills-develop.md](rpstills-develop.md)). Each file also reports how neutral its white or grey garment reads.

A file whose skin sits past a working default gets a check: a prompt to look, not a verdict. The defaults are a starting point that Isaac may change; nothing passes or fails. The neutral is counted on its own line of the summary, not as a check, because the mask cannot tell a cast on a white shirt from a pale coloured shirt.

It rests on two additions to rpstills, also described here: face landmarks from Vision (`vision.faces(..., landmarks=True)`) and the landmark masks in `rpstills/skin.py`.

## Usage

```bash
cd production
~/.venvs/rp-tools/bin/python -m rpstills audit <finals folder> [--out DIR] [--profile NAME] [--hero FILE]
```

- `<finals folder>`: every JPEG and TIFF (8- or 16-bit) directly inside it. Files are measured as stored, so pass upright files. Vision applies a file's EXIF orientation and the measure does not, so a file whose orientation tag is not 1 is reported and not measured, as is a file whose size differs from what Vision saw.
- `--out DIR`: where `audit.json` goes. The default is the audited folder. The output names client files, so a folder inside any git working tree is refused before Vision runs.
- `--profile NAME`: a profile from `stills-config.json`. Each row then gains its distance from the profile's aspect and eye line.
- `--hero FILE`: a file in the folder (a name, or a path to it) whose skin Lab is the reference instead of the set median. Use it to match a set to one frame. A file outside the folder is refused before Vision runs; a hero with no skin measure is refused after.

It prints one row per file with its checks, then the set spread (standard deviation and min..max) of every metric, then the reference, then a summary: how many files are flagged and on what, and separately how many read a neutral, how many of those are over the `neutral_err` default, and how many have no neutral. It writes `audit.json` with the rows, the `reference` (`source` median or hero, `file`, `L`, `a`, `b`, `n`), the spread, the profile and every constant it used, the check defaults included (`params.check`). Vision runs through the compiled helper, so the audit needs macOS and `swiftc`. Without them it says so and stops.

## What each column is

Measured on the largest face in the frame (by box area). Vision's first face is not always the subject.

| Column | What |
|---|---|
| `L*`, `a*`, `b*`, `hue`, `C*` | mean CIELAB (D65, sRGB curve) over the cheek apples, less any pixel outside the skin tone box (a spectacle rim crossing the patch); the box sample when Vision gave no landmarks (`skin_region` says which) |
| `scope` | the angle of the mean skin colour on a Rec.709 vectorscope: BT.709 Y'CbCr from the encoded values, measured counter-clockwise from +Cb, the way a scope draws it. A plain measurement: a typical light-skinned cheek reads near the scope's skin-tone line at 123, and a person's own complexion may sit well off it |
| `dE00`, `dL`, `dC*`, `dhue` | the skin against the reference: CIEDE2000, then the differences in L*, chroma and hue (signed, wrapped to ±180; positive is toward yellow). The reference is the median of L*, a* and b* over the measured files (each component on its own), or the hero's skin with `--hero`. `dhue` is empty when the file's or the reference's chroma is under 5 (`HUE_MIN_CHROMA`): near grey, as in a black-and-white set, the hue angle is noise |
| `neu.a`, `neu.b`, `neu.err` | the neutral reference below the face (`skin.neutral_mask` with develop's defaults: below the face, L* 40 and up, chroma 25 and under, not skin (skin is judged against the frame's own cheek: at least half its chroma within 25 degrees of its hue), at least 0.2% of the frame, connected to a narrow band under the chin, and not a print (colours spread past 9 a*b* units)): its median a* and b* with clipped pixels left out, and `neutral_err`, the chroma of that median. `neutral_n_px` in the JSON counts its pixels. All are empty when no neutral is found, and when more than half the mask is clipped in every channel (`NEUTRAL_BLOWN`): what is left of a blown white shirt is its edge against the background, so the row reads no neutral and `neutral_note` says why |
| `neu.clip` | the share of the neutral mask left out as clipped (any channel), in percent |
| `clip%` | share of pixels with any channel at 252/255 or above |
| `aspect`, `asp.err` | width / height, and its relative error against the profile |
| `eyeY`, `eye.err` | the eye line (mean of both eye centres) from the top, as a share of the height, and its distance from the profile's `eye_line` |
| `head` | headroom: the crown estimate (0.6 face heights above the top of Vision's box, the same constant the video reframe uses) from the top, as a share of the height. Negative means the crown is cut. It reads low: see Known limits |
| `faceW` | face box width as a share of the image width |
| `eyeSharp`, `eyeBoxPx` | variance of the Laplacian on the box around both eyes, resized to 400 px wide, and that box's width in the file. Compare `eyeSharp` only between files whose `eyeBoxPx` is similar |
| `bgL` | the 98th percentile of L* outside an ellipse round the face centre with semi-axes of 1.6 face widths and 1.4 face heights (3.2 x 2.8 face boxes across), and above the chin; `bg_share` in the JSON is how much of the frame that region covers |
| `sRGB` | whether the embedded ICC profile is sRGB, or the profile's name, or `none` |
| `check:` | the skin metrics past their working default, from `CHECK` in `rpstills/audit.py`: `d_E00` over 3.0, `d_L` over 3.0 either way, `d_hue_deg` over 5.0 either way. The JSON has them as `checks`; a file with an error has none. `neutral_err` over 2.0 (also in `CHECK`) is counted in the summary instead, never as a check |

## Landmarks and skin geometry

`vision_stills.swift` takes an opt-in first argument, `--landmarks`. It then adds each face's eye, brow, lip, contour and nose points, normalised to the whole image with a top-left origin (the same convention as the face box). Without the flag the output is byte-for-byte what it was. The index never asks for landmarks.

`rpstills/skin.py` turns a face into pixel masks: the cheek apples, a feathered skin mask (forehead to jaw, YCrCb skin tones, eyes, brows and lips cut out, and above the eye line nothing wider than the brows, because brown and blond hair at the temples passes the tone box), and the eye, brow, lip, spectacle-lens and under-eye regions. The inner-lip region adds the two mouth corners to Vision's six inner-lip points, which span only the middle teeth. The under-eye region leaves out the cheek patches, so a lift there cannot move the skin sample. Every size scales from the face box.

Left and right are image sides. On the frames checked, Vision's `leftEye` was the image-left eye, which is the subject's own right eye. skin.py does not rely on that: it sorts the eyes and the mouth corners by image x and pairs each cheek patch with the eye and mouth corner on its own side. Each patch sits 40% of the way from the eye centre to that mouth corner.

## What it does not do

- No pass or fail and no score. A check names a number to look at; the defaults are working values, not a house standard.
- No fixed skin target. Skin is compared within the set, and a set can agree with itself and still be wrong for a person; the neutral columns are the cross-check.
- No correction. It never writes an image.
- It does not compare against a session. The reference and the spread come from the folder given.

## Known limits

- The cheek patches, the skin tone box and the proportions were checked on one subject, wearing glasses, in overcast light. They are not validated on other skin tones, on faces turned well away from the camera, or in other lighting.
- With glasses on, the cheek patches cross the lower rim of the frame. On the frames checked the rim was 5 to 9% of the patch and moved the mean by 0.9 to 2.0 L* and 0.6 to 1.1 degrees of hue, so the cheek sample now leaves out pixels outside the skin tone box. Rim pixels that pass the box still count, and when under half the patch passes (a render far off white balance) the whole patch is used.
- The neutral is whatever pale, low-chroma garment sits below the face. A pale coloured shirt (light blue or lavender, chroma under 25) passes the mask, and so do the pale motifs of a print; `neutral_err` then reads that colour (10 to 22 on one set), not a cast. This is why it is not a check. Read `neu.a`, `neu.b` with the picture.
- A strong warm cast on a white garment can come close to the cheek's own hue and be taken for skin, so the cast reads low. Skin is judged against the frame's cheek (half its chroma, within 25 degrees of its hue), so ordinary warm whites stay in the mask; cool and green casts read in full.
- The median reference follows the set. If most files share a cast, the median carries it, and only the neutral columns can show it. A cool or green cast on white garments shows there; a shared warm cast can show nowhere, with every row empty in the neutral columns. The summary counts the files without a neutral, so a set where none was read says so.
- A mostly clipped neutral is read on what is left: the folds and the shade side, which on one set read a few C* above the lit side. `neu.clip` says how much was left out. A garment clipped in one channel only is itself a sign of a cast and is still read.
- A file whose skin came from the box sample (no landmarks) joins the median with the cheek samples.
- On a tight headshot the background region is only the top corners, a few percent of the frame (see `bg_share`), so `bgL` rests on a small sample.
- The crown is an estimate from the face box, not a measured hairline. Vision's box top sits about 0.2 face heights above the brows, and on the one set checked the estimate fell 0.03 to 0.05 of the frame height above the visible top of the hair. `head` therefore reads low, and a small negative value need not mean a cut crown.
- Eye sharpness depends on resolution. An eye box narrower than 400 px is upsampled and reads softer: on one set of finals, the same frames exported at half size read 0.46 to 0.78 of the full-size value, and at a third 0.22 to 0.35. Compare it only between files with a similar `eyeBoxPx`; it is not an absolute focus test.
- EXIF orientation is not applied. Pass upright files; a file with an orientation tag other than 1, or a size that differs from Vision's, is reported rather than measured.
