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
that is not a card copy. Stdlib only.
"""

import json
import os

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


def unanswered(path):
    """True for a clip of a copied card with no recorded answer: the question
    "is this anamorphic?" is still open for it."""
    return card_root(path) is not None and factor(path) is None


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
