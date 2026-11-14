"""Card records as the index reads them: a clip's id from the record beside its card folder.

A card folder is the nearest ancestor with a `<folder>.card.json` beside it (whatever the folder is called). The id is
`<CAM>-<first 6 of card_id>/<stem>`, from the toolkit's `rpresolve.card.load` when it can be imported (its `clips` list is
the authority), else from the record's own `cam` and `card_id`. Nothing here writes."""
import functools
import json
import os

RECORD = ".card.json"


@functools.lru_cache(maxsize=4096)
def card_folder(directory):
    """The card folder at or above this directory, or None. Cached per directory: a card's clips share their ancestors."""
    d = directory
    while True:
        if os.path.isfile(d + RECORD):
            return d
        up = os.path.dirname(d)
        if up == d or not up:
            return None
        d = up


def _toolkit_load(folder):
    from rpresolve import card
    c = card.load(folder)
    return {x["relpath"]: x["clip_id"] for x in c["clips"]}, c["record"]


@functools.lru_cache(maxsize=1024)
def _record(folder):
    """({relpath: clip_id}, name prefix or None) for a card folder."""
    try:
        clips, rec = _toolkit_load(folder)
    except Exception:  # no toolkit, or a record it rejects: read the two fields the id needs
        clips, rec = {}, None
        try:
            with open(folder + RECORD, encoding="utf-8") as f:
                rec = json.load(f)
            clips = {x["relpath"]: x["clip_id"] for x in rec.get("clips") or [] if isinstance(rec.get("schema"), dict)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    name = None
    if isinstance(rec, dict) and isinstance(rec.get("cam"), str) and isinstance(rec.get("card_id"), str):
        name = f"{rec['cam']}-{rec['card_id'][:6]}"
    return clips, name


def clip_id(abspath):
    """The record's id for a media file inside a recorded card folder, or None (no record beside any ancestor)."""
    folder = card_folder(os.path.dirname(os.path.abspath(abspath)))
    if not folder:
        return None
    clips, name = _record(folder)
    rel = os.path.relpath(abspath, folder)
    stem = os.path.splitext(os.path.basename(abspath))[0]
    return clips.get(rel) or (f"{name}/{stem}" if name else None)


def folder_of(abspath):
    """The card folder a media file sits in (a record beside it), or None."""
    return card_folder(os.path.dirname(os.path.abspath(abspath)))


def clear():
    """Forget what was read (a record written since would otherwise be missed in a long-lived process)."""
    card_folder.cache_clear()
    _record.cache_clear()
