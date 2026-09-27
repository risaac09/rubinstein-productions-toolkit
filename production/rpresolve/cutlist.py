"""
rpresolve.cutlist: the offline half of the edit tools. A cut manifest, the
checks every cut must pass, and select proposals. Stdlib only; nothing here
touches Resolve (cut.py does that).

A manifest is JSON:
    {"version": 1,
     "source_path": "/abs/source.mp4", "source_sha256": "...", "fps": 25,
     "words": "/abs/source.words.json",        mlx_whisper word-level JSON
     "approved_text": "/abs/essay.md",         what every clip may say
     "clips": [{"name": "twenty_60",
                "spans": [{"in": 2125.9, "out": 2176.9,
                           "end_words": "...", "source_text": "..."}],
                "reframe": {"face_x": 270}}]}
in and out are seconds in the source. A span keeps source time [in, out):
the out-point is where the next frame would start, so a cut covers
round(out * fps) - round(in * fps) frames.

The words JSON is the one ASR source of truth: end_words and source_text
are read from it, and endcheck checks every out-point against it.

Word timestamps say which words a span holds; they cannot place a cut.
Whisper's word boundaries drift by a couple of tenths of a second and often
touch (one word "ends" exactly where the next "starts"), so on Ep 002 the
word times put the "20%" out-point after "very quiet" while the audio shows
it in the middle of "being". So a word belongs to a span by its midpoint,
and the cut itself is checked against the audio.

Checks (endcheck):
    - end word: the last word whose midpoint falls before the out-point
      equals the end of end_words (case and punctuation ignored)
    - clean cut: the out-point sits in a silence of the source audio (every
      10 ms window below SILENCE_DB) at least MIN_SILENCE_S long, and at
      least EDGE_S from either edge of it; the check proposes the nearest
      clean out-point after the end word when it fails
    - approved text: the span's claim-bearing words sit inside the approved
      text (pass / review / fail; see PASS_COVERAGE and friends)
"""

import array
import ast
import csv
import difflib
import hashlib
import json
import math
import os
import re
import subprocess

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
AUDIO_RATE = 16000
HOP_S = 0.010
SILENCE_DB = -45.0
MIN_SILENCE_S = 0.030
EDGE_S = 0.010
SEARCH_S = 1.5
END_PAD_S = 0.030
# The approved-text gate has three verdicts, calibrated on Ep 002 (approved
# spans score 0.63-0.89 on content words, the lines the essay check dropped
# 0.00-0.33): fail below FAIL_COVERAGE or at a missing run of FAIL_RUN words;
# review below PASS_COVERAGE or at a missing run of REVIEW_RUN; pass otherwise.
# An essay is an edit of the speech, so review means a person reads the
# missing phrase; only fail blocks a cut.
PASS_COVERAGE = 0.80
FAIL_COVERAGE = 0.50
REVIEW_RUN = 3
FAIL_RUN = 6
# Words that carry no claim of their own: fillers, and the function words an
# edited essay freely swaps ("as if" for "like"). Dropped before the check.
FILLERS = {"um", "uh", "erm", "hmm", "mm", "mhm", "ah", "like", "know", "mean", "kind",
           "sort", "just", "really", "actually", "basically", "literally", "yeah", "okay", "ok",
           "right", "so", "well", "oh"}
STOPWORDS = {"a", "an", "the", "and", "or", "but", "if", "as", "of", "to", "in", "on", "at", "by",
             "for", "with", "from", "into", "about", "than", "then", "that", "this", "these",
             "those", "it", "it's", "its", "is", "are", "was", "were", "be", "been", "being",
             "am", "i", "i'm", "me", "my", "we", "we're", "our", "you", "you're", "your", "he",
             "she", "they", "they're", "them", "their", "there", "here", "what", "which", "who",
             "how", "when", "where", "why", "do", "does", "did", "have", "has", "had", "can",
             "could", "would", "should", "will", "not", "no", "yes", "all", "some", "any", "also",
             "very", "too", "more", "most", "much", "many", "one", "because", "whereas", "while",
             "now", "still", "even", "up", "out", "down", "over", "again", "that's", "there's",
             "don't", "didn't", "can't", "isn't", "i've", "i'd", "i'll", "let's", "get", "got"}

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


class CutlistError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def tokens(text):
    """Lower-case word tokens with curly quotes straightened and other
    punctuation dropped: "It's 20%." -> ["it's", "20"]."""
    text = (text or "").lower().replace("’", "'").replace("‘", "'")
    return _WORD.findall(text)


def stem(tok):
    """Fold simple English inflections so an edit's "kept" still meets the
    spoken "keeping": a crude suffix strip, not a lemmatizer."""
    for suf in ("ingly", "ing", "edly", "ed", "ly", "es", "s"):
        if len(tok) > len(suf) + 2 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def content_tokens(text):
    """Claim-bearing tokens of a text: '%' read as 'percent', fillers and
    function words dropped, inflections folded."""
    text = (text or "").replace("%", " percent ")
    return [stem(t) for t in tokens(text) if t not in FILLERS and t not in STOPWORDS]


def strip_frontmatter(text):
    """Markdown without a leading YAML frontmatter block."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def load_approved(path):
    with open(path, encoding="utf-8") as f:
        return content_tokens(strip_frontmatter(f.read()))


def approved_check(span_tokens, approved_tokens):
    """Whether a span's claim-bearing words sit inside the approved text.
    Both sides come from content_tokens. The span is anchored on its longest
    run shared with the approved text; the approved words within a window
    around that anchor (the span's length again on each side) form the local
    vocabulary. Returns {coverage, longest_unmatched_run, unmatched,
    verdict, ok}:
    coverage is the share of span words found in that vocabulary, and the
    unmatched run is the longest stretch of consecutive span words missing
    from it, which is how a line the essay never says shows up."""
    n = len(span_tokens)
    if n == 0:
        return {"coverage": 0.0, "longest_unmatched_run": 0, "unmatched": "",
                "verdict": "fail", "ok": False}
    sm = difflib.SequenceMatcher(None, span_tokens, approved_tokens, autojunk=False)
    anchor = sm.find_longest_match(0, n, 0, len(approved_tokens))
    lo = max(0, anchor.b - anchor.a - n)
    hi = min(len(approved_tokens), anchor.b + (n - anchor.a) + n)
    vocab = set(approved_tokens[lo:hi])
    matched = [t in vocab for t in span_tokens]
    coverage = sum(matched) / n
    run = best = best_end = 0
    for i, m in enumerate(matched):
        run = 0 if m else run + 1
        if run > best:
            best, best_end = run, i + 1
    unmatched = " ".join(span_tokens[best_end - best:best_end]) if best else ""
    if coverage < FAIL_COVERAGE or best >= FAIL_RUN:
        verdict = "fail"
    elif coverage < PASS_COVERAGE or best >= REVIEW_RUN:
        verdict = "review"
    else:
        verdict = "pass"
    return {"coverage": round(coverage, 3), "longest_unmatched_run": best,
            "unmatched": unmatched, "verdict": verdict, "ok": verdict != "fail"}


# ---------------------------------------------------------------------------
# Words (mlx_whisper word-level JSON)
# ---------------------------------------------------------------------------

def load_words(path):
    """[{start, end, word}] in source-time order from a Whisper JSON with
    word timestamps (segments[].words[])."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    words = []
    for seg in data.get("segments") or []:
        for w in seg.get("words") or []:
            text = (w.get("word") or w.get("text") or "").strip()
            if text and w.get("start") is not None and w.get("end") is not None:
                words.append({"start": float(w["start"]), "end": float(w["end"]), "word": text})
    if not words:
        raise CutlistError(f"no word timestamps in {path} (transcribe with word timestamps)")
    words.sort(key=lambda w: (w["start"], w["end"]))
    return words


def repetition_loops(words, n=3, repeats=3):
    """[(start, end, phrase)] where an n-word phrase repeats back to back at
    least `repeats` times: Whisper's hallucination loop. Spans overlapping
    one cannot be trusted to the words."""
    toks = [tokens(w["word"])[:1] or [""] for w in words]
    toks = [t[0] for t in toks]
    loops, i = [], 0
    while i + n * repeats <= len(toks):
        phrase = toks[i:i + n]
        k = 1
        while toks[i + k * n:i + (k + 1) * n] == phrase:
            k += 1
        if k >= repeats and any(phrase):
            end = i + k * n - 1
            loops.append((words[i]["start"], words[end]["end"], " ".join(phrase)))
            i = end + 1
        else:
            i += 1
    return loops


def midpoint(w):
    return (w["start"] + w["end"]) / 2


def words_between(words, a, b):
    """Words whose midpoint falls in [a, b)."""
    return [w for w in words if a <= midpoint(w) < b]


def span_text(words, a, b):
    return " ".join(w["word"] for w in words_between(words, a, b))


def end_words_for(words, out, count=3):
    """The last `count` words whose midpoint falls before `out`."""
    before = [w for w in words if midpoint(w) < out]
    return " ".join(w["word"] for w in before[-count:])


# ---------------------------------------------------------------------------
# Audio: where the silences are
# ---------------------------------------------------------------------------

def envelope(source, t0, t1, ffmpeg=None):
    """[(time, dBFS)] per 10 ms of mono 16 kHz audio from source[t0:t1]."""
    t0 = max(0.0, t0)
    cmd = [ffmpeg or FFMPEG, "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}",
           "-i", "file:" + source, "-vn", "-ac", "1", "-ar", str(AUDIO_RATE),
           "-f", "s16le", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except subprocess.TimeoutExpired:
        raise CutlistError(f"ffmpeg timed out reading audio at {t0:.2f}s")
    if proc.returncode != 0:
        raise CutlistError(f"ffmpeg could not read audio at {t0:.2f}s: "
                           f"{proc.stderr.decode(errors='replace').strip()[-160:]}")
    samples = array.array("h")
    samples.frombytes(proc.stdout[:len(proc.stdout) // 2 * 2])
    hop = int(AUDIO_RATE * HOP_S)
    out = []
    for i in range(0, len(samples) - hop + 1, hop):
        chunk = samples[i:i + hop]
        rms = math.sqrt(sum(x * x for x in chunk) / hop) / 32768.0
        out.append((t0 + i / AUDIO_RATE, 20 * math.log10(rms + 1e-9)))
    return out


def silences(env, threshold=SILENCE_DB, min_len=MIN_SILENCE_S):
    """[(start, end)] runs of windows below threshold, at least min_len long."""
    runs, start = [], None
    for t, db in env + [(env[-1][0] + HOP_S if env else 0.0, 0.0)]:
        if db < threshold and start is None:
            start = t
        elif db >= threshold and start is not None:
            if t - start >= min_len - 1e-9:
                runs.append((round(start, 3), round(t, 3)))
            start = None
    return runs


def cut_is_clean(runs, out, edge=EDGE_S):
    """The silence run holding out, when out is at least `edge` from both
    of its ends; else None."""
    for a, b in runs:
        if a + edge - 1e-9 <= out <= b - edge + 1e-9:
            return (a, b)
    return None


# A cut that ends on one of these has run into the next sentence.
DANGLING = {"and", "so", "but", "or", "the", "a", "an", "because", "which", "to", "of",
            "like", "um", "uh"}


def words_before(words, t, count=3):
    return " ".join(w["word"] for w in [w for w in words if midpoint(w) < t][-count:])


def nearest_clean(words, env, out):
    """The clean cuts closest to `out` on each side: {"before": {t, ends},
    "after": {t, ends}}, each None when the window has none. A clean cut is
    the middle of a silence run long enough for EDGE_S on both sides."""
    near = {"before": None, "after": None}
    for a, b in silences(env):
        c = round((a + b) / 2, 3)
        if b - a < 2 * EDGE_S:
            continue
        side = "before" if c <= out else "after"
        best = near[side]
        if best is None or abs(c - out) < abs(best["t"] - out):
            near[side] = {"t": c, "ends": words_before(words, c)}
    return near


def end_check(words, out, end_words, env=None):
    """Check one out-point. words decide the end word (by midpoint); env,
    the audio envelope around out, decides whether the cut is clean. With
    env None only the word check runs and the result says so. Returns
    {ok, reason, last, silence, suggest}."""
    before = [w for w in words if midpoint(w) < out]
    last = before[-1] if before else None
    want = tokens(end_words)
    result = {"ok": False, "reason": "", "last": last, "silence": None, "suggest": None,
              "nearest": None, "dangling": None, "audio_checked": env is not None}
    if last and tokens(last["word"])[-1:] and tokens(last["word"])[-1] in DANGLING:
        result["dangling"] = last["word"]
    if not want:
        result["reason"] = "no end_words recorded"
        return result
    got = tokens(" ".join(w["word"] for w in before[-len(want):]))
    if got[-len(want):] != want:
        result["reason"] = (f"last words before the out-point are \"{' '.join(got)}\", "
                            f"expected \"{' '.join(want)}\"")
    if env is not None:
        runs = silences(env)
        result["silence"] = cut_is_clean(runs, out)
        result["nearest"] = nearest_clean(words, env, out)
        if result["silence"] is None and not result["reason"]:
            db = min((abs(t - out), d) for t, d in env)[1] if env else None
            result["reason"] = (f"out-point is not in a silence (audio {db:.0f} dBFS there); "
                                "it would clip a word")
        # Suggest the earliest silence in the window whose midpoint passes
        # the word rule itself: the words before it end in end_words.
        for a, b in runs:
            c = round((a + b) / 2, 3)
            tail = tokens(" ".join(w["word"] for w in words if midpoint(w) < c)[-400:])
            if tail[-len(want):] == want:
                result["suggest"] = c
                break
    if result["dangling"] and not result["reason"]:
        result["reason"] = f"ends on \"{result['dangling']}\": the cut runs into the next sentence"
    result["ok"] = not result["reason"]
    return result


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    validate_manifest(m)
    return m


def validate_manifest(m):
    for key in ("source_path", "source_sha256", "fps", "words", "approved_text", "clips"):
        if key not in m:
            raise CutlistError(f"manifest has no '{key}'")
    names = set()
    for clip in m["clips"]:
        name = clip.get("name")
        if not name or name in names:
            raise CutlistError(f"clip name missing or repeated: {name!r}")
        names.add(name)
        prev_out = None
        for s in clip.get("spans") or []:
            if not (isinstance(s.get("in"), (int, float)) and isinstance(s.get("out"), (int, float))):
                raise CutlistError(f"{name}: span without numeric in/out: {s}")
            if s["out"] <= s["in"]:
                raise CutlistError(f"{name}: span ends before it starts: {s['in']}-{s['out']}")
            if prev_out is not None and s["in"] < prev_out:
                raise CutlistError(f"{name}: spans overlap or run backwards at {s['in']}")
            prev_out = s["out"]
        if not clip.get("spans"):
            raise CutlistError(f"{name}: no spans")
    return m


def frames(span, fps):
    """Frames a span covers: source frames [round(in*fps), round(out*fps))."""
    return round(span["out"] * fps) - round(span["in"] * fps)


def clip_frames(clip, fps):
    return sum(frames(s, fps) for s in clip["spans"])


# ---------------------------------------------------------------------------
# Importers: the three cut formats already in use
# ---------------------------------------------------------------------------

def spans_from_cuts_script(path, var="CUTS"):
    """{clip: [(in, out), ...]} from a Python file that assigns a literal
    dict to CUTS (the Ep 002 cut script). Parsed with ast; never executed."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == var for t in node.targets):
            value = ast.literal_eval(node.value)
            return {k: [tuple(map(float, s)) for s in v] for k, v in value.items()}
    raise CutlistError(f"no {var} = {{...}} assignment in {path}")


def parse_timecode(text, fps=None):
    """Seconds from SS.mmm, MM:SS(.mmm), HH:MM:SS(.mmm) or HH:MM:SS:FF (needs fps)."""
    parts = text.strip().split(":")
    try:
        if len(parts) == 4:
            if not fps:
                raise CutlistError(f"{text}: HH:MM:SS:FF needs fps")
            h, m, s, f = (int(p) for p in parts)
            return h * 3600 + m * 60 + s + f / fps
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    except ValueError:
        raise CutlistError(f"not a timecode: {text!r}")


def spans_from_vertcut_tsv(path, fps=None, offset=0.0):
    """{id: [(in, out)]} from a vertcut TSV (id, in, out, title); one
    continuous span per row."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row or row[0].startswith("#") or len(row) < 3:
                continue
            a = parse_timecode(row[1], fps) + offset
            b = parse_timecode(row[2], fps) + offset
            out.setdefault(row[0].strip(), []).append((a, b))
    return out


def spans_from_edl(path):
    """{source: [(start, end)]} from a video-use edl.json (ranges[])."""
    with open(path, encoding="utf-8") as f:
        edl = json.load(f)
    out = {}
    for r in edl.get("ranges") or []:
        out.setdefault(r["source"], []).append((float(r["start"]), float(r["end"])))
    return out


def build_manifest(spans_by_clip, source_path, fps, words_path, approved_path,
                   source_sha256=None, reframe=None, words=None):
    """A manifest from {clip: [(in, out)]}, filling end_words and source_text
    from the words JSON."""
    words = words if words is not None else load_words(words_path)
    clips = []
    for name, spans in spans_by_clip.items():
        clips.append({"name": name,
                      "spans": [{"in": a, "out": b, "end_words": end_words_for(words, b),
                                 "source_text": span_text(words, a, b)} for a, b in spans],
                      **({"reframe": dict(reframe)} if reframe else {})})
    m = {"version": 1, "source_path": source_path,
         "source_sha256": source_sha256 or sha256_file(source_path), "fps": fps,
         "words": words_path, "approved_text": approved_path, "clips": clips}
    return validate_manifest(m)


# ---------------------------------------------------------------------------
# endcheck and selects
# ---------------------------------------------------------------------------

def endcheck(manifest, words=None, approved=None, audio=True, ffmpeg=None):
    """Every span of every clip against the words, the source audio and the
    approved text. Returns [{clip, span, in, out, frames, end, text, ok}]."""
    words = words if words is not None else load_words(manifest["words"])
    approved = approved if approved is not None else load_approved(manifest["approved_text"])
    loops = repetition_loops(words)
    rows = []
    for clip in manifest["clips"]:
        for i, s in enumerate(clip["spans"], 1):
            bad = [lp for lp in loops if lp[0] < s["out"] + SEARCH_S and lp[1] > s["in"]]
            if bad:
                a0, b0, phrase = bad[0]
                rows.append({"clip": clip["name"], "span": i, "in": s["in"], "out": s["out"],
                             "frames": frames(s, manifest["fps"]), "end": None, "text": None,
                             "ok": False, "reason": f"transcript loops \"{phrase} ...\" "
                             f"at {a0:.1f}-{b0:.1f}s; re-transcribe before trusting this span"})
                continue
            env = (envelope(manifest["source_path"], s["out"] - SEARCH_S, s["out"] + SEARCH_S, ffmpeg)
                   if audio else None)
            end = end_check(words, s["out"], s.get("end_words", ""), env)
            text = approved_check(content_tokens(span_text(words, s["in"], s["out"])), approved)
            rows.append({"clip": clip["name"], "span": i, "in": s["in"], "out": s["out"],
                         "frames": frames(s, manifest["fps"]), "end": end, "text": text,
                         "ok": end["ok"] and text["ok"], "review": text["verdict"] == "review",
                         "reason": "; ".join(r for r in (end["reason"],
                                   "" if text["ok"] else "not inside the approved text") if r)})
    return rows


def sentences(words, gap=0.6):
    """Group words into sentences: a break after end punctuation, or at a
    silence longer than `gap` seconds."""
    out, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > gap or cur[-1]["word"].rstrip()[-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def selects(words, approved, min_s=20.0, max_s=65.0, pad=0.15):
    """Spans of consecutive sentences that sit inside the approved text and
    last between min_s and max_s seconds, best first, non-overlapping.
    Each: {in, out, seconds, coverage, end_words, text}."""
    sents = sentences(words)
    candidates = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            a, b = sents[i][0]["start"], sents[j][-1]["end"]
            dur = b - a
            if dur > max_s:
                break
            if dur < min_s:
                continue
            span_words = [w for s in sents[i:j + 1] for w in s]
            check = approved_check(content_tokens(" ".join(w["word"] for w in span_words)), approved)
            if check["verdict"] == "pass":
                nxt = sents[j + 1][0]["start"] if j + 1 < len(sents) else b + 1.0
                out = round(min(b + pad, (b + nxt) / 2), 2)
                candidates.append({"in": round(max(0.0, a - 0.05), 2), "out": out,
                                   "seconds": round(out - a, 1), "coverage": check["coverage"],
                                   "end_words": " ".join(w["word"] for w in span_words[-3:]),
                                   "text": " ".join(w["word"] for w in span_words)})
    candidates.sort(key=lambda c: (-c["coverage"], -c["seconds"]))
    chosen = []
    for c in candidates:
        if all(c["out"] <= k["in"] or c["in"] >= k["out"] for k in chosen):
            chosen.append(c)
    return sorted(chosen, key=lambda c: c["in"])
