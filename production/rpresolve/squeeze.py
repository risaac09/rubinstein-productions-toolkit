"""
rpresolve.squeeze: the anamorphic squeeze of a card's clips, recorded once by a person.

Most cameras record an anamorphic picture squeezed, with square pixels and nothing in the file
that says so; an in-camera desqueeze is for the monitor only. So the factor cannot be detected.
It is written down when the card is copied, and everything after reads it from there.

The record is JSON beside a copied card, named like offload.sh's own sidecars:

    <parent>/<CAM>-<id6>/                   the card copy
    <parent>/<CAM>-<id6>.card.json          offload.sh's sidecar: this folder is a card
    <parent>/<CAM>-<id6>.squeeze.json       {"default": 1, "clips": {"P1040114.MOV": 1.5}}

`default` covers every clip the `clips` map does not name (file names, not paths). A factor
of 1 is a spherical lens: recording it is the answer "not anamorphic", which is what clears
the question for a card. A `squeeze.json` inside any folder works the same way for footage
that is not a card copy.

When a card has a record (`<CAM>-<id6>.card.json`, see rpresolve/card.py and production/card-record.md)
the answers are read from it by clip id, with a legacy squeeze.json folded in; this module's own
reading of squeeze.json stays for footage that is not a card copy.

A Blackmagic RAW file is the exception: the camera writes its anamorphic setting into the
file (`anamorphic` = "1.5x"), so a tagged clip answers for itself and `answer` reads it when
nobody has recorded anything. An empty tag is no answer: a lens the camera was not told about
may still be anamorphic. Stdlib only; the tag is read with ffprobe.
"""

import json
import os
import re
import subprocess

INSIDE = "squeeze.json"
SUFFIX = ".squeeze.json"
CARD_SUFFIX = ".card.json"
MIN_FACTOR, MAX_FACTOR = 1.0, 2.5


class SqueezeError(ValueError):
    """A record that cannot be read or written; the message is one line."""


def factor_problem(f):
    """Why f is not a squeeze factor, or None."""
    if isinstance(f, bool) or not isinstance(f, (int, float)) or not MIN_FACTOR <= f <= MAX_FACTOR:
        return f"a squeeze factor is a number from {MIN_FACTOR:g} to {MAX_FACTOR:g} (1 is spherical)"
    return None


def _ancestors(path):
    d = os.path.dirname(os.path.abspath(path))
    while True:
        yield d
        up = os.path.dirname(d)
        if up == d:
            return
        d = up


def card_root(path):
    """The card copy a media file sits in (the nearest folder with an offload.sh
    `<folder>.card.json` beside it), or None."""
    for d in _ancestors(path):
        if os.path.isfile(d + CARD_SUFFIX):
            return d
    return None


def record_path(path):
    """The nearest record that covers a media file, or None: `<folder>.squeeze.json`
    beside a folder, or `squeeze.json` inside one, the closest folder first."""
    for d in _ancestors(path):
        for p in (d + SUFFIX, os.path.join(d, INSIDE)):
            if os.path.isfile(p):
                return p
    return None


def load(record):
    """The record at a path, checked: {"default": f or None, "clips": {name: f}}."""
    try:
        with open(record, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise SqueezeError(f"{record}: cannot read the squeeze record ({e})")
    if not isinstance(doc, dict) or not isinstance(doc.get("clips", {}), dict):
        raise SqueezeError(f"{record}: a squeeze record is {{default, clips}}")
    out = {"default": doc.get("default"), "clips": dict(doc.get("clips") or {})}
    for who, f in [("default", out["default"])] + sorted(out["clips"].items()):
        if not (who == "default" and f is None) and factor_problem(f):
            raise SqueezeError(f"{record}: {who}: {factor_problem(f)}")
    return out


def factor(path):
    """The recorded squeeze of a media file: a float (1.0 is spherical), or None when
    no record covers it."""
    record = record_path(path)
    if not record:
        return None
    doc = load(record)
    f = doc["clips"].get(os.path.basename(path), doc["default"])
    return None if f is None else float(f)


def file_tag(path, ffprobe="ffprobe"):
    """The squeeze a Blackmagic RAW file states for itself, or None (not BRAW, no tag,
    an empty tag, or ffprobe could not read it)."""
    if not path.lower().endswith(".braw"):
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format_tags=anamorphic",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*x?\s*", out or "", re.I)
    f = float(m.group(1)) if m else None
    return f if f is not None and not factor_problem(f) else None


def record_factor(path):
    """The squeeze the card record gives a clip of a copied card (answers.squeeze by clip id,
    a legacy squeeze.json folded in; a clip's in-card proxy takes its original's answer), or
    None: no record, or no answer."""
    root = card_root(path)
    if not root:
        return None
    from . import card  # lazy: card imports this module
    c = card.loaded(root)
    sq = c["answers"].get("squeeze") or {}
    clip, _ = card.clip_for(c, path)
    f = (sq.get("clips") or {}).get(clip["clip_id"], sq.get("default")) if clip else sq.get("default")
    if f is not None and factor_problem(f):
        raise SqueezeError(f"{root}: {factor_problem(f)}")
    return None if f is None else float(f)


def answer(path, ffprobe="ffprobe"):
    """(factor, where) for a media file: a person's record first ("record": the card record,
    else a squeeze.json beside or above the file), else the file's own tag ("file tag"),
    else (None, None)."""
    f = record_factor(path)
    if f is None:
        f = factor(path)
    if f is not None:
        return f, "record"
    f = file_tag(path, ffprobe)
    return (f, "file tag") if f is not None else (None, None)


def asks_about(path):
    """True for a clip of a copied card the squeeze question is asked about: not loose
    footage, and not an in-card camera proxy (its original is asked)."""
    root = card_root(path)
    if root is None:
        return False
    from . import card
    return not card.clip_for(card.loaded(root), path)[1]


def unanswered(path, ffprobe="ffprobe"):
    """True for a clip of a copied card with no answer, recorded or in the file: the
    question "is this anamorphic?" is still open for it."""
    return asks_about(path) and answer(path, ffprobe)[0] is None


def picture_aspect(path, ffprobe="ffprobe"):
    """Width over height of the picture ffmpeg decodes from a file (its frame cropping
    applied), or None when ffprobe cannot say."""
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:stream_side_data=crop_top,crop_bottom,crop_left,crop_right",
                              "-of", "json", path], capture_output=True, text=True, timeout=60).stdout
        st = json.loads(out)["streams"][0]
        crop = next((d for d in st.get("side_data_list") or [] if "crop_top" in d), {})
        w = int(st["width"]) - int(crop.get("crop_left", 0)) - int(crop.get("crop_right", 0))
        h = int(st["height"]) - int(crop.get("crop_top", 0)) - int(crop.get("crop_bottom", 0))
        return w / h if w > 0 and h > 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return None


def remaining(f, source_aspect, drawn_aspect, slack=0.03):
    """The stretch still owed to the file that is drawn (a proxy) for a source squeezed f:
    a proxy made desqueezed already has the real shape, so nothing is owed. None when no
    stretch is needed."""
    if not f or f == 1:
        return None
    if source_aspect and drawn_aspect:
        f = f * source_aspect / drawn_aspect
    return None if abs(f - 1) <= slack else f


def write(folder, default=None, clips=None):
    """Write `<folder>.squeeze.json` beside a folder, merged over what is there (a new
    default replaces the old; named clips are added or replaced). Returns its path."""
    folder = os.path.abspath(folder).rstrip(os.sep)
    if not os.path.isdir(folder):
        raise SqueezeError(f"{folder}: not a folder")
    record = folder + SUFFIX
    doc = load(record) if os.path.isfile(record) else {"default": None, "clips": {}}
    for who, f in ([("default", default)] if default is not None else []) + sorted((clips or {}).items()):
        if factor_problem(f):
            raise SqueezeError(f"{who}: {factor_problem(f)}")
    if default is not None:
        doc["default"] = default
    doc["clips"].update(clips or {})
    with open(record, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return record
