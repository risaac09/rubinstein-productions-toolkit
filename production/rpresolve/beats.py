"""
rpresolve.beats: a beat map for a piece of music, as a proposal. Offline:
ffmpeg decodes, numpy does the rest; no librosa, never Resolve.

Method (Ellis 2007, the tracker librosa follows):
    onset strength  log-magnitude STFT (NFFT/HOP at SR, centred frames),
                    positive spectral flux summed in each of the seven
                    BANDS_HZ bands, every band divided by its own standard
                    deviation before they are added, so the many bins of
                    the treble cannot outvote the few of the bass
    tempo           autocorrelation of the onset strength between MIN_BPM
                    and MAX_BPM, weighted by a log-normal prior centred on
                    PRIOR_BPM (PRIOR_OCTAVES wide), the peak refined to a
                    fraction of a frame
    beats           dynamic programming: each beat is the best onset about
                    one period after the last, a log-period penalty scaled
                    by TIGHTNESS; leading and trailing beats weaker than
                    TRIM of the beats' RMS strength are dropped (a silent
                    intro or tail gets no grid)
    phase check     an off-beat hi-hat still pulls the tracker half a beat
                    off, because log flux hears a quiet hat over silence as
                    loudly as a kick. So the linear-magnitude flux under
                    LOW_HZ (kick and bass) is compared at the beats and
                    halfway between them; when it is PHASE_MARGIN times
                    stronger halfway, the beats are tracked again on it
                    ("phase": "low band" in the output, else "kept")

Output beats.json: {"file", "duration_s", "tempo", "beats_s", "method",
"phase", "proposal": true, "params"}. `beats_s` are seconds on the file's
own clock; rhythm reads them (made from a cut's own audio, they sit on its
clock). On synthetic kick, snare and hat loops beats land within about
12 ms of the hits, a frame or so early.

Run from production/:
    python3 -m rpresolve.beats MUSIC --out beats.json [--prior-bpm 120]

What it does not do: downbeats or bars, tempo changes beyond what the
tracker's tightness lets it follow, swing, or music without a pulse. A
beat the editor marks by hand outranks it; it proposes a grid to check by
ear.
"""

import argparse
import json
import os
import sys

import numpy as np

from . import cutlist, paths, trimreview

SR = cutlist.AUDIO_RATE    # 16 kHz mono, decoded by cutlist.decode_pcm
NFFT = 512                 # 32 ms window
HOP = 128                  # 8 ms between onset frames
GAMMA = 100.0              # log compression: log(1 + GAMMA * |X|)
BANDS_HZ = (30, 100, 250, 600, 1500, 3000, 5000, 8000)  # seven bands
LOW_HZ = (30, 250)         # kick and bass: the phase check reads linear magnitude here
PHASE_MARGIN = 1.1         # retrack on LOW_HZ when it is this much stronger off the beats
MIN_BPM, MAX_BPM = 60.0, 180.0
PRIOR_BPM = 120.0
PRIOR_OCTAVES = 1.0
TIGHTNESS = 100.0
TRIM = 0.5
MIN_SECONDS = 4.0          # a shorter file holds too few beats for a tempo
BLOCK = 4096               # STFT frames per block: memory stays flat on long files
METHOD = ("7-band std-normalised spectral flux; autocorrelation tempo with a log-normal "
          "prior; dynamic-programming beat tracker; low-band phase check")


class BeatsError(ValueError):
    pass


def onset_strength(y, sr=SR, nfft=NFFT, hop=HOP, edges=BANDS_HZ):
    """(env, low) per HOP, frame k at k * hop / sr: env the 7-band log flux,
    every band divided by its standard deviation, scaled to a peak of 1;
    low the linear-magnitude flux in LOW_HZ, where a kick's energy is (log
    compression makes a quiet hat over silence jump as far as a kick)."""
    y = np.pad(np.asarray(y, dtype=np.float64), nfft // 2)  # centred frames, zeros past both ends
    n = 1 + (len(y) - nfft) // hop
    if n < 2:
        raise BeatsError("too little audio for an onset envelope")
    win = np.hanning(nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    bands = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
    lowband = (freqs >= LOW_HZ[0]) & (freqs < LOW_HZ[1])
    flux, low = np.zeros((n, len(bands))), np.zeros(n)
    prev = np.zeros((1, len(freqs)))            # silence before the file: a hit at 0 s counts
    for b0 in range(0, n, BLOCK):
        b1 = min(n, b0 + BLOCK)
        frames = np.lib.stride_tricks.as_strided(
            y[b0 * hop:], (b1 - b0, nfft), (y.strides[0] * hop, y.strides[0]))
        stack = np.vstack([prev, np.abs(np.fft.rfft(frames * win, axis=1))])
        d = np.maximum(np.diff(np.log1p(GAMMA * stack), axis=0), 0)
        flux[b0:b1] = np.stack([d[:, m].sum(axis=1) for m in bands], axis=1)
        low[b0:b1] = np.maximum(np.diff(stack[:, lowband], axis=0), 0).sum(axis=1)
        prev = stack[-1:]
    # frames whose window runs past the end see the cut-off as a burst of
    # every frequency, not an onset
    edge = nfft // (2 * hop)
    flux[n - edge:], low[n - edge:] = 0, 0
    env = (flux / (flux.std(axis=0) + 1e-9)).sum(axis=1)
    top = env.max()
    if top <= 0:
        raise BeatsError("no onsets: the audio is silent or constant")
    return env / top, low


def tempo(env, fps, prior_bpm=PRIOR_BPM, octaves=PRIOR_OCTAVES, lo=MIN_BPM, hi=MAX_BPM):
    """BPM from the onset strength's autocorrelation under the prior."""
    x = env - env.mean()
    size = 1 << int(np.ceil(np.log2(2 * len(x))))
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, size)) ** 2, size)[:len(x)]
    # onsets are a frame or two wide, so a period between two lags splits its
    # peak; summing three lags keeps it whole
    ac = np.convolve(ac, np.ones(3), mode="same")
    lags = np.arange(len(ac), dtype=float)
    bpm = 60.0 * fps / np.maximum(lags, 1.0)
    ok = (lags > 0) & (bpm >= lo) & (bpm <= hi)
    if not ok.any():
        raise BeatsError("too little audio for the tempo range")
    prior = np.exp(-0.5 * (np.log2(bpm / prior_bpm) / octaves) ** 2)
    k = int(np.argmax(np.where(ok, ac * prior, -np.inf)))
    if 0 < k < len(ac) - 1:                     # parabolic peak: a fraction of a frame
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b + c
        k = k + (float(np.clip(0.5 * (a - c) / den, -0.5, 0.5)) if den < 0 else 0.0)
    return 60.0 * fps / k


def track(env, fps, bpm, tightness=TIGHTNESS, trim=TRIM):
    """Beat frames: dynamic programming over the onset strength."""
    period = 60.0 * fps / bpm
    e = env / (env.std() + 1e-9)
    n = len(e)
    dist = np.arange(max(1, int(round(period / 2))), int(round(2 * period)) + 1)
    penalty = -tightness * np.log(dist / period) ** 2
    score, back = e.copy(), np.full(n, -1)
    for i in range(int(dist[0]), n):
        prev = i - dist
        ok = prev >= 0
        cand = score[prev[ok]] + penalty[ok]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[i] = e[i] + cand[j]
            back[i] = prev[ok][j]
    tail = np.arange(max(0, n - int(round(period))), n)
    i = int(tail[np.argmax(score[tail])])
    beats = []
    while i >= 0:
        beats.append(i)
        i = int(back[i])
    beats = np.array(beats[::-1])
    strength = e[beats]
    keep = np.flatnonzero(strength >= trim * np.sqrt(np.mean(strength ** 2)))
    return beats[keep[0]:keep[-1] + 1] if len(keep) else beats[:0]


def phase_strength(low, beats):
    """Mean low-band strength at the beats and halfway between them."""
    def at(frames):
        return float(np.mean([low[max(0, i - 1):i + 2].max() for i in frames]))
    return at(beats), at((beats[:-1] + beats[1:]) // 2)


def beat_map(y, prior_bpm=PRIOR_BPM):
    """(tempo, beat times in seconds, phase) for mono samples at SR. phase
    is "kept", or "low band" when the kick sat between the tracked beats
    (the off-beat lock) and the beats were tracked again on LOW_HZ."""
    env, low = onset_strength(y)
    fps = SR / HOP
    bpm = tempo(env, fps, prior_bpm)
    beats, phase = track(env, fps, bpm), "kept"
    if len(beats) > 2 and low.max() > 0:
        on, off = phase_strength(low, beats)
        if off > on * PHASE_MARGIN:
            beats, phase = track(low / low.max(), fps, bpm), "low band"
    return bpm, beats / fps, phase


def build(source, prior_bpm=PRIOR_BPM, ffmpeg=None):
    """The beat map of a music file, as the beats.json dict."""
    if not os.path.isfile(source):
        raise BeatsError(f"{source} is not a file")
    raw = cutlist.decode_pcm(source, 0.0, None, ffmpeg, timeout=trimreview.DECODE_TIMEOUT_S)
    y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if len(y) < MIN_SECONDS * SR:
        raise BeatsError(f"{source} has {len(y) / SR:.1f} s of audio; a beat map needs "
                         f"{MIN_SECONDS:g} s or more")
    bpm, beats, phase = beat_map(y, prior_bpm)
    return {"file": os.path.abspath(source), "duration_s": round(len(y) / SR, 3),
            "tempo": round(float(bpm), 2), "beats_s": [round(float(t), 3) for t in beats],
            "method": METHOD, "phase": phase, "proposal": True,
            "params": {"sr": SR, "nfft": NFFT, "hop": HOP, "gamma": GAMMA,
                       "bands_hz": list(BANDS_HZ), "bpm_range": [MIN_BPM, MAX_BPM],
                       "prior_bpm": prior_bpm, "prior_octaves": PRIOR_OCTAVES,
                       "tightness": TIGHTNESS, "trim": TRIM, "low_hz": list(LOW_HZ),
                       "phase_margin": PHASE_MARGIN}}


def write(report, path):
    """Write beats.json; refused inside any git working tree."""
    problem = paths.out_problem(path, any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    from resolve_survey import write_report  # atomic: temp file, then replace
    write_report(path, json.dumps(report, indent=2) + "\n")
    return path


def summary(report):
    b = report["beats_s"]
    span = f" from {b[0]:.2f} s to {b[-1]:.2f} s" if b else ""
    return (f"beats: {report['tempo']:g} BPM, {len(b)} beat(s){span} in "
            f"{report['duration_s']:g} s. A proposal: check it against the music by ear.")


def add_arguments(p):
    p.add_argument("music", help="a music file (any format ffmpeg reads)")
    p.add_argument("--out", required=True, help="beats.json, outside any git tree")
    p.add_argument("--prior-bpm", type=float, default=PRIOR_BPM,
                   help=f"centre of the tempo prior (default {PRIOR_BPM:g})")
    return p


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        report = build(args.music, args.prior_bpm)
        write(report, args.out)
    except (BeatsError, cutlist.CutlistError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(summary(report))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.beats",
                                 description="A beat map proposal for a music file.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
