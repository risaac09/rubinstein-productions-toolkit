#!/usr/bin/env python3
"""vertcut manifest: a cuts worksheet (id, in, out, title) as a cut manifest v2.

The rows are read by vertcut_cut.read_manifest, the one worksheet parser. Its
four-part HH:MM:SS:FF timecodes are swapped, for this run only, for the
NTSC-correct reading rpresolve uses (frames counted at the nominal rate, then
divided by the real one), so the spans written here are the ones
rpresolve.cutlist reads back from the same worksheet.
"""

import argparse
import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vertcut_cut as vc  # noqa: E402  (puts production/ on sys.path)
from rpresolve import cutlist, paths  # noqa: E402


@contextlib.contextmanager
def _ntsc_timecodes():
    """vertcut_cut.parse_tc with its HH:MM:SS:FF branch replaced by cutlist's."""
    orig = vc.parse_tc

    def parse(s, fps):
        if len(s.strip().split(":")) == 4:
            try:
                return cutlist.parse_timecode(s, fps)
            except cutlist.CutlistError as e:
                vc.die(str(e))
        return orig(s, fps)

    vc.parse_tc = parse
    try:
        yield
    finally:
        vc.parse_tc = orig


def build(src, tsv, words, fps, offset=0.0):
    with _ntsc_timecodes():
        rows = vc.read_manifest(tsv, fps)
    if min(r["start"] for r in rows) + offset < 0:
        vc.die("--offset %g puts a worksheet in-point before the start of the media" % offset)
    spans = {}
    for r in rows:
        spans.setdefault(r["id"], []).append((r["start"] + offset, r["end"] + offset))
    try:
        return cutlist.build_manifest(spans, os.path.abspath(src), fps, os.path.abspath(words),
                                      None, version=2)
    except (cutlist.CutlistError, OSError, ValueError) as e:
        vc.die(str(e))


def mid_word_cuts(m):
    """[(clip, edge, time, word)] for each in or out that lands inside a spoken word."""
    words = cutlist.load_words(m["sources"][0]["words"])
    return [(c["name"], edge, s[edge], w["word"]) for c in m["clips"] for s in c["spans"]
            for edge in ("in", "out") for w in words if w["start"] < s[edge] < w["end"]]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vertcut manifest")
    ap.add_argument("src")
    ap.add_argument("tsv", help="cuts worksheet: id, in, out, title")
    ap.add_argument("--words", required=True, help="Whisper JSON with word timestamps")
    ap.add_argument("-o", "--out", required=True, help="manifest JSON to write, outside any git tree")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="seconds added to every worksheet timecode (as in vertcut cut)")
    ap.add_argument("--fps", type=float, help="frame rate for HH:MM:SS:FF (default: probed from SRC)")
    a = ap.parse_args(argv)
    problem = paths.out_problem(a.out)
    if problem:
        vc.die(problem)
    fps = a.fps or vc.probe(a.src)[2]
    m = build(a.src, a.tsv, a.words, fps, a.offset)
    for clip, edge, t, word in mid_word_cuts(m):
        print("vertcut: WARN %s %s at %.3f s is inside the word %r" % (clip, edge, t, word.strip()),
              file=sys.stderr)
    try:
        paths.write_private(a.out, json.dumps(m, indent=1) + "\n")
    except (paths.OutputRefused, OSError) as e:
        vc.die(str(e))
    n = sum(len(c["spans"]) for c in m["clips"])
    print("vertcut: wrote %s (%d clip(s), %d span(s), %g fps)" % (a.out, len(m["clips"]), n, fps))


if __name__ == "__main__":
    main()
