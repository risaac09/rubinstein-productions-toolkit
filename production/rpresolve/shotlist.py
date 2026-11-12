"""
rpresolve.shotlist: a shot list written in story order, compiled into the v2 manifest
that the draft farm (drafts) and assemble_timeline already read. Stdlib only, offline.

A shot list is JSON:

    {"fps": 24, "aspect": "9:16", "look": "<look id or none>", "end": "black:0.4",
     "music": {"path": "/abs/song.m4a", "in": 4.0},
     "shots": [{"name": "a", "source": "/abs/clip.mov", "in": 3.6, "beats": 4,
                "cx": [0.46, 0.46], "speed": 0.8, "source_fps": 30}]}

`end`, `fade_out` (seconds, 0 to 5: the last shot fades to black over its end), `music`,
`speed` and `source_fps` are optional. cx is the crop centre at the
shot's first and last frame as a share of the source width. A shot without `speed` takes
the conform speed: 0.8 for a source near 30 fps and 0.4 for one near 60 at a 24 fps list
(a slow-motion conform), else 1.

A cut plan gives each shot its timeline frames: `cut_frames` (one more than the shots, as
`notes snap` writes it) or one `period` in seconds for every shot. A span runs from `in`
for the shot's timeline length, as methods.to_manifest writes it, and the retime entry
carries the speed: that is the length the draft farm and assemble_timeline cut to, so the
cuts land on the music. (A retimed shot therefore reads `frames/fps * speed` of source.)
"""

import json
import math
import os

from . import methods

ID_MAIN = "shotlist"


class ShotlistError(ValueError):
    """The list or the plan is wrong; the message is one line."""


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ShotlistError(f"{path}: cannot read as JSON ({e})")


def conform_speed(fps, source_fps):
    """0.8 / 0.4 for a 30 / 60 fps source on a 24 fps list, else 1."""
    if abs(fps - 24) < 0.5 and _num(source_fps):  # 23.976 too
        for rate, speed in ((30, 0.8), (60, 0.4)):
            if abs(source_fps - rate) < 0.5:
                return speed
    return 1.0


def shot_speed(shot, fps):
    return float(shot["speed"]) if shot.get("speed") is not None \
        else conform_speed(fps, shot.get("source_fps"))


def validate(sl):
    """Every problem with a shot list as one line each; [] when it is sound."""
    if not isinstance(sl, dict):
        return ["a shot list is a JSON object"]
    p = []
    fps = sl.get("fps")
    if not _num(fps) or fps <= 0:
        p.append("fps must be a positive number")
    if sl.get("aspect") not in methods.ASPECTS:
        p.append(f"aspect must be one of {', '.join(methods.ASPECTS)}")
    look = sl.get("look")
    if look not in (None, "none") and not (
            isinstance(look, str) and methods.ID.match(look)
            and os.path.isfile(os.path.join(methods.LOOK_DIR, look + ".json"))):
        p.append(f"look {look!r} is not 'none' or a look in production/lutlab-looks")
    end = sl.get("end")
    if end is not None and not (isinstance(end, str) and methods.END.match(end)):
        p.append('end must be "black:S" (seconds of black after the last shot)')
    fade = sl.get("fade_out")
    if fade is not None and not (_num(fade) and 0 <= fade <= 5):
        p.append("fade_out must be seconds from 0 to 5 (the last shot fades to black)")
    music = sl.get("music")
    if music is not None:
        if not (isinstance(music, dict) and isinstance(music.get("path"), str)
                and os.path.isabs(music["path"])):
            p.append("music needs an absolute path")
        elif music.get("in") is not None and not (_num(music["in"]) and music["in"] >= 0):
            p.append("music.in must be seconds, 0 or more")
    shots = sl.get("shots")
    if not isinstance(shots, list) or not shots:
        return p + ["shots must be a non-empty list"]
    names = set()
    for i, s in enumerate(shots, 1):
        if not isinstance(s, dict):
            p.append(f"shot {i}: a shot is an object")
            continue
        who = f"shot {i} ({s.get('name')})"
        if not isinstance(s.get("name"), str) or not s["name"].strip():
            p.append(f"shot {i}: name must be a non-empty string")
        elif s["name"] in names:
            p.append(f"{who}: name is used twice")
        names.add(s.get("name"))
        if not isinstance(s.get("source"), str) or not os.path.isabs(s["source"]):
            p.append(f"{who}: source must be an absolute path")
        if not _num(s.get("in")) or s["in"] < 0:
            p.append(f"{who}: in must be seconds, 0 or more")
        b = s.get("beats")
        if not isinstance(b, int) or isinstance(b, bool) or b <= 0:
            p.append(f"{who}: beats must be a whole number above 0")
        cx = s.get("cx")
        if not (isinstance(cx, list) and len(cx) == 2 and all(_num(v) and 0 <= v <= 1 for v in cx)):
            p.append(f"{who}: cx must be [start, end], each a share of the width from 0 to 1")
        if s.get("speed") is not None and not (_num(s["speed"]) and s["speed"] > 0):
            p.append(f"{who}: speed must be a number above 0")
        if s.get("source_fps") is not None and not (_num(s["source_fps"]) and s["source_fps"] > 0):
            p.append(f"{who}: source_fps must be a number above 0")
    return p


def shot_frames(sl, cuts=None, period=None):
    """Timeline frames per shot, from a cut plan (dict) or a period in seconds."""
    n, fps = len(sl["shots"]), sl["fps"]
    if cuts is not None:
        cf = cuts.get("cut_frames") if isinstance(cuts, dict) else None
        if not (isinstance(cf, list) and all(isinstance(v, int) and not isinstance(v, bool)
                                             for v in cf)):
            raise ShotlistError("the cut plan has no cut_frames list of whole frames")
        if len(cf) != n + 1:
            raise ShotlistError(f"the cut plan has {len(cf)} cut_frames; {n} shots need {n + 1}")
        out = [b - a for a, b in zip(cf, cf[1:])]
        for s, f in zip(sl["shots"], out):
            if f <= 0:
                raise ShotlistError(f"shot {s['name']}: the cut plan gives it {f} frames")
        return out
    if not _num(period) or period <= 0:
        raise ShotlistError("give a cut plan (--cuts) or a --period in seconds above 0")
    f = int(round(period * fps))
    if f < 1:
        raise ShotlistError(f"a period of {period} s is under one frame at {fps} fps")
    return [f] * n


def method_for(sl, frames):
    """The method.json the draft farm reads beside the manifest (it takes fps, aspect,
    look and the end rule from it): one plain slot per shot."""
    fps, end = sl["fps"], sl.get("end")
    m = {"edit_method": 1, "id": ID_MAIN, "name": "Shot list", "summary": "compiled from a shot list",
         "fits": ["travel"], "aspect": sl["aspect"], "fps": fps,
         "length_s": 0, "bpm": None,
         "look": None if sl.get("look") in (None, "none") else sl["look"],
         "rules": {"reuse": True, "min_shot_s": 0.5 / fps, "snap_to_beat": False, "end": end},
         "slots": [{"role": "detail", "seconds": f / fps, "query": s["name"], "move": "static",
                    "speed": 1.0, "transition_out": {"type": "cut", "frames": 0},
                    "effect": None, "scale": None, "text": None}
                   for s, f in zip(sl["shots"], frames)]}
    m["length_s"] = sum(x["seconds"] for x in m["slots"]) + methods.end_black(m)
    return m


def compile_list(sl, frames, music_in=None):
    """(manifest, method) for a valid list and its frames per shot."""
    fps = sl["fps"]
    ids, sources, spans, effects = {}, [], [], []
    for i, (s, n) in enumerate(zip(sl["shots"], frames)):
        if s["source"] not in ids:
            ids[s["source"]] = f"s{len(ids) + 1}"
            sources.append({"id": ids[s["source"]], "path": s["source"]})
        span = {"source": ids[s["source"]], "in": round(s["in"], 6),
                "out": round(s["in"] + n / fps, 6)}
        if any(o["source"] == span["source"] and o["in"] < span["out"] and span["in"] < o["out"]
               for o in spans):
            span["reuse"] = True
        spans.append(span)
        a, b = s["cx"]
        fade = sl.get("fade_out") if i == len(frames) - 1 else None
        if a != b or fade:
            # pan_base is the centre vertical_apply puts in the item's own Pan (crops_for
            # writes the same rounded mean); the macro's X is 0.5 + pan_base - pan_*, so
            # the item sits on its crop centre at pan_from and moves to pan_to.
            pan = {"pan_from": round(a, 4), "pan_to": round(b, 4),
                   "pan_base": round((a + b) / 2, 4)}
            if fade:  # the last shot also fades to black; with a == b it is a plain fade
                pan["fade_out"] = fade
            effects.append({"span_index": i, "effect": "reframe-pan", "params": pan})
        speed = shot_speed(s, fps)
        if speed != 1:
            effects.append({"span_index": i, "effect": "retime", "params": {"speed": speed}})
    method = method_for(sl, frames)
    problems = methods.validate(method)
    if problems:
        raise ShotlistError("the shot list makes no sound method: " + "; ".join(problems))
    manifest = {"version": 2, "speech": False, "fps": fps, "sources": sources,
                "clips": [{"name": ID_MAIN, "spans": spans}], "effects": effects,
                "method": {"id": ID_MAIN, "aspect": sl["aspect"], "look": method["look"],
                           "end": sl.get("end"), "ui_steps": []}}
    music = sl.get("music")
    if music:
        mi = music.get("in")
        manifest["music"] = {"path": music["path"],
                             "in": mi if mi is not None else (music_in or 0)}
    return manifest, method


def crops_for(sl, manifest):
    """A 9x16 crops.json (cmd_vertical.load_crops) with each shot's static centre, the
    mean of its cx; None for a 16:9 list."""
    if sl["aspect"] != "9:16":
        return None
    spans = manifest["clips"][0]["spans"]
    items = [{"index": i + 1, "name": s["name"], "path": s["source"], "in": sp["in"],
              "out": sp["out"], "cx": round(sum(s["cx"]) / 2, 4), "zoom": 1.0,
              "speed": shot_speed(s, sl["fps"]), "how": "shotlist", "status": "hand"}
             for i, (s, sp) in enumerate(zip(sl["shots"], spans))]
    return {"aspect": "9x16", "reviewed": False, "items": items}
