"""
rpresolve.wordsnap: Whisper word times moved onto the speech edges in the
source audio. Offline: ffmpeg and numpy, never Resolve.

Whisper's word boundaries drift, and next to a pause they drift into it: the
word after a breath starts early, the word before it ends late. A cut made
on those times keeps dead air or clips a consonant. wordsnap reads the
source as a 10 ms RMS envelope (trimreview.envelope), calls a window speech
when it sits MARGIN_DB over the file's own floor (its FLOOR_PCT percentile
window, digital silence left out), and moves each word edge that touches a
pause onto the speech edge, never further than REACH_S:

    start in a pause     later, to where the speech begins (before the end)
    start in speech      earlier, to where that run of speech begins, when
                         it begins after the previous word's (snapped) end
    end in a pause       earlier, to where the speech stops (after the start)
    end in speech        later, to where that run of speech stops, when it
                         stops before the next word starts and no pause
                         of REACH_S or more lies inside the word

Pauses shorter than MIN_RUN_S inside speech (a stop consonant) count as
speech, and speech shorter than that (a click) as pause. An edge between
two words said without a pause has no edge in the envelope and stays where
Whisper put it. A word with no speech under it at all (a Whisper guess over
room tone) keeps its times. No overlap between words is introduced.

Output: the words JSON as given (segments[].words[], so cutlist.load_words
reads it unchanged) with every word's start and end replaced and a
`moved_ms` {"start", "end"} added (snapped minus Whisper, signed
milliseconds), plus a top-level `wordsnap` block that echoes the source,
parameters, floor, threshold and the move statistics. Segment times and
the text are left as they are.

Run from production/:
    python3 -m rpresolve.wordsnap WORDS.json SOURCE --out SNAPPED.json
        [--reach S] [--margin-db DB]

It proposes word edges; a cut is still checked on the waveform (endcheck).
"""

import argparse
import copy
import json
import os
import sys

import numpy as np

from . import cutlist, paths, trimreview

HOP_S = cutlist.HOP_S      # 10 ms windows, the envelope cutlist and trimreview use
FLOOR_PCT = 10             # the file's floor: this percentile of its windows
DIGITAL_SILENCE_DB = -120.0  # windows at or under this are left out of the floor
MARGIN_DB = 12.0           # speech: a window this far over the floor
REACH_S = 0.4              # the furthest an edge may move
MIN_RUN_S = cutlist.MIN_SILENCE_S  # shorter pauses inside speech, and blips, are ignored
EPS = 1e-6


class WordsnapError(ValueError):
    pass


# ---------------------------------------------------------------------------
# The envelope as speech runs
# ---------------------------------------------------------------------------

def runs(active):
    """(run_start, run_end) arrays: for every window, the first window of
    the run (speech or pause) holding it and the window after its last."""
    n = len(active)
    change = np.flatnonzero(active[1:] != active[:-1]) + 1
    bounds = np.r_[0, change, n]
    lengths = np.diff(bounds)
    return np.repeat(bounds[:-1], lengths), np.repeat(bounds[1:], lengths)


def speech_mask(db, margin_db=MARGIN_DB, min_run=MIN_RUN_S, hop=HOP_S):
    """(active, floor_db, threshold_db) for an array of window levels. A
    pause shorter than min_run inside speech (a stop consonant) counts as
    speech, then a speech run shorter than min_run (a click) as pause."""
    # digital silence (exact zeros: a muted track, a gated head) is no floor;
    # at -180 dB it would put every window of room tone over the threshold
    live = db[db > DIGITAL_SILENCE_DB]
    floor = float(np.percentile(live if len(live) else db, FLOOR_PCT))
    thr = floor + margin_db
    active = db > thr
    k = int(round(min_run / hop))
    for value in (False, True):
        rs, re_ = runs(active)
        short = (active == value) & (re_ - rs < k)
        if not value:
            short &= (rs > 0) & (re_ < len(active))
        active = np.where(short, not value, active)
    return active, floor, thr


def snap_words(words, active, reach=REACH_S, hop=HOP_S):
    """[(start, end)] snapped, for words [(start, end)] in time order over
    an envelope whose window i covers [i*hop, (i+1)*hop)."""
    n = len(active)
    if n == 0:
        return [(s, e) for s, e in words]
    rs, re_ = runs(active)
    out, prev_end = [], -np.inf
    for k, (s, e) in enumerate(words):
        next_start = words[k + 1][0] if k + 1 < len(words) else np.inf
        new_s, new_e = s, e
        i = int(np.floor(s / hop + EPS))          # the window starting at or holding s
        if 0 <= i < n:
            if not active[i]:
                t = re_[i] * hop                  # where this pause ends
                if re_[i] < n and t - s <= reach + EPS and t < e - EPS:
                    new_s = t
            else:
                t = rs[i] * hop                   # where this run of speech begins
                if s - t <= reach + EPS and t >= prev_end - EPS:
                    new_s = t
        j = int(np.ceil(e / hop - EPS)) - 1       # the window ending at or holding e
        if 0 <= j < n:
            if not active[j]:
                t = rs[j] * hop                   # where this pause begins
                if rs[j] > 0 and e - t <= reach + EPS and t > new_s + EPS:
                    new_e = t
            else:
                t = re_[j] * hop                  # where this run of speech stops
                # a pause of `reach` or more inside the word means this run is
                # the next, untranscribed sound, not the word's tail
                i0 = max(0, int(np.floor(new_s / hop + EPS)))
                gaps = (re_[i0:j] - rs[i0:j])[~active[i0:j]]
                if (t - e <= reach + EPS and t <= next_start + EPS
                        and (not len(gaps) or gaps.max() * hop < reach - EPS)):
                    new_e = t
        # an edge that did not move keeps Whisper's value exactly
        out.append((s if new_s == s else round(float(new_s), 3),
                    e if new_e == e else round(float(new_e), 3)))
        prev_end = new_e
    return out


# ---------------------------------------------------------------------------
# The words JSON
# ---------------------------------------------------------------------------

def _word_refs(data):
    """The word dicts of a Whisper JSON that cutlist.load_words would read,
    in time order."""
    refs = []
    for seg in data.get("segments") or []:
        for w in seg.get("words") or []:
            text = (w.get("word") or w.get("text") or "").strip()
            if text and w.get("start") is not None and w.get("end") is not None:
                refs.append(w)
    if not refs:
        raise WordsnapError("no word timestamps in the words JSON "
                            "(transcribe with word timestamps)")
    refs.sort(key=lambda w: (float(w["start"]), float(w["end"])))
    return refs


def _stats(moves):
    a = np.abs(np.array(moves, dtype=float)) if moves else np.zeros(0)
    if not len(a):
        return {"moved": 0, "median_abs_ms": 0, "p90_abs_ms": 0, "max_abs_ms": 0}
    return {"moved": int((a > 0).sum()), "median_abs_ms": int(round(np.median(a))),
            "p90_abs_ms": int(round(np.percentile(a, 90))), "max_abs_ms": int(round(a.max()))}


def snap(data, env, reach=REACH_S, margin_db=MARGIN_DB):
    """A copy of the Whisper JSON `data` with its words snapped against
    env [(time, dBFS)] per HOP_S window from the start of the audio, and
    the wordsnap block (without source paths)."""
    out = copy.deepcopy(data)
    refs = _word_refs(out)
    db = np.array([d for _, d in env], dtype=float)
    if not len(db):
        raise WordsnapError("the source has no audio to snap against")
    active, floor, thr = speech_mask(db, margin_db)
    raw = [(float(w["start"]), float(w["end"])) for w in refs]
    snapped = snap_words(raw, active, reach)
    starts, ends = [], []
    for w, (s, e), (ns, ne) in zip(refs, raw, snapped):
        ms = {"start": int(round((ns - s) * 1000)), "end": int(round((ne - e) * 1000))}
        w["start"], w["end"], w["moved_ms"] = ns, ne, ms
        starts.append(ms["start"])
        ends.append(ms["end"])
    out["wordsnap"] = {
        "hop_s": HOP_S, "floor_percentile": FLOOR_PCT, "digital_silence_db": DIGITAL_SILENCE_DB,
        "margin_db": margin_db, "min_run_s": MIN_RUN_S, "reach_s": reach, "floor_db": round(floor, 1),
        "threshold_db": round(thr, 1), "speech_share": round(float(active.mean()), 3),
        "words": len(refs), "starts": _stats(starts), "ends": _stats(ends)}
    return out


def build(words_path, source, reach=REACH_S, margin_db=MARGIN_DB, ffmpeg=None):
    """The snapped words JSON for a Whisper JSON and the audio it came from."""
    with open(words_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise WordsnapError(f"{words_path} is not a Whisper JSON object")
    if not os.path.isfile(source):
        raise WordsnapError(f"{source} is not a file")
    out = snap(data, trimreview.envelope(source, 0.0, None, ffmpeg), reach, margin_db)
    out["wordsnap"] = {"source": os.path.abspath(source),
                       "words_file": os.path.abspath(words_path), **out["wordsnap"]}
    return out


def write(report, path):
    """Write the snapped JSON; refused inside any git working tree."""
    problem = paths.out_problem(path, any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    from resolve_survey import write_report  # atomic: temp file, then replace
    write_report(path, json.dumps(report, indent=2) + "\n")
    return path


def summary(report):
    ws = report["wordsnap"]
    s, e = ws["starts"], ws["ends"]
    return (f"wordsnap: {ws['words']} word(s); {s['moved']} start(s) and {e['moved']} end(s) "
            f"moved; |start| median {s['median_abs_ms']} ms, max {s['max_abs_ms']} ms; "
            f"|end| median {e['median_abs_ms']} ms, max {e['max_abs_ms']} ms "
            f"(floor {ws['floor_db']} dBFS, speech over {ws['threshold_db']} dBFS, "
            f"reach {ws['reach_s']:g} s).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_arguments(p):
    p.add_argument("words", help="mlx_whisper JSON with word timestamps")
    p.add_argument("source", help="the media file the words were transcribed from")
    p.add_argument("--out", required=True, help="snapped JSON, outside any git tree")
    p.add_argument("--reach", type=float, default=REACH_S,
                   help=f"furthest an edge may move, seconds (default {REACH_S:g})")
    p.add_argument("--margin-db", type=float, default=MARGIN_DB,
                   help=f"speech: dB over the file's floor (default {MARGIN_DB:g})")
    return p


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        if os.path.realpath(args.out) in map(os.path.realpath, (args.words, args.source)):
            raise paths.OutputRefused(f"{args.out} is an input; the snapped words never replace it")
        report = build(args.words, args.source, args.reach, args.margin_db)
        write(report, args.out)
    except (WordsnapError, cutlist.CutlistError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(summary(report))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.wordsnap",
                                 description="Snap Whisper word times to the speech edges "
                                             "in the source audio.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
