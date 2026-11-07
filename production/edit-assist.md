# Edit assist: wordsnap, shots, beats, cutout

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

qc and rhythm run ffmpeg's `scdet` on a render. shots runs the same filter (`scdet=t=10`, qc's threshold) through the same parser (`qc.parse_scdet`) on the footage itself, and every frame that scores the threshold or more starts a new shot. The file has a header (`shot,in,out,duration,into,blend_in,blend_out`) and one row per shot, times in source seconds. A `.tsv` path is written tab-separated, the paper-edit shape rhythm reads; any other path is comma-separated. `qc --shots` counts the rows of either.

On three 3-second trims of camera test clips joined end to end (chart, lens-cap black, chart; 1920x1280 at 29.97 fps), shots found cuts at 3.003 s and 6.006 s, on the true joins to the frame. Joining three chart clips of the same framing at different ISO, the joins scored 4.3 and 3.2, under the threshold, and no cut was found (the next highest frame scored 0.4).

What it does not do, and limits:

- Fades, wipes and slow dissolves: scdet compares neighbouring frames, so a slow transition seldom crosses the threshold. A second pass (on by default, `--no-dissolves` skips it) finds dissolves as MIN_FRAMES or more of steady change, and can still miss some.
- A hard cut between two shots of the same scene in the same light can score under 10 (as above). Lower `--threshold` for such footage, and expect false cuts on fast motion.
- Flashes, strobes and whip pans can score as cuts.
- It decodes every frame at full resolution: a long 6K HEVC file takes a while.

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
