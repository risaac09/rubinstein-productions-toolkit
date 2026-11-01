#!/usr/bin/env python3
"""vertcut cut — turn a cuts manifest + a source video into 9:16 captioned clips.

Reads a TSV manifest (id, in, out, title), slices the source, reframes to
1080x1920, burns word-grouped captions from a Whisper word-timestamp JSON, and
writes an .srt sidecar next to each export.

The words come through rpresolve.guard: text it withheld (singing, music, a
hallucination) is a [label], never words. A label is drawn as a bracketed cue
([singing], [music]) and a clip that overlaps singing or music is refused
unless --allow-music is given.

Captions are drawn as Pillow-rendered PNG overlays rather than through ffmpeg's
subtitles filter, because Homebrew's ffmpeg ships without libass.
"""

import argparse
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont

# The file-name rule and the destination live in rpresolve (production/), not here.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "production"))
from rpresolve import cutlist, deliver, delivercheck, hooklint, paths, reelab, reviewlog, safezone, vision  # noqa: E402
from rpresolve.config import ConfigError, load_config  # noqa: E402
from rpresolve.paths import in_any_git_tree  # noqa: E402
from rpresolve.tastedistill import read_events  # noqa: E402

W, H = 1080, 1920
DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
# Neutral stand-ins. Brand values (text colour, bar colour, caption face) reach the tool
# through --caption-style or the untracked captions.local.json beside this tool's README.
DEFAULT_TEXT_COLOR, DEFAULT_BAR_COLOR = "#FFFFFF", "#000000"
DEFAULT_BAR_OPACITY = 0.88          # the bar's opacity
LOCAL_STYLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captions.local.json")
STYLE_KEYS = ("text_color", "bar_color", "font")
LINE_CHARS, MAX_LINES = 16, 2       # the portrait standard
CAPTION_BOTTOM = 1680               # bottom edge of the bar: lower third (from 1280), clear of the feed UI
DEFAULT_LUFS, DEFAULT_TP, DEFAULT_LRA = -16.0, -1.5, 11.0   # the LinkedIn targets; -14 for YouTube and short-form feeds
DEFAULT_DEST = "linkedin_9x16"
# Wall time per second of clip, for the gate's estimate: the README measures transcription at about a
# third of real time on Apple silicon, and a veryfast x264 encode is assumed to cost no more.
RATIO = 1 / 3
GATE_MINUTES = 30                   # the review gate of production/media-review-loop.md
NTSC_30 = Fraction(30000, 1001)
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")


def die(msg):
    sys.stderr.write("vertcut: %s\n" % msg)
    raise SystemExit(1)


# ---------------------------------------------------------------- encoding

def loudnorm_filter(lufs=DEFAULT_LUFS, tp=DEFAULT_TP, lra=DEFAULT_LRA):
    """The ffmpeg loudnorm filter for a loudness target, in LUFS, true peak in dBTP."""
    if not -30.0 <= lufs <= -5.0:
        die("--lufs %g is outside -30 .. -5" % lufs)
    if not -9.0 <= tp <= 0.0:
        die("--tp %g is outside -9 .. 0" % tp)
    if not 1.0 <= lra <= 20.0:
        die("--lra %g is outside 1 .. 20" % lra)
    return "loudnorm=I=%g:TP=%g:LRA=%g" % (lufs, tp, lra)


def load_destination(key):
    """The destination `key` from resolve-config.json (with $RPRESOLVE_CONFIG laid over it, as
    deliver_check reads it), which names the file's aspect, audio and loudness."""
    try:
        return deliver.destination(load_config(os.environ.get("RPRESOLVE_CONFIG") or None, strict=True), key)
    except (ConfigError, deliver.DeliverError) as e:
        die(str(e))


def check_destination(dest):
    """vertcut writes a W x H AAC file, so a destination of another size or audio codec would get a
    name and audio settings its picture does not match. Refuse it before any work."""
    res = dest.get("resolution")
    if not isinstance(res, dict) or (res.get("width"), res.get("height")) != (W, H):
        die("destination '%s' is %s; vertcut writes %dx%d only"
            % (dest["key"], "%sx%s" % (res.get("width"), res.get("height"))
               if isinstance(res, dict) else res, W, H))
    codec = (dest.get("audio") or {}).get("codec")
    if codec not in (None, "aac"):
        die("destination '%s' takes %s audio; vertcut writes AAC only" % (dest["key"], codec))


def audio_codec_args(dest):
    """AAC at the destination's bitrate, sample rate and channel count."""
    a = dest.get("audio") or {}
    args = ["-c:a", "aac"]
    if a.get("bitrate_kbps"):
        args += ["-b:a", "%dk" % a["bitrate_kbps"]]
    if a.get("sample_rate"):
        args += ["-ar", str(a["sample_rate"])]
    if a.get("channels"):
        args += ["-ac", str(a["channels"])]
    return args


# Written explicitly so an untagged source still yields a tagged file (deliver_check asserts the
# transfer tag). The picture is Rec. 709, limited range, as the destination's colour block says.
# setparams is what makes the primaries and transfer reach the file: with only the encoder flags,
# ffmpeg 9 wrote the matrix and range but left primaries and transfer unset on an untagged source.
COLOR_TAG_FILTER = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
COLOR_TAG_ARGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                  "-color_range", "tv"]


def srt_path(out, dest):
    """Where the .srt goes. Beside the file (deliver.sidecar_path) unless the destination burns its
    captions in: deliver_check fails a burn-in file with any caption file beside it, so that srt
    goes to a review-srt/ folder inside the output folder instead."""
    path = deliver.sidecar_path(out)
    if dest.get("captions") != "burnin":
        return path
    folder = os.path.join(os.path.dirname(out), "review-srt")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, os.path.basename(path))


def write_sidecar(path, text):
    """Write a caption or hook file as every private output is written: refused when its real path (a
    review-srt folder that is a symlink counts by where it leads) is inside a repository."""
    try:
        paths.write_private(path, text)
    except (paths.OutputRefused, OSError) as e:
        die("%s: %s" % (path, e))


def clip_name(dest, parts, title):
    """The house file name for one clip: parts are show, episode, guest, index. The slug comes
    from the title. Exits with the rule's own message when a part breaks it."""
    try:
        return deliver.name_for(dest, dict(parts, slug=slug(title, "clip")))
    except deliver.NameRuleError as e:
        die("clip name: %s" % e)


def video_codec_args(crf, preset):
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", preset]


def out_rate(src_fps):
    """The output frame rate, as a Fraction: the source's, capped at 30. A source above 30 in the NTSC
    family (59.94) goes out at 30000/1001, not 30; any other above 30 goes out at 30."""
    fr = Fraction(src_fps).limit_denominator(1001)
    if fr <= 30:
        return fr
    return NTSC_30 if fr.denominator == 1001 else Fraction(30)


# ---------------------------------------------------------------- timecodes

def parse_tc(s, fps):
    """Accept SS.mmm, MM:SS, HH:MM:SS(.mmm), or DaVinci HH:MM:SS:FF."""
    s = s.strip()
    if not s:
        die("empty timecode")
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:   # a drop-frame HH:MM:SS;FF lands here
        die("not a timecode: %s (use SS.mmm, MM:SS, HH:MM:SS or HH:MM:SS:FF)" % s)
    if len(parts) == 4:
        try:  # NTSC-correct: frames count at the nominal rate, then divide by the real one
            return cutlist.parse_timecode(s, fps)
        except cutlist.CutlistError as e:
            die(str(e))
    die("unparseable timecode: %s" % s)


def srt_tc(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def slug(text, fallback):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:deliver.SLUG_MAX].strip("-") or fallback


# ---------------------------------------------------------------- manifest

def read_manifest(path, fps):
    rows = []
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = [c.strip() for c in line.split("\t") if c.strip() != ""]
            if len(cols) < 3:  # tolerate space-aligned manifests
                cols = line.split(None, 3)
            if len(cols) < 3:
                die("%s:%d needs at least id<TAB>in<TAB>out" % (path, lineno))
            cid, tin, tout = cols[0], cols[1], cols[2]
            title = cols[3] if len(cols) > 3 else ""
            hook = cols[4] if len(cols) > 4 else ""     # optional; needs a title, as empty columns are dropped
            start, end = parse_tc(tin, fps), parse_tc(tout, fps)
            if end <= start:
                die("%s:%d out (%s) is not after in (%s)" % (path, lineno, tout, tin))
            rows.append({"id": cid, "start": start, "end": end, "title": title, "hook": hook})
    if not rows:
        die("no clips in %s" % path)
    return rows


# ---------------------------------------------------------------- probing

def probe(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "json", src],
        capture_output=True, text=True)
    if out.returncode != 0:
        die("ffprobe failed on %s: %s" % (src, out.stderr.strip()))
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        die("no video stream in %s" % src)
    st = streams[0]
    num, _, den = (st.get("r_frame_rate") or "0/0").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return int(st["width"]), int(st["height"]), (fps or 25.0)


def has_audio_stream(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=index", "-of", "csv=p=0", src],
        capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.strip() != ""


# ---------------------------------------------------------------- captions

def load_words(path):
    """The spoken words of a transcript, [{start, end, word}] in source-time
    order, read through rpresolve.guard (cutlist.load_words): a segment the
    guard withheld (singing, music, a hallucination) carries no words, and
    cutlist.blocks_of(words) says where they were. A .raw.json is refused."""
    try:
        words = cutlist.load_words(path)
    except cutlist.CutlistError as e:
        die(str(e))
    if not words.audio_checked:
        sys.stderr.write("vertcut: warning: %s was not screened for singing from audio "
                         "(run `vertcut transcribe`, or `vertcut kinds`)\n" % os.path.basename(path))
    return words


# A withheld uncertain span is sung-leaning (or next to a sung one): refused like
# singing, and labelled with it under --labels music.
REFUSE_KINDS = ("sung", "music", "uncertain")
# --labels: which withheld spans get a bracketed cue on the clip
LABEL_SETS = {"none": (), "music": REFUSE_KINDS,
              "all": ("sung", "music", "hallucination", "uncertain")}


def music_overlaps(blocks, start, end):
    """The withheld spans of singing or music that [start, end] overlaps."""
    return [b for b in blocks if b["kind"] in REFUSE_KINDS
            and b["start"] < end and b["end"] > start]


def label_cues(blocks, start, end, kinds):
    """Caption cues for withheld spans, rebased to the clip: the bracketed
    label, never words."""
    dur = end - start
    out = []
    for b in blocks:
        if b["kind"] not in kinds or b["end"] <= start or b["start"] >= end:
            continue
        out.append({"start": max(0.0, b["start"] - start),
                    "end": min(dur, b["end"] - start), "text": b["label"], "label": True})
    return [c for c in out if c["end"] > c["start"]]


def group_cues(words, start, end, max_words, max_chars, gap, line_chars=LINE_CHARS,
               blocks=(), label_kinds=()):
    """Slice words to [start,end], rebase to 0, and group into caption cues.
    Withheld spans in `blocks` whose kind is in label_kinds become bracketed
    label cues ([singing], [music], [inaudible]) in time order."""
    dur = end - start
    picked = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        picked.append({
            "start": max(0.0, w["start"] - start),
            "end": min(dur, w["end"] - start),
            "word": w["word"],
        })
    cues, cur = [], []

    def flush():
        if cur:
            cues.append({
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "text": " ".join(x["word"] for x in cur),
            })
            del cur[:]

    for w in picked:
        if len(w["word"]) > line_chars:
            raise CaptionError("the word %r is %d characters; a caption line holds %d "
                               "(raise --line-chars or fix the transcript)"
                               % (w["word"], len(w["word"]), line_chars))
        if cur:
            joined = " ".join([x["word"] for x in cur] + [w["word"]])
            if (len(cur) >= max_words or len(joined) > max_chars
                    or len(wrap_chars(joined, line_chars)) > MAX_LINES
                    or w["start"] - cur[-1]["end"] > gap):
                flush()
        cur.append(w)
    flush()

    # Close small gaps so captions don't flicker between cues.
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else dur
        if nxt - c["end"] < 0.25:
            c["end"] = nxt
        c["end"] = max(c["end"], c["start"] + 0.35)
        c["end"] = min(c["end"], dur)
    cues = [c for c in cues if c["end"] > c["start"]]
    if blocks and label_kinds:
        cues = sorted(cues + label_cues(blocks, start, end, label_kinds),
                      key=lambda c: (c["start"], c["end"]))
        for i, c in enumerate(cues[:-1]):       # a label never runs over the next cue
            c["end"] = min(c["end"], cues[i + 1]["start"]) if c.get("label") else c["end"]
        cues = [c for c in cues if c["end"] > c["start"]]
    return cues


class CaptionError(ValueError):
    pass


def wrap_chars(text, limit):
    """Greedy wrap by character count. A word longer than the limit gets its own line."""
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if len(trial) <= limit or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def parse_color(value, what):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value).strip())
    if not m:
        raise CaptionError("%s must be a #RRGGBB colour, got %r" % (what, value))
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def resolve_style(file_path, text_color, bar_color, font):
    """Defaults, then the style file, then flags. The file is --caption-style, else the
    untracked captions.local.json if it exists."""
    style = {"text_color": DEFAULT_TEXT_COLOR, "bar_color": DEFAULT_BAR_COLOR, "font": DEFAULT_FONT}
    path = file_path or (LOCAL_STYLE if os.path.isfile(LOCAL_STYLE) else None)
    if path:
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise CaptionError("cannot read caption style %s: %s" % (path, e))
        if not isinstance(data, dict):
            raise CaptionError("caption style %s must be a JSON object" % path)
        unknown = sorted(set(data) - set(STYLE_KEYS))
        if unknown:
            raise CaptionError("caption style %s: unknown keys %s (allowed: %s)"
                               % (path, ", ".join(unknown), ", ".join(STYLE_KEYS)))
        style.update({k: v for k, v in data.items() if v})
    for key, val in (("text_color", text_color), ("bar_color", bar_color), ("font", font)):
        if val:
            style[key] = val
    style["text_rgb"] = parse_color(style["text_color"], "text colour")
    style["bar_rgb"] = parse_color(style["bar_color"], "bar colour")
    return style


def cue_size(lines, font):
    """(width, height, line height, pad x, pad y) of a cue's bar."""
    scratch = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * 1.15)
    pad_x, pad_y = font.size // 2, font.size // 4
    return (int(max(scratch.textlength(l, font=font) for l in lines)) + pad_x * 2,
            lh * len(lines) + pad_y * 2, lh, pad_x, pad_y)


def cue_box(lines, font, caption_y, frame=(W, H)):
    """Pixel box (x0, y0, x1, y1) of a cue's bar: centred, its bottom edge at caption_y."""
    width, height = cue_size(lines, font)[:2]
    x0 = (frame[0] - width) / 2
    return x0, caption_y - height, x0 + width, caption_y


def unsafe_sides(box, frame=(W, H)):
    """The sides of the safezone (rpresolve.safezone) a pixel box crosses, e.g. ['bottom']."""
    fw, fh = frame
    x0, y0, x1, y1 = box
    return safezone.outside((x0 / fw, y0 / fh, x1 / fw, y1 / fh), safezone.margins(fw, fh))


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def hook_lines(text):
    """A hook's lines: `|` starts the next line."""
    return [p.strip() for p in text.split("|") if p.strip()]


def hook_hold(lines):
    """Seconds on screen: the least hooklint accepts, max(0.8, 0.3 per word)."""
    n = len([w for w in " ".join(lines).split() if re.search(r"\w", w)])
    return max(hooklint.HOLD_FLOOR, hooklint.HOLD_PER_WORD * n)


def plan_hook(lines, font, caption_y, line_chars):
    """Pixel box of the hook card: top-centred at the top safezone edge. Raises CaptionError when it
    leaves the portrait margins or meets the caption bar (two full lines, the largest it can be; none
    when caption_y is None)."""
    width, height = cue_size(lines, font)[:2]
    x0, y0 = (W - width) // 2, math.ceil(H * safezone.PORTRAIT["top"])
    box = (x0, y0, x0 + width, y0 + height)
    sides = unsafe_sides(box)
    if sides:
        raise CaptionError("the hook card crosses the %s safezone; shorten the lines or lower --font-size"
                           % "/".join(sides))
    if caption_y is not None and overlaps(box, cue_box(["M" * line_chars] * MAX_LINES, font, caption_y)):
        raise CaptionError("the hook card meets the caption bar; raise --caption-y or lower --font-size")
    return box


def hook_covers_face(box, faces):
    """True when the pixel box overlaps any Vision face (normalized, top-left origin)."""
    return any(overlaps(box, (f["x"] * W, f["y"] * H, (f["x"] + f["w"]) * W, (f["y"] + f["h"]) * H))
               for f in faces)


def edge_padded(img, canvas, at):
    """img on a canvas of transparent pixels that keep the nearest edge pixel's colour. ffmpeg's yuv420
    overlay subsamples chroma with a filter that reads past the picture's edge, where it repeats the edge
    pixel; black padding would tint the chroma at the bar's border and differ from a cue overlaid alone."""
    (w, h), (cw, ch), (x, y) = img.size, canvas, at
    out = Image.new("RGBA", canvas)
    out.paste(img, at)
    for box, src, size in (((x, 0), (x, y, x + w, y + 1), (w, y)), ((x, y + h), (x, y + h - 1, x + w, y + h), (w, ch - y - h))):
        if size[1]:
            out.paste(out.crop(src).resize(size, Image.NEAREST), box)
    for box, src, size in (((0, 0), (x, 0, x + 1, ch), (x, ch)), ((x + w, 0), (x + w - 1, 0, x + w, ch), (cw - x - w, ch))):
        if size[0]:
            out.paste(out.crop(src).resize(size, Image.NEAREST), box)
    alpha = Image.new("L", canvas, 0)
    alpha.paste(img.getchannel("A"), at)
    out.putalpha(alpha)
    return out


def render_cue(lines, font, path, style, opacity, canvas=None, at=(0, 0)):
    """Draw one cue: text on a partial-opacity bar. Returns (width, height). With canvas (width, height)
    the file is that size, the bar pasted at `at` on transparent pixels, so cues share one overlay."""
    width, height, lh, pad_x, pad_y = cue_size(lines, font)
    if width > W:
        raise CaptionError("a caption line is %d px wide, wider than the %d px frame; "
                           "lower --font-size or --line-chars" % (width, W))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dd = ImageDraw.Draw(img)
    dd.rounded_rectangle((0, 0, width - 1, height - 1), radius=max(2, font.size // 16),
                         fill=style["bar_rgb"] + (round(opacity * 255),))
    for i, line in enumerate(lines):
        lw = dd.textlength(line, font=font)
        dd.text(((width - lw) / 2, pad_y + i * lh), line, font=font,
                fill=style["text_rgb"] + (255,))
    if canvas:
        img = edge_padded(img, canvas, at)
    img.save(path)
    return width, height


# image2 stamps a still at 25 frames a second unless told otherwise, which moves a cue's switch by up to
# 20 ms; a thousand a second leaves it where the list puts it.
FINE = "option framerate 1000\n"


def phase_of(start, fps):
    """Seconds from a clip's start to its first frame: the source's frames fall on k / fps, and the cut
    keeps the first one at or after `start`."""
    fd = 1.0 / float(fps)
    return math.ceil(start / fd - 1e-9) * fd - start


def cue_streams(cues, lines_of, font, caption_y, tmp, style, opacity, fps, phase=0.0):
    """The cues as at most four ffmpeg inputs however many there are, so the filter graph stays one size
    for 3 cues or 3000 (one input and overlay per cue grew without bound). Each stream is a concat list
    that holds a transparent canvas per cue for the frames it shows on and a blank one between. ffmpeg's yuv420 overlay subsamples the picture's chroma from the picture's own origin, so a
    cue keeps the grid it had alone: cues are grouped by the parity of their x and y, and each group's
    canvas starts on that parity, with the cue at an even offset in it. Returns [(list path, x, y)]
    for `overlay=x:y`."""
    ch = max(cue_size(lines_of(c), font)[1] for c in cues)
    fd = 1.0 / float(fps)     # one source frame; shortcut: a variable-rate source drifts off this grid
    # Frame indices [first, stop) each cue shows on, as the enable window it replaces did (a frame at or
    # after the start, up to and including the end), cut at the next cue's first frame whatever its group.
    # Frame j is at phase + j * fd, phase being how far the clip's first frame sits past its own start.
    firsts = [math.ceil((round(c["start"], 3) - phase) / fd - 1e-9) for c in cues]
    spans = {}
    for i, c in enumerate(cues):
        stop = math.floor((round(c["end"], 3) - phase) / fd + 1e-9) + 1
        if i + 1 < len(cues):
            stop = min(stop, firsts[i + 1])
        if stop > firsts[i]:
            spans[i] = (firsts[i], stop)
    groups = {}
    for i, c in enumerate(cues):
        w, h = cue_size(lines_of(c), font)[:2]
        groups.setdefault((int((W - w) / 2) % 2, (caption_y - h) % 2), []).append(i)
    out = []
    for (qx, qy), members in sorted(groups.items()):
        x0 = -qx                                              # origin on parity qx, at or left of 0
        y0 = caption_y - ch - ((caption_y - ch - qy) % 2)     # origin on parity qy, at or above the tallest bar
        canvas = (W + qx, caption_y - y0)
        blank = os.path.join(tmp, "blank%d%d.png" % (qx, qy))
        Image.new("RGBA", canvas, (0, 0, 0, 0)).save(blank)
        rows, done = [], 0.0

        def at_of(frame):
            # The stream switches a quarter of a frame before the frame it first applies to: not on the frame
            # time (a microsecond of rounding would move it a frame) and not at the midpoint between two
            # (where ffmpeg's frame sync can pick either); rounded as a running total, so entries do not drift.
            return round(max(0.0, phase + (frame - 0.25) * fd), 6)

        def hold_until(png, frame):
            nonlocal done
            rows.append("file '%s'\n%sduration %.6f" % (png, FINE, at_of(frame) - done))
            done = at_of(frame)

        for i in members:
            if i not in spans:
                continue
            first, stop = spans[i]
            if at_of(first) > done:
                hold_until(blank, first)
            lines = lines_of(cues[i])
            w, h = cue_size(lines, font)[:2]
            png = os.path.join(tmp, "cue%03d.png" % i)
            render_cue(lines, font, png, style, opacity, canvas, (int((W - w) / 2) - x0, caption_y - h - y0))
            hold_until(png, stop)
        rows.append("file '%s'\n%sduration 3600" % (blank, FINE))
        rows.append("file '%s'\n%s" % (blank, FINE.strip()))    # concat ignores the last entry's duration; repeat it
        path = os.path.join(tmp, "cues%d%d.ffconcat" % (qx, qy))
        with open(path, "w") as fh:
            fh.write("ffconcat version 1.0\n" + "\n".join(rows) + "\n")
        out.append((path, x0, y0))
    return out


# ---------------------------------------------------------------- reframing

def reframe_chain(src_w, src_h, mode, xoff):
    """Return (filter string producing [base], effective mode)."""
    if mode == "crop":
        scaled_w = int(round(src_w * H / src_h / 2)) * 2
        if scaled_w < W:
            mode = "fit"
        else:
            span = scaled_w - W
            x = int(round(span * (0.5 + xoff / 2.0)))
            x = max(0, min(span, x))
            return ("[0:v]scale=%d:%d:flags=lanczos,crop=%d:%d:%d:0,setsar=1[base]"
                    % (scaled_w, H, W, H, x)), "crop"
    return (
        "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
        "boxblur=24:2,eq=brightness=-0.12[bg];"
        "[0:v]scale=%d:-2:flags=lanczos[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]" % (W, H, W, H, W)
    ), "fit"


def plan_chain(plan_path, src, src_w, src_h, rows, fps, offset):
    """{row id: filter string producing [base]} from a reframe-plan manifest copy (its --out file).
    A worksheet row takes the 9x16 crop of the one plan span that holds it: the row's media time
    (in/out plus --offset) must lie inside the span's in/out, within a frame. Time, not the clip
    name, because the worksheet's ids are free text and the plan's spans are the only thing that
    says which footage a crop was judged on; when two spans hold a row, the clip named for the
    row's id wins. Refused, never guessed: a plan made for another source, another picture size, a
    squeezed or barred crop, a crop that left the frame, a moving crop, a row no span holds."""
    try:
        with open(plan_path, encoding="utf-8") as fh:
            m = json.load(fh)
        cutlist.validate_manifest(m, allow_no_speech=True)
    except (OSError, ValueError, cutlist.CutlistError) as e:
        die("--plan %s: %s" % (plan_path, e))
    if "9x16" not in ((m.get("reframe_plan") or {}).get("aspects") or []):
        die("--plan %s has no 9x16 crops; make it with reframe-plan (--aspects 9x16)" % plan_path)
    tol = 1.0 / fps
    real = os.path.realpath(src)
    held = [(c, sp) for c in m["clips"] for sp in c["spans"]
            if os.path.realpath(cutlist.source_for(m, sp)["path"]) == real]
    if not held:
        die("--plan %s was made for another source file than %s" % (plan_path, src))
    out = {}
    for row in rows:
        a, b = row["start"] + offset, row["end"] + offset
        hits = [(c, sp) for c, sp in held if sp["in"] - tol <= a and b <= sp["out"] + tol]
        if len(hits) > 1:
            hits = [h for h in hits if h[0]["name"] == row["id"]] or hits
        crops = {json.dumps((sp.get("reframe") or {}).get("9x16"), sort_keys=True) for _, sp in hits}
        if not hits or len(crops) > 1:
            die("row %s (%.2f-%.2f s): %s" % (row["id"], a, b, "no plan span holds it, so there is no crop"
                if not hits else "several plan spans hold it with different crops; split the row"))
        c, sp = hits[0]
        e = (sp.get("reframe") or {}).get("9x16")
        why = None
        if not e:
            why = "its span has no 9x16 crop"
        elif e.get("status") not in ("pass", "fallback") or e.get("bars"):
            why = "its crop is %s%s, not a crop that fills the frame" % (e.get("status"), " with bars" if e.get("bars") else "")
        elif any(isinstance(e.get(k), (list, dict)) for k in ("x", "y", "scale")) or e.get("keyframes"):
            why = "its crop moves; only a static crop is read"
        elif e.get("x") is None or e.get("y") is None or not e.get("scale"):
            why = "its crop has no x, y and scale"
        elif (e.get("stored") and list(e["stored"]) != list(e.get("src") or [])) or e.get("squeeze") not in (None, 1, 1.0):
            why = "its source is squeezed, and vertcut does not desqueeze"
        elif list(e.get("src") or []) != [src_w, src_h]:
            why = "it was planned on a %s picture and this source is %dx%d" % (
                "x".join(str(v) for v in e.get("src") or ["?"]), src_w, src_h)
        elif e.get("out") and list(e["out"]) != [W, H]:
            why = "its crop was planned for a %sx%s frame, not %dx%d" % (e["out"][0], e["out"][1], W, H)
        if why:
            die("row %s: plan span %s-%s of clip %s: %s" % (row["id"], sp["in"], sp["out"], c["name"], why))
        cw, chh = W / float(e["scale"]), H / float(e["scale"])
        x0, y0 = float(e["x"]) - cw / 2, float(e["y"]) - chh / 2
        if x0 < -0.5 or y0 < -0.5 or x0 + cw > src_w + 0.5 or y0 + chh > src_h + 0.5:
            die("row %s: plan crop %.0fx%.0f at (%.0f, %.0f) leaves the %dx%d picture" % (row["id"], cw, chh, x0, y0, src_w, src_h))
        iw, ih = min(int(round(cw)), src_w), min(int(round(chh)), src_h)
        ix, iy = max(0, min(src_w - iw, int(round(x0)))), max(0, min(src_h - ih, int(round(y0))))
        out[row["index"]] = "[0:v]crop=%d:%d:%d:%d,scale=%d:%d:flags=lanczos,setsar=1[base]" % (iw, ih, ix, iy, W, H)
    return out


def frame0_faces(src, start, chain, tmp):
    """Vision face boxes on the clip's reframed first frame, or None off the Mac (the check is skipped)."""
    png = os.path.join(tmp, "frame0.png")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "%.3f" % start, "-i", src,
                        "-filter_complex", chain, "-map", "[base]", "-frames:v", "1", png])
    if r.returncode != 0:
        die("could not read frame 0 of %s" % src)
    if sys.platform != "darwin" or not vision.vision_binary():
        return None
    found = vision.face_boxes([png])
    return None if found is None else found.get(png, [])


# ---------------------------------------------------------------- main

def gate_problem(args, spans, outs, estimate):
    """None when the batch may run; else why it may not. A batch over GATE_MINUTES, or one that replaces
    an existing output, needs a gate-packet that Isaac answered go in the review log."""
    replaced = [o for o in outs if os.path.exists(o)]
    if estimate <= GATE_MINUTES * 60 and not replaced:
        return None
    why = ("estimated %.0f min is over the %d min gate" % (estimate / 60, GATE_MINUTES)
           if estimate > GATE_MINUTES * 60 else "%s already exists" % os.path.basename(replaced[0]))
    cmd = "python3 production/resolve_workflow.py gate-packet --out GATE_DIR --project P-0000 --source %s --cuts %s" % (
        shlex.quote(args.src), shlex.quote(args.manifest))
    if args.offset:
        cmd += " --offset %s" % args.offset
    hint = ("%s. Build a gate-packet, read it, log go with review-log --step gate --choice go --key GATE_DIR, "
            "then pass --reviewed GATE_DIR/gate.json --review-log PROJECT_DIR/review/events.jsonl:\n  %s" % (why, cmd))
    if not (args.reviewed and args.review_log):
        return hint
    for f in (args.reviewed, args.review_log):
        if not os.path.isfile(f):
            return "no such file: %s" % f
        if in_any_git_tree(f):
            return "%s is inside a git work tree; keep the gate and the review log outside any repository" % f
    try:
        with open(args.reviewed, encoding="utf-8") as fh:
            gate = json.load(fh)
        packet = os.path.join(os.path.dirname(os.path.abspath(args.reviewed)), "packet.json")
        if reviewlog._sha256(packet) != gate["packet_sha256"]:
            return "gate.json does not match its packet.json; build the packet again"
        if reviewlog._sha256(args.manifest) not in {i.get("sha256") for i in gate["inputs"]}:
            return "gate.json was not built from this cuts file; build it with --source and --cuts"
        if reviewlog._sha256(args.src) not in {i.get("sha256") for i in gate["inputs"]}:
            return "gate.json was not built from this source file; build it with --source and --cuts"
        if gate["total_runtime_s"] + 1 < sum(spans):
            return "the gate-packet covers %.0f s of clips, this batch is %.0f s" % (gate["total_runtime_s"], sum(spans))
        events, _ = read_events(args.review_log)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return "cannot read the gate: %s: %s" % (type(e).__name__, e)
    answers = [e for _, e in events if e.get("step") == "gate" and e.get("stimulus") == gate["packet_sha256"]
               and e.get("project") == gate.get("project")]
    if not answers or answers[-1].get("choice") != "go":
        return "no go for this gate-packet in %s (latest answer: %s)\n  %s" % (
            args.review_log, answers[-1].get("choice") if answers else "none", hint)
    return None


REVIEW_VALUE_FLAGS = ("--review-seed", "--variant", "--ledger-row")


def parse_variant(spec):
    """One --variant: comma-separated x=N, style=FILE, hook=on|off. Only what is named changes."""
    opts = {}
    for part in spec.split(","):
        k, _, v = part.strip().partition("=")
        if not (k in ("x", "style") and v) and not (k == "hook" and v in ("on", "off")) or k in opts:
            die("--variant %r: use x=N, style=FILE, hook=on|off, each once" % spec)
        opts[k] = v
    return opts


def child_args(argv, opts):
    """The cut command line for one variant: this run's own arguments without the review flags, then the
    variant's, which win as the later flags."""
    out, i = [], 0
    while i < len(argv):
        name = argv[i].split("=", 1)[0]
        if name == "--review":
            i += 1
        elif name in REVIEW_VALUE_FLAGS:
            i += 1 if "=" in argv[i] else 2
        else:
            out.append(argv[i])
            i += 1
    if "x" in opts:
        out += ["--x=" + opts["x"]]    # = form: a negative number is not read as a flag
    if "style" in opts:
        out += ["--caption-style", opts["style"]]
    if opts.get("hook") == "off":
        out.append("--no-hook")
    return out


def review(args, argv):
    """Render 2 to 4 variants of one worksheet row and hand them to rpresolve.reelab: a lettered,
    loudness-matched packet in -o DIR, its sealed key in DIR.key. Log the answer with review-log."""
    variants = [parse_variant(v) for v in args.variant]
    if not 2 <= len(variants) <= 4:
        die("--review takes 2 to 4 --variant options, got %d" % len(variants))
    if len({tuple(sorted(v.items())) for v in variants}) < len(variants):
        die("two --variant options are the same; a variant differs from the others in what it names")
    if not args.only or "," in args.only:
        die("--review takes --only with one clip id")
    if args.dry_run or args.no_hook or args.ledger_row or args.reviewed:
        die("--review renders every variant as asked; drop --dry-run, --no-hook, --ledger-row and --reviewed")
    row = next((c for c in read_manifest(args.manifest, args.fps or probe(args.src)[2]) if c["id"] == args.only.strip()), None)
    if row is None:
        die("--only matched no clip ids")
    if any(v.get("hook") == "on" for v in variants) and not (row["hook"] or args.hook_text):
        die("hook=on, but clip %s has no hook (a fifth worksheet column or --hook-text)" % row["id"])
    tmp = tempfile.mkdtemp(prefix="vertcut-review-")
    try:
        files = []
        for k, (spec, opts) in enumerate(zip(args.variant, variants)):
            d = os.path.join(tmp, str(k))
            r = subprocess.run([sys.executable, os.path.abspath(__file__)] + child_args(argv, opts) + ["-o", d])
            made = [f for f in os.listdir(d) if f.endswith(".mp4")] if r.returncode == 0 else []
            if len(made) != 1:
                die("variant %r did not render" % spec)
            named = os.path.join(tmp, "variant%d-%s.mp4" % (k + 1, re.sub(r"[^A-Za-z0-9.-]+", "_", spec.replace("=", "-"))))
            os.rename(os.path.join(d, made[0]), named)   # review-log records these names beside each letter
            files.append(named)
        seconds = min(reelab.MAX_SECONDS, min(reelab._inspect(f)[2] for f in files) - 0.05)
        if seconds <= 0:
            die("clip %s is too short for a review packet" % row["id"])
        packet = reelab.build(files, args.outdir, 0.0, seconds, args.review_seed)
    except (reelab.ReelAbError, delivercheck.ToolMissing, delivercheck.CheckError, OSError) as e:
        die(str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("vertcut: packet %s (%d variants, spread %s LU); key in %s"
          % (args.outdir, len(variants), packet["spread_lu"], reelab.key_dir(args.outdir)))
    print("vertcut: log the answer: python3 production/resolve_workflow.py review-log --dir PROJECT_DIR "
          "--project P-0000 --step edit --device phone --choice LETTER --key %s --question reel_option" % args.outdir)


def main():
    ap = argparse.ArgumentParser(prog="vertcut cut", add_help=True, allow_abbrev=False)
    ap.add_argument("src")
    ap.add_argument("manifest")
    ap.add_argument("-o", "--outdir", default="vertcut-out")
    ap.add_argument("--words", help="safe transcript JSON from `vertcut transcribe` "
                                    "(a raw Whisper JSON is screened in memory; a .raw.json is refused)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--allow-music", action="store_true",
                    help="render a clip that overlaps singing or music; its words stay "
                         "withheld and the span gets a bracketed label card")
    ap.add_argument("--labels", choices=sorted(LABEL_SETS), default="music",
                    help="which withheld spans get a [label] card: music = [singing] and "
                         "[music] (default), all = also [inaudible], none = no cards")
    ap.add_argument("--frame", choices=["crop", "fit"], help="default crop")
    ap.add_argument("--x", type=float, default=0.0,
                    help="crop offset, -1 (full left) .. 1 (full right)")
    ap.add_argument("--plan", metavar="PLAN.json",
                    help="a reframe-plan manifest copy: each row takes the static 9x16 crop of the plan span "
                         "that holds it, in place of --x; a row no span holds, or a plan for another source, is refused")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="seconds to add to every manifest timecode, when the "
                         "manifest's zero is not the media file's zero; verify "
                         "the sign on one clip before rendering the set")
    ap.add_argument("--fps", type=float, help="frame rate for HH:MM:SS:FF timecodes")
    ap.add_argument("--caption-style", metavar="JSON",
                    help="local file with text_color, bar_color and font (default: "
                         "tools/vertcut/captions.local.json if present; never committed)")
    ap.add_argument("--text-color", help="#RRGGBB, over the style file")
    ap.add_argument("--bar-color", help="#RRGGBB, over the style file")
    ap.add_argument("--bar-opacity", type=float, default=DEFAULT_BAR_OPACITY,
                    help="caption bar opacity, 0-1 (default %g)" % DEFAULT_BAR_OPACITY)
    ap.add_argument("--font", help="font file, over the style file (default Arial Bold)")
    ap.add_argument("--font-size", type=int, default=66)
    ap.add_argument("--caption-y", type=int, default=CAPTION_BOTTOM,
                    help="y of the caption bar's bottom edge (default %d)" % CAPTION_BOTTOM)
    ap.add_argument("--hook-text", metavar="TEXT",
                    help="frame-0 hook, your own words verbatim, `|` between its two lines; checked with "
                         "rpresolve.hooklint (exit 2 on a FAIL). A worksheet's fifth column sets one per clip")
    ap.add_argument("--hook-source", metavar="REF", help="where the hook text comes from; required with a hook")
    ap.add_argument("--line-chars", type=int, default=LINE_CHARS,
                    help="characters per caption line, two lines at most (default %d)" % LINE_CHARS)
    ap.add_argument("--max-words", type=int, default=3)
    ap.add_argument("--max-chars", type=int, default=LINE_CHARS * MAX_LINES)
    ap.add_argument("--gap", type=float, default=0.6, help="silence that breaks a cue")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--preset", choices=X264_PRESETS, default="veryfast",
                    help="x264 preset (slower is smaller at the same quality)")
    ap.add_argument("--lufs", type=float,
                    help="integrated loudness target (default: the destination's, -16 for "
                         "linkedin_9x16; %g when the destination names none)" % DEFAULT_LUFS)
    ap.add_argument("--tp", type=float, default=DEFAULT_TP, help="true-peak ceiling in dBTP (default %g)" % DEFAULT_TP)
    ap.add_argument("--lra", type=float, default=DEFAULT_LRA, help="loudness range target (default %g)" % DEFAULT_LRA)
    ap.add_argument("--only", help="comma-separated clip ids to render")
    ap.add_argument("--review", action="store_true",
                    help="render the --variant options of the one --only clip and write a blind, loudness-matched "
                         "A/B packet to -o DIR (key in DIR.key); log the answer with review-log")
    ap.add_argument("--variant", action="append", default=[], metavar="SPEC",
                    help="with --review, 2 to 4 times: comma-separated x=N, style=FILE, hook=on|off")
    ap.add_argument("--review-seed", type=int, help="letter-order seed for --review (default: random, kept in the key)")
    ap.add_argument("--no-hook", action="store_true", help="draw no hook, whatever the worksheet or --hook-text says")
    ap.add_argument("--ledger-row", metavar="PATH",
                    help="write a release-ledger row stub per clip (JSON lines, from delivercheck.ledger_row) "
                         "to this file, outside any repository")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reviewed", metavar="GATE_JSON",
                    help="gate.json of a gate-packet answered go; a batch over %d min or a replacement needs it" % GATE_MINUTES)
    ap.add_argument("--review-log", metavar="EVENTS_JSONL", help="the review log that holds the go line, with --reviewed")
    ap.add_argument("--destination", default=DEFAULT_DEST,
                    help="destination in resolve-config.json that sets the file name's aspect "
                         "and the audio format (default %s)" % DEFAULT_DEST)
    ap.add_argument("--show", help="show code for the house file name, such as SW")
    ap.add_argument("--episode", help="episode number for the house file name, such as 1 or 001")
    ap.add_argument("--guest", help="guest name for the house file name, letters and digits only")
    ap.add_argument("--draft-names", action="store_true",
                    help="name files <id>-<title-slug>.mp4 instead of the house name; these "
                         "fail deliver_check, so use them for review copies only")
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        die("no such file: %s" % args.src)
    if args.review:
        return review(args, sys.argv[1:])
    if args.variant or args.review_seed is not None:
        die("--variant and --review-seed go with --review")
    if args.ledger_row:
        problem = paths.out_problem(args.ledger_row) or paths.source_problem([args.ledger_row], [args.src, args.manifest])
        if problem:
            die(problem)
    src_w, src_h, src_fps = probe(args.src)
    has_audio = has_audio_stream(args.src)
    fps = args.fps or src_fps
    dest = load_destination(args.destination)
    check_destination(dest)
    lufs = args.lufs
    if lufs is None:
        lufs = (dest.get("loudness") or {}).get("integrated_lufs")
        if lufs is None:
            lufs = DEFAULT_LUFS
        else:
            sys.stderr.write("vertcut: --lufs not given; using %s's target, %g LUFS\n" % (dest["key"], lufs))
    audio_filter = loudnorm_filter(lufs, args.tp, args.lra)   # validated before any work
    name_parts = {"show": args.show, "episode": args.episode, "guest": args.guest}
    if not args.draft_names:
        missing = [k for k, v in name_parts.items() if not v]
        if missing:
            die("the house file name needs --%s (or --draft-names for a review copy "
                "that will not pass deliver_check)" % ", --".join(missing))
        if dest.get("naming") != "clip":
            die("destination '%s' takes a %s name, not a clip name" % (dest["key"], dest.get("naming")))

    clips = read_manifest(args.manifest, fps)
    for pos, clip in enumerate(clips, 1):
        clip["index"] = pos        # the row's place in the whole manifest, so --only keeps names stable
    if args.only:
        want = {c.strip() for c in args.only.split(",")}
        clips = [c for c in clips if c["id"] in want]
        if not clips:
            die("--only matched no clip ids")

    hooks = {}
    for clip in clips:
        text = None if args.no_hook else (clip["hook"] or args.hook_text)
        if not text:
            continue
        lines = hook_lines(text)
        hold = hook_hold(lines)
        bad = [r for r in hooklint.lint(lines, W, H, hold=hold, start=0) if r[1] == "FAIL"]
        short = hold > clip["end"] - clip["start"]
        if bad or short or not args.hook_source:
            for name, _, detail in bad:
                sys.stderr.write("vertcut: hook for clip %s: %s: %s\n" % (clip["id"], name, detail))
            if not args.hook_source:
                sys.stderr.write("vertcut: a hook needs --hook-source REF\n")
            if short:
                sys.stderr.write("vertcut: clip %s is shorter than its hook's %.1f s hold\n" % (clip["id"], hold))
            raise SystemExit(2)
        hooks[clip["id"]] = {"lines": lines, "text": "\n".join(lines), "hold": hold}

    words = None
    if not args.no_captions:
        if not args.words:
            die("captions need --words FILE (or pass --no-captions)")
        words = load_words(args.words)
        if not args.allow_music:
            hit = ["clip %s: %s %.1f-%.1f s" % (clip["id"], b["kind"], b["start"], b["end"])
                   for clip in clips
                   for b in music_overlaps(cutlist.blocks_of(words), clip["start"] + args.offset,
                                           clip["end"] + args.offset)]
            if hit:
                die("these clips overlap singing or music (text withheld):\n  %s\n"
                    "pass --allow-music to render them with a label card and no words, or "
                    "correct the span with `vertcut kinds <json> --apply-overrides sung.tsv`"
                    % "\n  ".join(hit))
    if words is not None or hooks:
        if not 0 < args.bar_opacity <= 1:
            die("--bar-opacity must be above 0 and at most 1")
        if args.line_chars < 1:
            die("--line-chars must be at least 1")
        try:
            style = resolve_style(args.caption_style, args.text_color, args.bar_color, args.font)
        except CaptionError as e:
            die(str(e))
        if not os.path.isfile(style["font"]):
            die("font not found: %s" % style["font"])
        font = ImageFont.truetype(style["font"], args.font_size)
        for h in hooks.values():
            try:
                h["box"] = plan_hook(h["lines"], font, None if words is None else args.caption_y, args.line_chars)
            except CaptionError as e:
                die(str(e))

    chain, mode = reframe_chain(src_w, src_h, args.frame or "crop", args.x)
    plan_chains = {}
    if args.plan:
        if args.x or args.frame == "fit":
            die("--plan sets the crop; it does not go with --x or --frame fit")
        plan_chains, mode = plan_chain(args.plan, args.src, src_w, src_h, clips, src_fps, args.offset), "plan"
    if in_any_git_tree(args.outdir):
        die("%s is inside a git work tree; clips and captions name client speech, write them outside any repository (-o DIR)" % args.outdir)

    def out_path(clip):
        if args.draft_names:
            stem = "%s-%s" % (re.sub(r"[^A-Za-z0-9._-]+", "-", clip["id"]).strip("-") or "clip", slug(clip["title"], "clip"))
        else:
            stem = os.path.splitext(clip_name(dest, dict(name_parts, index=clip["index"]), clip["title"]))[0]
        return os.path.join(args.outdir, stem + ".mp4")

    spans = [c["end"] - c["start"] for c in clips]
    estimate = sum(spans) * RATIO
    print("vertcut: %d clips, %.1f min of clips, estimated %.1f min to cut" % (len(clips), sum(spans) / 60, estimate / 60))
    problem = gate_problem(args, spans, [out_path(c) for c in clips], estimate)
    if problem:
        die(problem)
    os.makedirs(args.outdir, exist_ok=True)
    print("vertcut: source %dx%d @ %.3f fps, reframe=%s -> %dx%d"
          % (src_w, src_h, src_fps, mode, W, H))

    warned, ledger = False, 0
    for n, clip in enumerate(clips, 1):
        hook = hooks.get(clip["id"])
        start = clip["start"] + args.offset
        end = clip["end"] + args.offset
        dur = end - start
        out = out_path(clip)
        name = os.path.splitext(os.path.basename(out))[0]

        cues = []
        if words is not None:
            try:
                cues = group_cues(words, start, end, args.max_words, args.max_chars,
                                  args.gap, args.line_chars, cutlist.blocks_of(words),
                                  LABEL_SETS[args.labels])
            except CaptionError as e:
                die("clip %s: %s" % (clip["id"], e))

        if hook:   # captions wait for the hook to leave
            cues = [dict(c, start=max(c["start"], hook["hold"])) for c in cues if c["end"] - hook["hold"] >= 0.25]
        if cues and not warned:
            # The default bar sits inside the bottom UI band; reported, not moved.
            # Height only: a cue too wide for the frame is refused when drawn, and 16 capital Ms is wider than any real cue.
            sides = [s for s in unsafe_sides(cue_box(["M" * args.line_chars] * MAX_LINES, font, args.caption_y))
                     if s in ("top", "bottom")]
            if sides:
                warned = True
                sys.stderr.write("vertcut: WARN the caption bar (bottom edge y=%d) enters the %s safezone band\n"
                                 % (args.caption_y, "/".join(sides)))
        print("vertcut: [%d/%d] %s  %.2fs  %d cues%s"
              % (n, len(clips), os.path.basename(out), dur, len(cues),
                 "  hook %.1fs" % hook["hold"] if hook else ""))
        if args.dry_run:
            continue

        tmp = tempfile.mkdtemp(prefix="vertcut-")
        try:
            row_chain = plan_chains[clip["index"]] if args.plan else chain   # a plan gives each row its own crop
            filters = [row_chain]
            inputs = ["-ss", "%.3f" % start, "-to", "%.3f" % end, "-i", args.src]
            label = "[base]"
            if cues:
                try:
                    streams = cue_streams(cues, lambda c: wrap_chars(c["text"], args.line_chars),
                                          font, args.caption_y, tmp, style, args.bar_opacity, src_fps,
                                          phase_of(start, src_fps))
                except CaptionError as e:
                    die("clip %s: %s" % (clip["id"], e))
                for lst, cx, cy in streams:
                    inputs += ["-f", "concat", "-safe", "0", "-i", lst]
                    nxt = "[v%d]" % len(filters)
                    filters.append("%s[%d:v]overlay=x=%d:y=%d%s"
                                   % (label, inputs.count("-i") - 1, cx, cy, nxt))
                    label = nxt

            if hook:
                png = os.path.join(tmp, "hook.png")
                render_cue(hook["lines"], font, png, style, args.bar_opacity)
                faces = frame0_faces(args.src, start, row_chain, tmp)
                if faces is None:
                    sys.stderr.write("vertcut: SKIP face check on frame 0 (needs macOS Vision)\n")
                elif hook_covers_face(hook["box"], faces):
                    die("clip %s: the hook card covers a face on frame 0" % clip["id"])
                inputs += ["-loop", "1", "-i", png]
                filters.append("%s[%d:v]overlay=x=%d:y=%d:enable='between(t,0,%.3f)'[vh]"
                               % (label, inputs.count("-i") - 1, hook["box"][0], hook["box"][1], hook["hold"]))
                label = "[vh]"
            filters.append("%s%s[vout]" % (label, COLOR_TAG_FILTER))
            label = "[vout]"

            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y"]
                   + inputs
                   + ["-filter_complex", ";".join(filters),
                      "-map", label] + (["-map", "0:a"] if has_audio else []) + [
                      "-t", "%.3f" % dur,
                      ] + video_codec_args(args.crf, args.preset) + [
                      "-pix_fmt", "yuv420p", "-r", str(out_rate(src_fps))]
                   + COLOR_TAG_ARGS + ["-movflags", "+faststart"]
                   + (audio_codec_args(dest) + ["-af", audio_filter] if has_audio else ["-an"])
                   + [out])

            r = subprocess.run(cmd)
            if r.returncode != 0:
                die("ffmpeg failed on clip %s" % clip["id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if args.ledger_row:
            try:
                result = delivercheck.check(out, dest)
                if result["status"] != "pass":
                    sys.stderr.write("vertcut: WARN clip %s: deliver check %s\n" % (clip["id"], result["status"]))
                line = delivercheck.ledger_line(delivercheck.ledger_row(result, sha256=delivercheck.file_sha256(out)))
                # appended and written per clip, so an earlier batch's rows and a half-done batch's rows survive
                old = open(args.ledger_row, encoding="utf-8").read() if os.path.isfile(args.ledger_row) else ""
                paths.write_private(args.ledger_row, old + line)
                ledger += 1
            except (delivercheck.CheckError, delivercheck.ToolMissing, OSError, paths.OutputRefused) as e:
                die("ledger row for clip %s: %s" % (clip["id"], e))

        if hook:
            folder = os.path.join(os.path.dirname(out), "review-srt")
            os.makedirs(folder, exist_ok=True)
            write_sidecar(os.path.join(folder, name + ".hook.json"),
                          json.dumps({"text": hook["text"], "source": args.hook_source,
                                      "box": [round(v) for v in hook["box"]], "start": 0, "end": hook["hold"]},
                                     indent=2))
        if cues:
            write_sidecar(srt_path(out, dest), "".join(
                "%d\n%s --> %s\n%s\n\n" % (i, srt_tc(c["start"]), srt_tc(c["end"]), c["text"])
                for i, c in enumerate(cues, 1)))

    if ledger:
        print("vertcut: %d ledger row stubs added to %s" % (ledger, args.ledger_row))
    print("vertcut: done -> %s" % args.outdir)


if __name__ == "__main__":
    main()
