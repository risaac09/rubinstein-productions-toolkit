"""
rpresolve.audioreport: what a mix sounds like, as numbers and pictures a
reader can check. Offline: ffmpeg decodes and measures, numpy counts;
nothing here touches Resolve or sends audio anywhere.

report() reads one audio stream of a file, whole or a time range, and
writes into an output folder outside any git tree:
    audio-report.json   every number below
    loudness.png        short-term loudness (dark line) and the momentary
                        spread (light) over time, silence gaps shaded,
                        clip marks red and click marks orange on the floor,
                        the target dashed green when there is one
    spectrogram.png     ffmpeg showspectrumpic, log frequency from 20 Hz

The numbers:
    integrated_lufs, lra_lu, true_peak_dbtp
                  ebur128 with peak=true, read as deliver_check reads them
    max_momentary_lufs, max_short_term_lufs
                  the loudest 400 ms and 3 s from ebur128's 100 ms log
    channels      per channel: peak_dbfs, rms_dbfs, crest_db (peak over
                  RMS), dc_offset, full_scale_samples (|x| >= CLIP_LEVEL)
                  and clip_runs: runs of CLIP_RUN or more samples held at
                  the channel's own peak. A flat top at the peak is clipping
                  whether or not it was turned down afterwards; a lossy
                  encode smears flat tops, so there true peak speaks instead
    clicks        times of isolated discontinuities: a sample whose second
                  difference is CLICK_SIGMA times its 100 ms block's robust
                  sigma and at least CLICK_FLOOR, grouped within CLICK_GAP_S
    silences      runs of 100 ms windows whose loudest channel is under
                  silence_db for at least silence_min_s
    dropouts      the same for digital silence (under -120 dBFS) that
                  neither starts nor ends the range: a gap in the signal
    stereo        channels 1 and 2: correlation (1 the same signal, 0
                  unrelated, below 0 out of phase) and balance_db (1 minus 2)
    hop_s, rms_db the loudest channel's RMS per 100 ms window, dBFS (floor
                  -120), the window starting at range start + i * hop_s
    noise_floor_dbfs, loud_dbfs, spread_db
                  10th and 95th percentile of the 100 ms RMS (digital
                  silence left out), and the gap between them: a rough
                  signal-to-noise figure, not a measured one
    octave_bands_db
                  each octave's share of the energy, 31.5 Hz to 16 kHz, in
                  dB; independent of level, so two files compare directly
    flags         what needs an ear, each with its metric, value, threshold
                  and the first times it happens: clipping, true peak over
                  the ceiling, clicks, dropouts, DC offset, a dead channel,
                  dual mono, channels out of phase, loudness off target.
                  Every threshold is a working default, not a standard; with
                  --dest the target and ceiling are the house's choices
    reference     with a reference file: its numbers, and this file minus
                  the reference for loudness, range, peaks, crest, spread
                  and every octave band

None of this is listening. A clean report says nothing about whether a
voice is clear or a denoise has damaged it; that needs ears.
"""

import json
import math
import os
import re
import subprocess
import tempfile

import numpy as np

from . import delivercheck as dc
from .paths import OutputRefused, in_any_git_tree

WINDOW_S = 0.1
CHUNK_WINDOWS = 600          # 60 s of audio per read
CLIP_LEVEL = 0.999           # about -0.01 dBFS
CLIP_RUN = 3
CLIP_MARKS = 200             # clip times kept per channel, for the picture
CLICK_SIGMA = 12.0
CLICK_FLOOR = 0.02           # about -34 dBFS: a quieter step, or a tone cut at a zero
                             # crossing, is not reported
CLICK_GAP_S = 0.002
DIGITAL_SILENCE_DB = -120.0
OCTAVES_HZ = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
DEFAULTS = {"silence_db": -50.0, "silence_min_s": 0.5, "dc_max": 0.01, "clip_runs_min": 3,
            "true_peak_max_dbtp": -1.0, "tolerance_lu": 0.5, "dead_gap_db": 30.0,
            "dual_mono_corr": 0.999, "phase_corr": -0.3}
FLAG_TIMES = 10              # times listed per flag, at least FLAG_GAP_S apart
FLAG_GAP_S = 0.5
PLOT_W, PLOT_H = 1600, 480
PLOT_FLOOR_LUFS = -60.0
# ebur128 logs -120.7 where it has no reading: before its 400 ms or 3 s window
# fills, and over digital silence.
NO_READING_LUFS = -120.0


class ReportError(RuntimeError):
    """The file cannot be read or the report cannot be written."""


def parse_time(text):
    """Seconds from '75', '75.5', '1:15' or '0:01:15.5'. Raises ValueError."""
    parts = str(text).strip().split(":")
    if not 1 <= len(parts) <= 3 or any(p.strip() == "" for p in parts):
        raise ValueError(f"not a time: {text!r} (seconds, m:ss or h:mm:ss)")
    s = 0.0
    for p in parts:
        try:
            s = s * 60 + float(p)
        except ValueError:
            raise ValueError(f"not a time: {text!r} (seconds, m:ss or h:mm:ss)")
    if not math.isfinite(s):
        raise ValueError(f"not a time: {text!r} (seconds, m:ss or h:mm:ss)")
    if s < 0:
        raise ValueError(f"a time cannot be negative: {text!r}")
    return s


def _db(x, floor=DIGITAL_SILENCE_DB):
    return round(float(max(20 * math.log10(x), floor)), 2) + 0.0 if x > 0 else floor


def _runs(mask):
    """(starts, ends) of the True runs in a 1-D bool array, ends exclusive."""
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1)


def _range_args(start, end):
    args = ["-ss", f"{start:.3f}"] if start else []
    return args + (["-t", f"{end - (start or 0.0):.3f}"] if end is not None else [])


# ---------------------------------------------------------------------------
# ffmpeg passes
# ---------------------------------------------------------------------------

_FRAME = re.compile(r"\bt:\s*([-\d.]+)\s+TARGET:.*?\bM:\s*([-\d.]+|-?inf)\s+S:\s*([-\d.]+|-?inf)")


def parse_framelog(text):
    """[(t, momentary, short_term)] from ebur128's verbose frame log."""
    return [(float(t), float(m), float(s)) for t, m, s in _FRAME.findall(text)]


def loudness(path, stream=0, start=None, end=None):
    """ebur128's summary and its 100 ms log, times on the file's clock."""
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-v", "verbose",
                    *_range_args(start, end), "-i", dc._src(path), "-map", f"0:a:{stream}",
                    "-af", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
                   dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise ReportError(f"ffmpeg could not measure loudness in {path}: "
                          f"{proc.stderr.strip()[-300:]}")
    try:
        summary = dc.parse_ebur128(proc.stderr)
    except dc.CheckError as e:  # no summary: the range holds no audio
        raise ReportError(f"no loudness read from {path} in that range ({e})")
    frames = [(t + (start or 0.0), m, s) for t, m, s in parse_framelog(proc.stderr)]
    return summary, frames


def spectrogram(path, out_png, rate, stream=0, start=None, end=None):
    # showspectrumpic holds the whole range in memory, about 0.6 GB per 30 min of
    # mono at 48 kHz, so the picture is of the mono mix; give a long film a range.
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y",
                    *_range_args(start, end), "-i", dc._src(path), "-filter_complex",
                    f"[0:a:{stream}]aformat=channel_layouts=mono,showspectrumpic=s={PLOT_W}x{PLOT_H}:fscale=log:start=20:stop={rate // 2}[v]",
                    "-map", "[v]", "-frames:v", "1", out_png], dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise ReportError(f"ffmpeg could not draw the spectrogram: {proc.stderr.strip()[-300:]}")


def audio_stream(path, stream=0):
    """{sample_rate, channels, codec, duration_s} of the stream; raises ReportError."""
    try:
        info = dc.probe(path)
    except dc.CheckError as e:
        raise ReportError(str(e))
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if stream >= len(audio):
        raise ReportError(f"{path} has {len(audio)} audio stream(s); there is no stream {stream}.")
    a = audio[stream]
    if not a.get("sample_rate") or not a.get("channels"):
        raise ReportError(f"ffprobe gives no sample rate or channel count for audio stream "
                          f"{stream} of {path}.")
    dur = a.get("duration") or (info.get("format") or {}).get("duration")
    return {"sample_rate": int(a["sample_rate"]), "channels": int(a["channels"]),
            "codec": a.get("codec_name"), "duration_s": float(dur) if dur else None}


# ---------------------------------------------------------------------------
# The sample pass
# ---------------------------------------------------------------------------

class _Stats:
    """Running per-channel sums, window levels, clicks and spectrum over
    blocks of decoded samples (n, channels) at `rate`."""

    def __init__(self, rate, channels, t0):
        self.rate, self.ch, self.t0 = rate, channels, t0
        self.win = max(1, int(round(rate * WINDOW_S)))
        self.n = 0
        self.sum = np.zeros(channels)
        self.sumsq = np.zeros(channels)
        self.sum_12 = 0.0
        self.peak = np.zeros(channels)
        self.full_scale = np.zeros(channels, dtype=np.int64)
        self.clip_runs = np.zeros(channels, dtype=np.int64)
        self.clip_at = [[] for _ in range(channels)]
        self.window_db = []
        self.click_at = []
        self.power = np.zeros(self.win // 2 + 1)
        self.frames = 0
        self.hann = np.hanning(self.win)

    def add(self, x):
        t_block = self.t0 + self.n / self.rate
        self.sum += x.sum(axis=0)
        self.sumsq += (x.astype(np.float64) ** 2).sum(axis=0)
        if self.ch >= 2:
            self.sum_12 += float(np.dot(x[:, 0].astype(np.float64), x[:, 1]))
        mag = np.abs(x)
        self.full_scale += (mag >= CLIP_LEVEL).sum(axis=0)
        for c in range(self.ch):
            self._clip_runs(c, mag[:, c], t_block)
        full = len(x) // self.win * self.win
        if full:
            w = x[:full].reshape(-1, self.win, self.ch)
            rms = np.sqrt((w.astype(np.float64) ** 2).mean(axis=1)).max(axis=1)
            self.window_db += [_db(r) for r in rms]
            spec = np.fft.rfft(w.mean(axis=2) * self.hann, axis=1)
            self.power += (np.abs(spec) ** 2).sum(axis=0)
            self.frames += len(w)
            self._clicks(x[:full], t_block)
        if len(x) > full:  # the last part-window at the end of the stream
            self.window_db.append(_db(float(np.sqrt((x[full:].astype(np.float64) ** 2)
                                                    .mean(axis=0)).max())))
        self.n += len(x)

    def _clip_runs(self, c, mag, t_block):
        # shortcut: a run that straddles two reads counts as two, at most once a minute;
        # read the range whole if a count near the flag line matters.
        top = mag.max(initial=0.0)
        if top < self.peak[c] - 1e-7:
            return
        if top > self.peak[c] + 1e-7:  # a new peak: runs held at the old one were not clips
            self.peak[c], self.clip_runs[c], self.clip_at[c] = top, 0, []
        s, e = _runs(mag >= self.peak[c] - 1e-7)
        held = s[(e - s) >= CLIP_RUN]
        self.clip_runs[c] += len(held)
        self.clip_at[c] += [round(t_block + i / self.rate, 3) for i in held[:CLIP_MARKS]]

    def _clicks(self, x, t_block):
        # shortcut: the second difference stops at each read's edges (once a minute),
        # so a click on a read's first or last sample is seen at half strength.
        e = np.zeros_like(x)
        e[1:-1] = x[2:] - 2 * x[1:-1] + x[:-2]
        w = e.reshape(-1, self.win, self.ch)
        dev = np.abs(w - np.median(w, axis=1, keepdims=True))
        sigma = 1.4826 * np.median(dev, axis=1, keepdims=True)
        # A window that is mostly silence has a sigma near 0, so the onset of sound
        # at its end would read as clicks; the busier neighbour sets the bar instead.
        sigma[1:] = np.maximum(sigma[1:], sigma[:-1].copy())
        sigma[:-1] = np.maximum(sigma[:-1], sigma[1:].copy())
        # The corners of a flat top are discontinuities too; clipping is counted
        # as clip runs, so a sample on or beside a held value is not a click.
        same = np.zeros(x.shape, bool)
        same[1:] |= x[1:] == x[:-1]
        same[:-1] |= x[:-1] == x[1:]
        flat = same & (np.abs(x) > CLICK_FLOOR)
        near = flat.copy()
        near[1:] |= flat[:-1]
        near[:-1] |= flat[1:]
        hit = ((dev > np.maximum(CLICK_SIGMA * sigma, CLICK_FLOOR))
               & ~near.reshape(w.shape)).any(axis=2).ravel()
        for i in np.flatnonzero(hit):
            t = t_block + i / self.rate
            if not self.click_at or t - self.click_at[-1] > CLICK_GAP_S:
                self.click_at.append(round(float(t), 4))

    def result(self, silence_db, silence_min_s):
        n = max(self.n, 1)
        rms = np.sqrt(self.sumsq / n)
        chans = [{"peak_dbfs": _db(self.peak[c]), "rms_dbfs": _db(rms[c]),
                  "crest_db": round(_db(self.peak[c]) - _db(rms[c]), 2) if rms[c] > 0 else None,
                  "dc_offset": round(float(self.sum[c] / n), 5),
                  "full_scale_samples": int(self.full_scale[c]),
                  "clip_runs": int(self.clip_runs[c]) if self.peak[c] > 0 else 0}
                 for c in range(self.ch)]
        db = np.array(self.window_db)
        s, e = _runs(db < silence_db)
        keep = (e - s) * WINDOW_S >= silence_min_s - 1e-9
        silences = [[round(self.t0 + a * WINDOW_S, 2), round(self.t0 + min(b * WINDOW_S, n / self.rate), 2)]
                    for a, b in zip(s[keep].tolist(), e[keep].tolist())]
        s, e = _runs(db <= DIGITAL_SILENCE_DB)
        keep = ((e - s) * WINDOW_S >= silence_min_s - 1e-9) & (s > 0) & (e < len(db))
        dropouts = [[round(self.t0 + a * WINDOW_S, 2), round(self.t0 + b * WINDOW_S, 2)]
                    for a, b in zip(s[keep].tolist(), e[keep].tolist())]
        live = db[db > DIGITAL_SILENCE_DB]
        floor = float(np.percentile(live, 10)) if len(live) else None
        loud = float(np.percentile(live, 95)) if len(live) else None
        clips = sorted({t for c in range(self.ch) if self.peak[c] > 0 for t in self.clip_at[c]})
        return {"channels": chans, "clicks": self.click_at, "clips": clips[:CLIP_MARKS],
                "silences": silences, "dropouts": dropouts, "stereo": self.stereo(),
                "noise_floor_dbfs": None if floor is None else round(floor, 1),
                "loud_dbfs": None if loud is None else round(loud, 1),
                "spread_db": None if floor is None else round(loud - floor, 1),
                "octave_bands_db": self.octaves(), "window_db": self.window_db,
                "decoded_s": self.n / self.rate}

    def stereo(self):
        if self.ch < 2 or not self.n:
            return None
        mean = self.sum[:2] / self.n
        var = self.sumsq[:2] / self.n - mean ** 2
        cov = self.sum_12 / self.n - mean[0] * mean[1]
        rms = np.sqrt(self.sumsq[:2] / self.n)
        return {"correlation": round(float(cov / math.sqrt(var[0] * var[1])), 3)
                if var[0] > 0 and var[1] > 0 else None,
                "balance_db": round(_db(rms[0]) - _db(rms[1]), 1)}

    def octaves(self):
        freqs = np.fft.rfftfreq(self.win, 1.0 / self.rate)
        total = self.power[freqs >= OCTAVES_HZ[0] / math.sqrt(2)].sum()
        out = {}
        for f in OCTAVES_HZ:
            band = self.power[(freqs >= f / math.sqrt(2)) & (freqs < f * math.sqrt(2))].sum()
            out[f"{f:g}"] = (round(10 * math.log10(band / total), 1)
                             if total > 0 and band > 0 else None)
        return out


def samples(path, info, stream=0, start=None, end=None, silence_db=-50.0, silence_min_s=0.5):
    """One decode of the stream at its own rate and channels (no resampling,
    which would invent overs), counted block by block."""
    st = _Stats(info["sample_rate"], info["channels"], start or 0.0)
    cmd = [dc.FFMPEG, "-v", "error", "-nostdin", *_range_args(start, end), "-i", dc._src(path),
           "-map", f"0:a:{stream}", "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    frame = 4 * st.ch
    step = st.win * CHUNK_WINDOWS * frame
    # stderr goes to a file: a pipe nobody reads fills on a damaged file and stalls ffmpeg.
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf)
    try:
        pending = b""
        while True:
            raw = proc.stdout.read(step)
            pending += raw
            take = len(pending) if not raw else len(pending) // step * step
            if take:
                block = np.frombuffer(pending[:take - take % frame], "<f4").reshape(-1, st.ch)
                if len(block):
                    st.add(block)
                pending = pending[take:]
            if not raw:
                break
    finally:
        proc.stdout.close()
        proc.wait()
        errf.seek(0)
        err = errf.read().decode(errors="replace")
        errf.close()
    if proc.returncode != 0:
        raise ReportError(f"ffmpeg could not decode {path}: {err.strip()[-300:]}")
    if not st.n:
        raise ReportError(f"no audio decoded from {path} in that range")
    return st.result(silence_db, silence_min_s)


# ---------------------------------------------------------------------------
# The loudness picture
# ---------------------------------------------------------------------------

# 3x5 glyphs, rows top to bottom, as bit strings.
_GLYPHS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111",
    "3": "111001111001111", "4": "101101111001001", "5": "111100111001111",
    "6": "111100111101111", "7": "111001001010010", "8": "111101111101111",
    "9": "111101111001111", "-": "000000111000000", ":": "000010000010000",
    ".": "000000000000010", " ": "000000000000000", "I": "111010010010111",
    "L": "100100100100111", "U": "101101101101111", "F": "111100110100100",
    "S": "011100010001110", "R": "110101110101101", "A": "010101111101101",
    "T": "111010010010010", "P": "110101110100100", "D": "110101101101110",
    "B": "110101110101110", "M": "101111111101101",
}


def _text(img, x, y, text, color, scale=2):
    for ch in text.upper():
        bits = _GLYPHS.get(ch, _GLYPHS[" "])
        for i, b in enumerate(bits):
            if b == "1":
                r, c = divmod(i, 3)
                img[y + r * scale:y + (r + 1) * scale, x + c * scale:x + (c + 1) * scale] = color
        x += 4 * scale


def _tick_step(span_s):
    for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200):
        if span_s / step <= 12:
            return step
    return 14400


def _clock(t):
    t = int(round(t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def plot(frames, t0, t1, out_png, silences=(), clicks=(), clips=(), target=None, title=""):
    """loudness.png from ebur128's frames; ffmpeg encodes the PNG."""
    left, right, top, bottom = 56, 16, 30, 26
    w, h = PLOT_W - left - right, PLOT_H - top - bottom
    img = np.full((PLOT_H, PLOT_W, 3), 255, np.uint8)
    span = max(t1 - t0, WINDOW_S)

    def px(t):
        return left + int(round((t - t0) / span * (w - 1)))

    def py(v):
        v = min(max(v, PLOT_FLOOR_LUFS), 0.0)
        return top + int(round(v / PLOT_FLOOR_LUFS * (h - 1)))

    for a, b in silences:
        img[top:top + h, px(a):px(b) + 1] = (226, 232, 240)
    for v in range(0, int(PLOT_FLOOR_LUFS) - 1, -6):
        img[py(v), left:left + w] = (210, 210, 210)
        _text(img, 8, py(v) - 5, f"{v}", (90, 90, 90))
    step = _tick_step(span)
    for k in range(int(math.ceil(t0 / step)), int(t1 // step) + 1):
        x = px(k * step)
        img[top:top + h, x] = (225, 225, 225)
        _text(img, max(left, x - 12), top + h + 8, _clock(k * step), (90, 90, 90))
    if target is not None:
        for x in range(left, left + w, 12):
            img[py(target), x:x + 6] = (30, 150, 60)
    if frames:
        f = np.array(frames)
        edges = t0 + np.arange(w + 1) * span / w
        lo, hi = np.searchsorted(f[:, 0], edges[:-1]), np.searchsorted(f[:, 0], edges[1:])
        prev = None
        for col in range(w):
            # a column with no frame of its own takes the next one, so lines stay unbroken
            a = min(lo[col], len(f) - 1)
            m, s = f[a:max(hi[col], a + 1), 1], f[a:max(hi[col], a + 1), 2]
            m, s = m[m > NO_READING_LUFS], s[s > NO_READING_LUFS]
            if len(m):
                img[py(m.max()):py(m.min()) + 2, left + col] = (140, 175, 220)
            if not len(s):
                prev = None
                continue
            y = py(float(s.mean()))
            y0, y1 = (y, y) if prev is None else sorted((prev, y))
            img[y0 - 1:y1 + 2, left + col] = (20, 60, 140)
            prev = y
    for times, color in ((clicks, (240, 140, 0)), (clips, (210, 30, 30))):
        for t in times:
            img[top + h - 10:top + h, max(left, px(t) - 1):px(t) + 2] = color
    img[top:top + h, left] = img[top + h - 1, left:left + w] = (120, 120, 120)
    _text(img, left, 8, title, (20, 20, 20))
    proc = subprocess.run([dc.FFMPEG, "-v", "error", "-nostdin", "-y", "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-s", f"{PLOT_W}x{PLOT_H}", "-i", "-",
                           "-frames:v", "1", out_png], input=img.tobytes(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if proc.returncode != 0:
        raise ReportError(f"ffmpeg could not write {out_png}: "
                          f"{proc.stderr.decode(errors='replace').strip()[-300:]}")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def measure(path, stream=0, start=None, end=None, th=None):
    """Every number for one file (no pictures)."""
    th = dict(DEFAULTS, **(th or {}))
    dc.check_tools(ffmpeg=True)
    info = audio_stream(path, stream)
    if end is not None and end <= (start or 0.0):
        raise ReportError("--end must come after --start")
    summary, frames = loudness(path, stream, start, end)
    s = samples(path, info, stream, start, end, th["silence_db"], th["silence_min_s"])
    finite = lambda i: [f[i] for f in frames if f[i] > NO_READING_LUFS]  # noqa: E731
    r = {"file": os.path.abspath(path), "stream": stream, **info,
         "range_s": [start or 0.0, round((start or 0.0) + s["decoded_s"], 3)],
         "integrated_lufs": summary["integrated_lufs"], "lra_lu": summary["lra_lu"],
         "true_peak_dbtp": summary["true_peak_dbtp"],
         "max_momentary_lufs": max(finite(1), default=None),
         "max_short_term_lufs": max(finite(2), default=None), **s}
    r["frames"] = frames
    return r


def _flag(name, value, threshold, note, at_s=()):
    spaced = []
    for t in at_s:
        if len(spaced) == FLAG_TIMES:
            break
        if not spaced or t - spaced[-1] >= FLAG_GAP_S:
            spaced.append(t)
    return {"flag": name, "value": value, "threshold": threshold, "note": note, "at_s": spaced}


def flags(r, th):
    """What needs an ear, most likely damage first."""
    out = []
    runs = sum(c["clip_runs"] for c in r["channels"])
    if runs >= th["clip_runs_min"]:
        out.append(_flag("clipping", runs, th["clip_runs_min"],
                         f"{runs} flat-topped runs at the peak", r.get("clips", ())))
    if r.get("dropouts"):
        out.append(_flag("dropout", len(r["dropouts"]), th["silence_min_s"],
                         f"{len(r['dropouts'])} gap(s) of digital silence inside the range",
                         [a for a, _ in r["dropouts"]]))
    if r["clicks"]:
        out.append(_flag("clicks", len(r["clicks"]), CLICK_SIGMA,
                         f"{len(r['clicks'])} isolated discontinuities", r["clicks"]))
    tp = r.get("true_peak_dbtp")
    if tp is not None and tp > th["true_peak_max_dbtp"]:
        out.append(_flag("true_peak", tp, th["true_peak_max_dbtp"],
                         f"true peak {tp} dBTP is over the {th['true_peak_max_dbtp']} dBTP ceiling"))
    levels = [c["rms_dbfs"] for c in r["channels"]]
    for i, lv in enumerate(levels, 1):
        if len(levels) > 1 and max(levels) - lv >= th["dead_gap_db"]:
            out.append(_flag("dead_channel", lv, th["dead_gap_db"],
                             f"channel {i} sits {max(levels) - lv:.0f} dB under the loudest"))
    st = r.get("stereo") or {}
    corr = st.get("correlation")
    if corr is not None and corr >= th["dual_mono_corr"]:
        out.append(_flag("dual_mono", corr, th["dual_mono_corr"],
                         "channels 1 and 2 carry the same signal (mono in stereo; it reads "
                         "about 3 LU louder than the mono source)"))
    elif corr is not None and corr <= th["phase_corr"]:
        out.append(_flag("phase", corr, th["phase_corr"],
                         "channels 1 and 2 are out of phase: a mono fold-down loses level"))
    worst = max((abs(c["dc_offset"]) for c in r["channels"]), default=0)
    if worst > th["dc_max"]:
        out.append(_flag("dc_offset", worst, th["dc_max"], f"DC offset {worst}"))
    if th.get("target_lufs") is not None and r.get("integrated_lufs") is not None:
        off = r["integrated_lufs"] - th["target_lufs"]
        if abs(off) > th["tolerance_lu"]:
            out.append(_flag("loudness", r["integrated_lufs"], th["target_lufs"],
                             f"integrated {r['integrated_lufs']} LUFS is {off:+.1f} LU off the "
                             f"{th['target_lufs']} target"))
    return out


COMPARED = ("integrated_lufs", "lra_lu", "true_peak_dbtp", "max_short_term_lufs",
            "noise_floor_dbfs", "spread_db")


def compare(r, ref):
    """This file minus the reference, field by field and per octave."""
    def minus(a, b):
        return None if a is None or b is None else round(a - b, 2)
    d = {k: minus(r.get(k), ref.get(k)) for k in COMPARED}
    crest = lambda x: max((c["crest_db"] for c in x["channels"] if c["crest_db"] is not None),  # noqa: E731
                          default=None)
    d["crest_db"] = minus(crest(r), crest(ref))
    d["octave_bands_db"] = {k: minus(v, ref["octave_bands_db"].get(k))
                            for k, v in r["octave_bands_db"].items()}
    return d


def _public(r):
    return {k: v for k, v in r.items() if k not in ("frames", "window_db", "decoded_s")}


def report(path, out_dir, stream=0, start=None, end=None, reference=None, ref_stream=0,
           target_lufs=None, th=None):
    """Write audio-report.json, loudness.png and spectrogram.png into out_dir
    (made if its parent exists) and return the report."""
    if in_any_git_tree(out_dir):
        raise OutputRefused(f"{out_dir} is inside a git working tree. The report names "
                            "media; write it outside any repository.")
    if not os.path.isdir(out_dir):
        os.mkdir(out_dir)  # one level only, and before the long reads: the parent must exist
    th = dict(DEFAULTS, **(th or {}), target_lufs=target_lufs)
    r = measure(path, stream, start, end, th)
    r["flags"] = flags(r, th)
    r["thresholds"] = th
    r["thresholds_note"] = ("Working defaults, not standards; tune them on your own footage. "
                            "A --dest target and ceiling are the house's choices.")
    if reference:
        ref = measure(reference, ref_stream, th=th)
        r["reference"] = {"numbers": _public(ref), "difference": compare(r, ref)}
    t0, t1 = r["range_s"]
    title = (f"I {r['integrated_lufs']} LUFS  LRA {r['lra_lu']} LU  "
             f"TP {r['true_peak_dbtp']} DBTP")
    plot(r["frames"], t0, t1, os.path.join(out_dir, "loudness.png"), r["silences"],
         r["clicks"], r["clips"], target_lufs, title)
    spectrogram(path, os.path.join(out_dir, "spectrogram.png"), r["sample_rate"], stream, start, end)
    out = _public(r)
    out["hop_s"], out["rms_db"] = WINDOW_S, [round(v, 1) for v in r["window_db"]]
    out["images"] = {"loudness": os.path.join(out_dir, "loudness.png"),
                     "spectrogram": os.path.join(out_dir, "spectrogram.png")}
    with open(os.path.join(out_dir, "audio-report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def format_summary(r):
    lines = [f"{os.path.basename(r['file'])}  stream {r['stream']}  "
             f"{r['range_s'][0]:.1f}-{r['range_s'][1]:.1f} s  "
             f"{r['channels'] and len(r['channels'])} ch @ {r['sample_rate']} Hz",
             f"Loudness   I {r['integrated_lufs']} LUFS, LRA {r['lra_lu']} LU, true peak "
             f"{r['true_peak_dbtp']} dBTP, max S {r['max_short_term_lufs']}, "
             f"max M {r['max_momentary_lufs']}",
             f"Level      floor {r['noise_floor_dbfs']} dBFS, loud {r['loud_dbfs']} dBFS, "
             f"spread {r['spread_db']} dB"]
    for i, c in enumerate(r["channels"], 1):
        lines.append(f"Ch {i}       peak {c['peak_dbfs']} dBFS, RMS {c['rms_dbfs']}, crest "
                     f"{c['crest_db']} dB, DC {c['dc_offset']}, clip runs {c['clip_runs']}, "
                     f"full-scale samples {c['full_scale_samples']}")
    lines.append("Octaves    " + "  ".join(f"{k}:{v}" for k, v in r["octave_bands_db"].items()))
    lines.append(f"Silences   {len(r['silences'])}" + (
        f" (longest {max(b - a for a, b in r['silences']):.1f} s)" if r["silences"] else ""))
    lines.append(f"Clicks     {len(r['clicks'])}")
    if "reference" in r:
        d = r["reference"]["difference"]
        lines.append("Vs ref     " + ", ".join(f"{k} {v:+}" for k, v in d.items()
                                                if k != "octave_bands_db" and v is not None))
        lines.append("Octaves    " + "  ".join(f"{k}:{v:+}" for k, v in d["octave_bands_db"].items()
                                                if v is not None))
    if r.get("stereo"):
        lines.append(f"Stereo     correlation {r['stereo']['correlation']}, balance "
                     f"{r['stereo']['balance_db']} dB")
    lines += [f"FLAG       {f['note']}" + (f" (at {', '.join(f'{t:g}' for t in f['at_s'])} s)"
                                           if f["at_s"] else "")
              for f in r["flags"]] or ["Flags      none"]
    lines.append(f"Images     {r['images']['loudness']}, {r['images']['spectrogram']}")
    return "\n".join(lines) + "\n"
