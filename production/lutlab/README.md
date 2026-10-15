# lutlab

Turns a look into a few readable numbers you can shift on purpose, and back
into a LUT. A look can come from a `.cube` (decompose) or from image pairs
(fit-pairs): a Lumix RAW and the camera's own JPEG, or a log frame and its
graded frame. The numbers rebuild as a `.cube` for Resolve and as a Hald CLUT
PNG for stills tools (darktable lut3d, RawTherapee film simulation).

Runs on macOS's `/usr/bin/python3` (3.9), which has numpy, scipy, Pillow and
OpenCV. Homebrew's python3, python3.12 and python3.14 have no numpy. RAW
develop uses darktable-cli from the app bundle
(`/Applications/darktable.app/Contents/MacOS/darktable-cli`); nothing to
install.

```bash
cd production
/usr/bin/python3 -m lutlab decompose LOOK.cube --in vlog --out-space r709 --out DIR
/usr/bin/python3 -m lutlab build DIR/params.json --cube NEW.cube --size 33 --hald NEW.png
/usr/bin/python3 -m lutlab fit-pairs --raw A.RW2 --jpg A.JPG [--raw B.RW2 --jpg B.JPG] --out DIR
/usr/bin/python3 -m lutlab fit-pairs --src log.tif --dst graded.tif --in vlog --out-space r709 --out DIR
```

Spaces: `vlog` (V-Log/V-Gamut), `logc3` (LogC3 EI 800/AWG3), `di` (DaVinci
Intermediate/DWG), `r709` (gamma 2.4), `srgb`, `lin709`. Output spaces for the
model: `r709`, `srgb`.

## Where output goes

Every write is refused inside any git work tree and under `~/Desktop`,
`~/Documents` and `~/Library/Mobile Documents`. Decompositions of licensed
LUTs carry the LUT's values (the residual rebuilds it exactly), so they never
go into a repository. Give `--out`, or set `LUTLAB_OUT` and outputs land in
`$LUTLAB_OUT/<name>`. `build` refuses to overwrite a file without
`--overwrite`: Resolve caches a LUT by path, so give each version a new name.
`decompose` refuses a folder that already has a `params.json` (it may hold
your edits) unless you pass `--overwrite`.

## The model

Applied in this order (`lutlab/model.py` has the exact maths):

1. **Input decode** to scene linear in Rec.709 primaries. Exposure is in
   stops from 18% grey.
2. **tone**: one filmic curve. `mid` is the output code value of 18% grey,
   `contrast` the slope there in code values per stop, `toe_start` and
   `shoulder_start` where the straight part ends (stops from grey),
   `black` and `white` the limits, `toe_strength` and `shoulder_strength` the
   shape of each tail (toward 50: settles within a stop or two; toward 0.05:
   a long soft tail). `mode` is what the curve is applied to: `per_channel`
   (each of R, G, B, the film-like way, which also raises saturation where
   the curve is steep), `luminance` or `max_rgb` (colour ratios kept).
   decompose tries all three and keeps the closest.
3. **Colour**, in OKLab, after the tone curve:
   - `hue_lum`: lightness change by hue, in L points at chroma 0.10.
   - `sat_gain`: chroma gain by hue (12 knots, every 30 degrees) and
     lightness band (shadows, mids, highlights); `sat_rolloff`: extra gain by
     chroma, for how strong colours are compressed.
   - `hue_shift`: hue rotation in degrees on the same 12 x 3 grid.
   - `split_tone`: a tint by lightness (7 levels), as a hue angle and an
     amount in OKLab chroma points (x100). About 1 is a just-visible cast.
4. **Residual**: decompose writes what the numbers miss as
   `<name>.residual.cube` (deltas on the source lattice). `build` adds it
   back, so an unedited rebuild matches the source to the file's rounding;
   `--no-residual` gives the model alone. Pair fits have no residual.

OKLab hues for orientation: red ~29, orange ~55 (skin 45 to 70), yellow ~110,
green ~142, cyan ~195, blue ~264, magenta ~328. Knot names in `params.json`
are the knot angle plus a nearby colour name.

To change a look one variable at a time, edit `params.json` (soften the
shoulder: lower `shoulder_strength`; rotate skin 3 degrees toward yellow: add
3 to `hue_shift.*["060 orange"]`; desaturate shadows: lower
`sat_gain.shadows`), then `build` to a new file name. `build --in srgb`
re-targets the input encoding, for a stills Hald fed a flat sRGB render;
`build --in vlog` puts a look fitted on stills in front of V-Log footage.

## How decompose fits

The tone curve is fitted on the LUT's neutral axis (R = G = B), against output
luminance, so a tint stays out of the curve and shows up in `split_tone`.
The colour numbers are fitted where real scenes have colours: an even OKLab
sample over -6 to +4 stops, chroma up to about the ColorChecker's strongest
patch, every 5 degrees of hue. A log LUT's lattice is mostly saturations no
surface reaches, and fitting it evenly bends the numbers away from skin.
The report gives Delta E 2000 at each stage on those natural colours, on the
plausible lattice nodes, and on every node.

The report also runs the ColorChecker and a family of 432 skin tones through
the look. A display-referred look (`r709`/`srgb` in) is fed the sRGB code
values and compared with those codes as displayed; a log look is fed the
patches as scene reflectance and compared with a plain sRGB rendering.
Density is how much darker a colour lands than a grey of the same input
luminance, against the same gap before the look.

## fit-pairs

RAW + JPEG: darktable-cli develops the RAW with `workflow=none` (no filmic or
sigmoid), legacy chromatic adaptation, camera white balance, out as 16-bit
linear Rec.709, in a throwaway config and an in-memory library (Isaac's
darktable library is never touched). The JPEG is rotated by its EXIF
orientation. Then both are scaled to 1200 px, aligned by an ECC homography,
blurred slightly, and masked: the outer 10% (lens correction and vignetting
compensation differ), the strongest edges (alignment), clipped pixels. 32 px
blocks on a checkerboard split the pixels: one half is binned (32^3 grid of
mean colours) and fitted, the other half reports the held-out error.
`compare-N.jpg` puts the plain develop, the model and the target side by side.

The RAW develop's exposure scale is arbitrary, so `mid` and absolute
lightness carry the camera's exposure offset; contrast, hue and chroma read
directly. A few frames fit only the colours they contain.

Frame pairs: export the log frame and the graded frame from Resolve at the same
framing (16-bit TIFF or PNG), and give the log space with `--in`.

## Not in the model yet

Halation and grain are spatial, not expressible in a LUT. When they are added
they are separate parameter sets: halation as threshold, radius, tint and
strength; grain as size, amplitude by luma, and chroma amount.

## Known limits

- One curve for all three channels: a look whose channels have different
  curves shows that as `split_tone` plus the residual.
- 12 hue knots: a look that treats two nearby hues very differently (inside
  30 degrees) leaves that in the residual.
- `rawtherapee-cli` 5.12 crashed on start (SIGTRAP, even for `-v`) on
  2026-10-15, so the Hald export was checked numerically against the cube,
  not yet inside RawTherapee or darktable.

Tests: `/usr/bin/python3 production/tests/test_lutlab.py` (synthetic LUTs only).
