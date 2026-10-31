# Media lane quickstart

Copy-paste checklists for the five lanes, from a fresh shell. The first two are the ones to run yourself; the other three are shorter. What each tool does is in [media-lane.md](media-lane.md).

Every command was run in a clean shell (`env -i` with only `HOME` and `PATH`) against real media. The times and numbers under "A pass looks like" are from those runs on an M-series Mac.

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
| `lutlab shot-match`: "frames do not show the same content" (exit 2, nothing written) | The two stills are not the same scene, framed alike (rank correlation under 0.8) | Crop both to the same chart or subject |
