"""
rpresolve.guard: the one door a Whisper transcript goes through. Whisper
writes singing as words (lyrics, which a caption or an agent can then
repeat), and wind and silence as sentences. tools/vertcut/lib/segkind.py
decides, from numbers and never from the words, which segments are speech;
this module is how the library reaches it, and every reader of a Whisper
JSON in rpresolve goes through load() so none can skip it. A test scans the
sources for a reader that does not.

load() returns the transcript with withheld text already replaced by a
label, plus the withheld spans. A JSON that segkind already wrote is used
as it is; a raw Whisper JSON is screened in memory from the Whisper numbers
and the text counts alone (no audio here, so singing is not screened from
sound) and says so. A .raw.json is refused by name: it is the unredacted
transcript. If segkind cannot be found, nothing is read: fail closed.

Stdlib only. segkind uses numpy for audio when there is some.
"""

import os
import sys


class GuardError(ValueError):
    pass


# kinds that mean "someone may be singing here": a clip must not be cut
# there without a person looking
MUSICAL = ("sung", "music", "uncertain")


def _segkind():
    try:
        import segkind
        return segkind
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    lib = os.path.normpath(os.path.join(here, "..", "..", "tools", "vertcut", "lib"))
    if os.path.isdir(lib) and lib not in sys.path:
        sys.path.append(lib)
    try:
        import segkind
        return segkind
    except ImportError:
        raise GuardError("tools/vertcut/lib/segkind.py was not found, so the transcript cannot "
                         "be screened for singing and hallucinations; refusing to read it "
                         "unscreened")


def load(path, audio_path=None):
    """(safe_data, blocks, audio_checked). safe_data is the Whisper JSON
    with withheld segments redacted. blocks: [{start, end, kind, label}]
    for each withheld segment, in time order."""
    sk = _segkind()
    try:
        data, results, checked = sk.load_guarded(path, audio_path)
    except sk.SegkindError as e:
        raise GuardError(str(e))
    blocks = [{"start": r["start"], "end": r["end"], "kind": r["kind"], "label": r["label"]}
              for r in results if r["withheld"] and not r.get("empty")
              and r["start"] is not None and r["end"] is not None]
    return data, blocks, bool(checked)

