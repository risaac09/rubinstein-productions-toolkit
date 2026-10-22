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

A row is {check, status, found, expected, source[, note]}; status is PASS,
FAIL or SKIP, as in delivercheck.
"""

import csv
import os
import re

from . import deliver, delivercheck as dc

PASS, FAIL, SKIP = dc.PASS, dc.FAIL, dc.SKIP

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


def _at(seconds):
    return f"{seconds:.2f}s"


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def _detect_pass(path, th, has_video, has_audio):
    """The ffmpeg log of one decode with every detector filter attached."""
    cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(path)]
    if has_video:
        cmd += ["-map", "0:v:0", "-vf",
                f"blackdetect=d={th['black_min_s']}:pix_th={th['black_pix_th']},"
                f"freezedetect=n={th['freeze_noise_db']}dB:d={th['freeze_min_s']},"
                f"scdet=t={th['scdet_threshold']}"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af",
                f"silencedetect=n={th['silence_noise_db']}dB:d={th['silence_min_s']},"
                "astats=measure_perchannel=none"]
    proc = dc._run(cmd + ["-f", "null", "-"], dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not scan {path}: {proc.stderr.strip()[-300:]}")
    return proc.stderr


def _gap(start, duration):
    return f"{_at(start)} for {_at(duration)}" if duration is not None else f"{_at(start)} to the end"


def _segments(name, found, th_s, what):
    """PASS when no segment was found; FAIL naming up to three."""
    shown = ", ".join(found[:3]) + (f" and {len(found) - 3} more" if len(found) > 3 else "")
    return _row(name, not found, shown if found else f"no {what}",
                f"no {what} of {th_s:g}s or longer")


def run(path, dest=None, config_folders=None, fps=None, size=None, expected_duration=None,
        shots=None, thresholds=None):
    """The QC table for path. dest: a merged destination (deliver.destination)
    or None to skip the deliver_check rows. shots: the expected shot count.
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
    rows = []
    if not (has_video or has_audio):
        rows.append(_row("streams", False, "no video or audio stream", "at least one"))

    if dest is not None:
        # Loudness is read once, below, from the same target.
        for r in dc.check(path, dest, fps=fps, size=size, loudness=False,
                          folders=config_folders)["checks"]:
            if r["check"] not in ("loudness", "true_peak"):
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

    log = _detect_pass(path, th, has_video, has_audio) if (has_video or has_audio) else ""

    if has_video:
        black = parse_black(log)
        rows.append(_segments("blackdetect", [f"{_at(a)} for {_at(d)}" for a, _, d in black],
                              th["black_min_s"], "black"))
        frozen = parse_freeze(log)
        rows.append(_segments("freezedetect", [_gap(a, d) for a, d in frozen],
                              th["freeze_min_s"], "freeze"))
        cuts = parse_scdet(log, th["scdet_threshold"])
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
            [_gap(s, d) for s, d in gaps],
            th["silence_min_s"], "silence"))
    else:
        for n in ("loudness", "true_peak", "astats_peak", "astats_dc", "silencedetect"):
            rows.append(_row(n, None, "no audio stream", "-"))

    order = {FAIL: 0, PASS: 1, SKIP: 2}
    rows.sort(key=lambda r: order[r["status"]])  # stable: each source keeps its own order
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (PASS, FAIL, SKIP)}
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
    lines.append(f"qc: {c[PASS]} pass, {c[FAIL]} fail, {c[SKIP]} skipped: {result['status'].upper()}")
    return "\n".join(lines) + "\n"
