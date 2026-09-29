"""
rpresolve.captions: turn the caption sidecar Resolve renders beside a
deliverable into the zero-based SubRip file a web platform takes. Offline:
ffprobe only, never Resolve. Stdlib only.

What Resolve 21.0.4.5 writes (sandbox renders, spike 20, 2026-09-29): a
render with SubtitleFormat "SeparateFile" leaves "<stem>_<subtitle track
name>.ttml" beside the file, such as "<stem>_Subtitle 1.ttml". It is IMSC1
TTML (ttp:timeBase="media", ttp:frameRate="25") whose cues are timed from
the TIMELINE's timecode: a timeline that starts at 01:00:00:00 gives
<p begin="01:00:00.039" end="01:00:03.519">. The rendered file carries the
same start as a timecode tag (ffprobe: timecode=01:00:00:00). A platform
reads an .srt from the file's first frame, so every cue moves back by the
file's start.

deliver_captions(file, dest):
    1. Refuses unless the destination's captions mode is "sidecar", the
       file sits outside any git working tree, <stem>.srt does not exist
       yet, and exactly one "<stem>_*.ttml" is beside it (or `track`
       names one of several).
    2. Parses the TTML (parse_ttml): every namespace, clock times
       (HH:MM:SS.fff, HH:MM:SS:FF with ttp:frameRate and
       ttp:frameRateMultiplier, sub-frames), offset times (12.5s, 300f,
       500ms, 2m, 1h, 100t), begin with end or dur, begin offsets on body
       and div, <br/> as a line break, spans flattened.
    3. Takes off the file's start timecode (file_start: the video stream's
       "timecode" tag, else another stream's, else the format's), read
       against the file's own frame rate; drop-frame (";") at 29.97 and
       59.94 is counted as SMPTE drop-frame, and refused at any other rate.
       With no timecode tag it refuses unless the first cue already starts
       inside the file. After the shift every cue must start at or after 0
       and end within the video's duration plus
       delivercheck.CAPTION_END_SLACK_S, or it refuses with the numbers.
    4. Writes <stem>.srt (UTF-8, LF line ends, cues numbered from 1,
       HH:MM:SS,mmm), never over an existing file, and reads it back with
       delivercheck.parse_srt: the same cue count, text and times (to the
       millisecond) as the TTML, or the new .srt is removed again and it
       fails.
    5. Unless keep_ttml, moves the TTML it converted to
       ~/.Trash/deliver-captions-<timestamp>/. A move that fails is an
       error that says where everything is; nothing is lost. Other tracks'
       TTMLs stay where they are (one .srt per stem).

Not verified yet: at 23.976 or 29.97 the timecode label and real time part
by 0.1% (3.6 s an hour). Which of the two Resolve writes into the TTML
has only been seen at 25 fps, where they agree; the result warns at a
non-integer rate. A shift the wrong way shows up as cues before 0 or past
the end, which refuse.
"""

import os
import re
import time
import xml.etree.ElementTree as ET
from fractions import Fraction

from . import deliver, paths
from . import delivercheck as dc

TRASH_LABEL = "deliver-captions"
XML_NS = "{http://www.w3.org/XML/1998/namespace}"
# Elements inside a <p> whose text is not caption text.
NOT_TEXT = {"metadata", "set", "animate", "animation"}
TIMING = ("begin", "end", "dur")
BR = "\x00"  # stands for <br/> while whitespace is collapsed

CLOCK = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})(?:(\.\d+)|:(\d{2,})(?:\.(\d+))?)?$")
OFFSET = re.compile(r"^(\d+(?:\.\d+)?)(h|ms|m|s|f|t)$")
TIMECODE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})([:;])(\d{2,3})$")
# Drop-frame counts: frames dropped at the start of each minute but every tenth.
DROP_FRAME = {Fraction(30000, 1001): 2, Fraction(60000, 1001): 4}


class CaptionError(dc.CheckError):
    """A caption sidecar that cannot be turned into an .srt; nothing written."""


# ---------------------------------------------------------------------------
# TTML
# ---------------------------------------------------------------------------

def _local(name):
    return name.rsplit("}", 1)[-1] if isinstance(name, str) else ""


def _attr(el, local):
    """The attribute named `local` in any namespace (or none), or None."""
    if local in el.attrib:
        return el.attrib[local]
    for key, value in el.attrib.items():
        if _local(key) == local:
            return value
    return None


def _positive_int(text, what):
    try:
        n = int(str(text).strip())
    except ValueError:
        raise CaptionError(f"TTML {what} {text!r} is not a whole number.")
    if n <= 0:
        raise CaptionError(f"TTML {what} {text!r} must be above 0.")
    return n


def timing_params(root):
    """{fps, nominal, sub_frames, tick_rate, time_base} from the <tt>
    element's ttp: parameters, with TTML's defaults (frame rate 30,
    multiplier 1, sub-frame rate 1; tick rate the effective frame rate
    times the sub-frame rate when a frame rate is given, else 1). fps is
    the effective frame rate, nominal the frame count per second label."""
    rate_attr = _attr(root, "frameRate")
    nominal = _positive_int(rate_attr, "ttp:frameRate") if rate_attr is not None else 30
    mult = _attr(root, "frameRateMultiplier")
    if mult is not None:
        parts = mult.split()
        if len(parts) != 2:
            raise CaptionError(f"TTML ttp:frameRateMultiplier {mult!r} must be two numbers, "
                               "such as '1000 1001'.")
        factor = Fraction(_positive_int(parts[0], "ttp:frameRateMultiplier"),
                          _positive_int(parts[1], "ttp:frameRateMultiplier"))
    else:
        factor = Fraction(1)
    sub = _attr(root, "subFrameRate")
    sub_frames = _positive_int(sub, "ttp:subFrameRate") if sub is not None else 1
    fps = nominal * factor
    tick = _attr(root, "tickRate")
    if tick is not None:
        tick_rate = Fraction(_positive_int(tick, "ttp:tickRate"))
    else:
        tick_rate = fps * sub_frames if rate_attr is not None else Fraction(1)
    base = (_attr(root, "timeBase") or "media").strip()
    if base not in ("media", "smpte"):
        raise CaptionError(f"TTML ttp:timeBase {base!r}: only media time (Resolve's) and "
                           "non-drop SMPTE time are read.")
    drop = (_attr(root, "dropMode") or "nonDrop").strip()
    if base == "smpte" and drop != "nonDrop":
        raise CaptionError(f"TTML ttp:dropMode {drop!r} under SMPTE time is not read; only "
                           "nonDrop is.")
    return {"fps": fps, "nominal": nominal, "sub_frames": sub_frames, "tick_rate": tick_rate,
            "time_base": base}


def parse_time(expr, params):
    """A TTML time expression as exact seconds (Fraction). Raises
    CaptionError naming what it cannot read."""
    text = str(expr or "").strip()
    m = CLOCK.match(text)
    if m:
        h, mi, s, frac, frames, subs = m.groups()
        if int(mi) > 59 or int(s) > 59:
            raise CaptionError(f"TTML time {text!r}: minutes and seconds run 00 to 59.")
        t = Fraction(int(h) * 3600 + int(mi) * 60 + int(s))
        if frac:
            t += Fraction(frac[1:]) / (10 ** (len(frac) - 1))
        elif frames is not None:
            if int(frames) >= params["nominal"]:
                raise CaptionError(f"TTML time {text!r}: frame {frames} at a frame rate of "
                                   f"{params['nominal']}.")
            f = Fraction(int(frames))
            if subs is not None:
                if int(subs) >= params["sub_frames"]:
                    raise CaptionError(f"TTML time {text!r}: sub-frame {subs} at a sub-frame "
                                       f"rate of {params['sub_frames']}.")
                f += Fraction(int(subs), params["sub_frames"])
            t += f / params["fps"]
        return t
    m = OFFSET.match(text)
    if m:
        value = Fraction(m.group(1))
        unit = m.group(2)
        if unit == "h":
            return value * 3600
        if unit == "m":
            return value * 60
        if unit == "s":
            return value
        if unit == "ms":
            return value / 1000
        if unit == "f":
            return value / params["fps"]
        return value / params["tick_rate"]
    raise CaptionError(f"TTML time {text!r} is not a clock time (HH:MM:SS.fff, HH:MM:SS:FF) "
                       "or an offset time (12.5s, 300f, 500ms).")


def _p_text(p):
    """A <p>'s caption text: spans flattened, <br/> as a line break, runs
    of white space collapsed as TTML's default xml:space does (kept as line
    breaks under xml:space="preserve"), blank lines dropped."""
    preserve = p.get(XML_NS + "space") == "preserve"
    parts = []

    def walk(el):
        if el.text:
            parts.append(el.text)
        for child in el:
            name = _local(child.tag)
            if name == "br":
                parts.append(BR)
            elif name == "span":
                if any(_attr(child, k) is not None for k in TIMING):
                    raise CaptionError("a <span> inside a caption carries its own timing, which "
                                       "this converter does not split into cues.")
                walk(child)
            elif name not in NOT_TEXT:
                walk(child)
            if child.tail:
                parts.append(child.tail)
    walk(p)
    text = "".join(parts)
    if preserve:
        text = text.replace("\r\n", BR).replace("\n", BR).replace("\r", BR)
    lines = [re.sub(r"[ \t\r\n]+", " ", line).strip() for line in text.split(BR)]
    return "\n".join(line for line in lines if line)


def parse_ttml(text):
    """{cues, params, empty} from TTML text. cues: [(begin, end, text)] in
    exact seconds (Fraction), in time order; empty: how many <p> had no
    text and were left out. Raises CaptionError."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise CaptionError(f"the TTML is not well-formed XML: {e}")
    if _local(root.tag) != "tt":
        raise CaptionError(f"the root element is <{_local(root.tag)}>; a TTML file's root is "
                           "<tt>.")
    params = timing_params(root)
    cues, empty = [], 0

    def visit(el, offset):
        begin = _attr(el, "begin")
        here = offset + (parse_time(begin, params) if begin is not None else 0)
        name = _local(el.tag)
        if name == "p":
            words = _p_text(el)
            end, dur = _attr(el, "end"), _attr(el, "dur")
            if begin is None:
                raise CaptionError(f"a caption has no begin time ({words[:40]!r}).")
            if end is not None:
                stop = offset + parse_time(end, params)
            elif dur is not None:
                stop = here + parse_time(dur, params)
            else:
                raise CaptionError(f"a caption at {float(here):.3f} s has no end or dur.")
            if not words:
                return 1
            if stop <= here:
                raise CaptionError(f"a caption ends at {float(stop):.3f} s, at or before its "
                                   f"begin {float(here):.3f} s.")
            cues.append((here, stop, words))
            return 0
        return sum(visit(child, here) for child in el if _local(child.tag) in ("body", "div", "p"))

    for child in root:
        if _local(child.tag) == "body":
            empty += visit(child, Fraction(0))
    cues.sort(key=lambda c: (c[0], c[1]))
    return {"cues": cues, "params": params, "empty": empty}


# ---------------------------------------------------------------------------
# The file's start
# ---------------------------------------------------------------------------

def parse_timecode(tc, fps):
    """Seconds (Fraction) from 00:00:00:00 to timecode tc in a file of
    frame rate fps (a Fraction): frames counted at the nominal rate
    (round(fps)), then divided by fps. A drop-frame timecode (';' before the
    frames) is counted as SMPTE drop-frame at 29.97 and 59.94 and refused
    at any other rate. Raises CaptionError."""
    m = TIMECODE.match(str(tc or "").strip())
    if not m:
        raise CaptionError(f"timecode {tc!r} is not HH:MM:SS:FF (or HH:MM:SS;FF for "
                           "drop-frame).")
    h, mi, s, sep, ff = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5))
    nominal = round(fps)
    if mi > 59 or s > 59 or ff >= nominal:
        raise CaptionError(f"timecode {tc} does not fit a frame rate of {float(fps):.3f}.")
    frames = ((h * 60 + mi) * 60 + s) * nominal + ff
    if sep == ";":
        drop = DROP_FRAME.get(fps)
        if drop is None:
            raise CaptionError(f"timecode {tc} is drop-frame (';'), which only 29.97 and 59.94 "
                               f"fps use; this file runs at {float(fps):.3f} fps. Check the "
                               "file's timecode before converting its captions.")
        minutes = h * 60 + mi
        if mi % 10 and s == 0 and ff < drop:
            raise CaptionError(f"timecode {tc} names a frame drop-frame counting skips.")
        frames -= drop * (minutes - minutes // 10)
    return Fraction(frames) / fps


def file_start(info):
    """(timecode, where it was read) from ffprobe's output: the first video
    stream's "timecode" tag, else any other stream's, else the format's;
    (None, None) when there is none."""
    streams = info.get("streams") or []
    ordered = ([s for s in streams if s.get("codec_type") == "video"] +
               [s for s in streams if s.get("codec_type") != "video"])
    for s in ordered:
        tc = (s.get("tags") or {}).get("timecode")
        if tc:
            return tc, f"stream #{s.get('index')} ({s.get('codec_type')}) tag"
    tc = ((info.get("format") or {}).get("tags") or {}).get("timecode")
    return (tc, "format tag") if tc else (None, None)


def file_rate(info):
    """The first video stream's frame rate as a Fraction, or None."""
    for s in info.get("streams") or []:
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            return dc.rate(s.get("r_frame_rate")) or dc.rate(s.get("avg_frame_rate"))
    return None


# ---------------------------------------------------------------------------
# SubRip
# ---------------------------------------------------------------------------

def srt_time(seconds):
    """HH:MM:SS,mmm for a time at or after 0, rounded to the millisecond."""
    ms = int(round(Fraction(seconds) * 1000))
    if ms < 0:
        raise CaptionError(f"a cue time of {float(seconds):.3f} s is before 0.")
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_srt(cues):
    """SubRip text for cues [(start, end, text)]: numbered from 1, LF line
    ends, a blank line between cues."""
    blocks = [f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n"
              for n, (a, b, text) in enumerate(cues, 1)]
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# deliver_captions
# ---------------------------------------------------------------------------

def _pick(sidecars, track, file):
    if not sidecars:
        raise CaptionError(f"no Resolve caption sidecar beside {file}: expected "
                           f"'{os.path.splitext(os.path.basename(file))[0]}_<track>.ttml', which "
                           "a render with captions set to a separate file writes.")
    names = [t for t, _ in sidecars]
    if track is not None:
        found = [x for x in sidecars if x[0] == track]
        if not found:
            raise CaptionError(f"no sidecar for track '{track}' beside {file}; Resolve wrote "
                               f"track(s): {', '.join(names)}.")
        return found[0]
    if len(sidecars) > 1:
        raise CaptionError(f"{len(sidecars)} Resolve sidecars sit beside {file}, one per "
                           f"subtitle track ({', '.join(names)}); name the one to convert with "
                           "--track (track in the MCP tool).")
    return sidecars[0]


def _fmt_s(t):
    return f"{float(t):.3f} s"


def plan(path, dest, track=None):
    """Everything deliver_captions would do, read and checked, without
    writing: {file, destination, ttml, track, others, srt, cues (shifted),
    params, start, duration, warnings}. Raises CaptionError, or
    dc.ToolMissing when ffprobe is missing."""
    if dest.get("captions") != "sidecar":
        raise CaptionError(f"'{dest['key']}' takes {dest.get('captions')} captions; only a "
                           "sidecar destination gets an .srt, so there is nothing to convert.")
    dc.check_tools(ffmpeg=False)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise CaptionError(f"{path} is not a file.")
    folder = os.path.dirname(path)
    if paths.in_any_git_tree(folder):
        raise CaptionError(f"{folder} is inside a git working tree; deliverables and their "
                           "captions stay outside any repository.")
    srt = deliver.sidecar_path(path)
    if os.path.lexists(srt):
        raise CaptionError(f"{srt} already exists; nothing is overwritten. Move it away first "
                           "if it should be made again.")
    sidecars = deliver.resolve_sidecars(path)
    track, ttml = _pick(sidecars, track, path)
    try:
        with open(ttml, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise CaptionError(f"could not read {ttml}: {e}")
    parsed = parse_ttml(raw)
    cues = parsed["cues"]
    if not cues:
        raise CaptionError(f"{os.path.basename(ttml)} holds no caption text.")
    warnings = []
    if parsed["empty"]:
        warnings.append(f"{parsed['empty']} caption(s) with no text were left out.")

    info = dc.probe(path)
    duration = dc.duration(info)
    if duration is None:
        raise CaptionError(f"ffprobe could not read the duration of {path}, so the captions "
                           "cannot be checked against it.")
    fps = file_rate(info)
    tc, where = file_start(info)
    if tc:
        if fps is None:
            raise CaptionError(f"{path} has timecode {tc} but no readable video frame rate to "
                               "count it with.")
        offset = parse_timecode(tc, fps)
        if fps.denominator != 1:
            warnings.append(f"at {float(fps):.3f} fps the timecode label and real time part by "
                            "0.1% (3.6 s an hour); the start was counted in real time "
                            f"({_fmt_s(offset)}), which is not yet confirmed against a Resolve "
                            "render at this rate. Check the first caption against the picture.")
    else:
        if not 0 <= cues[0][0] < duration:
            raise CaptionError(f"{path} has no timecode tag to take off, and the first caption "
                               f"starts at {_fmt_s(cues[0][0])}, outside the file's "
                               f"{duration:.3f} s: the captions are timed from something this "
                               "file does not record.")
        offset = Fraction(0)
        warnings.append("the file has no timecode tag; the captions are taken as timed from "
                        "its first frame.")
    shifted = [(a - offset, b - offset, text) for a, b, text in cues]
    problems = dc.cue_time_problems([(float(a), float(b), t) for a, b, t in shifted], duration)
    if problems:
        raise CaptionError(f"after taking off the file's start ({tc or 'no timecode'}, "
                           f"{_fmt_s(offset)}) the captions do not fit the video: " +
                           "; ".join(problems) + ". Nothing was written.")
    others = [p for t, p in sidecars if p != ttml]
    return {"file": path, "destination": dest["key"], "ttml": ttml, "track": track,
            "others": others, "srt": srt, "cues": shifted, "params": parsed["params"],
            "start": {"timecode": tc, "read_from": where,
                      "fps": None if fps is None else str(fps),
                      "seconds": float(offset)},
            "duration": duration, "raw_sha": _sha(raw), "warnings": warnings}


def _sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _summary_of(p):
    cues = p["cues"]
    return {"count": len(cues), "first": [float(cues[0][0]), float(cues[0][1])],
            "last": [float(cues[-1][0]), float(cues[-1][1])]}


def deliver_captions(path, dest, track=None, keep_ttml=False, dry_run=False, expect_sha=None,
                     trash_root=None, now=None):
    """Convert Resolve's TTML sidecar beside `path` into <stem>.srt (see the
    module docstring). dry_run reads and checks everything and writes
    nothing; expect_sha, when given, refuses unless the plan still matches
    the dry run's plan_sha. Returns {file, destination, ttml, track, others,
    srt, cues {count, first, last}, start, duration, plan_sha, dry_run,
    written, verified, trashed, warnings, status}. Raises CaptionError (a
    delivercheck.CheckError) or delivercheck.ToolMissing."""
    from .workflows import plan_sha
    p = plan(path, dest, track)
    cues = p["cues"]
    sha = plan_sha("deliver_captions", p["file"], p["ttml"], p["raw_sha"], p["srt"],
                   p["start"], p["duration"], bool(keep_ttml))
    out = {k: p[k] for k in ("file", "destination", "ttml", "track", "others", "srt", "start",
                             "duration", "warnings")}
    out.update({"cues": _summary_of(p), "plan_sha": sha, "dry_run": bool(dry_run),
                "written": False, "verified": False, "trashed": None, "status": "planned"})
    if p["others"]:
        out["warnings"].append("other track(s) left beside the file: " +
                               ", ".join(os.path.basename(x) for x in p["others"]))
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise CaptionError("the plan changed since the dry run (the file, its sidecar or the "
                           "destination differ); run the dry run again and review it.")
    text = format_srt(cues)
    try:
        with open(p["srt"], "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except FileExistsError:
        raise CaptionError(f"{p['srt']} appeared while converting; nothing is overwritten.")
    except OSError as e:
        raise CaptionError(f"could not write {p['srt']}: {e}")
    out["written"] = True
    mismatch = _verify(p["srt"], cues)
    if mismatch:
        try:
            os.unlink(p["srt"])
            gone = "it was removed again"
        except OSError as e:
            gone = f"it could not be removed ({e}); delete it by hand"
        out["written"] = False
        raise CaptionError(f"{p['srt']} did not read back as written ({mismatch}); {gone}. The "
                           "TTML was left in place.")
    out["verified"] = True
    if not keep_ttml:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        try:
            out["trashed"] = dc._trash(p["ttml"], trash_root or os.path.expanduser("~/.Trash"),
                                       stamp, label=TRASH_LABEL)
        except (OSError, dc.CheckError) as e:
            raise CaptionError(f"{p['srt']} is written and verified, but {p['ttml']} could not "
                               f"be moved to the Trash ({e}); it is still beside the file. Move "
                               "it by hand, or run again with --keep-ttml next time.")
    out["status"] = "pass"
    return out


def _verify(srt, cues):
    """Why the .srt on disk differs from cues, or None."""
    try:
        with open(srt, encoding="utf-8") as f:
            back, problems = dc.parse_srt(f.read())
    except (OSError, UnicodeDecodeError) as e:
        return f"unreadable: {e}"
    if problems:
        return "; ".join(problems[:3])
    if len(back) != len(cues):
        return f"{len(back)} cue(s) read back, {len(cues)} written"
    for n, ((a, b, t), (a2, b2, t2)) in enumerate(zip(cues, back), 1):
        if t != t2:
            return f"cue {n}'s text differs"
        if abs(float(a) - a2) > 0.0015 or abs(float(b) - b2) > 0.0015:
            return f"cue {n}'s times differ ({a2:.3f} to {b2:.3f} s read back)"
    return None


def format_result(r):
    """The result as text lines for the CLI."""
    c, st = r["cues"], r["start"]
    lines = [f"{r['file']}  ->  {r['destination']}",
             f"  Sidecar:  {os.path.basename(r['ttml'])} (track '{r['track']}')",
             "  Start:    " + (f"timecode {st['timecode']} ({st['read_from']}) at {st['fps']} fps "
                               f"= {st['seconds']:.3f} s, taken off every cue"
                               if st["timecode"] else "no timecode tag; cues kept as they are"),
             f"  Cues:     {c['count']}, {c['first'][0]:.3f} s to {c['last'][1]:.3f} s; the "
             f"video runs {r['duration']:.3f} s"]
    if r["dry_run"]:
        lines.append(f"  Would write {r['srt']}; nothing written (dry run).")
    else:
        lines.append(f"  Wrote:    {r['srt']} (read back: {c['count']} cue(s), text and times "
                     "match)")
        lines.append(f"  TTML:     moved to {r['trashed']}" if r["trashed"] else
                     "  TTML:     kept beside the file")
    return "\n".join(lines) + "\n"
