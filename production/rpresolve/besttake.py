"""
rpresolve.besttake: a proposal order for the takes of one answer. Offline;
ffmpeg and numpy, plus macOS Vision for faces when it is there. Nothing here
changes a file, and it never picks: the editor does.

Input: a folder of takes (video or audio, one level) or a manifest, a JSON
file {"takes": [{"file": "A.mov", "words": "A.json"}]} with paths relative to
the manifest and "words" optional. A folder's transcript for A.mov is
A.json in the folder (or in --words-dir): Whisper JSON with word timestamps,
the shape pacing and wordsnap read.

Per take, from the modules that already measure it:
    speech_ratio, pauses, pauses_per_min, longest_pause_s
                      the share of the take with speech in it, and the gaps
                      of pacing.PAUSE_MIN_S or more between speech runs.
                      Speech is a 0.1 s window (dxmetrics.frame_power)
                      SPEECH_ABOVE_FLOOR_DB over the take's own noise floor
    wpm, speaking_wpm, words
                      pacing.report on the transcript; empty without one
    integrated_lufs, wada_snr_db
                      dxmetrics.measure
    noise_floor_dbfs  the mean power of the quietest NOISE_PCT percent of
                      windows, in dBFS
    clipped_share     the share of windows that reach full scale
                      (CLIP_PEAK), dropouts excluded
    cuts, cuts_per_min, longest_shot_share
                      video only: shots.build, the in-camera cuts. A steady
                      take has none; a longest shot well under the take's
                      length is a jump
    face_share        video only: the share of frames, one every
                      FACE_EVERY_S up to FACE_MAX, with a face in them
                      (macOS Vision through rpresolve.vision). Left empty
                      where Vision cannot run, or with --no-faces

The order: RANKING below is the whole formula. Each metric becomes a score
from 0 to 1 by a straight ramp: 1 at "good", 0 at "bad", clipped, so it works
for higher-is-better and lower-is-better alike. A band ("good" a [low, high]
pair, "bad" a [low, high] pair outside it) scores 1 inside and ramps to 0 at
either bad end. A take's score is the weighted mean of the scores it has,
weights renormalised over the metrics it could be measured on. Change a
number in RANKING, or pass --formula FILE.json with the rows you want to
change ({"clipped_share": {"weight": 0}}); the formula that ran is in the
JSON beside the order. Ties keep the takes' name order.

The reason on each row quotes numbers: the two metrics that scored highest
and the two that scored lowest, with their values. It does not say what a
number means for the shot; a quiet room, a slow read or a clipped peak can be
the right take. A proposal list, not a verdict.

Run from production/:
    python3 -m rpresolve.besttake (FOLDER | MANIFEST.json) [--words-dir D]
        [--no-faces] [--formula FILE.json] [--json OUT.json] [--md OUT.md]

OUT files go outside any git tree.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

from . import audioreport as ar
from . import cutlist
from . import delivercheck as dc
from . import dxmetrics as dxm
from . import pacing, paths, shots, vision

SPEECH_ABOVE_FLOOR_DB = 12.0   # a window this far over the noise floor is speech
NOISE_PCT = 10                 # the quietest tenth of windows is the room
CLIP_PEAK = 0.999              # a window whose largest sample reaches this is clipped
MIN_SPREAD_DB = 20.0           # 95th percentile over the floor; less means no room tone to read
FACE_EVERY_S = 2.0
FACE_MAX = 30
MEDIA_EXT = (".mov", ".mp4", ".mkv", ".m4v", ".mxf", ".avi", ".wav", ".aif", ".aiff",
             ".flac", ".m4a", ".mp3", ".aac")

# metric: good, bad, weight. A single value for good and bad is a ramp; a
# [low, high] pair for each is a band. Weights are relative.
RANKING = {
    "speech_ratio":       {"good": 0.75, "bad": 0.35, "weight": 2.0},
    "pauses_per_min":     {"good": 4.0, "bad": 20.0, "weight": 1.0},
    "wpm":                {"good": [130, 170], "bad": [90, 220], "weight": 1.0},
    "noise_floor_dbfs":   {"good": -60.0, "bad": -40.0, "weight": 2.0},
    "wada_snr_db":        {"good": 30.0, "bad": 10.0, "weight": 1.0},
    "integrated_lufs":    {"good": [-24.0, -16.0], "bad": [-40.0, -8.0], "weight": 0.5},
    "clipped_share":      {"good": 0.0, "bad": 0.02, "weight": 3.0},
    "cuts_per_min":       {"good": 0.0, "bad": 6.0, "weight": 1.0},
    "face_share":         {"good": 0.95, "bad": 0.5, "weight": 1.0},
}
LABELS = {"speech_ratio": "speech share", "pauses_per_min": "pauses per min",
          "wpm": "wpm", "noise_floor_dbfs": "noise floor dBFS", "wada_snr_db": "SNR dB",
          "integrated_lufs": "LUFS", "clipped_share": "clipped share",
          "cuts_per_min": "cuts per min", "face_share": "face share"}


class BestTakeError(ValueError):
    """Input that gives no list of takes."""


def check_formula(table):
    """The table, or BestTakeError: known metrics, numeric ramps, weights >= 0."""
    for k, row in table.items():
        if k not in LABELS:
            raise BestTakeError(f"formula: unknown metric {k!r} (known: {', '.join(LABELS)})")
        g, b = row.get("good"), row.get("bad")
        if isinstance(g, list) != isinstance(b, list):
            raise BestTakeError(f"formula: {k} good and bad must both be numbers or both [low, high]")
        flat = (g + b) if isinstance(g, list) else [g, b]
        if len(flat) not in (2, 4) or not all(isinstance(v, (int, float)) for v in flat):
            raise BestTakeError(f"formula: {k} needs numbers for good and bad")
        if isinstance(g, list) and not (b[0] < g[0] <= g[1] < b[1]):
            raise BestTakeError(f"formula: {k} band must nest: bad low < good low <= good high < bad high")
        if not isinstance(g, list) and g == b:
            raise BestTakeError(f"formula: {k} good and bad are the same number")
        if not isinstance(row.get("weight"), (int, float)) or row["weight"] < 0:
            raise BestTakeError(f"formula: {k} weight must be a number, 0 or more")
    return table


def load_formula(path=None):
    """RANKING with the rows of a --formula JSON merged over it, field by field."""
    table = {k: dict(v) for k, v in RANKING.items()}
    if path:
        with open(path, encoding="utf-8") as f:
            for k, row in json.load(f).items():
                table.setdefault(k, {}).update(row)
    return check_formula(table)


def ramp(v, good, bad):
    """1 at good, 0 at bad, straight between, clipped."""
    return min(1.0, max(0.0, (v - bad) / (good - bad)))


def metric_score(v, row):
    g, b = row["good"], row["bad"]
    if isinstance(g, list):
        if v < g[0]:
            return ramp(v, g[0], b[0])
        return ramp(v, g[1], b[1]) if v > g[1] else 1.0
    return ramp(v, g, b)


def score_take(metrics, table):
    """({metric: score}, weighted mean or None) over the metrics the take has."""
    scores = {k: round(metric_score(metrics[k], r), 3) for k, r in table.items()
              if metrics.get(k) is not None}
    wsum = sum(table[k]["weight"] for k in scores)
    if not scores or wsum == 0:
        return scores, None
    return scores, round(sum(table[k]["weight"] * s for k, s in scores.items()) / wsum, 3)


def reason(metrics, scores):
    """Numbers only: the two highest- and two lowest-scoring metrics."""
    if not scores:
        return "no metric could be measured"
    order = sorted(scores, key=lambda k: (-scores[k], k))
    def q(k):
        return f"{LABELS[k]} {metrics[k]:g} (score {scores[k]:.2f})"
    hi, lo = order[:2], [k for k in reversed(order) if k not in order[:2]][:2]
    return "; ".join(["highest: " + ", ".join(q(k) for k in hi)] +
                     (["lowest: " + ", ".join(q(k) for k in lo)] if lo else []))


# ---------------------------------------------------------------------------
# Measuring a take
# ---------------------------------------------------------------------------

def _db(power):
    return 10 * math.log10(power) if power > 0 else None


def audio_numbers(fp):
    """Speech, pause, noise floor and clip numbers from dxmetrics.frame_power."""
    hop, power, peak = fp["hop_s"], fp["power"], fp["peak"]
    n = len(power)
    live = (power > 0) & ~fp["dead"]
    if not live.any():
        return {"duration_s": round(fp["samples"] / fp["sample_rate"], 3)}
    dur = fp["samples"] / fp["sample_rate"]
    quiet = np.sort(power[live])[:max(1, int(live.sum() * NOISE_PCT / 100))]
    floor = _db(float(quiet.mean()))
    db = 10 * np.log10(np.maximum(power, 1e-20))
    speech = live & (db > floor + SPEECH_ABOVE_FLOOR_DB)
    spread = float(np.percentile(db[live], 95)) - floor
    runs = pacing.speech_runs([{"start": i * hop, "end": (i + 1) * hop}
                               for i in np.flatnonzero(speech)])
    gaps = [b[0] - a[1] for a, b in zip(runs, runs[1:])]
    return {"duration_s": round(dur, 3),
            "speech_ratio": round(float(speech.sum()) * hop / dur, 3),
            "pauses": len(gaps), "pauses_per_min": round(len(gaps) / dur * 60, 1),
            "longest_pause_s": round(max(gaps), 2) if gaps else None,
            "noise_floor_dbfs": round(floor, 1), "level_spread_db": round(spread, 1),
            "clipped_share": round(float(((peak >= CLIP_PEAK) & ~fp["dead"]).sum()) / n, 4)}


def face_share(path, duration):
    """Share of sampled frames with a face, or None when Vision cannot run."""
    with tempfile.TemporaryDirectory(prefix="besttake-") as tmp:
        proc = subprocess.run(
            [dc.FFMPEG, "-v", "error", "-nostdin", "-i", dc._src(path), "-map", "0:v:0",
             "-vf", f"fps=1/{FACE_EVERY_S:g}", "-frames:v", str(FACE_MAX), "-q:v", "3",
             os.path.join(tmp, "f%03d.jpg")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=dc.READ_TIMEOUT_S)
        frames = sorted(os.path.join(tmp, f) for f in os.listdir(tmp))
        if proc.returncode != 0 or not frames:
            return None
        found = vision.face_boxes(frames)
        if found is None:
            return None
        return round(sum(1 for f in frames if found.get(f)) / len(frames), 3)


def extract_audio(path, out):
    """Decode the take's first audio stream once, to float WAV at its own rate and
    channels; every audio measure then reads this file, not the camera codec."""
    proc = subprocess.run([dc.FFMPEG, "-v", "error", "-nostdin", "-y", "-i", dc._src(path),
                           "-map", "0:a:0", "-vn", "-c:a", "pcm_f32le", "-rf64", "auto", out],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, timeout=dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise ar.ReportError(f"ffmpeg could not decode {path}: "
                             f"{proc.stderr.decode(errors='replace').strip()[-300:]}")


def measure_take(path, words_path=None, faces=True):
    """One take's row: {file, metrics, notes}. Raises ar.ReportError,
    dc.CheckError, OSError or ValueError for a file that cannot be read."""
    info = dc.probe(path)
    kinds = [s.get("codec_type") for s in info.get("streams", [])
             if not (s.get("disposition") or {}).get("attached_pic")]
    if "audio" not in kinds:
        raise ar.ReportError(f"{path} has no audio stream")
    with tempfile.TemporaryDirectory(prefix="besttake-") as tmp:
        wav = os.path.join(tmp, "audio.wav")
        extract_audio(path, wav)
        m, notes = audio_measures(wav)
    return video_measures(path, kinds, m, notes, words_path, faces)


def audio_measures(wav):
    """(metrics, notes) from the one decoded copy of a take's audio."""
    fp = dxm.frame_power(wav, 0, dxm.FRAME_S)
    m = audio_numbers(fp)
    notes = []
    if "speech_ratio" not in m:
        notes.append("every window is digital silence")
    else:
        if m["level_spread_db"] < MIN_SPREAD_DB:
            notes.append(f"level spread {m['level_spread_db']:g} dB: no room-tone stretch, so "
                         f"speech share and noise floor are unreliable")
        dx = dxm.measure(wav)
        m["integrated_lufs"], m["wada_snr_db"] = dx["integrated_lufs"], dx["wada_snr_db"]
    return m, notes


def video_measures(path, kinds, m, notes, words_path, faces):
    if words_path:
        try:
            words = cutlist.load_words(words_path)
            # a last word a few ms past the probed length is rounding, not a wrong file
            rep = pacing.report(words, max(m["duration_s"], words[-1]["end"]))
            m.update(words=rep["words"], wpm=rep["wpm"], speaking_wpm=rep["speaking_wpm"])
        except (OSError, ValueError) as e:
            notes.append(f"transcript not read: {e}")
    if "video" in kinds:
        sh = shots.build(path)
        m["cuts"] = len(sh["cuts"])
        m["cuts_per_min"] = round(len(sh["cuts"]) / m["duration_s"] * 60, 1)
        m["longest_shot_share"] = round(max(s["duration"] for s in sh["shots"]) / sh["duration_s"], 3)
        if faces:
            try:
                m["face_share"] = face_share(path, m["duration_s"])
            except (OSError, subprocess.SubprocessError):
                m["face_share"] = None   # the faces are optional: keep the take
            if m["face_share"] is None:
                notes.append("faces not measured (Vision unavailable)")
    return {"file": os.path.abspath(path), "metrics": m, "notes": notes}


# ---------------------------------------------------------------------------
# Input and ranking
# ---------------------------------------------------------------------------

def find_takes(source, words_dir=None):
    """[(file, transcript or None)] from a folder or a manifest."""
    if os.path.isdir(source):
        wdir = words_dir or source
        out = []
        for name in sorted(os.listdir(source)):
            if name.lower().endswith(MEDIA_EXT) and not name.startswith("."):
                w = os.path.join(wdir, os.path.splitext(name)[0] + ".json")
                out.append((os.path.join(source, name), w if os.path.isfile(w) else None))
        if not out:
            raise BestTakeError(f"no takes in {source} (looked for {', '.join(MEDIA_EXT)})")
        return out
    if not os.path.isfile(source):
        raise BestTakeError(f"{source} is neither a folder nor a manifest")
    base = os.path.dirname(os.path.abspath(source))
    with open(source, encoding="utf-8") as f:
        takes = json.load(f).get("takes")
    if not takes or not all(isinstance(t, dict) and t.get("file") for t in takes):
        raise BestTakeError(f'{source} needs {{"takes": [{{"file": ...}}]}}')
    def join(p):
        return os.path.join(base, p)   # an absolute p wins in os.path.join
    return [(join(t["file"]), join(t["words"]) if t.get("words") else None) for t in takes]


def build(source, words_dir=None, faces=True, formula=None):
    """{formula, takes (ranked), skipped}."""
    dc.check_tools(ffmpeg=True)
    table = formula or load_formula()
    rows, skipped = [], []
    for path, words in find_takes(source, words_dir):
        try:
            row = measure_take(path, words, faces)
        except (ar.ReportError, dc.CheckError, OSError, ValueError, subprocess.SubprocessError) as e:
            skipped.append({"file": os.path.abspath(path), "error": str(e)})
            continue
        row["scores"], row["score"] = score_take(row["metrics"], table)
        row["scored_on"] = len(row["scores"])
        row["reason"] = reason(row["metrics"], row["scores"])
        rows.append(row)
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["file"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"note": "A proposal order from the numbers below; the editor picks the take.",
            "formula": table,
            "params": {"speech_above_floor_db": SPEECH_ABOVE_FLOOR_DB, "noise_pct": NOISE_PCT,
                       "clip_peak": CLIP_PEAK, "pause_min_s": pacing.PAUSE_MIN_S,
                       "face_every_s": FACE_EVERY_S, "face_max": FACE_MAX},
            "takes": rows, "skipped": skipped}


COLUMNS = (("speech_ratio", "speech"), ("pauses", "pauses"), ("wpm", "wpm"),
           ("integrated_lufs", "LUFS"), ("noise_floor_dbfs", "floor dBFS"),
           ("wada_snr_db", "SNR dB"), ("clipped_share", "clipped"), ("cuts", "cuts"),
           ("face_share", "faces"))
# "n" is how many metrics the score is a mean of: takes with a transcript or video have more.


def markdown(rep):
    """The ranked list as a Markdown table, with the skipped takes below."""
    head = ["#", "take", "score", "n"] + [c for _, c in COLUMNS] + ["numbers"]
    lines = ["Proposal order from the numbers; not a pick. The editor chooses the take.", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rep["takes"]:
        cells = [r["rank"], os.path.basename(r["file"]),
                 "-" if r["score"] is None else f"{r['score']:.2f}", r["scored_on"]]
        cells += ["-" if r["metrics"].get(k) is None else f"{r['metrics'][k]:g}" for k, _ in COLUMNS]
        cells.append(r["reason"])
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    for s in rep["skipped"]:
        lines.append(f"\nSkipped {os.path.basename(s['file'])}: {s['error']}")
    for r in rep["takes"]:
        for n in r["notes"]:
            lines.append(f"\n{os.path.basename(r['file'])}: {n}")
    return "\n".join(lines) + "\n"


def add_arguments(p):
    p.add_argument("source", help="a folder of takes, or a manifest JSON {\"takes\": [{\"file\": ...}]}")
    p.add_argument("--words-dir", help="where TAKE.json transcripts live (default: the folder)")
    p.add_argument("--no-faces", action="store_true", help="skip the Vision face share")
    p.add_argument("--formula", help="JSON rows to change in RANKING (see the module docstring)")
    p.add_argument("--json", metavar="OUT", help="write the ranked list as JSON, outside any git tree")
    p.add_argument("--md", metavar="OUT", help="write the Markdown table, outside any git tree")
    return p


def run(args):
    try:
        for out in (args.json, args.md):
            problem = out and paths.out_problem(out, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
        rep = build(args.source, args.words_dir, not args.no_faces, load_formula(args.formula))
        text = markdown(rep)
        from resolve_survey import write_report  # atomic: temp file, then replace
        if args.json:
            write_report(args.json, json.dumps(rep, indent=2) + "\n")
        if args.md:
            write_report(args.md, text)
    except (dc.ToolMissing, dc.CheckError, OSError, ValueError) as e:
        print(f"besttake: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0 if rep["takes"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.besttake",
                                 description="A proposal order for the takes of one answer.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
