# Best-take proposals

`python3 -m rpresolve.besttake` reads the takes of one answer and proposes an
order from numbers. It writes nothing into the takes, never calls one "best",
and the editor picks. Run from `production/`.

```
python3 -m rpresolve.besttake FOLDER_OR_MANIFEST [--words-dir D] [--no-faces]
    [--formula FILE.json] [--json OUT.json] [--md OUT.md]
```

Outputs go outside any git tree. The table also prints to the terminal. The
exit code is 1 when no take could be read.

## Input

- A folder of video or audio takes, one level deep. A transcript for `A.mov`
  is `A.json` in the folder (or in `--words-dir`): Whisper JSON with word
  timestamps, the shape pacing and wordsnap read.
- Or a manifest, `{"takes": [{"file": "A.mov", "words": "A.json"}]}`, with
  paths relative to the manifest and `words` optional.

A file that cannot be read is listed under "skipped" with the reason; the rest
still rank.

## What is measured

Every number comes from a module that already measures it.

| Number | From | Notes |
|---|---|---|
| `speech_ratio`, `pauses`, `pauses_per_min`, `longest_pause_s` | `dxmetrics.frame_power`, `pacing.speech_runs` | speech is a 0.1 s window 12 dB over the take's own noise floor; a pause is a gap of `pacing.PAUSE_MIN_S` (0.25 s) or more |
| `wpm`, `speaking_wpm`, `words` | `pacing.report` | only with a transcript |
| `integrated_lufs`, `wada_snr_db` | `dxmetrics.measure` | WADA compares only with itself, take against take |
| `noise_floor_dbfs` | `dxmetrics.frame_power` | mean power of the quietest 10% of windows |
| `clipped_share` | `dxmetrics.frame_power` | windows whose peak reaches full scale, dropouts excluded |
| `cuts`, `cuts_per_min`, `longest_shot_share` | `shots.build` | video only: in-camera cuts as instability |
| `face_share` | `vision.face_boxes` | video only, macOS Vision, a frame every 2 s up to 30; empty (and noted) where Vision cannot run, or with `--no-faces`. Missing metrics are left out of the score, not zeroed |

## The formula

`RANKING` in `rpresolve/besttake.py` is the whole formula. A metric scores 0 to
1 on a straight ramp, 1 at `good` and 0 at `bad`, clipped. A band (`good` and
`bad` both `[low, high]`) scores 1 inside `good` and ramps to 0 at either `bad`
end. A take's score is the weighted mean of the scores it has. Defaults:

| Metric | Good | Bad | Weight |
|---|---|---|---|
| `speech_ratio` | 0.75 | 0.35 | 2 |
| `pauses_per_min` | 4 | 20 | 1 |
| `wpm` | 130 to 170 | 90 / 220 | 1 |
| `noise_floor_dbfs` | -60 | -40 | 2 |
| `wada_snr_db` | 30 | 10 | 1 |
| `integrated_lufs` | -24 to -16 | -40 / -8 | 0.5 |
| `clipped_share` | 0 | 0.02 | 3 |
| `cuts_per_min` | 0 | 6 | 1 |
| `face_share` | 0.95 | 0.5 | 1 |

Change a row in the file, or pass `--formula FILE.json` with only the fields to
change, for example `{"clipped_share": {"weight": 0}}` to ignore clipping. The
formula that ran is printed in the JSON beside the order. These starting
numbers are a judgement, not a measurement; a slow, quiet read can be the right
take, so set them to the piece.

## Reading the table

One row per take: rank, score, `n` (how many metrics the score is a mean of; a take with a transcript or video has more, so compare scores with that in mind), the numbers, and a reason that only quotes them
(the two metrics that scored highest and the two that scored lowest, with
values). It does not say what a number means for the shot. A take with under 20 dB between its loud and quiet windows has no room-tone stretch; its speech share and noise floor carry a note. Each take is decoded a few times, and `dxmetrics` holds a take's audio in memory, so run very long takes one at a time. Listen to the top
few and to any take whose lowest metric is one you would forgive.
