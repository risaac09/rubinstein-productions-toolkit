"""
reel-spans: quoted start and end phrases to a checked vertcut cuts worksheet. The tool
proposes nothing. It finds the words the person quoted in a word-timed transcript and
writes in at the first word of the start phrase and out at the last word of the end phrase,
on those words' own edges. Matching ignores case and punctuation (cutlist.tokens).

Per row, never fixed silently:
  FAIL  a phrase not found (the nearest text is a hint, never used), a phrase found more
        than once and no occurrence given (each time listed), an occurrence past the last
        match, or the end before the start. A FAIL row is left out of the worksheet.
  WARN  a span over MAX_S or under MIN_S, or ending on a word with no sentence-final
        punctuation. A WARN row is written.
"""

import csv
import difflib
import re

from . import cutlist, paths

MAX_S = 90.0
MIN_S = 5.0
_FINAL = re.compile(r"[.!?…][\"')\]”’]*$")


def flatten(words):
    """[(token, word index)] over the transcript: a word may hold none or several tokens."""
    return [(t, i) for i, w in enumerate(words) for t in cutlist.tokens(w["word"])]


def find(flat, phrase):
    """(phrase tokens, every match as (first word index, last word index), in order)."""
    want = cutlist.tokens(phrase)
    if not want:
        return want, []
    toks = [t for t, _ in flat]
    n = len(want)
    hits, k = [], 0
    while k <= len(toks) - n:  # a match is not counted again inside itself ("no no" in "no no no")
        if toks[k:k + n] == want:
            hits.append((flat[k][1], flat[k + n - 1][1]))
            k += n
        else:
            k += 1
    return want, hits


def nearest(flat, want, words):
    """The transcript text most like the phrase, as a hint: "text" at 12.30 s."""
    toks = [t for t, _ in flat]
    n = min(len(want), len(toks))
    if not n:
        return None
    target = " ".join(want)
    best = max(range(len(toks) - n + 1),
               key=lambda k: difflib.SequenceMatcher(None, target, " ".join(toks[k:k + n])).ratio())
    a, b = flat[best][1], flat[best + n - 1][1]
    return '"%s" at %.2f s' % (" ".join(w["word"] for w in words[a:b + 1]), words[a]["start"])


def _pick(flat, words, phrase, occurrence, label):
    """(match or None, [FAIL messages])."""
    want, hits = find(flat, phrase)
    if not want:
        return None, ["%s phrase has no words" % label]
    if not hits:
        hint = nearest(flat, want, words)
        return None, ["%s phrase not found%s" % (label, "; nearest text: " + hint if hint else "")]
    if occurrence:
        if occurrence > len(hits):
            return None, ["%s phrase has %d match(es), occurrence %d asked for" % (label, len(hits), occurrence)]
        return hits[occurrence - 1], []
    if len(hits) > 1:
        times = ", ".join("%.2f s" % words[a]["start"] for a, _ in hits)
        return None, ["%s phrase found %d times (%s); give an occurrence number" % (label, len(hits), times)]
    return hits[0], []


def locate(words, rows, min_s=MIN_S, max_s=MAX_S):
    """rows: dicts title, start, end, start_occurrence, end_occurrence (the last two optional,
    1-based). Returns one result per row: n, row, in, out (None on FAIL), fails, warns."""
    flat = flatten(words)
    results = []
    for n, row in enumerate(rows, 1):
        a, fails = _pick(flat, words, row["start"], row.get("start_occurrence"), "start")
        b, more = _pick(flat, words, row["end"], row.get("end_occurrence"), "end")
        fails += more
        warns, tin, tout = [], None, None
        if a and b and b[1] < a[0]:
            fails.append("end phrase comes before the start phrase")
        if not fails:
            tin, tout = words[a[0]]["start"], words[b[1]]["end"]
            if tout - tin > max_s:
                warns.append("span is %.1f s, over %g s" % (tout - tin, max_s))
            if tout - tin < min_s:
                warns.append("span is %.1f s, under %g s" % (tout - tin, min_s))
            if not _FINAL.search(words[b[1]]["word"]):
                warns.append("ends mid-sentence on %r" % words[b[1]]["word"])
        results.append({"n": n, "row": row, "in": tin, "out": tout, "fails": fails, "warns": warns})
    return results


def _occurrence(text):
    if not text:
        return None
    if not text.isdigit() or int(text) < 1:
        raise ValueError("occurrence %r is not a whole number from 1" % text)
    return int(text)


def read_rows(path):
    """The person's TSV: title, start phrase, end phrase, then optional start and end occurrence
    numbers. A first row whose first cell is 'title' is a header; lines starting '# ' are comments."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:  # quotes in a phrase are text, not CSV quoting
        for lineno, cols in enumerate(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE), 1):
            if not "".join(cols).strip() or re.match(r"#(\s|$)", cols[0].lstrip()):
                continue
            if not rows and cols[0].strip().lower() == "title":
                continue
            cols = [c.strip() for c in cols] + [""] * 5
            if not (cols[1] and cols[2]):
                raise ValueError("%s:%d needs title, start phrase and end phrase" % (path, lineno))
            try:
                rows.append({"title": cols[0], "start": cols[1], "end": cols[2],
                             "start_occurrence": _occurrence(cols[3]), "end_occurrence": _occurrence(cols[4])})
            except ValueError as e:
                raise ValueError("%s:%d %s" % (path, lineno, e))
    if not rows:
        raise ValueError("no rows in %s" % path)
    return rows


def worksheet(results):
    """The cuts worksheet text (id, in, out, title) for the rows with no FAIL, or None."""
    lines = ("r%02d\t%.3f\t%.3f\t%s" % (r["n"], r["in"], r["out"], r["row"]["title"])
             for r in results if not r["fails"])
    text = "".join(line.rstrip("\t") + "\n" for line in lines)
    return text or None


def report(results):
    lines = []
    for r in results:
        status = "FAIL" if r["fails"] else "WARN" if r["warns"] else "OK"
        span = "" if r["fails"] else " %.2f-%.2f s (%.1f s)" % (r["in"], r["out"], r["out"] - r["in"])
        lines.append("%s r%02d %s%s" % (status, r["n"], r["row"]["title"], span))
        lines += ["    FAIL: " + m for m in r["fails"]] + ["    WARN: " + m for m in r["warns"]]
    return "\n".join(lines)


def build(words_path, tsv_path, out, min_s=MIN_S, max_s=MAX_S):
    """Locate the phrases and write the worksheet to `out` (refused in any git tree).
    Returns (results, report text, written path or None)."""
    problem = paths.out_problem(out) or paths.source_problem([out], [words_path, tsv_path])
    if problem:
        raise paths.OutputRefused(problem)
    words = cutlist.load_words(words_path)
    results = locate(words, read_rows(tsv_path), min_s, max_s)
    text = worksheet(results)
    if text:
        paths.write_private(out, text)
    return results, report(results), out if text else None
