# rpstills audit: the numbers of a set of finished stills

`rpstills audit` measures a folder of finished portraits and writes the numbers down. It gives no verdict. Nobody has set the ranges a finished portrait should fall in yet, and invented thresholds would read as a house standard. The audit therefore reports each file and the spread of the set, and leaves the judgement to a person.

It rests on two additions to rpstills, also described here: face landmarks from Vision (`vision.faces(..., landmarks=True)`) and the landmark masks in `rpstills/skin.py`.

## Usage

```bash
cd production
~/.venvs/rp-tools/bin/python -m rpstills audit <finals folder> [--out DIR] [--profile NAME]
```

- `<finals folder>`: every JPEG and TIFF (8- or 16-bit) directly inside it. Files are measured as stored, so pass upright files. Vision applies a file's EXIF orientation and the measure does not, so a file whose orientation tag is not 1 is reported and not measured, as is a file whose size differs from what Vision saw.
- `--out DIR`: where `audit.json` goes. The default is the audited folder. The output names client files, so a folder inside any git working tree is refused before Vision runs.
- `--profile NAME`: a profile from `stills-config.json`. Each row then gains its distance from the profile's aspect and eye line.

It prints one row per file, then the set spread (standard deviation and min..max) of every metric. It writes `audit.json` with the rows, the spread, the profile, the reference angles and every constant it used. Vision runs through the compiled helper, so the audit needs macOS and `swiftc`. Without them it says so and stops.

## What each column is

Measured on the largest face in the frame (by box area). Vision's first face is not always the subject.

| Column | What |
|---|---|
| `L*`, `a*`, `b*`, `hue`, `C*` | mean CIELAB (D65, sRGB curve) over the cheek apples, less any pixel outside the skin tone box (a spectacle rim crossing the patch); the box sample when Vision gave no landmarks (`skin_region` says which) |
| `scope` | the angle of the mean skin colour on a Rec.709 vectorscope: BT.709 Y'CbCr from the encoded values, measured counter-clockwise from +Cb, the way a scope draws it |
| `d117.7`, `d123` | signed distance in degrees from two reference angles: 117.7 is the stills trial target (cheek Lab 64/15/14); 123 is the scope's skin-tone line, where a typical light-skinned cheek reads (Lab 59.6/11.8/14.6 reads 123.2). These are distances, not targets |
| `clip%` | share of pixels with any channel at 252/255 or above |
| `aspect`, `asp.err` | width / height, and its relative error against the profile |
| `eyeY`, `eye.err` | the eye line (mean of both eye centres) from the top, as a share of the height, and its distance from the profile's `eye_line` |
| `head` | headroom: the crown estimate (0.6 face heights above the top of Vision's box, the same constant the video reframe uses) from the top, as a share of the height. Negative means the crown is cut. It reads low: see Known limits |
| `faceW` | face box width as a share of the image width |
| `eyeSharp`, `eyeBoxPx` | variance of the Laplacian on the box around both eyes, resized to 400 px wide, and that box's width in the file. Compare `eyeSharp` only between files whose `eyeBoxPx` is similar |
| `bgL` | the 98th percentile of L* outside an ellipse round the face centre with semi-axes of 1.6 face widths and 1.4 face heights (3.2 x 2.8 face boxes across), and above the chin; `bg_share` in the JSON is how much of the frame that region covers |
| `sRGB` | whether the embedded ICC profile is sRGB, or the profile's name, or `none` |

## Landmarks and skin geometry

`vision_stills.swift` takes an opt-in first argument, `--landmarks`. It then adds each face's eye, brow, lip, contour and nose points, normalised to the whole image with a top-left origin (the same convention as the face box). Without the flag the output is byte-for-byte what it was. The index never asks for landmarks.

`rpstills/skin.py` turns a face into pixel masks: the cheek apples, a feathered skin mask (forehead to jaw, YCrCb skin tones, eyes, brows and lips cut out, and above the eye line nothing wider than the brows, because brown and blond hair at the temples passes the tone box), and the eye, brow, lip, spectacle-lens and under-eye regions. The inner-lip region adds the two mouth corners to Vision's six inner-lip points, which span only the middle teeth. The under-eye region leaves out the cheek patches, so a lift there cannot move the skin sample. Every size scales from the face box.

Left and right are image sides. On the frames checked, Vision's `leftEye` was the image-left eye, which is the subject's own right eye. skin.py does not rely on that: it sorts the eyes and the mouth corners by image x and pairs each cheek patch with the eye and mouth corner on its own side. Each patch sits 40% of the way from the eye centre to that mouth corner.

## What it does not do

- No pass or fail, no warning, no score. Ranges will be set by hand, or derived from a numbers-only reference set, as a separate change.
- No correction. It never writes an image.
- It does not compare against a session. A set spread is computed over the folder given.

## Known limits

- The cheek patches, the skin tone box and the proportions were checked on one subject, wearing glasses, in overcast light. They are not validated on other skin tones, on faces turned well away from the camera, or in other lighting.
- With glasses on, the cheek patches cross the lower rim of the frame. On the frames checked the rim was 5 to 9% of the patch and moved the mean by 0.9 to 2.0 L* and 0.6 to 1.1 degrees of hue, so the cheek sample now leaves out pixels outside the skin tone box. Rim pixels that pass the box still count, and when under half the patch passes (a render far off white balance) the whole patch is used.
- On a tight headshot the background region is only the top corners, a few percent of the frame (see `bg_share`), so `bgL` rests on a small sample.
- The crown is an estimate from the face box, not a measured hairline. Vision's box top sits about 0.2 face heights above the brows, and on the one set checked the estimate fell 0.03 to 0.05 of the frame height above the visible top of the hair. `head` therefore reads low, and a small negative value need not mean a cut crown.
- Eye sharpness depends on resolution. An eye box narrower than 400 px is upsampled and reads softer: on one set of finals, the same frames exported at half size read 0.46 to 0.78 of the full-size value, and at a third 0.22 to 0.35. Compare it only between files with a similar `eyeBoxPx`; it is not an absolute focus test.
- EXIF orientation is not applied. Pass upright files; a file with an orientation tag other than 1, or a size that differs from Vision's, is reported rather than measured.
