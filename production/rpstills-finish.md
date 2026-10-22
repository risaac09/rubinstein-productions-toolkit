# rpstills finish and absheet: retouch, background, crop, and the A/B/C sheet

`rpstills finish` turns developed portraits into delivery JPEGs: an optional background burn, an optional retouch, then one crop per profile with the eye line placed from face landmarks. `rpstills absheet` makes the one picture that asks for a choice between candidates (looks, crops, retouch strengths), answered with a letter.

Both rest on the face landmarks from Vision (`vision.faces(..., landmarks=True)`) and the masks in `rpstills/skin.py`, described in [rpstills-audit.md](rpstills-audit.md).

## finish

```bash
cd production
~/.venvs/rp-tools/bin/python -m rpstills finish <out> [--ids ID ...] [--src DIR] \
    [--retouch none|all|ID ...] [--background off|on] [--crop PROFILE ...] [--config PATH]
```

- `<out>`: the shoot's output folder. The sources are `<out>/develop/<id>.tif` (what `rpstills develop` writes) unless `--src DIR` names a folder of TIFF (8- or 16-bit) or JPEG files. An id is the file name without its extension.
- `--ids`: finish only these frames.
- `--retouch`: `none` (the default), `all`, or a list of ids (space or comma separated). Retouch never runs unless asked.
- `--background`: `off` (the default) or `on`.
- `--crop`: solo crop profiles from `stills-config.json`; the default is `headshot_4x5_eye36`. Union profiles (`group_16x9`) and profiles without an aspect are refused.

For each frame, finish asks Vision for the faces again, with landmarks, and works on the largest face (by box area, not Vision's first face). Then, in order:

1. **Background** (`--background on`). `rpstills/background.py` runs OpenCV GrabCut on a quarter-size copy, seeded from the face box: the face core is certain subject, a head ellipse and a torso trapezoid down to the frame bottom are probable subject, and a strip along the top and both sides is certain background. OpenCV's random generator is seeded, so a run repeats. The mask is closed, dilated a little to keep flyaway hair out of the burn, scaled up and feathered. Outside it the frame goes down 0.6 EV (in linear light) and loses 15% of its saturation. The mask is refused, and the frame finished without a burn, when the face core is too small to seed it, when GrabCut fails, when the mask covers less than 95% of the face core, or when it covers under 5% or over 90% of the frame. The reason is in `finish.json`.
2. **Retouch** (`--retouch`). `rpstills/retouch.py`, every step, on the face's region only (the box padded by 0.6 of its larger side). Each step is `fn(rgb, face_px, **params) -> (rgb, report)` and leaves untouched pixels bit-identical:

   | Step | What it does |
   |---|---|
   | `heal_spots` | a dark spot is where a small blur (face width / 450) sits 0.09 (sRGB luma) below a wide blur (face width / 55) on eroded skin, no larger than (face width / 70)² px; it is replaced by the wide skin blur. Never inside the eyes, brows or lips. Larger dark areas (moles, freckle clusters) are kept and counted |
   | `evenness` | band-pass on the skin mask: takes 30% of the band between the two blurs and 5% of the finest band, so blotches even out and pores stay. It fades out toward the mask edge, where the wide blur sees skin on one side only and would otherwise lift a shadowed jaw into a light rim |
   | `eyes` | inside the eye polygons: x1.08 and an unsharp mask of 0.4 |
   | `under_eye` | the under-eye ellipses, where darker than the cheek's 60th percentile, lifted toward it by at most 12%, mixed at 0.6 |
   | `teeth` | inside the inner lips only, bright low-saturation pixels: saturation -35%, value +4% |
   | `deglare` | spectacle lenses: inside an ellipse per lens (not the eye), where the image is brighter than the cheek by 4 L* and has lost 6 of the cheek's chroma (a veil of sky or window washes skin toward neutral or blue; bare forehead keeps its redness), the low-frequency veil is subtracted and a*, b* pulled back to the cheek. It reports the area touched. It dims a veil; it cannot bring back what an opaque reflection hides |

   A face without landmarks is not retouched; the frame is still cropped and the reason recorded.
3. **Crop**. `crops.solo_crop` with the eye line measured from the landmarks (the mean of the two eye centres), so a profile's `eye_line` lands on the eyes rather than on the box estimate. When the frame cannot hold the crop it is shifted, or shrunk and flagged `tight`, exactly as `rpstills crops` does; `finish.json` records where the eye line actually landed (`eye_line_at`). A profile with an `out` size is resized to it; one without is written at the crop's own size.

It writes `<out>/finish/<profile>/<id>.jpg` (quality 93, 4:4:4 chroma, an sRGB ICC profile built by Pillow's ImageCms, so no platform profile file is needed) and `<out>/finish/finish.json`: per frame the face, the mask share or the refusal, every retouch step's report, each crop's pixels, size, flags (`tight`, `face_cut`, `upscale`) and achieved eye line, and every constant used. Files are written atomically.

The output shows and names client frames, so an `<out>` inside any git working tree is refused before any work. A frame that cannot be finished (unreadable, no face, Vision saw another size) is recorded with its reason, the others are still written, and the run exits 1.

## Crop profiles

`stills-config.json` 0.2.0 adds three solo profiles under new names. The existing profiles are unchanged.

| Profile | Aspect | Crop height (face box heights) | Eye line from the top |
|---|---|---|---|
| `headshot_square_tight` | 1:1 | 2.3 | 0.40 |
| `headshot_4x5_eye36` | 4:5 | 3.2 | 0.36 |
| `halfbody_2x3` | 2:3 | 5.2 | 0.25 |

They have no `out` size (written at the crop's size) and quality 93. `rpstills crops` plans them for solo frames as it does the others. `crops.solo_crop` takes an optional `eye_y` (a share of the frame height from the top); without it the eye line is the old box estimate, 0.40 of the face height below the top of the box.

## absheet

```bash
~/.venvs/rp-tools/bin/python -m rpstills absheet IMG IMG [IMG ...] --out DIR [--seed N] [--title T]
```

Takes 2 to 9 images and writes:

- `DIR/sheet.jpg`: the images in a grid, each with a large letter (A, B, C ...) white on a near-black badge. At most 2000 px on the long edge and under 1 MiB: the JPEG quality steps down from 90 until it fits. sRGB profile embedded.
- `DIR/key.json`: each letter to its source file, input position and size, the seed, the grid, and the sheet's size, bytes and quality.

The order is shuffled by the seed and is never the order given, so a letter says nothing about which candidate came first or which is new. Without `--seed` a random seed is drawn and recorded in `key.json`.

It is made to be read on a phone. The grid is the one that brings the sheet nearest a 3:4 portrait screen, so each picture is as large as a phone can show it; each letter is 16% of its cell's short side (at least 40 px), so it stays about 14 px or more tall when the whole sheet is shown 390 px wide. Images keep their aspect inside equal cells; EXIF rotation is applied; 16-bit TIFFs are read. Pillow draws everything, so no ffmpeg `drawtext` is needed. The answer is one letter; `key.json` turns it back into a file. A `DIR` inside any git working tree is refused.

## What it does not do

- finish does not grade. Exposure and colour come from the develop step; apart from the burn and the retouch, pixels are passed through.
- No liquify, no reshaping, no hair or flyaway cleanup, no blemish removal beyond small dark spots, no lens flare or reflection removal beyond dimming a veil.
- absheet records nothing about the answer. The taste log lives outside this repository.

## Known limits

- The retouch strengths, the skin tone box and the proportions were tuned on one subject, wearing glasses, in overcast light, on 6000 x 4000 frames. They are not validated on other skin tones, light, lenses or resolutions. Look at every retouched frame before it is delivered.
- `deglare` gates on brightness and loss of skin colour. A warm reflection (a lamp, a sunset) does not wash skin toward neutral and is left alone; a cool, bright patch of skin inside the lens ellipse can still be dimmed a little.
- GrabCut can take a background close to the subject's tones, or miss hands and props; the dilation that keeps hair out of the burn leaves a thin, slightly brighter halo along the subject's outline. The guard only catches gross failures (no face core, under 5% or over 90% of the frame).
- The background burn works on the full frame in float: on a 24 MP frame expect around 12 s and up to about 3.5 GB of memory per frame.
- When the frame cannot hold the crop where the eye line wants it (little room above the head, say), the crop is shifted and the eyes land away from the profile's line (higher, in that case); only shrinking is flagged (`tight`). Read `eye_line_at` in `finish.json`.
- EXIF orientation is not applied in finish. Pass upright files; a mismatch with Vision's size is reported rather than finished.
