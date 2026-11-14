"""
paper-edit: validate a paper edit before anything is built, and with --manifest-out
write it as a version 2 cut manifest (manifest.md) that assemble_timeline reads
unchanged. Offline: it never connects to Resolve and builds no timeline.

The paper edit is a TSV with a header row, one row per span, in play order.
Columns (a name on the right of a slash is an accepted alias):
    source/clip_id   required   id of a source in --sources
    in/source_in     required   source time: seconds, MM:SS.mmm, HH:MM:SS.mmm or
                                HH:MM:SS:FF (FF counted at the source's rate)
    out/source_out   required   same forms; the span is [in, out), as in cutlist.py
    order            optional   when present it must read 1, 2, 3 ... down the rows
    beat, function, note   optional   carried by nobody here; read by people
    speaker          optional   turns on the frankenbite flag
    text/approved_text optional the words the span is meant to say
    reuse            optional   yes/true lets the span overlap an earlier span of its source

--sources is JSON: a list of {id, path, sha256?, words?, fps?, duration?}, or an
object {"sources": [...]}. words is a Whisper word JSON (read through the screen,
never the raw file), duration is the source length in seconds, fps the source's
rate (default --fps).

Checks, one row each (pass, review, fail, n/a):
    sources    every row's source is listed, its file exists, its sha256 matches
    bounds     0 <= in < out, and out <= duration where duration is known
    overlap    no two spans of one source overlap unless the later says reuse
    timecode   times parse, FF is below the rate, times sit on the frame grid,
               a source's rate equals --fps (a different rate is a review)
    text       a span's text against the transcript of that span, by the
               coverage rule of cutlist.approved_check
    frankenbite a join of two spans of one speaker that are not adjacent in one
               source: a review, for a meaning check before lock
    length     the spans total --length (+/- --length-tolerance frames)
    order      the order column, when there is one

Exit 0 all pass, 2 any review, 1 any fail. --manifest-out is written on 0 and 2,
never on 1, outside any git tree.
"""

import csv
import json
import math
import os
import sys
from fractions import Fraction

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "paper-edit"
HELP = "Offline: validate a paper edit and write it as a cut manifest"

ALIASES = {"source": "source", "clip_id": "source", "in": "in", "source_in": "in",
           "out": "out", "source_out": "out", "text": "text", "approved_text": "text"}
TRUE = {"1", "yes", "true", "y"}
GRID = 0.02  # frames: how far a time may sit from a frame boundary


def add_arguments(p):
    p.add_argument("paper_edit", help="the paper-edit TSV")
    p.add_argument("--sources", required=True, help="JSON list of sources (see the command's doc)")
    p.add_argument("--fps", required=True, help="the edit's frame rate: 24, 29.97 or 24000/1001")
    p.add_argument("--length", help="the stated total length: seconds or a timecode")
    p.add_argument("--length-tolerance", type=int, default=0, metavar="FRAMES",
                   help="frames the total may differ from --length (default 0)")
    p.add_argument("--name", default="paper-edit", help="the manifest's clip name")
    p.add_argument("--manifest-out", metavar="JSON", help="write the validated edit as a v2 manifest")


def parse_fps(text):
    try:
        f = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"not a frame rate: {text!r}")
    if f <= 0:
        raise ValueError(f"not a frame rate: {text!r}")
    for ntsc in (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)):
        if abs(float(f) - float(ntsc)) < 0.01:  # 29.97 means 30000/1001, as cutlist counts timecode
            return ntsc
    return f


def fps_number(f):
    """The manifest's number: 24000/1001 is written 23.976, as the other tools count it."""
    return round(float(f), 3) if f.denominator == 1001 else (int(f) if f.denominator == 1 else float(f))


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f, delimiter="\t") if r and not r[0].startswith("#")]
    if not rows:
        raise ValueError("the paper edit has no header row")
    head = [ALIASES.get(h.strip().lower(), h.strip().lower()) for h in rows[0]]
    for need in ("source", "in", "out"):
        if need not in head:
            raise ValueError(f"the paper edit has no {need!r} column")
    out = []
    for r in rows[1:]:
        if len(r) > len(head):
            raise ValueError(f"row {len(out) + 1} has more cells than the header")
        out.append(dict(zip(head, [c.strip() for c in r + [""] * (len(head) - len(r))])))
    if not out:
        raise ValueError("the paper edit has no spans")
    return out, head


def read_sources(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(data, list) or not all(isinstance(s, dict) and s.get("id") for s in data):
        raise ValueError("--sources must be a list of objects, each with an id")
    ids = [s["id"] for s in data]
    if len(set(ids)) != len(ids):
        raise ValueError("--sources repeats an id: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
    return {s["id"]: s for s in data}


def timecode(text, fps):
    """Seconds, or ValueError. FF above the nominal rate is refused here."""
    from rpresolve import cutlist
    parts = text.split(":")
    try:
        if len(parts) == 4 and int(parts[3]) >= -(-fps.numerator // fps.denominator):
            raise ValueError(f"frame field of {text} is not below the rate {float(fps):g}")
        return cutlist.parse_timecode(text, float(fps))
    except (cutlist.CutlistError, ValueError) as e:
        raise ValueError(str(e))


def times(rows, srcs, fps):
    """Fill row['t_in'], row['t_out'] (seconds) and row['fps']; return the problems."""
    bad = []
    for i, r in enumerate(rows, 1):
        s = srcs.get(r["source"]) or {}
        try:
            r["fps"] = parse_fps(s["fps"]) if s.get("fps") else fps
            r["t_in"], r["t_out"] = timecode(r["in"], r["fps"]), timecode(r["out"], r["fps"])
        except ValueError as e:
            r["t_in"] = r["t_out"] = None
            bad.append(f"span {i}: {e}")
    return bad


def check_sources(rows, srcs):
    from rpresolve import cutlist
    bad = []
    for i, r in enumerate(rows, 1):
        s = srcs.get(r["source"])
        if s is None:
            bad.append(f"span {i}: source {r['source']!r} is not in --sources")
    for sid in sorted({r["source"] for r in rows if r["source"] in srcs}):
        s = srcs[sid]
        if not s.get("path") or not os.path.isfile(s["path"]):
            bad.append(f"source {sid}: file not found")
        elif s.get("words") and not os.path.isfile(s["words"]):
            bad.append(f"source {sid}: transcript not found")
        elif s.get("sha256") and cutlist.sha256_file(s["path"]) != s["sha256"]:
            bad.append(f"source {sid}: sha256 does not match the file")
    return ("fail" if bad else "pass"), f"{len(rows)} span(s)", bad


def check_bounds(rows, srcs):
    bad, note = [], []
    for i, r in enumerate(rows, 1):
        if r["t_in"] is None:
            continue
        d = (srcs.get(r["source"]) or {}).get("duration")
        if isinstance(d, (int, float)) and not math.isfinite(d):
            d = None  # NaN passes every comparison; read it as missing
        if r["t_in"] < 0 or r["t_out"] <= r["t_in"]:
            bad.append(f"span {i}: {r['in']} to {r['out']} is not a forward span from 0 or later")
        elif d is not None and r["t_out"] > float(d) + 1e-6:
            bad.append(f"span {i}: out {r['out']} is past the end of source {r['source']} ({float(d):g} s)")
        elif d is None and r["source"] in srcs and r["source"] not in note:
            note.append(r["source"])
    if bad:
        return "fail", "", bad
    if note:
        return "review", "no duration for " + ", ".join(note) + "; the end was not checked", []
    return "pass", "", []


def check_overlap(rows):
    bad = []
    for j, b in enumerate(rows):
        if b["t_in"] is None or b.get("reuse", "").lower() in TRUE:
            continue
        for i, a in enumerate(rows[:j]):
            if a["t_in"] is not None and a["source"] == b["source"] and \
                    a["t_in"] < b["t_out"] and b["t_in"] < a["t_out"]:
                bad.append(f"spans {i + 1} and {j + 1} overlap in source {b['source']} "
                           "(mark the later one reuse if meant)")
    return ("fail" if bad else "pass"), "", bad


def check_timecode(rows, fps, problems):
    bad, odd = list(problems), []
    for i, r in enumerate(rows, 1):
        if r["t_in"] is None:
            continue
        for k, t in (("in", r["t_in"]), ("out", r["t_out"])):
            x = t * float(r["fps"])
            if abs(x - round(x)) > GRID:
                bad.append(f"span {i}: {k} {r[k]} is not on a frame at {float(r['fps']):g} fps")
        if r["fps"] != fps:
            odd.append(f"span {i}: source rate {float(r['fps']):g} differs from the edit's {float(fps):g}")
    if bad:
        return "fail", "", bad
    return ("review", "", odd) if odd else ("pass", f"{float(fps):g} fps", [])


def check_text(rows, srcs):
    from rpresolve import cutlist
    bad, soft, n, cache = [], [], 0, {}
    for i, r in enumerate(rows, 1):
        if not r.get("text") or r["t_in"] is None:
            continue
        n += 1
        w = (srcs.get(r["source"]) or {}).get("words")
        if not w:
            soft.append(f"span {i}: source {r['source']} has no transcript; text not checked")
            continue
        try:
            cache[w] = cache.get(w) or cutlist.load_words(w)
        except (cutlist.CutlistError, OSError, ValueError) as e:
            bad.append(f"span {i}: {e}")
            continue
        heard = cutlist.content_tokens(cutlist.span_text(cache[w], r["t_in"], r["t_out"]))
        v = cutlist.approved_check(heard, cutlist.content_tokens(r["text"]))
        # the transcript is the claim, the paper edit's text the approval: a heard word
        # missing from the text is what the rule reports
        msg = f"span {i}: coverage {v['coverage']}" + (f", unsaid-by-text run {v['unmatched']!r}" if v["unmatched"] else "")
        if v["verdict"] == "fail":
            bad.append(msg)
        elif v["verdict"] == "review":
            soft.append(msg)
    if bad:
        return "fail", "", bad
    if n == 0:
        return "n/a", "no span carries text", []
    return ("review", "", soft) if soft else ("pass", f"{n} span(s)", [])


def check_frankenbite(rows, head):
    if "speaker" not in head:
        return "n/a", "no speaker column", []
    soft = []
    for i, (a, b) in enumerate(zip(rows, rows[1:]), 1):
        if a["speaker"] and a["speaker"] == b["speaker"] and None not in (a["t_out"], b["t_in"]) and \
                not (a["source"] == b["source"] and abs(a["t_out"] - b["t_in"]) < 1e-6):
            soft.append(f"spans {i} and {i + 1}: {a['speaker']} joined across a gap; check the meaning before lock")
    return ("review" if soft else "pass"), "", soft


def total_frames(rows, fps):
    """The edit's length in frames at the edit's rate: each span's source frames,
    counted at the source's own rate, retimed to the edit's (assemble's rule)."""
    from rpresolve import cutlist
    return sum(round(cutlist.frames({"in": r["t_in"], "out": r["t_out"]}, float(r["fps"]))
                     * float(fps / r["fps"])) for r in rows)


def check_length(rows, fps, length, tol):
    if length is None:
        return "n/a", "no --length stated", []
    if any(r["t_in"] is None for r in rows):
        return "n/a", "times did not parse", []
    got, want = total_frames(rows, fps), round(timecode(length, fps) * float(fps))
    if abs(got - want) > tol:
        return "fail", f"{got} frames, stated {want}", [f"the spans run {got - want:+d} frame(s) from the stated length"]
    return "pass", f"{got} frames", []


def check_order(rows, head):
    if "order" not in head:
        return "n/a", "no order column", []
    bad = [f"row {i}: order is {r['order']!r}" for i, r in enumerate(rows, 1) if r["order"] != str(i)]
    return ("fail" if bad else "pass"), "", bad


def manifest(rows, srcs, fps, name):
    """The version 2 manifest of a validated edit; ValueError when it cannot be one."""
    from rpresolve import cutlist
    used = list(dict.fromkeys(r["source"] for r in rows))
    speech = any(srcs[s].get("words") for s in used)
    if speech and not all(srcs[s].get("words") for s in used):
        raise ValueError("some sources have a transcript and some do not; a manifest needs all or none")
    out = []
    for sid in used:
        s = srcs[sid]
        e = {"id": sid, "path": os.path.abspath(s["path"]),
             "sha256": s.get("sha256") or cutlist.sha256_file(s["path"])}
        if speech:
            e["words"] = os.path.abspath(s["words"])
        out.append(e)
    m = {"version": 2, "fps": fps_number(fps), "sources": out,
         "clips": [{"name": name, "spans": [
             {k: v for k, v in (("in", round(r["t_in"], 6)), ("out", round(r["t_out"], 6)),
                                ("source", r["source"]),
                                ("reuse", True if r.get("reuse", "").lower() in TRUE else None))
              if v is not None} for r in rows]}]}
    if not speech:
        m["speech"] = False
    return cutlist.validate_manifest(m, allow_no_speech=not speech)


def out_clash(out, srcs, rows, inputs):
    """Why --manifest-out must not be written, or None: an input, a used source or its
    transcript, or an existing file that is not a manifest (media, a TSV, anything else)."""
    used = [srcs[r["source"]] for r in rows if r["source"] in srcs]
    paths = list(inputs) + [p for s in used for p in (s.get("path"), s.get("words")) if p]
    problem = rppaths.source_problem([out], paths)
    if problem or not os.path.exists(out):
        return problem
    try:
        with open(out, encoding="utf-8") as f:
            old = json.load(f)
        ok = isinstance(old, dict) and "clips" in old and "sources" in old
    except (OSError, ValueError):
        ok = False
    return None if ok else f"{out} exists and is not a manifest; it is never written over"


def run(args):
    from rpresolve import cutlist
    problem = rppaths.out_problem(args.manifest_out, any_git_tree=True) if args.manifest_out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        fps = parse_fps(args.fps)
        rows, head = read_rows(args.paper_edit)
        srcs = read_sources(args.sources)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    problems = times(rows, srcs, fps)
    try:
        length = check_length(rows, fps, args.length, args.length_tolerance)
    except ValueError as e:
        length = ("fail", "", [f"--length: {e}"])
    results = [("sources", check_sources(rows, srcs)), ("bounds", check_bounds(rows, srcs)),
               ("overlap", check_overlap(rows)), ("timecode", check_timecode(rows, fps, problems)),
               ("text", check_text(rows, srcs)), ("frankenbite", check_frankenbite(rows, head)),
               ("length", length), ("order", check_order(rows, head))]
    for name, (status, detail, notes) in results:
        print(f"{status.upper():7} {name:12} {detail}".rstrip())
        for n in notes:
            print(f"        - {n}")
    st = [r[1][0] for r in results]
    fails, reviews = st.count("fail"), st.count("review")
    print(f"paper-edit: {len(rows)} span(s): {st.count('pass')} pass, {reviews} review, "
          f"{fails} fail, {st.count('n/a')} n/a", file=sys.stderr)
    if fails:
        return 1
    if args.manifest_out:
        problem = out_clash(args.manifest_out, srcs, rows, [args.paper_edit, args.sources])
        if problem:
            print(f"ERROR: manifest not written: {problem}", file=sys.stderr)
            return 1
        try:
            m = manifest(rows, srcs, fps, args.name)
        except (ValueError, KeyError, OSError, cutlist.CutlistError) as e:
            print(f"ERROR: manifest not written: {e}", file=sys.stderr)
            return 1
        cli.write_private(args.manifest_out, json.dumps(m, indent=1) + "\n")
        print(f"Wrote {args.manifest_out}", file=sys.stderr)
    return 2 if reviews else 0


if __name__ == "__main__":
    cli.script(__name__)
