"""
rpresolve.scene_colour: the colour axis of the per-shot scene model
(production/scene-model.md, "Colour (proposed)"). Pure: it reads what
perceive and detect already wrote and measures nothing new.

One block per shot, from a perceive packet (packet.json, plus targets.json
when perceive ran with --targets) and, optionally, detect's JSON table:

    cast_a, cast_b      Lab a*, b* of the near-neutral midtones
                        (perceive neutral_cast)
    skin_hue_deg        the largest face's median vectorscope angle (Cb, Cr),
                        degrees; skin_off_line_deg is that minus 123
                        (measure.skin_stats hue_deg, hue_off_skin_line_deg)
    skin_lab            that face's skin Lab (skin_stats lab)
    palette             up to 8 of {lab, hue_deg, share_pct}; hue_deg here is
                        the Lab hue angle h_ab = atan2(b*, a*), a different
                        wheel from the vectorscope's, so never compare a
                        palette hue with a skin hue (perceive.palette)
    tint_shift_deg      Lab hue of the shadows minus the highlights, wrapped
                        to (-180, 180] (perceive.band_colour, via targets.json)
    input_space         detect's input_color_space for the source file

A number the sources could not give is null, and `withheld` names the field
and why. A packet whose source carried a transfer_note (log, PQ, HLG,
linear) gets every picture number withheld: perceive read it as display
Rec.709, so its colours are misread.

The skin hue is reported, never pushed to a target. Harmony has no number
here: the one candidate found, the Matsuda hue templates as used in
Cohen-Or et al., "Color harmonization" (ACM TOG 25(3), 2006), is a model of
harmony, not a measured tolerance, and its sector-width table was not read.
So harmony is a review question in every block (`review`), and `taste`
lists the fields whose reading belongs to a person.

Distances, for comparing two shots:
    hue_arc_deg   the shortest arc between two hue angles, 0 to 180 degrees
    palette_distance  each palette entry against its nearest entry in the
                  other palette by CIEDE2000 (color.delta_e_2000: Sharma, Wu
                  and Dalal, Color Res. Appl. 30(1), 2005, kL = kC = kH = 1),
                  weighted by share; and the share-weighted hue arc between
                  those nearest pairs, chromatic pairs only. No threshold is
                  applied to either.
"""

import json
import os

import numpy as np

from . import color

PALETTE_MAX = 8
# Lab C* under which a palette entry's hue is noise; the same floor perceive
# uses for near-neutral pixels (perceive.NEUTRAL_CHROMA).
HUE_CHROMA_FLOOR = 8.0
TASTE = ("skin_hue_deg", "skin_off_line_deg", "palette", "tint_shift_deg")
REVIEW = ("Do the palette's colours sit together the way this scene wants (harmony, split toning)? "
          "No sourced tolerance exists, so this is a person's call.")
PICTURE = ("cast_a", "cast_b", "skin_hue_deg", "skin_off_line_deg", "skin_lab", "palette", "tint_shift_deg")


class SceneColourError(Exception):
    pass


def hue_arc_deg(h1, h2):
    """The shortest arc between two hue angles in degrees, 0 to 180."""
    d = abs(h1 - h2) % 360.0
    return min(d, 360.0 - d)


def palette_distance(pal, ref):
    """pal against ref, each a list of {lab, share_pct[, hue_deg, chroma]}:
    {de2000, hue_arc_deg}, share-weighted over pal's entries, each paired
    with its nearest ref entry by CIEDE2000. hue_arc_deg uses only pairs
    where both entries are chromatic, and is None when none are."""
    if not pal or not ref:
        return {"de2000": None, "hue_arc_deg": None}
    ref_lab = np.array([r["lab"] for r in ref], dtype=float)
    de_sum = arc_sum = share = arc_share = 0.0
    for p in pal:
        d = color.delta_e_2000(np.array(p["lab"], dtype=float)[None], ref_lab)
        near = ref[int(np.argmin(d))]
        s = p["share_pct"]
        de_sum += s * float(np.min(d))
        share += s
        if min(_chroma(p), _chroma(near)) >= HUE_CHROMA_FLOOR:
            arc_sum += s * hue_arc_deg(_hue(p), _hue(near))
            arc_share += s
    return {"de2000": round(de_sum / share, 2) if share else None,
            "hue_arc_deg": round(arc_sum / arc_share, 1) if arc_share else None}


def _chroma(entry):
    return entry["chroma"] if "chroma" in entry else float(color.chroma(np.array(entry["lab"], dtype=float)))


def _hue(entry):
    if "hue_deg" in entry:
        return entry["hue_deg"]
    return float(np.degrees(np.arctan2(entry["lab"][2], entry["lab"][1])) % 360)


def _largest_skin(faces):
    for f in faces if isinstance(faces, list) else []:
        if f.get("skin"):
            return f["skin"]
    return None


def block(cine, targets=None, detect_row=None):
    """The colour block for one shot. cine is packet.json's "cine" dict,
    targets targets.json's dict or None, detect_row detect's row for the
    same file or None."""
    out = dict.fromkeys(PICTURE + ("input_space",))
    withheld = {}
    source = cine.get("source") or {}
    if "transfer_note" in source:
        for k in PICTURE:
            withheld[k] = "the source is " + source["transfer_note"]
    else:
        cast = cine.get("neutral_cast")
        if cast:
            out["cast_a"], out["cast_b"] = cast["a"], cast["b"]
        else:
            withheld["cast_a"] = withheld["cast_b"] = "under 0.5 percent of the frame is near neutral"
        skin = _largest_skin(cine.get("faces"))
        if skin:
            out["skin_hue_deg"], out["skin_off_line_deg"] = skin["hue_deg"], skin["hue_off_skin_line_deg"]
            out["skin_lab"] = skin["lab"]
            if skin.get("warnings"):
                out["skin_warnings"] = skin["warnings"]
        else:
            faces = cine.get("faces")
            why = faces if isinstance(faces, str) else "no face found" if not faces else "no face yielded a skin sample"
            for k in ("skin_hue_deg", "skin_off_line_deg", "skin_lab"):
                withheld[k] = why
        out["palette"] = [{"lab": p["lab"], "hue_deg": p["hue_deg"], "share_pct": p["share_pct"]}
                          for p in (cine.get("palette") or [])[:PALETTE_MAX]]
        if targets is None:
            withheld["tint_shift_deg"] = "no targets.json beside the packet (run perceive with --targets)"
        elif targets.get("hue_shift_shadows_vs_highlights_deg") is None:
            withheld["tint_shift_deg"] = "the shadows or the highlights band is empty"
        else:
            out["tint_shift_deg"] = targets["hue_shift_shadows_vs_highlights_deg"]
    if detect_row is None:
        withheld["input_space"] = "no detect row for this file"
    elif not detect_row.get("input_color_space"):
        withheld["input_space"] = f"detect could not pin it ({detect_row.get('note') or 'review'})"
    else:
        out["input_space"] = detect_row["input_color_space"]
    out["withheld"] = withheld
    out["taste"] = list(TASTE)
    out["review"] = REVIEW
    return out


def between(a, b):
    """How shot b differs from shot a (two blocks): palette distance, skin
    hue arc and the cast delta. Numbers only; no tolerance is applied."""
    skin = (round(hue_arc_deg(a["skin_hue_deg"], b["skin_hue_deg"]), 1)
            if None not in (a["skin_hue_deg"], b["skin_hue_deg"]) else None)
    cast = ({"a": round(b["cast_a"] - a["cast_a"], 2), "b": round(b["cast_b"] - a["cast_b"], 2)}
            if None not in (a["cast_a"], b["cast_a"]) else None)
    return {"palette": palette_distance(b["palette"], a["palette"]), "skin_hue_arc_deg": skin,
            "cast_delta": cast}


def load_packet(folder):
    """(cine, targets or None) from a perceive packet folder."""
    try:
        with open(os.path.join(folder, "packet.json"), encoding="utf-8") as f:
            packet = json.load(f)
    except (OSError, ValueError) as e:
        raise SceneColourError(f"{folder}: no readable packet.json ({e})")
    schema = packet.get("schema") if isinstance(packet, dict) else None
    if not isinstance(schema, dict) or schema.get("name") != "cine.perceive" or "cine" not in packet:
        raise SceneColourError(f"{folder}: packet.json is not a cine.perceive packet")
    targets = None
    path = os.path.join(folder, "targets.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            targets = json.load(f)
    return packet["cine"], targets


def shots(folders, detect_rows=None):
    """Blocks for perceive packet folders in story order, each with its
    difference from the shot before."""
    if detect_rows is not None and not (isinstance(detect_rows, list) and all(
            isinstance(r, dict) and "path" in r for r in detect_rows)):
        raise SceneColourError("the detect table is not detect --json output (a list of rows with a path)")
    by_path = {os.path.realpath(r["path"]): r for r in detect_rows or []}
    out, prev = [], None
    for folder in folders:
        cine, targets = load_packet(folder)
        file = (cine.get("source") or {}).get("file")
        b = block(cine, targets, by_path.get(os.path.realpath(file)) if file else None)
        shot = {"packet": os.path.abspath(folder), "file": file, "t": (cine.get("source") or {}).get("t"),
                "colour": b}
        if prev is not None:
            shot["from_previous"] = between(prev, b)
        out.append(shot)
        prev = b
    return out
