"""
rpresolve.notes: cut on the notes a song plays, for music with no rigid pulse.
beats.py fits a tempo grid; on a song that drifts, a cut on that grid misses
the notes actually played. Three steps, offline:

    measure  decode with ffmpeg, onset peaks (local maximum within +-PEAK_FRAMES
             of beats.onset_strength, z-scored) and the beat period by
             autocorrelation inside a BPM range -> notes.json
             {period, bpm, onsets, strength, sr, hop}
    snap     for each planned beat first + k * period, the strongest onset
             within the window if its strength reaches the floor, else the
             planned beat; frames are round((note - music_in) * fps)
    check    how many cuts land within one frame of a played onset, against
             the chance level: the share of the cuts' span within one frame
             of any onset

Optional lines (JSON list of [start, end] seconds) are carried into notes.json
as timestamps only. numpy is imported inside measure(); snap and check are
plain Python.
"""

import json
import os

PEAK_FRAMES = 4       # an onset is the largest frame within this many frames either side
MIN_BPM, MAX_BPM = 105.0, 135.0
PEAK_FLOOR = 0.5      # z-score a peak needs to be listed at all
LEAD = 0.04           # music starts this long before the first planned beat
WINDOW = 0.16         # snap looks this far either side of a planned beat
FLOOR = 1.5           # z-score a note needs to win over the planned beat
FPS = 24


class NotesError(ValueError):
    pass


def measure(source, min_bpm=MIN_BPM, max_bpm=MAX_BPM, floor=PEAK_FLOOR, lines=None, ffmpeg=None):
    """The notes.json dict of an audio file."""
    import numpy as np
    from . import beats, cutlist, trimreview
    if not os.path.isfile(source):
        raise NotesError(f"{source} is not a file")
    if not 0 < min_bpm < max_bpm:
        raise NotesError("the BPM range must be positive and increasing")
    raw = cutlist.decode_pcm(source, 0.0, None, ffmpeg, timeout=trimreview.DECODE_TIMEOUT_S)
    y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    env = beats.onset_strength(y)[0]
    z = (env - env.mean()) / (env.std() + 1e-9)
    fps = beats.SR / beats.HOP
    w = PEAK_FRAMES
    pk = [i for i in range(w, len(z) - w) if z[i] == z[i - w:i + w + 1].max() and z[i] > floor]
    ac = beats.autocorr(z)
    lags = np.arange(len(ac), dtype=float)
    bpm = 60.0 * fps / np.maximum(lags, 1.0)
    ok = (lags > 0) & (bpm >= min_bpm) & (bpm <= max_bpm)
    if not ok.any():
        raise NotesError("too little audio for the BPM range")
    k = int(np.argmax(np.where(ok, ac, -np.inf)))
    if 0 < k < len(ac) - 1:                      # parabolic peak: a fraction of a frame
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b + c
        k = k + (float(np.clip(0.5 * (a - c) / den, -0.5, 0.5)) if den < 0 else 0.0)
    period = k / fps
    out = {"period": round(period, 5), "bpm": round(60.0 / period, 2),
           "onsets": [round(i / fps, 4) for i in pk],
           "strength": [round(float(z[i]), 3) for i in pk], "sr": beats.SR, "hop": beats.HOP}
    if lines is not None:
        out["lines"] = [[float(a), float(b)] for a, b in lines]
    return out


def snap(notes, beats_planned, first, fps=FPS, window=WINDOW, floor=FLOOR, lead=LEAD):
    """{music_in, cut_frames, cut_times, cut_notes}; cut_notes are seconds after music_in."""
    on, st, per = notes["onsets"], notes["strength"], notes["period"]
    music_in = first - lead
    frames, picked, cum = [0], [first], 0
    for n in beats_planned:
        cum += n
        p = first + cum * per
        near = [i for i, t in enumerate(on) if abs(t - p) < window]
        best = max(near, key=lambda i: st[i]) if near else None
        c = on[best] if best is not None and st[best] >= floor else p
        picked.append(c)
        frames.append(round((c - music_in) * fps))
        if frames[-1] <= frames[-2]:
            raise NotesError(f"cut {len(frames) - 1} lands on or before the one ahead of it: its beat is "
                             "under a frame, or two cuts took one note (pass a smaller --window)")
    return {"music_in": music_in, "cut_frames": frames, "cut_times": [f / fps for f in frames],
            "cut_notes": [g - music_in for g in picked]}


def check(notes, cuts, music_in=0.0, fps=FPS, floor=FLOOR):
    """(cuts, on a note, chance, notes used): only onsets with strength >= floor count. Cuts are seconds after music_in. A cut is on a
    note within 1/fps of an onset; chance is the share of the cuts' span (first
    to last cut) within 1/fps of any onset."""
    tol = 1.0 / fps + 1e-9
    on = sorted(t for t, z in zip(notes["onsets"], notes["strength"]) if z >= floor)
    absolute = [c + music_in for c in cuts]
    hit = sum(1 for c in absolute if any(abs(c - t) <= tol for t in on))
    lo, hi = min(absolute), max(absolute)
    covered, end = 0.0, lo
    for t in on:
        a, b = max(t - tol, lo, end), min(t + tol, hi)
        if b > a:
            covered, end = covered + (b - a), b
    return len(absolute), hit, (covered / (hi - lo) if hi > lo else 0.0), len(on)


def load(path):
    with open(path) as f:
        return json.load(f)
