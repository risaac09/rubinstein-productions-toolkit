"""
rpresolve.gatepacket: the packet Isaac reads before a long render or a replacement
of a file a client can see (the 30-minute rule in production/media-review-loop.md).
Per clip: one full-frame contact sheet and two 20 s listening excerpts. Offline;
ffmpeg only; no Resolve, and nothing is sent anywhere.

Input is review copies (one file each), or a source plus a vertcut cuts TSV
(id, in, out, title), so the sheets come from the planned spans before any render.

Output folder OUT, which must be new or empty and outside any git tree:

    <name>.sheet.jpg   6 x 4 cells, each in a 320 px box (at most 1920 px wide),
                       under 1 MiB; the first frame of each time slot of the clip
                       (the select + scale + tile method of tools/ipad-probe/recipes/sheet.sh)
    <name>.open.wav    20 s, 48 kHz, from the clip's first frame (a clip under 20 s gives its
                       whole length, and both excerpts are then the same)
    <name>.line.wav    20 s centred on the sendable line when given, else the middle
    packet.json        {"version": 1, "files": [{"file", "sha256"}]}: every sheet and
                       excerpt, in order. Its sha256 is what Isaac saw.
    sealed/key.json    labels "1", "2", ... naming the clips, so review-log can decode
                       the line (the layout of a mixbuild packet)
    gate.json          the summary, shaped as below

gate.json:
    version          1
    project          the project id, like P-0001
    clip_count       number of clips
    total_runtime_s  sum of the clips' lengths. Render time is never estimated here.
    inputs           [{"path", "sha256"}]: each review copy, or the source and the cuts TSV
    packet_sha256    sha256 of packet.json
    clips            [{"name", "input" (index into inputs), "start_s", "end_s", "duration_s",
                       "sheet": {"file", "sha256", "cells": [{"cell", "t_s"}]},
                       "excerpts": [{"file", "sha256", "start_s", "seconds"}]}]
                     start_s and end_s are positions in the input; cell and excerpt
                     times are seconds from the clip's first frame.
    pending          the review-log line waiting for the answer: project, step "gate",
                     options (label to clip name), choice null, reason null, device
                     null, stimulus = packet_sha256.

To answer, run review-log with --step gate --choice go (or hold) --key OUT --device D
--project P; the line it writes has stimulus = packet_sha256, so a later reader
matches the answer to this gate.json. Claude never starts a render.
"""

import json
import math
import os
import re
import shutil
import subprocess
from fractions import Fraction

from rpresolve import cutlist, delivercheck, paths, reviewlog

COLS, ROWS, CELL = 6, 4, 320
EXCERPT_S = 20.0
MAX_SHEET_BYTES = (1 << 20) - 1
QUALITIES = (3, 6, 10, 15, 22, 31)
SHOWINFO = re.compile(r"Parsed_showinfo.* pts_time: *(-?[0-9.]+)")


class GateError(ValueError):
    """A packet that cannot be built."""


def _run(cmd, timeout=600):
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GateError(f"ffmpeg timed out after {timeout} s")
    if p.returncode != 0:
        raise GateError("ffmpeg failed: " + p.stderr.strip()[-300:])
    return p.stderr


def _sha(path):
    return reviewlog._sha256(path)


def _write_json(path, doc):
    paths.write_private(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def _name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def probe_clip(path):
    """(duration s, fps as a Fraction) of a file's picture."""
    try:
        delivercheck.check_tools(ffmpeg=False)
        doc = delivercheck.probe(path)
    except (delivercheck.CheckError, delivercheck.ToolMissing) as e:
        raise GateError(str(e))
    vid = next((s for s in doc.get("streams", []) if s.get("codec_type") == "video"), None)
    try:
        dur = float(doc["format"]["duration"])
        num, _, den = (vid or {}).get("r_frame_rate", "").partition("/")
        return dur, Fraction(int(num), int(den or 1))
    except (KeyError, ValueError, ZeroDivisionError):
        raise GateError(f"{path} has no readable duration and frame rate")


def contact_sheet(src, start, dur, out):
    """One JPEG of COLS x ROWS cells from src[start:start+dur]; returns the cell
    times in seconds from the clip's first frame."""
    iv = max(dur / (COLS * ROWS) - 0.0005, 0.001)
    vf = (f"select='isnan(prev_selected_t)+gt(floor(t/{iv:.4f})\\,floor(prev_selected_t/{iv:.4f}))',showinfo,"
          f"scale=w={CELL}:h={CELL}:force_original_aspect_ratio=decrease:force_divisible_by=2,tile={COLS}x{ROWS}")
    for q in QUALITIES:  # coarser until the sheet fits a chat attachment
        log = _run([delivercheck.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-y",
                    "-ss", f"{start:.6f}", "-t", f"{dur:.6f}", "-i", src, "-map", "0:v:0", "-vf", vf,
                    "-frames:v", "1", "-update", "1", "-q:v", str(q), out])
        if os.path.getsize(out) <= MAX_SHEET_BYTES:
            return [round(float(t), 3) for t in SHOWINFO.findall(log)][:COLS * ROWS]
    raise GateError(f"{out} stays over 1 MiB at the coarsest quality")


def excerpt(src, start, seconds, out):
    _run([delivercheck.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
          "-ss", f"{start:.6f}", "-t", f"{seconds:.6f}", "-i", src, "-vn", "-map", "0:a:0",
          "-ar", "48000", "-c:a", "pcm_s16le", out])


def excerpt_starts(dur, sendable=None):
    """(opening start, line start) in seconds from the clip's first frame; the
    line excerpt is centred on `sendable`, else on the middle, and kept inside the clip."""
    n = min(EXCERPT_S, dur)
    centre = dur / 2 if sendable is None else sendable
    return 0.0, min(max(centre - n / 2, 0.0), dur - n)


def clips_from_copies(files):
    clips, inputs = [], []
    for i, f in enumerate(files):
        f = os.path.abspath(f)
        dur, _ = probe_clip(f)
        inputs.append(f)
        clips.append({"name": _name(os.path.splitext(os.path.basename(f))[0]), "input": i,
                      "path": f, "start": 0.0, "end": dur})
    return clips, inputs


def clips_from_cuts(source, tsv, offset=0.0):
    source, tsv = os.path.abspath(source), os.path.abspath(tsv)
    dur, fps = probe_clip(source)
    try:
        spans = cutlist.spans_from_vertcut_tsv(tsv, float(fps), offset)
    except (cutlist.CutlistError, OSError, ValueError) as e:
        raise GateError(f"{tsv}: {e}")
    if not spans:
        raise GateError(f"no rows in {tsv}")
    if not math.isfinite(offset):
        raise GateError("--offset must be a finite number")
    clips = []
    for cid, sp in spans.items():
        if len(sp) != 1:
            raise GateError(f"clip {cid}: one continuous span per row, found {len(sp)}")
        (a, b), = sp
        if not 0 <= a < b <= dur + 0.001:
            raise GateError(f"clip {cid}: {a:.3f} to {b:.3f} s is not a span inside the source (0 to {dur:.3f} s)")
        clips.append({"name": _name(str(cid)), "input": 0, "path": source, "start": a, "end": b})
    return clips, [source, tsv]


def build(out, project, clips, inputs, sendable=None):
    """Write the packet into the new folder `out`; returns gate.json's dict.
    clips: [{"name", "input", "path", "start", "end"}]. sendable: {name: seconds from the clip's start}."""
    sendable = sendable or {}
    if not reviewlog.PROJECT_RE.fullmatch(project or ""):
        raise GateError(f"project id {project!r} must look like P-0001; no names")
    names = [c["name"] for c in clips]
    if len(set(names)) != len(names):
        raise GateError("two clips share a name: " + ", ".join(sorted({n for n in names if names.count(n) > 1})))
    for k, v in sendable.items():
        c = next((c for c in clips if c["name"] == k), None)
        if c is None or not 0 <= v <= c["end"] - c["start"]:
            raise GateError(f"--sendable {k}={v}: no such clip, or a time outside it")
    out = os.path.abspath(out)
    problem = paths.out_problem(os.path.join(out, "gate.json"))
    if problem:
        raise GateError(problem)
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise GateError(f"{out} exists and is not an empty folder; nothing is overwritten")
    try:
        delivercheck.check_tools()
    except delivercheck.ToolMissing as e:
        raise GateError(str(e))
    made = not os.path.exists(out)
    os.makedirs(os.path.join(out, "sealed"))
    try:
        return _write(out, project, clips, inputs, sendable)
    except BaseException:  # no half packet is left to block the next run
        shutil.rmtree(out, ignore_errors=True)
        if not made:
            os.makedirs(out, exist_ok=True)  # the empty folder it was given
        raise


def _write(out, project, clips, inputs, sendable):
    hashes = {p: _sha(p) for p in inputs}
    files, doc_clips, shas = [], [], {}
    for c in clips:
        dur = c["end"] - c["start"]
        sheet = os.path.join(out, c["name"] + ".sheet.jpg")
        cells = contact_sheet(c["path"], c["start"], dur, sheet)
        n = min(EXCERPT_S, dur)
        outputs = []
        for kind, s in zip(("open", "line"), excerpt_starts(dur, sendable.get(c["name"]))):
            f = os.path.join(out, f"{c['name']}.{kind}.wav")
            excerpt(c["path"], c["start"] + s, n, f)
            outputs.append((f, s))
        files += [sheet] + [f for f, _ in outputs]
        for f in files[-3:]:
            shas[f] = _sha(f)
        doc_clips.append({
            "name": c["name"], "input": c["input"], "start_s": round(c["start"], 3),
            "end_s": round(c["end"], 3), "duration_s": round(dur, 3),
            "sheet": {"file": os.path.basename(sheet), "sha256": shas[sheet],
                      "cells": [{"cell": i + 1, "t_s": t} for i, t in enumerate(cells)]},
            "excerpts": [{"file": os.path.basename(f), "sha256": shas[f], "start_s": round(s, 3),
                          "seconds": round(n, 3)} for f, s in outputs]})
    options = {str(i + 1): c["name"] for i, c in enumerate(clips)}
    _write_json(os.path.join(out, "sealed", "key.json"),
                {"labels": {k: {"source": v} for k, v in options.items()}})
    _write_json(os.path.join(out, "packet.json"), {"version": 1, "files": [
        {"file": os.path.basename(f), "sha256": shas[f]} for f in files]})
    packet_sha = _sha(os.path.join(out, "packet.json"))
    gate = {"version": 1, "project": project, "clip_count": len(clips),
            "total_runtime_s": round(sum(c["end"] - c["start"] for c in clips), 3),
            "inputs": [{"path": p, "sha256": hashes[p]} for p in inputs], "packet_sha256": packet_sha,
            "clips": doc_clips,
            "pending": {"project": project, "step": "gate", "options": options, "choice": None,
                        "reason": None, "device": None, "stimulus": packet_sha}}
    _write_json(os.path.join(out, "gate.json"), gate)
    return gate
