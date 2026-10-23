# ipad-probe

A probe and four recipes for an iPad with no Mac nearby. They are POSIX `sh`
scripts written for [a-Shell](https://github.com/holzschu/a-Shell), whose
shell is `dash` and whose ffmpeg may be a native build without GPL parts or a
WebAssembly one with libx264 (UNVERIFIED; the probe's `build gpl` and
`encoder` rows answer it). They run the same on macOS and Linux, which is
where they are tested.

Why they exist, and what an iPad can and cannot do for each stage of a
shoot, is in the [iPad lane](../../docs/ipad-lane.md). Nothing here has been
run on an iPad yet: the first run of `probe.sh` on the device is the test.

## Getting the folder onto the iPad

Keep it off iCloud Drive. AirDrop the `ipad-probe` folder into
Files > On My iPad > a-Shell, or download the repository zip from GitHub in
Safari into On My iPad and unzip it in Files. In a-Shell that folder is
`~/Documents/ipad-probe`.

## probe.sh

```
cd ~/Documents/ipad-probe
sh probe.sh --self-test        # the probe's own parsers; needs no ffmpeg
sh probe.sh > probe.md         # the table; open probe.md in Files and share it to Claude
sh probe.sh CLIP > probe.md    # also reads CLIP and times 5 s of its decode
```

One Markdown table, a row per check:

| kind | what it says |
|---|---|
| `version`, `build` | ffmpeg, ffprobe and python3 versions; whether ffmpeg was built with gpl, videotoolbox, libzimg, libass, libfreetype; whether `-hwaccels` lists videotoolbox (the recipes then decode in hardware) |
| `filter` | present or absent in `ffmpeg -filters`: lut3d, lut1d, haldclut, zscale, colorspace, tonemap, ebur128, loudnorm, afftdn, adeclick, sidechaincompress, acrossfade, silencedetect, scdet, signalstats, waveform, vectorscope, subtitles, drawtext |
| `encoder` | present or absent in `ffmpeg -encoders`: h264_videotoolbox, hevc_videotoolbox, libx264, libx265, libkvazaar, libsvtav1, prores_ks, aac |
| `smoke` | 2 s lavfi jobs, pass, fail or skipped: lavfi sources, an identity `.cube` written with `printf` through lut3d, loudnorm's JSON and its output sample rate, ebur128, scdet on a made cut, ffprobe on a made file, a VideoToolbox H.264 encode at `-b:v` |
| `python` | numpy, scipy, PIL and cv2 importable; whether python's `subprocess` can run ffprobe, and whether `os.path.isfile` passes on it (the toolkit's Python checks need both) |
| `tool` | the shell commands the recipes use, each run once on a known input, and the three SHA-256 routes (sha256sum, shasum, python3 hashlib) |
| `clip` | with CLIP: codec, size, pixel format, colour tags, audio stream count, decode speed in software and with VideoToolbox |

A summary line and the ffmpeg configuration follow the table. Absent rows are
findings, not errors: the probe exits 0 once the table is printed.

## Recipes

| recipe | does | needs |
|---|---|---|
| `recipes/verify-copy.sh SRC DEST [DEST ...]` | copies a card to each DEST, writes `DEST.SHA256SUMS` beside it, re-hashes every copy, and counts verified copies. Refuses a non-empty DEST, a DEST whose parent is missing, a DEST inside SRC, the same DEST twice, compared by physical path so no spelling or symlink gets past; a DEST that turns out to be an earlier one at copy time is skipped and not counted. | a SHA-256 route (`tool` rows) |
| `recipes/proxy.sh IN OUT.mp4 [BITRATE [LONG_EDGE]]` | H.264 proxy, long edge 1280, never upscaled, 6M by default: h264_videotoolbox, else libx264, else mpeg4; decodes with VideoToolbox when listed | an H.264 or mpeg4 encoder (`encoder` rows) |
| `recipes/loudness.sh IN OUT [I [TP [LRA]]]` | two-pass loudnorm to -16 LUFS / -1.5 dBTP / LRA 11 by default, asking for linear mode, 48 kHz out; with LRA left out the target is lifted to the measured LRA + 1, as delivercheck does; prints normalization_type and why when it is dynamic, then measures the result and notes a true peak over TP | loudnorm (`filter` and `smoke` rows) |
| `recipes/sheet.sh IN OUT.jpg [COLS [ROWS]]` | a 6 x 4 contact sheet from select, scale and tile (no drawtext), with the time of each picked frame printed to paste beside it | ffprobe for the duration |

None of them overwrites a file, writes to its input, or writes inside a git
work tree (an OUT or DEST in one is refused, however it is spelled). `FFMPEG`, `FFPROBE`,
`PYTHON` and (verify-copy only) `CP` name the commands each one calls.
`probe.sh` and `verify-copy.sh` keep scratch files in a new folder under
`TMPDIR` and remove it at exit.

## Tests

`production/tests/test_ipad_probe.py` runs every script under `sh` and, where
it is installed, `dash`, on media made with ffmpeg lavfi. A fake ffmpeg proves
the probe's absent rows come from ffmpeg's own lists, a corrupting `cp` proves
verify-copy fails a bad copy, and a PATH without sha256sum or shasum proves
the python3 fallback. CI's shellcheck step checks the scripts at warning level.

```
/usr/bin/python3 -m unittest production.tests.test_ipad_probe -v
```

## Limits

- Not run on an iPad. Whether a-Shell's ffmpeg is the native build (with
  VideoToolbox) or a WebAssembly one decides speed, and the probe's encoder
  and smoke rows answer it.
- a-Shell's Shortcuts action times out on long jobs (an open a-Shell issue):
  run the recipes in the app.
- A path holding a newline breaks verify-copy's listing; cameras never write
  one. "Different devices" is the person's call: the script can only count.
- The sheet takes the first frame of each cell's time slot. When slots are
  2 s or longer only keyframes are read, so a slot with no keyframe is
  skipped, later frames move up a cell and the cells left at the end are
  black; the printed times are those of the frames shown, and the black
  cells are listed as empty.
- loudness.sh's true peak is measured after encoding: an AAC OUT can land
  over TP, and the script says so rather than hiding it.
