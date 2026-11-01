# Media lane quickstart

**Status:** operational.

Copy-paste checklists for the six lanes, from a fresh shell. The first two are the ones to run yourself; the other four are shorter. What each tool does is in [media-lane.md](media-lane.md).

Every command in sections 1 to 5 was run in a clean shell (`env -i` with only `HOME` and `PATH`) against real media. The times and numbers under "A pass looks like" are from those runs on an M-series Mac. Section 6 is a hand checklist: a test checks its commands and flags against the tools' `--help`, and it has not been run end to end on real media.

## Before you start

- macOS with `ffmpeg` on `PATH` (Homebrew: `/opt/homebrew/bin`). Stills development needs macOS (Apple's RAW engine).
- Python: `~/.venvs/rp-tools/bin/python` for `rpstills`, `lutlab` and `vertcut`. The `rpresolve` modules (sound, edit) need only `numpy`; the system `python3` with numpy works, so does the same venv.
- Every command starts from the toolkit's `production/` folder.
- **Outputs go outside any git tree.** The stills, `lutlab`, `interchange`, `roomtone` and `mixbuild` commands refuse a folder inside a repository, so use `~/media-out/...`, not a path in a clone.
- Replace the paths in the `export` lines; leave everything else as written.

```bash
cd /path/to/rubinstein-productions-toolkit/production
```

## 1. Stills: one RAW, developed then finished

Change one path (`RAW`) and run.

```bash
export PY=~/.venvs/rp-tools/bin/python
export RAW=~/footage/clip.RW2           # one RAW file with a face and a white or grey shirt below it
export OUT=~/media-out/stills-job1      # outside any git tree

$PY -m rpstills develop "$OUT" --look stills-looks/portrait-neutral-v1.json --raw "$RAW" \
  && $PY -m rpstills finish "$OUT" --crop headshot_4x5_eye36
```

Result: `$OUT/develop/<name>.tif` (16-bit, about 200 MB), `$OUT/develop/calibration.json`, `$OUT/finish/headshot_4x5_eye36/<name>.jpg` and `$OUT/finish/finish.json`.

`develop` exits 1 when the frame did not converge, and the `&&` then stops before `finish`. That is on purpose: read the reason on stderr (see the table below), fix it, or run `finish` on its own if you accept the frame as it landed.

**A pass looks like:**

- `develop` prints the frame as `converged` with `delta_e` under 1 (smoke runs: 0.21 to 0.32 in 2 to 3 steps), 5 to 8 s a frame on a 5776 px file.
- `calibration.json` row: `"status": "converged"`.
- `finish` reports 1 of 1 finished, about 2 s a frame plain.

Retouch and a darker background are opt-in and cost about 18 s a frame:

```bash
$PY -m rpstills finish "$OUT" --retouch all --background on --crop headshot_square_tight
```

A second `finish` overwrites `finish/finish.json`; JPEGs from earlier crop profiles stay in their own folders.

## 2. Sound: a dialogue clip, mixed and checked

Dialogue (`dx.wav`, mono or stereo) plus a music bed, ducked under speech, with the stems summing back to the mix.

### Once: the denoise model

The `boom` and `lav` presets denoise with RNNoise, which needs a model file (the toolkit ships none). Any `.rnnn` model works; the smoke runs used the standard one:

```bash
export RPRESOLVE_RNNOISE_MODEL=~/.local/share/rp-models/rnnoise/std.rnnn
test -f "$RPRESOLVE_RNNOISE_MODEL" && echo model ok
```

### The job

```bash
export JOB=~/media-out/mix-job1          # outside any git tree
mkdir -p "$JOB/spec"
cp ~/footage/dx.wav ~/footage/music.wav "$JOB/spec/"

cat > "$JOB/spec/spec.yaml" <<'EOF'
sample_rate: 48000
channels: 2
stems:
  dialogue:
    input: dx.wav
    role: dx
    preset: boom
  music:
    input: music.wav
    role: mx
    preset: music_bed
mix:
  gains: {dialogue: 0, music: -14}
  duck: {key: dialogue, under: [music], depth: 12}
  chain:
    - speechnorm: {key: dialogue, i: -22}
EOF

python3 -m rpresolve.mixbuild render "$JOB/spec/spec.yaml" --out "$JOB/render" --report
```

### The target loudness, and why there is no limiter

`speechnorm` raises the mix until the speech alone reads `i` LUFS. With dialogue that is quiet against its own peaks, a `-16` target needs a gain that pushes the mix past 0 dBFS, and the 24-bit mix clips. A limiter would stop that, but a limiter is not linear: the stems then no longer sum to the mix and the sum-null check fails.

So the working spec asks for `-22` and has no limiter. If the target is out of reach, the run still writes the files and exits 1, and the reason now names the clipped mix (not "something on the mix bus is not linear"). Lower `i` by a few LU and render again. In the smoke clip (peak-to-loudness about 20 dB), `-16` clipped at +4.0 dBTP; `-22` landed at -2.0 dBTP.

Need `-16` for delivery? Drop `role` from the stems and put `limiter: {ceiling: -1.5}` after `speechnorm`. You then get a limited mix and no stem sum-null.

**A pass looks like** (exit 0), in `$JOB/render/render.json` and the console:

- stems sum-null residual far below the `-60` dBFS floor (smoke: -132.5 dB);
- mix true peak under about -1.5 dBTP (smoke: -2.0);
- speech loudness on target (smoke: speech -26.7 LUFS to a mix at -22.3 LUFS integrated, gain +4.7 dB);
- `reports/` per output with 0 clicks;
- about 12 s for a 97 s clip.

Files: `$JOB/render/mix.wav`, `stems/`, `export/DX.wav` and `MX.wav`, `render.json`.

### Ear check (numbers are not listening)

```bash
python3 -m rpresolve.mixbuild ab --out "$JOB/ab" "$JOB/spec/dx.wav" "$JOB/render/stems/dialogue.wav" --start 14 --seconds 20 --seed 1
```

It cuts the same 20 s from each file, matches their loudness, shuffles them to A and B, and seals the key in `$JOB/ab/sealed/key.json`. Listen to `A.wav` and `B.wav`, say which, then open the key. On a noisy outdoor clip the untreated file won once against RNNoise alone, so the preset is a starting chain, not a promise.

### Before a render: what Resolve says about the audio

With the timeline open in Resolve (Resolve Studio running), read its audio state and keep the JSON outside any git tree. After the operator renders, hand it to `deliver-check` so the queued job's audio is compared with the destination:

```bash
python3 resolve_workflow.py audio-state -o "$JOB/audio-state.json"
python3 resolve_workflow.py deliver-check "$JOB/final.mov" --dest client_master --audio-state "$JOB/audio-state.json"
```

`audio-state` lists the audio tracks, each item's `Audio*` properties (volume, Voice Isolation, Dialogue Leveler), each source's audio mapping, the Deliver page's format and render mode, and the audio fields of the queued jobs. It changes nothing and names what the API cannot read (below). The mixer, EQ and dynamics are not scriptable, and Resolve cannot read the Deliver page's sample rate or render range, so a mix that depends on them is checked on the rendered file, not here. The `queue_render` summary names those two settings when it sets them: reset them on the Deliver page if the page should show "Same as project" or In/Out Range again.

## 3. Colour

Camera log to a working space, a look on top, a check frame. Details: [lutlab-cst.md](lutlab-cst.md), [lutlab-balance.md](lutlab-balance.md), [lutlab/README.md](lutlab/README.md).

```bash
export PY=~/.venvs/rp-tools/bin/python
export C=~/media-out/colour-job1                      # outside any git tree
export SHOT=~/footage/shot1.MOV                       # a clip, or a folder of frames, in the camera's log

$PY -m lutlab cst --from vlog --to r709 --out "$C/cst"
$PY -m lutlab lutstats check "$C/cst/vlog-to-r709-65.cube" --from vlog --to r709
$PY -m lutlab balance "$SHOT" --camera gh7 --frames 6 --out "$C/balance"
```

`--camera` takes `gh7`, `s5ii` or another house camera; `--space vlog` works for any V-Log camera. `balance` finds the face on each sampled frame and the neutral (a white or grey garment or card below it). A chart or a frame with no face needs both boxes by hand: `--face X Y W H` and `--neutral-box X0 Y0 X1 Y1`, normalised 0..1 from the top left. Note the two differ: the face is origin and size, the neutral is two corners. `--matte vision` (macOS) restricts the neutral to the person, for a shot where a pale wall or someone else's shirt sits in the garment's row, and adds the skin sector to take into Colour Slice; see [lutlab-balance.md](lutlab-balance.md#person-matte).

Look at a cube on a frame (every cube gets a PNG and one contact sheet):

```bash
$PY -m lutlab apply "$C/cst/vlog-to-r709-65.cube" "$C/balance/shot1.cube" --frame ~/footage/frame.jpg --width 1000 --out "$C/apply"
```

Match one camera to another from a shared, framed-alike still of each (it refuses unpaired content):

```bash
$PY -m lutlab shot-match --ref ~/footage/camB-chart.png --src ~/footage/camA-chart.png --space vlog --out "$C/match"
```

**A pass looks like:**

- `cst` writes a 65-point cube (7.4 MB) in about 0.3 s; `lutstats check` for vlog to r709 at 65 points: natural dE2000 mean 0.15, p99 1.73, neutral max 0.71 (at `--size 33`, 970 KB, about 0.1 s: 0.515, 4.35, 0.92; `di` is 0.026 and 0.18 at 65, 0.07 and 0.42 at 33).
- `balance`: `converged`, mean `dE00 to set` near 0 (smoke chart clip: EV +2.3, 4321 K, skin L* 63.9, neutral a* b* within 0.2 of 0), 9 s, and `shot1.cc` plus `shot1.cube` beside `balance.md`.
- `shot-match`: held-out dE2000 median falls (smoke: 2.78 to 1.20), about 2 s.
- Resolve importing the `.cc` or `.cube` stays a hand step.

## 4. Edit

Transcript, word-snapped cut points, pacing, trim proposals, an interchange file. Details: [edit-assist.md](edit-assist.md), [interchange.md](interchange.md), [besttake.md](besttake.md).

Run on the clip's audio or video; the transcript needs `mlx-whisper` (Apple silicon) in the tool venv, and the first run on a model downloads it.

```bash
export PY=~/.venvs/rp-tools/bin/python
export SRC=~/footage/interview.mp4                    # a clip with speech
export E=~/media-out/edit-job1                        # outside any git tree
mkdir -p "$E"

# `copy` prints the folder name; point SRC at one clip in it
export SRC="$W/ingest/CAM_A-<id6>/clip.mov"
$PY resolve_workflow.py transcribe "$SRC" --backend mlx-whisper --out "$E/words.json" --srt "$E/words.srt"
$PY resolve_workflow.py wordsnap "$E/words.json" "$SRC" --out "$E/snapped.json"
$PY resolve_workflow.py pacing --words "$E/snapped.json" --captions "$E/words.srt"
$PY resolve_workflow.py captionlint "$E/words.srt"
$PY resolve_workflow.py trim-review "$SRC" --words "$E/snapped.json" --out "$E/trim.tsv"
```

**A pass looks like:**

- `transcribe`: about 5 s for 97 s of speech. A `leaked semaphore` warning at exit is noise.
- `wordsnap`: prints the words, the starts and ends it moved and the largest move (at most 400 ms by default).
- `pacing`: words per minute (smoke: 150 on a 97 s dialogue clip), pause ratio, caption reading speed.
- `captionlint`: exits 1 on the raw transcript SRT. That is the tool working: Whisper's segments are not caption-shaped, so expect lines over 42 characters.
- `trim-review`: a TSV of proposals (`silence`, `filler`, `repeat`); nothing is cut. Apply chosen rows to a cut manifest with `trim-apply MANIFEST TSV --out NEW.json --accept 1,3` ([edit-assist.md](edit-assist.md)).

A [version 2 manifest](manifest.md) with several sources goes through `cut` (one 16:9 `[auto]` timeline, each item from its own source's media-pool clip; pass `make_9x16` false), `interchange` (EDL, FCPXML, OTIO: one asset per source) and `pacing` (each span's own source words). Import every source into the project first; `cut` refuses when one is missing.

## 5. Review loop

Two candidates in, one lettered sheet out. One decision per message: see [media-review-loop.md](media-review-loop.md) for the rules and the answer log.

```bash
export PY=~/.venvs/rp-tools/bin/python
export R=~/media-out/review-job1                      # outside any git tree

$PY -m lutlab apply look-a.cube look-b.cube --frame ~/footage/frame.jpg --width 1000 --out "$R/cand"
$PY -m rpstills absheet "$R"/cand/frame*.png --out "$R/look-01" --seed 7 --title "Look 01"
```

(The candidate PNGs are named `<frame> - <cube>.png`, so keep the glob.) Send `$R/look-01/sheet.jpg` alone, ask for a letter, and read `$R/look-01.key/key.json` only after the answer. For sound the same job is `mixbuild ab` in the sound lane above.

**A pass looks like:** one PNG per cube, `sheet.jpg` at most 2000 px and 1 MiB (smoke: 421 KiB, 0.2 s), and `key.json` mapping the letters, written to the sibling folder `look-01.key`.

## 6. Offline reels: card to clip set

A card of talking footage to a set of captioned 9:16 clips, with a person's yes before the batch and before each clip leaves. Nothing here opens Resolve. Details: [transcribe.md](transcribe.md), [delivery-manifest.md](delivery-manifest.md), [media-review-loop.md](media-review-loop.md) and `tools/vertcut/README.md`. Names are placeholders; keep the work folder outside any git tree.

```bash
export PY=~/.venvs/rp-tools/bin/python
export W=~/work/sw999-test                          # outside any git tree
export CARD=/Volumes/CARD1                          # the mounted card
mkdir -p "$W/ingest" "$W/reel" "$W/out"
```

### Offload, then transcribe

```bash
./offload.sh id "$CARD"
./offload.sh copy "$CARD" "$W/ingest" --cam CAM_A
$PY resolve_workflow.py transcribe "$SRC" --backend mlx-whisper --out "$W/reel/words.json" --srt "$W/reel/words.srt"
```

`copy` hashes the card, copies, reads every file back from the destination and writes `CAM_A-<id6>.SHA256SUMS` beside the folder. It never writes to the card. Format the card only after `./offload.sh safe "$W/ingest/CAM_A-<id6>" <rclone-remote>` says so; the offsite copy is a hand step. `transcribe` needs `mlx-whisper` (section 4).

### Quote the spans, get a worksheet

Read the transcript and write, one row per reel, the words where it starts and ends: title, start phrase, end phrase, tab separated (optional fourth and fifth columns pick the nth occurrence). The tool only finds your quotes; it proposes nothing.

```bash
printf 'Test reel one\tfirst words of the clip\tlast words of the clip\n' > "$W/reel/phrases.tsv"
$PY resolve_workflow.py reel-spans "$W/reel/words.json" "$W/reel/phrases.tsv" -o "$W/reel/cuts.tsv"
```

Each row reports OK, WARN (under 5 s or over 90 s; `--min-s` and `--max-s` move those) or FAIL. A FAIL row is left out of `cuts.tsv` and the exit is 1: fix the quote and run again. For a hook, a fifth column of `cuts.tsv` takes the person's own words; never write one for them.

```bash
../tools/vertcut/vertcut manifest "$SRC" "$W/reel/cuts.tsv" --words "$W/reel/words.json" -o "$W/reel/manifest.json"
```

Result: a version 2 [manifest](manifest.md) for the Resolve route. The offline cut below reads `cuts.tsv` itself.

### The 30-minute gate

A batch estimated over 30 minutes, or one that would replace an existing output, stops until a person has seen a packet and said go. Build the packet from the plan, before any render, and send it as one message:

```bash
$PY resolve_workflow.py gate-packet --out "$W/gate" --project P-0001 --source "$SRC" --cuts "$W/reel/cuts.tsv"
```

It writes a contact sheet and two 20 s excerpts per clip, and `gate.json`. Wait for the answer, then log it verbatim:

```bash
$PY resolve_workflow.py review-log --dir "$W" --project P-0001 --step gate --device desktop --choice go --key "$W/gate" --reason "<their words>"
```

The log lands in `$W/review/events.jsonl`. Only the person's own `go` counts; Claude never writes one for them.

### Cut

```bash
../tools/vertcut/vertcut cut "$SRC" "$W/reel/cuts.tsv" --words "$W/reel/words.json" \
  --destination social_9x16 --show SW --episode 999 --guest Test \
  --hook-text "FIRST LINE|SECOND LINE" --hook-source "words.json 00:00:12" \
  --reviewed "$W/gate/gate.json" --review-log "$W/review/events.jsonl" \
  -o "$W/out/social_9x16"
```

- `--destination social_9x16` sets the aspect in the name, the AAC format and a -14 LUFS target (stderr says so).
- `--hook-text` is the person's own words, verbatim, `|` between two lines, at most nine words and 16 characters a line. `--hook-source` says where they said it. A hook that fails the check exits 2. They apply to every clip in the run: for a batch where each clip has its own hook, use the worksheet's fifth column instead and drop both flags, or cut one clip at a time with `--only`. Leave them off for a clip with no hook, or pass `--no-hook`.
- `--reviewed` and `--review-log` are needed only when the gate applies. Both files sit outside any repository, and the gate must match this `cuts.tsv`.
- Files are named by the house rule (`SW999_Test_01_<slug>_9x16.mp4`). Writing into a folder named for the destination lets the `folder` row pass.
- Add `--dry-run` first to see what would render.

### Pick between versions

One decision per message. For a lettered, blind choice of one clip's crop or hook, render the variants:

```bash
../tools/vertcut/vertcut cut "$SRC" "$W/reel/cuts.tsv" --words "$W/reel/words.json" --show SW --episode 999 --guest Test --only 1 --review --variant x=0 --variant x=-0.4 -o "$W/ab"
```

For renders you already have (2 to 4 of the same reel):

```bash
$PY resolve_workflow.py reel-ab --out "$W/ab2" --seconds 10 --opening "<render-a>.mp4" "<render-b>.mp4"
```

Send the sheet alone, read the key (`$W/ab.key`, `$W/ab2.key`) only after the letter comes back, and log it with the `review-log` line the run prints. See section 5 and [media-review-loop.md](media-review-loop.md).

### Check, then release

```bash
cat > "$W/reel/boxes.json" <<'EOF'
[{"label": "caption", "box": [0.1, 0.55, 0.9, 0.7]}]
EOF
F="$W/out/social_9x16/SW999_Test_01_<slug>_9x16.mp4"
mkdir -p "$W/reel/stubs"
$PY resolve_workflow.py deliver-check "$F" --dest social_9x16 --boxes "$W/reel/boxes.json" --review-log "$W/review/events.jsonl" --ledger-row "$W/reel/stubs/SW999_Test_01.jsonl"
$PY resolve_workflow.py qc "$F" --dest social_9x16 --words "$W/reel/words.json"
$PY resolve_workflow.py release-check "$W/out/social_9x16" --titles "$W/reel/titles.tsv" --review-log "$W/review/events.jsonl" --stubs "$W/reel/stubs"
```

`boxes.json` holds each caption's or super's box as fractions of the frame (x0, y0, x1, y1); `captionlint --boxes-out` writes one for you. `--ledger-row` writes one row stub per file (keep one file each in `stubs/`); with the review log it fills the approval field. `release-check` also reads a `SHA256SUMS` file and a `titles.tsv` (`file`, tab, `title`), so write them before it runs:

```bash
(cd "$W/out/social_9x16" && shasum -a 256 *.mp4 > SHA256SUMS)
printf 'SW999_Test_01_<slug>_9x16.mp4\tTest reel one\n' > "$W/reel/titles.tsv"
```

The The `approval` row WARNs until the log holds an `approve` or `go` line that names the file and its review copy. The person says yes; you record it, then run `deliver-check` and `release-check` again:

```bash
$PY resolve_workflow.py review-log --dir "$W" --project P-0001 --step gate --device desktop --choice approve --review-copy "$F" --reason "<their words>"
```

### Open rulings, and the default until one lands

- **Caption bar position.** vertcut puts the bar's bottom edge at y=1680, inside the bottom UI band, and prints a WARN. Default: leave it and accept the WARN; do not move it clip by clip. `--caption-y` is there when a ruling comes.
- **Conform at 23.976.** vertcut keeps the source rate (capped at 30), so a 23.976 source gives a 23.976 clip, and `deliver-check` and `qc` WARN `fps_floor` and `social_fps`. Default: ship as cut and report the WARN; do not retime.
- **Approval record.** Nothing writes the final-approval line for a vertcut file. Default: the person's own `approve` or `go`, logged with `--review-copy`, is the record; without it `approval` stays a WARN.

**A pass looks like:**

- `copy` exits 0 and the folder, `.SHA256SUMS` and `.card.json` all exist.
- Every `reel-spans` row is OK, or WARN with a reason you accept.
- `gate.json` names the clip count and total runtime, and the log holds a `gate` line with choice `go`.
- The clips sit in the destination folder with the house names.
- `deliver-check` and `qc` show no FAIL; the only WARNs are the open rulings above.
- `release-check` prints READY for each file once the sums, titles, stubs and approval line are in place.
- Stays by hand: formatting the card, the offsite copy, every yes, the upload, and the ledger fields filled after publishing.

## When it fails

| You see | It means | Do this |
|---|---|---|
| `develop`: `no face` | Vision found no face in the small render | Use a frame with a face; a landscape or a back-of-head frame is correctly refused |
| `develop`: `no neutral reference in frame` | No white or grey garment or card below the face | Add `--neutral-box X0 Y0 X1 Y1` (a grey card, 0..1 from top left) or `--wb-from ID` (a frame in the same light) |
| `develop`: `not_converged`, tint near ±50 | Mixed or coloured light pushes the white balance to its bound | Check `delta_e` in `calibration.json`; if it is above 1, use `--wb-from` a frame in cleaner light |
| `develop` or `finish` refuses the folder | The output is inside a git tree | Pick `OUT` under `~/media-out/` |
| `mixbuild`: stems sum-null check fails | A non-linear step is on the mix bus (limiter, dynamic loudnorm), or the mix clipped | Drop the limiter; lower `speechnorm i`; read the reason, which names a clipped mix when that is the cause |
| `mixbuild`: arnndn model refused | `RPRESOLVE_RNNOISE_MODEL` unset or not an absolute path | Export it to an existing `.rnnn` file |
| `interchange`, `pacing` or `cut`: "audio_lead at a change of source is not supported" | A split edit would take sound from two files | Remove the `audio_lead` or `audio_tail` there |
| `cut`: "UNREFRAMED ... several sources" | A 9:16 or 1:1 is planned for one source | Pass `make_9x16` false; the 16:9 is built from all sources |
| `lutlab balance`: `no_neutral` or `no_face` | No white or grey reference below a found face, or no face at all (a chart) | Add `--face` and `--neutral-box`, or `--wb-from SHOT` for a shot in the same light |
| `gate-packet` or `vertcut cut`: stops on the review gate | The batch is over 30 minutes or would replace an output, and no `go` is logged | Send the packet, log the person's `go`, pass `--reviewed` and `--review-log` |
| `reel-spans`: a FAIL row | A quoted phrase is not in the transcript, or the span is reversed | Read the hint, fix the quote, run again; the row is left out of the worksheet |
| `vertcut cut`: exit 2 on the hook | The hook text fails `hooklint` (nine words, two lines of 16 characters, no em-dash or emoji) | Shorten it in the person's own words, or pass `--no-hook` |
| `lutlab shot-match`: "frames do not show the same content" (exit 2, nothing written) | The two stills are not the same scene, framed alike (rank correlation under 0.8) | Crop both to the same chart or subject |
