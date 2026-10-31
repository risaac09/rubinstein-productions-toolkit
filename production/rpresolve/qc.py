"""
rpresolve.qc: one FAIL-first table for a delivered file. Offline: ffprobe
and ffmpeg only, never Resolve. Stdlib only.

run() joins two sources into one row list:
    deliver_check   every row delivercheck.check() gives, when a destination
                    is named (loudness and true peak are read once, below)
    ffmpeg          one decode pass for blackdetect, freezedetect, scdet,
                    silencedetect and astats, one for ebur128

Rows:
    duration        against an expected duration, when given
    blackdetect     no black segment of THRESHOLDS["black_min_s"] or longer
    freezedetect    no frozen segment of freeze_min_s or longer
    scdet           the cut count; against a shot list the count must equal
                    its rows minus one (+/- scdet_tolerance), otherwise SKIP
    loudness        ebur128 integrated LUFS against the destination's target
                    and tolerance; SKIP with the value when there is none
    true_peak       ebur128 true peak at or under the destination's maximum,
                    else THRESHOLDS["true_peak_max_dbtp"]
    astats_peak     overall sample peak under astats_peak_max_db (clipping)
    astats_dc       overall DC offset under astats_dc_max
    silencedetect   no gap below silence_noise_db of silence_min_s or longer

Sound rows (VP-121). Every threshold is a working default, printed in the row
and set with --set; none is a standard. They are numbers, not listening:
    lra                 ebur128 loudness range, reported, never checked
    true_peak_per_cut   with cut times, each shot's true peak under the
                        ceiling of true_peak (the destination's, else
                        true_peak_max_dbtp); FAIL names the shot and its time
    silence_at_cut      with cut times, WARN where a 10 ms RMS envelope sits
                        under silence_cut_db across a cut for more than
                        silence_cut_frames frames
    speech_vs_rest      with --words, integrated loudness of the speech spans
                        (words joined across gaps under speech_join_s, then
                        padded speech_pad_s) against the rest; WARN when the
                        rest is louder than speech minus speech_rest_margin_lu
    phone_proxy         the mix folded to mono and high-passed at
                        phone_hp_hz: its loudest short-term loudness against
                        the full mix's; WARN when it drops more than
                        phone_drop_lu
    stems_sum           with --stems, the stems summed against the mix:
                        integrated within stems_lu_tol LU, residual RMS under
                        stems_residual_db
Cut times come from a start column of the --shots CSV (start_s, start, in_s,
in, time_s, time or cut_s; the first shot's start is not a cut), else from
the scdet cuts of the file itself. WARN does not fail the table.

Offline rows (VP-23a, VP-20). Thresholds are working defaults, printed in the row:
    blackdetect        black that starts within black_lead_start_s of the head
                       and lasts under black_lead_max_s is a fade-in: WARN
                       (decision 30). Longer leading black, or black anywhere
                       else, stays FAIL
    social_fps         on a social destination (aspect 9x16 or a key starting
                       "social"), the video frame rate against Instagram's
                       30 fps minimum (decision 14): WARN under
                       social_min_fps - social_fps_tol
    sha256             sha256 of the file, always reported; with --sha256 HEX
                       the hash must match (FAIL)
    safe_zone          on a social destination, frames sampled safe_frames
                       times; an edge that stays on the same pixel in every
                       usable frame is burned-in text or a logo, and WARN when
                       those edges fill more than safe_persist_frac of the top
                       safe_top, bottom safe_bottom or side safe_side margin.
                       A locked-off shot with a hard edge can trip it

Portrait renders (height over width) add four hook rows, WARN rather than
FAIL because the common hook rules are folklore; they read the same detect
pass, no extra decode:
    hook_open       frame 0 is not black, a fade or a still of hook_still_s+
    hook_sound      sound starts within hook_sound_s (no silence from 0)
    dead_tail       the last tail_s are not frozen or silent
    hook_caption    a sidecar <stem>.srt's first cue starts by hook_caption_s;
                    SKIP with no sidecar (burnt-in captions need OCR)

A row is {check, status, found, expected, source[, note]}; status is PASS,
FAIL, WARN or SKIP. WARN sorts after FAIL, counts on its own and never
changes the result status (or the exit code).
"""

import csv
import hashlib
import json
import os
import re
import subprocess

from . import cutlist, deliver, delivercheck as dc, safezone

PASS, FAIL, SKIP = dc.PASS, dc.FAIL, dc.SKIP
WARN = "WARN"

# Every threshold the table uses; override one with --set KEY=VALUE.
THRESHOLDS = {
    "duration_tol_s": 0.1,          # render vs. expected duration
    "black_min_s": 0.5,             # blackdetect d=
    "black_pix_th": 0.10,           # blackdetect pix_th=
    "freeze_min_s": 2.0,            # freezedetect d=
    "freeze_noise_db": -60.0,       # freezedetect n=
    "scdet_threshold": 10.0,        # scdet t=; a frame scoring this or more is a cut
    "scdet_tolerance": 0,           # cuts allowed either side of the shot list
    "true_peak_max_dbtp": -1.0,     # when the destination has no maximum
    "astats_peak_max_db": -0.1,     # overall sample peak; above it is clipping
    "astats_dc_max": 0.01,          # |DC offset|, full scale = 1.0
    "silence_min_s": 0.5,           # silencedetect d=
    "silence_noise_db": -50.0,      # silencedetect n=
    # Sound rows (VP-121); every one a working default until loudness-specs.md exists.
    "silence_cut_db": -60.0,        # 10 ms RMS under this is silence at a cut
    "silence_cut_frames": 2.0,      # a silence over this many frames across a cut warns
    "speech_pad_s": 0.2,            # padding on each speech span
    "speech_join_s": 0.5,           # word gaps under this join into one span
    "speech_rest_margin_lu": 6.0,   # warn when rest > speech - this
    "phone_hp_hz": 300.0,           # phone proxy high-pass
    "phone_drop_lu": 8.0,           # warn when the proxy's short-term max drops more than this
    "stems_lu_tol": 0.1,            # stems sum vs mix, integrated
    "stems_residual_db": -50.0,     # residual (mix minus stems) RMS ceiling
    # Offline rows (VP-23a, VP-20); every one a working default.
    "black_lead_start_s": 0.1,      # black starting this close to the head is leading black
    "black_lead_max_s": 2.0,        # leading black shorter than this is a fade-in: WARN
    "social_min_fps": 30.0,         # Instagram's stated minimum (decision 14)
    "social_fps_tol": 0.05,         # so 29.97 passes
    "safe_frames": 8.0,             # frames sampled for the safe-zone row
    "safe_top": safezone.PORTRAIT["top"],        # unsafe margin, fraction of height, from the top
    "safe_bottom": safezone.PORTRAIT["bottom"],  # ... from the bottom
    "safe_side": safezone.PORTRAIT["side"],      # ... fraction of width, each side
    "safe_edge_grad": 60.0,         # gradient (0-255) that counts as an edge
    "safe_persist_frac": 0.004,     # persistent-edge share of a margin that warns
    "hook_still_s": 1.0,            # portrait: a still or freeze from 0 this long is a WARN
    "hook_sound_s": 0.5,            # portrait: silence from 0 past this is a WARN
    "tail_s": 0.7,                  # portrait: the last this-many seconds frozen or silent
    "hook_caption_s": 1.5,          # portrait: the first sidecar cue should start by here
}


def parse_overrides(pairs, base=None):
    """['black_min_s=1', ...] -> a copy of base (default THRESHOLDS) with those set.
    Raises ValueError on an unknown key or a non-number."""
    out = dict(base or THRESHOLDS)
    for p in pairs or []:
        key, eq, val = str(p).partition("=")
        if not eq or key not in out:  # base's keys, so captionlint shares this
            raise ValueError(f"--set wants KEY=VALUE with KEY one of: {', '.join(sorted(out))} "
                             f"(got {p!r})")
        try:
            out[key] = float(val)
        except ValueError:
            raise ValueError(f"--set {key}: {val!r} is not a number")
    return out


def shot_count(path):
    """Shots in a shot-list CSV: its non-empty rows after the header."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    except OSError as e:
        raise dc.CheckError(f"cannot read the shot list {path}: {e}")
    if len(rows) < 2:
        raise dc.CheckError(f"{path} has no shots (a header row, then one row per shot).")
    return len(rows) - 1


def _row(name, ok, found, expected, note=None):
    r = dc._row(name, ok, found, expected, note)
    r["source"] = "ffmpeg"
    return r


# ---------------------------------------------------------------------------
# Parsers (ffmpeg's log, on stderr)
# ---------------------------------------------------------------------------

def parse_black(text):
    """[(start, end, duration)] from blackdetect."""
    return [(float(a), float(b), float(c)) for a, b, c in re.findall(
        r"black_start:(\S+) black_end:(\S+) black_duration:(\S+)", text)]


def parse_freeze(text):
    """[(start, duration)] from freezedetect. A freeze that runs to the end
    of the file prints only its start (duration None)."""
    out, start = [], None
    for m in re.finditer(r"freeze_start: (\S+)|freeze_duration: (\S+)", text):
        if m.group(1) is not None:
            if start is not None:
                out.append((start, None))
            start = float(m.group(1))
        elif start is not None:
            out.append((start, float(m.group(2))))
            start = None
    if start is not None:
        out.append((start, None))
    return out


def parse_scdet(text, threshold):
    """[time] of every frame whose scdet score is at or over threshold."""
    return [float(t) for s, t in re.findall(
        r"lavfi\.scd\.score: (\S+), lavfi\.scd\.time: (\S+)", text) if float(s) >= threshold]


def parse_silence(text):
    """[(start, duration)] from silencedetect; a gap that runs to the end
    has a start and no end line, so its duration is None."""
    gaps, start = [], None
    for m in re.finditer(r"silence_start: (\S+)|silence_end: \S+ \| silence_duration: (\S+)", text):
        if m.group(1) is not None:
            start = float(m.group(1))
        elif start is not None:
            gaps.append((start, float(m.group(2))))
            start = None
    if start is not None:
        gaps.append((start, None))
    return gaps


def parse_astats(text):
    """{peak_db, dc} from astats' Overall block; None for what it did not print."""
    i = text.rfind("] Overall")
    body = text[i:] if i >= 0 else ""

    def grab(label):
        m = re.search(label + r": (-?(?:inf|\d+(?:\.\d+)?(?:e-?\d+)?))", body)
        return float(m.group(1)) if m else None
    return {"peak_db": grab("Peak level dB"), "dc": grab("DC offset")}


# ---------------------------------------------------------------------------
# Sound rows (VP-121)
# ---------------------------------------------------------------------------

START_COLUMNS = ("start_s", "start", "in_s", "in", "time_s", "time", "cut_s")
ENV_WIN_S = 0.01      # the silence_at_cut envelope: 10 ms RMS, finer than audio-report's 100 ms
ENV_HALF_S = 0.5      # audio read either side of a cut
WD = " (working default)"
STEMS_PAD_SAMPLES = 2048   # a stem or mix this much longer than the others is codec padding


def _ar():
    """audioreport, which imports numpy: only the sound rows load it, so the
    rest of the table runs where numpy is missing."""
    from . import audioreport
    return audioreport


def _srow(name, status, found, expected, note=None):
    return {"check": name, "status": status, "found": found, "expected": expected,
            "source": "ffmpeg", **({"note": note} if note else {})}


def _np():
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def shot_cuts(path):
    """Cut times from the start column of a shot-list CSV (the first shot's
    start is not a cut), or None when it has no such column. Raises
    CheckError on a start that is not a number."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    except OSError as e:
        raise dc.CheckError(f"cannot read the shot list {path}: {e}")
    head = [c.strip().lower() for c in rows[0]] if rows else []
    col = next((head.index(n) for n in START_COLUMNS if n in head), None)
    if col is None:
        return None
    try:
        starts = [float(r[col]) for r in rows[1:]]
    except (ValueError, IndexError):
        raise dc.CheckError(f"{path}: the {head[col]} column holds a value that is not seconds.")
    return sorted(starts)[1:]


def speech_spans(words, pad, join):
    """[(start, end)] of speech: words joined across gaps under `join`, then
    each span padded by `pad` (spans that then touch merge)."""
    spans = []
    for w in sorted(words, key=lambda w: (w["start"], w["end"])):
        if spans and w["start"] - spans[-1][1] < join:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    out = []
    for s, e in spans:
        s, e = max(0.0, s - pad), e + pad
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def _ebur(path, chain=""):
    """(summary, [(t, M, S)]) of ebur128 after an optional filter chain."""
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-v", "verbose",
                    "-i", dc._src(path), "-map", "0:a:0", "-af",
                    chain + "ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
                   dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not measure {path}: {proc.stderr.strip()[-300:]}")
    return dc.parse_ebur128(proc.stderr), _ar().parse_framelog(proc.stderr)


def _max_short_term(frames):
    finite = [s for _, _, s in frames if s > _ar().NO_READING_LUFS]
    return max(finite) if finite else None


def _pcm(np, path, rate, ch, start=None, dur=None):
    """The first audio stream as float32 (n, ch) at rate, folded to ch channels."""
    cmd = [dc.FFMPEG, "-v", "error", "-nostdin"]
    cmd += ["-ss", f"{start:.3f}"] if start else []
    cmd += ["-t", f"{dur:.3f}"] if dur is not None else []
    cmd += ["-i", dc._src(path), "-map", "0:a:0", "-f", "f32le", "-ar", str(rate),
            "-ac", str(ch), "-"]
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not decode {path}: "
                            f"{proc.stderr.decode(errors='replace').strip()[-300:]}")
    return np.frombuffer(proc.stdout, "<f4").reshape(-1, ch)


def _rate_ch(path):
    try:
        i = _ar().audio_stream(path)
    except _ar().ReportError as e:
        raise dc.CheckError(str(e))
    return i["sample_rate"], i["channels"]


def _sound_lra(m):
    lra = m["lra_lu"]
    return _srow("lra", SKIP, f"{lra} LU", "reported, not checked", "loudness range from ebur128")


def _sound_true_peak_per_cut(path, cuts, dur, ceiling, note):
    if cuts is None:
        return _srow("true_peak_per_cut", SKIP, "no cut times", "cuts from --shots or scdet")
    # Cut times are seconds from the start of this file; any at or past its end are dropped.
    cuts = [c for c in sorted(cuts) if c > 0 and (not dur or c < dur)]
    edges = [0.0] + cuts + [dur or None]  # an unreadable duration leaves the last shot open-ended
    over, worst, unread, measured = [], None, [], 0
    for n, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        if b is not None and b - a < 0.01:
            continue
        try:
            tp = _ar().loudness(path, 0, a, b)[0]["true_peak_dbtp"]
        except _ar().ReportError:
            tp = None
        if tp is None:  # a read that returned no peak is as unmeasured as a failed one
            unread.append(n)
            continue
        measured += 1
        if worst is None or tp > worst:
            worst = tp
        if tp > ceiling + 1e-9:
            over.append(f"shot {n} ({_at(a)} to {_at(b) if b is not None else 'the end'}): {tp} dBTP")
    shown = "; ".join(over[:3]) + (f" and {len(over) - 3} more" if len(over) > 3 else "")
    gone = f"shot {', '.join(map(str, unread))} unreadable" if unread else ""
    expected = f"each shot <= {ceiling:g} dBTP"
    if over:
        return _srow("true_peak_per_cut", FAIL, shown + (f"; {gone}" if gone else ""), expected, note)
    if unread:  # nothing measured is a FAIL like an unreadable duration; some measured is a WARN
        found = gone if not measured else f"{measured} shots, worst {worst} dBTP; {gone}"
        return _srow("true_peak_per_cut", WARN if measured else FAIL, found, expected, note)
    return _srow("true_peak_per_cut", PASS, f"{measured} shots, worst {worst} dBTP", expected, note)


def _sound_silence_at_cut(np, path, cuts, th, fps):
    if cuts is None:
        return _srow("silence_at_cut", SKIP, "no cut times", "cuts from --shots or scdet")
    rate, ch = _rate_ch(path)
    limit = th["silence_cut_frames"] / fps
    win = max(1, int(round(rate * ENV_WIN_S)))
    hits = []
    for n, t in enumerate(sorted(cuts), 1):
        t0 = max(0.0, t - ENV_HALF_S)
        x = _pcm(np, path, rate, ch, t0, 2 * ENV_HALF_S)
        k = len(x) // win
        if not k:
            continue
        w = x[:k * win].reshape(k, win, ch).astype(np.float64)
        rms = np.sqrt((w ** 2).mean(axis=1)).max(axis=1)
        quiet = 20 * np.log10(np.maximum(rms, 1e-12)) < th["silence_cut_db"]
        i = int((t - t0) / ENV_WIN_S)
        if not (0 <= i < k and quiet[i]):
            continue
        lo = hi = i
        while lo > 0 and quiet[lo - 1]:
            lo -= 1
        while hi < k - 1 and quiet[hi + 1]:
            hi += 1
        length = (hi - lo + 1) * ENV_WIN_S
        if length > limit + 1e-9:
            hits.append(f"cut {n} at {_at(t)}: {length * 1000:.0f} ms under {th['silence_cut_db']:g} dBFS")
    shown = "; ".join(hits[:3]) + (f" and {len(hits) - 3} more" if len(hits) > 3 else "")
    return _srow("silence_at_cut", WARN if hits else PASS, shown if hits else "none",
                 f"no silence over {th['silence_cut_frames']:g} frames across a cut{WD}",
                 f"{ENV_WIN_S * 1000:g} ms RMS envelope, loudest channel, {fps:g} fps")


def load_words(path):
    """Word dicts from an mlx_whisper word-level JSON; raises CheckError."""
    try:
        return cutlist.load_words(path)
    except (OSError, json.JSONDecodeError) as e:
        raise dc.CheckError(f"cannot read the words file {path}: {e}")


def _masked_lufs(path, spans, inside):
    """Integrated LUFS of the stream with the spans kept (inside) or muted
    (not inside). The mute is digital silence, which ebur128's gate drops.
    shortcut: the spans ride in one filter argument, fine for hours of speech,
    not for tens of thousands of spans."""
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
    mute = f"not({expr})" if inside else expr
    summary, _ = _ebur(path, f"volume=0:enable='{mute}',")
    return summary["integrated_lufs"]


def _sound_speech_vs_rest(path, words, th):
    if words is None:
        return _srow("speech_vs_rest", SKIP, "no word file", "a word JSON (--words)")
    spans = speech_spans(words, th["speech_pad_s"], th["speech_join_s"])
    speech = _masked_lufs(path, spans, True)
    rest = _masked_lufs(path, spans, False)
    margin = th["speech_rest_margin_lu"]
    expected = f"rest <= speech - {margin:g} LU{WD}"
    found = f"speech {speech} LUFS, rest {rest} LUFS ({len(spans)} spans)"
    if speech is None or speech <= -70.0:
        return _srow("speech_vs_rest", SKIP, found, expected, "no speech loudness to compare")
    louder = rest is not None and rest > -70.0 and rest > speech - margin + 1e-9
    return _srow("speech_vs_rest", WARN if louder else PASS, found, expected,
                 f"words joined across gaps under {th['speech_join_s']:g}s, padded {th['speech_pad_s']:g}s")


def _sound_phone_proxy(path, full_frames, th):
    full = _max_short_term(full_frames)
    expected = f"drop <= {th['phone_drop_lu']:g} LU{WD}"
    note = f"mono, high-pass {th['phone_hp_hz']:g} Hz, max short-term"
    if full is None:
        return _srow("phone_proxy", SKIP, "shorter than 3 s", expected, note)
    _, frames = _ebur(path, f"aformat=channel_layouts=mono,highpass=f={th['phone_hp_hz']:g},")
    phone = _max_short_term(frames)
    phone_v = phone if phone is not None else _ar().NO_READING_LUFS
    drop = round(full - phone_v, 1)
    return _srow("phone_proxy", WARN if drop > th["phone_drop_lu"] + 1e-9 else PASS,
                 f"{drop:g} LU drop (mix {full} LUFS, phone {'none' if phone is None else phone})",
                 expected, note)


def _sound_stems_sum(np, path, stems, mix_lufs, th):
    if not stems:
        return _srow("stems_sum", SKIP, "no stems", "stems (--stems a.wav b.wav ...)")
    rate, ch = _rate_ch(path)
    # shortcut: the mix and every stem are held in memory (about 1.4 GB per hour of
    # stereo 48 kHz, doubled by the sum); chunk the read before checking a feature film.
    mix = _pcm(np, path, rate, ch)
    parts = [_pcm(np, s, rate, ch) for s in stems]
    n = min(len(mix), *(len(p) for p in parts))
    total = np.zeros((n, ch), dtype=np.float64)
    for p in parts:
        total += p[:n]
    cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-f", "f32le", "-ar", str(rate),
           "-ac", str(ch), "-i", "-", "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    proc = subprocess.run(cmd, input=total.astype("<f4").tobytes(), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError("ffmpeg could not measure the stems sum: "
                            + proc.stderr.decode(errors="replace").strip()[-300:])
    summed = dc.parse_ebur128(proc.stderr.decode(errors="replace"))["integrated_lufs"]
    resid = float(np.sqrt(((mix[:n].astype(np.float64) - total) ** 2).mean())) if n else 0.0
    resid_db = round(20 * np.log10(resid), 1) if resid > 0 else -120.0
    diff = None if summed is None or mix_lufs is None else round(abs(summed - mix_lufs), 2)
    ok = diff is not None and diff <= th["stems_lu_tol"] + 1e-9 and resid_db <= th["stems_residual_db"]
    short = max(len(mix), *(len(p) for p in parts)) - n
    ok = ok and short <= STEMS_PAD_SAMPLES  # more than codec padding missing: not the same length
    return _srow("stems_sum", PASS if ok else FAIL,
                 f"sum {summed} LUFS vs mix {mix_lufs} LUFS (diff {diff} LU), residual {resid_db} dBFS",
                 f"diff <= {th['stems_lu_tol']:g} LU and residual <= {th['stems_residual_db']:g} dBFS{WD}",
                 f"{len(stems)} stems, residual is RMS" + (f", lengths differ by {short} samples" if short else ""))


def _sound_rows(path, m, th, cuts, cuts_from, words, stems, ceiling, fps, dur):
    """The VP-121 rows for a file with audio. m: measure_loudness's dict."""
    np = _np()
    rows = [_sound_lra(m)]
    if np is None:  # audioreport, which these rows read, imports numpy
        return rows + [_srow(n, SKIP, "needs numpy", "numpy installed") for n in
                       ("true_peak_per_cut", "silence_at_cut", "speech_vs_rest", "phone_proxy",
                        "stems_sum")]
    rows.append(_sound_true_peak_per_cut(path, cuts, dur, ceiling, cuts_from))
    rows.append(_sound_silence_at_cut(np, path, cuts, th, fps))
    rows.append(_sound_speech_vs_rest(path, words, th))
    rows.append(_sound_phone_proxy(path, _ebur(path)[1], th))
    rows.append(_sound_stems_sum(np, path, stems, m["integrated_lufs"], th))
    return rows


def _at(seconds):
    return f"{seconds:.2f}s"


def _is_social(dest):
    return bool(dest) and (dest.get("aspect") == "9x16" or str(dest.get("key", "")).startswith("social"))


def _social_fps(streams, dest, th):
    expected = f">= {th['social_min_fps']:g} fps{WD}"
    if not _is_social(dest):
        return _srow("social_fps", SKIP, "not a social destination", expected, "give --dest social_9x16")
    r = next((dc.rate(s.get("avg_frame_rate")) for s in streams if s.get("codec_type") == "video"
              and dc.rate(s.get("avg_frame_rate"))), None)
    if r is None:
        return _srow("social_fps", SKIP, "no video frame rate", expected)
    fps = float(r)
    bad = fps < th["social_min_fps"] - th["social_fps_tol"]
    return _srow("social_fps", WARN if bad else PASS, f"{dc.fmt_rate(r)} fps", expected,
                 "Instagram states a 30 fps minimum; capture stays 23.976, conform only if a post is rejected")


def _sha256_row(path, want):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest()
    if want is None:
        return _srow("sha256", SKIP, got, "reported (--sha256 HEX to assert)")
    ok = got == want.strip().lower()
    return _srow("sha256", PASS if ok else FAIL, got, want.strip().lower())


SAFE_W, SAFE_H = 270, 480


def _safe_zone(np, path, dest, th, dur, streams):
    expected = f"persistent edges <= {th['safe_persist_frac']:g} of each margin{WD}"
    if not _is_social(dest):
        return _srow("safe_zone", SKIP, "not a social destination", expected, "give --dest social_9x16")
    v = next((x for x in streams if x.get("codec_type") == "video"), None)
    if v is None or not dur:
        return _srow("safe_zone", SKIP, "no video", expected)
    if not safezone.tall(v.get("width") or 0, v.get("height") or 0):  # the margins are 9:16 numbers
        return _srow("safe_zone", SKIP, f"{v.get('width')}x{v.get('height')} is not 9:16 portrait", expected)
    if np is None:
        return _srow("safe_zone", SKIP, "needs numpy", expected)
    # shortcut: fps= decodes the whole file to sample frames; seek per sample for long renders.
    n = int(th["safe_frames"])
    proc = subprocess.run(
        [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", dc._src(path), "-map", "0:v:0",
         "-vf", f"fps={n}/{dur},scale={SAFE_W}:{SAFE_H}:flags=area,format=gray", "-frames:v", str(n),
         "-f", "rawvideo", "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError("ffmpeg could not sample frames: "
                            + proc.stderr.decode(errors="replace").strip()[-300:])
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    frames = raw[:len(raw) // (SAFE_W * SAFE_H) * SAFE_W * SAFE_H].reshape(-1, SAFE_H, SAFE_W)
    frames = [f.astype(np.int16) for f in frames if int(f.max()) - int(f.min()) >= 20]  # drop flat frames
    if len(frames) < 3:
        return _srow("safe_zone", SKIP, f"{len(frames)} usable frames", expected, "needs 3 non-flat frames")
    persist = None
    for f in frames:
        g = np.abs(np.diff(f, axis=1))[:-1, :] + np.abs(np.diff(f, axis=0))[:, :-1]
        e = g > th["safe_edge_grad"]
        persist = e if persist is None else persist & e
    top, bot = int(SAFE_H * th["safe_top"]), int(SAFE_H * (1 - th["safe_bottom"]))
    side = int(SAFE_W * th["safe_side"])
    w = persist.shape[1]
    bands = {"top": persist[:top], "bottom": persist[bot:], "left": persist[:, :side],
             "right": persist[:, w - side:]}
    frac = {k: (float(v.mean()) if v.size else 0.0) for k, v in bands.items()}
    over = [k for k, v in frac.items() if v > th["safe_persist_frac"]]
    return _srow("safe_zone", WARN if over else PASS,
                 ", ".join(f"{k} {v:.4f}" for k, v in frac.items()), expected,
                 f"{len(frames)} frames; margins top {th['safe_top']:g}, bottom {th['safe_bottom']:g}, "
                 f"sides {th['safe_side']:g}; working defaults wider than the pages' 150/400 or 90/330 px, VP-114 measures on a device" + (f"; over in {', '.join(over)}" if over else ""))


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def _detect_pass(path, th, has_video, has_audio, portrait=False):
    """The ffmpeg log of one decode with every detector filter attached. A
    portrait render detects freezes and silences down to the hook thresholds;
    the plain rows filter back up to their own."""
    fd, sd = th["freeze_min_s"], th["silence_min_s"]
    if portrait:
        fd = min(fd, th["hook_still_s"], th["tail_s"])
        sd = min(sd, th["hook_sound_s"], th["tail_s"])
    cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(path)]
    if has_video:
        cmd += ["-map", "0:v:0", "-vf",
                f"blackdetect=d={th['black_min_s']}:pix_th={th['black_pix_th']},"
                f"freezedetect=n={th['freeze_noise_db']}dB:d={fd},"
                f"scdet=t={th['scdet_threshold']}"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af",
                f"silencedetect=n={th['silence_noise_db']}dB:d={sd},"
                "astats=measure_perchannel=none"]
    proc = dc._run(cmd + ["-f", "null", "-"], dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not scan {path}: {proc.stderr.strip()[-300:]}")
    return proc.stderr


def _hook_row(name, ok, found, expected, note=None):
    r = _row(name, ok, found, expected, note)
    if r["status"] == FAIL:
        r["status"] = WARN
    return r


def _long(segs, min_s):
    """Segments (start, duration) of min_s or longer; None duration runs to the end."""
    return [(a, d) for a, d in segs if d is None or d >= min_s - 1e-9]


def _hook_rows(path, th, dur, black, frozen, gaps, has_video, has_audio):
    """The portrait hook rows from the detect pass's raw segments."""
    eps = 0.1  # a frame or two at reel frame rates
    rows = []
    if has_video:
        still = float(th["hook_still_s"])
        opens = [f"black from {_at(a)} for {_at(d)}" for a, _, d in black if a <= eps]
        opens += [f"still from {_gap(a, d)}" for a, d in frozen
                  if a <= eps and (d is None or d >= still - 1e-9)]
        rows.append(_hook_row("hook_open", not opens, "; ".join(opens) or "moving picture from 0",
                              f"no black or still of {still:g}s or more at 0"))
    if has_audio:
        snd = float(th["hook_sound_s"])
        late = [_gap(a, d) for a, d in gaps if a <= eps and (d is None or d > snd)]
        rows.append(_hook_row("hook_sound", not late, "; ".join(late) or "sound from 0",
                              f"sound within {snd:g}s of 0"))
    if dur:
        tail = float(th["tail_s"])
        dead = []
        for kind, segs in (("freeze", frozen), ("silence", gaps)):
            for a, d in segs:
                if a <= dur - tail + eps and (d is None or a + d >= dur - eps):
                    dead.append(f"{kind} from {_at(a)} to the end")
        rows.append(_hook_row("dead_tail", not dead, "; ".join(dead) or "live to the end",
                              f"last {tail:g}s not frozen or silent"))
    srt = os.path.splitext(path)[0] + ".srt"
    cap = float(th["hook_caption_s"])
    if not os.path.isfile(srt):
        rows.append(_hook_row("hook_caption", None, "no sidecar .srt", f"first cue by {cap:g}s",
                              "burnt-in captions cannot be read without OCR"))
    else:
        try:
            with open(srt, encoding="utf-8-sig") as f:
                cues, _ = dc.parse_srt(f.read())
        except (OSError, UnicodeDecodeError):
            cues = []
        first = cues[0][0] if cues else None
        rows.append(_hook_row("hook_caption", first is not None and first <= cap + 1e-9,
                              "no readable cue" if first is None else f"first cue at {_at(first)}",
                              f"first cue by {cap:g}s"))
    return rows


def _gap(start, duration):
    return f"{_at(start)} for {_at(duration)}" if duration is not None else f"{_at(start)} to the end"


def _segments(name, found, th_s, what):
    """PASS when no segment was found; FAIL naming up to three."""
    shown = ", ".join(found[:3]) + (f" and {len(found) - 3} more" if len(found) > 3 else "")
    return _row(name, not found, shown if found else f"no {what}",
                f"no {what} of {th_s:g}s or longer")


def run(path, dest=None, config_folders=None, fps=None, size=None, expected_duration=None,
        shots=None, thresholds=None, shot_cut_times=None, words=None, stems=None,
        sha256=None):
    """The QC table for path. dest: a merged destination (deliver.destination)
    or None to skip the deliver_check rows. shots: the expected shot count.
    shot_cut_times: cut times from the shot list (else the sound rows use
    the scdet cuts); words: load_words()'s list; stems: paths of stem files;
    sha256: the hash the file must have.
    Returns {file, destination, checks, counts, status, thresholds}, FAIL
    rows first; raises dc.ToolMissing or dc.CheckError."""
    th = thresholds or THRESHOLDS
    dc.check_tools(ffmpeg=True)
    if not os.path.isfile(path):
        raise dc.CheckError(f"{path} is not a file.")
    info = dc.probe(path)
    streams = info.get("streams") or []
    has_video = any(s.get("codec_type") == "video"
                    and not (s.get("disposition") or {}).get("attached_pic") for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    vid = next((s for s in streams if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")), {})
    portrait = has_video and (vid.get("height") or 0) > (vid.get("width") or 0)
    rows = []
    if not (has_video or has_audio):
        rows.append(_row("streams", False, "no video or audio stream", "at least one"))

    if dest is not None:
        # Loudness is read once, below, from the same target.
        for r in dc.check(path, dest, fps=fps, size=size, loudness=False,
                          folders=config_folders)["checks"]:
            if r["check"] not in ("loudness", "true_peak", "safe_zone"):  # qc has its own safe_zone
                rows.append({**r, "source": "deliver_check"})
    else:
        rows.append(dict(dc._row("deliver_check", None, "not run", "a destination (--dest)"),
                         source="deliver_check"))

    dur = dc.duration(info)
    if expected_duration is not None:
        tol = float(th["duration_tol_s"])
        rows.append(_row("duration", dur is not None and abs(dur - expected_duration) <= tol + 1e-9,
                         "unreadable" if dur is None else f"{dur:.3f}s",
                         f"{expected_duration:g}s +/- {tol:g}s"))

    log = _detect_pass(path, th, has_video, has_audio, portrait) if (has_video or has_audio) else ""
    scdet_cuts = None

    black, frozen, gaps = [], [], []
    if has_video:
        black = parse_black(log)
        lead = [b for b in black if b[0] <= th["black_lead_start_s"] and b[2] < th["black_lead_max_s"]]
        rest = [b for b in black if b not in lead]
        r = _segments("blackdetect", [f"{_at(a)} for {_at(d)}" for a, _, d in rest],
                      th["black_min_s"], "black")
        if lead and not rest:  # decision 30: a fade-in from black is a WARN, not a fault
            r.update(status=WARN, expected=f"no black of {th['black_min_s']:g}s or longer after the head", found=f"{_at(lead[0][0])} for {_at(lead[0][2])} at the head",
                     note=f"leading black under {th['black_lead_max_s']:g}s (fade-in){WD}")
        rows.append(r)
        frozen = parse_freeze(log)
        rows.append(_segments("freezedetect", [_gap(a, d) for a, d in _long(frozen, th["freeze_min_s"])],
                              th["freeze_min_s"], "freeze"))
        cuts = parse_scdet(log, th["scdet_threshold"])
        scdet_cuts = cuts
        if shots is None:
            rows.append(_row("scdet", None, f"{len(cuts)} cuts", "a shot list (--shots)",
                             "no shot list to compare"))
        else:
            tol = int(th["scdet_tolerance"])
            rows.append(_row("scdet", abs(len(cuts) - (shots - 1)) <= tol,
                             f"{len(cuts)} cuts", f"{shots - 1} cuts ({shots} shots)"
                             + (f" +/- {tol}" if tol else ""),
                             "cuts at " + ", ".join(_at(c) for c in cuts[:8]) if cuts else None))
    else:
        for n in ("blackdetect", "freezedetect", "scdet"):
            rows.append(_row(n, None, "no video stream", "-"))

    if has_audio:
        loud = (dest or {}).get("loudness") or {}
        m = dc.measure_loudness(path)
        i, tp = m["integrated_lufs"], m["true_peak_dbtp"]
        if dest is not None and deliver.has_loudness_target(dest):
            target, tol = float(loud["integrated_lufs"]), float(loud["tolerance_lu"])
            rows.append(_row("loudness", i is not None and abs(i - target) <= tol + 1e-9,
                             f"{i} LUFS", f"{target:g} LUFS +/- {tol:g} LU"))
            tp_max = float(loud["true_peak_max_dbtp"])
        else:
            rows.append(_row("loudness", None, f"{i} LUFS", "no target (--dest)"))
            tp_max = float(th["true_peak_max_dbtp"])
        rows.append(_row("true_peak", tp is not None and tp <= tp_max + 1e-9,
                         f"{tp} dBTP", f"<= {tp_max:g} dBTP"))

        a = parse_astats(log)
        pk, dcoff = a["peak_db"], a["dc"]
        rows.append(_row("astats_peak", pk is not None and pk <= th["astats_peak_max_db"],
                         f"{pk} dBFS", f"<= {th['astats_peak_max_db']:g} dBFS"))
        rows.append(_row("astats_dc", dcoff is not None and abs(dcoff) <= th["astats_dc_max"],
                         f"{dcoff}", f"|DC| <= {th['astats_dc_max']:g}"))
        gaps = parse_silence(log)
        rows.append(_segments(
            "silencedetect",
            [_gap(s, d) for s, d in _long(gaps, th["silence_min_s"])],
            th["silence_min_s"], "silence"))
        sound_cuts, cuts_from = shot_cut_times, "cuts from the shot list"
        if sound_cuts is None:
            sound_cuts, cuts_from = scdet_cuts, "cuts from scdet"
        fr = next((dc.rate(s.get("avg_frame_rate")) for s in streams
                   if s.get("codec_type") == "video" and dc.rate(s.get("avg_frame_rate"))), None)
        rows += _sound_rows(path, m, th, sound_cuts, cuts_from, words, stems, tp_max,
                            float(fr) if fr else 24.0, dur or 0.0)
    else:
        for n in ("loudness", "true_peak", "astats_peak", "astats_dc", "silencedetect", "lra",
                  "true_peak_per_cut", "silence_at_cut", "speech_vs_rest", "phone_proxy",
                  "stems_sum"):
            rows.append(_row(n, None, "no audio stream", "-"))

    rows.append(_social_fps(streams, dest, th))
    rows.append(_sha256_row(path, sha256))
    rows.append(_safe_zone(_np(), path, dest, th, dur, streams))
    if portrait:
        rows += _hook_rows(path, th, dur, black, frozen, gaps, has_video, has_audio)

    order = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}
    rows.sort(key=lambda r: order[r["status"]])  # stable: each source keeps its own order
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (PASS, FAIL, WARN, SKIP)}
    return {"file": os.path.abspath(path), "destination": dest["key"] if dest else None,
            "checks": rows, "counts": counts, "status": "fail" if counts[FAIL] else "pass",
            "thresholds": th}


def format_report(result):
    """The table as aligned text lines, FAIL rows first."""
    lines = [f"{result['file']}  ->  {result['destination'] or '(no destination)'}"]
    for r in result["checks"]:
        lines.append(f"  {r['status']:<4}  {r['check']:<14} found {r['found']}  "
                     f"(expected {r['expected']})" + (f"  [{r['note']}]" if r.get("note") else ""))
    c = result["counts"]
    warn = f", {c[WARN]} warn" if c.get(WARN) else ""
    lines.append(f"qc: {c[PASS]} pass, {c[FAIL]} fail{warn}, {c[SKIP]} skipped: {result['status'].upper()}")
    return "\n".join(lines) + "\n"
