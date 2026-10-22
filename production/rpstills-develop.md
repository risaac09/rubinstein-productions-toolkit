# rpstills develop: RAW to 16-bit TIFF, solved onto a skin target

`rpstills develop` develops each selected RAW file and moves three settings, exposure, white balance temperature and tint, until the skin on the cheeks measures one CIELAB target. It writes a 16-bit sRGB TIFF per frame and a `calibration.json` that says, frame by frame, where the solve landed and whether it met the target. It is the step between the cull and `rpstills finish`.

The target it ships with is a **trial**, not a house standard. The house skin reference is undecided; see "The trial look" below.

## Usage

```bash
cd production
~/.venvs/rp-tools/bin/python -m rpstills develop <out dir> --look stills-looks/portrait-cheek-trial-v0.json \
    [--ids ID ...] [--backend apple|lightcraft] [--raw PATH ...]
```

- `<out dir>`: the shoot's rpstills folder. Frames are `selects.json` beside the index, else the cull proposal in `cull.json` (the same selection `rpstills crops` reads), or `--ids`. Each frame's RAW is the index row's `raw` field (the RAW twin found at index time). A frame with no RAW twin is reported and skipped.
- `--raw PATH ...`: develop these RAW files instead; the id is the file stem. `--ids` then picks among them.
- `--look FILE`: a look with a `solve` block, and optionally a `develop` block (below).
- `--backend`: overrides the look's `develop.backend`. The default is `apple`.

It writes `<out dir>/develop/<id>.tif` and `<out dir>/develop/calibration.json`. Both name client frames, so a folder inside any git working tree is refused before anything is developed. A run that develops some frames keeps the rows of frames developed earlier. The exit status is 1 when any frame is not `converged` (failed, pinned or not_converged), and stderr names those frames; a shell chain such as `develop && finish` stops on a missed target.

## How the solve works

1. The RAW is developed small (1500 px on the long edge) at a starting point: 0 EV and the as-shot white balance when the backend reports it (both do), else 5500 and tint 0. Values outside the look's bounds are clipped to them.
2. Vision finds the largest face, with landmarks, on that render. The face is kept in normalised coordinates, so every later render, small or full size, is sampled on the same patch scaled to its own size.
3. The sample is the mean CIELAB of the cheek apples (`rpstills.skin.cheek_mask`), or the box sample when the look says `"sample": "box"`.
4. A damped Newton solve in (EV, mired, tint), where mired is 1,000,000 / the backend's temperature (kelvin on apple). EV drives L\*, mired drives b\*, tint drives a\*. The Jacobian is measured by finite differences at the start and updated by Broyden's rule after every step, so it follows the response as the white balance moves several thousand kelvin. A step is scaled as a whole to stay within its limits, and a step that makes things worse is undone and the next one halved.
5. An axis whose step would leave its bounds lands exactly on the bound. If a later step still pushes it out, it is **pinned**, whether or not that step improved the other axes: it leaves the free set and the remaining axes solve on their own components. With tint pinned at the look's `tint_max`, EV and mired solve L\* and b\*, and a\* falls where it does. A start clipped onto a bound (Apple's as-shot tint is often outside `tint_max`) is held on the first step but not pinned, so the pin is decided on the updated Jacobian, not the one measured at the start. An axis with no room at all (`tint_max` 0) is pinned from the start. The finite-difference probe stays inside the bounds, however narrow.
6. The solve stops when the residual over the free axes' components is within 0.5 (CIELAB units), or after 6 steps. Statuses are judged at 1.0; the half is a margin for the full-size file, which measured up to 0.45 off the small render. A frame costs 4 small renders for the Jacobian plus one per step.
7. The frame is developed at full size, 16-bit, at the solved point, and the sample is measured again on that file (`lab_out`).

Each frame ends with a status, the worse of the solve render's and the delivered file's:

| Status | Meaning |
|---|---|
| `converged` | every requested axis met the target within 1.0 |
| `pinned` | the free axes met it, but an axis sits on a bound and the full target is missed; `delta_e_out` says by how much |
| `not_converged` | the steps ran out, or the full-size file misses what the small render met; the TIFF is the best point found |
| `failed` | no RAW, no face, no landmarks, or the backend failed; no TIFF |

Only `converged` sets `"converged": true`. Nothing is reported as a success that is not one.

## calibration.json

Per frame: `x` (ev, temp_k, mired, tint; `temp` instead of `temp_k` on a backend whose temperature is not Kelvin), `x0` (the start), `as_shot`, `lab` (solve render) and `lab_out` (the delivered file), `residual` (over the free axes), `delta_e` and `delta_e_out` (all of L\*a\*b\*), `pinned`, `free`, `iterations`, `evaluations`, `converged`, `status`, `status_solve`, `backend`, `backend_version`, `look` (name and version), `target`, `sample`, `temp_unit`, `params` (the solve block and every constant the frame was solved under), `sample_px`, `face`, `size`, `seconds`. At the top: the latest run's backend, target and `params`, and `look_values_ignored` when the backend could not apply the look's values. A row kept from an earlier run keeps its own `params`, so read a row's parameters from the row.

## Backends

- **apple** (default, macOS): `rpstills/develop_raw.swift` on Core Image's `CIRAWFilter`, compiled once into `~/Library/Caches/rpstills` the way `vision.py` compiles its helper. It sets exposure, neutral temperature and neutral tint, scales to a long edge, and writes a TIFF with the sRGB profile (16-bit RGBA with an opaque alpha). It takes no look values; a look's `develop.values` are listed as ignored.
- **lightcraft** (TRIAL, opt-in): `lightcraft-cli` from the environment variable `RPSTILLS_LIGHTCRAFT`; without it the backend is not available. Each render is one `lightcraft-cli run --import RAW library.selectAll [develop.profile id=P] develop.set {json} app.export path=OUT longEdge=N [bitDepth=16]`, and the start is its as-shot white balance from `lightcraft-cli run --import RAW library.selectAll develop.wb mode=asShot`. On a RAW LightCraft has no Kelvin scale for (the Panasonic RW2 test files), `wb.temp` is relative: 6500 is as shot, not 6500 K. Its rows therefore carry `temp` and a `temp_unit`, never `temp_k`, and the look's `temp_bounds_k` bounds that relative value. The look's `develop.values` are LightCraft control ids (`light.contrast`, `curve.darks`, and so on), and `develop.profile` a LightCraft profile id. The solve sets `light.exposure`, `wb.temp` and `wb.tint` itself. The binary's path and `--version` are recorded in `calibration.json`. Nothing should rely on this backend until its tool trial ends in an adopt decision.

## Look file blocks

Looks gain two blocks. `rpstills look` ignores both, so a look file stays a valid LUT parameter file.

```json
"develop": {"backend": "apple", "profile": null, "values": {}},
"solve": {"target_lab": [64, 15, 14], "sample": "cheek", "free": ["ev", "mired", "tint"],
          "tint_max": 6, "ev_bounds": [-3, 3], "temp_bounds_k": [2500, 12000]}
```

`free` may leave an axis out: it then stays at its start, and its Lab component is not asked for (with `["ev", "mired"]`, a frame converges on L\* and b\* alone). `temp_bounds_k` is Kelvin only on the apple backend; on lightcraft it bounds `wb.temp` in LightCraft's own units.

## The trial look

`stills-looks/portrait-cheek-trial-v0.json` targets L\* 64, a\* 15, b\* 14 at the cheek apples: hue 43 degrees, 117.7 degrees on a Rec.709 vectorscope. It was picked by eye on one overcast session. The published skin-tone line on a vectorscope sits near 123 degrees, so the two references disagree by about 5.5 degrees, and which one rules is undecided. `rpstills audit` reports the distance to both. The file is labelled as a trial, and its version stays below 1.0 until a reference is decided.

## What it does not do

- No look beyond exposure and white balance on the apple backend: contrast, curves and colour come later (`rpstills finish`, or a LightCraft look on the trial backend).
- No per-person judgement. Every frame is pushed onto the same target; someone with naturally ruddy or olive skin is pushed toward it like everyone else. Review the set.
- No orientation handling beyond what the engine applies. Vision runs on the developed render, so the face and the sample always agree with the file.
- It does not touch the original RAW files or write sidecars.

## Known limits

- Tint is in each backend's own units. `tint_max` 6 was set in LightCraft units, where tint 0 sits near neutral. On the test frames Apple's as-shot tint was -14.4, and Apple needed tint between -29 and beyond -40 to bring a\* down to 14 to 18, so with the trial look every frame pins tint on the apple backend and a\* lands 4 to 10 units high. Temperature differs too: on these RW2 files LightCraft's `wb.temp` is relative (6500 = as shot), so its solved values (8200 to 12000, one frame on the bound) are not Kelvin and cannot be compared with Apple's 5700 to 7200 K. With the trial look on the 7 test frames, Apple pinned tint on all 7; LightCraft converged on 1 and pinned tint on 6 (tint and temperature on one). At the same tint cap Apple's cheeks measured 2.0 to 6.7 a\* higher than LightCraft's (L\* within 0.4, b\* within 1.4). Bounds and solved values do not transfer between backends.
- The sample is the cheek apples of the largest face, with the geometry checked on one subject (see `rpstills-audit.md`). A face Vision misses, or one without landmarks, fails the frame with the cheek sample; the box sample needs no landmarks but measures a different patch, so it needs its own target.
- The solve renders at 1500 px. On the test frames the full-size file measured within 0.45 of the solve render in each of L\*, a\* and b\*; the delivered file is measured anyway, and a miss shows as `not_converged`.
- Time: 3 to 6 seconds a frame for 6000 x 4000 RW2 files on an M4 Max, either backend: 6 to 9 renders each, the last at full size.
- Measured on one camera (Panasonic RW2) and one session. Other cameras, mixed light and backlit faces are untested.
