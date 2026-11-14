"""
content-check: the degenerate-edit checks of a published agent-editing benchmark
(Timeline-Bench, arXiv 2609.35143: mostly silent, mostly frozen, repeated
footage, dead air, channel imbalance, faces cut by the frame edge) that `qc`
does not run, plus one the benchmark's own sources recommend (never cut inside a
word). Offline on a rendered file: ffprobe, ffmpeg and, for two rows, numpy and
macOS Vision. It never connects to Resolve and writes nothing.

Rows, each {check, status, found, expected, source[, note]} like qc's:
    silent_share      share of the runtime under silence_noise_db for gaps of
                      silence_min_s or longer, FAIL over silent_share_max; no
                      audio stream is a WARN (the benchmark fails it, but a
                      silent draft is legitimate)
    frozen_share      share of the runtime frozen (freezedetect, freeze_min_s
                      or longer), FAIL over frozen_share_max
    channel_balance   a stereo file: the RMS level of the left and right
                      channels against imbalance_max_db; SKIP for other layouts
    repeated_footage  picture shown again after repeat_min_gap_s or more, in a
                      run of repeat_min_s or longer, found by comparing 32x32
                      grey thumbnails sampled at repeat_fps; a run whose
                      pictures hardly change between samples (a held or
                      locked-off shot) is not counted; needs numpy
    cut_in_word       with --words (word times in the render's own time, from
                      transcribing the render) and cut times (a --shots CSV
                      start column, else the file's own scene cuts): FAIL where
                      a cut lands inside a word by more than word_inside_tol_s,
                      WARN where it lands within word_edge_s of a word edge
    face_at_edge      sampled frames with a face (macOS Vision): WARN when a
                      face box touches the frame edge in over face_share_max of
                      them; SKIP without the helper or with under face_min
                      frames holding a face

Every threshold is a working default, printed in its row, and set with
--set KEY=VALUE; none is a standard. The benchmark's own limits are the
defaults for the share, balance and repeat rows. Silence and freeze segments
come from the same detector pass qc uses, so a gap shorter than silence_min_s
or a freeze shorter than freeze_min_s is not counted in a share.

Exit 0 when no row fails, 1 on a FAIL or an error.
"""

import bisect
import json
import math
import os
import re
import subprocess
import sys
import tempfile

from rpresolve import cli
from rpresolve import delivercheck as dc
from rpresolve import qc

NAME = "content-check"
HELP = ("Offline: the benchmark's degenerate-edit checks qc lacks (silent and frozen share, "
        "channel balance, repeated footage, a cut inside a word, a face at the edge) on a render")

THRESHOLDS = {
    "silent_share_max": 0.50,    # benchmark: mostly silent
    "frozen_share_max": 0.60,    # benchmark: mostly frozen
    "imbalance_max_db": 6.0,     # benchmark: channel imbalance
    "repeat_min_s": 1.0,         # benchmark: footage shown more than once, over this
    "repeat_min_gap_s": 3.0,     # a repeat is this far or farther from the first showing
    "repeat_fps": 2.0,           # thumbnails sampled per second
    "repeat_max_s": 3600.0,      # longer renders skip the row: the comparison grows with the square of the length
    "repeat_match": 4.0,         # mean grey difference (0-255) under this is the same picture
    "repeat_peak": 48.0,         # and no pixel differs by more than this (a small inset on black differs locally)
    "repeat_motion": 6.0,        # a run must change this much between samples, else it is a held shot
    "word_edge_s": 0.03,         # video-use's lower bound for padding a cut off a word edge
    "word_inside_tol_s": 0.04,   # a cut this far inside a word or more is inside it
    "face_edge": 0.005,          # Vision clips a cut face to the frame, so a box this close to the edge is cut
    "face_share_max": 0.30,      # benchmark: faces cut by the frame edge
    "face_samples": 60.0,        # frames sampled for faces
    "face_min": 3.0,             # frames holding a face needed to judge
}
WD = " (working default)"


def add_arguments(p):
    p.add_argument("file", help="The rendered file")
    p.add_argument("--words", metavar="JSON",
                   help="Word timestamps of this render in its own time (a transcription of the render)")
    p.add_argument("--shots", metavar="CSV",
                   help="Shot list CSV with a start column; its starts are the cut times")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="Override a threshold (repeatable)")
    p.add_argument("--no-faces", action="store_true", help="Skip the face_at_edge row")
    p.add_argument("--json", action="store_true", help="Print the table as JSON")


def _row(name, status, found, expected, note=None):
    return {"check": name, "status": status, "found": found, "expected": expected,
            "source": "ffmpeg", **({"note": note} if note else {})}


def _ok(name, ok, found, expected, note=None):
    return _row(name, qc.PASS if ok else qc.FAIL, found, expected, note)


def _at(t):
    return qc._at(t)


def _covered(segs, dur):
    """Seconds under a list of (start, duration); a duration of None runs to dur."""
    return sum(((dur - s) if d is None else d) for s, d in segs)


def parse_channel_rms(text):
    """[RMS level dB] per channel from astats' per-channel blocks, in channel order."""
    levels = []
    for m in re.finditer(r"Channel: \d+\n(?:(?!Channel: ).)*?RMS level dB: (-?(?:inf|\d+(?:\.\d+)?))", text, re.S):
        levels.append(float("-inf") if m.group(1).endswith("inf") else float(m.group(1)))
    return levels


def _channel_levels(path):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(path), "-map", "0:a:0",
           "-af", "astats=measure_overall=none:measure_perchannel=RMS_level", "-f", "null", "-"]
    proc = dc._run(cmd, dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not read the channel levels of {path}: "
                            f"{proc.stderr.strip()[-300:]}")
    return parse_channel_rms(proc.stderr)


def channel_row(levels, channels, th):
    if channels != 2:
        return _row("channel_balance", qc.SKIP, f"{channels} channels", "a stereo file")
    if len(levels) != 2:
        return _row("channel_balance", qc.SKIP, "levels unreadable", "two channel levels")
    left, right = levels
    if math.isinf(left) and math.isinf(right):
        return _row("channel_balance", qc.SKIP, "both channels silent", "sound in the file")
    diff = float("inf") if math.isinf(left) or math.isinf(right) else abs(left - right)
    top = th["imbalance_max_db"]
    return _ok("channel_balance", diff <= top,
               "one channel silent" if diff == float("inf") else f"{diff:.1f} dB apart",
               f"left and right within {top:g} dB", f"L {left:.1f} dB, R {right:.1f} dB RMS{WD}")


def share_rows(log, dur, has_video, has_audio, th):
    rows = []
    if not has_audio:
        rows.append(_row("silent_share", qc.WARN, "no audio stream", "an audio stream",
                         "WARN, not the benchmark's FAIL: a silent draft or preview is legitimate"))
    else:
        sil = _covered(qc.parse_silence(log), dur)
        top = th["silent_share_max"]
        rows.append(_ok("silent_share", sil / dur <= top, f"{sil / dur:.0%} silent",
                        f"at most {top:.0%} silent", f"gaps of {th['silence_min_s']:g}s or longer{WD}"))
    if has_video:
        frz = _covered(qc._long(qc.parse_freeze(log), th["freeze_min_s"]), dur)
        top = th["frozen_share_max"]
        rows.append(_ok("frozen_share", frz / dur <= top, f"{frz / dur:.0%} frozen",
                        f"at most {top:.0%} frozen", f"freezes of {th['freeze_min_s']:g}s or longer{WD}"))
    return rows


def repeat_runs(np, thumbs, fps, th):
    """[(first_start_s, shown_again_s, length_s)] from an array of thumbnails
    (one row per sample): runs of consecutive samples whose picture matches the
    picture a fixed offset earlier, offset at least repeat_min_gap_s, the run
    lasting repeat_min_s or more and its pictures changing between samples."""
    n = len(thumbs)
    gap = max(1, int(math.ceil(th["repeat_min_gap_s"] * fps)))
    need = max(1, int(math.ceil(th["repeat_min_s"] * fps)))
    steps = np.abs(np.diff(thumbs, axis=0)).mean(axis=1) if n > 1 else np.array([])
    by_offset = {}
    for i in range(n - gap):
        diff = np.abs(thumbs[i + gap:] - thumbs[i])
        same = (diff.mean(axis=1) < th["repeat_match"]) & (diff.max(axis=1) < th["repeat_peak"])
        for j in np.nonzero(same)[0]:
            by_offset.setdefault(int(j) + gap, []).append(i)
    runs = []
    for off, idx in by_offset.items():
        start = prev = idx[0]
        for i in idx[1:] + [None]:
            if i is not None and i == prev + 1:
                prev = i
                continue
            length = prev - start + 1
            seg = steps[start:prev + 1]
            moving = seg.mean() if len(seg) else 0.0
            if length >= need and moving > th["repeat_motion"]:
                runs.append((start / fps, (start + off) / fps, length / fps))
            if i is not None:
                start = prev = i
    return sorted(runs)


def _union_s(spans):
    """Seconds covered by a list of (start, end), overlaps counted once."""
    total, end = 0.0, None
    for a, b in sorted(spans):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def _thumbs(np, path, fps):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-i", dc._src(path),
           "-map", "0:v:0", "-vf", f"fps={fps:g},scale=32:32:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=dc.READ_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise dc.CheckError("ffmpeg timed out reading thumbnails")
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not read thumbnails of {path}: "
                            f"{proc.stderr.decode(errors='replace').strip()[-300:]}")
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    return raw[:len(raw) // 1024 * 1024].reshape(-1, 1024).astype(np.float32)


def repeated_row(path, th, dur=None):
    np = qc._np()
    if np is None:
        return _row("repeated_footage", qc.SKIP, "needs numpy", "numpy")
    if dur is not None and dur > th["repeat_max_s"]:
        return _row("repeated_footage", qc.SKIP, f"{dur:.0f}s long", f"under {th['repeat_max_s']:g}s",
                    "raise repeat_max_s with --set to run it anyway")
    fps = th["repeat_fps"]
    thumbs = _thumbs(np, path, fps)
    if len(thumbs) < 2:
        return _row("repeated_footage", qc.SKIP, "under two samples", "a render of a few seconds")
    runs = repeat_runs(np, thumbs, fps, th)
    total = _union_s([(b, b + r) for _, b, r in runs])
    shown = "; ".join(f"{_at(a)} again at {_at(b)} for {r:g}s" for a, b, r in runs[:3])
    if len(runs) > 3:
        shown += f"; {len(runs) - 3} more"
    return _ok("repeated_footage", not runs, shown if runs else "none",
               f"no picture repeated for {th['repeat_min_s']:g}s or more",
               f"{total:g}s repeated; samples {fps:g}/s, 32x32 grey{WD}" if runs else
               f"samples {fps:g}/s, 32x32 grey; a held shot is not counted{WD}")


def word_rows(cuts, words, th, source="the file's scene cuts"):
    """The cut_in_word row. words: [{start, end, word}] sorted by start."""
    if not cuts:
        return _row("cut_in_word", qc.SKIP, "no cuts found", "at least one cut",
                    f"cuts from {source}")
    edge_s = th["word_edge_s"]
    starts = [w["start"] for w in words]
    inside, near = [], []
    for c in cuts:
        hi = bisect.bisect_right(starts, c + edge_s)
        for w in words[max(0, hi - 3):hi]:
            margin = min(th["word_inside_tol_s"], (w["end"] - w["start"]) / 4)
            if w["start"] + margin < c < w["end"] - margin:
                inside.append((c, w))
                break
            if min(abs(c - w["start"]), abs(c - w["end"])) < edge_s:
                near.append((c, w))
                break
    note = f"cuts from {source}"
    if inside:
        shown = ", ".join(f"{_at(c)} in '{w['word']}'" for c, w in inside[:3])
        return _row("cut_in_word", qc.FAIL, f"{len(inside)} of {len(cuts)} cuts inside a word: {shown}",
                    "no cut inside a word", note)
    if near:
        shown = ", ".join(f"{_at(c)} by '{w['word']}'" for c, w in near[:3])
        return _row("cut_in_word", qc.WARN, f"{len(near)} of {len(cuts)} cuts within {edge_s * 1000:.0f} ms "
                    f"of a word edge: {shown}", "no cut on a word edge", f"{note}; pad 30 to 200 ms{WD}")
    return _row("cut_in_word", qc.PASS, f"{len(cuts)} cuts, none in a word", "no cut inside a word", note)


def face_row(path, dur, th):
    from rpresolve import vision
    n = int(th["face_samples"])
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-i", dc._src(path),
               "-map", "0:v:0", "-vf", f"fps={n / max(dur, 1.0):.4f},scale=640:-2", "-frames:v", str(n),
               os.path.join(tmp, "f%04d.jpg")]
        proc = dc._run(cmd, dc.READ_TIMEOUT_S)
        if proc.returncode != 0:
            raise dc.CheckError(f"ffmpeg could not sample frames of {path}: {proc.stderr.strip()[-300:]}")
        images = sorted(os.path.join(tmp, f) for f in os.listdir(tmp))
        boxes = vision.face_boxes(images) if images else {}
    if boxes is None:
        return _row("face_at_edge", qc.SKIP, "Vision helper unavailable", "macOS with swiftc")
    e = th["face_edge"]
    with_face = [b for b in boxes.values() if b]
    if len(with_face) < th["face_min"]:
        return _row("face_at_edge", qc.SKIP, f"faces in {len(with_face)} of {len(images)} frames",
                    f"faces in {th['face_min']:g} frames or more")
    cut = [b for b in with_face if any(f["x"] < e or f["y"] < e or f["x"] + f["w"] > 1 - e
                                       or f["y"] + f["h"] > 1 - e for f in b)]
    share, top = len(cut) / len(with_face), th["face_share_max"]
    return _row("face_at_edge", qc.PASS if share <= top else qc.WARN,
                f"{share:.0%} of {len(with_face)} frames with a face", f"at most {top:.0%} touching the edge",
                f"a box within {e:.0%} of the frame edge counts; {len(images)} frames sampled{WD}")


def run_checks(path, th, words=None, shots=None, faces=True):
    """The table for path: {file, destination, checks, counts, status, thresholds}."""
    dc.check_tools(ffmpeg=True)
    if not os.path.isfile(path):
        raise dc.CheckError(f"{path} is not a file.")
    info = dc.probe(path)
    streams = info.get("streams") or []
    vid = next((s for s in streams if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")), None)
    aud = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = dc.duration(info)
    if not dur or dur <= 0:
        raise dc.CheckError(f"{path} has no readable duration.")
    log = qc._detect_pass(path, th, vid is not None, aud is not None)
    rows = share_rows(log, dur, vid is not None, aud is not None, th)
    if aud is not None:
        rows.append(channel_row(_channel_levels(path), int(aud.get("channels") or 0), th))
    if vid is not None:
        rows.append(repeated_row(path, th, dur))
        cuts, source = None, "the file's scene cuts"
        if shots:
            cuts = qc.shot_cuts(shots)
            if cuts is None:
                raise dc.CheckError(f"{shots} has no start column ({', '.join(qc.START_COLUMNS)}).")
            source = "the shot list"
        else:
            cuts = qc.parse_scdet(log, th["scdet_threshold"])
        if words is None:
            rows.append(_row("cut_in_word", qc.SKIP, "no word file", "a word JSON (--words)"))
        else:
            rows.append(word_rows(cuts, words, th, source))
        if faces:
            rows.append(face_row(path, dur, th))
    rank = {qc.FAIL: 0, qc.WARN: 1, qc.PASS: 2, qc.SKIP: 3}
    rows.sort(key=lambda r: rank[r["status"]])
    counts = {s: sum(r["status"] == s for r in rows) for s in (qc.PASS, qc.FAIL, qc.WARN, qc.SKIP)}
    return {"file": os.path.basename(path), "destination": None, "checks": rows, "counts": counts,
            "status": qc.FAIL if counts[qc.FAIL] else qc.PASS,
            "thresholds": {k: th[k] for k in THRESHOLDS}}


def format_report(result):
    """The table as aligned text lines, FAIL rows first."""
    lines = [result["file"]]
    for r in result["checks"]:
        lines.append(f"  {r['status']:<4}  {r['check']:<17} found {r['found']}  (expected {r['expected']})"
                     + (f"  [{r['note']}]" if r.get("note") else ""))
    c = result["counts"]
    warn = f", {c[qc.WARN]} warn" if c.get(qc.WARN) else ""
    lines.append(f"content-check: {c[qc.PASS]} pass, {c[qc.FAIL]} fail{warn}, {c[qc.SKIP]} skipped: "
                 f"{result['status'].upper()}")
    return "\n".join(lines) + "\n"


def run(args):
    """Run the table over one rendered file and print it (text, or JSON with
    --json). Exit 0 when no row fails, 1 on a FAIL or an error."""
    try:
        th = qc.parse_overrides(args.set, base={**qc.THRESHOLDS, **THRESHOLDS})
        words = qc.load_words(os.path.abspath(args.words)) if args.words else None
        shots = os.path.abspath(args.shots) if args.shots else None
        result = run_checks(os.path.abspath(args.file), th, words, shots, faces=not args.no_faces)
    except (dc.CheckError, dc.ToolMissing, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(format_report(result), end="")
    return 1 if result["status"] == qc.FAIL else 0


if __name__ == "__main__":
    cli.script(__name__)
