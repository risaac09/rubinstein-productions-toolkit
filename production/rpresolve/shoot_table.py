"""
rpresolve.shoot_table: one CSV row per source clip for the pre-ingest
look at a shoot (capture-standard.md, canonical stage 2). Offline and
read-only; nothing here imports Resolve.

Camera and profile come from detect.classify, never re-derived here. The
rest is ffprobe (duration, fps, size, sample aspect, audio stream),
exiftool (start time, lens, squeeze) and one ffmpeg volumedetect pass over
the audio of a non-BRAW clip. A BRAW file is never handed to ffmpeg: its
facts come from exiftool alone, and its audio is reported as not measured.

Columns are COLUMNS. `camera` and `profile` are detect's names; `size` is
WxH; `flags` is a semicolon list of: long-take, vfr, no-audio, weak-audio,
unexpected (detect did not pin the row: review, corrupt, derived, proxy or
unknown camera). `local_start` is the camera's wall-clock digits with any
timezone suffix dropped, plus the --offset for that camera.
"""

import csv
import datetime
import io
import os
import re
import subprocess

from . import detect

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")

COLUMNS = (
    "file", "camera", "local_start", "duration_s", "fps", "vfr", "size",
    "profile", "squeeze", "lens", "audio_source", "flags", "note",
)

LONG_TAKE_MIN = 10.0
WEAK_AUDIO_DB = -50.0  # mean volume below this reads as weak or silent

_START_TAGS = ("CreationDate", "DateTimeOriginal", "CreateDate", "MediaCreateDate",
               "TrackCreateDate")
_LENS_TAGS = ("LensModel", "LensID", "LensType", "Lens")
_SQUEEZE_TAGS = ("AnamorphicRatio", "SqueezeFactor", "Squeeze", "Anamorphic")


def _exif(exif, *names):
    """First non-empty exiftool value whose bare tag name is in names
    (names are tried in order; -G1 keys look like 'QuickTime:CreateDate')."""
    for name in names:
        for key, val in exif.items():
            if key.rsplit(":", 1)[-1] == name and str(val).strip():
                return val
    return ""


def parse_start(text):
    """Naive datetime from an exiftool or ffprobe time string, or None."""
    m = re.match(r"(\d{4})[-:](\d\d)[-:](\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text))
    if not m or m.group(1) == "0000":
        return None
    try:
        return datetime.datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def _start(h):
    for name in _START_TAGS:
        t = parse_start(_exif(h.exif, name))
        if t:
            return t
    return parse_start(h.format_tags.get("creation_time", ""))


def _duration(h, is_braw):
    d = h.format.get("duration")
    if d is None and is_braw:
        d = _exif(h.exif, "Duration")
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*s?\s*$", str(d)) if d is not None else None
    return float(m.group(1)) if m else None


def _fps(h, is_braw):
    f = detect._fraction(h.video.get("avg_frame_rate")) if h.video else None
    if f is None:
        v = _exif(h.exif, "VideoFrameRate", "FrameRate") if is_braw else ""
        f = detect._fraction(v) if v else None
    return float(f) if f else None


def _size(h, is_braw):
    w, ht = h.video.get("width"), h.video.get("height")
    if not (w and ht) and is_braw:
        w, ht = _exif(h.exif, "ImageWidth"), _exif(h.exif, "ImageHeight")
    return f"{w}x{ht}" if w and ht else ""


def _squeeze(h):
    sar = detect._fraction(str(h.video.get("sample_aspect_ratio", "")).replace(":", "/"))
    if sar and sar != 1:
        return f"{float(sar):.2f}"
    return str(_exif(h.exif, *_SQUEEZE_TAGS))


def _audio(h):
    for s in h.streams:
        if s.get("codec_type") == "audio":
            return s
    return None


def mean_volume(path, ffmpeg=None):
    """Mean volume in dB from ffmpeg volumedetect over the audio only, or
    None when it cannot be read. Never called for a BRAW file."""
    cmd = [ffmpeg or FFMPEG, "-nostdin", "-v", "info", "-i", "file:" + path, "-vn",
           "-af", "volumedetect", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                              timeout=1800)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"mean_volume:\s*(-?inf|-?[\d.]+) dB", proc.stderr or "")
    if not m:
        return None
    return float("-inf") if "inf" in m.group(1) else float(m.group(1))


def build(paths, long_min=LONG_TAKE_MIN, weak_db=WEAK_AUDIO_DB, offsets=None,
          ffprobe=None, exiftool=None, ffmpeg=None):
    """Return (rows, missing). offsets maps a camera name (any case) to
    seconds added to local_start."""
    ffprobe = ffprobe or detect.FFPROBE
    exiftool = exiftool or detect.EXIFTOOL
    detect.check_tools(ffprobe, exiftool)
    offs = {k.lower(): v for k, v in (offsets or {}).items()}
    files, missing = detect.collect_files(paths)
    exif = detect.run_exiftool(files, exiftool=exiftool) if files else {}
    cache, rows = {}, []
    for f in files:
        is_braw = f.lower().endswith(".braw")
        try:
            if is_braw:  # metadata only: no ffprobe, no ffmpeg
                doc = {"path": f, "ffprobe": None, "ffprobe_error": "",
                       "exiftool": exif.get(f) or {}, "atoms": detect.read_atoms(f),
                       "proxy_original": detect.find_proxy_original(f, cache)}
            else:
                doc = detect.probe(f, exif.get(f), ffprobe=ffprobe, dir_cache=cache)
            d = detect.classify(doc)
            rows.append(_row(f, detect.Headers(doc), d, is_braw, long_min, weak_db, offs, ffmpeg))
        except detect.ToolMissing:
            raise
        except Exception as e:
            rows.append(_failed(f, e))
    return rows, missing


def _row(path, h, d, is_braw, long_min, weak_db, offs, ffmpeg):
    flags, notes = [], [d["note"]] if d["note"] else []
    if detect.needs_review(d) or d["profile"] in ("derived", "proxy") or d["camera"] == "unknown":
        flags.append("unexpected")
    if d["vfr"] == "yes":
        flags.append("vfr")
    dur = _duration(h, is_braw)
    if dur is not None and dur > long_min * 60:
        flags.append("long-take")
    start = _start(h)
    cam_off = offs.get(d["camera"].lower())
    if start and cam_off:
        start += datetime.timedelta(seconds=cam_off)
    if is_braw:
        audio = "not measured (BRAW)"
    else:
        a = _audio(h)
        if a is None:
            audio = "none"
            flags.append("no-audio")
        else:
            audio = "embedded {} {}ch {}Hz".format(a.get("codec_name", "?"), a.get("channels", "?"),
                                                  a.get("sample_rate", "?"))
            vol = mean_volume(path, ffmpeg)
            if vol is None:
                notes.append("audio level unreadable")
            elif vol < weak_db:
                flags.append("weak-audio")
                notes.append("mean volume {} dB".format("-inf" if vol == float("-inf") else f"{vol:.1f}"))
    fps = _fps(h, is_braw)
    return {
        "file": path, "camera": d["camera"],
        "local_start": start.isoformat() if start else "",
        "duration_s": f"{dur:.3f}" if dur is not None else "",
        "fps": f"{fps:.3f}".rstrip("0").rstrip(".") if fps else "",
        "vfr": d["vfr"], "size": _size(h, is_braw), "profile": d["profile"],
        "squeeze": _squeeze(h), "lens": str(_exif(h.exif, *_LENS_TAGS)),
        "audio_source": audio, "flags": ";".join(flags), "note": "; ".join(notes),
    }


def _failed(path, exc):
    row = {c: "" for c in COLUMNS}
    row.update(file=path, camera="unknown", flags="unexpected",
               note=f"shoot-table failed on this file: {type(exc).__name__}: {str(exc)[:160]}")
    return row


def unknown_offsets(rows, offsets):
    """Offset camera names that match no row, for a typo check."""
    seen = {r["camera"].lower() for r in rows}
    return sorted(k for k in (offsets or {}) if k.lower() not in seen)


def format_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def summarize(rows):
    flagged = sum(1 for r in rows if r["flags"])
    return f"shoot-table: {len(rows)} file(s), {flagged} with flags"
