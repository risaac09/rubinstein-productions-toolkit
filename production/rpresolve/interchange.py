"""
rpresolve.interchange: one clip of a cut manifest (see cutlist.py) as a file
another editor imports: a CMX 3600 EDL, FCPXML 1.10 or OpenTimelineIO JSON.
Offline and stdlib only. ffprobe reads the source when it is on this
machine; nothing touches Resolve.

Run from production/:
    python3 -m rpresolve.interchange MANIFEST --format edl|fcpxml|otio --out FILE
        [--clip NAME] [--fps 30000/1001] [--media-root DIR] [--title T]

The clip's spans are laid end to end on one timeline, the same timeline cut
builds in a running Resolve. --clip names the clip when the manifest holds
more than one. A "speech": false manifest is read too: nothing here checks
words.

Time:
    - a span covers source frames [frame(in), frame(out)) (cutlist.frame,
      rounding half up), counted at the manifest's own fps number (29.97)
      when it matches the rate, exactly as cut counts them, else at the
      rate. Everything after that is whole frames, so a timeline of any
      length ends exactly where its spans add up, and an FCPXML time is
      always a whole number of frame durations. A span that starts before
      the source's first frame or runs past its last is refused
    - the rate is --fps, else the manifest's fps, else the source's. 23.976,
      29.97 and 59.94 are read as 24000/1001, 30000/1001 and 60000/1001, and
      so is a probed rate such as 2997/100. A source probed at a different
      rate is refused, since these files carry one rate for source and record
    - the source's start timecode (ffprobe's timecode tag) offsets every
      source time, so EDL source timecodes, the FCPXML asset start and the
      OTIO available range match the camera's own timecode, hours wrapping
      at 24 as a time-of-day clock does. A source with no tag starts at
      00:00:00:00
    - the record side starts at RECORD_START
    - drop-frame at 30000/1001 and 60000/1001 unless the source's timecode
      is non-drop (':' before the frames); non-drop at every other rate. A
      drop-frame (';') timecode at any other rate (119.88) is refused

Split edits: a span may carry "audio_lead" (seconds its sound starts before
its picture: a J cut) or "audio_tail" (seconds its sound runs past its
picture: an L cut). The neighbouring span's sound gives way by the same
amount, as a rolling edit leaves it, so the sound stays one unbroken run as
long as the picture. A J cut on the first span, an L cut on the last, or a
J and an L meeting at one edit are refused.

Formats:
    edl     CMX 3600: TITLE and FCM lines, then a V event per span and, when
            the source has sound, an AA event per span with its own times
            (sorted by record in), 8-character reels from the file name, a
            "* FROM CLIP NAME:" line after every event; more than
            EDL_MAX_EVENTS events is refused
    fcpxml  FCPXML 1.10: one format, one asset with a media-rep, a project
            whose spine holds an asset-clip per span; split edits as
            audioStart and audioDuration on the asset-clip
    otio    OpenTimelineIO JSON (Timeline.1, Stack.1, Track.1, Clip.2,
            ExternalReference.1, TimeRange.1, RationalTime.1): a video track
            and, when the source has sound, an audio track

--media-root DIR writes the media URL as DIR/<file name>, for media that
sits elsewhere on the machine that imports the file. The source is still
probed at its manifest path; when it is not on this machine its timecode,
size and sound are assumed (DEFAULT_SIZE, DEFAULT_AUDIO, 00:00:00:00) and
the summary says so.

The output names client media, so it is refused inside any git working tree,
and refused when --out is the manifest or the source. It is written to a
temporary file that then replaces --out. Not handled: transitions, speed changes, reframes, captions, markers, more
than one source. See production/interchange.md.
"""

import argparse
import json
import math
import os
import posixpath
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from urllib.parse import quote

from . import captions, cutlist, detect, paths, relink
from . import delivercheck as dc
from .workflows import _frames_to_timecode

FORMATS = ("edl", "fcpxml", "otio")
FCPXML_VERSION = "1.10"
RECORD_START = "01:00:00:00"
EDL_MAX_EVENTS = 999  # CMX 3600's three-digit event number
RATE_TOLERANCE = Fraction(1, 100)  # fps: a probed source this close to the rate runs at it
NTSC_TOLERANCE = Fraction(1, 1000)  # fps: 29.97 and friends read as n*1000/1001
DEFAULT_SIZE = (1920, 1080)
DEFAULT_AUDIO = {"streams": 1, "channels": 2, "rate": 48000}
COLOR_SPACE = "1-1-1 (Rec. 709)"


class InterchangeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Rate and timecode
# ---------------------------------------------------------------------------

def parse_rate(value):
    """An exact frame rate from 25, '29.97', 23.976 or '30000/1001'; a
    decimal within NTSC_TOLERANCE of n*1000/1001 is read as that."""
    try:
        r = dc.parse_fps(str(value).strip())
    except ValueError as e:
        raise InterchangeError(str(e))
    ntsc = Fraction(round(r * Fraction(1001, 1000)) * 1000, 1001)
    return ntsc if r.denominator != 1 and abs(r - ntsc) < NTSC_TOLERANCE else r


def fmt_rate(rate):
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


def timecode(frames, rate, drop):
    """The label of a frame count: HH:MM:SS:FF, or HH:MM:SS;FF drop-frame.
    Hours wrap at 24, as a time-of-day camera clock does past midnight (a
    drop-frame day is 144 whole ten-minute blocks, so the rest is unchanged)."""
    hh, rest = _frames_to_timecode(frames, rate, drop).split(":", 1)
    return f"{int(hh) % 24:02d}:{rest}"


def tc_frames(tc, rate):
    """Frames from 00:00:00:00 to a timecode (';' counted as drop-frame)."""
    try:
        return int(captions.parse_timecode(tc, rate) * rate)
    except captions.CaptionError as e:
        raise InterchangeError(str(e))


def fcp_time(frames, rate):
    """FCPXML rational seconds for a frame count: '5s' when whole, else over
    the frame duration's denominator ('90090/30000s' at 30000/1001)."""
    secs = Fraction(frames) / rate
    if secs.denominator == 1:
        return f"{secs.numerator}s"
    fd = 1 / rate
    return f"{frames * fd.numerator}/{fd.denominator}s"


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------

def probe(path):
    """What a timeline needs from the source file: {rate, tc, frames, size,
    audio}. Raises InterchangeError when ffprobe cannot read it."""
    try:
        info, err = detect.run_ffprobe(path)
    except detect.ToolMissing as e:
        raise InterchangeError(f"cannot probe {path}: {e} (set RPRESOLVE_FFPROBE)")
    if info is None:
        raise InterchangeError(f"ffprobe cannot read {path}: {err}")
    tc, _ = captions.file_start(info)
    facts = relink.probe_facts(info, tc)
    if facts["kind"] != "video":
        raise InterchangeError(f"{path} has no picture")
    audio = [s for s in info.get("streams") or [] if s.get("codec_type") == "audio"]
    r = captions.file_rate(info)  # `-r 29.97` files probe as 2997/100: snap like --fps
    return {"rate": parse_rate(r) if r else None, "tc": tc, "frames": facts["frames"],
            "size": facts["resolution"],
            "audio": {"streams": len(audio), "channels": int(audio[0].get("channels") or 2),
                      "rate": int(audio[0].get("sample_rate") or 48000)} if audio else None}


def reel(name):
    """An 8-character CMX reel from a file name: letters, digits and _."""
    stem = os.path.splitext(name)[0].upper()
    return "".join(c for c in stem if c.isascii() and (c.isalnum() or c == "_"))[:8] or "AX"


def _one_line(text):
    return " ".join(str(text).split())


# ---------------------------------------------------------------------------
# The timeline every writer reads
# ---------------------------------------------------------------------------

def _split_frames(span, key, rate, where):
    v = span.get(key, 0)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise InterchangeError(f"{where}: {key} must be finite seconds at or above 0, not {v!r}")
    return cutlist.frame(v, rate)


def timeline(manifest, clip=None, fps=None, media=None, media_root=None, title=None):
    """One clip of a validated manifest as whole frames: {title, clip, rate,
    drop, record_start, source, events, frames, warnings}. media is probe()'s
    result, or None when the source is not on this machine. Each event holds
    picture (v_in, v_out) and sound (a_in, a_out) in source frames from the
    file's first frame, and rec_in, rec_out, a_rec_in, a_rec_out in record
    frames from RECORD_START."""
    clips = manifest["clips"]
    if clip is None:
        if len(clips) != 1:
            raise InterchangeError(f"the manifest holds {len(clips)} clips "
                                   f"({', '.join(c['name'] for c in clips)}); name one with --clip")
        c = clips[0]
    else:
        c = next((x for x in clips if x["name"] == clip), None)
        if c is None:
            raise InterchangeError(f"no clip named {clip!r} in the manifest "
                                   f"({', '.join(x['name'] for x in clips)})")
    if fps is not None:
        rate = parse_rate(fps)
    elif manifest.get("fps"):
        rate = parse_rate(manifest["fps"])
    elif media and media["rate"]:
        rate = media["rate"]
    else:
        raise InterchangeError("the manifest names no fps and the source was not probed; pass --fps")
    if media and media["rate"]:
        if abs(media["rate"] - rate) > RATE_TOLERANCE:
            raise InterchangeError(f"the source runs at {fmt_rate(media['rate'])} fps but the "
                                   f"timeline would run at {fmt_rate(rate)}; these files carry "
                                   "one rate (pass --fps to match the source)")
        rate = media["rate"]
    # cut counts span frames at the manifest's own number (29.97, not 30000/1001); match it
    fr = manifest.get("fps")
    fr = fr if type(fr) in (int, float) and abs(fr - rate) < NTSC_TOLERANCE else rate

    path = manifest["source_path"]
    name = os.path.basename(path)
    warnings, probed = [], media is not None
    if not probed:
        media = {"rate": None, "tc": None, "frames": None, "size": DEFAULT_SIZE,
                 "audio": dict(DEFAULT_AUDIO)}
        warnings.append(f"{name} is not on this machine, so it was not probed: its timecode is "
                        f"taken as 00:00:00:00, its size as {DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]} "
                        "and its sound as one stereo stream. Check source times against the "
                        "camera's timecode before trusting a conform")
    tc = media["tc"]
    if tc and ";" in tc and rate not in captions.DROP_FRAME:
        raise InterchangeError(f"{name}: its timecode {tc} is drop-frame at {fmt_rate(rate)} fps; "
                               "drop-frame is counted only at 29.97 and 59.94, so this source "
                               "is not supported (119.88 fps camera files write such timecode)")
    drop = rate in captions.DROP_FRAME and (tc is None or ";" in tc)
    start = tc_frames(tc, rate) if tc else 0
    record_start = tc_frames(RECORD_START[:-3] + (";" if drop else ":") + RECORD_START[-2:], rate)
    if media_root:
        if not media_root.startswith("/"):
            raise InterchangeError(f"--media-root {media_root} is not absolute; a file URL needs "
                                   "a path from /")
        url_path = posixpath.join(media_root, name)
    else:
        url_path = os.path.abspath(path)

    events = []
    for i, s in enumerate(c["spans"], 1):
        where = f"{c['name']} span {i}"
        if not (math.isfinite(s["in"]) and math.isfinite(s["out"])):
            raise InterchangeError(f"{where}: in and out must be finite seconds, not "
                                   f"{s['in']!r}-{s['out']!r}")
        v_in, v_out = cutlist.frame(s["in"], fr), cutlist.frame(s["out"], fr)
        if v_out <= v_in:
            raise InterchangeError(f"{where}: {s['in']}-{s['out']} s holds no whole frame at "
                                   f"{fmt_rate(rate)} fps")
        events.append({"v_in": v_in, "v_out": v_out,
                       "lead": _split_frames(s, "audio_lead", fr, where),
                       "tail": _split_frames(s, "audio_tail", fr, where)})
    n = len(events)
    if (any(e["lead"] or e["tail"] for e in events)) and not media["audio"]:
        raise InterchangeError(f"{c['name']}: a split edit needs sound and {name} has none")
    rec = 0
    for k, e in enumerate(events):
        where = f"{c['name']} span {k + 1}"
        if e["lead"] and k == 0:
            raise InterchangeError(f"{where}: audio_lead (a J cut) needs a span before it")
        if e["tail"] and k == n - 1:
            raise InterchangeError(f"{where}: audio_tail (an L cut) needs a span after it")
        prev_tail = events[k - 1]["tail"] if k else 0
        next_lead = events[k + 1]["lead"] if k + 1 < n else 0
        if e["lead"] and prev_tail:
            raise InterchangeError(f"{where}: its audio_lead meets span {k}'s audio_tail at one "
                                   "edit; keep one")
        e["rec_in"], e["rec_out"] = rec, rec + e["v_out"] - e["v_in"]
        e["a_in"] = e["v_in"] - e["lead"] + prev_tail
        e["a_out"] = e["v_out"] + e["tail"] - next_lead
        e["a_rec_in"] = rec - e["lead"] + prev_tail
        e["a_rec_out"] = e["rec_out"] + e["tail"] - next_lead
        rec = e["rec_out"]
        if e["a_out"] <= e["a_in"]:
            raise InterchangeError(f"{where}: the split edits around it leave it no sound")
        if min(e["v_in"], e["a_in"]) < 0:
            raise InterchangeError(f"{where} starts before the first frame of {name}")
        if media["frames"] and max(e["v_out"], e["a_out"]) > media["frames"]:
            raise InterchangeError(f"{where} runs past the end of {name} "
                                   f"({media['frames']} frames)")

    frames = media["frames"] or max(max(e["v_out"], e["a_out"]) for e in events)
    source = {"path": path, "name": name, "reel": reel(name), "url": "file://" + quote(url_path),
              "tc": tc, "start": start, "frames": frames, "size": media["size"] or DEFAULT_SIZE,
              "audio": media["audio"], "probed": probed}
    return {"title": _one_line(title or c["name"]), "clip": c["name"], "rate": rate, "drop": drop,
            "record_start": record_start, "source": source, "events": events, "frames": rec,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def edl(tl):
    """CMX 3600 text."""
    rate, drop, src = tl["rate"], tl["drop"], tl["source"]
    s0, r0 = src["start"], tl["record_start"]
    rows = []
    for e in tl["events"]:
        rows.append((e["rec_in"], 0, "V", e["v_in"], e["v_out"], e["rec_in"], e["rec_out"]))
        if src["audio"]:
            rows.append((e["a_rec_in"], 1, "AA", e["a_in"], e["a_out"], e["a_rec_in"], e["a_rec_out"]))
    if len(rows) > EDL_MAX_EVENTS:
        raise InterchangeError(f"this cut needs {len(rows)} EDL events and CMX 3600 holds "
                               f"{EDL_MAX_EVENTS}; write fcpxml or otio")
    rows.sort()
    out = [f"TITLE: {tl['title']}", "FCM: " + ("DROP FRAME" if drop else "NON-DROP FRAME"), ""]
    for n, (_, _, ch, a, b, ra, rb) in enumerate(rows, 1):
        tcs = " ".join(timecode(f, rate, drop) for f in (s0 + a, s0 + b, r0 + ra, r0 + rb))
        out.append(f"{n:03d}  {src['reel']:<8} {ch:<5} C        {tcs}")
        out.append(f"* FROM CLIP NAME: {_one_line(src['name'])}")
    return "\n".join(out) + "\n"


def fcpxml(tl):
    """FCPXML 1.10 text."""
    rate, src = tl["rate"], tl["source"]
    s0, r0 = src["start"], tl["record_start"]
    t = lambda f: fcp_time(f, rate)
    tc_format = "DF" if tl["drop"] else "NDF"
    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    res = ET.SubElement(root, "resources")
    w, h = src["size"]
    ET.SubElement(res, "format", id="r1", frameDuration=t(1), width=str(w), height=str(h),
                  colorSpace=COLOR_SPACE)
    asset = {"id": "r2", "name": src["name"], "start": t(s0), "duration": t(src["frames"]),
             "hasVideo": "1", "format": "r1", "hasAudio": "1" if src["audio"] else "0"}
    if src["audio"]:
        asset.update(audioSources=str(src["audio"]["streams"]),
                     audioChannels=str(src["audio"]["channels"]), audioRate=str(src["audio"]["rate"]))
    ET.SubElement(ET.SubElement(res, "asset", asset), "media-rep", kind="original-media",
                  src=src["url"])
    event = ET.SubElement(ET.SubElement(root, "library"), "event", name=tl["title"])
    seq = ET.SubElement(ET.SubElement(event, "project", name=tl["title"]), "sequence", format="r1",
                        duration=t(tl["frames"]), tcStart=t(r0), tcFormat=tc_format,
                        audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")
    for e in tl["events"]:
        clip = {"ref": "r2", "name": src["name"], "offset": t(r0 + e["rec_in"]),
                "start": t(s0 + e["v_in"]), "duration": t(e["v_out"] - e["v_in"]),
                "tcFormat": tc_format}
        if (e["a_in"], e["a_out"]) != (e["v_in"], e["v_out"]):
            clip.update(audioStart=t(s0 + e["a_in"]), audioDuration=t(e["a_out"] - e["a_in"]))
        ET.SubElement(spine, "asset-clip", clip)
    ET.indent(root)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n'
            + ET.tostring(root, encoding="unicode") + "\n")


def otio(tl):
    """OpenTimelineIO JSON text."""
    rate, src = float(tl["rate"]), tl["source"]
    s0 = src["start"]

    def rt(f):
        return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": float(f)}

    def tr(a, d):
        return {"OTIO_SCHEMA": "TimeRange.1", "duration": rt(d), "start_time": rt(a)}

    def clip(a, b):
        ref = {"OTIO_SCHEMA": "ExternalReference.1", "metadata": {}, "name": src["name"],
               "available_range": tr(s0, src["frames"]), "available_image_bounds": None,
               "target_url": src["url"]}
        return {"OTIO_SCHEMA": "Clip.2", "metadata": {}, "name": src["name"],
                "source_range": tr(s0 + a, b - a), "effects": [], "markers": [], "enabled": True,
                "media_references": {"DEFAULT_MEDIA": ref},
                "active_media_reference_key": "DEFAULT_MEDIA"}

    def track(name, kind, children):
        return {"OTIO_SCHEMA": "Track.1", "metadata": {}, "name": name, "source_range": None,
                "effects": [], "markers": [], "enabled": True, "children": children, "kind": kind}

    tracks = [track("V1", "Video", [clip(e["v_in"], e["v_out"]) for e in tl["events"]])]
    if src["audio"]:
        tracks.append(track("A1", "Audio", [clip(e["a_in"], e["a_out"]) for e in tl["events"]]))
    doc = {"OTIO_SCHEMA": "Timeline.1", "metadata": {}, "name": tl["title"],
           "global_start_time": rt(tl["record_start"]),
           "tracks": {"OTIO_SCHEMA": "Stack.1", "metadata": {}, "name": "tracks",
                      "source_range": None, "effects": [], "markers": [], "enabled": True,
                      "children": tracks}}
    return json.dumps(doc, indent=2) + "\n"


WRITERS = {"edl": edl, "fcpxml": fcpxml, "otio": otio}


# ---------------------------------------------------------------------------
# Build, report, command line
# ---------------------------------------------------------------------------

def build(manifest, fmt, clip=None, fps=None, media_root=None, title=None):
    """(text, report) for one clip of a validated manifest. The source is
    probed when it is on this machine."""
    if fmt not in WRITERS:
        raise InterchangeError(f"format {fmt!r} is not one of {', '.join(FORMATS)}")
    path = manifest["source_path"]
    media = probe(path) if os.path.isfile(path) else None
    tl = timeline(manifest, clip=clip, fps=fps, media=media, media_root=media_root, title=title)
    return WRITERS[fmt](tl), report(tl, fmt)


def report(tl, fmt):
    """The parameters and totals of a written timeline."""
    src, rate = tl["source"], tl["rate"]
    return {"format": fmt, "clip": tl["clip"], "title": tl["title"], "fps": fmt_rate(rate),
            "drop_frame": tl["drop"], "record_start": timecode(tl["record_start"], rate, tl["drop"]),
            "source": src["name"], "url": src["url"], "probed": src["probed"],
            "source_tc": timecode(src["start"], rate, tl["drop"]), "size": list(src["size"]),
            "audio": src["audio"], "spans": len(tl["events"]), "frames": tl["frames"],
            "seconds": round(float(tl["frames"] / rate), 3),
            "split_edits": sum(1 for e in tl["events"] if e["lead"] or e["tail"]),
            "warnings": list(tl["warnings"])}


def summary(rep):
    """One line."""
    return (f"{rep['format']} {rep['title']}: {rep['spans']} span(s), {rep['frames']} frames "
            f"({rep['seconds']} s) at {rep['fps']}{' DF' if rep['drop_frame'] else ''}, source TC "
            f"{rep['source_tc']}{'' if rep['probed'] else ' (assumed)'}, "
            f"{rep['split_edits']} split edit(s)")


def add_arguments(p):
    p.add_argument("manifest", help="a cut manifest (see rpresolve/cutlist.py)")
    p.add_argument("--format", required=True, choices=FORMATS)
    p.add_argument("--out", required=True, help="the file to write, outside any git working tree")
    p.add_argument("--clip", help="the manifest clip to write (needed when it holds more than one)")
    p.add_argument("--fps", help="frame rate: 30000/1001, 29.97, 25 (default: the manifest's)")
    p.add_argument("--media-root", help="the folder the media URL names, on the machine that "
                                        "imports the file (default: the manifest's source folder)")
    p.add_argument("--title", help="the timeline's name (default: the clip's name)")


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        manifest = cutlist.load_manifest(args.manifest, allow_no_speech=True)
        for label, p in (("manifest", args.manifest), ("source", manifest["source_path"])):
            if os.path.realpath(args.out) == os.path.realpath(p) or (
                    os.path.exists(args.out) and os.path.exists(p) and os.path.samefile(args.out, p)):
                raise paths.OutputRefused(f"--out {args.out} is the {label}; write the "
                                          f"{args.format} elsewhere")
        text, rep = build(manifest, args.format, clip=args.clip, fps=args.fps,
                          media_root=args.media_root, title=args.title)
        from resolve_survey import write_report  # atomic: temp file, then replace
        write_report(args.out, text)  # a hard link into a git tree keeps its old content
    except (OSError, ValueError) as e:  # InterchangeError, CutlistError, OutputRefused, bad JSON
        print(f"interchange: {e}", file=sys.stderr)
        return 2
    for w in rep["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    print(f"{summary(rep)} -> {args.out}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.interchange",
                                description=__doc__.split("\n\n")[0])
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
