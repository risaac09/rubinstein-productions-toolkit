# lutlab

Turns a look into a few readable numbers you can shift on purpose, and back
into a LUT. A look can come from a `.cube` (decompose) or from image pairs
(fit-pairs): a Lumix RAW and the camera's own JPEG, or a log frame and its
graded frame. The numbers rebuild as a `.cube` for Resolve and as a Hald CLUT
PNG for stills tools (darktable lut3d, RawTherapee film simulation).

Runs on the venv at `~/.venvs/rp-tools` (Homebrew Python 3.14 with numpy, scipy,
Pillow and OpenCV from `requirements.txt`; `/opt/homebrew/bin/python3.14 -m venv
~/.venvs/rp-tools && ~/.venvs/rp-tools/bin/python -m pip install -r requirements.txt`).
Xcode's `/usr/bin/python3` (3.9) no longer runs it: Pillow's security fixes need 3.10 or later. RAW
develop uses darktable-cli from the app bundle
(`/Applications/darktable.app/Contents/MacOS/darktable-cli`); nothing to
install.

```bash
cd production
~/.venvs/rp-tools/bin/python -m lutlab decompose LOOK.cube --in vlog --out-space r709 --out DIR
~/.venvs/rp-tools/bin/python -m lutlab build DIR/params.json --cube NEW.cube --size 33 --hald NEW.png
~/.venvs/rp-tools/bin/python -m lutlab fit-pairs --raw A.RW2 --jpg A.JPG [--raw B.RW2 --jpg B.JPG] --out DIR
~/.venvs/rp-tools/bin/python -m lutlab fit-pairs --src log.tif --dst graded.tif --in vlog --out-space r709 --out DIR
~/.venvs/rp-tools/bin/python -m lutlab looks BASE/params.json --camera pyxis,s5ii,rcm [--monitor] --out DIR
~/.venvs/rp-tools/bin/python -m lutlab port LUT.cube --from vlog --camera pyxis --size 65 [--monitor] --out DIR
~/.venvs/rp-tools/bin/python -m lutlab diff A.cube B.cube --in vlog
~/.venvs/rp-tools/bin/python -m lutlab apply A.cube B.cube --frame still.tif [--frame ...] --out DIR
~/.venvs/rp-tools/bin/python -m lutlab library [--scan] [--list] [--space vlog] [--dupes]
```

Spaces: `vlog` (V-Log/V-Gamut), `bmdfilm5` (Blackmagic Film Gen 5/Wide Gamut
Gen 5), `logc3` (LogC3 EI 800/AWG3), `logc4` (LogC4/AWG4), `di` (DaVinci
Intermediate/DWG), `r709` (gamma 2.4), `srgb`, `lin709`. Output spaces for the
model: `r709`, `srgb`. Cameras (`--camera`): `s5ii` is V-Log, `pyxis` is BRAW
decoded as Gen 5; `rcm` (for `looks` only) is DaVinci Intermediate in and out. LUT inputs can be `.cube` or Resolve's own `.dat`.

## Cameras, ports and looks

**One LUT, both cameras.** A LUT only knows the code values it expects, not
the camera. `port` builds a lattice in the target camera's log, sends every
node through scene linear into the log the LUT was made for, and samples the
LUT there. There's no model and no fit, so inside the source's domain it is
the same LUT, re-addressed. It prints the share of natural scene colours the
source LUT never saw, which are clamped at its edge. Use `--size 65`: putting
a LUT onto a new lattice softens hard clips slightly, and 65 points keeps
that under the noise (V-Log to Gen 5 and back: median Delta E 0.12 on a
real 65-point LUT). `--monitor` adds a 33-point copy clipped to 0..1 for the
camera itself; the Pyxis loads 33-point `.cube`. The S5II's LUT Library takes
`.cube` (2 to 33 points) or Panasonic's `.vlt`, from the card's root, 10 slots,
full range, and on a FAT32 (SD/SDHC) card the file name is 8 characters at most
([manual](https://eww.pavc.panasonic.co.jp/dscoi/DC-S5M2/html/DC-S5M2_DVQP2839_eng/0071.html)).
So a 33-point `--monitor` file loads as it is: no `.vlt` writer is needed.
Copy it to the card under a short name (`RPNEUT.cube`). Check a bought
pack's license before porting it to a camera it wasn't sold for, and keep
the ports local.

**Looks as a base plus a delta.** `build BASE/params.json --delta LOOK.json`
layers a look on a decomposed base, and `looks` does every delta for every
camera at once, writing `<look> [<Camera>].cube`. The deltas ship in
`production/lutlab-looks/`, our own numbers:

| Delta | What it does |
|---|---|
| `neutral` | the base unchanged |
| `tungsten` | white balance +28 mireds, golden highlights, a little teal in the deepest shadows |
| `ice-blue` | white balance -24 mireds, blue-cyan shadows, saturation down, skin held |
| `print-teal` | print contrast, greens to teal, teal and orange split, black lifted slightly |
| `soft-print` | low contrast and saturation, open shadows, long soft shoulder |
| `bleach` | saturation to about 60%, harder contrast, steel shadows |

**Looks on a camera monitor.** `looks --monitor` also writes, for each camera look, a 33-point copy clipped
to 0..1 beside the full-size file. It is the same copy `port --monitor` makes (one shared function), and
`rcm` gets none: that set is for a Resolve node and no camera loads it. With `--camera pyxis,s5ii,rcm
--monitor` the `rcm` files are still written and the monitor copies skip it, with a note on stderr; with
`--camera rcm` alone the command refuses. The file name is the camera's first two letters or digits plus the
first four of the look's name without its leading "RP ", upper case, so it fits the S5II's 8-character FAT32
limit: `S5PRIN.cube` is Print Teal for the S5II, `PYPRIN.cube` for the Pyxis. The six shipped looks give
`BLEA`, `ICEB`, `NEUT`, `PRIN`, `SOFT` and `TUNG`, which do not collide. If two looks in one run would, the
last two characters become two hex digits of a hash of the look's name, and the run refuses if they still
collide. Copy the file to the root of the card.

Which cameras can load the files:
- **S5II:** yes, from the card, as the first section above describes (`.cube`, 2 to 33 points, 8 characters on FAT32).
- **Pyxis:** yes, 33-point `.cube`, as the first section above says.
- **GH7, GH5 and iPhone:** this repo knows no route. Nothing here writes or loads a LUT for them.

Delta arithmetic (`model.apply_delta`):
- **Added:** tone `black`/`white`/`mid`/`toe_start`/`shoulder_start`; `hue_lum`; `hue_shift`; `split_tone` (as OKLab vectors); `white_balance`.
- **Multiplied:** tone `contrast` and strengths; `sat_gain`; `sat_rolloff`.
- `sat_gain` and `hue_shift` take a number, `{band: number}`, `{"all": {hue: number}}` or `{band: {hue: number}}`.
- The base's residual stays under the look.

`white_balance` is a new optional stage in scene linear before the curve:
`temp` in mireds (+ warmer), `tint` in thousandths of CIE 1960 v
(+ magenta), via Bradford adaptation along the daylight locus. At zero it is
the identity.

**The ARRI base, without buying anything.** Resolve ships ARRI's LogC3 to
Rec.709 transform as `Arri/Arri Alexa LogC to Rec709.dat`, and lutlab reads it
directly:

```bash
~/.venvs/rp-tools/bin/python -m lutlab decompose "$HOME/DaVinci Resolve/LUT/Arri/Arri Alexa LogC to Rec709.dat" \
  --in logc3 --out "$HOME/DaVinci Resolve/lutlab-base/arri-logc3-709" --name arri-logc3-709
~/.venvs/rp-tools/bin/python -m lutlab looks "$HOME/DaVinci Resolve/lutlab-base/arri-logc3-709/params.json" \
  --camera pyxis,s5ii --out "$LUTLAB_OUT/RP Looks"
```

Keep the base outside Resolve's LUT folders: its `.residual.cube` holds
deltas, not a look, and Resolve would list it as one.

The decomposition fits that LUT to a median Delta E 0.5 before the residual
(exact after it). ARRI's LogC4 LUTs work the same way with `--in logc4`. The
base carries ARRI's values, so it stays local; the guard refuses a repository.

**Buying a pack.** Build the looks first and grade a test shot from each
camera. If a commercial pack still earns its price, buy it for one camera
and `port` it to the other. `diff` gives the distance between your look and
theirs, and between a port and its source.

**Where to point `LUTLAB_OUT`.** On macOS, Resolve scans
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT`, and
`SetLUT` refuses a file anywhere else (tested on Resolve Studio 21.1.1). Point
`LUTLAB_OUT` at a `lutlab` folder inside it. It is outside git and iCloud, and
the Resolve MCP `apply_grade` can put baked looks on `[auto]` timelines
straight away. `~/DaVinci Resolve/LUT` is in `DEFAULT_LUT_ROOTS` for path
lookups, but Resolve does not scan it.

**What the node sees.** A camera LUT expects that camera's log on the node.
In a non-managed project (DaVinci YRGB) it does, so use the `[S5II]` and
`[Pyxis]` files there. Under Resolve Color Management the node sees the
timeline space, whatever the camera, so use the `[RCM]` set:

```bash
~/.venvs/rp-tools/bin/python -m lutlab looks BASE/params.json --camera rcm --size 65 --out "$LUTLAB_OUT/RP Looks RCM"
```

An `[RCM]` look reads DaVinci Intermediate and writes the finished Rec.709
picture back in DaVinci Intermediate code values. Resolve's output transform
then turns it into the same Rec.709 the camera LUT would have given. One set
serves both cameras and anything else Resolve can convert, with no per-clip
input colour space to set.

It needs Project Settings, Color Management to read: timeline colour space
DaVinci WG/Intermediate, output colour space Rec.709 Gamma 2.4, tone mapping
and gamut mapping None. A fresh Color Managed project does not start there:
its timeline and output are both Rec.709 (Scene), so change them first in
each project. Leave tone mapping off: it would compress the picture a second
time. Do not put a Rec.709-out camera LUT in such a project; see the
alternative below.

Measured, on an S5II V-Log clip with RCM Print Teal and Neutral:
- Bake size: against the camera LUT on natural colours (print-teal, ARRI
  base, V-Log), median Delta E 0.22 at 33 points and 0.08 at 65, so bake
  `--size 65`.
- LUT applied by Resolve: in a project whose output was DaVinci Intermediate,
  the render matches lutlab's model at median Delta E 0.43 (p95 1.4).
- End to end: in a project set up as above, the Rec.709 render is 0.79
  (Neutral) and 0.82 (Print Teal) from the camera look, p95 2.4 to 2.8, the
  same ballpark as the camera-LUT renders (0.7 to 0.9).
- Pyxis: the same RCM Print Teal on a Pyxis BRAW clip (Resolve's own Gen 5 to
  DaVinci Wide Gamut decode) renders 0.95 from the Pyxis camera look, p95 3.0.

The alternative, with no second set: give each clip an Input Color Space equal
to the timeline space, so the node sees the camera's code values, and put a
camera LUT on it. It works, but it is a hand step per clip, it switches off
the colour management those clips were in the project for, and a V-Log LUT
fed Rec.709 (Scene) instead clips everything above about +2.5 stops. The
`[RCM]` set replaces it.

`verify-render` and `apply` read a LUT's output as Rec.709, so they do not
suit an `[RCM]` file; check the look through the matching camera LUT.

**library** walks Resolve's LUT folders and writes `registry.json`. Each
entry gets an input-space guess, taken from the part of the name before
" to " and then the nearest folder (a conversion such as "GH5 Cine-D to VLog"
expects Cine-D, so its guess is `unknown`). Byte-identical copies are grouped.
It never moves or deletes anything. Hand fixes go in
`library-overrides.json` beside the registry, keyed by sha256:
`{"<sha256>": {"space": "vlog", "note": "..."}}`.
To see which LUTs projects really use, search the project databases for LUT
paths: `grep -a -o -E '[A-Za-z0-9 _()/.-]{3,120}\.(cube|CUBE|dat)'` over
`~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Resolve Project Library/Resolve Projects/Users/guest/Projects/*/Project.db`.

**apply** previews before anything reaches Resolve: each frame through each
LUT, plus `contact.jpg`, or one LUT over a clip as H.264 through ffmpeg's
`lut3d`. ffmpeg can't read BRAW, so export the Pyxis still from Resolve
first (Gen 5, or DaVinci Intermediate with a DI-input LUT).

**verify-render** checks a Resolve render against the LUT it should carry:
`verify-render SOURCE RENDER LUT --t SECONDS`. ffmpeg decodes one frame of
each (read as BT.709), the source is fit to the render's frame, run through
the LUT, and compared with the render over the interior (`--margin`, default
10%) in Delta E 2000. Median under about 1 means Resolve did what lutlab
models. The source must be in the LUT's input encoding, so for Pyxis render a
Gen 5 frame or clip out of Resolve first. `--render-t` covers a render that
starts at a different time than the source.

**BRAW stills without Resolve.** `lutlab frames CLIP.braw --t 3` decodes a Blackmagic RAW
frame as Film Gen 5 / Wide Gamut through `lutlab/braw/braw2ppm`, a small helper built from the
Blackmagic RAW SDK (`sh lutlab/braw/build.sh`; the binary stays out of git, and `LUTLAB_BRAW2PPM`
points at another build). Checked against Resolve's Gen 5 render of the same frame: median Delta E
0.3 once both are blurred, so colour agrees and only resampling differs.

**Tuning the looks on footage.** The loop, with a new output folder each
round because Resolve caches a LUT by path:

```bash
~/.venvs/rp-tools/bin/python -m lutlab frames CLIP.MOV --t 2 --t 6 --out "$WORK/frames/s5ii"
~/.venvs/rp-tools/bin/python -m lutlab looks BASE/params.json --camera pyxis,s5ii --out "$WORK/looks-v2"
~/.venvs/rp-tools/bin/python -m lutlab apply "$WORK/looks-v2/RP Neutral [S5II].cube" ... --frame "$WORK/frames/s5ii/CLIP t2s.png" --width 420 --out "$WORK/contact-s5ii"
```

`frames` decodes the way `verify-render` does, so the previews match what
Resolve renders. For a Pyxis, take a Gen 5 render from a plain YRGB project
(ffmpeg cannot read BRAW): import the clips into a plain YRGB project, then with the timeline open on the Color page call `Timeline.GrabStill()` at each playhead position and `GalleryStillAlbum.ExportStills(stills, folder, prefix, "tif")` through the scripting API, which writes 16-bit Gen 5 TIFFs without rendering. Edit the numbers in `production/lutlab-looks/`,
bake into a new folder, compare; copy a round into Resolve's LUT root only
when it is the one you want.

For video, lutlab replaces the darktable skill's `generate_lut.py`, which
stays for stills presets.

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

- `white_balance` reaches only from 4000 K to 25000 K on the daylight
  locus (about +96 mireds warm at most), which is plenty for a look but not
  for correcting a tungsten-lit shot. Correct white balance in Resolve
  first.

Tests: `~/.venvs/rp-tools/bin/python production/tests/test_lutlab.py` (synthetic LUTs only).
