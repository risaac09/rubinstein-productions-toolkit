"""
rpresolve.trimreview: where an edit could be tightened, as a list to
review. Long silences come from the source audio, filler words and
repeats from the mlx_whisper word JSON (cutlist.load_words), and the rows
go to a TSV and, through workflows.trim_review_markers, onto an [auto]
timeline as markers. Nothing is cut, rippled or deleted, here or
downstream: the list proposes and Isaac trims. Stdlib only; numpy, when
present, only makes the silence envelope faster.

Kinds, colours (markers) and suggestions:
    silence    Blue    a run of 10 ms windows below the threshold at least
                       min_silence_s long (cutlist.silences): cut-candidate
                       from CUT_S, tighten from TIGHTEN_S, keep below
    filler     Yellow  um, uh, erm, er (high), hmm, mm, mhm, ah (medium):
                       cut-candidate; with soft=True also "like", "you
                       know" and "I mean" (low): keep
    repeat     Purple  a word or two said again back to back ("I I", "we
                       were we were"): tighten; an emphatic double ("very
                       very", "no no") is low and keep
    asr-loop   Red     a phrase Whisper wrote three times or more
                       (cutlist.repetition_loops): the transcript is wrong
                       there, so keep, re-transcribe, and read nothing into
                       the other rows inside it

Confidence of a silence: high when no word's midpoint falls inside it,
medium when one does (Whisper heard something quiet there) or when there
are no words to compare. Whisper's word times drift by about 0.2 s and it
leaves out many fillers, so a row says where to look; the cut itself is
made on the waveform, and a filler Whisper did not write is not listed.

Times: over a whole source, rows are in source seconds. Over a cut
manifest, each row names its clip and span, start and end are seconds on
that clip's timeline (spans laid end to end at the manifest's fps, as cut
builds them), and source_start and source_end keep the source time.

North register: the TSV is a new human-fed surface, fed once per episode
by the cut run. Its kill criterion is six unfed weeks: if six weeks pass
with no TSV read or acted on, it retires to markers only, with a one-line
note in resolve-template-spec.md.
"""

import re

from . import cutlist

SILENCE_DB = cutlist.SILENCE_DB
AUTO_MARGIN_DB = 10.0         # "auto": the quietest tenth of the windows plus this
AUTO_RANGE_DB = (-70.0, -30.0)
MIN_SILENCE_S = 0.8
TIGHTEN_S = 1.2
CUT_S = 2.5
DECODE_TIMEOUT_S = 900
ENVELOPE_BLOCK = 30000        # 10 ms windows per block: five minutes

FILLERS = {"um": "high", "umm": "high", "uh": "high", "uhh": "high", "uhm": "high",
           "erm": "high", "er": "high", "hmm": "medium", "hm": "medium", "mm": "medium",
           "mhm": "medium", "ah": "medium"}
SOFT_SINGLE = {"like"}
SOFT_PAIRS = {("you", "know"), ("i", "mean")}
EMPHATIC = {"very", "really", "so", "no", "yes", "yeah", "go", "ha", "bye", "okay", "ok",
            "hey", "wow", "right", "much", "many", "more"}

KEEP, TIGHTEN, CUT = "keep", "tighten", "cut-candidate"
SILENCE, FILLER, REPEAT, LOOP = "silence", "filler", "repeat", "asr-loop"
KINDS = (SILENCE, FILLER, REPEAT, LOOP)
COLORS = {SILENCE: "Blue", FILLER: "Yellow", REPEAT: "Purple", LOOP: "Red"}
COLUMNS = ("start", "end", "kind", "text", "confidence", "suggestion",
           "clip", "span", "source_start", "source_end")


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def envelope(source, t0, t1=None, ffmpeg=None):
    """[(time, dBFS)] per 10 ms of source[t0:t1] (t1 None: to the end), as
    cutlist.envelope computes it. With numpy the windows are computed a
    block at a time, so an hour of audio never sits in memory as floats;
    without it, cutlist's own loop runs (slower: tens of seconds an hour)."""
    try:
        import numpy as np
    except ImportError:
        return cutlist.envelope(source, t0, t1, ffmpeg, timeout=DECODE_TIMEOUT_S)
    t0 = max(0.0, t0)
    raw = cutlist.decode_pcm(source, t0, t1, ffmpeg, timeout=DECODE_TIMEOUT_S)
    x = np.frombuffer(raw, dtype="<i2")
    hop = int(cutlist.AUDIO_RATE * cutlist.HOP_S)
    n = len(x) // hop
    out = []
    for b0 in range(0, n, ENVELOPE_BLOCK):
        b1 = min(n, b0 + ENVELOPE_BLOCK)
        block = x[b0 * hop:b1 * hop].reshape(b1 - b0, hop).astype(np.float64)
        db = 20 * np.log10(np.sqrt((block * block).mean(axis=1)) / 32768.0 + 1e-9)
        out += [(t0 + (b0 + i) * cutlist.HOP_S, float(v)) for i, v in enumerate(db)]
    return out


def threshold(env, silence_db=SILENCE_DB):
    """The silence threshold in dBFS: a number as given, or "auto": the
    10th percentile of the windows plus AUTO_MARGIN_DB, kept within
    AUTO_RANGE_DB."""
    if silence_db != "auto":
        return float(silence_db)
    if not env:
        return SILENCE_DB
    dbs = sorted(d for _, d in env)
    floor = dbs[int(0.1 * (len(dbs) - 1))]
    return round(min(max(floor + AUTO_MARGIN_DB, AUTO_RANGE_DB[0]), AUTO_RANGE_DB[1]), 1)


# ---------------------------------------------------------------------------
# Rows over one range of the source (source seconds)
# ---------------------------------------------------------------------------

def _row(start, end, kind, text, confidence, suggestion):
    return {"start": round(start, 3), "end": round(end, 3), "kind": kind, "text": text,
            "confidence": confidence, "suggestion": suggestion}


def silence_rows(env, words, a, b, thr, min_s=MIN_SILENCE_S, tighten_s=TIGHTEN_S, cut_s=CUT_S):
    """Silence rows in [a, b) from an envelope already computed over it."""
    rows = []
    mids = [cutlist.midpoint(w) for w in words] if words is not None else None
    for s, e in cutlist.silences(env, thr, min_s):
        s, e = max(s, a), min(e, b)
        if e - s < min_s - 1e-9:
            continue
        length = e - s
        where = ("at the head of the range; " if s <= a + cutlist.HOP_S else
                 "at the tail of the range; " if e >= b - cutlist.HOP_S else "")
        heard = [w for w, m in zip(words, mids) if s <= m < e] if words is not None else []
        if words is None:
            conf, note = "medium", "no words to compare"
        elif heard:
            conf, note = "medium", "Whisper heard: " + " ".join(w["word"].strip()
                                                                for w in heard[:6])
        else:
            conf, note = "high", ""
        sug = CUT if length >= cut_s else TIGHTEN if length >= tighten_s else KEEP
        rows.append(_row(s, e, SILENCE, f"{where}{length:.2f} s below {thr:g} dBFS" +
                         (f"; {note}" if note else ""), conf, sug))
    return rows


def _tok(w):
    t = cutlist.tokens(w["word"])
    return t[0] if t else ""


def filler_rows(words, soft=False):
    """Filler rows from the words (source seconds)."""
    rows = []
    toks = [_tok(w) for w in words]
    i = 0
    while i < len(words):
        t = toks[i]
        whole = cutlist.tokens(words[i]["word"])
        if t in FILLERS and len(whole) == 1:
            rows.append(_row(words[i]["start"], words[i]["end"], FILLER, words[i]["word"].strip(),
                             FILLERS[t], CUT))
        elif soft and i + 1 < len(words) and (t, toks[i + 1]) in SOFT_PAIRS:
            rows.append(_row(words[i]["start"], words[i + 1]["end"], FILLER,
                             f"{words[i]['word'].strip()} {words[i + 1]['word'].strip()}",
                             "low", KEEP))
            i += 2
            continue
        elif soft and t in SOFT_SINGLE and len(whole) == 1:
            rows.append(_row(words[i]["start"], words[i]["end"], FILLER, words[i]["word"].strip(),
                             "low", KEEP))
        i += 1
    return rows


def _overlaps(a, b, spans):
    return any(a < e and b > s for s, e in spans)


def repeat_rows(words):
    """(repeat rows, asr-loop rows) from the words (source seconds)."""
    loops = [_row(s, e, LOOP, f"Whisper repeats \"{phrase} ...\"; re-transcribe before trusting "
                  "this stretch", "high", KEEP)
             for s, e, phrase in cutlist.repetition_loops(words)]
    taken = [(r["start"], r["end"]) for r in loops]
    rows = []
    for n in (1, 2):
        for s, e, phrase in cutlist.repetition_loops(words, n=n, repeats=2):
            first = phrase.split()[0] if phrase else ""
            if _overlaps(s, e, taken) or (n == 1 and first in FILLERS):
                continue
            said = " ".join(w["word"].strip() for w in words if s <= w["start"] and w["end"] <= e)
            emphatic = n == 1 and first in EMPHATIC
            rows.append(_row(s, e, REPEAT, said or phrase, "low" if emphatic else "medium",
                             KEEP if emphatic else TIGHTEN))
            taken.append((s, e))
    return rows, loops


def review_range(source, words, a, b=None, audio=True, silence_db=SILENCE_DB,
                 min_silence_s=MIN_SILENCE_S, tighten_s=TIGHTEN_S, cut_s=CUT_S, soft=False,
                 ffmpeg=None, env=None):
    """Rows for source[a:b] in source seconds, sorted by start. words is
    every word of the source (or None for audio only); the ones whose
    midpoint falls in [a, b) count. Returns (rows, threshold used)."""
    inside = None
    if words is not None:
        inside = [w for w in words if a <= cutlist.midpoint(w) and
                  (b is None or cutlist.midpoint(w) < b)]
    rows, thr = [], None
    if audio:
        env = env if env is not None else envelope(source, a, b, ffmpeg)
        thr = threshold(env, silence_db)
        end = b if b is not None else (env[-1][0] + cutlist.HOP_S if env else a)
        rows += silence_rows(env, inside, a, end, thr, min_silence_s, tighten_s, cut_s)
    if inside:
        rows += filler_rows(inside, soft)
        reps, loops = repeat_rows(inside)
        loop_spans = [(r["start"], r["end"]) for r in loops]
        rows += loops + reps
        rows = [r for r in rows if r["kind"] in (LOOP, SILENCE)
                or not _overlaps(r["start"], r["end"], loop_spans)]
    order = {k: i for i, k in enumerate(KINDS)}
    rows.sort(key=lambda r: (r["start"], order[r["kind"]]))
    return rows, thr


# ---------------------------------------------------------------------------
# A whole source, or a cut manifest
# ---------------------------------------------------------------------------

def review_source(source, words=None, ranges=None, **opts):
    """Rows over the whole source, or over `ranges` [(a, b)] of it, in
    source seconds with source_start and source_end filled in. Returns
    {rows, thresholds}."""
    rows, thresholds = [], []
    for a, b in ranges or [(0.0, None)]:
        got, thr = review_range(source, words, a, b, **opts)
        thresholds.append(thr)
        for r in got:
            rows.append({**r, "clip": "", "span": "", "source_start": r["start"],
                         "source_end": r["end"]})
    return {"rows": rows, "thresholds": thresholds}


def review_manifest(manifest, words=None, **opts):
    """Rows per clip and span of a cut manifest (cutlist.load_manifest):
    start and end on the clip's timeline, source_start and source_end in
    the source. Returns {rows, thresholds}."""
    words = words if words is not None else cutlist.load_words(manifest["words"])
    fps = manifest["fps"]
    rows, thresholds = [], []
    for clip in manifest["clips"]:
        offset = 0.0
        for i, s in enumerate(clip["spans"], 1):
            a = cutlist.frame(s["in"], fps) / float(fps)
            b = cutlist.frame(s["out"], fps) / float(fps)
            got, thr = review_range(manifest["source_path"], words, a, b, **opts)
            thresholds.append(thr)
            for r in got:
                rows.append({**r, "start": round(offset + r["start"] - a, 3),
                             "end": round(offset + r["end"] - a, 3), "clip": clip["name"],
                             "span": i, "source_start": r["start"], "source_end": r["end"]})
            offset += cutlist.frames(s, fps) / float(fps)
    return {"rows": rows, "thresholds": thresholds}


def is_manifest(path):
    """True when path reads as a cut manifest (JSON with clips and a
    source_path)."""
    if not path.lower().endswith(".json"):
        return False
    import json
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and "clips" in doc and "source_path" in doc


def counts(rows):
    """{kind: n} and {suggestion: n} over rows."""
    by_kind = {k: 0 for k in KINDS}
    by_sug = {KEEP: 0, TIGHTEN: 0, CUT: 0}
    for r in rows:
        by_kind[r["kind"]] += 1
        by_sug[r["suggestion"]] += 1
    return {"kind": by_kind, "suggestion": by_sug}


def summary(rows):
    c = counts(rows)
    return (f"trim-review: {len(rows)} row(s): " +
            ", ".join(f"{n} {k}" for k, n in c["kind"].items()) + "; suggestions " +
            ", ".join(f"{n} {k}" for k, n in c["suggestion"].items()) +
            ". Nothing is cut: the rows are proposals.")


def tsv(rows):
    """Rows as the review TSV: COLUMNS, one row per line."""
    def cell(v):
        return re.sub(r"[\t\r\n]+", " ", "" if v is None else str(v))
    lines = ["\t".join(COLUMNS)]
    lines += ["\t".join(cell(r.get(k, "")) for k in COLUMNS) for r in rows]
    return "\n".join(lines) + "\n"
