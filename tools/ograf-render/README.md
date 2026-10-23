# ograf-render

Renders an [OGraf](https://ograf.ebu.io) template offline to a ProRes 4444 master with alpha, the way Resolve's non-realtime path drives it, so `overlay-check` can verify the result without Resolve open. Node 22 or newer, built-ins only, no packages. Needs Chrome (or Chromium) and `ffmpeg` on the machine.

```bash
node tools/ograf-render/ograf-render.mjs path/to/Thing.ograf.json --out out/thing
python3 production/resolve_workflow.py overlay-check out/thing/Thing.mov --fps 24000/1001
```

## What it does

1. Refuses a template unless its manifest sets `supportsNonRealTime` to `true`.
2. Serves the template folder on loopback and opens it in one headless Chrome (temporary profile, removed after). The page is sized with `Emulation.setDeviceMetricsOverride` and the background is transparent.
3. Calls `load` (`renderType: "non-realtime"`), `updateAction` with the schema defaults (merged with `--data`), then `setActionsSchedule` with Resolve's default schedule: `playAction` at 0, and an automatic `stopAction` placed from `actionDurations` so the exit ends on the last frame.
4. Per frame calls `goToTime` at the frame's timestamp (default 24000/1001 fps) and saves a straight-alpha PNG.
5. Wraps the PNGs with `ffmpeg` to ProRes 4444 (`prores_ks`, `yuva444p10le`). Colour (range tv, BT.709) is set with `setparams` in the filter chain, not with output flags.
6. Writes the files below, then revisits a spread of frames in reverse to check that a seek gives the same pixels again.

## Interface

| Flag | Meaning |
|---|---|
| `--out DIR` | Required. Created if missing. |
| `--fps N` or `A/B` | Default `24000/1001`. |
| `--size WxH` | Default the manifest's `renderRequirements` ideal, else 1920x1080. |
| `--duration S` | Default the larger of `v_bmd.duration` and play plus stop, else 10 s. |
| `--frames A-B` | Render only those frames. |
| `--order shuffled` | Visit frames in a fixed shuffled order. The output must equal a sequential run. |
| `--data JSON` or `@FILE` | Property values over the schema defaults. |
| `--keep-frames`, `--no-video` | Keep `OUT/frames`; stop before ffmpeg. |
| `--strict-fonts` | A missing font fails the run instead of warning. |
| `--lint` | Run Resolve's own `lint-ograf.py` when it is installed (`OGRAF_LINT` sets another path). It is never copied here. |
| `--chrome PATH` | Chrome binary; also the `CHROME` environment variable. |

Exit codes: `0` ok, `1` a check failed (determinism, `--strict-fonts`, linter errors), `2` refused or bad input, `3` Chrome or ffmpeg missing.

Output in `OUT/`:

- `NAME.mov`: the master.
- `boxes.json`: per frame, a box (`x, y, w, h, opacity`) for each element carrying `data-layer`, including inside open shadow roots.
- `report.json`: settings, per-frame sha256 of the PNGs, the determinism result, fonts and the linter result.

## Checks and limits

- Determinism: a template whose `goToTime` depends on history or on `Math.random()` gives different pixels on revisit and fails with exit 1.
- Fonts: each text element's first family is measured against `monospace` and `serif`; `document.fonts.check()` cannot be used because it is true for any system family. A family loaded through `@font-face` counts as present.
- `main` must be a JS module; an HTML entry is not supported.
- Shadow roots must be open for `data-layer` boxes.
- Frames are 8-bit PNG widened to 10-bit; ProRes 4444 reads back as 12-bit in recent ffprobe.

## Tests

```bash
node --test tools/ograf-render/test.mjs
```

The fixture template is written by the test. The tests skip with a message where Chrome or ffmpeg is missing; the `overlay-check` case also skips until that command and numpy are present.
