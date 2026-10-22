"""
rpresolve.captionlint: a caption file checked against reading rules, as a
FAIL-first table in qc's shape. Offline and stdlib only.

Reads an .srt (delivercheck.parse_srt) or TTML (.ttml, .xml, .dfxp;
captions.parse_ttml, the reader deliver_captions uses). Rows:

    parse          the file reads: SubRip blocks well formed, in time order,
                   at least one cue (FAIL)
    line_length    no line over max_chars characters (FAIL)
    lines_per_cue  no cue over max_lines lines (FAIL)
    reading_speed  no cue over max_cps characters per second (WARN: a short
                   cue may run fast)
    overlap        no cue starts before an earlier one ends (FAIL)
    gap            no gap between cues under min_gap_s, which reads as a
                   flicker (WARN); longer gaps are pauses and pass
    safe_zone      every positioned cue inside title safe, safe_margin_pct
                   in from each edge (FAIL); SKIP with no position data

Characters are counted with markup taken off (<i>, <font ...>, {\\an8});
a line break is not a character. Reading speed is a cue's characters over
its duration.

Position data read: SubRip coordinates on the timing line (X1:.. X2:..
Y1:.. Y2:.., pixels, so --size WxH is needed) and TTML regions that a
body, div, p or span names, with tts:origin and tts:extent as attributes in
% or px (px against the root's tts:extent, else --size). A region styled
any other way, or a SubRip {\\anN} alignment tag, is not a position.

Thresholds: 42 characters a line is resolve-config.json's landscape figure
(portrait 16, square 24: pass --set max_chars=16); two lines, 20 characters
a second and a two-frame gap are Netflix's English timed-text
limits for adult programmes; title safe at 90% of the frame is SMPTE ST
2046-1's. Override any with --set KEY=VALUE.

Run from production/:
    python3 -m rpresolve.captionlint CAPTIONS [--size 1920x1080] [--set max_chars=16] [--json]

Exit 1 when a row FAILs, 0 otherwise (WARN included), 2 when the file
cannot be read.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

from . import captions, delivercheck as dc, qc

PASS, FAIL, SKIP = dc.PASS, dc.FAIL, dc.SKIP
WARN = "WARN"
ORDER = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}

THRESHOLDS = {
    "max_chars": 42,
    "max_lines": 2,
    "max_cps": 20.0,
    "min_gap_s": 0.066,  # two frames at 30 fps: a two-frame gap at 24, 25 or 30 passes
    "safe_margin_pct": 5.0,
}

MARKUP = re.compile(r"</?[A-Za-z][^>]*>|\{\\[^}]*\}")
COORDS = re.compile(r"X1:(\d+)\s+X2:(\d+)\s+Y1:(\d+)\s+Y2:(\d+)", re.I)
LENGTH = re.compile(r"(-?\d+(?:\.\d+)?)(%|px)")
TTML_EXT = (".ttml", ".xml", ".dfxp")
SHOWN = 3  # cues a row names before "and N more"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def measure(text):
    """(lines, characters) of one cue's text, markup taken off."""
    lines = [ln for ln in (MARKUP.sub("", x).strip() for x in text.split("\n")) if ln]
    return lines, sum(len(ln) for ln in lines)


def _srt_boxes(text, cues, size):
    """[(cue number, (x0, y0, x1, y1) as frame fractions)] from SubRip
    coordinates; None when the file has none."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    timing = [ln.strip() for ln in lines if dc.SRT_LINE.match(ln.strip())]
    found = [(n, COORDS.search(t)) for n, t in enumerate(timing, 1)]
    found = [(n, m) for n, m in found if m]
    if not found:
        return None, None
    if not size:
        return [], "SubRip coordinates are pixels; pass --size WxH"
    if len(timing) != len(cues):
        return [], "timing lines and cues do not pair up (see the parse row)"
    w, h = size
    return [(n, (int(m[1]) / w, int(m[3]) / h, int(m[2]) / w, int(m[4]) / h))
            for n, m in found], None


def _pair(value, frame):
    """'10% 80%' or '192px 864px' -> (x, y) as frame fractions, or None."""
    parts = (value or "").split()
    if len(parts) != 2:
        return None
    out = []
    for i, part in enumerate(parts):
        m = LENGTH.fullmatch(part)
        if not m:
            return None
        if m[2] == "%":
            out.append(float(m[1]) / 100)
        elif frame:
            out.append(float(m[1]) / frame[i])
        else:
            return None
    return tuple(out)


def _ttml_boxes(text, size):
    """[(region label, box)] for the regions the body names; None when it
    names none. Unreadable regions come back in the note."""
    root = ET.fromstring(text)
    px = re.fullmatch(r"(\d+(?:\.\d+)?)px\s+(\d+(?:\.\d+)?)px",
                      (captions._attr(root, "extent") or "").strip())
    frame = (float(px[1]), float(px[2])) if px else size
    regions = {captions._attr(el, "id"): el for el in root.iter()
               if captions._local(el.tag) == "region"}
    used = Counter(captions._attr(el, "region") for body in root
                   if captions._local(body.tag) == "body" for el in body.iter()
                   if captions._attr(el, "region"))
    if not used:
        return None, None
    boxes, unread = [], []
    for rid, n in sorted(used.items()):
        el = regions.get(rid)
        origin = _pair(captions._attr(el, "origin"), frame) if el is not None else None
        extent = _pair(captions._attr(el, "extent"), frame) if el is not None else None
        if origin is None or extent is None:
            unread.append(rid)
            continue
        boxes.append((f"region {rid} ({n} element(s))",
                      (origin[0], origin[1], origin[0] + extent[0], origin[1] + extent[1])))
    note = (f"region(s) {', '.join(unread)} have no origin and extent in % or px "
            "(px needs the root's tts:extent or --size)") if unread else None
    return boxes, note


def load(path, size=None):
    """{format, cues: [(start, end, text)], problems, boxes, box_note}.
    boxes is None when the file carries no position data. Raises ValueError
    (captions.CaptionError included) when the file cannot be read at all."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        raw = f.read()
    if ext == ".srt":
        text = raw.decode("utf-8-sig")
        cues, problems = dc.parse_srt(text)
        boxes, note = _srt_boxes(text, cues, size)
        return {"format": "srt", "cues": cues, "problems": problems, "boxes": boxes,
                "box_note": note}
    if ext in TTML_EXT:
        doc = captions.parse_ttml(raw)  # bytes: the XML declaration names the encoding
        cues = [(float(a), float(b), t) for a, b, t in doc["cues"]]
        boxes, note = _ttml_boxes(raw, size)
        return {"format": "ttml", "cues": cues, "problems": [] if cues else ["no cues"],
                "boxes": boxes, "box_note": note}
    raise ValueError(f"{path}: not .srt or TTML ({', '.join(TTML_EXT)})")


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def _row(name, status, found, expected, note=None):
    r = {"check": name, "status": status, "found": found, "expected": expected,
         "source": "captions"}
    if note:
        r["note"] = note
    return r


def _verdict(name, bad, total, expected, what="cues", status=FAIL):
    """PASS when `bad` is empty, else `status` naming the first SHOWN."""
    if not bad:
        return _row(name, PASS, f"0 of {total} {what}", expected)
    more = f" and {len(bad) - SHOWN} more" if len(bad) > SHOWN else ""
    return _row(name, status, f"{len(bad)} of {total} {what}", expected,
                "; ".join(bad[:SHOWN]) + more)


def lint(doc, thresholds=None):
    """{format, checks, counts, status, thresholds} for load()'s result.
    status is fail, warn or pass."""
    th = thresholds or THRESHOLDS
    cues, n = doc["cues"], len(doc["cues"])
    probs = doc["problems"]
    rows = [_row("parse", FAIL, f"{len(probs)} problem(s)", "well-formed, time-ordered cues",
                 "; ".join(probs[:SHOWN])) if probs else
            _row("parse", PASS, f"{n} cue(s)", "well-formed, time-ordered cues")]
    checks = ("line_length", "lines_per_cue", "reading_speed", "overlap", "gap")
    if not cues:
        rows += [_row(c, SKIP, "no cues", "-") for c in checks + ("safe_zone",)]
    else:
        long_, tall, fast, over, short = [], [], [], [], []
        last_end = None
        for i, (a, b, text) in enumerate(cues, 1):
            at = f"cue {i} at {a:.2f}s"
            lines, chars = measure(text)
            widest = max((len(ln) for ln in lines), default=0)
            if widest > th["max_chars"]:
                long_.append(f"{at}: {widest} chars")
            if len(lines) > th["max_lines"]:
                tall.append(f"{at}: {len(lines)} lines")
            cps = chars / (b - a) if b > a else float("inf")
            if cps > th["max_cps"] + 1e-9:
                fast.append(f"{at}: {cps:.1f} cps")
            if last_end is not None and a < last_end - 1e-6:
                over.append(f"{at} starts {last_end - a:.3f}s before an earlier cue ends")
            elif last_end is not None and 0 < a - last_end < th["min_gap_s"] - 1e-9:
                short.append(f"{at}: {a - last_end:.3f}s after the cue before")
            last_end = b if last_end is None else max(last_end, b)
        rows += [
            _verdict("line_length", long_, n, f"<= {th['max_chars']:g} chars a line"),
            _verdict("lines_per_cue", tall, n, f"<= {th['max_lines']:g} lines"),
            _verdict("reading_speed", fast, n, f"<= {th['max_cps']:g} cps", status=WARN),
            _verdict("overlap", over, n, "none"),
            _verdict("gap", short, n, f"none under {th['min_gap_s']:g}s", status=WARN),
        ]
        m = th["safe_margin_pct"] / 100
        expected = f"inside {100 - 2 * th['safe_margin_pct']:g}% title safe"
        if doc["boxes"] is None:
            rows.append(_row("safe_zone", SKIP, "no position data", expected))
        elif doc["box_note"] and not doc["boxes"]:
            rows.append(_row("safe_zone", SKIP, "positions not read", expected, doc["box_note"]))
        else:
            out = [(f"cue {k}" if isinstance(k, int) else k) +
                   f": x {x0:.0%}-{x1:.0%}, y {y0:.0%}-{y1:.0%}"
                   for k, (x0, y0, x1, y1) in doc["boxes"]
                   if min(x0, y0) < m - 1e-9 or max(x1, y1) > 1 - m + 1e-9]
            r = _verdict("safe_zone", out, len(doc["boxes"]), expected, what="positioned")
            if doc["box_note"]:
                r["note"] = "; ".join(x for x in (r.get("note"), doc["box_note"]) if x)
            rows.append(r)
    rows.sort(key=lambda r: ORDER[r["status"]])
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ORDER}
    status = "fail" if counts[FAIL] else "warn" if counts[WARN] else "pass"
    return {"format": doc["format"], "cues": n, "checks": rows, "counts": counts,
            "status": status, "thresholds": th}


def format_report(result, path):
    """The table as aligned text lines, FAIL rows first (qc's layout)."""
    lines = [f"{path}  ({result['format']}, {result['cues']} cue(s))"]
    for r in result["checks"]:
        lines.append(f"  {r['status']:<4}  {r['check']:<14} found {r['found']}  "
                     f"(expected {r['expected']})" + (f"  [{r['note']}]" if r.get("note") else ""))
    c = result["counts"]
    lines.append(f"captionlint: {c[PASS]} pass, {c[FAIL]} fail, {c[WARN]} warn, {c[SKIP]} "
                 f"skipped: {result['status'].upper()}")
    return "\n".join(lines) + "\n"


def parse_size(value):
    """'1920x1080' -> (1920, 1080)."""
    m = re.fullmatch(r"(\d+)x(\d+)", value or "")
    if not m or not int(m[1]) or not int(m[2]):
        raise argparse.ArgumentTypeError(f"--size wants WIDTHxHEIGHT in pixels (got {value!r})")
    return int(m[1]), int(m[2])


def add_arguments(p):
    p.add_argument("captions", help="an .srt or TTML file (.ttml, .xml, .dfxp)")
    p.add_argument("--size", type=parse_size, help="frame size WxH, for pixel positions")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help=f"override a threshold ({', '.join(THRESHOLDS)})")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    return p


def run(args):
    try:
        th = qc.parse_overrides(args.set, base=THRESHOLDS)
        result = lint(load(args.captions, args.size), th)
    except (OSError, ValueError, captions.CaptionError) as e:  # bad UTF-8 is a ValueError
        print(f"captionlint: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, indent=2) + "\n" if args.json else
                     format_report(result, args.captions))
    return 1 if result["status"] == "fail" else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.captionlint",
                                 description="A caption file against reading rules.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
