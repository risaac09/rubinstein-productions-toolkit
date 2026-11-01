# vertcut

Bare-minimum AI-assisted workflow for turning a long-form Say Why recording into
9:16 captioned pull-clips. Two commands, no build step, no service.

The judgment stays with you and Claude: which moments become clips is decided
in the cuts worksheet, the same artifact the pipeline already produces. vertcut
only does the two mechanical parts — hearing the words, and cutting the frame.

## Delivery

vertcut's clips pass `deliver_check` for `linkedin_9x16` (checked 2026-10-16 on
a synthetic untagged 16:9 source: every asserted row passes) and for `social_9x16`
(`--destination social_9x16 --lufs -14`). `production/tests/test_vertcut_e2e.py`
checks the second end to end on a synthetic 29.97 source, through `deliver_check`
and `qc`, after planting three faults it must see: a -16 LUFS render fails
loudness, a 23.976 source warns `fps_floor` and `social_fps`, and a log without
the matching line warns `approval`. Three things make that so:

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
AAC, since vertcut writes only that. The output frame rate keeps the source's NTSC
rate (30000/1001, not 29.97), capped at 30. One limit remains: vertcut files do
not write the final-approval line or the delivery manifest; the `approval` row
passes only when `deliver_check --review-log` finds an approve or go line in the
review log (`resolve_workflow.py review-log --review-copy`) naming the clip, and
otherwise WARNs. The gate and the manifest belong to the
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

### What the transcript withholds

Whisper writes singing down as words (lyrics, if the song is one) and wind or
silence as sentences. Before anything reads the transcript, `lib/segkind.py`
decides what each segment is from numbers, never from the words: Whisper's own
confidence and compression figures, words per second, repetition, and, while
the audio is still in the temp folder, pitch (held notes, a semitone grid, a
steady beat). Three files come out:

- `<name>.json`, the safe transcript every tool reads. A segment screened as
  sung, music or a hallucination keeps its timestamps and gets a `[singing]`,
  `[music]` or `[inaudible]` label in place of its text; its words are gone.
  A `safety` block records the classifier version and the counts.
- `<name>.kinds.json`, kinds and time ranges only, no text. Other tools read
  it to warn about sung spans without opening the transcript.
- `<name>.raw.json`, the unredacted Whisper output, mode 600. Nothing reads it
  by default and `vertcut cut --words` refuses it by name. `--discard-raw`
  deletes it; keep it when a wrong call may need undoing.

`vertcut kinds <name>.json` prints what each span is and when, nothing else;
`--segments` adds the scores. The classifier is tuned for recall and is honest
about its limits: hallucinations are caught reliably from Whisper's numbers,
singing is not reliably separable from speech by simple features (group
singing outdoors least of all), so a segment next to a sung one is withheld
too, and an uncertain segment that leans sung is withheld. A `sung.tsv`
(`clip<TAB>start<TAB>end<TAB>kind`, kind one of `speech`, `sung`, `music`,
`hallucination`) is authoritative in either direction: `vertcut kinds
<name>.json --apply-overrides sung.tsv` rewrites the safe JSON and the sidecar
from the raw file, and `vertcut transcribe --overrides sung.tsv` applies it on
the first pass. A transcript made elsewhere (rpresolve's `transcribe`, or a
raw Whisper JSON) is screened in memory when read, from the Whisper numbers
and text counts alone, and the tools say that singing was not screened.

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

To take a worksheet down the Resolve route instead (`reframe_plan`, `cut`, `create_captions`, which is where client-visible work belongs), `tools/vertcut/vertcut manifest SOURCE.mov clips.tsv --words SOURCE.json -o manifest.json` writes it as a version 2 cut manifest (`--offset` and `--fps` as above; the output must sit outside any repository). Both commands read `HH:MM:SS:FF` rows the NTSC-correct way (frames count at the nominal rate, then divide by the real one).

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

### Crops from a reframe plan

`--plan` reads the static 9x16 crop that `reframe-plan` judged against the speaker's face. A row takes the crop of the one plan span whose in and out hold the row's media time (in and out plus `--offset`, within a frame). Time decides, not the row id: ids are free text, and the span is what says which footage the crop was checked on. If two spans hold a row, the plan clip named for the row's id wins. It stops, and builds nothing, when the plan was made for another source file or picture size, has no 9x16 crops, no span holds the row (or the row straddles two), the span's crop is not a pass or fallback that fills the frame (manual, no face, bars), is squeezed, moves, or reaches outside the picture. A crop is never invented. `--plan` does not go with `--x` or `--frame`.

### Options worth knowing

| flag | default | what it does |
|---|---|---|
| `--frame crop\|fit` | `crop` | `crop` fills 9:16 from a 16:9 subject; `fit` letterboxes the full frame over a blurred copy. Use `fit` for two-shots. |
| `--x -1..1` | `0` | Shifts the crop window. `-0.4` when the speaker sits left of centre. |
| `--plan PLAN.json` | none | Takes each row's crop from a `reframe_plan` manifest copy (the file `reframe-plan --out` writes) in place of `--x`. See below. |
| `--show` / `--episode` / `--guest` | required | Parts of the house file name. The index and slug come from the manifest row. |
| `--destination` | `linkedin_9x16` | Key in `resolve-config.json` that sets the aspect in the name and the audio format. |
| `--draft-names` | off | Old `<id>-<title-slug>.mp4` names, for review copies. They fail the name row. |
| `--no-captions` | off | Reframe only. |
| `--allow-music` | off | Render a clip that overlaps a span the transcript withheld as singing or music. Without it the run stops and names the clips. The words stay withheld either way. |
| `--labels music\|all\|none` | `music` | Which withheld spans get a bracketed cue: `music` draws `[singing]` and `[music]`, `all` adds `[inaudible]`, `none` draws no label. |
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
| `--no-hook` | off | Draw no hook, whatever the worksheet or `--hook-text` says. |
| `--dry-run` | off | Print what would be rendered. |

### Hook text and the safe zone

`--hook-text "FIRST LINE|SECOND LINE" --hook-source REF` (or a fifth worksheet column, which needs a title) draws a frame-0 card at the top safezone edge and holds it for max(0.8 s, 0.3 s per word). The text is yours, used as given; the tool never writes or suggests copy. `rpresolve.hooklint` checks it first (nine words, two lines of 16 characters, no em-dash, no emoji), and a FAIL exits 2. Captions wait until the card leaves, `review-srt/<stem>.hook.json` records text, source, box and span, and on macOS a face on frame 0 under the card stops the run (elsewhere that check is skipped, with a note).

The caption bar's bottom edge defaults to y=1680, which sits inside the bottom UI band (the lowest 35 percent of a 9:16 frame). A run with captions prints a `WARN` saying so and leaves the bar where it is; `--caption-y` moves it.

How many cues a clip has does not change the ffmpeg command: the cues go in as at most four image streams (grouped by the odd or even position of their bar, which keeps the chroma the same as a cue overlaid alone), each timed to the source's frames. On the current ffmpeg, luma is bit for bit what one overlay per cue gave and chroma differs by rounding in a few pixels at a bar's rounded corners; on an older ffmpeg (6.1 was measured) a cue's first or last frame can land one frame either way. Where one cue ends on the frame the next begins, the new cue alone is drawn; before, both were, stacked for that one frame. A variable-frame-rate source can drift off the frame grid the streams are timed to.

A batch that needs the review gate (over 30 minutes, or replacing a file) must name the same source file and cuts file the gate-packet was built from. `--offset` is not recorded in gate.json, so the gate cannot tell two offsets apart; check it yourself.

### The review gate

A batch whose estimate is over 30 minutes, or one that would replace an existing output, stops until a person has read a gate-packet and answered go. The estimate is the clips' total length times `RATIO`, a third (transcription runs at about a third of real time on Apple silicon, and a veryfast encode is assumed to cost no more). Build the packet with `resolve_workflow.py gate-packet`, log `go` with `review-log --step gate`, then pass `--reviewed GATE_DIR/gate.json --review-log PROJECT_DIR/review/events.jsonl`. Both files must sit outside any repository, and the gate must match this cuts file and cover this batch.

### Review variants

`--review --only ID --variant SPEC --variant SPEC -o PACKET_DIR` renders 2 to 4 variants of one worksheet row and hands them to `reel-ab`: a lettered, loudness-matched 720p packet in `PACKET_DIR` and a sealed key in `PACKET_DIR.key`. A SPEC names only what differs, from `x=N` (crop x), `style=FILE` (a caption style file) and `hook=on|off`, comma-separated; nothing is invented, and two identical specs are refused. Everything else comes from the rest of the command line. The run prints the `review-log` line that records the answer.

### Release chain

`--ledger-row PATH` adds one release-ledger row stub per clip to PATH (JSON lines, outside any repository, never replaced) through `delivercheck.ledger_row`, so the row carries the file's name, hash, destination and check status; the fields a person fills after publishing stay empty. When the destination has a loudness target (`social_9x16` is -14 LUFS) and `--lufs` is not given, that target is used and stderr says so. The output frame rate follows the source up to 30, and a 59.94 source is written at 30000/1001.

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
