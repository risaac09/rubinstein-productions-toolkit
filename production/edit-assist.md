# Edit assist: wordsnap, shots, decon, beats, cutout

**Status:** operational.

Three offline helpers for the edit. Each reads media with ffmpeg, does its arithmetic in numpy, writes one file outside any git working tree (and never over its own input), and never touches Resolve. Each proposes; the editor decides. They run on Python 3.9 with numpy, like the rest of `rpresolve`.

| Tool | Reads | Writes | Feeds |
|---|---|---|---|
| `wordsnap` | an mlx_whisper words JSON and the media it came from | the same JSON with word edges moved onto the speech | anything that reads words through `cutlist.load_words`: selects, endcheck, trim review |
| `shots` | a video file (camera original, screen recording, supplied edit) | a shot list, `.csv` or `.tsv` | `qc --shots`, and rhythm (`.tsv`) |
| `beats` | a music file, or a cut's own audio | `beats.json` | rhythm `--beats` |
| `cutout` | a span of a talking-head clip | a ProRes 4444 clip with alpha and `cutout-report.json` | a Resolve timeline or an ffmpeg overlay |

Run each from `production/`. Every one also exposes `add_arguments(parser)` and `run(args)` for a parent CLI.

## wordsnap

```sh
python3 -m rpresolve.wordsnap WORDS.json SOURCE --out SNAPPED.json [--reach 0.4] [--margin-db 12]
```

Whisper's word boundaries drift, and next to a pause they drift into it: the word after a breath starts early, and the word before a pause often ends early or late. A cut made on those times keeps dead air or clips a consonant. wordsnap reads the source audio as a 10 ms RMS envelope, calls a window speech when it sits 12 dB over the file's own floor (the 10th percentile window), and moves each word edge that touches a pause onto the edge of the speech, never more than 0.4 s:

- a start in a pause moves later, to where the speech begins
- a start inside speech moves earlier, to where that run of speech begins, when it begins after the previous word ended
- an end in a pause moves earlier, to where the speech stops
- an end inside speech moves later, to where that run stops, when it stops before the next word starts and no pause of 0.4 s or more lies inside the word

Pauses under 30 ms inside speech (stop consonants) count as speech, and blips under 30 ms in a pause (clicks) count as pause. An edge between two words said without a pause stays where Whisper put it. A word with no speech under it keeps its times. No overlap between words is introduced.

The output keeps the `segments[].words[]` contract, so `cutlist.load_words` reads it unchanged. Each word gains `moved_ms` (`{"start", "end"}`, snapped minus Whisper, signed milliseconds), and a top-level `wordsnap` block echoes the source, the parameters, the floor and threshold in dBFS, the share of windows called speech, and the median, p90 and largest move for starts and ends.

On two short speech clips from a camera test (quiet room, floor near -79 dBFS, mlx_whisper large-v3-turbo with word timestamps), 6 of 30 word edges moved, by 60 to 380 ms (median 150 ms): four starts that Whisper put in the silence before a word moved later, and two ends that Whisper put inside the decay of the last syllable moved later. Each landed where the envelope crosses the threshold.

What it does not do, and limits:

- It does not cut. endcheck still checks every out-point against the waveform.
- An edge more than 0.4 s off stays put. Whisper's first word after a long silence can start a second or more early; raise `--reach` for that file, and check the result.
- When Whisper's last word swallows a pause and the start of something it did not transcribe, the end stays put (the pause inside the word blocks the move). Re-transcribe or trim by hand.
- The threshold is relative to the file's own quietest tenth, with windows of digital silence (exact zeros: a muted call track, a gated head) left out. A file that is speech wall to wall has no real floor; quiet syllables then read as pause. Loud room tone (HVAC, traffic) lifts the floor and the threshold with it.
- Breaths and lip noise over the threshold count as speech.

## shots

```sh
python3 -m rpresolve.shots SOURCE --out SHOTS.tsv [--threshold 10] [--no-dissolves]
```

`--backend pyscenedetect` takes the hard cuts from PySceneDetect's adaptive detector (downscale 2) in place of scdet. It is a separate program the toolkit calls and never imports: `uv tool install 'scenedetect[opencv]==0.7.2'` puts it on PATH. On a 240 s render with 26 planned cuts it found 24 and no false cut, against scdet's 14 and 50 false (trial of 2026-10-31); `--threshold` applies to scdet only. It runs with a shortest shot of 2 frames: PySceneDetect's default of 15 found 7 of 15 cuts in the punch-montage method's timing, and 2 found all 15. On the 240 s render through this command it finds 25 cuts in 40 s, the trial's 24 and one real cut the default hid (a 0.4 s shot), and no false one. With this backend a cut outranks a dissolve that holds it. The dissolve pass still reads handheld motion as dissolves (56 on that render through this command, which has none), so pass `--no-dissolves` on handheld edits.

qc and rhythm run ffmpeg's `scdet` on a render. shots runs the same filter (`scdet=t=10`, qc's threshold) through the same parser (`qc.parse_scdet`) on the footage itself, and every frame that scores the threshold or more starts a new shot. The file has a header (`shot,in,out,duration,into,blend_in,blend_out`) and one row per shot, times in source seconds. A `.tsv` path is written tab-separated, the paper-edit shape rhythm reads; any other path is comma-separated. `qc --shots` counts the rows of either.

On three 3-second trims of camera test clips joined end to end (chart, lens-cap black, chart; 1920x1280 at 29.97 fps), shots found cuts at 3.003 s and 6.006 s, on the true joins to the frame. Joining three chart clips of the same framing at different ISO, the joins scored 4.3 and 3.2, under the threshold, and no cut was found (the next highest frame scored 0.4).

What it does not do, and limits:

- Fades, wipes and slow dissolves: scdet compares neighbouring frames, so a slow transition seldom crosses the threshold. A second pass (on by default, `--no-dissolves` skips it) finds dissolves as MIN_FRAMES or more of steady change, and can still miss some.
- Dips and flashes: the same pass reads the frames it already decoded. A flat near-black run of 2 frames or more (near-white, 4 or more) with a picture on both sides is a dip; the shot after it has `into` `dip`, `blend_in` and `blend_out` span the fall and rise, and the dip replaces any cut or dissolve inside it. A flash is 1 to 3 frames far brighter than both neighbours and is listed by time under `flashes` in the report (not a column). `--no-dissolves` skips both. A fade at the very start or end of the file is not a dip.
- A hard cut between two shots of the same scene in the same light can score under 10 (as above). Lower `--threshold` for such footage, and expect false cuts on fast motion.
- Strobes and whip pans can score as cuts. A flash scores two cuts, and they stay in the list: the `flashes` list in the report names where to look.
- It decodes every frame at full resolution: a long 6K HEVC file takes a while.

## decon

```sh
python3 resolve_workflow.py decon fetch URL --out-dir DIR [--yt-dlp PATH]
python3 resolve_workflow.py decon recipe REF --out recipe.json [--id ID] [--beats MAP.json] [--against METHOD] [--grade] [--titles] [--effects] [--sound [--kinds KINDS.json]] [--moves]
python3 resolve_workflow.py decon sound REF --out sound.json [--kinds KINDS.json]
python3 resolve_workflow.py decon compare REF REBUILD --out compare.json [--sheet sheet.png] [--titles]
python3 resolve_workflow.py music-map VIDEO --out music-map.json [--shots shots.csv]
```

`fetch` is the first step: it downloads one public video with yt-dlp (an argument list, never a shell; only `http` and `https` URLs; best mp4 where there is one; `--no-playlist`) into DIR as `<id>.mp4` and `<id>.info.json`, then writes `<id>.ref.json`: url, id, title, uploader, duration, fps, width, height, file name, sha256 and `fetched_at` (UTC). ffprobe checks the file: it must have a video stream and its duration must be within 0.5 s of the info json's, else exit 1. yt-dlp missing is an error with the `brew install yt-dlp` hint, or pass `--yt-dlp PATH`. DIR must be outside every git tree, like the other outputs. References are for study only: keep them in a scratch folder or on storage you control, never commit or publish them, and respect the source's terms.

`recipe` and `compare` both take `--backend` and `--no-dissolves` as shots does, and the cuts come from shots. Pass `--backend pyscenedetect` on real edits.

`recipe` writes a finished video as an edit method (`edit-methods.md`) that the method validator passes: one slot per shot with its length, a dissolve or dip as the slot's `transition_out`, a black last shot (mean L* under 5) as `rules.end`, the aspect from the frame, and with a beat map on the video's own clock the tempo and `snap_to_beat`. Without `--moves` it carries timing only, and role, move, query and fits are placeholders. `--against` checks the round trip on a render of a known method: the same slot count and every slot within 2 frames, exit 1 if not. A flat-colour render of punch-montage (15 slots, the shortest 3 frames, 0.7 s of end black) comes back with 15 slots inside the 2 frames.

`--moves` (stages D3 and D4, `rpresolve/decon_moves.py`, needs OpenCV) reads each shot's camera move by feature tracking and fills the slots' `move`, `role` and `scale`. Each shot is read 320 px wide, off its blends and 2 frames in from each end, at most 120 frame pairs (a longer shot every Nth frame). Corners are tracked by pyramidal Lucas-Kanade against a keyframe that moves on after a 10 percent shift or an 8 percent zoom, and a similarity (scale, rotation, shift) is fitted with RANSAC per frame, so a car driving at the lens or grass in the wind does not add up over a locked-off shot. From the chained fit: `scale` (end size over start, 1.15 for a 15 percent push), `centre` (the zoom's fixed point, `zoom-push`'s `centre`), `pan` and `tilt` (how far the content at the frame centre moved, a share of the frame width or height), `jitter` (the RMS of each step's shift less its 5-step running mean) and `ramp` (the move's speed in its last third over its first, kept at 2.5 times or more, or a third or less, on a shot that moves).

The class is the largest of |ln scale|, |pan| and |tilt| when it reaches 0.03: push, pull, pan or tilt; handheld when jitter reaches 0.0025 of the width before a move reaches 0.09; else static. The words: static `static`, push `push-in` with `scale` [1, s], pull `static` with `scale` [1/s, 1] (the vocabulary has no pull, so the rebuild keyframes the zoom out; `to_manifest` writes that as a Zoom keyframe step), pan `pan`, tilt `tilt-up` (the only tilt word, for either direction), handheld `handheld`. The role is the commonest one for that move in the shipped methods, a placeholder until something reads the picture: the footage-recall captioner may describe the author's own footage only, never a downloaded reference. `recipe.moves.json` beside `recipe.json` holds every number per shot plus the effect params a rebuild would use: `zoom-push` with its `centre` (the macro's own push is 1.0 to 1.15 over 48 frames, so the note gives the measured scale), `reframe-pan` with `pan_from` 0.5 and `pan_to` 0.5 minus the pan (a share of this frame's width; on a wider source, times the crop's share of it), and `retime` with `speed` 1 and `speed_to` the ramp (relative, not the source's absolute speed). The method JSON's slot speed stays 1.

Checked on synthetic shots warped from a textured still with known moves and encoded by ffmpeg (`tests/test_decon_moves.py`): the `zoom-push` macro's own push (1.0 to 1.15 over 48 frames) measured within 0.6 percent of the zoom, a pull, an off-centre push (centre 0.3 back within 0.05), a 25 percent pan and a 20 percent tilt within 0.6 percent, shake as handheld, and a pan that starts still and speeds up as a ramp; the limit is 5 percent. On the author's own method drafts, 30 shots labelled by eye from first-and-last-frame overlays (8 more that hold a cut the shot detector missed were left out) agreed on the class 27 times (90 percent; the limit is 80). Most were locked off; the three misses were a slow drift near the 0.03 line read as static twice, and grass in the wind read as handheld. On one draft span built with a known `reframe-pan`, the measured pan matched an independent phase-correlation reading (0.045 of the width a second in both). A missed cut inside a shot gives that shot a false move: the shot list is only as good as `shots`.

`--effects` also puts the reference's effects on the slots (one more decode of the file): a flash within a quarter second of a shot's start becomes the slot's `exposure-flash` (a flash further in is reported and left off); a freeze frame (ffmpeg `freezedetect` at -50 dB, a hold of 0.3 s or more that starts after the shot has moved, so a still picture is not a freeze) becomes `freeze {at, seconds}`; a speed ramp becomes `ramp [1, ratio]`, read from a line fitted to the shot's frame differences with frozen frames left out. A ramp is a relative speed (1 at the shot's start, not the source's own speed), is read only where there is motion to measure, and reads low past about 5x because a frame difference saturates. Dips are already the slot's `dip` transition. Checked on synthetic clips: a 1 s hold in 3 s of scrolling bars, found at 1.0 s for 1.0 s, and a 1x to 5x ramp, found with a ratio above 2.5; a constant-speed clip and a still picture give neither. `methods.to_manifest` lists a ramp as a `retime` effect (`speed`, `speed_to`) and a freeze as a UI step.

`--grade` also estimates the look. Shots are joined into scenes while each one's mean Lab stays within CIEDE2000 12 of the shot before. Each scene gets a still from the middle of its span, and `recipe.grade/` (beside `recipe.json`, outside any git tree) holds `sceneNN.cube` from `lutlab transfer`, the stills, and `grade.json`: scene, shot span, still time, cube, method, fit numbers (held-out sliced W2 before and after), and a `vignette` flag (all four corners 8 L* or more under the centre). The method JSON is unchanged. The source for each transfer is a neutral stand-in made from the scene's own still (per-channel stretch to a common black and white, gray-world means), so the cube reads as what takes a neutral full-range picture to this look; the rebuild's real footage is not known yet. A scene whose still lies too far from neutral for lutlab's guards is recorded with `cube: null` and the reason. Checked on clips graded with `print-teal`, `bleach` and `tungsten` through ffmpeg `lut3d` and estimated back from the true ungraded still: median CIEDE2000 between the graded still and the source with the estimated cube applied is 1.4, 0.7 and 1.3 (limit 5). That is the easy case: the true source is known and the content is the same. `skin_isolated` and `sky_separated` are not reported; a still cannot tell whether either was graded apart from the rest.

`compare` scores a rebuild against its reference, 0 to 100, as the mean of seven parts from 0 to 1 (more with `beats`, `sound` and `titles`, below): cut recall and no extra cuts (within 2 frames), shot length at matched shots, the difference in coefficient of variation, the distance between the shot-length histograms (these five are timing only), colour (median CIEDE2000 of each shot's mean Lab, zero at 20) and picture (median difference of an 8 x 8 grid of block means per shot, zero at a quarter of full scale). The score is timing-weighted, so a rebuild with the same cut times scores high whatever it shows; it is not a claim that the videos match. The result lists `timing_only_parts` and sets `picture_differs` when colour or picture is under 0.5, and the printed summary says so. compare.json stores file names, not paths. A recipe's default name is `Recipe ID` and its summary does not name the source file. Each reference shot is matched to the rebuild at the same share of the running time. A reference against itself scores 100; the punch-montage render against a shuffled order of the same shots scores under 60. `--sheet` writes the matched middle frames (a one-frame shot is read at its first frame) as pairs, reference left. When both files have audio, an eighth part `beats` is 1 minus the difference between the two cuts' on-beat shares (within 1 frame), each cut on its own file's beat map; both shares are reported, and `beats` is listed as timing only. A file with no audio or no beats drops the part and the summary says so. A `sound` part (also timing only: it reads no picture) is the mean of how near the two integrated loudnesses are (zero at 6 LU apart) and how near the two shares of cuts with a hit are (see `decon sound`); a file with no audio drops it. With `--titles`, a ninth part `titles` scores title timing (below).

`--titles` reads on-screen text with the macOS Vision text request (a Swift helper compiled once with `swiftc`, as reframe does for faces; elsewhere it is an error). `recipe --titles` adds `titles` to the method: each line of text with its `in_s` and `out_s` (out is the end of the last frame drawn), its `box` where it rests (`{x, y, w, h}`, a share of the frame from its top left; Vision's line box, ascent to descent) and its `animation` and `animation_out`: `cut`, `fade`, `slide`, or `word` when the line grows word by word. Every fourth frame is read first. A line is followed while its text stays the same, grows by whole words, or differs by a misread letter, and it ends after two samples without it. Each edge is then found to the frame: Vision reads the frames between, and from the first frame it reads the in point walks back while the line's box still differs from the empty frame before it by 5% of the settled title's difference, since a fade is drawn before it can be read (the out point the same way forward). A first frame under 60% of that difference is a fade; a box over 2% of the frame from where it rests is a slide. `compare --titles` pairs each reference title with the rebuild's of the same (or word-prefix) text nearest in time and reports the in and out error in frames (median and maximum); the part is each pair's 1 minus its mean error over 12 frames, summed over the larger title count, so a title missing from either side counts as zero. It is timing only. Checked on Pillow renders over a still gradient (a cut, an 8-frame fade in and out, a 10-frame slide, three words 6 frames apart): every in and out on the exact frame, box overlap (IoU) against the font's line box 0.81 to 0.90, every animation class right. Limits: over moving footage the walk runs on to the empty sample, so an in point can be up to 3 frames early and an out point up to 3 late; times are frame index over the frame rate, so a file whose video starts at a non-zero timestamp or has a variable frame rate is off by that; a slide from outside the frame is timed from the first frame Vision reads; text in the footage (a sign, a shirt) is reported as a title; two lines are two titles.

`music-map` writes a video's own music map: `beats_s` and `bpm` (so `--beats` and rhythm read it), and `sections` from RMS energy in 1 s windows, binned low, mid or high by thirds of the file's range, a change of bin a boundary, a section under 4 s joined to the one before, labelled `s1`, `s2` and so on. It hears loudness, not verse or chorus. With `--shots` (the `.csv` or `.tsv` shot list `shots` writes, or a report in JSON) it adds `cuts`: each cut's nearest beat, the signed offset in frames, and its section. It is a separate command from `beats` so that one stays a plain beat map of a music file. It refuses a file over 30 minutes (the whole track is held in memory) and an `--out` that names the video or the shots report. A proposal: check it by ear.

`sound` (stage D8) maps a reference's audio, and `recipe --sound` carries the map into the recipe as an optional top-level `sound` field that `methods.validate` checks (a method made by hand has none). It holds:

- `integrated_lufs` and `lra_lu`, and `curve_db`: the mean momentary loudness (ebur128, the pass `qc` and `audioreport` use) of each whole second in LUFS, null where a second has no reading.
- `spans`: `{start_s, end_s, kind}` with kind `speech`, `sung`, `music`, `hallucination` or `uncertain`, from `vertcut kinds` as time ranges only, never text. They come from `--kinds` (a `<name>.kinds.json` from `vertcut transcribe`), else the one beside the video, else `vertcut kinds` run on the audio alone. Without a transcript the classifier cannot tell a voice from a pad and is tuned to catch singing, so read those spans as a proposal.
- `hits`: audio onsets within 2 frames of a cut. An onset is the 10 ms RMS rising 10 dB or more within 20 ms, in one step of 6 dB, to above -45 dBFS, at least 50 ms after the last. In the recipe a hit is `{slot, offset_frames}`, the slot that starts at the cut and the signed offset (onset minus cut). A cut into the end black is left out. `decon sound` also gives `onsets`, `cuts` and `hit_share`.

A file with no audio, or over 30 minutes, is an error. Checked on flat-colour shots cut at 2, 4 and 6 s under noise with bursts on the first cut, one frame after the second and none at the third (two hits found, offsets 0 and 1 frame), a loud span that shows in its second of the curve, and a 14 dB duck of a steady tone (the shape `duckplan` makes) that shows as a 14 dB dip. Not checked on a real mix with music and speech. A proposal: check it by ear.

## beats

```sh
python3 -m rpresolve.beats MUSIC --out beats.json [--prior-bpm 120]
```

A beat tracker in numpy (Ellis 2007, the method librosa follows, without librosa):

1. Onset strength: positive log-spectral flux in seven bands (30 Hz to 8 kHz at 16 kHz mono, 32 ms window, 8 ms hop), each band divided by its own standard deviation, so the many treble bins cannot outvote the few bass bins.
2. Tempo: autocorrelation of the onset strength between 60 and 180 BPM, weighted by a log-normal prior centred on 120 BPM (one octave wide), refined to a fraction of a frame.
3. 3:2 check: with the kick and an off-beat hat about equal in the onset strength, it repeats every half beat, and the peak at 1.5 beats can outscore the beat (a 150 BPM loop read as 100). So when the linear-magnitude flux under 250 Hz (the kick) does not repeat at the tempo's period but does at 3/2 or 2/3 of the tempo (autocorrelation at least half its lag-0 value), that tempo is taken.
4. Beats: dynamic programming, each beat the best onset about one period after the last (tightness 100). Leading and trailing beats weaker than half the beats' RMS strength are dropped, so a silent intro or tail gets no grid.
5. Phase check: an off-beat hi-hat can still pull the tracker half a beat off, because log flux hears a quiet hat over silence as loudly as a kick. So the linear-magnitude flux under 250 Hz (kick and bass) is compared at the beats and halfway between them. When it is stronger halfway (by 1.1 times), the beats are tracked again on it, and the output says `"phase": "low band"`, else `"kept"`.

`beats.json` holds `file`, `duration_s`, `tempo`, `beats_s` (seconds on the file's own clock), `method`, `phase`, `proposal: true` and `params` (every constant above). rhythm reads `beats_s`; make the map from the cut's own audio so the beats sit on the cut's clock.

On synthetic drum loops (kick on the beat, a hat on the off-beat, with and without a backbeat snare and a pad; five variants at every 2 BPM from 88 to 168), beats land within 12 ms of the hits, a frame or so early, with tempo within 0.1 BPM. A WAV and the same loop encoded to AAC give the same beats.

What it does not do, and limits:

- Downbeats or bars: every beat is the same.
- Octave errors: a loop at 75 BPM with off-beat hats reads as 150 BPM (the prior prefers it), and loops above about 170 BPM can read at half tempo. Pass `--prior-bpm` near the tempo you hear.
- 3:2 errors: the 3:2 check needs a kick on every beat. With a sparser or syncopated kick (half-time, a tresillo), a loop at 150 to 160 BPM can still read at two thirds of its tempo (160 as 107), with beats up to half a beat off. `--prior-bpm` near the tempo fixes it.
- Tempo changes beyond what the tracker's tightness follows, swing, rubato, and music without a pulse.
- A bass line that sits on the off-beats can flip the phase check the wrong way.
- It was checked on synthetic loops only, not on recorded music with vocals. Check the grid by ear before cutting to it; a beat the editor marks by hand outranks it.

## trim-apply

```sh
python3 -m rpresolve.trimapply MANIFEST REVIEW.tsv --out NEW.json (--accept 3,7,12-15 | --accepted-file ROWS.txt)
```

`trim_review` proposes; trim-apply applies only the rows an editor names. It is not one of the three helpers above and does not read media: it reads a [cut manifest](manifest.md) and the TSV `trim_review` wrote from it, and writes a copy of the manifest with the accepted rows cut out. There is no default list and no "accept all".

Rows are counted from 1 down the TSV. `--accepted-file` takes numbers and ranges, one or more a line, with `#` comments. An accepted row must be a silence, filler or repeat suggested `tighten` or `cut-candidate`, with confidence above `low`. It cuts `[source_start, source_end)` out of its span, leaving up to two spans; a silence keeps 0.12 s of its pause each side, and a repeat row (which spans both copies) cuts its first half only. The command refuses (exit 2, nothing written) when:

- neither or both of `--accept` and `--accepted-file` are given, or the list is empty, has a duplicate or a bad token, or names a row the TSV lacks
- a row is an `asr-loop`, suggested `keep`, or low confidence
- the TSV header is not `trim_review`'s, or it reviews a whole source (no clip or span), or a row names a clip, span or source the manifest lacks, or lies outside its span
- two accepted rows overlap in one span, a cut leaves under one frame, a silence is too short for its pad, or a clip would be empty
- the manifest is `"speech": false`
- `--out` or its sidecar exists, is the manifest or the TSV, or lies inside a git working tree

`NEW.json.applied.json` records the file hashes, the accepted rows, each cut and the spans it left, the frames removed per clip and every field cleared. `end_words` and `source_text` of a changed span are cleared, not guessed, and `audio_lead` and `audio_tail` stay only on the first and last piece, so run `endcheck` on the copy. A version 1 manifest comes back version 1.

Not here: an MCP tool, `--overwrite`, applying Resolve marker selections, re-deriving end words, and checking that the TSV came from this exact manifest (it carries no hash; the check is that every row lies inside its span).

## cutout

```sh
python3 -m rpresolve.cutout SOURCE --in S --out S --out-dir DIR [--quality accurate|balanced|fast] [--feather PX] [--height 1080]
```

The person in a span of a clip, segmented from the background: a clip with alpha that Resolve reads as a clip with alpha and ffmpeg lays over anything with `overlay`. ffmpeg extracts the span's frames as PNGs into a temp folder under `--out-dir`, scaled to `--height` with the aspect kept; a small Swift helper (`vision_person.swift`, compiled once into `~/Library/Caches/rpresolve` like the face helper) runs macOS Vision's person segmentation over them at the chosen quality and writes one 8-bit matte per frame; `--feather` box-blurs the matte by that many pixels; ffmpeg `alphamerge` joins picture and matte and ProRes 4444 writes `<stem>_cutout_<in>-<out>.mov` at the source's frame rate, picture only, tagged bt709. The temp frames are removed. The report, on stdout and in `cutout-report.json` beside the clip, gives the frame count, the mean matte coverage, the frames where no person was found (a WARN line), the quality level and the output path. Output is refused inside any git working tree (exit 2) and never written over the source; without macOS and `swiftc` the helper cannot run and the tool exits 1 with that reason.

What it does not do, and limits:

- One person at a time is what Vision gives: with two people in frame the matte covers both, or jumps between them. Frame one subject.
- Hair edges and thin things (glasses arms, a mic cable) come out soft or missing; `accurate` is slowest and best, `fast` is a coarse matte for a preview. `--feather 2` or `3` hides a hard edge on a busy background.
- Fast motion (a hand swung across the lens, a quick turn) smears the matte for a frame or two. Pick a still span.
- The matte is Vision's; nothing here checks it. Look at the clip over a flat colour before it goes into an edit.
- The picture goes through 8-bit PNG frames, so a 10-bit source lands as 8-bit picture; a log source stays log (tagged bt709 all the same), so grade the cut-out the way the source is graded.
- No audio: the cut-out is picture. Lay it over the edit on a video track and keep the source's sound.
