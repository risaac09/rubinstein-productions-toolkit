"""
rpresolve.dxmetrics: before-and-after numbers for a dialogue chain, with no
clean reference to compare against. Offline: ffmpeg decodes and measures
loudness, numpy does the rest; nothing here changes a file.

Run from production/:
    python3 -m rpresolve.dxmetrics measure FILE [--stream N] [--start S] [--end S]
        [--out JSON]
    python3 -m rpresolve.dxmetrics lag A B [--stream N] [--stream-b N] [--start S]
        [--seconds 30] [--max-lag-ms 250]
    python3 -m rpresolve.dxmetrics compare A B [--stream N] [--stream-b N]
        [--start S] [--end S] [--out JSON]

The numbers, for one audio stream mixed to mono:
    integrated_lufs, lra_lu, true_peak_dbtp
                      ffmpeg ebur128 with peak=true, read as audio-report reads
                      them (on the stream's own channels, not the mono mix)
    sibilance_ratio_db
                      5-9 kHz power over 100 Hz-5 kHz power (Welch, 4096). A
                      de-esser lowers it; presence EQ and compression raise it,
                      so read it as a ratio, never as a level
    flatness          mean spectral flatness of 2048-sample frames, the
                      geometric over the arithmetic mean of the magnitude
                      spectrum as ffmpeg's aspectralstats computes it: about
                      0.85 for white noise, 0.7 for pink, near 0 for a tone.
                      Hum and music lower it, broadband hiss raises it
    hum_excess_db     for 50 and 60 Hz mains, the mean height of harmonics 1-5
                      over their neighbouring bins (Welch, 16384) in the
                      quietest 25% of 100 ms frames, where speech does not
                      cover the hum. A few dB is the noise of the estimate;
                      10 dB or more is hum worth a notch
    wada_snr_db       a blind signal-to-noise estimate in the manner of WADA
                      (Kim and Stern, 2008): speech amplitudes taken as
                      Gamma(0.4), noise as Gaussian. The lookup is built here
                      by seeded simulation, not copied from the paper, so the
                      figure compares only with itself, before and after. It is
                      held to WADA_RANGE_DB
    lag (lag, compare)
                      where B's content sits against A's, by cross-correlation
                      at A's rate: positive means B is late. afftdn delays its
                      output 1200 samples at 48 kHz (25 ms), anlmdn 384 and
                      asubcut (order 8) 151; ffmpeg does not compensate, and the
                      file keeps its length, so a denoised stem lands late
                      against picture. polarity says whether the best match
                      was inverted. A highpass or EQ shifts phase, which can
                      move the peak a sample or two.

compare prints both files' numbers, B minus A, and the lag.

None of this is listening. A chain can improve every number here and still
smear consonants or leave musical noise; that needs ears and a blind A/B.
"""

import argparse
import json
import math
import subprocess
import sys
import tempfile

import numpy as np

from . import audioreport as ar
from . import delivercheck as dc
from . import paths

SIB_BANDS_HZ = ((5000, 9000), (100, 5000))   # (sibilance band, speech body band)
SIB_NPERSEG = 4096
FLATNESS_FRAME = 2048
HUM_NPERSEG = 16384
HUM_QUIET_SHARE = 0.25
HUM_HARMONICS = 5
HUM_PEAK_BINS = 1                 # the peak is the largest of the bin and one each side
HUM_NEIGHBOURS = (4, 12)          # neighbour bins 4 to 12 away on each side
FRAME_S = 0.1
WADA_RANGE_DB = (-10.0, 50.0)
WADA_SAMPLES = 200000
WADA_SEED = 0
LAG_SECONDS = 30.0
MAX_LAG_MS = 250.0
BATCH = 64                         # FFT frames per numpy batch
DELTAS = ("integrated_lufs", "lra_lu", "true_peak_dbtp", "sibilance_ratio_db", "flatness",
          "hum_excess_db_50", "hum_excess_db_60", "wada_snr_db")


def params():
    return {"sibilance_bands_hz": SIB_BANDS_HZ, "sibilance_nperseg": SIB_NPERSEG,
            "flatness_frame": FLATNESS_FRAME, "hum_nperseg": HUM_NPERSEG,
            "hum_quiet_share": HUM_QUIET_SHARE, "hum_harmonics": HUM_HARMONICS,
            "hum_neighbour_bins": HUM_NEIGHBOURS, "frame_s": FRAME_S,
            "wada_range_db": WADA_RANGE_DB, "wada_samples": WADA_SAMPLES, "wada_seed": WADA_SEED}


def _finite(v, nd=2):
    return round(float(v), nd) if v is not None and math.isfinite(v) else None


# ---------------------------------------------------------------------------
# Reading audio (also used by roomtone and duckplan)
# ---------------------------------------------------------------------------

def decode(path, stream=0, start=None, end=None, rate=None):
    """(samples, rate): one audio stream mixed to mono as float64, at its own
    rate or `rate`. Raises ar.ReportError."""
    # shortcut: the whole range is held in memory (about 0.7 GB per hour at 48 kHz);
    # give a range for a long file, or move this to block reads if that becomes common.
    info = ar.audio_stream(path, stream)
    rate = int(rate or info["sample_rate"])
    proc = subprocess.run([dc.FFMPEG, "-v", "error", "-nostdin", *ar._range_args(start, end),
                           "-i", dc._src(path), "-map", f"0:a:{stream}", "-ac", "1",
                           "-ar", str(rate), "-f", "f32le", "-acodec", "pcm_f32le", "-"],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise ar.ReportError(f"ffmpeg could not decode {path}: "
                             f"{proc.stderr.decode(errors='replace').strip()[-300:]}")
    x = np.frombuffer(proc.stdout, "<f4").astype(np.float64)
    if not len(x):
        raise ar.ReportError(f"no audio decoded from {path} in that range")
    return x, rate


def frame_power(path, stream=0, hop_s=FRAME_S, af=None):
    """Per hop_s window of one stream, read in blocks so an hour costs little
    memory: {sample_rate, channels, samples, power (mean square over the
    channels), peak (largest absolute sample)}. A last part-window is left
    out. `af` is an ffmpeg filter run first (it must keep the channel count)."""
    info = ar.audio_stream(path, stream)
    rate, ch = info["sample_rate"], info["channels"]
    hop = max(1, int(round(rate * hop_s)))
    cmd = [dc.FFMPEG, "-v", "error", "-nostdin", "-i", dc._src(path), "-map", f"0:a:{stream}",
           *(["-af", af] if af else []), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    frame = 4 * ch
    step = hop * frame * 600
    power, peak, n = [], [], 0
    # stderr goes to a file: a pipe nobody reads fills on a damaged file and stalls ffmpeg.
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf)
    try:
        pending = b""
        while True:
            raw = proc.stdout.read(step)
            pending += raw
            whole = len(pending) // (hop * frame) * (hop * frame)
            if whole:
                x = np.frombuffer(pending[:whole], "<f4").astype(np.float64).reshape(-1, hop, ch)
                power.append((x ** 2).mean(axis=(1, 2)))
                peak.append(np.abs(x).max(axis=(1, 2)))
                n += whole // frame
                pending = pending[whole:]
            if not raw:
                n += len(pending) // frame
                break
    finally:
        proc.stdout.close()
        proc.wait()
        errf.seek(0)
        err = errf.read().decode(errors="replace")
        errf.close()
    if proc.returncode != 0:
        raise ar.ReportError(f"ffmpeg could not decode {path}: {err.strip()[-300:]}")
    return {"sample_rate": rate, "channels": ch, "samples": n, "hop_s": hop / rate,
            "power": np.concatenate(power) if power else np.zeros(0),
            "peak": np.concatenate(peak) if peak else np.zeros(0)}


# ---------------------------------------------------------------------------
# The measures
# ---------------------------------------------------------------------------

def _spectra(x, n, step):
    """Power spectra of Hann-windowed frames of n samples every step, in batches."""
    w = np.hanning(n)
    count = 1 + (len(x) - n) // step if len(x) >= n else 0
    for i in range(0, count, BATCH):
        k = np.arange(i, min(i + BATCH, count))
        yield np.abs(np.fft.rfft(x[k[:, None] * step + np.arange(n)] * w, axis=1)) ** 2


def welch(x, nperseg):
    """Mean power spectrum, Hann window, half overlap; None when x is shorter."""
    total, count = None, 0
    for p in _spectra(x, nperseg, nperseg // 2):
        total = p.sum(axis=0) if total is None else total + p.sum(axis=0)
        count += len(p)
    return None if not count else total / count


def sibilance_ratio_db(x, rate):
    (hi_lo, hi_hi), (lo_lo, lo_hi) = SIB_BANDS_HZ
    p = welch(x, SIB_NPERSEG)
    if p is None or rate / 2 < hi_hi:
        return None
    f = np.fft.rfftfreq(SIB_NPERSEG, 1.0 / rate)
    hi = p[(f >= hi_lo) & (f < hi_hi)].sum()
    lo = p[(f >= lo_lo) & (f < lo_hi)].sum()
    return _finite(10 * math.log10(hi / lo)) if hi > 0 and lo > 0 else None


def flatness(x):
    """Mean spectral flatness of the magnitude spectrum over frames that hold
    any signal (DC bin left out)."""
    vals = []
    for p in _spectra(x, FLATNESS_FRAME, FLATNESS_FRAME // 2):
        p = np.sqrt(p[:, 1:])
        live = p.mean(axis=1) > 0
        if live.any():
            p = p[live]
            vals.append(np.exp(np.log(p + 1e-30).mean(axis=1)) / p.mean(axis=1))
    return _finite(np.concatenate(vals).mean(), 3) if vals else None


def quiet_frames(x, rate, share=HUM_QUIET_SHARE):
    """The quietest `share` of 100 ms frames (digital silence left out), in time order."""
    n = max(1, int(round(rate * FRAME_S)))
    fr = x[:len(x) // n * n].reshape(-1, n)
    pw = (fr ** 2).mean(axis=1)
    live = np.flatnonzero(pw > 0)
    if not len(live):
        return np.zeros(0)
    pick = live[np.argsort(pw[live], kind="stable")[:max(1, int(len(live) * share))]]
    return fr[np.sort(pick)].ravel()


def hum_excess_db(x, rate):
    """{50: dB, 60: dB}: mean excess of harmonics 1-5 over neighbouring bins
    in the quietest frames; None each when there is too little quiet audio."""
    q = quiet_frames(x, rate)
    p = welch(q, HUM_NPERSEG) if len(q) >= HUM_NPERSEG else None
    out = {}
    for base in (50, 60):
        if p is None:
            out[base] = None
            continue
        df = rate / HUM_NPERSEG
        a, b = HUM_NEIGHBOURS
        ex = []
        for k in range(1, HUM_HARMONICS + 1):
            c = int(round(base * k / df))
            if c - b < 1 or c + b + 1 > len(p):
                continue
            pk = p[c - HUM_PEAK_BINS:c + HUM_PEAK_BINS + 1].max()
            nb = np.median(np.r_[p[c - b:c - a + 1], p[c + a:c + b + 1]])
            if pk > 0 and nb > 0:
                ex.append(10 * math.log10(pk / nb))
        out[base] = _finite(np.mean(ex)) if ex else None
    return out


_WADA = None


def _wada_table():
    """(G, SNR dB) for SNRs across WADA_RANGE_DB, by simulation with a fixed seed."""
    global _WADA
    if _WADA is None:
        rng = np.random.default_rng(WADA_SEED)
        s = rng.gamma(0.4, 1.0, WADA_SAMPLES) * rng.choice([-1.0, 1.0], WADA_SAMPLES)
        s /= np.sqrt(np.mean(s ** 2))
        noise = rng.standard_normal(WADA_SAMPLES)
        snrs = np.arange(WADA_RANGE_DB[0], WADA_RANGE_DB[1] + 1.0, 1.0)
        g = []
        for d in snrs:
            a = np.abs(s * 10 ** (d / 20) + noise) + 1e-12
            g.append(math.log(a.mean()) - np.log(a).mean())
        order = np.argsort(g)
        _WADA = (np.array(g)[order], snrs[order])
    return _WADA


def wada_snr_db(x):
    a = np.abs(x - x.mean())
    a = a[a > 1e-9]
    if len(a) < 1000:
        return None
    g = math.log(a.mean()) - np.log(a).mean()
    table, snrs = _wada_table()
    return _finite(np.interp(g, table, snrs), 1)


def measure(path, stream=0, start=None, end=None):
    """Every number for one stream (or a range of it)."""
    dc.check_tools(ffmpeg=True)
    if end is not None and end <= (start or 0.0):
        raise ValueError("--end must come after --start")
    x, rate = decode(path, stream, start, end)
    loud, _ = ar.loudness(path, stream, start, end)
    hum = hum_excess_db(x, rate)
    return {"file": path, "stream": stream, "sample_rate": rate,
            "range_s": [start or 0.0, round((start or 0.0) + len(x) / rate, 3)],
            "integrated_lufs": _finite(loud["integrated_lufs"], 1),
            "lra_lu": _finite(loud["lra_lu"], 1),
            "true_peak_dbtp": _finite(loud["true_peak_dbtp"], 1),
            "sibilance_ratio_db": sibilance_ratio_db(x, rate),
            "flatness": flatness(x),
            "hum_excess_db_50": hum[50], "hum_excess_db_60": hum[60],
            "wada_snr_db": wada_snr_db(x), "params": params()}


def lag(a_path, b_path, stream=0, stream_b=0, start=None, seconds=LAG_SECONDS,
        max_lag_ms=MAX_LAG_MS):
    """Where B's content sits against A's, in samples at A's rate (positive: B late)."""
    dc.check_tools(ffmpeg=True)
    if seconds <= 0 or max_lag_ms <= 0:
        raise ValueError("--seconds and --max-lag-ms must be positive")
    end = (start or 0.0) + seconds
    a, rate = decode(a_path, stream, start, end)
    b, _ = decode(b_path, stream_b, start, end, rate=rate)
    a, b = a - a.mean(), b - b.mean()
    m = int(rate * max_lag_ms / 1000)
    nfft = 1 << (len(a) + len(b) - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(b, nfft) * np.conj(np.fft.rfft(a, nfft)), nfft)
    lags = np.r_[np.arange(0, m + 1), np.arange(-m, 0)]
    vals = np.r_[c[:m + 1], c[nfft - m:]]
    i = int(np.argmax(np.abs(vals)))
    norm = math.sqrt(float((a ** 2).sum() * (b ** 2).sum()))
    k = int(lags[i])
    return {"a": a_path, "b": b_path, "sample_rate": rate, "lag_samples": k,
            "lag_ms": round(1000.0 * k / rate, 2),
            "peak_corr": round(abs(float(vals[i])) / norm, 3) if norm > 0 else None,
            "polarity": "inverted" if vals[i] < 0 else "same",
            "window_s": [start or 0.0, end], "max_lag_ms": max_lag_ms}


def compare(a_path, b_path, stream=0, stream_b=0, start=None, end=None):
    """Both files' numbers, B minus A, and the lag of B against A."""
    a = measure(a_path, stream, start, end)
    b = measure(b_path, stream_b, start, end)
    delta = {k: (round(b[k] - a[k], 3) if a[k] is not None and b[k] is not None else None)
             for k in DELTAS}
    seconds = (end - (start or 0.0)) if end is not None else LAG_SECONDS
    return {"a": a, "b": b, "delta": delta,
            "lag": lag(a_path, b_path, stream, stream_b, start, min(seconds, LAG_SECONDS))}


def summary(r):
    """One line per file."""
    return (f"{r['file']} s{r['stream']}: I {r['integrated_lufs']} LUFS, LRA {r['lra_lu']}, "
            f"TP {r['true_peak_dbtp']}, sibilance {r['sibilance_ratio_db']} dB, flatness "
            f"{r['flatness']}, hum 50/60 {r['hum_excess_db_50']}/{r['hum_excess_db_60']} dB, "
            f"WADA SNR {r['wada_snr_db']} dB")


def lag_line(g):
    return (f"lag {g['lag_samples']} samples ({g['lag_ms']} ms) at {g['sample_rate']} Hz, "
            f"B {'late' if g['lag_samples'] > 0 else 'early' if g['lag_samples'] < 0 else 'aligned'}"
            f", correlation {g['peak_corr']}, polarity {g['polarity']}")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    m = sub.add_parser("measure", help="the numbers for one file")
    m.add_argument("file")
    c = sub.add_parser("compare", help="two files' numbers, B minus A, and the lag")
    g = sub.add_parser("lag", help="cross-correlation lag of B against A")
    for q in (c, g):
        q.add_argument("a")
        q.add_argument("b")
        q.add_argument("--stream-b", type=int, default=0, help="Audio stream of B")
    for q in (m, c, g):
        q.add_argument("--stream", type=int, default=0, help="Audio stream (of A), 0 is the first")
        q.add_argument("--start", help="Where to start: seconds, m:ss or h:mm:ss")
    for q in (m, c):
        q.add_argument("--end", help="Where to stop (default: the end)")
        q.add_argument("--out", help="Write the JSON here, outside any git tree")
    g.add_argument("--seconds", type=float, default=LAG_SECONDS, help="How much to correlate")
    g.add_argument("--max-lag-ms", type=float, default=MAX_LAG_MS)


def run(args):
    """Exit 0 the numbers are printed (and written); 1 they could not be."""
    out = getattr(args, "out", None)
    problem = paths.out_problem(out, any_git_tree=True) if out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        start = ar.parse_time(args.start) if args.start else None
        end = ar.parse_time(args.end) if getattr(args, "end", None) else None
        if args.action == "measure":
            r = measure(args.file, args.stream, start, end)
            text = summary(r)
        elif args.action == "lag":
            r = lag(args.a, args.b, args.stream, args.stream_b, start, args.seconds,
                    args.max_lag_ms)
            text = lag_line(r)
        else:
            r = compare(args.a, args.b, args.stream, args.stream_b, start, end)
            text = "\n".join([f"A {summary(r['a'])}", f"B {summary(r['b'])}",
                              "B-A " + ", ".join(f"{k} {v:+g}" for k, v in r["delta"].items()
                                                 if v is not None),
                              lag_line(r["lag"])])
    except (ar.ReportError, dc.CheckError, dc.ToolMissing, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(text)
    if out:
        paths.write_private(out, json.dumps(r, indent=1) + "\n", any_git_tree=True)
        print(f"Wrote {out}", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.dxmetrics",
                                 description="Before-and-after dialogue numbers with no clean "
                                             "reference. Offline; judges nothing.")
    add_arguments(ap)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
