# rpstills: the offline front end of the stills lane

Index, cluster and cull a shoot's camera JPEGs and build a review sheet.
Nothing here decodes a RAW file or talks to DaVinci Resolve; the grade and
the render stay in Resolve through the `rpresolve` tools. Plan of record:
corpus note "2026-10-06 Stills production pipeline plan".

Runs on macOS's `/usr/bin/python3` (Pillow, imagehash, numpy and OpenCV
live there). Faces come from macOS Vision through `vision_stills.swift`,
compiled once into `~/Library/Caches/rpstills` by `vision.py`; without
`swiftc` the index still runs with empty faces and the cull proposes nothing.

```bash
cd production
/usr/bin/python3 -m rpstills run <shoot folder> <out dir> --session <name>
```

`<out dir>` must sit outside any git repository (the Work volume's `Active`
tier is the house place). It receives:

| File | Stage | What |
|---|---|---|
| `proxies/<id>.jpg` | index | upright 2000 px proxies, one per camera JPEG |
| `index.jsonl` | index | one row per frame: EXIF, phash, faces with capture quality and eye state, face sharpness, the RAW twin's path |
| `clusters.json` | cluster | segments (gap over 20 min), bursts (2 s and phash 8), runs (one person or group in front of the camera, gap over 45 s or a class change) |
| `cull.json` | cull | a score per frame inside its run and a proposal: 3 picks per solo run, 3 per pair or group run, one per burst at most |
| `review.html` | review | the sheet: every frame per run, proposals in yellow; click to keep or drop, name the run, export `selects.json` |

A fifth stage, `crops`, reads `selects.json` (exported from the sheet and
placed beside `index.jsonl`; without it the cull proposal stands in) and
writes `crops.json` and `crops-preview.jpg`: a crop per standard profile in
`production/stills-config.json`, normalized and in pixels on the upright
frame. Flags per crop: `tight` (the frame could not hold the wanted crop),
`face_cut` (a counted face is not fully inside) and `upscale` (fewer pixels
than the profile's output size). Run it with
`/usr/bin/python3 -m rpstills crops <out dir>`.

Every stage can be rerun on its own (`index`, `cluster`, `cull`, `review`)
and every output is additive; no frame is moved, renamed or deleted. A
rerun of `index` skips proxies that already exist and rewrites the rest.

Scores compare only inside a run. Vision's capture quality is the main
term (0.5), then face sharpness ranked within the run (0.2), eyes open
(0.2) and face size (0.1). The thresholds live at the top of `cull.py`
and `cluster.py` and are reported in each output file's `params`.

The sheet never names a person. The name field is Isaac's, from the
roster, and it travels into `selects.json` for the delivery file names.

Tests: `/usr/bin/python3 production/tests/test_rpstills_pure.py`.

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
