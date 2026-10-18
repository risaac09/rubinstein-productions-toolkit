# rpstills: the offline front end of the stills lane

Index, cluster and cull a shoot's camera JPEGs and build a review sheet.
Nothing here decodes a RAW file or talks to DaVinci Resolve; the grade and
the render stay in Resolve through the `rpresolve` tools. Plan of record:
the internal stills pipeline plan.

Runs on the venv at `~/.venvs/rp-tools` (Homebrew Python 3.14 with Pillow,
imagehash, numpy and OpenCV from `requirements.txt`; see the lutlab README for
the one-line setup). Faces and whole-body person boxes come from macOS Vision through
`vision_stills.swift`, compiled once into `~/Library/Caches/rpstills` by
`vision.py`; without `swiftc` the index still runs with empty faces, and a run
is then ranked by the frame alone (see "Frames with no face" below).

```bash
cd production
~/.venvs/rp-tools/bin/python -m rpstills run <shoot folder> <out dir> --session <name>
```

`<out dir>` must sit outside any git repository (the Work volume's `Active`
tier is the house place). It receives:

| File | Stage | What |
|---|---|---|
| `proxies/<id>.jpg` | index | upright 2000 px proxies, one per camera JPEG |
| `index.jsonl` | index | one row per frame: EXIF (or a derived time and its `time_source`), phash, faces with capture quality and eye state, face sharpness, person boxes, the whole-frame measures, the RAW twin's path |
| `clusters.json` | cluster | segments (gap over 20 min), bursts (2 s and phash 8), runs (one person or group in front of the camera, gap over 45 s or a class change) |
| `cull.json` | cull | a score per frame inside its run and a proposal: 3 picks per solo run, 3 per pair or group run, one per burst at most; 2 to 4 frame picks for a run with no subject face |
| `review.html` | review | the sheet: every frame per run, proposals in yellow; click to keep or drop, name the run, export `selects.json` |

## Wide frames, small faces, no face, no EXIF

Four things reach past a headshot session's camera JPEGs, found on 125 frame
grabs (2976x1324) of a wide stage:

- **Tiles.** A face of 5% of the frame height is about 66 px on a 2000 px
  proxy of a 2.25:1 frame, and Vision misses it. A frame with no subject face
  at proxy size gets a second pass: the upright original is cut into a 3x2
  grid of overlapping tiles (portrait: 2x3), each brought up to about 2200 px
  on its long edge (at most 2x, never down), and Vision runs on those. Faces
  are mapped back to the frame and merged, duplicates dropped. On the 125
  grabs it found a subject face on 43 more frames (24 to 67) in about 20
  seconds. `--tiles off` skips it, `--tiles all` tiles every frame. Rows
  record `tiled` and `face_source` (`proxy` or `tiles`). Tile files are
  written under `<out>/tiles/` a dozen frames at a time and removed.
- **The subject rule.** A face counts when it is at least 4% of the frame
  height and at least half the height of the largest face in the frame
  (`cluster.counted_faces`). An audience behind a performer is background,
  not a group; without the rule tiled detection would turn every stage frame
  into one. `clusters.json` and `cull.json` record `min_face` and
  `subject_ratio`.
- **Frames with no face.** A run with no counted face used to propose
  nothing. It is now ranked by the frame (`frames.py`): sharpness ranked
  inside the run, exposure (a lens cap, a white sheet, fog and blown
  highlights score 0 or low; a dark stage with a lit performer scores well),
  and composition when Vision found a person (size, placement on a third or
  the centre line, headroom). A frame softer than a Laplacian variance of 40
  at 1000 px is out of focus in absolute terms and scores 0: a rank alone
  would hand a perfect score to the best of a run of blurred frames. The run
  proposes 2 frames, one more per 12 it holds up to 4, one per burst, none
  under a score of 0.30, and none within 8 bits of a pick's perceptual hash.
  A face run also skips a frame within 4 bits of one of its own picks, which
  a static camera and a performer who sits still produce by the dozen. Each
  score and proposal says `kind` (`face` or `frame`); the review sheet marks
  a frame score with an `f` and shows its parts on hover. Frame picks have no
  crop: the standard profiles are face-class profiles, so `crops` plans none
  for them.
- **Times without EXIF.** Frame grabs have no `DateTimeOriginal`, and without
  a time the cluster stage cannot tell a gap from a run. A frame takes its
  time from, in order: a sidecar (`--times FILE`, or `times.json` or
  `times.tsv` in the shoot folder: `{"<id>": "<ISO>"}`, or `{"starts":
  {"<prefix>": "<ISO>"}, "interval": 4}`, or `id<TAB>time` lines), a stamp in
  the file name (`20260115-100512`, `2026-01-15_10-05-12`), or its place in a
  numbered sequence (`shot_007` at `--interval 4` after `--start`). The row's
  `time_source` says which; a sequence time is a position in a grab, not a
  clock. A frame none of these reaches stays undated, as before, and the index
  says how many.

Measured on those 125 grabs: faces on 24 frames and 19 picks (none for the 101
face-less frames) became a subject face on 67 frames and 67 picks over 42 runs,
18 of them frame picks in 11 of the 15 runs with no subject face. The other
four held only out-of-focus frames (three runs) or blank ones (one). The
numbers depend on the footage; the parameters are at the top of `cluster.py`,
`cull.py`, `frames.py` and `index.py`.

A fifth stage, `crops`, reads `selects.json` (exported from the sheet and
placed beside `index.jsonl`; without it the cull proposal stands in) and
writes `crops.json` and `crops-preview.jpg`: a crop per standard profile in
`production/stills-config.json`, normalized and in pixels on the upright
frame. Flags per crop: `tight` (the frame could not hold the wanted crop),
`face_cut` (a counted face is not fully inside) and `upscale` (fewer pixels
than the profile's output size). Run it with
`~/.venvs/rp-tools/bin/python -m rpstills crops <out dir>`.

Every stage can be rerun on its own (`index`, `cluster`, `cull`, `review`)
and every output is additive; no frame is moved, renamed or deleted. A
rerun of `index` skips proxies that already exist and rewrites the rest.

Scores compare only inside a run. Vision's capture quality is the main
term (0.5), then face sharpness ranked within the run (0.2), eyes open
(0.2) and face size (0.1). A run with no subject face scores by the frame
instead (above). The thresholds live at the top of `cull.py`, `cluster.py`
and `frames.py` and are reported in each output file's `params`.

The sheet never names a person. The name field is Isaac's, from the
roster, and it travels into `selects.json` for the delivery file names.

Tests: `~/.venvs/rp-tools/bin/python production/tests/test_rpstills_pure.py` and
`production/tests/test_rpstills_nonface.py` (synthetic images and a fake
Vision; no real media, no network).

## Grading a stills session (RAW only for paid work)

The look is a measured fit, not a guess. Paid deliverables are graded and
rendered from the RAW files; the camera JPEG twins stay proxies.

1. **Colour management.** Set the stills timeline's output colour space to
   `sRGB` (`SetSetting("colorSpaceOutput", "sRGB")`, read it back). No output
   transform node. An RW2's Input Color Space has no effect, and the raw
   decode settings are in the Color page's Camera Raw panel, not the API.
2. **Neutral render.** Render every selected frame from RAW with no grade, one
   single-frame job per still (single clip mode, `MarkIn` = `MarkOut` = the
   item's start frame).
3. **Measure.** `rpstills.measure` samples skin in the face box and reports
   CIELAB. Look at the session medians and spreads before choosing targets.
4. **Fit.** `measure.fit_look` gives a first exposure and white balance guess
   for the target skin colour. Then close the loop in Resolve: write the LUT
   (`python3 -m rpstills look params.json out.cube`, put it under Resolve's
   LUT folder, `RefreshLUTList()`), `SetLUT` on node 1 of each item, render a
   spread subset, measure, and correct. Damp the white balance correction by
   half; the first run oscillated without it. Use a new LUT file name for every
   iteration so Resolve cannot serve a cached copy. Two iterations were enough.
5. **Trims.** `look.trim_ev` gives a bounded per-shot exposure trim from the
   neutral skin lightness; each distinct trim is one LUT variant.
6. **Verify on every frame.** Compare skin lightness spread, hue and chroma
   against the neutral render and look at the extremes.

The scripting API cannot create nodes, so the look lives in node 1 as a LUT.
Hand tweaks go in nodes 2 and up in the Color page, on top of it.

`stills-looks/portrait-natural-v1.json` holds the first look with its fit and
validation numbers. Refit for a new session; do not reuse the numbers.
