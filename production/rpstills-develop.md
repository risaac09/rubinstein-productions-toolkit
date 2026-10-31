# rpstills develop: RAW to 16-bit TIFF, neutral per person, exposure on the cheek

**Status:** operational.

`rpstills develop` develops each selected RAW file and moves three settings, exposure, white balance temperature and tint, until the frame measures what the look asks for. It writes a 16-bit sRGB TIFF per frame and a `calibration.json` that says, frame by frame, where the solve landed and whether it met the target. It is the step between the cull and `rpstills finish`.

The house rule for delivered portraits: **neutralise on a neutral reference, set exposure on the cheek, never push skin hue to a number.** That is the look's `solve.mode` `"neutral"`, shipped as `stills-looks/portrait-neutral-v1.json`. The older mode, `"target"`, solves the cheek's whole colour onto one CIELAB target; it stays for matching a set to one hero frame.

## Usage

```bash
cd production
~/.venvs/rp-tools/bin/python -m rpstills develop <out dir> --look stills-looks/portrait-neutral-v1.json \
    [--ids ID ...] [--backend apple|lightcraft] [--raw PATH ...] \
    [--neutral-box X0 Y0 X1 Y1] [--wb-from ID]
```

- `<out dir>`: the shoot's rpstills folder. Frames are `selects.json` beside the index, else the cull proposal in `cull.json` (the same selection `rpstills crops` reads), or `--ids`. Each frame's RAW is the index row's `raw` field (the RAW twin found at index time). A frame with no RAW twin is reported and skipped.
- `--raw PATH ...`: develop these RAW files instead; the id is the file stem. `--ids` then picks among them.
- `--look FILE`: a look with a `solve` block, and optionally a `develop` block (below).
- `--backend`: overrides the look's `develop.backend`. The default is `apple`.
- `--neutral-box X0 Y0 X1 Y1` (neutral mode): a box, normalised 0..1 with the origin top left, that is the neutral reference in every frame solved on its own neutral, in place of the garment found below the face. For a grey card held anywhere in frame. With `--wb-from` it applies to the reference frame only, and to nothing when the reference comes from `calibration.json`.
- `--wb-from ID` (neutral mode): frame ID is solved first on its own neutral; every other frame takes its white balance and solves exposure alone. ID is a frame of this run, or a row of `<out dir>/develop/calibration.json` from an earlier run. For frames in the same light where the subject wears no neutral. On the opt-in lightcraft backend `wb.temp` can be relative to each file's as-shot white balance, so pair frames shot with the camera's white balance fixed, not on auto.

It writes `<out dir>/develop/<id>.tif` and `<out dir>/develop/calibration.json`. Both name client frames, so a folder inside any git working tree is refused before anything is developed. A run that develops some frames keeps the rows of frames developed earlier. The exit status is 1 when any frame is not `converged` (failed, pinned or not_converged), and stderr names those frames; a shell chain such as `develop && finish` stops on a missed target.

## Neutral mode

The solve aims at three numbers: the cheek's L\* onto `target_L` (64 by default), and the median a\* and b\* of a neutral reference onto `neutral_ab` (0, 0). The skin's hue and chroma are not asked for: they land where the complexion and the light put them.

The neutral reference, per frame:

1. **Auto** (`rpstills.skin.neutral_mask`): a white or grey garment, or a grey card held low, below the face. It looks from 0.15 face heights under the face box to the bottom of the image, within 3 face widths either side of the face centre, and keeps pixels with L\* at least 40 and chroma at most 25 that are not skin. Skin is judged against this frame's own cheek, measured on the same render: at least half its chroma and within 25 degrees of its hue. A neck or chest is left out that way, and a white shirt in warm light, which a generic skin-tone box would also take, stays in. The mask is opened, and only the parts that reach 0.15 face widths either side of the face centre are kept. That band is narrower than a neck, so a pale wall, path or sky seen beside the neck or the body is dropped. Under 0.2% of the frame left means no neutral, and so does a mask whose colours spread too far: when the 90th percentile of the a\*b\* distance from its median passes 9 it is a print or a tinted wall joined to the garment, not a neutral. The mask is taken once, on the first render (the as-shot white balance), and carried to every later render and to the full-size file by nearest-neighbour resizing; it is never taken again at another white balance.
2. **Box** (`--neutral-box`): the box, in every frame solved on its own neutral (with `--wb-from`, the reference frame only).
3. **From another frame** (`--wb-from ID`): that frame's solved mired and tint, held exactly. Only EV moves, onto the cheek L\*, and the status is judged on L\* alone. The reference must be a `converged` neutral-mode row on the same backend (tint and temperature are each backend's own units); a reference developed in this run that fails, pins or does not converge hands nothing on, and the frames that needed it fail with that reason. A `--wb-from` ID in neither the run nor `calibration.json`, or a stored row that is not a converged neutral row on this backend, stops the run before anything is developed.

The neutral is the median CIELAB over the mask, with clipped pixels (any channel at or above 252/255) left out. A render where every pixel of it is clipped cannot be measured: the solve probes exposure downward instead of up, or takes a shorter step. On the delivered file, or at the start, it fails the frame. A frame where no neutral is found, and no `--wb-from` was given, fails with:

> no neutral reference in frame: shoot a grey card, pass --neutral-box, or --wb-from ID (a frame in the same light)

It is never developed on the cheek alone.

## How the solve works

1. The RAW is developed small (1500 px on the long edge) at a starting point: 0 EV and the as-shot white balance when the backend reports it (both do), else 5500 and tint 0. Values outside the look's bounds are clipped to them (a `--wb-from` white balance is held as it is).
2. Vision finds the largest face, with landmarks, on that render. The face is kept in normalised coordinates, so every later render, small or full size, is sampled on the same patch scaled to its own size. In neutral mode the neutral mask is taken on this render too.
3. The skin sample is the mean CIELAB of the cheek apples (`rpstills.skin.cheek_mask`), or the box sample when the look says `"sample": "box"`. Target mode compares its L\*a\*b\* with `target_lab`; neutral mode compares its L\* and the neutral's a\* and b\* with `[target_L, *neutral_ab]`.
4. A damped Newton solve in (EV, mired, tint), where mired is 1,000,000 / the backend's temperature (kelvin on apple). EV drives L\*, mired drives b\*, tint drives a\*. The Jacobian is measured by finite differences at the start and updated by Broyden's rule after every step, so it follows the response as the white balance moves several thousand kelvin. A step is scaled as a whole to stay within its limits, and a step that makes things worse is undone and the next one halved.
5. An axis whose step would leave its bounds lands exactly on the bound. If a later step still pushes it out, it is **pinned**, whether or not that step improved the other axes: it leaves the free set and the remaining axes solve on their own components. With tint pinned at the look's `tint_max`, EV and mired solve L\* and b\*, and a\* falls where it does. A start clipped onto a bound (Apple's as-shot tint is often outside a small `tint_max`) is held on the first step but not pinned, so the pin is decided on the updated Jacobian, not the one measured at the start. An axis with no room at all (`tint_max` 0) is pinned from the start. The finite-difference probe stays inside the bounds, however narrow.
6. The solve stops when the residual over the free axes' components is within 0.5 (CIELAB units), or after 6 steps. Statuses are judged at 1.0; the half is a margin for the full-size file, which measured up to 0.45 off the small render in target mode (more on some `--wb-from` frames, see Known limits). A frame costs 4 small renders for the Jacobian plus one per step (2 plus one per step with `--wb-from`).
7. The frame is developed at full size, 16-bit, at the solved point, and the sample (and the carried neutral mask) is measured again on that file.

Each frame ends with a status, the worse of the solve render's and the delivered file's:

| Status | Meaning |
|---|---|
| `converged` | every requested axis met the target within 1.0 |
| `pinned` | the free axes met it, but an axis sits on a bound and the full target is missed; `delta_e_out` says by how much |
| `not_converged` | the steps ran out, or the full-size file misses what the small render met; the TIFF is the best point found |
| `failed` | no RAW, no face, no landmarks, no neutral reference, or the backend failed; no TIFF |

Only `converged` sets `"converged": true`. Nothing is reported as a success that is not one.

## calibration.json

Per frame: `x` (ev, temp_k, mired, tint; `temp` instead of `temp_k` on a backend whose temperature is not Kelvin), `x0` (the start), `as_shot`, `lab` (the cheek on the solve render) and `lab_out` (the cheek on the delivered file), `residual` (over the free axes), `delta_e` and `delta_e_out` (against `target`: in neutral mode over the cheek L\* and the neutral's a\* and b\*, and over L\* alone on a `--wb-from` frame), `pinned`, `free`, `iterations`, `evaluations`, `converged`, `status`, `status_solve`, `mode`, `target` (`[L*, a*, b*]`; a\* and b\* are null on a `--wb-from` frame), `neutral` (null in target mode, else `source` auto, box or wb_from, `n_px` and `lab_out` of the neutral on the delivered file, `box`, `from`), `backend`, `backend_version`, `look` (name and version), `sample`, `temp_unit`, `params` (the solve block and every constant the frame was solved under), `sample_px`, `face`, `size`, `seconds`. At the top: the latest run's backend, mode, target and `params`, and `look_values_ignored` when the backend could not apply the look's values. A row kept from an earlier run keeps its own `params`, so read a row's parameters from the row.

## Backends

- **apple** (default, macOS): `rpstills/develop_raw.swift` on Core Image's `CIRAWFilter`, compiled once into `~/Library/Caches/rpstills` the way `vision.py` compiles its helper. It sets exposure, neutral temperature and neutral tint, scales to a long edge, and writes a TIFF with the sRGB profile (16-bit RGBA with an opaque alpha). It takes no look values; a look's `develop.values` are listed as ignored.
- **lightcraft** (opt-in): `lightcraft-cli` from the environment variable `RPSTILLS_LIGHTCRAFT`; without it the backend is not available. Each render is one `lightcraft-cli run --import RAW library.selectAll [develop.profile id=P] develop.set {json} app.export path=OUT longEdge=N [bitDepth=16]`, and the start is its as-shot white balance from `lightcraft-cli run --import RAW library.selectAll develop.wb mode=asShot`. On a RAW LightCraft has no Kelvin scale for (the Panasonic RW2 test files), `wb.temp` is relative: 6500 is as shot, not 6500 K. Its rows therefore carry `temp` and a `temp_unit`, never `temp_k`, and the look's `temp_bounds_k` bounds that relative value. The look's `develop.values` are LightCraft control ids (`light.contrast`, `curve.darks`, and so on), and `develop.profile` a LightCraft profile id. The solve sets `light.exposure`, `wb.temp` and `wb.tint` itself. The binary's path and `--version` are recorded in `calibration.json`. It was adopted on 2026-10-22 as the opt-in RAW backend; apple stays the default.

## Look file blocks

Looks gain two blocks. `rpstills look` ignores both, so a look file stays a valid LUT parameter file.

```json
"develop": {"backend": "apple", "profile": null, "values": {}},
"solve": {"mode": "neutral", "target_L": 64, "neutral_ab": [0, 0],
          "neutral": {"min_L": 40, "max_chroma": 25, "min_share": 0.002},
          "sample": "cheek", "free": ["ev", "mired", "tint"],
          "tint_max": 50, "ev_bounds": [-3, 3], "temp_bounds_k": [2500, 20000]}
```

`mode` is `"target"` when left out, and then `target_lab` (`[L*, a*, b*]`) is required. In neutral mode `target_L` defaults to `target_lab[0]` when there is one, else 64; `neutral_ab` to `[0, 0]`; `neutral` to the values above (the defaults of `rpstills.skin.neutral_mask`). `free` may leave an axis out: it then stays at its start, and its Lab component is not asked for (with `["ev", "mired"]`, a frame converges on L\* and b\* alone). `temp_bounds_k` is Kelvin only on the apple backend; on lightcraft it bounds `wb.temp` in LightCraft's own units. `tint_max` is per backend, in each backend's own units: `{"apple": 50, "lightcraft": 6}`, and the solve reads the active backend's bound. A backend the look leaves out takes that default, an unknown backend name fails the look, and a plain number (the older form, as in the neutral look) bounds every backend alike. Apple's 50 comes from the test frames below (Known limits), since `CIRAWFilter` documents no tint range. `calibration.json` records the bound the active backend ran under.

## The looks

- `stills-looks/portrait-neutral-v1.json`: neutral mode, cheek L\* 64, neutral a\* 0 and b\* 0, `tint_max` 50, `temp_bounds_k` 2500 to 20000, apple backend. Picked blind on one portrait against two fixed skin targets, the trial target below and the published skin-tone line (123 degrees on a Rec.709 vectorscope). Both fixed targets needed tints that turned a white shirt green-yellow; the per-person neutral read 113 degrees on that face. L\* 64 is a working default for exposure, not a taste target.
- `stills-looks/portrait-cheek-trial-v0.json`: target mode, L\* 64, a\* 15, b\* 14 at the cheek apples (hue 43 degrees, 117.7 degrees on a Rec.709 vectorscope), picked by eye on one overcast session, `tint_max` apple 50 and lightcraft 6. Superseded for delivery by the neutral look; kept for matching a set to one hero frame.

## What it does not do

- No look beyond exposure and white balance on the apple backend: contrast, curves and colour come later (`rpstills finish`, or a LightCraft look on the opt-in lightcraft backend).
- In target mode, no per-person judgement: every frame is pushed onto the same target, so someone with naturally ruddy or olive skin is pushed toward it like everyone else. Neutral mode exists for that reason.
- No check that a `--wb-from` frame was shot in the same light, or with the same look: pair frames by capture time and setting.
- No orientation handling beyond what the engine applies. Vision runs on the developed render, so the face and the sample always agree with the file.
- It does not touch the original RAW files or write sidecars.

## Known limits

- A pale patterned or pastel fabric is told from a neutral only by the colour spread inside the mask. On the test set two frames in a floral blouse, which an earlier mask took as neutral (converging at tint -23 to -24 in LightCraft units, against -1.5 to -1.8 for white shirts in the same light), read a spread of 12.4 to 13.7 on the as-shot render, against 6.2 to 6.7 for two white shirts; they now fail with no neutral. The limit of 9 sits between them, but four frames of one subject set no threshold: a white shirt in mixed light may pass it and fail too. Use `--wb-from` or `--neutral-box`, and review the set.
- A wall or backdrop that touches the garment joins it. A neutral one leaves the median neutral; a tinted one spreads the colours past the limit and the frame fails. A tie, or an open collar of skin, filling the band under the chin down to the image bottom leaves nothing to keep, and the frame fails.
- The mask is taken at the as-shot white balance and exposure. A garment far from neutral at as shot (chroma over 25, or a warm cast near the skin's own hue with at least half its chroma) is missed, and the frame fails rather than guessing. A grey card must read L\* 40 on that render: an 18% card reads 49.5 with the cheek at L\* 64, but on the test set the as-shot render sat about 1 EV darker (cheek L\* 44 to 48), which puts the card near 35. Name it with `--neutral-box`.
- Tint is in each backend's own units. The trial look's tint bound was once a shared 6, set in LightCraft units, where tint 0 sits near neutral. On the test frames Apple's as-shot tint was -14.4, and Apple needed tint between -29 and beyond -40 to bring a\* down to 14 to 18, so under the shared 6 every frame pinned tint on the apple backend and a\* landed 4 to 10 units high. The look now bounds tint per backend (apple 50, lightcraft 6). Temperature differs too: on these RW2 files LightCraft's `wb.temp` is relative (6500 = as shot), so its solved values are not Kelvin and cannot be compared with Apple's. Under the shared 6 on the 7 test frames, Apple pinned tint on all 7; LightCraft converged on 1 and pinned tint on 6. In neutral mode on LightCraft, the white-shirt frames converged in 2 steps with tint near -1.7, and a blue-shirt frame given `--wb-from` converged in 1 step. On apple the same white shirts needed tint near -31, which `tint_max` 30 cut off on its bound; the neutral look's `tint_max` 50 leaves room. Bounds and solved values do not transfer between backends.
- The sample is the cheek apples of the largest face, with the geometry checked on one subject (see `rpstills-audit.md`). A face Vision misses, or one without landmarks, fails the frame with the cheek sample; the box sample needs no landmarks but measures a different patch, so it needs its own target.
- The solve renders at 1500 px. On the test frames the full-size file measured within 0.45 of the solve render in each of L\*, a\* and b\* in target mode. On `--wb-from` frames of a subject in glasses LightCraft rendered the full-size file 1.0 and 1.8 L\* brighter than its 1500 px export at the same settings, and the second came out `not_converged`. The delivered file is measured anyway, and a miss shows as `not_converged`.
- Time: 3 to 6 seconds a frame for 6000 x 4000 RW2 files on an M4 Max, either backend: 6 to 9 renders each, the last at full size; about 3.5 seconds for a `--wb-from` frame.
- Measured on one camera (Panasonic RW2) and one session. Other cameras, mixed light and backlit faces are untested.
