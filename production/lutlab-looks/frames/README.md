# Look book reference frames

Six frames, one per look, each the same synthetic chart run through that look. No photograph, no client footage. The chart is computed, so anyone can rebuild it.

The chart (`../make_chart.py`) holds 24 colour patches (skin-tone-like, foliage, sky, primaries, a grey row), a 21-step grey ramp one third of a stop apart around 18 percent grey, a smooth ramp, and a hue sweep at full and half saturation. It is encoded as V-Log / V-Gamut, the S5II input the looks expect. Patch values are our own approximations, not a measured chart.

Rebuild, from `production/`, with outputs outside the repository (lutlab refuses to write inside one). The look bake needs the local ARRI base described in `lutlab/README.md` ("The ARRI base, without buying anything"); the base stays local.

```bash
W=$(mktemp -d)
~/.venvs/rp-tools/bin/python lutlab-looks/make_chart.py "$W/chart.png"
~/.venvs/rp-tools/bin/python -m lutlab looks ~/"DaVinci Resolve/lutlab-base/arri-logc3-709/params.json" --camera s5ii --out "$W/looks"
~/.venvs/rp-tools/bin/python -m lutlab apply "$W/looks/RP Neutral [S5II].cube" "$W/looks/RP Bleach [S5II].cube" \
  "$W/looks/RP Ice Blue [S5II].cube" "$W/looks/RP Print Teal [S5II].cube" "$W/looks/RP Soft Print [S5II].cube" \
  "$W/looks/RP Tungsten [S5II].cube" --frame "$W/chart.png" --width 960 --out "$W/apply"
```

Each `apply` PNG is saved as a JPEG (quality 88, no chroma subsampling) under the look's file name: `neutral.jpg`, `bleach.jpg`, `ice-blue.jpg`, `print-teal.jpg`, `soft-print.jpg`, `tungsten.jpg`. The chart's pixel values are the same on every run (the PNG bytes can differ with the OpenCV version); the look pixels depend on the base LUT Resolve ships, so a different Resolve version can shift them slightly.

The one-third-stop grey step at about 9 percent linear lands on the chart's own background and reads as a gap in the ramp. That is the chart, not a defect in a look.

The saturated ends of the hue sweep sit outside Rec.709 and are clipped when encoded to V-Log, so the first and last few pixels of the sweep are flat.
