"""
rpresolve.hooklint: the hook-text rules (CG-H4), written once. A lint only:
it checks lines somebody supplies and never writes, suggests or rewrites
copy. Pure Python, no Resolve, no media.

Rows (PASS, FAIL or SKIP):
    words     9 words or fewer across the hook
    lines     at most 2 lines, each at most 16 characters on a tall (9:16)
              frame and 24 on any other shape (safezone.tall decides)
    dash      no em-dash
    emoji     no emoji
    hold      on screen at least max(0.8 s, 0.3 s per word); SKIP without --hold
    start     starts at frame 0; SKIP without --start
    lexicon   no phrase from an explicit banned list; SKIP when none is given

Run: python3 -m rpresolve.hooklint --line "FIRST LINE" [--line "SECOND"]
         --size 1080x1920 [--hold S] [--start S] [--lexicon FILE]
Exit 0 no FAIL, 1 any FAIL, 2 bad usage.
"""

import argparse
import re
import sys

from rpresolve import safezone

MAX_WORDS = 9
MAX_LINES = 2
LINE_CHARS = {True: 16, False: 24}        # keyed by safezone.tall()
HOLD_FLOOR, HOLD_PER_WORD = 0.8, 0.3
# pictographs only (code point ranges): plain symbols such as a check mark or arrow stay allowed
_RANGES = ((0x1F000, 0x1FAFF), (0x231A, 0x231B), (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x2600, 0x26FF),
           (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E),
           (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
           (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55))
_EMOJI = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _RANGES) + "]")
EM_DASH = chr(0x2014)


def lint(lines, width, height, hold=None, start=None, lexicon=None):
    """[(name, status, detail)] for a hook given as a list of lines."""
    lines = [p for s in lines for p in str(s).split("\n")]
    text = " ".join(lines)
    n = len([w for w in text.split() if re.search(r"\w", w)])
    limit = LINE_CHARS[safezone.tall(width, height)]
    longest = max(map(len, lines), default=0)
    rows = [("words", "PASS" if n <= MAX_WORDS else "FAIL", f"{n} word(s), at most {MAX_WORDS}")]
    ok = 0 < len(lines) <= MAX_LINES and longest <= limit
    rows.append(("lines", "PASS" if ok else "FAIL",
                 f"{len(lines)} line(s) (at most {MAX_LINES}), longest {longest}"
                 f" characters (at most {limit} on {width}x{height})"))
    dash = EM_DASH in text
    rows.append(("dash", "FAIL" if dash else "PASS", "em-dash found" if dash else "no em-dash"))
    rows.append(("emoji", "FAIL" if _EMOJI.search(text) else "PASS", "emoji found" if _EMOJI.search(text) else "no emoji"))
    need = max(HOLD_FLOOR, HOLD_PER_WORD * n)
    rows.append(("hold",) + (("SKIP", "no hold given") if hold is None else
                ("PASS" if hold >= need - 1e-9 else "FAIL", f"{hold:g} s, at least {need:g} s")))
    rows.append(("start",) + (("SKIP", "no start given") if start is None else
                ("PASS" if start == 0 else "FAIL", f"starts at {start:g} s, must be frame 0")))
    words = [w.strip().lower() for w in (lexicon or []) if w.strip()]
    if not words:
        rows.append(("lexicon", "SKIP", "no banned list configured"))
    else:
        low = text.lower()
        hit = [w for w in words if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low)]
        rows.append(("lexicon", "FAIL" if hit else "PASS", "banned: " + ", ".join(hit) if hit else "none found"))
    return rows


def _size(v):
    m = re.fullmatch(r"(\d+)x(\d+)", v)
    if not m:
        raise argparse.ArgumentTypeError(f"--size wants WIDTHxHEIGHT in pixels (got {v!r})")
    if not (int(m[1]) and int(m[2])):
        raise argparse.ArgumentTypeError("--size must be non-zero")
    return int(m[1]), int(m[2])


def _secs(v):
    f = float(v)
    if not 0 <= f < float("inf"):
        raise argparse.ArgumentTypeError(f"seconds must be a finite number, 0 or more (got {v!r})")
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.hooklint", description=__doc__.split("\n\n")[0])
    ap.add_argument("--line", action="append", required=True, help="one line of the hook; repeat for a second")
    ap.add_argument("--size", type=_size, required=True, help="WIDTHxHEIGHT of the frame")
    ap.add_argument("--hold", type=_secs, help="seconds on screen")
    ap.add_argument("--start", type=_secs, help="seconds into the clip where it appears")
    ap.add_argument("--lexicon", help="file of banned phrases, one per line; omit to SKIP that row")
    a = ap.parse_args(argv)
    lex = None
    if a.lexicon:
        try:
            with open(a.lexicon, encoding="utf-8") as f:
                lex = f.read().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            print(f"{a.lexicon}: {getattr(e, 'strerror', None) or e}", file=sys.stderr)
            return 2
    rows = lint(a.line, *a.size, hold=a.hold, start=a.start, lexicon=lex)
    for name, status, detail in rows:
        print(f"{status:<5}{name:<8}{detail}")
    return 1 if any(s == "FAIL" for _, s, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
