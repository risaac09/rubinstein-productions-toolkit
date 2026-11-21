"""
rpresolve.decon: take a finished video apart into an edit method, and score a rebuild
against it. Offline; reads the picture with ffmpeg and never writes to Resolve.

    recipe(report, video, ...)      a shot list from shots.build as an edit method
                                    (methods.validate passes on it)
    match_method(recipe, method)    the round trip: slot count and per-slot error in frames
    compare(ref, rebuild, ...)      cut-time error, rhythm and colour distance, one score

A recipe carries timing only: cuts, dissolves, dips, the black at the end, and the tempo when a
beat map is given. Role, move, query and fits are placeholders until shot description
exists, and the method's own `look` stays null.

The compare score is 0 to 100: the mean of six parts, each 0 to 1, where a reference
against itself scores 100. Cut recall and extra cuts (within CUT_TOL_FRAMES), shot length
at matched indexes, the difference in coefficient of variation, the distance between
the shot-length histograms, and the median colour distance (CIEDE2000 of each shot's
mean Lab, at DE_SCALE for zero).
"""

import math
import os
import statistics
import subprocess

from . import delivercheck as dc, methods, rhythm

CUT_TOL_FRAMES = 2
DE_SCALE = 20.0      # a median CIEDE2000 this large scores zero for colour
BLACK_L = 5.0        # a last shot whose mean L* sits below this is the method's end black
LAB_BOX = 96         # frames are read this small for colour; a mean needs no detail
CELL = 144           # sheet cell, square, each frame fitted inside it
PAIRS_PER_ROW = 4
GAP = 8


class DeconError(ValueError):
    pass


def _dims(video):
    info = dc.probe(video)
    v = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), None)
    if not v or not v.get("width") or not v.get("height"):
        raise DeconError(f"{video} has no video size")
    return int(v["width"]), int(v["height"])


def frame(video, t, box, dims=None):
    """The frame at t seconds, fitted inside box x box, as an HxWx3 uint8 array."""
    import numpy as np
    w, h = dims or _dims(video)
    s = min(box / w, box / h)
    ow, oh = max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.6f}",
                        "-i", dc._src(video), "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={ow}:{oh}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=dc.READ_TIMEOUT_S)
    if p.returncode != 0 or len(p.stdout) < ow * oh * 3:
        raise DeconError(f"no frame at {t:.3f} s in {video}: {p.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(p.stdout[:ow * oh * 3], dtype=np.uint8).reshape(oh, ow, 3)


def mean_lab(img):
    from . import color
    return color.rec709_to_lab(img.reshape(-1, 3) / 255.0).mean(axis=0)


def _mid(shot):
    return (shot["in"] + shot["out"]) / 2


def recipe(report, video, ident, name=None, fits=("motion",), beats_path=None):
    """An edit method from a shots.build report. Raises DeconError when it would not validate."""
    shots, fps = list(report["shots"]), report.get("fps")
    if not fps:
        raise DeconError(f"{video} has no frame rate")
    w, h = _dims(video)
    end = None
    if len(shots) > 1 and mean_lab(frame(video, _mid(shots[-1]), LAB_BOX, (w, h)))[0] < BLACK_L:
        end = f"black:{shots.pop()['duration']:g}"
    slots = []
    for i, s in enumerate(shots):
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        if nxt and nxt["into"] == "dissolve":
            out = {"type": "dissolve", "frames": max(1, round((nxt["blend_out"] - nxt["blend_in"]) * fps))}
        elif nxt and nxt["into"] == "dip":
            out = {"type": "dip", "frames": max(1, round((nxt["blend_out"] - nxt["blend_in"]) * fps))}
        else:
            out = {"type": "cut", "frames": 0}
        slots.append({"role": "place", "seconds": s["duration"],
                      "query": f"shot {s['shot']} of the reference, {s['in']:.2f} to {s['out']:.2f} s",
                      "move": "static", "speed": 1.0, "transition_out": out, "effect": None,
                      "scale": None, "text": None})
    durs = [s["seconds"] for s in slots]
    bpm, snap = None, False
    if beats_path:
        beats = rhythm.load_beats(beats_path)
        if len(beats) > 1:
            b = round(60 / statistics.median(y - x for x, y in zip(beats, beats[1:])), 2)
            bpm = [b, b]
        hit, chance = rhythm.on_beat(durs, beats, fps)[1]
        snap = hit >= 0.5 and hit > chance
    m = {"edit_method": 1, "id": ident, "name": name or f"Recipe from {os.path.basename(video)}",
         "summary": (f"Deconstructed from {os.path.basename(video)}: {len(slots)} shots, average "
                     f"{statistics.fmean(durs):.2f} s, cuts by {report.get('backend', 'scdet')}. Role, "
                     "move, query and fits are placeholders until shot description exists."),
         "fits": list(fits), "aspect": "9:16" if h > w else "16:9", "fps": fps,
         "length_s": round(sum(durs) + methods.end_black({"rules": {"end": end}}), 3),
         "bpm": bpm, "look": None,
         "rules": {"reuse": False, "min_shot_s": max(0.001, math.floor(min(durs) * 1000) / 1000),
                   "snap_to_beat": snap, "end": end},
         "slots": slots}
    problems = methods.validate(m)
    if problems:
        raise DeconError("the recipe does not validate: " + "; ".join(problems))
    return m


def match_method(rec, method):
    """{slots: [recipe, method], errors_frames, max_error_frames, ok}: the round trip, at
    the method's fps, within CUT_TOL_FRAMES per slot."""
    fps = method["fps"]
    err = [abs(a["seconds"] - b["seconds"]) * fps for a, b in zip(rec["slots"], method["slots"])]
    n = [len(rec["slots"]), len(method["slots"])]
    worst = max(err) if err else 0.0
    return {"slots": n, "errors_frames": [round(e, 2) for e in err], "max_error_frames": round(worst, 2),
            "ok": n[0] == n[1] and worst <= CUT_TOL_FRAMES + 1e-6}


def _bounds(report):
    return [s["in"] for s in report["shots"][1:]]


def _near(xs, t):
    return min((abs(x - t) for x in xs), default=math.inf)


def compare(ref, rb, ref_video, rb_video, sheet=None):
    """Score a rebuild's shots.build report against the reference's. Writes the side-by-side
    sheet (PNG) when sheet is a path."""
    import numpy as np
    from . import color
    fps = ref.get("fps") or rb.get("fps")
    if not fps:
        raise DeconError("neither file has a frame rate")
    rc, bc = _bounds(ref), _bounds(rb)
    err = [_near(bc, t) * fps for t in rc]
    tol = CUT_TOL_FRAMES + 1e-6
    recall = sum(e <= tol for e in err) / len(rc) if rc else 1.0
    extra = sum(_near(rc, t) * fps > tol for t in bc) / len(bc) if bc else 0.0
    d_ref = [s["duration"] for s in ref["shots"]]
    d_rb = [s["duration"] for s in rb["shots"]]
    st_ref, st_rb = rhythm.stats(d_ref, fps), rhythm.stats(d_rb, fps)
    h_ref = [c / len(d_ref) for _, c in rhythm.histogram(d_ref)]
    h_rb = [c / len(d_rb) for _, c in rhythm.histogram(d_rb)]
    hist = 0.5 * sum(abs(a - b) for a, b in zip(h_ref, h_rb))
    # the rebuild frame matched to each reference shot: same place in the cut, by share of length
    k = rb["duration_s"] / ref["duration_s"] if ref["duration_s"] else 1.0
    dims_ref, dims_rb = _dims(ref_video), _dims(rb_video)
    pairs = []
    for s in ref["shots"]:
        t = _mid(s) * k
        j = next((x for x in rb["shots"] if x["in"] <= t < x["out"]), rb["shots"][-1])
        pairs.append({"shot": s["shot"], "t": round(_mid(s), 3), "rebuild_shot": j["shot"],
                      "length_ratio": round(min(s["duration"], j["duration"]) / max(s["duration"], j["duration"]), 3),
                      "a": frame(ref_video, _mid(s), CELL, dims_ref), "b": frame(rb_video, t, CELL, dims_rb)})
    for p in pairs:
        p["delta_e"] = round(float(color.delta_e_2000(mean_lab(p["a"]), mean_lab(p["b"]))), 2)
    de = [p["delta_e"] for p in pairs]
    length = statistics.median(p["length_ratio"] for p in pairs)
    parts = {"cut_recall": recall, "no_extra_cuts": 1 - extra, "shot_length": length,
             "variation": max(0.0, 1 - abs(st_ref["cv"] - st_rb["cv"])), "histogram": 1 - hist,
             "colour": max(0.0, 1 - statistics.median(de) / DE_SCALE)}
    if sheet:
        _sheet(pairs, sheet, np)
    return {"reference": os.path.abspath(ref_video), "rebuild": os.path.abspath(rb_video), "fps": fps,
            "shots": [len(d_ref), len(d_rb)],
            "cut_error_frames": {"median": round(statistics.median(err), 2) if err else 0.0,
                                 "max": round(max(err), 2) if err else 0.0,
                                 "recall": round(recall, 3), "extra": round(extra, 3)},
            "rhythm": {"asl": [round(st_ref["asl"], 3), round(st_rb["asl"], 3)],
                       "cv": [round(st_ref["cv"], 3), round(st_rb["cv"], 3)],
                       "histogram_distance": round(hist, 3), "shot_length_ratio": round(length, 3)},
            "delta_e": {"median": round(statistics.median(de), 2), "max": round(max(de), 2)},
            "parts": {k2: round(v, 3) for k2, v in parts.items()},
            "score": round(100 * statistics.fmean(parts.values()), 1),
            "matched": [{k2: v for k2, v in p.items() if k2 not in ("a", "b")} for p in pairs]}


def _sheet(pairs, out, np):
    """Reference left, rebuild right, PAIRS_PER_ROW pairs to a row, as a PNG."""
    cols = min(PAIRS_PER_ROW, len(pairs))
    rows = math.ceil(len(pairs) / cols)
    W = cols * (2 * CELL + GAP) + GAP
    H = rows * (CELL + GAP) + GAP
    img = np.full((H, W, 3), 24, dtype=np.uint8)
    for n, p in enumerate(pairs):
        y0, x0 = GAP + (n // cols) * (CELL + GAP), GAP + (n % cols) * (2 * CELL + GAP)
        for i, f in enumerate((p["a"], p["b"])):
            fh, fw = f.shape[:2]
            y, x = y0 + (CELL - fh) // 2, x0 + i * CELL + (CELL - fw) // 2
            img[y:y + fh, x:x + fw] = f
    proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-i", "-", "-frames:v", "1", out],
                          input=img.tobytes(), capture_output=True)
    if proc.returncode != 0:
        raise DeconError(f"could not write {out}: {proc.stderr.decode(errors='replace')[-200:]}")


def summary_compare(r):
    c, rh = r["cut_error_frames"], r["rhythm"]
    return "\n".join([
        f"Score {r['score']} of 100 ({', '.join(f'{k} {v}' for k, v in r['parts'].items())})",
        f"Shots: reference {r['shots'][0]}, rebuild {r['shots'][1]}",
        f"Cut error: median {c['median']} frames, max {c['max']}; recall {c['recall']}, extra {c['extra']} "
        f"(within {CUT_TOL_FRAMES} frames)",
        f"Rhythm: ASL {rh['asl'][0]} vs {rh['asl'][1]} s, CV {rh['cv'][0]} vs {rh['cv'][1]}, "
        f"histogram distance {rh['histogram_distance']}, shot length at matched shots {rh['shot_length_ratio']}",
        f"Colour: CIEDE2000 median {r['delta_e']['median']}, max {r['delta_e']['max']}",
    ])
