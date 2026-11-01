#!/usr/bin/env python3
"""segkind: say what each Whisper segment is before anything reads it.

Whisper transcribes anything that sounds like language: singing becomes
words (copyrighted lyrics, if the song is one), wind and silence become
loops ("thank you" for a minute), and a hum becomes a sentence. Every tool
downstream trusts the text. segkind puts one question in front of all of
them: is this segment speech, sung, music, a hallucination, or can't we
tell? It does not read the words to decide; it looks at numbers.

Kinds:
    speech         keep the text
    sung           withhold the text, label [singing]
    music          withhold the text, label [music]
    hallucination  withhold the text, label [inaudible]
    uncertain      the signals disagree. Kept when speech-leaning;
                   withheld ([inaudible]) when sung-leaning, which is the
                   fail-safe: wrongly withholding speech costs one override,
                   leaking lyrics cannot be taken back.

Signals, each logged on the segment as numbers (never text):
    whisper   no_speech_prob, avg_logprob, compression_ratio, NaN, a
              zero-length segment with text, words per second, zero-length
              words, n-gram repetition, a phrase repeated across segments
    audio     voiced fraction and pitch (YIN-style, 50 ms frames), note
              plateaus, semitone quantisation, voiced-run length, harmonic
              strength, spectral flatness, envelope periodicity
    text      repetition across the clip (a chorus), long sustained words,
              low words per second. Counts and ratios only.

A sung.tsv (clip, start, end, kind; tab separated) is authoritative and
wins over the classifier in either direction (read_overrides).

Honest limits (see tools/vertcut/README.md): hallucination is detected
reliably from the Whisper numbers; singing is not detectable with simple
features across group singing outdoors, so the classifier is tuned for
recall and a human label always wins. No audio, no singing screen: the
result says so (safety.audio_checked).

Stdlib only; numpy is used for the audio signals when present. Nothing in
this module prints, logs or writes a segment's text.
"""

import json
import math
import os
import re
import subprocess
import sys

VERSION = "segkind-1"
KINDS = ("speech", "sung", "music", "hallucination", "uncertain")
NON_SPEECH = ("sung", "music", "hallucination")
LABELS = {"sung": "[singing]", "music": "[music]", "hallucination": "[inaudible]",
          "uncertain": "[inaudible]"}
LABEL_SET = frozenset(LABELS.values())

SR = 16000
HOP_S = 0.010
FRAME_S = 0.050           # pitch analysis window
MAX_LAG_HZ = 50.0         # lowest f0
MIN_LAG_HZ = 800.0        # highest f0

# Hallucination: Whisper's own compression-ratio gate, and signatures its
# loops leave in the timestamps. Calibrated on 41 field clips: loops score
# 15 to 53, real speech and singing score under 2.
COMPRESSION_MAX = 2.4
WPS_MAX = 8.0                 # words per second no speaker reaches (speech 4.5 or less)
SHORT_RATE_MAX = 10.0         # the same limit for a segment of a few words
ZERO_WORD_FRAC = 0.5
REPEAT_NGRAM_MAX = 0.6
LOOP_SEGMENTS = 3             # identical segments in a row
LOGPROB_LOW = -1.0

MIN_SEG_S = 0.4               # shorter than this: too few frames for audio features


class SegkindError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Numbers and text helpers (text is only ever counted here)
# ---------------------------------------------------------------------------

def _num(v):
    """A finite float, or None for missing, NaN and infinity."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round(v, n=3):
    return None if v is None else round(float(v), n)


_TOK = re.compile(r"\w+(?:'\w+)*", re.UNICODE)
MUSIC_GLYPHS = "\u266a\u266b\u266c\u2669\U0001f3b5\U0001f3b6"
_MUSIC_TAG = re.compile(r"^\s*[\[(][^\])]*\bmusic\b[^\])]*[\])]\s*$", re.I)


def _tokens(text):
    """Word tokens of any script, lower-cased, apostrophes straightened."""
    text = (text or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    return [t.replace("_", "") for t in _TOK.findall(text) if t.replace("_", "")]


def music_marked(text):
    """True when Whisper itself marked the segment as music: a music glyph
    (it wraps what it takes for lyrics in them) or a bracketed music tag."""
    text = text or ""
    return any(g in text for g in MUSIC_GLYPHS) or bool(_MUSIC_TAG.match(text))


def _is_label(text):
    return (text or "").strip() in LABEL_SET


# ---------------------------------------------------------------------------
# Audio: decode, frame signals, per-segment features (numpy only)
# ---------------------------------------------------------------------------

def _np():
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def load_audio(path):
    """Mono 16 kHz float32 samples in [-1, 1) from a 16 kHz mono 16-bit wav
    (read directly) or any media ffmpeg can decode. None when numpy or the
    decode is unavailable: the caller reports that no audio screen ran."""
    np = _np()
    if np is None or not path or not os.path.isfile(path):
        return None
    if path.lower().endswith(".wav"):
        import wave
        try:
            with wave.open(path, "rb") as w:
                if w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SR:
                    raw = w.readframes(w.getnframes())
                    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        except (wave.Error, EOFError, OSError):
            pass
    ffmpeg = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")   # the toolkit's one variable
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-vn",
                            "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                           capture_output=True, timeout=3600)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


class Analysis:
    """Frame-level signals for one audio stream, 10 ms apart. Frame i is
    centred at off + i * HOP_S seconds."""

    def __init__(self, f0, ap, db, flat, off, dur):
        self.f0, self.ap, self.db, self.flat = f0, ap, db, flat
        self.off, self.dur, self.n = off, dur, len(f0)
        self.floor_db = None
        self.voiced = None


BAND_HZ = (70.0, 1500.0)   # pitch is tracked inside the voice's own band: wind below, hiss above


def _bandpass(x, sr, lo=BAND_HZ[0], hi=BAND_HZ[1], taps=401):
    """Zero-phase FIR band-pass by overlap-add FFT convolution, in float32.
    Wind rumble below and hiss above cost the pitch tracker most of its
    signal-to-noise; the voice's harmonics live in between."""
    np = _np()
    n = np.arange(taps) - (taps - 1) / 2.0

    def sinc_lp(fc):
        return 2 * fc / sr * np.sinc(2 * fc / sr * n)

    h = (sinc_lp(hi) - sinc_lp(lo)) * np.hamming(taps)
    block = 1 << 16
    nfft = 1 << 17
    H = np.fft.rfft(h, nfft)
    out = np.zeros(len(x) + taps - 1, dtype=np.float32)
    for i in range(0, len(x), block):
        seg = x[i:i + block].astype(np.float64)
        y = np.fft.irfft(np.fft.rfft(seg, nfft) * H, nfft)[:len(seg) + taps - 1]
        out[i:i + len(y)] += y.astype(np.float32)
    d = (taps - 1) // 2
    return out[d:d + len(x)]


def analyze(x, sr=SR):
    """Pitch (YIN-style cumulative-mean-normalised difference over FFT),
    aperiodicity, energy and spectral flatness per 10 ms frame."""
    np = _np()
    if np is None:
        raise SegkindError("numpy is required for the audio signals")
    hop = int(round(sr * HOP_S))
    win = int(round(sr * 0.030))
    maxlag = int(sr / MAX_LAG_HZ)
    minlag = int(sr / MIN_LAG_HZ)
    flen = win + maxlag
    nfft = 1024
    x = np.asarray(x, dtype=np.float32)
    if len(x) < flen + hop:
        x = np.concatenate([x, np.zeros(flen + hop - len(x), dtype=np.float32)])
    y = _bandpass(x, sr)
    n = 1 + (len(x) - flen) // hop
    f0 = np.zeros(n, dtype=np.float32)
    ap = np.ones(n, dtype=np.float32)
    flat = np.zeros(n, dtype=np.float32)
    # frame energy from a running sum, no frame matrix needed
    cs = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    starts_all = np.arange(n) * hop
    ex = (cs[starts_all + flen] - cs[starts_all]) / flen
    db = (10 * np.log10(ex + 1e-12)).astype(np.float32)
    lagv = np.arange(1, maxlag + 1, dtype=np.float32)
    ar12 = np.arange(12)
    hann = np.hanning(512).astype(np.float32)
    freqs = np.fft.rfftfreq(512, 1.0 / sr)
    band = (freqs >= 100) & (freqs <= 4000)
    for b0 in range(0, n, 2048):
        b1 = min(n, b0 + 2048)
        st = starts_all[b0:b1]
        idx = st[:, None] + np.arange(flen)[None, :]
        X = y[idx]
        A = np.fft.rfft(X[:, :win], nfft, axis=1)
        B = np.fft.rfft(X, nfft, axis=1)
        cross = np.fft.irfft(np.conj(A) * B, nfft, axis=1)[:, :maxlag + 1]
        sq = X.astype(np.float64) ** 2
        c2 = np.concatenate([np.zeros((len(X), 1)), np.cumsum(sq, axis=1)], axis=1)
        e1 = c2[:, win]
        e2 = c2[:, win:win + maxlag + 1] - c2[:, :maxlag + 1]
        d = e1[:, None] + e2 - 2.0 * cross
        d = np.maximum(d, 0.0)
        cum = np.cumsum(d[:, 1:], axis=1)
        dn = np.ones_like(d)
        dn[:, 1:] = d[:, 1:] * lagv[None, :] / (cum + 1e-12)
        rng = dn[:, minlag:]
        dm = rng.min(axis=1)
        first = (rng <= dm[:, None] + 0.08).argmax(axis=1) + minlag
        sel = np.minimum(first[:, None] + ar12[None, :], maxlag)
        loc = np.take_along_axis(dn, sel, axis=1)
        tau = sel[np.arange(len(sel)), loc.argmin(axis=1)]
        rows = np.arange(len(tau))
        t = np.clip(tau, 1, maxlag - 1)
        a_, b_, c_ = dn[rows, t - 1], dn[rows, t], dn[rows, t + 1]
        den = a_ - 2 * b_ + c_
        with np.errstate(divide="ignore", invalid="ignore"):
            off = np.where(np.abs(den) > 1e-9, 0.5 * (a_ - c_) / den, 0.0)
        off = np.nan_to_num(off)
        f0[b0:b1] = sr / (t + np.clip(off, -1, 1))
        ap[b0:b1] = dn[rows, tau]
        # spectral flatness on the same 10 ms grid, 32 ms windows
        Xs = x[st[:, None] + np.arange(512)[None, :]] * hann[None, :]
        P = np.abs(np.fft.rfft(Xs, axis=1)) ** 2 + 1e-12
        Pb = P[:, band]
        flat[b0:b1] = np.exp(np.log(Pb).mean(axis=1)) / Pb.mean(axis=1)
    off_s = flen / 2.0 / sr
    return Analysis(f0, ap, db, flat, off_s, len(x) / float(sr))


AP_VOICED = 0.35          # aperiodicity below this counts as a pitched frame
PLATEAU_CENTS = 60.0      # a note: f0 stays this close to its running median ...
PLATEAU_MIN_S = 0.25      # ... for at least this long
RUN_GAP_FRAMES = 3        # gaps this short do not end a voiced run


def finish(a):
    """Set the noise floor and voiced mask on an Analysis: a frame is
    pitched when it is periodic enough and clearly above the clip's quiet."""
    np = _np()
    a.floor_db = float(max(-72.0, np.percentile(a.db, 10) + 3.0))
    a.voiced = (a.ap < AP_VOICED) & (a.db > a.floor_db) & (a.f0 > MAX_LAG_HZ) & (a.f0 < MIN_LAG_HZ)
    return a


def _plateaus(cents, voiced):
    """[(i, j)] frame runs (j exclusive) where f0 holds near one pitch: a
    sung note. Speech glides, so its voiced runs rarely hold for 200 ms."""
    min_n = int(round(PLATEAU_MIN_S / HOP_S))
    out, n, i = [], len(cents), 0
    while i < n:
        if not voiced[i]:
            i += 1
            continue
        j, recent = i + 1, [cents[i]]
        while j < n and voiced[j]:
            ref = sorted(recent[-15:])[len(recent[-15:]) // 2]
            if abs(cents[j] - ref) > PLATEAU_CENTS:
                break
            recent.append(cents[j])
            j += 1
        if j - i >= min_n:
            out.append((i, j))
        i = max(j, i + 1)
    return out


def _runs(mask, gap=RUN_GAP_FRAMES):
    """[(i, j)] runs of True in mask, bridging gaps of at most `gap` frames."""
    out, n, i = [], len(mask), 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j, last = i, i
        while j < n and (mask[j] or j - last <= gap):
            if mask[j]:
                last = j
            j += 1
        out.append((i, last + 1))
        i = last + 1
    return out


def _pct(v, q):
    np = _np()
    return float(np.percentile(v, q)) if len(v) else None


def audio_features(a, t0, t1):
    """Numbers describing the audio of [t0, t1) seconds, or None when the
    window is too short for them to mean anything."""
    np = _np()
    i0 = max(0, int(round((t0 - a.off) / HOP_S)))
    i1 = min(a.n, int(round((t1 - a.off) / HOP_S)) + 1)
    n = i1 - i0
    if n < int(MIN_SEG_S / HOP_S):
        return None
    voiced = a.voiced[i0:i1]
    f0 = a.f0[i0:i1]
    nv = int(voiced.sum())
    feats = {"frames": n, "voiced_frac": nv / float(n), "voiced_s": nv * HOP_S, "db_med": float(np.median(a.db[i0:i1])),
             "db_range": float(np.percentile(a.db[i0:i1], 90) - np.percentile(a.db[i0:i1], 10)),
             "flat_med": float(np.median(a.flat[i0:i1])),
             "ap_med": None, "plateau_frac": None, "plateau_longest_s": None,
             "plateau_notes_per_s": None, "quant_R": None, "quant_n": 0,
             "run_med_s": None, "run_p90_s": None, "steady_frac": None, "beat_ac": None}
    # envelope periodicity: how regular the loudness pulses are
    env = 10 ** (a.db[i0:i1] / 20.0)
    env = env - env.mean()
    if n >= 150 and env.std() > 1e-9:
        sm = np.convolve(env, np.ones(5) / 5.0, mode="same")
        ac = np.correlate(sm, sm, mode="full")[len(sm) - 1:]
        ac = ac / (ac[0] + 1e-12)
        lo, hi = int(0.25 / HOP_S), min(len(ac) - 1, int(1.0 / HOP_S))
        if hi > lo:
            feats["beat_ac"] = float(ac[lo:hi + 1].max())
    if nv < 20:
        return feats
    cents = np.where(voiced, 1200.0 * np.log2(np.maximum(f0, 1.0) / 110.0), 0.0)
    feats["ap_med"] = float(np.median(a.ap[i0:i1][voiced]))
    runs = [(i, j) for i, j in _runs(voiced) if j - i >= 5]
    if runs:
        lens = np.array([(j - i) * HOP_S for i, j in runs])
        feats["run_med_s"] = float(np.median(lens))
        feats["run_p90_s"] = float(np.percentile(lens, 90))
    pl = _plateaus(cents.tolist(), voiced.tolist())
    pf = sum(j - i for i, j in pl)
    feats["plateau_frac"] = pf / float(nv)
    feats["plateau_longest_s"] = max([(j - i) * HOP_S for i, j in pl], default=0.0)
    feats["plateau_notes_per_s"] = len(pl) / (n * HOP_S)
    # steady: frame pairs where pitch moves under 15 cents
    pair = voiced[1:] & voiced[:-1]
    if pair.sum() >= 20:
        feats["steady_frac"] = float((np.abs(np.diff(cents))[pair] < 15.0).mean())
    # quantisation: do the notes sit on a semitone grid? Rayleigh length of
    # the semitone phase of each plateau's median pitch.
    if len(pl) >= 4:
        med = np.array([np.median(cents[i:j]) for i, j in pl])
        ph = 2 * np.pi * ((med / 100.0) % 1.0)
        R = float(np.hypot(np.cos(ph).mean(), np.sin(ph).mean()))
        feats["quant_R"], feats["quant_n"] = R, len(pl)
    return feats


# ---------------------------------------------------------------------------
# Whisper and text signals. Counts and ratios only: the words themselves are
# tokenised to be counted and are never kept, printed or logged.
# ---------------------------------------------------------------------------

LONG_WORD_S = 0.8             # a word held this long is a sustained note, or a stretched alignment
MIN_WORDS_RATE_S = 0.3        # no words-per-second for segments shorter than this
CHORUS_JACCARD = 0.6
CHORUS_MIN_TOKENS = 4
CHORUS_MAX_SEGMENTS = 3000    # the pairwise scan is quadratic


def _seg_words(seg):
    out = []
    for w in seg.get("words") or []:
        s, e = _num(w.get("start")), _num(w.get("end"))
        if s is not None and e is not None:
            out.append((s, e, _num(w.get("probability"))))
    return out


def _longest_run(toks):
    best = run = 0
    prev = None
    for t in toks:
        run = run + 1 if t == prev else 1
        prev = t
        best = max(best, run)
    return best


def _ngram_repeat(toks, n=3):
    """Share of n-grams that repeat an earlier one: 0 for ordinary
    speech, near 1 for a loop. Zero when there are too few tokens."""
    grams = [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]
    if len(grams) < 4:
        return 0.0
    return 1.0 - len(set(grams)) / float(len(grams))


def _zratio(text):
    """Whisper's own compression ratio, on one segment's text."""
    import zlib
    raw = (text or "").encode("utf-8")
    if len(raw) < 30:
        return None
    return len(raw) / float(len(zlib.compress(raw)))


def seg_text(seg):
    """The segment's text, counted and never shown: its text field, or the
    words joined when a JSON has no text field (a hand-made or non-Whisper
    export), so a missing field is not a way around the screen."""
    text = seg.get("text")
    if text is None:
        text = " ".join(str(w.get("word") or w.get("text") or "") for w in seg.get("words") or [])
    return text if isinstance(text, str) else ""


def seg_features(seg):
    """The Whisper-side and text-side numbers of one segment."""
    start, end = _num(seg.get("start")), _num(seg.get("end"))
    text = seg_text(seg)
    toks = _tokens(text)
    words = _seg_words(seg)
    dur = (end - start) if (start is not None and end is not None) else None
    f = {"start": start, "end": end, "dur": dur, "n_tok": len(toks), "n_words": len(words),
         "no_speech_prob": _num(seg.get("no_speech_prob")),
         "avg_logprob": _num(seg.get("avg_logprob")),
         "nan_logprob": "avg_logprob" in seg and _num(seg.get("avg_logprob")) is None,
         "compression_ratio": _num(seg.get("compression_ratio")),
         "zratio": _zratio(text), "seek": seg.get("seek"),
         "wps": (len(toks) / dur) if (dur is not None and dur >= MIN_WORDS_RATE_S) else None,
         "zero_dur_text": bool(toks) and dur is not None and dur <= 0.001,
         "music_marked": music_marked(text),
         "rate": (len(toks) / max(dur, 0.001)) if (dur is not None and toks) else None,
         "run1": _longest_run(toks), "rep3": _ngram_repeat(toks)}
    if words:
        durs = sorted(e - s for s, e, _ in words)
        probs = sorted(p for _, _, p in words if p is not None)
        f["zero_word_frac"] = sum(1 for d in durs if d <= 0.001) / float(len(durs))
        f["word_max_s"] = durs[-1]
        f["word_p90_s"] = durs[min(len(durs) - 1, int(0.9 * len(durs)))]
        f["long_word_frac"] = sum(1 for d in durs if d >= LONG_WORD_S) / float(len(durs))
        f["prob_med"] = probs[len(probs) // 2] if probs else None
    else:
        f.update({"zero_word_frac": None, "word_max_s": None, "word_p90_s": None,
                  "long_word_frac": None, "prob_med": None})
    return f


def cross_features(segs, feats):
    """Signals that need the other segments: a phrase repeated back to back
    (a Whisper loop) and lines that recur across the clip (a chorus)."""
    keys = [tuple(_tokens(seg_text(s))) for s in segs]
    n = len(segs)
    loop = [0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and keys[j + 1] == keys[i] and keys[i]:
            j += 1
        if keys[i] and j - i + 1 >= LOOP_SEGMENTS:
            for k in range(i, j + 1):
                loop[k] = j - i + 1
        i = j + 1
    chorus = [0.0] * n
    if n <= CHORUS_MAX_SEGMENTS:
        sets = [set(k) if len(k) >= CHORUS_MIN_TOKENS else None for k in keys]
        for i in range(n):
            if sets[i] is None:
                continue
            for j in range(n):
                if i != j and sets[j] is not None:
                    jac = len(sets[i] & sets[j]) / float(len(sets[i] | sets[j]))
                    if jac > chorus[i]:
                        chorus[i] = jac
    for k, f in enumerate(feats):
        f["loop_run"] = loop[k]
        f["chorus_jaccard"] = chorus[k]
    return feats


def _ramp(v, lo, hi):
    """0 at or below lo, 1 at or above hi, linear between; 0 for None."""
    if v is None:
        return 0.0
    if hi == lo:
        return 1.0 if v >= hi else 0.0
    return max(0.0, min(1.0, (v - lo) / float(hi - lo)))


def _noisy_or(parts):
    """Combine independent evidence: 1 - prod(1 - weight * strength)."""
    p = 1.0
    for strength, weight in parts:
        p *= 1.0 - weight * max(0.0, min(1.0, strength))
    return 1.0 - p


# ---------------------------------------------------------------------------
# Scores and the decision
# ---------------------------------------------------------------------------

T_SUNG = 0.60     # at or above: sung or music
T_LEAN = 0.30     # at or above: uncertain, and the leaning decides
T_WATCH = 0.15    # at or above: uncertain, leaning speech (kept)
MIN_VOICED_S = 1.0  # pitched audio needed before pitch features carry full weight
BLEED_GAP_S = 1.5  # a song runs across segments: neighbours of a sung one are not trusted


def hallucination_score(f):
    """(score 0..1, reasons). A strong signal alone gives 1.0."""
    strong, weak = [], []
    cr = f["compression_ratio"]
    zw = f["zero_word_frac"] or 0.0
    if f["zero_dur_text"]:
        strong.append("zero_duration_text")
    if f["n_words"] >= 2 and (zw >= 0.8 or (zw >= ZERO_WORD_FRAC and (f["dur"] or 0) < 0.5)):
        strong.append("zero_length_words")
    elif f["n_words"] >= 2 and zw >= ZERO_WORD_FRAC:
        weak.append(("zero_length_words", 0.4))
    if f["rate"] is not None and ((f["n_tok"] >= 8 and f["rate"] >= WPS_MAX)
                                  or (f["n_tok"] >= 2 and f["rate"] >= SHORT_RATE_MAX)):
        strong.append("words_per_second")
    if f["run1"] >= 5 or (f["rep3"] >= REPEAT_NGRAM_MAX and f["n_tok"] >= 6):
        strong.append("repeated_tokens")
    if f["loop_run"] >= LOOP_SEGMENTS:
        strong.append("repeated_segments")
    if cr is not None and cr >= COMPRESSION_MAX:
        # Whisper's own gate: it reports the ratio per 30 s window, so a real
        # sentence next to a loop carries it too. Withheld all the same; the
        # window needs re-transcribing before anyone trusts it.
        strong.append("compression_ratio")
    if f["nan_logprob"]:
        weak.append(("nan_logprob", 0.5))
    if f["avg_logprob"] is not None and f["avg_logprob"] < LOGPROB_LOW:
        weak.append(("low_logprob", 0.4))
    if f["no_speech_prob"] is not None and f["no_speech_prob"] >= 0.6:
        weak.append(("no_speech_prob", 0.5))
    if f["prob_med"] is not None and f["prob_med"] < 0.3 and f["n_words"] >= 3:
        weak.append(("low_word_probability", 0.25))
    reasons = strong + [r for r, _ in weak]
    if strong:
        return 1.0, reasons
    return _noisy_or([(1.0, w) for _, w in weak]), reasons


def sung_score(f, a, held):
    """(score 0..1, reasons) that a segment is sung or music, from audio
    features `a` (None when there was no audio or the segment is too short),
    the text signals in `f`, and `held`: the longest word under which the
    audio is pitched, in seconds (None without audio)."""
    parts, reasons = [], []

    def add(name, strength, weight):
        if strength > 0.0:
            reasons.append(name)
        parts.append((strength, weight))

    if a is not None and a.get("plateau_frac") is not None:
        # a few pitched frames prove nothing: weigh by how much was pitched
        w = min(1.0, a["voiced_s"] / MIN_VOICED_S)
        add("held_pitch_plateaus", w * _ramp(a["plateau_frac"], 0.30, 0.65), 0.90)
        add("long_note", w * _ramp(a["plateau_longest_s"], 0.50, 1.00), 0.60)
        if a["quant_R"] is not None:
            add("semitone_quantised", _ramp(a["quant_n"] * a["quant_R"] ** 2, 3.0, 9.0), 0.50)
        if a["voiced_s"] >= MIN_VOICED_S and a["steady_frac"] is not None:
            add("steady_pitch", _ramp(a["steady_frac"], 0.75, 0.95), 0.30)
    if a is not None and a.get("beat_ac") is not None:
        add("regular_pulse", _ramp(a["beat_ac"], 0.55, 0.85), 0.20)
    if held is not None:
        add("held_word_pitched", _ramp(held, LONG_WORD_S, 2.0), 0.50)
    elif f["word_max_s"] is not None:
        add("held_word_unverified", _ramp(f["word_max_s"], 1.0, 3.0), 0.15)
    add("repeated_lines", _ramp(f["chorus_jaccard"], CHORUS_JACCARD, 0.9), 0.25)
    return _noisy_or(parts), reasons


T_HALLU = 0.60
UNCERTAIN_LEAN, UNCERTAIN_WITHHOLD, UNCERTAIN_KEEP = "lean", "withhold", "keep"
UNCERTAIN_POLICIES = (UNCERTAIN_LEAN, UNCERTAIN_WITHHOLD, UNCERTAIN_KEEP)


def held_pitched(a, seg):
    """Longest word of at least LONG_WORD_S whose audio is pitched for half
    of its frames, in seconds; 0.0 when there is none."""
    best = 0.0
    for s, e, _ in _seg_words(seg):
        if e - s < LONG_WORD_S:
            continue
        i0 = max(0, int(round((s - a.off) / HOP_S)))
        i1 = min(a.n, int(round((e - a.off) / HOP_S)) + 1)
        if i1 - i0 >= 10 and a.voiced[i0:i1].mean() >= 0.5:
            best = max(best, e - s)
    return best


def withheld(kind, lean, policy=UNCERTAIN_LEAN):
    """True when the text of a segment of this kind must not be shown."""
    if kind in NON_SPEECH:
        return True
    if kind == "uncertain":
        if policy == UNCERTAIN_WITHHOLD:
            return True
        if policy == UNCERTAIN_KEEP:
            return False
        return lean == "sung"
    return False


def _decide(h, s, f):
    """(kind, lean) from the two scores."""
    if h >= T_HALLU:
        return "hallucination", None
    if s >= T_SUNG:
        return ("music" if (f["n_tok"] == 0 or (f["wps"] is not None and f["wps"] < 1.0))
                else "sung"), None
    top = max(h, s)
    if top >= T_LEAN:
        return "uncertain", ("hallucination" if h >= s else "sung")
    if top >= T_WATCH:
        return "uncertain", "speech"
    return "speech", None


def is_safe(data):
    """True for a JSON this module already wrote: it carries a safety block
    and per-segment kinds, and withheld text is gone."""
    return (isinstance(data, dict) and isinstance(data.get("safety"), dict)
            and bool(data["safety"].get("version")))


def _result(i, seg, kind, lean, reasons, scores, source, policy, feats=None):
    wh = withheld(kind, lean, policy)
    return {"index": i, "start": _num(seg.get("start")), "end": _num(seg.get("end")),
            "kind": kind, "lean": lean, "withheld": wh,
            "label": (LABELS[kind] if wh else None), "source": source,
            "reasons": list(reasons), "scores": scores, "features": feats or {}}


def classify(data, analysis=None, overrides=None, clip=None, policy=UNCERTAIN_LEAN):
    """One result per segment of a Whisper JSON: {index, start, end, kind,
    lean, withheld, label, source, reasons, scores, features}. `analysis` is
    an Analysis (after finish) or None, and None means no singing screen
    from audio. `overrides` is a list from read_overrides, authoritative in
    either direction. A JSON this module already wrote keeps its kinds."""
    segs = data.get("segments") or []
    if is_safe(data):
        res = []
        for i, seg in enumerate(segs):
            kind = seg.get("kind") if seg.get("kind") in KINDS else "speech"
            ev = seg.get("kind_evidence") or {}
            lean = ev.get("lean")
            was = bool(seg.get("redacted"))
            r = _result(i, seg, kind, lean, ev.get("reasons") or [], ev.get("scores") or {},
                        ev.get("source") or "classifier", policy)
            r["empty"] = (ev.get("reasons") or []) == ["empty"]
            r["withheld"] = was or r["withheld"]
            r["label"] = seg.get("label") or (LABELS[kind] if r["withheld"] else None)
            r["carried"] = True
            res.append(r)
        return apply_overrides(res, segs, overrides, clip)
    feats = cross_features(segs, [seg_features(s) for s in segs])
    res = []
    for i, (seg, f) in enumerate(zip(segs, feats)):
        if not seg_text(seg).strip() and not f["n_words"]:
            r = _result(i, seg, "speech", None, ["empty"], {"hallucination": 0.0, "sung": 0.0},
                        "classifier", policy)
            r["empty"] = True
            res.append(r)
            continue
        if f["music_marked"]:
            res.append(_result(i, seg, "music", None, ["whisper_music_marker"],
                               {"hallucination": 0.0, "sung": 1.0}, "classifier", policy))
            continue
        a = held = None
        if analysis is not None and f["dur"] is not None and f["dur"] >= MIN_SEG_S:
            a = audio_features(analysis, f["start"], f["end"])
            held = held_pitched(analysis, seg)
        h, hr = hallucination_score(f)
        s, sr = sung_score(f, a, held)
        kind, lean = _decide(h, s, f)
        reasons = (hr if h >= s else sr) if kind != "speech" else []
        if kind == "uncertain" and not reasons:
            reasons = hr + sr
        flat = dict(f)
        flat.pop("music_marked", None)
        if a:
            flat.update({"audio_" + k: v for k, v in a.items()})
        flat["held_pitched_s"] = held
        flat = {k: _round(v) if isinstance(v, float) else v for k, v in flat.items()
                if isinstance(v, (int, float, bool, type(None)))}
        res.append(_result(i, seg, kind, lean, reasons,
                           {"hallucination": _round(h), "sung": _round(s)}, "classifier",
                           policy, flat))
    _propagate(res, policy)
    return apply_overrides(res, segs, overrides, clip)


def _propagate(res, policy):
    """A song runs across Whisper segments, and a segment next to one we are
    sure is sung is not to be trusted: within BLEED_GAP_S of a sung or music
    segment (or of a withheld run of them), a speech-leaning segment becomes
    uncertain and sung-leaning. One segment is taken on each side of a run."""
    def sungish(r):
        return r["kind"] in ("sung", "music") or (r["kind"] == "uncertain"
                                                   and r["lean"] == "sung")

    def speechish(r):
        return r["kind"] == "speech" or (r["kind"] == "uncertain" and r["lean"] == "speech")

    n = len(res)
    for step in (1, -1):
        rng = range(n) if step == 1 else range(n - 1, -1, -1)
        for i in rng:
            if not sungish(res[i]) or res[i].get("bled"):
                continue
            prev, j = i, i + step
            while 0 <= j < n:
                a, b = (res[prev], res[j]) if step == 1 else (res[j], res[prev])
                if a["end"] is None or b["start"] is None or b["start"] - a["end"] > BLEED_GAP_S:
                    break
                r = res[j]
                if speechish(r):
                    r.update({"kind": "uncertain", "lean": "sung", "bled": True,
                              "withheld": withheld("uncertain", "sung", policy),
                              "label": LABELS["uncertain"], "reasons": ["next_to_sung_segment"]})
                    break
                if sungish(r) and not r.get("bled"):
                    prev, j = j, j + step
                    continue
                break


# ---------------------------------------------------------------------------
# sung.tsv: the human's word is final
# ---------------------------------------------------------------------------

OVERRIDE_KINDS = ("speech", "sung", "music", "hallucination")
OVERRIDE_NONSPEECH_FRAC = 0.25   # a withhold label covers a segment from this share ...
OVERRIDE_NONSPEECH_S = 1.0       # ... or from this many seconds of overlap
OVERRIDE_SPEECH_FRAC = 0.75      # a speech label must cover most of the segment


def parse_time(text):
    """Seconds from SS(.mmm), MM:SS(.mmm) or HH:MM:SS(.mmm)."""
    parts = (text or "").strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SegkindError("not a time: %r" % (text,))


def read_overrides(path):
    """[{clip, start, end, kind, line}] from a sung.tsv: clip<TAB>start<TAB>
    end<TAB>kind, '#' comments and blank lines ignored, an optional header
    row. clip is a clip name (the media file's name without extension) or *.
    kind is speech, sung, music or hallucination. The file is authoritative:
    it wins over the classifier, in either direction."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) >= 4 and cols[0].lower() == "clip" and cols[3].lower() == "kind":
                continue
            if len(cols) < 4:
                raise SegkindError("%s:%d needs clip<TAB>start<TAB>end<TAB>kind" % (path, n))
            try:
                start, end = parse_time(cols[1]), parse_time(cols[2])
            except SegkindError as e:
                raise SegkindError("%s:%d %s" % (path, n, e))
            kind = cols[3].lower()
            if kind not in OVERRIDE_KINDS:
                raise SegkindError("%s:%d kind must be one of %s" % (path, n,
                                                                      ", ".join(OVERRIDE_KINDS)))
            if end <= start:
                raise SegkindError("%s:%d end is not after start" % (path, n))
            rows.append({"clip": cols[0], "start": start, "end": end, "kind": kind, "line": n})
    return rows


def apply_overrides(results, segs, overrides, clip):
    """Set the kind of every segment an override covers. A withhold label
    (sung, music, hallucination) covers a segment it overlaps by a quarter
    of the segment or a second; a speech label must cover three quarters.
    If both apply, withholding wins: that is the fail-safe side. Segments
    from an already-safe JSON have no text left to restore, so a speech
    label cannot bring them back; they are marked promotion_blocked."""
    if not overrides:
        return results
    mine = [o for o in overrides if o["clip"] in ("*", clip)]
    for r in results:
        s, e = r["start"], r["end"]
        if s is None or e is None:
            continue
        dur = max(e - s, 0.0)
        best_non = best_sp = None
        for o in mine:
            ov = min(e, o["end"]) - max(s, o["start"])
            if dur <= 0.0:
                ov = 1.0 if o["start"] <= s <= o["end"] else -1.0
                frac = 1.0 if ov > 0 else 0.0
            else:
                frac = ov / dur
            if o["kind"] == "speech":
                if frac >= OVERRIDE_SPEECH_FRAC and (best_sp is None or ov > best_sp[0]):
                    best_sp = (ov, o)
            elif frac >= OVERRIDE_NONSPEECH_FRAC or ov >= OVERRIDE_NONSPEECH_S:
                if best_non is None or ov > best_non[0]:
                    best_non = (ov, o)
        pick = best_non or best_sp
        if pick is None:
            continue
        o = pick[1]
        if o["kind"] == "speech" and r.get("carried") and r["withheld"]:
            r["promotion_blocked"] = True
            continue
        was = (r["kind"], r["withheld"])
        r.update({"kind": o["kind"], "lean": None, "source": "override",
                  "withheld": o["kind"] in NON_SPEECH, "reasons": ["override"],
                  "override_line": o["line"], "was_kind": was[0], "was_withheld": was[1]})
        r["label"] = LABELS[o["kind"]] if r["withheld"] else None
    return results


# ---------------------------------------------------------------------------
# Summaries and the safe JSON
# ---------------------------------------------------------------------------

def summarize(results):
    """Counts and seconds, numbers only."""
    counts = {k: 0 for k in KINDS}
    secs = 0.0
    nwith = 0
    ov = {"applied": 0, "promoted_to_speech": 0, "blocked": 0}
    empty = 0
    for r in results:
        if r.get("empty"):
            empty += 1          # nothing to show, nothing to withhold
            continue
        counts[r["kind"]] += 1
        if r["withheld"]:
            nwith += 1
            if r["start"] is not None and r["end"] is not None:
                secs += max(0.0, r["end"] - r["start"])
        if r["source"] == "override":
            ov["applied"] += 1
            if r["kind"] == "speech" and r.get("was_withheld"):
                ov["promoted_to_speech"] += 1
        if r.get("promotion_blocked"):
            ov["blocked"] += 1
    return {"counts": counts, "empty_segments": empty, "withheld_segments": nwith,
            "withheld_seconds": round(secs, 2), "overrides": ov}


def ranges(results, gap=1.0):
    """Adjacent segments of one kind and one withheld state merged into
    {start, end, kind, withheld, segments} ranges, in order."""
    out = []
    for r in results:
        if r["start"] is None or r["end"] is None or r.get("empty"):
            continue
        last = out[-1] if out else None
        if (last and last["kind"] == r["kind"] and last["withheld"] == r["withheld"]
                and r["start"] - last["end"] <= gap):
            last["end"] = max(last["end"], r["end"])
            last["segments"] += 1
        else:
            out.append({"start": r["start"], "end": r["end"], "kind": r["kind"],
                        "withheld": r["withheld"], "segments": 1})
    return out


def _clean(v):
    """JSON-safe copy: NaN and infinity become null (a strict parser, jq
    for one, rejects the literal NaN Whisper can write)."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


SEG_KEEP_WITHHELD = ("id", "seek", "start", "end", "temperature", "avg_logprob",
                     "compression_ratio", "no_speech_prob")


def redact(data, results, audio_checked, policy=UNCERTAIN_LEAN, clip=None):
    """The safe JSON: every segment keeps its timestamps and gains kind,
    withheld and kind_evidence (numbers and reason codes). A withheld
    segment's text becomes its label, and its words and tokens are
    dropped. Speech and speech-leaning text is kept. The top-level text is
    rebuilt from what is left, and a safety block records the classifier
    version and the counts."""
    segs = data.get("segments") or []
    out_segs, texts = [], []
    for seg, r in zip(segs, results):
        ev = {"source": r["source"], "reasons": r["reasons"], "scores": r["scores"]}
        if r["lean"]:
            ev["lean"] = r["lean"]
        if r.get("override_line"):
            ev["override_line"] = r["override_line"]
        if r["withheld"]:
            o = {k: seg[k] for k in SEG_KEEP_WITHHELD if k in seg}
            o.update({"text": r["label"], "words": [], "kind": r["kind"],
                      "label": r["label"], "kind_evidence": ev})
            texts.append(r["label"])
        else:
            o = dict(seg)
            o.update({"kind": r["kind"], "kind_evidence": ev})
            texts.append((seg.get("text") or "").strip())
        o["redacted"] = bool(r["withheld"])
        out_segs.append(_clean(o))
    collapsed = []
    for t in texts:
        if t and not (t in LABEL_SET and collapsed and collapsed[-1] == t):
            collapsed.append(t)
    sm = summarize(results)
    safety = {"version": VERSION, "audio_checked": bool(audio_checked), "uncertain": policy,
              "counts": sm["counts"], "withheld_segments": sm["withheld_segments"],
              "withheld_seconds": sm["withheld_seconds"], "overrides": sm["overrides"],
              "note": ("withheld text was replaced by its label; timestamps and kinds are "
                       "kept. Inspect a clip with `vertcut kinds`, which prints no text.")}
    if not audio_checked:
        safety["warning"] = "no audio: singing was not screened, only the Whisper numbers were"
    out = {"text": " ".join(collapsed), "segments": out_segs, "safety": safety}
    if "language" in data:
        out["language"] = data["language"]
    return out


def sidecar(results, clip, duration, audio_checked):
    """<clip>.kinds.json: time ranges and kinds, no text. Other tools read
    it to warn about withheld spans without opening the transcript."""
    sm = summarize(results)
    return {"version": VERSION, "clip": clip, "duration": _round(duration, 2),
            "audio_checked": bool(audio_checked),
            "spans": [{"start": _round(r["start"], 3), "end": _round(r["end"], 3),
                       "kind": r["kind"], "withheld": r["withheld"]}
                      for r in ranges(results, gap=0.5)],
            "counts": sm["counts"], "withheld_segments": sm["withheld_segments"],
            "withheld_seconds": sm["withheld_seconds"]}


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)    # accepts NaN, which Whisper can write


def dump_json(obj, path):
    """Write strict JSON atomically."""
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_clean(obj), fh, ensure_ascii=False, allow_nan=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def clip_name(path):
    """The clip name for overrides and sidecars: the file name without its
    extension, and without a .raw or .kinds suffix."""
    base = os.path.splitext(os.path.basename(path))[0]
    for suf in (".raw", ".kinds"):
        if base.endswith(suf):
            base = base[:-len(suf)]
    return base


MEDIA_EXT = (".wav", ".mov", ".mp4", ".m4a", ".mp3", ".mkv", ".aiff", ".aif", ".flac", ".mxf")


def sibling_media(json_path):
    """The media file beside a transcript, by name, or None."""
    d = os.path.dirname(os.path.abspath(json_path))
    stem = clip_name(json_path)
    for ext in MEDIA_EXT:
        for cand in (stem + ext, stem + ext.upper()):
            p = os.path.join(d, cand)
            if os.path.isfile(p):
                return p
    return None


def screen(data, audio_path=None, overrides=None, clip=None, policy=UNCERTAIN_LEAN):
    """classify() with the audio decoded and analysed from audio_path.
    Returns (results, audio_checked)."""
    analysis = None
    if audio_path:
        x = load_audio(audio_path)
        if x is not None:
            analysis = finish(analyze(x))
    return classify(data, analysis, overrides, clip, policy), analysis is not None


def load_guarded(path, audio_path=None, overrides=None, policy=UNCERTAIN_LEAN):
    """Read a transcript the way every consumer must. A JSON this module
    already wrote is used as it is. A raw Whisper JSON is screened in
    memory, from the Whisper numbers and the text counts alone unless
    audio_path is given, and its withheld segments are redacted before
    anything sees them. A .raw.json is refused by name: it is the unredacted
    transcript, and consumers read the safe <name>.json beside it.
    Returns (safe_data, results, audio_checked)."""
    if os.path.basename(path).lower().endswith(".raw.json"):
        raise SegkindError("%s is the unredacted transcript; read the safe %s.json beside it "
                           "(made by `vertcut transcribe`, or `vertcut kinds --apply-overrides`)"
                           % (os.path.basename(path), clip_name(path)))
    data = load_json(path)
    clip = clip_name(path)
    if is_safe(data):
        res = classify(data, None, overrides, clip, policy)
        return redact(data, res, data["safety"].get("audio_checked"), policy, clip), res, \
            bool(data["safety"].get("audio_checked"))
    res, checked = screen(data, audio_path, overrides, clip, policy)
    return redact(data, res, checked, policy, clip), res, checked


def classify_audio(analysis, win=4.0):
    """Audio-only windows, for a file with no transcript: the same sung
    score on the audio alone, one result per `win` seconds. A window with
    little pitched sound reports nothing found, which is not a clean bill
    of health: there are no words to check it against."""
    out = []
    dummy = {"word_max_s": None, "chorus_jaccard": 0.0}
    t, i = 0.0, 0
    while t < analysis.dur - 0.5:
        e = min(analysis.dur, t + win)
        a = audio_features(analysis, t, e)
        s, reasons = sung_score(dummy, a, None)
        if s >= T_SUNG:
            kind, lean = "music", None
        elif s >= T_LEAN:
            kind, lean = "uncertain", "sung"
        else:
            kind, lean = "speech", None
        seg = {"start": t, "end": e}
        r = _result(i, seg, kind, lean, reasons if kind != "speech" else [],
                    {"sung": _round(s)}, "classifier", UNCERTAIN_LEAN)
        out.append(r)
        t, i = e, i + 1
    return out


# ---------------------------------------------------------------------------
# Command line. Everything printed is a kind, a time, a count or a score.
# ---------------------------------------------------------------------------

def _fmt_range(r):
    return "%8.2f-%8.2f" % (r["start"], r["end"])


def _report(results, clip, audio_checked, policy, out, detail=False):
    sm = summarize(results)
    c = sm["counts"]
    out.write("%s  clip %s  segments %d  audio screened: %s  uncertain: %s\n"
              % (VERSION, clip, len(results) - sm["empty_segments"],
                 "yes" if audio_checked else "NO (singing not screened)", policy))
    for rg in ranges(results):
        out.write("  %s  %-13s %s  (%d segment%s)\n"
                  % (_fmt_range(rg), rg["kind"], "WITHHELD" if rg["withheld"] else "keep    ",
                     rg["segments"], "" if rg["segments"] == 1 else "s"))
    if detail:
        for r in results:
            if r.get("empty"):
                continue
            sc = r["scores"]
            out.write("  #%-3d %s  %-13s lean=%-13s %-8s sung=%s halluc=%s src=%s reasons=%s\n"
                      % (r["index"], _fmt_range(r), r["kind"], r["lean"] or "-",
                         "WITHHELD" if r["withheld"] else "keep", sc.get("sung", "-"),
                         sc.get("hallucination", "-"), r["source"], ",".join(r["reasons"]) or "-"))
    out.write("counts: %s; withheld %d segment(s), %.1f s\n"
              % (", ".join("%s %d" % (k, c[k]) for k in KINDS), sm["withheld_segments"],
                 sm["withheld_seconds"]))
    if sm["overrides"]["applied"] or sm["overrides"]["blocked"]:
        o = sm["overrides"]
        out.write("overrides: %d applied (%d promoted to speech), %d promotion(s) blocked: "
                  "no raw transcript to restore\n" % (o["applied"], o["promoted_to_speech"],
                                                      o["blocked"]))
    if not audio_checked:
        out.write("warning: no audio, so singing was not screened; only the Whisper numbers "
                  "were. Label sung spans in a sung.tsv.\n")


def _find_inputs(path, audio):
    """(json_path, raw_path, audio_path) for an input that is a transcript
    or a media file."""
    if path.lower().endswith(".json"):
        stem = os.path.join(os.path.dirname(os.path.abspath(path)), clip_name(path))
        raw = stem + ".raw.json"
        return path, (raw if os.path.isfile(raw) else None), audio or sibling_media(path)
    stem = os.path.splitext(os.path.abspath(path))[0]
    raw, safe = stem + ".raw.json", stem + ".json"
    return (safe if os.path.isfile(safe) else None), (raw if os.path.isfile(raw) else None), \
        audio or path


def cmd_kinds(args, out=None):
    out = out or sys.stdout
    policy = args.uncertain
    ov_path = args.apply_overrides or args.overrides
    overrides = read_overrides(ov_path) if ov_path else None
    jpath, raw, audio = _find_inputs(args.input, args.audio)
    if jpath is None and raw is None:
        # media with no transcript: audio windows only
        x = load_audio(audio)
        if x is None:
            raise SegkindError("cannot read audio from %s (numpy and ffmpeg are needed)"
                               % args.input)
        res = classify_audio(finish(analyze(x)))
        res = apply_overrides(res, [], overrides, clip_name(args.input))
        _report(res, clip_name(args.input), True, policy, out, args.segments)
        out.write("note: no transcript beside this file; windows are audio-only.\n")
        return 0
    clip = clip_name(jpath or raw)
    data = load_json(jpath) if jpath else None
    write = bool(args.apply_overrides or args.write)
    if write and raw:
        data, source_json = load_json(raw), raw     # restore needs the raw text
    elif data is None:
        data, source_json = load_json(raw), raw
    else:
        source_json = jpath
    safe_in = is_safe(data)
    results, checked = screen(data, None if safe_in else audio, overrides, clip, policy) \
        if not safe_in else (classify(data, None, overrides, clip, policy),
                             bool(data["safety"].get("audio_checked")))
    sm_dur = max([r["end"] for r in results if r["end"] is not None] or [0.0])
    _report(results, clip, checked, policy, out, args.segments)
    if write:
        base = os.path.join(os.path.dirname(os.path.abspath(jpath or raw)), clip)
        dump_json(redact(data, results, checked, policy, clip), base + ".json")
        dump_json(sidecar(results, clip, sm_dur, checked), base + ".kinds.json")
        out.write("wrote %s.json and %s.kinds.json\n" % (os.path.basename(base),
                                                         os.path.basename(base)))
    return 0


def cmd_redact(args, out=None):
    out = out or sys.stdout
    overrides = read_overrides(args.overrides) if args.overrides else None
    data = load_json(args.raw)
    clip = args.clip or clip_name(args.raw)
    results, checked = screen(data, args.audio, overrides, clip, args.uncertain)
    dump_json(redact(data, results, checked, args.uncertain, clip), args.out)
    sm_dur = max([r["end"] for r in results if r["end"] is not None] or [0.0])
    side = args.sidecar or os.path.join(os.path.dirname(os.path.abspath(args.out)),
                                        clip + ".kinds.json")
    dump_json(sidecar(results, clip, sm_dur, checked), side)
    _report(results, clip, checked, args.uncertain, out)
    return 0


def cmd_eval(args, out=None):
    """Score the classifier against labels (a sung.tsv of ground truth).
    A segment takes the kind of the label that covers half of it; segments
    no label covers are left out. Prints counts and rates, never text."""
    out = out or sys.stdout
    labels = read_overrides(args.labels)
    tp = fp = fn = tn = 0
    by_kind = {}
    for path in args.transcripts:
        clip = clip_name(path)
        data = load_json(path)
        audio = None
        if args.audio_dir:
            cand = os.path.join(args.audio_dir, clip + ".wav")
            audio = cand if os.path.isfile(cand) else None
        audio = audio or sibling_media(path)
        results, _ = screen(data, audio, None, clip, args.uncertain)
        mine = [o for o in labels if o["clip"] in ("*", clip)]
        for r in results:
            if r.get("empty") or r["start"] is None:
                continue
            dur = max(r["end"] - r["start"], 1e-3)
            truth = None
            for o in mine:
                ov = min(r["end"], o["end"]) - max(r["start"], o["start"])
                if ov / dur >= 0.5 or (r["end"] - r["start"] <= 0.001 and
                                       o["start"] <= r["start"] <= o["end"]):
                    truth = o["kind"]
                    break
            if truth is None:
                continue
            nonspeech = truth != "speech"
            by = by_kind.setdefault(truth, [0, 0])
            by[0] += 1
            by[1] += 1 if r["withheld"] else 0
            if nonspeech and r["withheld"]:
                tp += 1
            elif nonspeech:
                fn += 1
            elif r["withheld"]:
                fp += 1
            else:
                tn += 1
    prec = tp / float(tp + fp) if tp + fp else float("nan")
    rec = tp / float(tp + fn) if tp + fn else float("nan")
    fpr = fp / float(fp + tn) if fp + tn else float("nan")
    out.write("segments scored %d: withheld-vs-labelled-non-speech tp %d fp %d fn %d tn %d\n"
              % (tp + fp + fn + tn, tp, fp, fn, tn))
    out.write("precision %.3f  recall %.3f  false-withhold rate on speech %.3f\n"
              % (prec, rec, fpr))
    for k in sorted(by_kind):
        n, w = by_kind[k]
        out.write("  labelled %-13s n=%d withheld=%d (%.0f%%)\n" % (k, n, w, 100.0 * w / n))
    return 0


def build_parser():
    import argparse
    ap = argparse.ArgumentParser(prog="segkind", description="Classify Whisper segments; "
                                 "prints kinds, times and counts, never text.")
    sub = ap.add_subparsers(dest="cmd")
    k = sub.add_parser("kinds", help="show kinds and time ranges for a transcript or media file")
    k.add_argument("input", help="a transcript .json (raw or safe) or an audio/video file")
    k.add_argument("--audio", help="audio/video to screen (default: the file beside the JSON)")
    k.add_argument("--overrides", metavar="SUNG_TSV", help="preview a sung.tsv; writes nothing")
    k.add_argument("--apply-overrides", metavar="SUNG_TSV",
                   help="apply a sung.tsv and rewrite <clip>.json and <clip>.kinds.json")
    k.add_argument("--write", action="store_true",
                   help="write <clip>.json (safe) and <clip>.kinds.json from the raw transcript")
    k.add_argument("--segments", action="store_true", help="one line per segment with scores")
    k.add_argument("--uncertain", choices=UNCERTAIN_POLICIES, default=UNCERTAIN_LEAN)
    r = sub.add_parser("redact", help="write the safe JSON and the kinds sidecar from a raw JSON")
    r.add_argument("raw")
    r.add_argument("--out", required=True)
    r.add_argument("--audio")
    r.add_argument("--sidecar")
    r.add_argument("--overrides", metavar="SUNG_TSV")
    r.add_argument("--clip")
    r.add_argument("--uncertain", choices=UNCERTAIN_POLICIES, default=UNCERTAIN_LEAN)
    e = sub.add_parser("eval", help="score the classifier against a labels file")
    e.add_argument("transcripts", nargs="+")
    e.add_argument("--labels", required=True, metavar="LABELS_TSV")
    e.add_argument("--audio-dir")
    e.add_argument("--uncertain", choices=UNCERTAIN_POLICIES, default=UNCERTAIN_LEAN)
    return ap


def main(argv=None, out=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return {"kinds": cmd_kinds, "redact": cmd_redact, "eval": cmd_eval}[args.cmd](args, out)
    except (SegkindError, OSError, ValueError) as e:
        sys.stderr.write("segkind: %s\n" % e)
        return 1
    except Exception as e:      # never let a traceback carry transcript text
        sys.stderr.write("segkind: internal error (%s); nothing from the transcript is "
                         "printed on failure\n" % type(e).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
