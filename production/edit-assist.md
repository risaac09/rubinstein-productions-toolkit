# Edit assist: wordsnap, shots, beats

Three offline helpers for the edit. Each reads media with ffmpeg, does its arithmetic in numpy, writes one file outside any git working tree, and never touches Resolve. Each proposes; the editor decides. They run on Python 3.9 with numpy, like the rest of `rpresolve`.

| Tool | Reads | Writes | Feeds |
|---|---|---|---|
| `wordsnap` | an mlx_whisper words JSON and the media it came from | the same JSON with word edges moved onto the speech | anything that reads words through `cutlist.load_words`: selects, endcheck, trim review |
| `shots` | a video file (camera original, screen recording, supplied edit) | a shot list, `.csv` or `.tsv` | `qc --shots`, and rhythm (`.tsv`) |
| `beats` | a music file, or a cut's own audio | `beats.json` | rhythm `--beats` |

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
- The threshold is relative to the file's own quietest tenth. A file that is speech wall to wall has no real floor; quiet syllables then read as pause. Loud room tone (HVAC, traffic) lifts the floor and the threshold with it.
- Breaths and lip noise over the threshold count as speech.
- The envelope is read with `cutlist.decode_pcm`, which seeks with `-ss` before the input. On AAC audio that seek drops up to one AAC packet (about 23 ms at 44.1 kHz), so edges on an AAC source can sit up to that much early. PCM audio (camera originals, WAV) is exact.

## shots

```sh
python3 -m rpresolve.shots SOURCE --out SHOTS.tsv [--threshold 10]
```

qc and rhythm run ffmpeg's `scdet` on a render. shots runs the same filter (`scdet=t=10`, qc's threshold) through the same parser (`qc.parse_scdet`) on the footage itself, and every frame that scores the threshold or more starts a new shot. The file has a header (`shot,in,out,duration`) and one row per shot, times in source seconds. A `.tsv` path is written tab-separated, the paper-edit shape rhythm reads; any other path is comma-separated. `qc --shots` counts the rows of either.

On three 3-second trims of camera test clips joined end to end (chart, lens-cap black, chart; 1920x1280 at 29.97 fps), shots found cuts at 3.003 s and 6.006 s, on the true joins to the frame. Joining three chart clips of the same framing at different ISO, the joins scored 4.3 and 3.2, under the threshold, and no cut was found (the next highest frame scored 0.4).

What it does not do, and limits:

- Dissolves, fades and wipes: scdet compares neighbouring frames, so a slow transition seldom crosses the threshold.
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
3. Beats: dynamic programming, each beat the best onset about one period after the last (tightness 100). Leading and trailing beats weaker than half the beats' RMS strength are dropped, so a silent intro or tail gets no grid.
4. Phase check: an off-beat hi-hat can still pull the tracker half a beat off, because log flux hears a quiet hat over silence as loudly as a kick. So the linear-magnitude flux under 250 Hz (kick and bass) is compared at the beats and halfway between them. When it is stronger halfway (by 1.1 times), the beats are tracked again on it, and the output says `"phase": "low band"`, else `"kept"`.

`beats.json` holds `file`, `duration_s`, `tempo`, `beats_s` (seconds on the file's own clock), `method`, `phase`, `proposal: true` and `params` (every constant above). rhythm reads `beats_s`; make the map from the cut's own audio so the beats sit on the cut's clock.

On synthetic drum loops (kick on the beat, a bright hat on the off-beat, snare on the backbeat, a pad or a bass), beats land within 12 ms of the hits, a frame or so early, at 97, 104 and 120 BPM, with tempo within 0.05 BPM. The same loops encoded to AAC read about 23 ms earlier still (see the seek note under wordsnap).

What it does not do, and limits:

- Downbeats or bars: every beat is the same.
- Octave errors: a loop at 75 BPM with off-beat hats reads as 150 BPM (the prior prefers it). Pass `--prior-bpm` near the tempo you hear.
- Tempo changes beyond what the tracker's tightness follows, swing, rubato, and music without a pulse.
- A bass line that sits on the off-beats can flip the phase check the wrong way.
- It was checked on synthetic loops only, not on recorded music with vocals. Check the grid by ear before cutting to it; a beat the editor marks by hand outranks it.
- The same AAC seek note as wordsnap applies.
