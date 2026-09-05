# vertcut

Bare-minimum AI-assisted workflow for turning a long-form Say Why recording into
9:16 captioned pull-clips. Two commands, no build step, no service.

The judgment stays with Isaac and Claude: which moments become clips is decided
in the cuts worksheet, the same artifact the pipeline already produces. vertcut
only does the two mechanical parts — hearing the words, and cutting the frame.

## Requirements

- `ffmpeg` / `ffprobe` (Homebrew). Note its build has **no libass**, which is why
  captions are Pillow-rendered PNG overlays rather than a `subtitles` filter.
- `mlx_whisper` (`pip3 install --user mlx-whisper`) for the transcribe step.
- Pillow on the Python that runs it (system `python3` already has it).

## 1. Transcribe

```
tools/vertcut/vertcut transcribe "/Volumes/Samsung 2TB/Vesta - Say Why/master.mov"
```

Extracts 16k mono audio, runs `whisper-large-v3-turbo` locally with word
timestamps, writes `<name>.json` beside the source. Roughly real-time / 3 on the
M2 Pro. Nothing leaves the machine.

The DaVinci ASR export is fine for *finding* moments, but its timings are too
coarse to drive captions. This JSON is what the caption burner reads.

## 2. Write a cuts manifest

Tab-separated, one row per clip. Comments and blank lines are ignored.

```
# id	in	out	title
2	00:35:18	00:36:17	culture is not valuable
4	00:41:59	00:43:18	my own rigor comes from reality
```

In/out accept `SS.mmm`, `MM:SS`, `HH:MM:SS.mmm`, or DaVinci `HH:MM:SS:FF`
(pass `--fps` if the source frame rate is not the timeline frame rate). If the
Resolve timeline starts at 01:00:00:00, pass `--offset 3600` rather than
rewriting every row.

## 3. Cut

```
tools/vertcut/vertcut cut SOURCE.mov clips.tsv \
  --words SOURCE.json -o out/
```

For each row: trims, reframes to 1080x1920, burns word-grouped captions,
normalizes audio to -16 LUFS, and writes an `.srt` sidecar next to the `.mp4`
(LinkedIn and YouTube both accept the sidecar if you would rather not burn in).

### Options worth knowing

| flag | default | what it does |
|---|---|---|
| `--frame crop\|fit` | `crop` | `crop` fills 9:16 from a 16:9 subject; `fit` letterboxes the full frame over a blurred copy. Use `fit` for two-shots. |
| `--x -1..1` | `0` | Shifts the crop window. `-0.4` when the speaker sits left of centre. |
| `--no-captions` | off | Reframe only. |
| `--max-words` / `--max-chars` | `3` / `26` | Caption density. |
| `--font-size` / `--caption-y` | `66` / `1400` | Caption size and baseline. |
| `--only 2,4` | all | Render a subset while dialling in the framing. |
| `--dry-run` | off | Print what would be rendered. |

### Dialling in the framing

Render one clip with `--only`, look at it, adjust `--x`, repeat. A 60-second
clip takes about 20 seconds. `--frame fit` never crops anyone out of frame, so
it is the safe default when there are two people on the timeline.

## What this deliberately does not do

No title cards, no lower thirds, no auto-selection of clips, no speaker
tracking, and no internal cuts: one row is one continuous span. A clip that
needs its middle removed is two rows, or a trip to Resolve. Those are the parts where the constraint is the product: choosing the
moment is the editorial act, and the series' clips play clean by design.
