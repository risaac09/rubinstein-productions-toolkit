#!/usr/bin/env python3
"""vertcut cut — turn a cuts manifest + a source video into 9:16 captioned clips.

Reads a TSV manifest (id, in, out, title), slices the source, reframes to
1080x1920, burns word-grouped captions from a Whisper word-timestamp JSON, and
writes an .srt sidecar next to each export.

Captions are drawn as Pillow-rendered PNG overlays rather than through ffmpeg's
subtitles filter, because Homebrew's ffmpeg ships without libass.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

# The file-name rule and the destination live in rpresolve (production/), not here.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "production"))
from rpresolve import deliver  # noqa: E402
from rpresolve.config import ConfigError, load_config  # noqa: E402

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


def clip_name(dest, parts, title):
    """The house file name for one clip: parts are show, episode, guest, index. The slug comes
    from the title. Exits with the rule's own message when a part breaks it."""
    try:
        return deliver.name_for(dest, dict(parts, slug=slug(title, "clip")))
    except deliver.NameRuleError as e:
        die("clip name: %s" % e)


def video_codec_args(crf, preset):
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", preset]


# ---------------------------------------------------------------- timecodes

def parse_tc(s, fps):
    """Accept SS.mmm, MM:SS, HH:MM:SS(.mmm), or DaVinci HH:MM:SS:FF."""
    s = s.strip()
    if not s:
        die("empty timecode")
    parts = s.split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 4:
        h, m, sec, fr = parts
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(fr) / fps
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
            start, end = parse_tc(tin, fps), parse_tc(tout, fps)
            if end <= start:
                die("%s:%d out (%s) is not after in (%s)" % (path, lineno, tout, tin))
            rows.append({"id": cid, "start": start, "end": end, "title": title})
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
    """Flatten a Whisper JSON into [{start, end, word}] in source-time order."""
    with open(path) as fh:
        data = json.load(fh)
    words = []
    segments = data.get("segments") or []
    for seg in segments:
        for w in seg.get("words") or []:
            text = (w.get("word") or w.get("text") or "").strip()
            if not text or w.get("start") is None or w.get("end") is None:
                continue
            words.append({"start": float(w["start"]), "end": float(w["end"]), "word": text})
    if not words:
        die("no word timestamps in %s (transcribe with --word-timestamps True)" % path)
    return words


def group_cues(words, start, end, max_words, max_chars, gap, line_chars=LINE_CHARS):
    """Slice words to [start,end], rebase to 0, and group into caption cues."""
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
    return [c for c in cues if c["end"] > c["start"]]


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


def render_cue(lines, font, path, style, opacity):
    """Draw one cue: text on a partial-opacity bar. Returns (width, height)."""
    scratch = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * 1.15)
    pad_x, pad_y = font.size // 2, font.size // 4
    width = int(max(scratch.textlength(l, font=font) for l in lines)) + pad_x * 2
    height = lh * len(lines) + pad_y * 2
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
    img.save(path)
    return width, height


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


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(prog="vertcut cut", add_help=True)
    ap.add_argument("src")
    ap.add_argument("manifest")
    ap.add_argument("-o", "--outdir", default="vertcut-out")
    ap.add_argument("--words", help="Whisper JSON with word timestamps")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--frame", choices=["crop", "fit"], default="crop")
    ap.add_argument("--x", type=float, default=0.0,
                    help="crop offset, -1 (full left) .. 1 (full right)")
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
    ap.add_argument("--dry-run", action="store_true")
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
    src_w, src_h, src_fps = probe(args.src)
    has_audio = has_audio_stream(args.src)
    fps = args.fps or src_fps
    dest = load_destination(args.destination)
    check_destination(dest)
    lufs = args.lufs if args.lufs is not None else (
        (dest.get("loudness") or {}).get("integrated_lufs") or DEFAULT_LUFS)
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

    words = None
    if not args.no_captions:
        if not args.words:
            die("captions need --words FILE (or pass --no-captions)")
        words = load_words(args.words)
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

    chain, mode = reframe_chain(src_w, src_h, args.frame, args.x)
    os.makedirs(args.outdir, exist_ok=True)
    print("vertcut: source %dx%d @ %.3f fps, reframe=%s -> %dx%d"
          % (src_w, src_h, src_fps, mode, W, H))

    for n, clip in enumerate(clips, 1):
        start = clip["start"] + args.offset
        end = clip["end"] + args.offset
        dur = end - start
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", clip["id"]).strip("-") or "clip"
        if args.draft_names:
            name = "%s-%s" % (safe_id, slug(clip["title"], "clip"))
        else:
            name = os.path.splitext(clip_name(dest, dict(name_parts, index=clip["index"]), clip["title"]))[0]
        out = os.path.join(args.outdir, name + ".mp4")

        cues = []
        if words is not None:
            try:
                cues = group_cues(words, start, end, args.max_words, args.max_chars,
                                  args.gap, args.line_chars)
            except CaptionError as e:
                die("clip %s: %s" % (clip["id"], e))

        print("vertcut: [%d/%d] %s  %.2fs  %d cues"
              % (n, len(clips), os.path.basename(out), dur, len(cues)))
        if args.dry_run:
            continue

        tmp = tempfile.mkdtemp(prefix="vertcut-")
        try:
            filters = [chain]
            inputs = ["-ss", "%.3f" % start, "-to", "%.3f" % end, "-i", args.src]
            label = "[base]"
            for i, cue in enumerate(cues):
                png = os.path.join(tmp, "cue%03d.png" % i)
                try:
                    render_cue(wrap_chars(cue["text"], args.line_chars), font, png,
                               style, args.bar_opacity)
                except CaptionError as e:
                    die("clip %s: %s" % (clip["id"], e))
                inputs += ["-loop", "1", "-i", png]
                nxt = "[v%d]" % i
                filters.append(
                    "%s[%d:v]overlay=x=(W-w)/2:y=%d-h:enable='between(t,%.3f,%.3f)'%s"
                    % (label, i + 1, args.caption_y, cue["start"], cue["end"], nxt))
                label = nxt

            filters.append("%s%s[vout]" % (label, COLOR_TAG_FILTER))
            label = "[vout]"

            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y"]
                   + inputs
                   + ["-filter_complex", ";".join(filters),
                      "-map", label] + (["-map", "0:a"] if has_audio else []) + [
                      "-t", "%.3f" % dur,
                      ] + video_codec_args(args.crf, args.preset) + [
                      "-pix_fmt", "yuv420p", "-r", "%.4f" % min(src_fps, 30.0)]
                   + COLOR_TAG_ARGS + ["-movflags", "+faststart"]
                   + (audio_codec_args(dest) + ["-af", audio_filter] if has_audio else ["-an"])
                   + [out])

            r = subprocess.run(cmd)
            if r.returncode != 0:
                die("ffmpeg failed on clip %s" % clip["id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if cues:
            with open(srt_path(out, dest), "w") as fh:
                for i, cue in enumerate(cues, 1):
                    fh.write("%d\n%s --> %s\n%s\n\n"
                             % (i, srt_tc(cue["start"]), srt_tc(cue["end"]), cue["text"]))

    print("vertcut: done -> %s" % args.outdir)


if __name__ == "__main__":
    main()
