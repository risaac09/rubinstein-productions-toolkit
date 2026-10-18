# vertcut

Bare-minimum AI-assisted workflow for turning a long-form Say Why recording into
9:16 captioned pull-clips. Two commands, no build step, no service.

The judgment stays with you and Claude: which moments become clips is decided
in the cuts worksheet, the same artifact the pipeline already produces. vertcut
only does the two mechanical parts — hearing the words, and cutting the frame.

## Delivery

vertcut's clips pass `deliver_check` for `linkedin_9x16` (checked 2026-10-16 on
a synthetic untagged 16:9 source: every asserted row passes). Three things make
that so:

- The file is named by the house rule, built by `rpresolve.deliver.name_for`
  from `--show`, `--episode` and `--guest`, the clip's place in the manifest
  (the index) and its title (the slug): `SW001_Guest_01_example-clip_9x16.mp4`.
  The three flags are required; `--draft-names` gives the old
  `<id>-<title-slug>.mp4` for a review copy that will not pass the name row.
- The video is tagged bt709 (primaries, transfer and matrix, limited range)
  whatever the source carried.
- The audio is AAC at the destination's bitrate, sample rate and channel count
  (320 kbps, 48 kHz, stereo for `linkedin_9x16`), read from
  `production/resolve-config.json`; `--destination` picks another key.

Write into a folder named for the destination (`-o .../linkedin_9x16`) and the
`folder` row passes too; elsewhere it is skipped.

The loudnorm target comes from the destination too (-16 LUFS for
`linkedin_9x16`); a `--lufs` that differs from it fails the loudness row.
`--destination` refuses a key whose frame is not 1080x1920 or whose audio is not
AAC, since vertcut writes only that. One limit remains: vertcut files still
skip the final-approval gate and the delivery manifest, which belong to the
`reframe_plan`, `cut` and `queue_render` route in [`production/resolve-template-spec.md`](../../production/resolve-template-spec.md).

## Requirements

- `ffmpeg` / `ffprobe` (Homebrew). Note its default build has **no libass**, which is
  why captions are Pillow-rendered PNG overlays rather than a `subtitles` filter.
  `brew install ffmpeg-full` (keg-only, so it does not replace the default) adds
  libass and libvidstab if you want the filter or stabilisation; the PNG path stays
  the default here because it needs nothing beyond Pillow.
- `mlx_whisper` for the transcribe step, in the same venv: `~/.venvs/rp-tools/bin/python -m pip install -c requirements.txt mlx-whisper==0.4.3` (or set `VERTCUT_MLX_WHISPER`).
- Pillow on the Python that runs it: the `~/.venvs/rp-tools` venv by default (see `requirements.txt`), or set `VERTCUT_PYTHON`.

## 1. Transcribe

```
tools/vertcut/vertcut transcribe "/Volumes/DRIVE/Episode Folder/master.mov"
```

Extracts 16k mono audio, runs `whisper-large-v3-turbo` locally with word
timestamps, writes `<name>.json` beside the source (`-o`/`--out DIR` writes it to another folder; `--model` picks another mlx-whisper model, default `mlx-community/whisper-large-v3-turbo`). Roughly real-time / 3 on an
M2 Pro (measured then; newer Apple silicon is faster). Nothing leaves the machine.

The DaVinci ASR export is fine for *finding* moments, but its timings are too
coarse to drive captions. This JSON is what the caption burner reads.

## 2. Write a cuts manifest

Tab-separated, one row per clip. Comments and blank lines are ignored.

```
# id	in	out	title
1	00:00:10	00:00:40	first example clip
2	00:01:05	00:01:50	second example clip
```

In/out accept `SS.mmm`, `MM:SS`, `HH:MM:SS.mmm`, or DaVinci `HH:MM:SS:FF`
(pass `--fps` if the source frame rate is not the timeline frame rate).

`--offset` shifts every row by N seconds, for when the manifest's timecodes come
from a timeline whose zero is not the media file's zero — a Resolve timeline
starting at 01:00:00:00, for instance. The sign is whichever direction lands the
clip: render one row with `--only`, watch it, and flip to `--offset -3600` if it
came out an hour early. Do not assume; check one clip first.

## 3. Cut

```
tools/vertcut/vertcut cut SOURCE.mov clips.tsv \
  --words SOURCE.json -o out/ --show SW --episode 1 --guest Guest
```

For each row: trims, reframes to 1080x1920, burns word-grouped captions,
normalizes audio to -16 LUFS (the level `linkedin_9x16` expects; `--lufs -14` for
short-form feeds), and writes an `.srt` with the same stem for review copies
where you would rather not burn in. For a burn-in destination such as
`linkedin_9x16` the `.srt` goes to `review-srt/` inside the output folder,
because `deliver_check` fails a burn-in file with a caption file beside it.

### Options worth knowing

| flag | default | what it does |
|---|---|---|
| `--frame crop\|fit` | `crop` | `crop` fills 9:16 from a 16:9 subject; `fit` letterboxes the full frame over a blurred copy. Use `fit` for two-shots. |
| `--x -1..1` | `0` | Shifts the crop window. `-0.4` when the speaker sits left of centre. |
| `--show` / `--episode` / `--guest` | required | Parts of the house file name. The index and slug come from the manifest row. |
| `--destination` | `linkedin_9x16` | Key in `resolve-config.json` that sets the aspect in the name and the audio format. |
| `--draft-names` | off | Old `<id>-<title-slug>.mp4` names, for review copies. They fail the name row. |
| `--no-captions` | off | Reframe only. |
| `--max-words` / `--max-chars` | `3` / `32` | Caption density. A cue never runs past two lines of `--line-chars`. |
| `--line-chars` | `16` | Characters per caption line, two lines at most (the portrait standard). A single word longer than this stops the run. |
| `--caption-style FILE` | none | JSON with `text_color`, `bar_color` and `font`. Without the flag, `tools/vertcut/captions.local.json` is read if it exists. |
| `--text-color` / `--bar-color` | `#FFFFFF` / `#000000` | `#RRGGBB`, over the style file. |
| `--bar-opacity` | `0.88` | Opacity of the bar behind each cue. |
| `--lufs` / `--tp` / `--lra` | `-16` / `-1.5` / `11` | Loudness target, true-peak ceiling and range. `-14` suits YouTube and short-form feeds. |
| `--crf` / `--preset` | `20` / `veryfast` | x264 quality and speed. `--preset slow` is smaller at the same quality. |
| `--font FILE` | Arial Bold | Caption font file, over the style file. |
| `--gap` | `0.6` | Seconds of silence between words that breaks a cue. |
| `--font-size` / `--caption-y` | `66` / `1680` | Caption size, and the y of the bar's bottom edge (lower third). |
| `--only 1,2` | all | Render a subset while dialling in the framing. |
| `--dry-run` | off | Print what would be rendered. |

### Caption style

Captions follow a plain standard: text on a
partial-opacity bar, no outline, 16 characters a line, two lines at most, bottom
edge fixed so every cue sits in the same place. Brand colours and typeface are
yours to supply. Put them in
`tools/vertcut/captions.local.json` (git-ignored) or pass `--caption-style`:

```json
{"text_color": "#RRGGBB", "bar_color": "#RRGGBB", "font": "/path/to/face.ttf"}
```

Use your own brand's text colour, bar colour and caption face. With no file the tool uses plain white on black and
Arial Bold.

### Dialling in the framing

Render one clip with `--only`, look at it, adjust `--x`, repeat. A 60-second
clip takes about 20 seconds. `--frame fit` never crops anyone out of frame, so
it is the safe default when there are two people on the timeline.

## What this deliberately does not do

No title cards, no lower thirds, no auto-selection of clips, no speaker
tracking, and no internal cuts: one row is one continuous span. A clip that
needs its middle removed is two rows, or a trip to Resolve. Those are the parts where the constraint is the product: choosing the
moment is the editorial act, and the series' clips play clean by design.
