"""
rpresolve.sync: where a second recording sits against a reference, found
from the sound both of them heard. Offline: ffmpeg decodes, numpy
correlates; nothing here touches Resolve (workflows.sync builds the
timeline).

Dual-system sound is a camera clip plus a separately recorded audio file,
or two cameras in one room. Both are decoded to mono at DECODE_RATE and
cross-correlated with FFTs:

    1. coarse: the whole of both signals, block-averaged down to at most
       COARSE_RATE, one correlation over every lag the two could share;
    2. fine: at DECODE_RATE, FINE_SEARCH_S either side of the coarse lag,
       on windows of WINDOW_S across the overlap: a head, a tail and at
       least one evenly spaced between (MAX_WINDOWS at most), or the whole
       overlap as one window when it is shorter than three windows;
    3. when the windows' offsets lie on a line that slopes by more than a
       quarter sample per window, again with the other stretched by that
       slope about each window's centre: a drifting clock smears a
       window's peak (50 ppm over 30 s is 1.5 ms) and lowers its
       correlation, and the stretch restores it. The stretched pass is
       kept only when it raises the windows' correlation; a slope that
       came from a step lowers it, and the first pass stands.

offset_s is where the other file's first frame lands on the reference's
clock, measured at the head: positive when the other recording started
after the reference, negative when it started before. A straight line
fitted through every window's offset gives clock drift, in ms per minute
and ppm. Drift that adds up to more than MAX_DRIFT_FRAMES over the overlap
is reported with the retime that would cancel it; nothing here corrects
it. One clock keeps every window within a millisecond or so of that line
(the stretched pass, within microseconds on synthetic drift). A window
more than LINE_MIN_MS or LINE_FRAMES of a frame off it, whichever is
more, means the pair lines up differently in different places: a call
recorded at both ends (each voice reaches the two files by its own path;
184 ms apart on one real pair), samples dropped by one recorder, or an
edited file. A line through three windows absorbs two thirds of a step at
one end, so a step escapes only while under three times that limit (0.3
frame), and what it leaves is counted as drift. The report then groups the
windows by the offset they agree on.

Each measurement carries its confidence: the normalized correlation at the
peak (-1..1, its sign the polarity) and the ratio of the peak to the
highest correlation more than EXCLUDE_S away from it. A window's search
reaches only FINE_SEARCH_S either side of the coarse lag, so its ratio says
nothing about a lag further away; the coarse pass does, as the ratio of its
peak to the best lag more than FINE_SEARCH_S away. Under COARSE_MIN_RATIO
that runner-up is measured as the match is, window by window: when every
window there passes too, the sound repeats (a looped music bed, one sting
at both ends, a countdown) and one offset cannot be chosen. When the
runner-up fails, the low coarse ratio came from noise the fine pass sees
through (wind or handling rumble on a camera mic), and the match stands.
A likeness must also run through the window: its correlation is measured
again with its strongest EVENT_S left out (rest_ncc). One moment carrying
it all, a click over digital silence or quiet noise in each of two
unrelated files, correlates at ncc 1.0 and proves nothing; a door slam
over talk that also lines up keeps a high rest_ncc. A window below
MIN_PEAK_RATIO, or with ncc or rest_ncc below MIN_NCC, is not called a
match, and neither is digital silence, an overlap shorter than
MIN_OVERLAP_S, windows that disagree as above, a slope beyond any clock
(MAX_PLAUSIBLE_PPM), windows of opposite polarity, or a runner-up that
passes as above.

Offsets are in the files' own time: each file's zero is its first video
frame (its first audio sample when it has no video), so an audio stream
that starts later than the video in its container is accounted for.
Placed at frame precision, a clip lands within half a frame of the
measured offset: that residual is inherent (+/-20 ms at 25 fps) and is
reported beside the frames.

numpy is required (the system /usr/bin/python3 has it).
"""

import json
import os
import subprocess
from fractions import Fraction

import numpy as np

from . import cutlist
from .syncbuild import SyncError  # noqa: F401  (raised here; importable without numpy)

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
FFPROBE = os.environ.get("RPRESOLVE_FFPROBE", "/opt/homebrew/bin/ffprobe")
TIMEOUT_S = 900  # per decode; a long file over SMB must not hang the run

DECODE_RATE = 8000
COARSE_RATE = 1000
COARSE_MAX = 1 << 23       # samples of both coarse signals together; decimate further past it
WINDOW_S = 30.0
FINE_SEARCH_S = 1.0
EXCLUDE_S = 0.05
EVENT_S = 1.0     # a window must still correlate with its strongest second left out
MIN_OVERLAP_S = 5.0
MIN_PEAK_RATIO = 2.0
MIN_NCC = 0.1
COARSE_MIN_RATIO = 2.0  # a coarse runner-up closer than this is measured as a match would be
MAX_DRIFT_FRAMES = 0.5
LINE_MIN_MS = 2.0     # a window further than this off the drift line (and
LINE_FRAMES = 0.1     # than this much of a frame) is not on one clock's line
MAX_WINDOWS = 7
WARP_MIN_SAMPLES = 0.25   # a first-pass slope smearing a window by more is compensated
PEAK_RATIO_CAP = 1e6  # reported when nothing else correlates at all
MAX_PLAUSIBLE_PPM = 300.0  # two crystal clocks stay well inside this (+/-100 ppm each at worst)
DEFAULT_DRIFT_FPS = 25.0    # the drift threshold's frame when no fps is known


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=TIMEOUT_S, **kw)
    except FileNotFoundError:
        raise SyncError(f"{cmd[0]} not found; install ffmpeg (Homebrew) or set "
                        "RPRESOLVE_FFMPEG / RPRESOLVE_FFPROBE.")
    except subprocess.TimeoutExpired:
        raise SyncError(f"{os.path.basename(cmd[0])} timed out after {TIMEOUT_S}s")


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------

def _rate(text):
    """'30000/1001' or '25' -> float, or None for 0/0 and junk."""
    try:
        f = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return float(f) if f > 0 else None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def probe(path, stream=0):
    """{duration, audio {index, sample_rate, channels, start}, video {fps,
    start} or None, audio_start_s}. stream picks the audio stream (0 is the
    first). audio_start_s is the audio stream's start after the file's
    zero: its first video frame, else its first audio sample."""
    cmd = [FFPROBE, "-v", "error", "-show_entries",
           "stream=index,codec_type,sample_rate,channels,avg_frame_rate,r_frame_rate,"
           "start_time:stream_disposition=attached_pic:format=duration", "-of", "json",
           "file:" + path]
    proc = _run(cmd, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SyncError(f"ffprobe could not read {path}: {proc.stderr.strip()[-200:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    if len(audio) <= stream:
        raise SyncError(f"{path} has {len(audio)} audio stream(s); asked for audio stream "
                        f"{stream} (0 is the first)")
    a = audio[stream]
    v = video[0] if video else None
    a_start = _float(a.get("start_time")) or 0.0
    zero = (_float(v.get("start_time")) or 0.0) if v else a_start
    fps = (_rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))) if v else None
    return {"duration": _float((data.get("format") or {}).get("duration")) or 0.0,
            "audio": {"index": a.get("index"), "sample_rate": int(a.get("sample_rate") or 0),
                      "channels": int(a.get("channels") or 0), "start": a_start},
            "video": {"fps": fps, "start": zero} if v else None,
            "audio_start_s": round(a_start - zero, 6)}


def decode(path, rate=DECODE_RATE, stream=0, channel=None):
    """Mono float32 samples at `rate` of one audio stream: every channel
    averaged, or only `channel` (1-based)."""
    mix = (["-af", f"pan=mono|c0=c{int(channel) - 1}"] if channel else ["-ac", "1"])
    cmd = [FFMPEG, "-v", "error", "-i", "file:" + path, "-map", f"0:a:{int(stream)}",
           "-vn", "-sn", "-dn", *mix, "-ar", str(int(rate)), "-f", "f32le", "-"]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise SyncError(f"ffmpeg could not decode the audio of {path}: "
                        f"{proc.stderr.decode(errors='replace').strip()[-200:]}")
    samples = np.frombuffer(proc.stdout[:len(proc.stdout) // 4 * 4], dtype="<f4")
    if not len(samples):
        raise SyncError(f"no audio samples decoded from {path}")
    return samples  # float32: an hour at 8 kHz is 115 MB; windows go to float64 as needed


# ---------------------------------------------------------------------------
# Correlation (pure; tested on synthetic signals)
# ---------------------------------------------------------------------------

def _fft_size(n):
    return 1 << (int(n) - 1).bit_length()


def xcorr(ref, other):
    """(lags, c): c[L] = sum_n ref[n] * other[n - L] for every lag L from
    -(len(other) - 1) to len(ref) - 1. At lag L, sample m of other lines up
    with sample m + L of ref."""
    n = len(ref) + len(other) - 1
    size = _fft_size(n)
    c = np.fft.irfft(np.fft.rfft(ref, size) * np.conj(np.fft.rfft(other, size)), size)
    c = np.concatenate((c[size - (len(other) - 1):], c[:len(ref)])) if len(other) > 1 \
        else c[:len(ref)]
    return np.arange(-(len(other) - 1), len(ref)), c


def _overlap(lags, n_ref, n_other):
    """(lo, hi) arrays: the ref samples [lo, hi) each lag overlaps."""
    lo = np.maximum(0, lags)
    hi = np.minimum(n_ref, n_other + lags)
    return lo, hi


def peak(ref, other, lag_min=None, lag_max=None, min_overlap=1, exclude=0):
    """The best lag of other against ref (see xcorr), searched over
    [lag_min, lag_max] among lags where the two overlap by at least
    min_overlap samples. Returns {lag (sub-sample), ncc, peak_ratio,
    second_lag, polarity, overlap}, or None when no lag qualifies. ncc is
    the correlation at the peak over the energy of the overlapping parts;
    peak_ratio the peak over the highest |c| more than `exclude` samples
    away (PEAK_RATIO_CAP at most, and when nothing is left to compare), and
    second_lag where that highest |c| is (None when nothing is left)."""
    ref = np.asarray(ref, dtype=np.float64)
    other = np.asarray(other, dtype=np.float64)
    if not len(ref) or not len(other):
        return None
    lags, c = xcorr(ref, other)
    lo, hi = _overlap(lags, len(ref), len(other))
    ok = (hi - lo) >= max(1, int(min_overlap))
    if lag_min is not None:
        ok &= lags >= lag_min
    if lag_max is not None:
        ok &= lags <= lag_max
    if not ok.any():
        return None
    mag = np.where(ok, np.abs(c), -1.0)
    k = int(np.argmax(mag))
    if mag[k] <= 0:
        return None
    # Sub-sample peak: a parabola through the peak and its neighbours.
    frac = 0.0
    if 0 < k < len(c) - 1 and ok[k - 1] and ok[k + 1]:
        s = 1.0 if c[k] >= 0 else -1.0
        y0, y1, y2 = s * c[k - 1], s * c[k], s * c[k + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    cs_r = np.concatenate(([0.0], np.cumsum(ref * ref)))
    cs_o = np.concatenate(([0.0], np.cumsum(other * other)))
    L = int(lags[k])
    a, b = int(lo[k]), int(hi[k])
    energy = (cs_r[b] - cs_r[a]) * (cs_o[b - L] - cs_o[a - L])
    ncc = float(c[k] / np.sqrt(energy)) if energy > 0 else 0.0
    rest = ok & (np.abs(lags - L) > int(exclude))
    second, second_lag = 0.0, None
    if rest.any():
        j = int(np.argmax(np.where(rest, np.abs(c), -1.0)))
        second, second_lag = float(abs(c[j])), int(lags[j])
    return {"lag": L + frac, "ncc": round(ncc, 4),
            "peak_ratio": round(min(float(abs(c[k]) / second), PEAK_RATIO_CAP), 3)
            if second > 0 else PEAK_RATIO_CAP,
            "second_lag": second_lag if second > 0 else None,
            "polarity": "normal" if c[k] >= 0 else "inverted", "overlap": b - a}


def without_event(x, y, lag, width):
    """(ncc, at): the normalized correlation of x against y at the integer
    lag (as in xcorr) with the `width` samples that add most to it left out,
    and where in x the strongest sample of that stretch is. Near the full ncc when the
    likeness runs through the window; near zero when one moment (a click, a
    clap, a single sample over digital silence) carries it all. What is left
    has its own mean taken out: removing a whole file's mean leaves digital
    silence as a constant, and two constants correlate perfectly."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    lo, hi = max(0, lag), min(len(x), len(y) + lag)
    if hi - lo <= width:
        return 0.0, lo
    xs, ys = x[lo:hi], y[lo - lag:hi - lag]
    p = xs * ys
    sign = 1.0 if p.sum() >= 0 else -1.0
    cs = np.concatenate(([0.0], np.cumsum(p)))
    k = int(np.argmax(sign * (cs[width:] - cs[:-width])))
    keep = np.ones(len(p), dtype=bool)
    keep[k:k + width] = False
    xr, yr = xs[keep] - xs[keep].mean(), ys[keep] - ys[keep].mean()
    energy = float((xr * xr).sum() * (yr * yr).sum())
    at = lo + k + int(np.argmax(sign * p[k:k + width]))  # the strongest sample in it
    return (float((xr * yr).sum() / np.sqrt(energy)) if energy > 0 else 0.0), at


def decimate(x, factor):
    """Block means of `factor` samples: a crude low-pass and downsample,
    the same on both signals, good enough for the coarse lag."""
    factor = int(factor)
    if factor <= 1:
        return np.asarray(x, dtype=np.float64)
    n = len(x) // factor * factor
    return np.asarray(x[:n]).reshape(-1, factor).mean(axis=1, dtype=np.float64)


def coarse_factor(n_ref, n_other, rate=DECODE_RATE, coarse_rate=COARSE_RATE,
                  coarse_max=COARSE_MAX):
    """The decimation factor for the coarse pass: down to coarse_rate, and
    by further factors of two until both signals together fit coarse_max."""
    factor = max(1, int(round(rate / float(coarse_rate))))
    while (n_ref + n_other) / factor > coarse_max:
        factor *= 2
    return factor


def windows(ov0, ov1, rate, window_s=WINDOW_S, max_windows=MAX_WINDOWS):
    """The ref-sample windows to measure in the overlap [ov0, ov1). When
    it holds three windows of window_s: a head, a tail, a middle one and,
    one per ten windows' worth of overlap, more evenly spaced between
    (max_windows in all). Three at least, so that a step (two parts that
    line up differently) leaves a window off the fitted drift line; with
    three, by a third of the step. Otherwise the whole overlap as one
    window."""
    w = int(round(window_s * rate))
    span = ov1 - ov0
    if w <= 0 or span < 3 * w:
        return [("whole", ov0, ov1)]
    n = max(3, min(int(max_windows), 3 + int(span // (10 * w))))
    step = (span - w) / float(n - 1)
    starts = [ov0 + int(round(i * step)) for i in range(n)]
    labels = ["head"] + [f"w{i + 1}" for i in range(1, n - 1)] + ["tail"]
    return [(label, a, a + w) for label, a in zip(labels, starts)]


def fine(ref, other, a, b, lag0, rate, search_s=FINE_SEARCH_S, exclude_s=EXCLUDE_S, warp=0.0):
    """The lag, in ref samples, at the centre of the ref window [a, b),
    searched within search_s of lag0: the window against the stretch of
    other it could line up with. With warp (the offset's change per
    reference sample, from a first pass), that stretch is resampled about
    the window's centre so a drifting clock does not smear the peak. None
    when nothing overlaps."""
    s = int(round(search_s * rate))
    o0 = max(0, int(np.floor(a - lag0)) - s)
    o1 = min(len(other), int(np.ceil(b - lag0)) + s)
    if o1 <= o0 or b <= a:
        return None
    seg = other[o0:o1]
    if warp:
        # Reference sample n shows other sample n - L(n), L(n) = L(c) + warp * (n - c):
        # read other at j0 + (m - j0) * (1 - warp) for each m about j0 = c - L(c).
        j0 = (a + b) / 2.0 - lag0
        pos = j0 + (np.arange(o0, o1) - j0) * (1 - warp)
        i0 = max(0, int(np.floor(pos.min())) - 1)
        i1 = min(len(other), int(np.ceil(pos.max())) + 2)
        seg = np.interp(pos, np.arange(i0, i1), other[i0:i1])
    shift = a - o0  # a local lag plus this is the global lag
    r = peak(ref[a:b], seg, lag_min=int(np.floor(lag0 - s - shift)),
             lag_max=int(np.ceil(lag0 + s - shift)), min_overlap=max(1, (b - a) // 2),
             exclude=int(round(exclude_s * rate)))
    if r:
        rest, at = without_event(ref[a:b], seg, int(round(r["lag"])),
                                 int(round(EVENT_S * rate)))
        r.update(rest_ncc=round(rest, 4), event_at=a + at)
        r["lag"] += shift
    return r


# ---------------------------------------------------------------------------
# Frames and drift
# ---------------------------------------------------------------------------

def frames(offset_s, fps):
    """The offset at fps: exact frames, the whole frame it is placed at,
    and what that placement leaves (|residual| <= 0.5 frame)."""
    exact = offset_s * fps
    placed = cutlist.frame(offset_s, fps)  # rounding half up
    return {"fps": fps, "exact": round(exact, 4), "placed": placed,
            "residual_frames": round(exact - placed, 4),
            "residual_ms": round((exact - placed) / fps * 1000, 3),
            "inherent_ms": round(500.0 / fps, 3)}


def drift(measured, overlap_s, fps=None, max_frames=MAX_DRIFT_FRAMES):
    """Clock drift from two or more window measurements ({center_s,
    offset_s}): the least-squares change in offset per second of the
    reference, as ms per minute and ppm; what it adds up to over the
    overlap and whether that passes max_frames; the speed (percent, for the
    other clip) that would cancel it; and how far each window sits from
    the fitted line, which a single clock keeps within a millisecond or so
    and a pair that lines up differently in different places does not.
    residual_limit_ms is the most a window may sit off the line:
    LINE_MIN_MS, or LINE_FRAMES of a frame when that is more."""
    t = np.array([m["center_s"] for m in measured], dtype=np.float64)
    o = np.array([m["offset_s"] for m in measured], dtype=np.float64)
    tc = t - t.mean()
    d = float((tc * (o - o.mean())).sum() / (tc * tc).sum())
    fit = o.mean() + d * tc
    res_ms = (o - fit) * 1000
    over_ms = d * overlap_s * 1000
    frame_ms = 1000.0 / (fps or DEFAULT_DRIFT_FPS)
    line_ms = max(LINE_MIN_MS, LINE_FRAMES * frame_ms)
    return {"ppm": round(d * 1e6, 2), "ms_per_min": round(d * 60000, 3),
            "over_overlap_ms": round(over_ms, 2),
            "over_overlap_frames": round(over_ms / frame_ms, 3),
            "threshold_frames": max_frames,
            "exceeds": abs(over_ms) > max_frames * frame_ms,
            "retime_pct": round(100 * (1 - d), 5),
            "span_s": round(float(t[-1] - t[0]), 3),
            "residuals_ms": [round(float(r), 3) for r in res_ms],
            "max_residual_ms": round(float(np.abs(res_ms).max()), 3),
            "residual_limit_ms": round(line_ms, 3)}


def groups(measured, limit_ms):
    """Windows grouped by the offset they agree on: sorted by offset, a new
    group wherever the next offset is more than limit_ms away. Largest
    group first: [{offset_s, windows: [label]}]."""
    out = []
    for m in sorted(measured, key=lambda m: m["offset_s"]):
        if out and (m["offset_s"] - out[-1]["_last"]) * 1000 <= limit_ms:
            out[-1]["_all"].append(m)
            out[-1]["_last"] = m["offset_s"]
        else:
            out.append({"_all": [m], "_last": m["offset_s"]})
    res = [{"offset_s": round(float(np.median([m["offset_s"] for m in g["_all"]])), 6),
            "windows": [m["label"] for m in sorted(g["_all"], key=lambda m: m["center_s"])]}
           for g in out]
    return sorted(res, key=lambda g: -len(g["windows"]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _measure(ref, other, spans, lag_at, warp, rate, search_s, check_cancel):
    """[(centre sample, lag or None, peak result)] per window span."""
    out = []
    for _, a, b in spans:
        if check_cancel:
            check_cancel()
        c = (a + b) / 2.0
        r = fine(ref, other, a, b, lag_at(c), rate, search_s, warp=warp)
        out.append((c, r["lag"] if r else None, r))
    return out


def _rival(ref, other, lag0, lag, rate, window_s, search_s, min_ratio, min_ncc, shift_s,
           ref_start_s, check_cancel):
    """The coarse runner-up at `lag` measured as the match at lag0 is: its
    windows, each with whether it passes the same checks, and match when
    every one does. A window that lands within search_s of lag0 found the
    match itself, so it does not pass."""
    ov0 = max(0, int(round(lag)))
    ov1 = min(len(ref), len(other) + int(round(lag)))
    out = {"offset_s": round(lag / rate + shift_s, 4), "windows": [], "match": False}
    if (ov1 - ov0) / rate < MIN_OVERLAP_S:
        return out
    spans = windows(ov0, ov1, rate, window_s)
    got = _measure(ref, other, spans, lambda c: lag, 0.0, rate, search_s, check_cancel)
    for (label, a, b), (_, found, r) in zip(spans, got):
        w = {"label": label, "center_s": round((a + b) / 2 / rate + ref_start_s, 3),
             "offset_s": None, "ncc": None, "peak_ratio": None, "passes": False}
        if r is not None:
            w.update(offset_s=round(found / rate + shift_s, 6), ncc=r["ncc"],
                     peak_ratio=r["peak_ratio"],
                     passes=bool(r["peak_ratio"] >= min_ratio and abs(r["ncc"]) >= min_ncc and
                                 np.sign(r["ncc"]) * r["rest_ncc"] >= min_ncc and
                                 abs(found - lag0) > search_s * rate))
        out["windows"].append(w)
    out["match"] = bool(out["windows"]) and all(w["passes"] for w in out["windows"])
    return out


def _slope(points):
    """Least-squares slope of lag against centre, [(centre, lag)]."""
    c = np.array([p[0] for p in points], dtype=np.float64)
    lag = np.array([p[1] for p in points], dtype=np.float64)
    cc = c - c.mean()
    den = float((cc * cc).sum())
    return float((cc * (lag - lag.mean())).sum() / den) if den > 0 else 0.0


def measure_signals(ref, other, rate=DECODE_RATE, fps=None, window_s=WINDOW_S,
                    search_s=FINE_SEARCH_S, min_ratio=MIN_PEAK_RATIO, min_ncc=MIN_NCC,
                    max_drift_frames=MAX_DRIFT_FRAMES, ref_start_s=0.0, other_start_s=0.0,
                    check_cancel=None):
    """The measurement on two decoded signals (see the module docstring).
    ref_start_s and other_start_s are each file's audio start after its
    zero (probe's audio_start_s)."""
    # One float32 copy of each, without its DC offset; windows and the coarse
    # signals are taken to float64 as they are cut out.
    ref = np.asarray(ref, dtype=np.float32)
    other = np.asarray(other, dtype=np.float32)
    ref = ref - np.float32(ref.mean(dtype=np.float64))
    other = other - np.float32(other.mean(dtype=np.float64))
    shift_s = ref_start_s - other_start_s  # audio lag -> file offset
    th = {"min_peak_ratio": min_ratio, "min_ncc": min_ncc, "min_overlap_s": MIN_OVERLAP_S,
          "max_drift_frames": max_drift_frames, "window_s": window_s, "search_s": search_s}
    out = {"rate": rate, "thresholds": th, "coarse": None, "overlap": None,
           "windows": [], "offset_s": None, "tail_offset_s": None, "drift": None,
           "frames": None, "polarity": None, "match": False, "reasons": []}
    silent = [role for role, x in (("reference", ref), ("other", other)) if not np.any(x)]
    if silent:
        out["reasons"].append(" and ".join(f"the {role}" for role in silent) +
                              " holds no sound to correlate (digital silence)")
        return out
    factor = coarse_factor(len(ref), len(other), rate)
    cr = rate / factor
    shorter = min(len(ref), len(other)) / factor
    # The runner-up is sought beyond the fine search: within it, each window's
    # own peak ratio already compares every lag.
    c = peak(decimate(ref, factor), decimate(other, factor),
             min_overlap=max(1, min(MIN_OVERLAP_S * cr, 0.5 * shorter)),
             exclude=int(round(search_s * cr)))
    if c is None:
        out["reasons"].append("the signals are too short to correlate")
        return out
    lag0 = c["lag"] * factor
    out["coarse"] = {"rate": cr, "offset_s": round(lag0 / rate + shift_s, 4),
                     "ncc": c["ncc"], "peak_ratio": c["peak_ratio"],
                     "second_offset_s": None if c["second_lag"] is None else
                     round(c["second_lag"] * factor / rate + shift_s, 4)}
    ov0 = max(0, int(round(lag0)))
    ov1 = min(len(ref), len(other) + int(round(lag0)))
    ov_s = (ov1 - ov0) / rate
    out["overlap"] = {"start_s": round(ov0 / rate + ref_start_s, 3),
                      "end_s": round(ov1 / rate + ref_start_s, 3), "seconds": round(ov_s, 3)}
    if ov_s < MIN_OVERLAP_S:
        out["reasons"].append(f"the recordings overlap for {ov_s:.1f} s at the best lag; "
                              f"at least {MIN_OVERLAP_S:g} s is needed")
        return out
    spans = windows(ov0, ov1, rate, window_s)
    first = _measure(ref, other, spans, lambda c: lag0, 0.0, rate, search_s, check_cancel)
    found = [(c, lag) for c, lag, _ in first if lag is not None]
    warp = _slope(found) if len(found) >= 3 else 0.0
    measured = first
    if warp and abs(warp) * (spans[0][2] - spans[0][1]) > WARP_MIN_SAMPLES:
        cs = np.array([c for c, _ in found])
        ls = np.array([lag for _, lag in found])
        at = lambda c: float(ls.mean() + warp * (c - cs.mean()))  # noqa: E731
        limit = max_drift_frames * rate / (fps or DEFAULT_DRIFT_FPS)
        # A drifting clock smears each window's peak: measure again with the
        # other stretched by the first pass's slope, about the fitted lag. Only
        # when that pass lies near a line, and kept only when the stretch raised
        # the correlation, as a real drift's does: a line fitted through a step
        # has a slope too, and stretching by it smears every window instead.
        if max(abs(lag - at(c)) for c, lag in found) <= limit:
            again = _measure(ref, other, spans, at, warp, rate, search_s, check_cancel)
            both = [(r1, r2) for (_, _, r1), (_, _, r2) in zip(first, again) if r1 and r2]
            gain = (np.mean([abs(r2["ncc"]) for _, r2 in both]) -
                    np.mean([abs(r1["ncc"]) for r1, _ in both])) if both else -1.0
            if gain > 0:
                measured = again
                out["drift_compensated_ppm"] = round(warp * 1e6, 2)
            else:
                out["stretch_dropped_ppm"] = round(warp * 1e6, 2)
    for (label, a, b), (_, lag, r) in zip(spans, measured):
        if r is None:
            out["reasons"].append(f"{label} window: nothing to correlate")
            continue
        w = {"label": label, "center_s": round((a + b) / 2 / rate + ref_start_s, 3),
             "seconds": round((b - a) / rate, 3), "offset_s": round(lag / rate + shift_s, 6),
             "ncc": r["ncc"], "rest_ncc": r["rest_ncc"], "peak_ratio": r["peak_ratio"],
             "polarity": r["polarity"]}
        out["windows"].append(w)
        if w["peak_ratio"] < min_ratio:
            out["reasons"].append(f"{label} window: peak only {w['peak_ratio']:.2f}x the next "
                                  f"(needs {min_ratio:g}x)")
        if abs(w["ncc"]) < min_ncc:
            out["reasons"].append(f"{label} window: normalized correlation {w['ncc']:+.3f} "
                                  f"(needs {min_ncc:g})")
        elif np.sign(w["ncc"]) * w["rest_ncc"] < min_ncc:
            out["reasons"].append(
                f"{label} window: the match rests on one moment, at "
                f"{r['event_at'] / rate + ref_start_s:.1f} s on the reference; without that "
                f"second the rest correlates at {w['rest_ncc']:+.3f} (needs {min_ncc:g}), which "
                "two unrelated clicks over silence or quiet noise can do too")
    if c["peak_ratio"] < COARSE_MIN_RATIO and c["second_lag"] is not None:
        # A second lag correlates nearly as well over the whole of both: sound
        # that repeats (a looped bed, a sting at both ends) or a coarse pass
        # that low-frequency noise drowned. The windows tell them apart.
        rival = _rival(ref, other, lag0, c["second_lag"] * factor, rate, window_s, search_s,
                       min_ratio, min_ncc, shift_s, ref_start_s, check_cancel)
        out["rival"] = rival
        if rival["match"]:
            out["reasons"].append(
                f"a second offset, {rival['offset_s']:+.3f} s, passes every window check too "
                f"(the coarse peak is only {c['peak_ratio']:.2f}x it): the sound repeats, so "
                "one offset cannot be chosen")
    if not out["windows"]:
        return out
    head = out["windows"][0]
    out["offset_s"] = head["offset_s"]
    out["polarity"] = head["polarity"]
    if len(out["windows"]) >= 2:
        out["tail_offset_s"] = out["windows"][-1]["offset_s"]
        d = drift(out["windows"], ov_s, fps, max_drift_frames)
        out["drift"] = d
        for w, r in zip(out["windows"], d["residuals_ms"]):
            w["residual_ms"] = r
        spread = (max(w["offset_s"] for w in out["windows"]) -
                  min(w["offset_s"] for w in out["windows"])) * 1000
        # Off the line first: a slope means something only when the windows lie on one.
        if d["max_residual_ms"] > d["residual_limit_ms"]:
            out["groups"] = groups(out["windows"], d["residual_limit_ms"])
            out["reasons"].append(
                f"the windows disagree: offsets spread over {spread:.1f} ms and one sits "
                f"{d['max_residual_ms']:.1f} ms off any straight drift line (one clock keeps "
                f"them within {d['residual_limit_ms']:g} ms). The pair lines up differently "
                "in different places: each part reaches the two files by a different path (a "
                "call recorded at both ends), one recorder dropped samples, or one file is "
                "edited")
        elif abs(d["ppm"]) > MAX_PLAUSIBLE_PPM:
            out["reasons"].append(
                f"the windows' offsets change by {d['ppm']:.0f} ppm, more than two clocks drift "
                f"apart ({MAX_PLAUSIBLE_PPM:g} ppm at most): a 0.1% pull-up or pull-down (1000 "
                "ppm; a recorder set to 48.048 or 47.952 kHz) reads like this, and so do windows "
                "that found different matches")
        if len({w["polarity"] for w in out["windows"]}) > 1:
            out["reasons"].append("the windows disagree on polarity")
    else:
        out["drift_note"] = (f"drift not measured: the overlap ({ov_s:.1f} s) is shorter than "
                             f"three {window_s:g} s windows")
    if fps:
        out["frames"] = frames(out["offset_s"], fps)
    out["match"] = not out["reasons"]
    return out


def measure(reference, other, fps=None, window_s=WINDOW_S, search_s=FINE_SEARCH_S,
            min_ratio=MIN_PEAK_RATIO, min_ncc=MIN_NCC, max_drift_frames=MAX_DRIFT_FRAMES,
            ref_stream=0, other_stream=0, ref_channel=None, other_channel=None,
            rate=DECODE_RATE, check_cancel=None):
    """Measure where `other` sits against `reference` (file paths). fps
    defaults to the reference's video frame rate. Returns the report of
    measure_signals plus each file's probe."""
    for p in (reference, other):
        if not os.path.isfile(p):
            raise SyncError(f"{p} is not a file")
    pr, po = probe(reference, ref_stream), probe(other, other_stream)
    if fps is None and pr["video"]:
        fps = pr["video"]["fps"]
    if check_cancel:
        check_cancel()
    ref = decode(reference, rate, ref_stream, ref_channel)
    if check_cancel:
        check_cancel()
    oth = decode(other, rate, other_stream, other_channel)
    report = measure_signals(ref, oth, rate, fps, window_s, search_s, min_ratio, min_ncc,
                             max_drift_frames, pr["audio_start_s"], po["audio_start_s"],
                             check_cancel=check_cancel)
    report["reference"] = {"path": reference, **pr, "decoded_s": round(len(ref) / rate, 3)}
    report["other"] = {"path": other, **po, "decoded_s": round(len(oth) / rate, 3)}
    report["fps"] = fps
    return report


def format_summary(r):
    """A short human-readable summary of a measure report."""
    lines = [f"sync-measure: {os.path.basename(r['other']['path'])} against "
             f"{os.path.basename(r['reference']['path'])}"]
    c = r.get("coarse")
    if c:
        lines.append(f"  coarse ({c['rate']:g} Hz): {c['offset_s']:+.3f} s, ncc {c['ncc']:+.3f}, "
                     f"peak {c['peak_ratio']:.2f}x" +
                     (f" the next best, at {c['second_offset_s']:+.3f} s"
                      if c.get("second_offset_s") is not None else ""))
    rv = r.get("rival")
    if rv:
        passed = sum(1 for w in rv["windows"] if w["passes"])
        lines.append(f"  that runner-up measured too: {passed} of {len(rv['windows'])} window(s) "
                     "pass there" + (" (the offset is ambiguous)" if rv["match"] else ""))
    ov = r.get("overlap")
    if ov:
        lines.append(f"  overlap: {ov['start_s']:.2f} to {ov['end_s']:.2f} s on the reference "
                     f"({ov['seconds']:.1f} s)")
    for w in r.get("windows") or []:
        lines.append(f"  {w['label']:5} at {w['center_s']:.1f} s: {w['offset_s']:+.4f} s, ncc "
                     f"{w['ncc']:+.3f}, peak {w['peak_ratio']:.2f}x ({w['polarity']})" +
                     (f", {w['residual_ms']:+.2f} ms off the drift line"
                      if w.get("residual_ms") is not None else ""))
    if r.get("drift_compensated_ppm") is not None:
        lines.append(f"  measured again with the other stretched by "
                     f"{r['drift_compensated_ppm']:+.2f} ppm (the first pass's drift)")
    if r.get("stretch_dropped_ppm") is not None:
        lines.append(f"  stretching the other by the first pass's {r['stretch_dropped_ppm']:+.2f} "
                     "ppm lowered the windows' correlation (a clock's drift raises it); the "
                     "first pass stands")
    d = r.get("drift")
    if d and r["match"]:
        lines.append(f"  drift: {d['ms_per_min']:+.3f} ms/min ({d['ppm']:+.2f} ppm), "
                     f"{d['over_overlap_ms']:+.1f} ms over the overlap ("
                     f"{d['over_overlap_frames']:+.2f} frame)" +
                     (f"; OVER {d['threshold_frames']:g} frame: retime the other clip to "
                      f"{d['retime_pct']:.5f}% to cancel it" if d["exceeds"] else ""))
    elif r.get("drift_note"):
        lines.append("  " + r["drift_note"])
    if r.get("groups"):
        lines.append("  windows agree in groups: " + "; ".join(
            f"{g['offset_s']:+.4f} s ({', '.join(g['windows'])})" for g in r["groups"]))
    f = r.get("frames")
    if r.get("offset_s") is not None:
        lines.append(f"  offset: {r['offset_s']:+.4f} s" +
                     (f" = {f['exact']:+.3f} frames at {f['fps']:g} fps, placed at "
                      f"{f['placed']:+d} (residual {f['residual_ms']:+.1f} ms; inherent "
                      f"+/-{f['inherent_ms']:.1f} ms at frame precision)" if f else ""))
    lines.append("  MATCH" if r["match"] else "  NO MATCH: " + "; ".join(r["reasons"]))
    return "\n".join(lines) + "\n"
