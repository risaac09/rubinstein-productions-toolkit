"""
rpresolve.packet: one folder per moment (a frame, a cut or a span) that
bundles what the lanes already wrote, so Claude reads one place. Assembly
only: nothing is measured, scored or re-decoded here, and nothing touches
Resolve. Stdlib only.

The folder:
    index.json      the sidecar envelope (schema story.packet, version 1):
                    the moment, the parts in reading order, and for each
                    part its path, sha256 and where it came from
    cine/           a perceive folder, copied as it is (sheet.png, the
                    scopes, packet.json, schema cine.perceive)
    sound/          an audio-report folder: report.json and the images it
                    lists beside it, copied as they are
    words.json      the words of the story packet's Whisper JSON that fall in
                    the window, verbatim (word, start, end), and the phrase
                    lines storypacket prints for them
    filmstrip.<ext> a timeline_view image, copied as it is. The producer is
                    tools/video-editor/video-use/helpers/timeline_view.py,
                    vendored by fetch-video-use.sh and often missing, so the
                    assembler takes a path and never runs it

Reading order, in index.json's `story.parts`: one image sheet first, then
the numbers, then the words, then the sound slice and the filmstrip. A part
with no input is left out; the index never lists a file that is not there.

A frame or a cut (t_s) takes the words within WORDS_PAD_S of it; a span
(start_s, end_s) takes the words that overlap it. The sound slice is the
report as audio-report wrote it for that range; the assembler cannot cut a
report, so run audio-report with the same start and end first. Every
input that is an envelope must carry the packet's clip_id, or nothing is
written. Paths in index.json are relative to the folder. The folder holds
frames, words and measurements of media, so it is refused inside any git
working tree.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

from . import cutlist, sidecar, storypacket
from .paths import in_any_git_tree

NAME, VERSION = "story.packet", 1
WORDS_PAD_S = 2.0


class PacketError(ValueError):
    """An input the packet cannot be built from."""


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise PacketError(f"cannot read {path}: {e}")


def moment(t_s=None, start_s=None, end_s=None):
    """The moment as the index records it: a frame/cut (t_s) or a span."""
    if t_s is not None and start_s is None and end_s is None:
        return {"kind": "frame", "t_s": round(float(t_s), 3)}
    if t_s is None and start_s is not None and end_s is not None and 0 <= start_s < end_s:
        return {"kind": "span", "start_s": round(float(start_s), 3), "end_s": round(float(end_s), 3)}
    raise PacketError("a moment is t_s alone, or start_s and end_s with 0 <= start_s < end_s")


def window(m):
    if m["kind"] == "span":
        return m["start_s"], m["end_s"]
    return max(0.0, m["t_s"] - WORDS_PAD_S), m["t_s"] + WORDS_PAD_S


def _check_clip(doc, clip_id, what):
    """An envelope input must name the packet's clip; one that predates the
    envelope (no schema key) is taken as given."""
    if isinstance(doc, dict) and "schema" in doc:
        bad = sidecar.validate(doc)
        if bad:
            raise PacketError(f"{what} is not a sound sidecar: " + "; ".join(bad))
        if not doc["schema"]["name"].startswith("sound."):
            raise PacketError(f"{what} is {doc['schema']['name']}, not a sound sidecar")
        if doc["clip_id"] != clip_id:
            raise PacketError(f"{what} is for clip {doc['clip_id']}, not {clip_id}")


def _copy(src, out, rel):
    dst = os.path.join(out, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return {"path": rel, "sha256": _sha(dst)}


def _cine_doc(folder, m):
    """The perceive folder's packet.json, checked: a cine.perceive version 2
    envelope whose frame is the moment (a frame within a millisecond of t_s,
    or inside the span), and whose files stay inside the folder."""
    doc = _load(os.path.join(folder, "packet.json"))
    bad = sidecar.validate(doc)
    if bad or doc["schema"] != {"name": "cine.perceive", "version": 2}:
        raise PacketError("perceive folder needs a cine.perceive version 2 packet.json"
                          + (": " + "; ".join(bad) if bad else ""))
    t = (doc["cine"].get("source") or {}).get("t")
    if not isinstance(t, (int, float)):
        raise PacketError("the perceive packet records no frame time (cine.source.t)")
    lo, hi = (m["start_s"], m["end_s"]) if m["kind"] == "span" else (m["t_s"] - 0.001, m["t_s"] + 0.001)
    if not lo <= t <= hi:
        raise PacketError(f"the perceive frame is at {t}s, outside the moment")
    for rel in (doc["cine"].get("files") or {}).values():
        if os.path.isabs(rel) or os.path.normpath(rel).startswith(".."):
            raise PacketError(f"the perceive packet lists {rel}, outside its folder")
    return doc


def _cine(folder, doc, out, parts):
    files = doc["cine"].get("files") or {}
    sheet = files.get("sheet")
    if sheet:
        parts.append({"part": "sheet", "from": "perceive", **_copy(os.path.join(folder, sheet), out, "cine/" + sheet)})
    for rel in sorted(set(files.values()) - {sheet}):
        _copy(os.path.join(folder, rel), out, "cine/" + rel)
    parts.append({"part": "numbers", "from": "perceive",
                  "schema": doc["schema"], **_copy(os.path.join(folder, "packet.json"), out, "cine/packet.json")})
    return doc


def _words(path, m, out, clip_id, parts):
    lo, hi = window(m)
    words = [w for w in cutlist.load_words(path) if w["end"] > lo and w["start"] < hi]
    body = {"clip_id": clip_id, "window": {"start_s": round(lo, 3), "end_s": round(hi, 3)},
            "words": words, "phrases": storypacket.phrase_lines(words) if words else []}
    dst = os.path.join(out, "words.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
        f.write("\n")
    parts.append({"part": "words", "from": "story-packet words", "path": "words.json", "sha256": _sha(dst),
                  "count": len(words)})


def _sound(folder, out, clip_id, parts):
    report = os.path.join(folder, "report.json")
    _check_clip(_load(report), clip_id, "the audio report")
    entry = {"part": "sound", "from": "audio-report", **_copy(report, out, "sound/report.json")}
    # Images sit beside the report; copy each so the report's relative links hold.
    entry["files"] = [_copy(os.path.join(folder, n), out, "sound/" + n)["path"]
                      for n in sorted(os.listdir(folder)) if n.lower().endswith(".png")]
    parts.append(entry)


def assemble(out, m, perceive_dir=None, audio_dir=None, words_json=None, filmstrip=None, source=None):
    """Write the packet folder `out` (new or empty, outside any git tree) for
    moment `m` and return index.json's dict. At least one input is needed;
    the clip is the perceive packet's, else `source` (a media path)."""
    if not any((perceive_dir, audio_dir, words_json, filmstrip)):
        raise PacketError("nothing to assemble: give at least one input")
    if in_any_git_tree(out):
        raise PacketError(f"{out} is inside a git working tree; the packet holds frames and words of the media.")
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise PacketError(f"{out} exists and is not an empty folder; name a new one.")
    if not os.path.isdir(os.path.dirname(os.path.abspath(out))):
        raise PacketError(f"the folder above {out} does not exist.")
    cine_doc = _cine_doc(perceive_dir, m) if perceive_dir else None
    if cine_doc:
        src_path = cine_doc["source"]["path"]
    else:
        src_path = source
    if not src_path:
        raise PacketError("the clip is unknown: give a perceive folder or the source media path")
    head = sidecar.header(NAME, VERSION, src_path, tool="rpresolve.packet")
    if cine_doc:
        for k in ("clip_id", "source", "transfer", "primaries"):
            head[k] = cine_doc[k]
    created = not os.path.isdir(out)
    if created:
        os.mkdir(out)
    parts = []
    try:
        if perceive_dir:
            _cine(perceive_dir, cine_doc, out, parts)
        if words_json:
            _words(words_json, m, out, head["clip_id"], parts)
        if audio_dir:
            _sound(audio_dir, out, head["clip_id"], parts)
        if filmstrip:
            parts.append({"part": "filmstrip", "from": "timeline_view",
                          **_copy(filmstrip, out, "filmstrip" + os.path.splitext(filmstrip)[1].lower())})
        head["story"] = {"moment": m, "parts": parts}
        bad = sidecar.validate(head)
        if bad:
            raise PacketError("index failed the envelope check: " + "; ".join(bad))
        with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
            json.dump(head, f, indent=2)
            f.write("\n")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        if not created:
            os.makedirs(out, exist_ok=True)
        raise
    return head


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m rpresolve.packet", description=__doc__.split("\n\n")[0])
    ap.add_argument("out", help="new folder, outside any git tree")
    ap.add_argument("--at", type=float, help="a frame or cut, seconds from the source start")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--perceive", help="a perceive output folder")
    ap.add_argument("--audio", help="an audio-report output folder (report.json and its images)")
    ap.add_argument("--words", help="the clip's Whisper JSON with word timestamps")
    ap.add_argument("--filmstrip", help="a timeline_view image")
    ap.add_argument("--source", help="the media path, when there is no perceive folder")
    a = ap.parse_args(argv)
    try:
        idx = assemble(a.out, moment(a.at, a.start, a.end), a.perceive, a.audio, a.words, a.filmstrip, a.source)
    except (PacketError, cutlist.CutlistError, OSError) as e:
        print(f"packet: {e}", file=sys.stderr)
        return 2
    print(f"{a.out}: {idx['clip_id']}, " + ", ".join(p["part"] for p in idx["story"]["parts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
