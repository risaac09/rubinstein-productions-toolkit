# lutlab cst, lutstats and pack

Three lutlab commands that need numpy and nothing else (only `pack --base`, which bakes looks, needs scipy). They run on the
`~/.venvs/rp-tools` venv like the rest of lutlab, and also on macOS's `/usr/bin/python3` (3.9) with numpy
(checked), so a machine without the image packages can still make and check LUTs.

- `cst` writes a colour space transform as a `.cube`: a camera log into DaVinci Intermediate, or into
  Rec.709 / sRGB for viewing.
- `lutstats` measures a LUT in Delta E 2000 against the exact transform it claims, or measures a round
  trip through two LUTs.
- `pack` zips a set of LUTs for Resolve, with a README for Resolve on an iPad, a cube that proves the import
  worked, and SHA-256 sums.

```bash
cd production
~/.venvs/rp-tools/bin/python -m lutlab cst --from vlog --to di --size 65 --out DIR          # DIR/vlog-to-di-65.cube
~/.venvs/rp-tools/bin/python -m lutlab cst --from vlog --to r709 --out DIR                  # DIR/vlog-to-r709-33.cube
~/.venvs/rp-tools/bin/python -m lutlab lutstats check DIR/vlog-to-di-65.cube --from vlog --to di
~/.venvs/rp-tools/bin/python -m lutlab lutstats roundtrip --from vlog --to di --size 33 [--interp trilinear]
~/.venvs/rp-tools/bin/python -m lutlab pack --camera s5ii,gh7,rcm --out DIR                 # DIR/rp-luts-YYYYMMDD.zip
~/.venvs/rp-tools/bin/python -m lutlab pack --camera s5ii,gh7,rcm --base BASE/params.json --size 65 --date 20261022 --out DIR
```

Output goes to `--out` or `$LUTLAB_OUT/cst` / `$LUTLAB_OUT/pack`. Like everything in lutlab, it refuses a
git work tree and the iCloud folders. An existing file is never replaced without `--overwrite`.

## cst

`--from vlog|bmdfilm5|logc3|logc4|di|lin709 --to di|r709|srgb`, `--size 33` (default) or `65`. The file
is `<from>-to-<to>-<size>.cube`.

It is a technical transform, not a look. It decodes the source's code values to scene linear and changes
primaries through linear Rec.709, the hub every lutlab space goes through. Then it encodes to the target
and clips to 0..1. There is no tone mapping and no gamut mapping: it is Resolve's Color Space Transform
with both set to None. So `--to r709` and `--to srgb` clip everything above display white, scene linear
1.0, about 2.5 stops over 18% grey. Use those to judge exposure and colour, or to see the DaVinci
Intermediate output of a hand-built pipeline, not as a grade.

At every lattice node the table equals `port` applied to an identity LUT: an identity cube in the target
space, ported from the source. That was the way to get a CST before this command existed, and the tests
hold the two equal. V-Log 18% grey (code 0.4233) lands on DaVinci Intermediate 0.3360.

The cameras: the S5II and the GH7 shoot V-Log (`--camera gh7` now works in `port` and `looks` too), and the
Pyxis BRAW decodes as Film Gen 5 (`bmdfilm5`).

## lutstats

`check LUT --from S --to D` samples the LUT where footage lands and compares it with the exact `cst`
transform from S to D. "Where footage lands" means the same natural colours `decompose` fits on (greys from
-6 to +4 stops, chroma up to about the ColorChecker's strongest patch, every 5 degrees of hue), plus the
neutral axis from the space's black code to 1.0. A cube from anywhere can be checked, for example a
CST exported from Resolve.

`roundtrip --from S --to D --size N` bakes S to D and D to S at N points, sends the samples there and back,
and compares them with where they started. It reports Delta E 2000 and the error in 10-bit code values.

Both print JSON, with mean, p99 and max, and a one-line summary. Delta E 2000 is measured the way
`lutlab diff` measures it: codes decoded to linear Rec.709, negatives to 0, CIELAB with diffuse white at
L* 100. Scene values above white keep counting (L* goes past 100). Interpolation is tetrahedral by
default, which is ffmpeg `lut3d`'s default and one of Resolve's two settings. `--interp trilinear` uses
lutlab's own `cube.sample`. lutstats only reports: nothing passes or fails.

Measured, natural colours, tetrahedral:

| | mean | p99 | max |
|---|---|---|---|
| `check` vlog to di, 33 | 0.074 | 0.42 | 0.61 |
| `check` vlog to di, 65 | 0.026 | 0.18 | 0.28 |
| `check` vlog to r709, 33 | 0.52 | 4.35 | 11.7 |
| `check` vlog to r709, 65 | 0.15 | 1.73 | 6.19 |
| `roundtrip` vlog and di, 33 | 0.074 | 0.55 | 0.85 |
| `roundtrip` vlog and di, 65 | 0.024 | 0.17 | 0.30 |

The display targets carry the large maximums. Their error collects at the clip to display white and at
the Rec.709 gamut edge, which is a hard corner the lattice cannot follow; 65 points cuts it two to three times. On the
neutral axis, tetrahedral keeps R = G = B exactly and trilinear does not. The vlog round trip's neutral
maximum (1.5 at 33 points) sits at V-Log 1.0, eight stops over 18% grey (5.5 over white).

On a real V-Log ColorChecker frame from each camera, ffmpeg `lut3d=interp=tetrahedral` and lutstats'
tetrahedral sampler agree within Delta E 0.005. The 33-point vlog-to-r709 cube sits a median 0.16 (p99 1.9 to 2.9)
from the exact transform, and the 65-point cube a median 0.04 (p99 0.7 to 1.3).

## pack

`pack --camera LIST [--base PARAMS.json [--looks DIR|FILE ...]] [--size 33|65] [--date YYYYMMDD]` writes
`rp-luts-<date>.zip`. Everything sits under one folder, `rp-luts-<date>/`:

- `cst/`: for each camera's log, `<log>-to-di-N.cube` and `<log>-to-r709-N.cube` (the S5II and GH7 share
  the V-Log pair). For `rcm`, `di-to-r709-N.cube`, only for the last node of a project that is not colour
  managed (see below): in a Resolve Color Managed project with Rec.709 output it would be converted twice.
- `looks/`: `<look> [<Camera>].cube` for every look delta on the base, for every camera. This is what the
  `looks` command makes, and it needs scipy. It only happens with `--base`; `--looks` defaults to the
  shipped deltas in `production/lutlab-looks/`. Without `--base` the folder is left out and the README says
  so.
- `check/cast-magenta.cube`: not a look. It adds 0.05 to red and blue and takes 0.05 from green, on any
  input, so grey turns visibly magenta. Put it on a clip to prove a LUT reached the node, then take it off.
- `README.txt`: where the files go on an iPad (Files > On My iPad > DaVinci Resolve > LUT, then Settings >
  Color Management > Update Lists), which files need a DaVinci Intermediate timeline, and what is
  unverified.
- `SHA256SUMS`: run `shasum -a 256 -c SHA256SUMS` inside the folder.

The zip is reproducible: the members are in a fixed order and their time stamps come from `--date`, so the
same inputs give the same bytes.

**Without colour management.** On an iPad where the V-Log input transform is not confirmed, the pack still
gives the colour-managed result in a project that is not colour managed (DaVinci YRGB). Use three nodes:
`vlog-to-di`, then the `[RCM]` look, then `di-to-r709`. On a real GH7 frame that chain, at 33 points
through ffmpeg, sits a median Delta E 0.28 (p99 1.06) from the `[GH7]` camera look applied directly.

## Reading Lumix V-Log files

The S5II and GH7 H.265 V-Log files are full range, as their `pc` tag says. On the ColorChecker grey ramp
(ISO 3200, both cameras), a full-range V-Log model fits the four lightest patches to RMS 1.7-2.1 code
values. A legal-range model is off by 10-11 codes. The steps measure 70-72 codes per stop, against 74.4
expected for full range and 63.7 for legal. The frame's darkest 0.1% sits at codes 123-127, which is V-Log
zero light in full range (128); legal range would put it near 174. So read them without legal-range
expansion, and tell ffmpeg the matrix too, or it may assume BT.601:

```bash
ffmpeg -ss 3 -i CLIP.MOV -frames:v 1 -vf "setparams=range=pc:colorspace=bt709:color_primaries=bt709:color_trc=bt709,\
scale=w=1440:h=-2:in_color_matrix=bt709:in_range=full:out_range=full:flags=area+accurate_rnd+full_chroma_int,\
format=rgb48le,lut3d=file=vlog-to-r709-65.cube:interp=tetrahedral" -pix_fmt rgb48be frame.png
```

## What these commands do not do

- No tone mapping, gamut mapping, shaper or 1D LUTs (`cube.read_cube` still refuses 1D files).
- No Apple Log, HLG or S-Log spaces. There is no iPhone camera here.
- No in-camera monitor copies. `port --monitor` and `looks --monitor` make those.
- `pack` does not install anything. Copying the files to the iPad and the import steps stay with a person.
- Nothing here checks how Resolve reads a cube. `verify-render` compares a Resolve render against a LUT.

## Known limits

- A cube's output is clipped to 0..1, as `port` clips it. DaVinci Intermediate can carry negative values
  (sub-black noise, a few colours outside DaVinci Wide Gamut), but a `<log>-to-di` cube writes them as 0.
  On the real frames a V-Log, DI and V-Log round trip through ffmpeg moved 0.09% (GH7) and 0.12% (S5II)
  of pixels by more than Delta E 1 at 33 points, and 0.002% and 0.08% at 65; the clip alone, exact and
  without a lattice, moves 0.02% and 0.09%. The clip is also where the round trip's large 10-bit code
  maximums come from (24 to 52 codes, in shadow pixels with a channel below V-Log black). The p99 was
  3.5 (GH7) and 1.7 (S5II) codes at 33 points, 3.3 and 0.4 at 65.
- `lutstats` samples synthetic natural colours, not footage. Its numbers describe the LUT, not a shot.
- The two cameras on the same chart at the same ISO did not match out of the box. The GH7's chart was
  0.75 stop darker. With exposure matched, L* agreed within 0.4 on every grey patch, but the GH7 sat
  about 2 to 4.5 a* more magenta on the lighter greys (Delta E 1.3 to 5.5). That is camera balance, which
  is the Balance node's job, not the CST's.
- Unverified on a device, and listed in the pack's README:
  - the iPad import route, which comes from a third-party tutorial;
  - whether the free iPad app loads a LUT in a node (Blackmagic lists it under Studio);
  - whether the iPad app interpolates tetrahedrally and lists 65-point files;
  - whether the iPad app offers V-Log as an input colour space.
