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
