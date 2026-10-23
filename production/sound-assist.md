# Sound assist: room tone, dialogue numbers, ducking

Three offline modules in `production/rpresolve/` for the sound pass:

- `roomtone` finds the room tone a recording already holds and loops it into a bed of any length.
- `dxmetrics` puts numbers on a dialogue chain, before and after, with no clean reference.
- `duckplan` ducks music under dialogue by a depth given in dB, keyed on whether someone is speaking.

They run on the Mac's Python 3.9 with numpy and ffmpeg (`RPRESOLVE_FFMPEG` and `RPRESOLVE_FFPROBE` override the Homebrew paths, as for the other rpresolve modules). Each is a command, `python3 -m rpresolve.<name>`, run from `production/`. Each refuses to write inside any git working tree, because its output names media. None of them changes a source file: an output that is a source under another name (a hard link, or a name that differs only in case on the Mac's case-insensitive disk) is refused. None of them is listening: the ear and a blind A/B decide.

## roomtone

```
python3 -m rpresolve.roomtone find FILE [FILE ...] [--window 3] [--out JSON]
python3 -m rpresolve.roomtone bed FILE --length S --out BED.wav [--stream N]
    [--start S --end S] [--xfade 1] [--curve auto|qsin|tri]
```

`find` reads every audio stream of every file and reports, per stream, the quietest window (start, end, RMS and peak in dBFS, in 100 ms steps). A stream of digital zeros, such as the two unused tracks of a four-track camera file, is left out and listed as `digital_silence`. A window that crosses a dropout inside a live stream (1 ms or more of zeros on every channel) is never chosen.

`bed` takes a window of one stream (by default the quietest 3 s) and builds one loop unit from it: the window's last `--xfade` seconds crossfaded into its start, so the end of the unit runs on into its own beginning sample for sample. ffmpeg's `aloop` repeats the unit to the length asked, so every seam is that one crossfade. The check compares the power over the middle 40% of each crossfade with the mean power of the two source stretches it blends, so the room's own drift does not count against the seam. A seam more than 1 dB off exits 1, with the bed still written for listening. The bed keeps the stream's rate and channels (24-bit WAV), with `BED.json` beside it.

The curve matters and depends on the room. Measured on generated signals: on noise, `qsin` held the seam within 0.35 dB and `tri` dipped 2.5 to 3.1 dB; on a 60 Hz tone, `tri` held 0 dB and `qsin` bumped 2.9 dB. `--curve auto` renders both and keeps the closer one. On camera test clips (room tone at -52 to -72 dBFS), `auto` chose `qsin` every time, with seams between -0.7 and +0.2 dB.

It does not set the level the bed sits at (6 to 10 dB under the clip noise is a usual start), fill gaps in a timeline, or take hum out of the loop. A window under 2 s is refused: a short loop is heard as a loop.

## dxmetrics

```
python3 -m rpresolve.dxmetrics measure FILE [--stream N] [--start S] [--end S] [--out JSON]
python3 -m rpresolve.dxmetrics lag A B [--stream N] [--stream-b N] [--start S]
    [--seconds 30] [--max-lag-ms 250]
python3 -m rpresolve.dxmetrics compare A B [--stream N] [--stream-b N] [--start S] [--end S]
    [--out JSON]
```

The numbers, for one stream mixed to mono:

| Number | What it is | How to read it |
|---|---|---|
| `integrated_lufs`, `lra_lu`, `true_peak_dbtp` | ffmpeg `ebur128` with `peak=true`, as audio-report reads them | level and range |
| `sibilance_ratio_db` | 5-9 kHz power over 100 Hz-5 kHz power | a de-esser lowers it; presence EQ and compression raise it |
| `flatness` | mean spectral flatness of 2048-sample frames, as `aspectralstats` computes it | about 0.85 white noise, 0.7 pink, near 0 a tone; hum and music lower it |
| `hum_excess_db_50`, `hum_excess_db_60` | mean height of mains harmonics 1-5 over their neighbouring bins (Welch 16384) in the quietest 25% of 100 ms frames | a few dB is the noise of the estimate; 10 dB or more is hum worth a notch |
| `wada_snr_db` | a blind signal-to-noise estimate in the manner of WADA (Kim and Stern, 2008), its lookup built by seeded simulation | compare it only with itself; it is held to -10..50 dB |
| `lag` | cross-correlation lag of B against A, in samples at A's rate, with the polarity of the best match | positive means B is late |

`compare` prints both files' numbers, B minus A, and the lag. The lag is there because some ffmpeg denoisers delay their output and ffmpeg does not compensate: `afftdn` by 1200 samples at 48 kHz (25 ms) and `anlmdn` by 384, and the file keeps its length, so a denoised stem lands late against picture. Measured on two camera test clips through `highpass=f=80,afftdn,deesser`: lag 1200 and 1196 samples (the high-pass shifts phase, which moved one peak by 4 samples), and 0 and -4 after `atrim=start_sample=1200,asetpts=PTS-STARTPTS`. A high-pass such as `asubcut` adds no fixed delay to compensate: it reads lag 0 on broadband audio, and on bass-heavy audio its phase shift can move the peak a few dozen samples with the polarity inverted. When either file holds no signal in the window (a music intro over silent dialogue), or the best match is under 0.05, `lag` gives no lag rather than "aligned": give `--start` where both files carry sound.

Limits. The whole range is held in memory, several copies at the peak: `measure` reached 1.1 GB on 10 minutes at 48 kHz and about 6 GB on an hour, so give a range on a long file. Sibilance and flatness cover the whole range, room tone included; give `--start` and `--end` around speech to read the voice. The hum figure averages five harmonics, so a pure fundamental reads lower than a buzzing one. The WADA figure leaves digital silence out, so the gaps of an edited stem do not count as clean signal, but it saturates once a denoiser empties the gaps: three camera test clips read 22 to 23 dB before `highpass=f=80,afftdn,deesser` and 40 to 50 dB after.

## duckplan

```
python3 -m rpresolve.duckplan plan DIALOGUE --out DIR [--stream N] [--depth 12] [--gate-db DB]
python3 -m rpresolve.duckplan render MUSIC DIALOGUE --out DIR [--music-stream N] [--stream N]
    [--depth 12] [--gate-db DB]
```

A compressor keyed on the raw dialogue ducks by an amount that follows the talker. On a test track with a loud and a quiet talker in one room (-20 and -32 LUFS), music under a raw-dialogue sidechain at the usual settings (threshold -30 dB, ratio 8, attack 20 ms, release 400 ms) went down 9.3 to 9.6 dB under the loud talker and 4.1 to 6.6 dB under the quiet one. `duckplan` keys on speech activity instead:

1. a 100 Hz high-pass on the dialogue, then the RMS of every 10 ms
2. a gate 10 dB over the floor (the 10th percentile of 100 ms RMS, audio-report's noise floor), or at `--gate-db`. When 10% or more of the 100 ms frames are digital silence (a gated, strip-silenced or synthetic voice stem), the floor is digital silence and the gate sits at -70 dBFS
3. openings under 50 ms dropped (a click is not a word), and each opening held 0.3 s so the pauses inside a phrase do not pump the music
4. the 0/1 result smoothed by a 2-pole low-pass at 8 Hz and moved 0.12 s earlier, so the music is down before the first syllable

The gain is -depth times the key, in dB. `plan` writes three files into DIR:

- `duck.sendcmd`: `asendcmd` lines for a volume filter named `duck`, for an ffmpeg graph run from DIR: `asetnsamples=n=480:p=0,asendcmd=f=duck.sendcmd,volume@duck=volume=1` at 48 kHz. It needs ffmpeg 6.0.1 or later: 6.0 names that filter `duck`, not `volume@duck`, so the commands reach nothing and the music is not ducked
- `duck-key.wav`: the key as a DC signal, 0 for silence and 1 for speech, mono 8 kHz, for a compressor's sidechain. `duckplan.json` gives the `sidechaincompress` setting that turns it into the depth asked (ratio 20, threshold -depth / (1 - 1/20) dB). Some hosts block DC on a sidechain input; ffmpeg does not
- `duckplan.json`: the parameters, the floor and gate, the phrases, and both usage lines

A dialogue with no speech above the gate is refused (exit 1) and nothing is written. `render` makes the plan, ducks the music through `sidechaincompress` keyed by `duck-key.wav`, writes `music-ducked.wav` at the music's rate and channels, and measures it against the music: the reduction where the key is fully on, where it is off, before it first rises (the lead-in), per phrase, and the spread across phrases. On the same test track with a generated chord bed, the reduction was 12.0 dB under every phrase at `--depth 12` and 18.0 dB at `--depth 18`, with 0.0 dB outside speech and over the 3.2 s lead-in.

Limits:

- The floor is one number per file. When a stem cuts between rooms whose tone differs by 10 dB or more, the louder room's tone opens the key: in a test cut, the key opened at 0 s on the louder room. `--gate-db` above the louder room's tone fixed it; so does planning each setup's stretch on its own.
- A stem with digital silence between clips that carry room tone (an edit with gaps) gets the -70 dBFS gate, so the room tone opens the key and the music stays down for each whole clip: in a test edit, two clips 2-8 s and 12-18 s keyed whole, where the talk ran 3-6 s and 13-16 s. `--gate-db` above the room tone (-50 there) keyed the talk alone. On a gated stem the fixed gate is what works: camera test clips with every 10 ms under -60 dBFS zeroed keyed 3.14 and 2.61 s of speech, against 3.21 and 2.61 s ungated.
- A talker within about 10 dB of the floor is partly missed. In the one-room test, the quiet talker lost 0.7 s in the middle of a 2.6 s phrase, where the music rose again.
- MUSIC and DIALOGUE are taken to start together; music past the dialogue's end is not ducked.
- It does not choose the depth (12 to 18 dB is a common start for music under talk), mix, or set loudness.

## How these feed mixbuild

`mixbuild` (pull request #246, held) renders stems and a mix from a spec. These modules feed it without changes to it:

- A room-tone bed is a stem: name `bed.wav` as a stem's `input` and give it a `gain` step.
- A ducked music file is the music stem's `input`. Leave `mix.duck` out of that spec, or the music is ducked twice. Since the #246 follow-ups, `mix.duck` itself runs duckplan on the rendered key stem and takes `depth` (and `gate_db`), so the depth holds across talkers without a separate pass.
- `dxmetrics compare` on a stem's input and mixbuild's output stem gives the before-and-after numbers its `render.json` does not carry, and its lag line should read 0: mixbuild measures each denoiser's delay and trims it off.
- `roomtone find` gives the `[start, end]` of a noise-only stretch, which a denoise step that learns its noise from a sample (`afftdn`'s `sample_noise` command) would take.

None of these modules runs `loudnorm`. A chain that does should end with `aresample=48000`: `loudnorm` outputs 192 kHz.
