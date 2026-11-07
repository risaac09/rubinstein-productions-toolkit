"""
rpresolve.fcpxmlread: what an FCPXML file says a timeline holds, read offline
so a Resolve import can be planned before and reconciled after. Stdlib only.

read(path) -> {rate, drop, events, transitions, media, warnings}
  rate: the sequence's frame rate as a Fraction (from the format's frameDuration)
  events: one per clip in record order: {name, kind ('video' or 'audio'),
          lane (0 for the spine, >0 above it, <0 below it), record (frames from
          the sequence start), frames, path (the asset's file, or None)}
  transitions: [{name, record, frames}] on the spine, in record order
  media: {path: exists} for every asset with a file URL
A connected clip's offset is in its parent's local time (the parent's `start`
is its local zero), as the FCPXML format defines it. Only the first project
of the first event is read. Times that do not land on a whole frame are
rounded and named in warnings.
"""

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from fractions import Fraction


class FcpxmlError(ValueError):
    pass


CLIP_TAGS = ("asset-clip", "clip", "ref-clip", "sync-clip", "mc-clip", "video", "audio", "gap")


def secs(text):
    """FCPXML rational seconds ('90090/30000s', '5s') as a Fraction."""
    t = (text or "0s").strip()
    if not t.endswith("s"):
        raise FcpxmlError(f"time '{text}' is not in seconds")
    t = t[:-1]
    if "/" in t:
        n, d = t.split("/", 1)
        return Fraction(int(n), int(d))
    return Fraction(t)


def read(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        raise FcpxmlError(f"cannot read {path}: {e}")
    if root.tag != "fcpxml":
        raise FcpxmlError(f"{path} is not an FCPXML document (root <{root.tag}>)")
    res = root.find("resources")
    formats, assets = {}, {}
    if res is not None:
        for f in res.findall("format"):
            formats[f.get("id")] = f
        for a in res.findall("asset"):
            rep = a.find("media-rep")
            src = (rep.get("src") if rep is not None else None) or a.get("src")
            assets[a.get("id")] = {"name": a.get("name"), "path": _file_path(src),
                                   "video": a.get("hasVideo", "1") != "0",
                                   "audio": a.get("hasAudio", "1") != "0"}
    seq = root.find("./library/event/project/sequence")
    if seq is None:
        seq = root.find(".//sequence")
    if seq is None:
        raise FcpxmlError(f"{path} holds no sequence")
    fmt = formats.get(seq.get("format"))
    if fmt is None or not fmt.get("frameDuration"):
        raise FcpxmlError(f"{path}: the sequence's format has no frameDuration")
    fd = secs(fmt.get("frameDuration"))
    if fd <= 0:
        raise FcpxmlError(f"{path}: frameDuration '{fmt.get('frameDuration')}' is not positive")
    rate = 1 / fd
    warnings = []
    out = {"rate": rate, "drop": seq.get("tcFormat", "NDF") == "DF", "events": [],
           "transitions": [], "media": {}, "warnings": warnings}

    def frames(text, what):
        f = secs(text) * rate
        if f.denominator != 1:
            warnings.append(f"{what}: {text} is not a whole frame at {rate} fps; rounded")
        return int(round(f))

    def walk(parent, record_base, local_zero):
        for el in parent:
            if el.tag == "transition":
                out["transitions"].append({"name": el.get("name", ""),
                                           "record": record_base + frames(el.get("offset"), "transition") - local_zero,
                                           "frames": frames(el.get("duration"), "transition")})
                continue
            if el.tag not in CLIP_TAGS:
                continue
            if el.tag in ("video", "audio") and parent.tag != "spine" and not el.get("lane"):
                continue  # a <clip>'s own picture or sound, not a connected clip
            offset = frames(el.get("offset", "0s"), el.get("name", el.tag))
            record = record_base + offset - local_zero
            length = frames(el.get("duration", "0s"), el.get("name", el.tag))
            lane = int(el.get("lane", "0"))
            asset = assets.get(el.get("ref"), {})
            if el.tag != "gap":
                kind = "audio" if el.tag == "audio" or (asset and not asset["video"]) else "video"
                ev = {"name": el.get("name") or asset.get("name") or el.tag, "kind": kind,
                      "lane": lane, "record": record, "frames": length, "path": asset.get("path")}
                adjust = el.find("adjust-transform")
                if adjust is not None and adjust.get("scale"):
                    try:
                        ev["scale"] = float(adjust.get("scale").split()[0])
                    except ValueError:
                        warnings.append(f"{ev['name']}: scale '{adjust.get('scale')}' is not a "
                                        "number; not checked")
                out["events"].append(ev)
            # children connected to this clip: their offsets count from its `start`
            walk(el, record, frames(el.get("start", "0s"), el.get("name", el.tag)))

    spine = seq.find("spine")
    if spine is None:
        raise FcpxmlError(f"{path}: the sequence has no spine")
    walk(spine, 0, 0)
    # at one record time: the spine clip, then the lanes above it, then those below
    out["events"].sort(key=lambda e: (e["record"], e["lane"] < 0, abs(e["lane"])))
    out["transitions"].sort(key=lambda t: t["record"])
    for a in assets.values():
        if a["path"]:
            out["media"][a["path"]] = os.path.isfile(a["path"])
    return out


def _file_path(src):
    if not src:
        return None
    u = urllib.parse.urlparse(src)
    if u.scheme == "file":
        return urllib.request.url2pathname(u.path)
    return src if os.path.isabs(src) else None
