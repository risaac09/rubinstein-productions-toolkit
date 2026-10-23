"""
rpresolve.pacing: how fast a cut talks, from its words and its captions.
Offline and stdlib only.

Shot lengths are the rhythm report's job (rpresolve.rhythm, VP-132): the
average shot length with its median and spread, the histogram, the trend.
For a cut manifest, `interchange --format edl` writes the EDL rhythm reads.
This report adds what rhythm does not read: the words.

    words          how many words the cut keeps
    wpm            words over the cut's whole length, per minute
    speaking_wpm   words over speech time only (articulation rate)
    pause_ratio    the share of the cut with no speech. Speech is the union
                   of word spans, gaps under PAUSE_MIN_S joined; lead-in and
                   tail count as pause
    pauses         gaps between words of PAUSE_MIN_S or more, and the longest
    captions       with --captions: cues, characters per second median, 90th
                   percentile (nearest rank) and maximum, and how many cues
                   run over captionlint's max_cps

Words: Whisper JSON with word timestamps (segments[].words[]), the shape
wordsnap reads and writes, through cutlist.load_words. With --manifest the
words are the source's (the manifest's own "words" file unless --words is
given; a clip over several sources reads each source's own file, each span
from its span's source): a word counts when its middle falls inside a span, and its times
move into record time with the span, spans laid end to end as cut lays
them, a split edit's sound moved with it. Without a manifest the words are
the cut's own, from 0 to --duration (default: the last word's end).

Run from production/:
    python3 -m rpresolve.pacing (--words W.json | --manifest M [--clip NAME])
        [--duration S] [--captions FILE.srt] [--json]

It reports and judges nothing: a slow passage can be the point.
"""

import argparse
import json
import math
import statistics
import sys

from . import captionlint, cutlist

PAUSE_MIN_S = 0.25  # a gap under 250 ms is articulation, not a pause (Goldman-Eisler 1968)


class PacingError(ValueError):
    """Input that gives no pacing to report."""


def cut_words(words, spans):
    """(words in record time, cut length in seconds) for a manifest clip's
    spans. A word counts when its middle falls in the span's sound, which a
    split edit moves as interchange does (audio_lead and audio_tail, the
    neighbour giving way); its edges are clipped to that sound. words is one
    list, or {source id: list} when the spans name their sources."""
    cutlist.check_split_sources(spans)
    out, rec = [], 0.0
    for i, s in enumerate(spans):
        lead = s.get("audio_lead", 0) - (spans[i - 1].get("audio_tail", 0) if i else 0)
        tail = s.get("audio_tail", 0) - (spans[i + 1].get("audio_lead", 0)
                                         if i + 1 < len(spans) else 0)
        a, b = float(s["in"]) - lead, float(s["out"]) + tail
        for w in (words[s.get("source")] if isinstance(words, dict) else words):
            if a <= (w["start"] + w["end"]) / 2 < b:
                out.append({"start": rec - lead + max(w["start"], a) - a,
                            "end": rec - lead + min(w["end"], b) - a, "word": w["word"]})
        rec += float(s["out"]) - float(s["in"])
    # Sound before the first frame or past the last (a lead on the first span,
    # a tail on the last) is not in the cut: clip to it.
    out = [dict(w, start=max(w["start"], 0.0), end=min(w["end"], rec)) for w in out
           if w["end"] > 0 and w["start"] < rec]
    return out, rec


def speech_runs(words, pause_min=PAUSE_MIN_S):
    """[[start, end]] of speech: word spans with gaps under pause_min joined."""
    runs = []
    for w in sorted(words, key=lambda w: w["start"]):
        if runs and w["start"] - runs[-1][1] < pause_min:
            runs[-1][1] = max(runs[-1][1], w["end"])
        else:
            runs.append([w["start"], w["end"]])
    return runs


def caption_speeds(cues, max_cps=captionlint.THRESHOLDS["max_cps"]):
    """{cues, cps_median, cps_p90, cps_max, over} for [(start, end, text)];
    a cue with no length (captionlint's parse row) is left out."""
    cps = sorted(captionlint.measure(t)[1] / (b - a) for a, b, t in cues if b > a)
    if not cps:
        return {"cues": len(cps), "cps_median": None, "cps_p90": None, "cps_max": None,
                "over": 0, "max_cps": max_cps}
    return {"cues": len(cps), "cps_median": round(statistics.median(cps), 1),
            "cps_p90": round(cps[math.ceil(0.9 * len(cps)) - 1], 1),
            "cps_max": round(cps[-1], 1), "over": sum(1 for c in cps if c > max_cps + 1e-9),
            "max_cps": max_cps}


def report(words, duration, cues=None):
    """The pacing numbers for words in cut time over a cut `duration` long."""
    if duration <= 0:
        raise PacingError("the cut has no length")
    if words and max(w["end"] for w in words) > duration + 1e-6:
        raise PacingError(f"a word ends at {max(w['end'] for w in words):.2f}s, past the cut's "
                          f"{duration:.2f}s; give --duration")
    runs = speech_runs(words)
    speech = sum(b - a for a, b in runs)
    gaps = [b[0] - a[1] for a, b in zip(runs, runs[1:])]
    out = {"duration_s": round(duration, 3), "words": len(words),
           "wpm": round(len(words) / duration * 60, 1),
           "speaking_wpm": round(len(words) / speech * 60, 1) if speech else None,
           "pause_ratio": round(1 - speech / duration, 3),
           "pauses": len(gaps), "longest_pause_s": round(max(gaps), 2) if gaps else None,
           "pause_min_s": PAUSE_MIN_S}
    if cues is not None:
        out["captions"] = caption_speeds(cues)
    return out


def format_report(rep):
    lines = [f"pacing: {rep['words']} words in {rep['duration_s']:g}s: {rep['wpm']:g} wpm, "
             f"{rep['speaking_wpm'] if rep['speaking_wpm'] is not None else '-'} wpm while "
             f"speaking",
             f"  pause ratio {rep['pause_ratio']:.1%}; {rep['pauses']} pause(s) of "
             f"{rep['pause_min_s']:g}s or more between words"
             + (f", longest {rep['longest_pause_s']:g}s" if rep["longest_pause_s"] else "")]
    c = rep.get("captions")
    if c:
        lines.append(f"  captions: {c['cues']} cue(s), cps median {c['cps_median']}, p90 "
                     f"{c['cps_p90']}, max {c['cps_max']}; {c['over']} over {c['max_cps']:g}")
    return "\n".join(lines) + "\n"


def _clip(manifest, name):
    clips = manifest["clips"]
    if name is None and len(clips) == 1:
        return clips[0]
    found = [c for c in clips if c["name"] == name]
    if not found:
        raise PacingError(("the manifest holds more than one clip" if name is None else
                           f"no clip named {name!r} in the manifest") +
                          f"; name one with --clip ({', '.join(c['name'] for c in clips)})")
    return found[0]


def build(words_path=None, manifest_path=None, clip=None, duration=None, captions_path=None):
    if manifest_path:
        if duration is not None:
            raise PacingError("--duration is the manifest's spans; leave it out")
        m = cutlist.load_manifest(manifest_path, allow_no_speech=True)
        c = _clip(m, clip)
        used = {cutlist.source_for(m, s)["id"]: cutlist.source_for(m, s) for s in c["spans"]}
        if words_path and len(used) > 1:
            raise PacingError(f"--words is one file and the clip plays from {len(used)} sources; "
                              "leave it out to read each source's own words")
        for s in used.values():
            if not (words_path or s.get("words")):
                raise PacingError(f"the manifest names no words file for source {s['id']}; "
                                  "give --words")
        by_span = {s["id"]: cutlist.load_words(words_path or s["words"]) for s in used.values()}
        words, length = cut_words({s.get("source"): by_span[cutlist.source_for(m, s)["id"]]
                                   for s in c["spans"]}, c["spans"])
    elif words_path:
        words = cutlist.load_words(words_path)
        length = duration if duration is not None else max(w["end"] for w in words)
    else:
        raise PacingError("give --words or --manifest")
    cues = captionlint.load(captions_path)["cues"] if captions_path else None
    return report(words, length, cues)


def add_arguments(p):
    p.add_argument("--words", help="Whisper JSON with word timestamps (wordsnap's shape)")
    p.add_argument("--manifest", help="a cut manifest (rpresolve/cutlist.py)")
    p.add_argument("--clip", help="the manifest clip (needed when it holds more than one)")
    p.add_argument("--duration", type=float, help="the cut's length in seconds, without a manifest")
    p.add_argument("--captions", help="the cut's .srt or TTML, for caption reading speed")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    return p


def run(args):
    try:
        rep = build(args.words, args.manifest, args.clip, args.duration, args.captions)
    except (OSError, ValueError, captionlint.captions.CaptionError) as e:  # CutlistError too
        print(f"pacing: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(rep, indent=2) + "\n" if args.json else format_report(rep))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.pacing",
                                 description="How fast a cut talks: words and captions.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
