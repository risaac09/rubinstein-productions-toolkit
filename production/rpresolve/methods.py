"""
rpresolve.methods: edit methods, reusable social edit recipes (production/edit-methods.md).

A method is a JSON file in production/edit-methods/: a timed list of slots, each a role
to fill from a shoot, with its move, transition, effect and scale. This module loads and
validates methods and turns a method plus the clips chosen for its slots into a version 2
cut manifest that interchange writes as FCPXML and import_timeline lands in Resolve; the
manifest's `effects` list says which Fusion macro apply_effect puts on which span.

    load(id_or_path)        a method by id or path, validated (ValueError if not)
    validate(m)             the list of problems ([] when the method is sound)
    list_methods()          every method in production/edit-methods/, by id
    to_manifest(m, fills)   fills: one {source_path, in, out} per slot, in seconds

Stdlib only.
"""

import json
import math
import os
import re

from .cmd_effect import WIRING as MACROS

METHOD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "edit-methods")
LOOK_DIR = os.path.join(os.path.dirname(METHOD_DIR), "lutlab-looks")
FITS = ("travel", "event", "outdoor", "people", "food", "night", "motion", "portrait", "nature")
ASPECTS = ("16:9", "9:16")
MOVES = ("push-in", "static", "tilt-up", "pan", "handheld", "forward")
TRANSITIONS = ("cut", "dissolve", "dip", "wipe")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
END = re.compile(r"^black:(\d+(?:\.\d+)?)$")

TOP = {"edit_method", "id", "name", "summary", "fits", "aspect", "fps", "length_s", "bpm",
       "look", "rules", "slots"}
RULES = {"reuse", "min_shot_s", "snap_to_beat", "end"}
SLOT = {"role", "seconds", "query", "move", "speed", "transition_out", "effect", "scale", "text"}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def end_black(m):
    """Seconds of black after the last slot (rules.end "black:S"), else 0."""
    e = (m.get("rules") or {}).get("end")
    match = END.match(e) if isinstance(e, str) else None
    return float(match.group(1)) if match else 0.0


def validate(m):
    """Every way m departs from the method format, as sentences; [] when it is sound."""
    if not isinstance(m, dict):
        return ["a method is a JSON object"]
    p = []
    p += [f"unknown field '{k}'" for k in sorted(set(m) - TOP)]
    p += [f"missing field '{k}'" for k in sorted(TOP - set(m))]
    if m.get("edit_method") != 1:
        p.append("edit_method must be 1")
    if not isinstance(m.get("id"), str) or not ID.match(m.get("id") or ""):
        p.append("id must be lowercase words joined by hyphens")
    for k in ("name", "summary"):
        if not isinstance(m.get(k), str) or not m.get(k).strip():
            p.append(f"{k} must be a non-empty string")
    fits = m.get("fits")
    if not isinstance(fits, list) or not fits or len(set(map(str, fits))) != len(fits):
        p.append("fits must be a non-empty list without repeats")
    else:
        p += [f"fits tag '{t}' is not one of {', '.join(FITS)}" for t in fits if t not in FITS]
    if m.get("aspect") not in ASPECTS:
        p.append(f"aspect must be one of {', '.join(ASPECTS)}")
    fps = m.get("fps")
    if not _num(fps) or fps <= 0:
        p.append("fps must be a positive number")
        fps = None
    bpm = m.get("bpm")
    if bpm is not None and not (isinstance(bpm, list) and len(bpm) == 2 and all(map(_num, bpm))
                                and 0 < bpm[0] <= bpm[1]):
        p.append("bpm must be null or [low, high] with 0 < low <= high")
    look = m.get("look")
    if look is not None and not (isinstance(look, str) and ID.match(look)
                                 and os.path.isfile(os.path.join(LOOK_DIR, look + ".json"))):
        p.append("look must be null or a look in production/lutlab-looks (its file name, "
                 "such as print-teal)")
    rules = m.get("rules")
    min_shot = 0.0
    if not isinstance(rules, dict):
        p.append("rules must be an object")
    else:
        p += [f"unknown rule '{k}'" for k in sorted(set(rules) - RULES)]
        p += [f"missing rule '{k}'" for k in sorted(RULES - set(rules))]
        for k in ("reuse", "snap_to_beat"):
            if not isinstance(rules.get(k), bool):
                p.append(f"rules.{k} must be true or false")
        if not _num(rules.get("min_shot_s")) or rules.get("min_shot_s") <= 0:
            p.append("rules.min_shot_s must be a positive number")
        else:
            min_shot = rules["min_shot_s"]
        e = rules.get("end")
        if e is not None and not (isinstance(e, str) and END.match(e)):
            p.append('rules.end must be null or "black:S" (seconds of black after the last slot)')
    slots = m.get("slots")
    if not isinstance(slots, list) or not slots:
        return p + ["slots must be a non-empty list"]
    for i, s in enumerate(slots, 1):
        p += [f"slot {i}: {x}" for x in _slot_problems(s, min_shot, i == len(slots))]
    if not p and fps:
        total = sum(s["seconds"] for s in slots) + end_black(m)
        if not _num(m.get("length_s")) or abs(total - m["length_s"]) > 1.0 / fps:
            p.append(f"length_s is {m.get('length_s')}, but the slots and the end black add "
                     f"up to {total:.3f}")
    return p


def _slot_problems(s, min_shot, last):
    if not isinstance(s, dict):
        return ["a slot is an object"]
    p = [f"unknown field '{k}'" for k in sorted(set(s) - SLOT)]
    p += [f"missing field '{k}'" for k in sorted(SLOT - set(s))]
    for k in ("role", "query"):
        if not isinstance(s.get(k), str) or not s.get(k).strip():
            p.append(f"{k} must be a non-empty string")
    if not _num(s.get("seconds")) or s.get("seconds") < min_shot:
        p.append(f"seconds must be a number of at least rules.min_shot_s ({min_shot})")
    if s.get("move") not in MOVES:
        p.append(f"move must be one of {', '.join(MOVES)}")
    if not _num(s.get("speed")) or s.get("speed") <= 0:
        p.append("speed must be a positive number")
    t = s.get("transition_out")
    if not isinstance(t, dict) or set(t) != {"type", "frames"}:
        p.append("transition_out must be {type, frames}")
    elif t["type"] not in TRANSITIONS:
        p.append(f"transition_out.type must be one of {', '.join(TRANSITIONS)}")
    elif not isinstance(t["frames"], int) or isinstance(t["frames"], bool) or t["frames"] < 0:
        p.append("transition_out.frames must be a whole number, 0 or more")
    elif (t["type"] == "cut") != (t["frames"] == 0):
        p.append("a cut has 0 frames and any other transition at least 1")
    elif last and t["type"] != "cut":
        p.append("the last slot ends the method, so its transition_out is a cut "
                 "(rules.end says what follows)")
    if s.get("effect") is not None and s.get("effect") not in MACROS:
        p.append(f"effect must be null or a macro id ({', '.join(sorted(MACROS))})")
    sc = s.get("scale")
    if sc is not None and not (isinstance(sc, list) and len(sc) == 2 and all(map(_num, sc))
                               and all(0.1 <= x <= 10 for x in sc)):
        p.append("scale must be null or [start, end], each 0.1 to 10")
    if s.get("text") not in (None, "word"):
        p.append('text must be null or "word"')
    return p


def load(id_or_path):
    """A method by id (production/edit-methods/<id>.json) or by path, validated."""
    path = id_or_path
    if ID.match(id_or_path) and not os.path.exists(id_or_path):
        path = os.path.join(METHOD_DIR, id_or_path + ".json")
    if not os.path.isfile(path):
        raise ValueError(f"no edit method '{id_or_path}'; the methods are "
                         f"{', '.join(m['id'] for m in list_methods())}")
    with open(path, encoding="utf-8") as f:
        try:
            m = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not JSON ({e})")
    problems = validate(m)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return m


def list_methods():
    """Every method in production/edit-methods/, validated, sorted by id."""
    return [load(os.path.join(METHOD_DIR, n)) for n in sorted(os.listdir(METHOD_DIR))
            if n.endswith(".json")]


def to_manifest(method, fills):
    """A version 2 cut manifest (speech false) for one clip named after the method: one
    span per slot, in slot order. Each span starts at its fill's `in` and runs the slot's
    seconds (the method's timing, not the fill's), so a fill must hold that much. The
    join into a span carries the previous slot's dissolve; a static scale (start equal to
    end) is written as the span's scale. Added beside the manifest's own fields:
    `effects` [{span_index, effect}] for apply_effect (span_index from 0, the timeline's
    item number less one), and `method` {id, end, hand_steps}, the parts no manifest
    field holds (dips, wipes, speed changes, animated scales, words, the end black).
    Raises ValueError on a fill that is too short, or on two fills that overlap in one
    source when the method's rules.reuse is false."""
    problems = validate(method)
    if problems:
        raise ValueError("the method is not sound: " + "; ".join(problems))
    slots = method["slots"]
    if not isinstance(fills, list) or len(fills) != len(slots):
        raise ValueError(f"{method['id']} has {len(slots)} slots; give one fill per slot")
    fps = method["fps"]
    ids, sources, spans, effects, steps = {}, [], [], [], []
    for i, (slot, fill) in enumerate(zip(slots, fills)):
        where = f"slot {i + 1} ({slot['role']})"
        if not isinstance(fill, dict) or not isinstance(fill.get("source_path"), str) \
                or not os.path.isabs(fill["source_path"]) \
                or not _num(fill.get("in")) or not _num(fill.get("out")):
            raise ValueError(f"{where}: a fill is {{source_path (absolute), in, out}}")
        if fill["in"] < 0 or fill["out"] - fill["in"] < slot["seconds"] - 0.5 / fps:
            raise ValueError(f"{where}: the fill holds {fill['out'] - fill['in']:.3f} s; "
                             f"the slot needs {slot['seconds']} s")
        path = fill["source_path"]
        if path not in ids:
            ids[path] = f"s{len(ids) + 1}"
            sources.append({"id": ids[path], "path": path})
        span = {"source": ids[path], "in": round(fill["in"], 6),
                "out": round(fill["in"] + slot["seconds"], 6)}
        prev = slots[i - 1]["transition_out"] if i else None
        if prev and prev["type"] == "dissolve":
            span["transition"] = {"type": "dissolve", "frames": prev["frames"]}
        elif prev and prev["type"] != "cut":
            steps.append(f"span {i}: a {prev['frames']}-frame {prev['type']} into it from span "
                         f"{i - 1} (cut in the manifest; FCPXML carries dissolves only)")
        sc = slot["scale"]
        if sc and sc[0] == sc[1] and sc[0] != 1:
            span["scale"] = sc[0]
        elif sc and sc[0] != sc[1] and slot["effect"] not in ("zoom-push", "scale-window"):
            steps.append(f"span {i}: scale {sc[0]} to {sc[1]} (no macro on the span animates it)")
        if slot["speed"] != 1:
            steps.append(f"span {i}: retime to {slot['speed'] * 100:g} percent")
        if slot["text"] == "word":
            steps.append(f"span {i}: set the word")
        for j, other in enumerate(spans):
            if other["source"] == span["source"] and other["in"] < span["out"] \
                    and span["in"] < other["out"]:
                if not method["rules"]["reuse"]:
                    raise ValueError(f"{where}: its fill overlaps slot {j + 1}'s in the same "
                                     "source, and the method does not reuse footage")
                span["reuse"] = True
        spans.append(span)
        if slot["effect"]:
            effects.append({"span_index": i, "effect": slot["effect"]})
    return {"version": 2, "speech": False, "fps": fps, "sources": sources,
            "clips": [{"name": method["id"], "spans": spans}],
            "effects": effects,
            "method": {"id": method["id"], "end": method["rules"]["end"], "hand_steps": steps}}
