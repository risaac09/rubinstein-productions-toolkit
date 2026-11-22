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

The compare score is 0 to 100: the mean of seven parts, each 0 to 1, where a reference
against itself scores 100. Cut recall and extra cuts (within CUT_TOL_FRAMES), shot length
at matched indexes, the difference in coefficient of variation, the distance between
the shot-length histograms, and the median colour distance (CIEDE2000 of each shot's
mean Lab, at DE_SCALE for zero). Five of the parts are timing only (TIMING_PARTS), so
a rebuild with the same cut times scores high whatever its picture; colour and a coarse 8 x 8
thumbnail difference are the only picture parts, and `picture_differs` says when either is low.
With audio in both, an eighth part, also timing only: 1 minus the difference of the two cuts'
on-beat shares, each on its own file's beat map (musicmap).
"""

import math
import os
import statistics
import subprocess

from . import audioreport as ar, beats, cutlist, decon_effects, decon_sound, delivercheck as dc, methods, musicmap, paths, rhythm

CUT_TOL_FRAMES = 2
DE_SCALE = 20.0      # a median CIEDE2000 this large scores zero for colour
BLACK_L = 5.0        # a last shot whose mean L* sits below this is the method's end black
LAB_BOX = 96         # frames are read this small for colour; a mean needs no detail
CELL = 144           # sheet cell, square, each frame fitted inside it
PIC_SCALE = 0.25     # a median thumbnail difference (0 to 1 of full scale) this large scores zero for picture
THUMB = 8            # picture is compared as an 8 x 8 grid of block means
TIMING_PARTS = ("cut_recall", "no_extra_cuts", "shot_length", "variation", "histogram")
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


def _thumb(img, np):
    """img as a THUMB x THUMB x 3 grid of block means, 0 to 1: where the light sits, not just how much."""
    ys = np.linspace(0, img.shape[0], THUMB + 1).astype(int)
    xs = np.linspace(0, img.shape[1], THUMB + 1).astype(int)
    return np.array([[img[ys[i]:max(ys[i + 1], ys[i] + 1), xs[j]:max(xs[j + 1], xs[j] + 1)].mean(axis=(0, 1))
                      for j in range(THUMB)] for i in range(THUMB)]) / 255.0


def mean_lab(img):
    from . import color
    return color.rec709_to_lab(img.reshape(-1, 3) / 255.0).mean(axis=0)


def _mid(shot, fps=None):
    """Where to read a shot: its middle, kept inside its real frames (a one-frame shot at its
    first frame; a seek past the last frame's pts returns nothing)."""
    t = (shot["in"] + shot["out"]) / 2
    return max(shot["in"], min(t, shot["out"] - 1.5 / fps)) if fps else t


def check_outputs(inputs, outputs):
    """Refuse an output that is, resolves to, or is a link to any input or another output, and
    a link at an output name. Raises DeconError."""
    ins = [p for p in inputs if p]
    seen = []
    for o in (x for x in outputs if x):
        if os.path.islink(o):
            raise DeconError(f"{o} is a symbolic link; name the real file")
        for other in ins + seen:
            same = os.path.realpath(o) == os.path.realpath(other)
            if not same and os.path.exists(o) and os.path.exists(other):
                same = os.path.samefile(o, other)
            if same:
                raise DeconError(f"{o} is the same file as {other}; refusing to overwrite it")
        seen.append(o)


def recipe(report, video, ident, name=None, fits=("motion",), beats_path=None, titles=None, found=None, sound=None):
    """An edit method from a shots.build report, with titles (decon_titles.detect) when given and sound (a decon_sound.build map) as its `sound` field. `found`
    (decon_effects.detect) puts its flashes, freezes and ramps on the slots, and gets a `placed` list back.
    Raises DeconError when it would not validate."""
    shots, fps = list(report["shots"]), report.get("fps")
    if not fps:
        raise DeconError(f"{video} has no frame rate")
    w, h = _dims(video)
    end = None
    if len(shots) > 1 and mean_lab(frame(video, _mid(shots[-1], fps), LAB_BOX, (w, h)))[0] < BLACK_L:
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
    if found is not None:
        found.update(decon_effects.place(slots, report["shots"], found))
    durs = [s["seconds"] for s in slots]
    bpm, snap = None, False
    if beats_path:
        beats = sorted(set(rhythm.load_beats(beats_path)))   # a repeated beat is one beat
        if len(beats) > 1:
            b = round(60 / statistics.median(y - x for x, y in zip(beats, beats[1:])), 2)
            bpm = [b, b]
        hit, chance = rhythm.on_beat(durs, beats, fps)[1]
        snap = hit >= 0.5 and hit > chance
    m = {"edit_method": 1, "id": ident, "name": name or f"Recipe {ident}",
         "summary": (f"Deconstructed from a reference video: {len(slots)} shots, average "
                     f"{statistics.fmean(durs):.2f} s, cuts by {report.get('backend', 'scdet')}. Role, "
                     "move, query and fits are placeholders until shot description exists."),
         "fits": list(fits), "aspect": "9:16" if h > w else "16:9", "fps": fps,
         "length_s": round(sum(durs) + methods.end_black({"rules": {"end": end}}), 3),
         "bpm": bpm, "look": None,
         "rules": {"reuse": False, "min_shot_s": max(0.001, math.floor(min(durs) * 1000) / 1000),
                   "snap_to_beat": snap, "end": end},
         "slots": slots}
    if titles is not None:
        m["titles"] = titles
    if sound:
        m["sound"] = decon_sound.for_recipe(sound, len(slots))
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


def compare(ref, rb, ref_video, rb_video, sheet=None, titles=None):
    """Score a rebuild's shots.build report against the reference's. Writes the side-by-side
    sheet (PNG) when sheet is a path. titles: (reference titles, rebuild titles) from
    decon_titles.detect, to score title timing."""
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
        t = max(0.0, min(_mid(s, fps) * k, rb["duration_s"] - 1.5 / fps))
        j = next((x for x in rb["shots"] if x["in"] <= t < x["out"]), rb["shots"][-1])
        pairs.append({"shot": s["shot"], "t": round(_mid(s, fps), 3), "rebuild_shot": j["shot"],
                      "length_ratio": round(min(s["duration"], j["duration"]) / max(s["duration"], j["duration"]), 3),
                      "a": frame(ref_video, _mid(s, fps), CELL, dims_ref), "b": frame(rb_video, t, CELL, dims_rb)})
    for p in pairs:
        p["picture"] = round(float(np.abs(_thumb(p["a"], np) - _thumb(p["b"], np)).mean()), 4)
        p["delta_e"] = round(float(color.delta_e_2000(mean_lab(p["a"]), mean_lab(p["b"]))), 2)
    de = [p["delta_e"] for p in pairs]
    length = statistics.median(p["length_ratio"] for p in pairs)
    parts = {"cut_recall": recall, "no_extra_cuts": 1 - extra, "shot_length": length,
             "variation": max(0.0, 1 - abs(st_ref["cv"] - st_rb["cv"])), "histogram": 1 - hist,
             "colour": max(0.0, 1 - statistics.median(de) / DE_SCALE),
             "picture": max(0.0, 1 - statistics.median(p["picture"] for p in pairs) / PIC_SCALE)}
    shares, skipped = _beat_shares(ref_video, rb_video, d_ref, d_rb, fps)
    if shares:
        parts["beats"] = max(0.0, 1 - abs(shares[0] - shares[1]))
    title_score = None
    if titles is not None:
        from . import decon_titles
        title_score = decon_titles.score(titles[0], titles[1], fps)
        part = title_score.pop("part")
        if part is not None:
            parts["titles"] = part
    snd, snd_skipped = _sound_parts(ref, rb, ref_video, rb_video)
    if snd is not None:
        parts["sound"] = snd
    if sheet:
        _sheet(pairs, sheet, np)
    return {"reference": os.path.basename(ref_video), "rebuild": os.path.basename(rb_video), "fps": fps,
            "timing_only_parts": list(TIMING_PARTS) + [k2 for k2 in ("beats", "titles", "sound") if k2 in parts],
            "picture_differs": bool(parts["colour"] < 0.5 or parts["picture"] < 0.5),
            "shots": [len(d_ref), len(d_rb)],
            "detector": {side: {k2: r.get(k2) for k2 in ("backend", "threshold_used", "low_contrast", "black_and_white")}
                         for side, r in (("reference", ref), ("rebuild", rb))},
            "cut_error_frames": {"median": round(statistics.median(err), 2) if err else 0.0,
                                 "max": round(max(err), 2) if err else 0.0,
                                 "recall": round(recall, 3), "extra": round(extra, 3)},
            "rhythm": {"asl": [round(st_ref["asl"], 3), round(st_rb["asl"], 3)],
                       "cv": [round(st_ref["cv"], 3), round(st_rb["cv"], 3)],
                       "histogram_distance": round(hist, 3), "shot_length_ratio": round(length, 3)},
            "delta_e": {"median": round(statistics.median(de), 2), "max": round(max(de), 2)},
            "parts": {k2: round(v, 3) for k2, v in parts.items()},
            "on_beat": {"share": shares, "skipped": skipped},
            "title_timing": title_score,
            "sound": {"part": parts.get("sound"), "skipped": snd_skipped},
            "score": round(100 * statistics.fmean(parts.values()), 1),
            "matched": [{k2: v for k2, v in p.items() if k2 not in ("a", "b")} for p in pairs]}


KEY_TOL_S = 0.5     # a detected cut this near a key step finds it (a step is read by eye from frames)


def key_problems(key):
    """Problems with a measured key: {"steps_s": [seconds], "titles": [{text, in_s, out_s}]}, titles optional."""
    if not isinstance(key, dict):
        return ["a key is an object"]
    p = [f"unknown field '{k}'" for k in sorted(set(key) - {"steps_s", "titles", "note"})]
    sec = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    st = key.get("steps_s")
    if not (isinstance(st, list) and all(sec(x) and x >= 0 for x in st)):
        p.append("steps_s must be a list of seconds")
    for j, t in enumerate(key.get("titles", []), 1):
        if not (isinstance(t, dict) and isinstance(t.get("text"), str) and t["text"].strip()
                and all(sec(t.get(k)) for k in ("in_s", "out_s")) and 0 <= t["in_s"] < t["out_s"]):
            p.append(f"title {j} must be {{text, in_s, out_s}} with 0 <= in_s < out_s")
    return p


def compare_key(ref, ref_video, key, titles=None):
    """Score a reference's shots.build report (and its detected titles, when given) against a measured key
    (key_problems says its shape). A step is found by a cut within KEY_TOL_S; a cut near no step is extra."""
    fps = ref.get("fps")
    if not fps:
        raise DeconError(f"{ref_video} has no frame rate")
    bad = key_problems(key)
    if bad:
        raise DeconError("key: " + "; ".join(bad))
    cuts, steps = _bounds(ref), sorted(key["steps_s"])
    tol = KEY_TOL_S + 1e-6
    err = [_near(cuts, t) for t in steps]
    found = [e for e in err if e <= tol]
    parts = {"step_recall": len(found) / len(steps) if steps else 1.0,
             "no_extra_cuts": 1 - (sum(_near(steps, t) > tol for t in cuts) / len(cuts) if cuts else 0.0)}
    title_score = None if titles is None else {"titles": [0, len(titles)], "matched": 0, "skipped": "the key has no titles"}
    if titles is not None and "titles" in key:
        from . import decon_titles
        title_score = decon_titles.score([dict(t, animation=None) for t in key["titles"]], titles, fps)
        part = title_score.pop("part")
        if part is not None:
            parts["titles"] = part
    return {"reference": os.path.basename(ref_video), "fps": fps, "steps": len(steps), "cuts": len(cuts),
            "found": len(found), "missed_s": [t for t, e in zip(steps, err) if e > tol],
            "extra_s": [round(t, 3) for t in cuts if _near(steps, t) > tol],
            "step_error_frames": {"median": round(statistics.median(found) * fps, 2) if found else None,
                                  "max": round(max(found) * fps, 2) if found else None},
            "title_timing": title_score, "parts": {k: round(v, 3) for k, v in parts.items()},
            "score": round(100 * statistics.fmean(parts.values()), 1)}


def summary_key(r):
    e = r["step_error_frames"]
    return "\n".join([
        f"Score {r['score']} of 100 against the key ({', '.join(f'{k} {v}' for k, v in r['parts'].items())})",
        f"Steps: {r['found']} of {r['steps']} found by a cut within {KEY_TOL_S} s (error median {e['median']} frames, "
        f"max {e['max']}); {len(r['extra_s'])} of {r['cuts']} cuts near no step",
        f"Missed steps (s): {', '.join(f'{t:g}' for t in r['missed_s']) or 'none'}",
        _title_line(r.get("title_timing")),
    ])


def _sound_parts(ref, rb, ref_video, rb_video):
    """(part, None), or (None, why) when a file has no audio or cannot be read. Both files'
    loudness and hit shares go through decon_sound.compare_part."""
    maps = []
    for r, v in ((ref, ref_video), (rb, rb_video)):
        try:
            maps.append(decon_sound.build(v, r, kinds=False))
        except (decon_sound.SoundError, ar.ReportError, cutlist.CutlistError, dc.CheckError) as e:
            return None, f"{os.path.basename(v)}: {e}"
    part = decon_sound.compare_part(*maps)
    return part, (None if part is not None else "silent audio or a file with no cuts")


def _beat_shares(ref_video, rb_video, d_ref, d_rb, fps):
    """([reference share, rebuild share], None) of cuts within 1 frame of a beat, each on its
    own file's beat map; (None, why) when a file has no audio or no beat map."""
    shares = []
    for v, d in ((ref_video, d_ref), (rb_video, d_rb)):
        try:
            mm = musicmap.build(v)
        except (musicmap.MusicMapError, beats.BeatsError, cutlist.CutlistError) as e:
            return None, f"{os.path.basename(v)}: {e}"
        if not mm["beats_s"]:
            return None, f"{os.path.basename(v)}: no beats found"
        shares.append(round(rhythm.on_beat(d, mm["beats_s"], fps)[1][0], 3))
    return shares, None


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
    # a fresh temp name (-y is safe on it), renamed onto out, so no existing file is written through
    with paths.atomic_path(out, suffix=".png") as tmp:
        proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
                               "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-i", "-", "-frames:v", "1",
                               dc._src(tmp)], input=img.tobytes(), capture_output=True)
        if proc.returncode != 0:
            raise DeconError(f"could not write {out}: {proc.stderr.decode(errors='replace')[-200:]}")


def _title_line(t):
    if t is None:
        return "Titles: not scored (pass --titles)"
    if t.get("skipped"):
        return f"Titles: not scored ({t['skipped']})"
    if not t["titles"][0] and not t["titles"][1]:
        return "Titles: none in either file"
    return (f"Titles: reference {t['titles'][0]}, rebuild {t['titles'][1]}, matched {t['matched']}; "
            f"in and out error median {t['median_frames']} frames, max {t['max_frames']}")


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
        (f"Sound: part {r['sound']['part']:.3f}" if r["sound"]["part"] is not None
         else f"Sound: not scored ({r['sound']['skipped']})"),
        (f"Beats: on-beat share {r['on_beat']['share'][0]} vs {r['on_beat']['share'][1]} (within 1 frame)"
         if r["on_beat"]["share"] else f"Beats: not scored ({r['on_beat']['skipped']})"),
        _title_line(r.get("title_timing")),
        f"Timing only: {', '.join(r['timing_only_parts'])}. The picture is read by colour and picture "
        "(a coarse 8 x 8 thumbnail per shot); this is not a proof the videos match.",
    ] + (["Picture differs: colour or picture is under 0.5, so the matching timing is not the same video."]
         if r["picture_differs"] else []))
