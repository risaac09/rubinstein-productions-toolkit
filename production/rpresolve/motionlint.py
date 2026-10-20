"""
rpresolve.motionlint: a motion spec per piece, and a lint that enforces the
house bans. Pure Python, no Resolve, no media, no numpy.

Spec (JSON), frames counted at fps:
    name, fps ("24000/1001" or a number), length (frames), min_hold (frames,
    default 12), curves {name: [x1, y1, x2, y2]} (cubic-bezier, CSS order),
    segments [{kind: build|hold|resolve, frames, tweens [...]}],
    cues {name: frame}
A tween is {targets [...], prop, ease (a name in curves), frames, stagger
(frames between targets, default 0), start (frames into the segment,
default 0)}. A hold carries no tweens.

Lint, FAIL rows (decision 24, signed 2026-10-20: no elastic, no bounce, no
back overshoot):
    ban-name      a curve or ease named elastic, bounce, back or overshoot
    overshoot     a control point y outside [0, 1]
    bad-curve     not four finite numbers, or x outside [0, 1]
    unknown-ease  a tween names a curve the table lacks
    uneased       a transform tween with no ease, or one that is linear
    sum           segment frames do not add up to length
    min-hold      a hold shorter than min_hold
    tween-fit     start + frames + stagger * (targets - 1) past its segment
    cue           a cue that is not an integer frame inside the piece
    schema        a missing or mistyped field

Run: python3 -m rpresolve.motionlint SPEC.json   (from production/)
Exit 0 pass, 1 any FAIL, 2 unreadable file.
"""

import json
import math
import re
import sys
from fractions import Fraction

KINDS = ("build", "hold", "resolve")
BANNED = re.compile(r"elastic|bounce|back|overshoot", re.I)
# Opacity and colour may stay linear; position, scale and rotation may not.
FREE_PROPS = ("opacity", "color", "fill", "stroke")
LINEAR = (0, 0, 1, 1)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def lint(spec):
    """Return a list of (code, path, message); empty means the spec passes."""
    out = []

    def fail(code, path, msg):
        out.append((code, path, msg))

    if not isinstance(spec, dict):
        return [("schema", "$", "spec is not an object")]
    length = spec.get("length")
    if not _int(length) or length <= 0:
        fail("schema", "length", "length must be a positive integer of frames")
    try:
        if isinstance(spec.get("fps"), bool):
            ok = False
        elif _num(spec.get("fps")):
            ok = spec["fps"] > 0
        else:
            ok = Fraction(spec.get("fps")) > 0
    except (TypeError, ValueError, ZeroDivisionError):
        ok = False
    if not ok:
        fail("schema", "fps", 'fps must be a positive number or a ratio like "24000/1001"')
    min_hold = spec.get("min_hold", 12)
    if not _int(min_hold) or min_hold < 0:
        fail("schema", "min_hold", "min_hold must be a non-negative integer")
        min_hold = 12

    curves = spec.get("curves", {})
    if not isinstance(curves, dict):
        fail("schema", "curves", "curves must map a name to four numbers")
        curves = {}
    for name, q in curves.items():
        path = f"curves.{name}"
        if BANNED.search(name):
            fail("ban-name", path, f"{name!r} names a banned ease (elastic, bounce, back, overshoot)")
        if not (isinstance(q, list) and len(q) == 4 and all(_num(v) for v in q)):
            fail("bad-curve", path, "a curve is [x1, y1, x2, y2], four finite numbers")
            continue
        if not (0 <= q[0] <= 1 and 0 <= q[2] <= 1):
            fail("bad-curve", path, f"x control points {q[0]}, {q[2]} must lie in [0, 1]")
        if not (0 <= q[1] <= 1 and 0 <= q[3] <= 1):
            fail("overshoot", path, f"y control points {q[1]}, {q[3]} leave [0, 1]: the curve overshoots")

    segs = spec.get("segments")
    if not isinstance(segs, list) or not segs:
        fail("schema", "segments", "segments must be a non-empty list")
        segs = []
    total = 0
    for i, s in enumerate(segs):
        p = f"segments[{i}]"
        if not isinstance(s, dict) or s.get("kind") not in KINDS or not _int(s.get("frames")) or s["frames"] <= 0:
            fail("schema", p, f"a segment needs kind in {KINDS} and positive integer frames")
            continue
        total += s["frames"]
        if s["kind"] == "hold":
            if s["frames"] < min_hold:
                fail("min-hold", p, f"hold of {s['frames']} frames is under min_hold {min_hold}")
            if s.get("tweens"):
                fail("schema", p, "a hold carries no tweens")
            continue
        tweens = s.get("tweens", [])
        if not isinstance(tweens, list):
            fail("schema", f"{p}.tweens", "tweens must be a list")
            continue
        for j, t in enumerate(tweens):
            tp = f"{p}.tweens[{j}]"
            targets = t.get("targets") if isinstance(t, dict) else None
            if not (isinstance(targets, list) and targets and isinstance(t.get("prop"), str)
                    and _int(t.get("frames")) and t["frames"] > 0):
                fail("schema", tp, "a tween needs targets, prop and positive integer frames")
                continue
            stagger, start = t.get("stagger", 0), t.get("start", 0)
            if not (_int(stagger) and stagger >= 0 and _int(start) and start >= 0):
                fail("schema", tp, "stagger and start are non-negative integer frames")
                continue
            end = start + t["frames"] + stagger * (len(targets) - 1)
            if end > s["frames"]:
                fail("tween-fit", tp, f"ends at frame {end} of a {s['frames']}-frame segment")
            ease = t.get("ease")
            if ease is not None and not isinstance(ease, str):
                fail("schema", tp, "ease must be a curve name")
                continue
            if ease is None or (curves.get(ease) is not None and tuple(curves[ease]) == LINEAR) or ease == "linear":
                if t["prop"] not in FREE_PROPS:
                    fail("uneased", tp, f"{t['prop']} moves without an ease")
                continue
            if BANNED.search(str(ease)):
                fail("ban-name", tp, f"ease {ease!r} is banned")
            if ease not in curves:
                fail("unknown-ease", tp, f"ease {ease!r} is not in the curve table")

    if _int(length) and total != length:
        fail("sum", "segments", f"segments add up to {total} frames, length is {length}")
    cues = spec.get("cues", {})
    if not isinstance(cues, dict):
        fail("schema", "cues", "cues must map a name to a frame")
        cues = {}
    for name, f in cues.items():
        if not _int(f) or not (_int(length) and 0 <= f < length):
            fail("cue", f"cues.{name}", f"cue {name!r} must be an integer frame inside the piece")
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.split("Run:")[1].strip().splitlines()[0], file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"cannot read {argv[0]}: {e}", file=sys.stderr)
        return 2
    rows = lint(spec)
    for code, path, msg in rows:
        print(f"FAIL {code:12} {path}: {msg}")
    print(f"{'FAIL' if rows else 'PASS'}: {len(rows)} finding(s)")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
