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

W, H = 1080, 1920
DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def die(msg):
    sys.stderr.write("vertcut: %s\n" % msg)
    raise SystemExit(1)


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
    return s[:60] or fallback


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
    st = json.loads(out.stdout)["streams"][0]
    num, _, den = st["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    return int(st["width"]), int(st["height"]), (fps or 25.0)


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


def group_cues(words, start, end, max_words, max_chars, gap):
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
        if cur:
            chars = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            if (len(cur) >= max_words or chars > max_chars
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


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_cue(text, font, path, max_w, stroke):
    scratch = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(scratch)
    lines = wrap(d, text, font, max_w)
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * 1.15)
    pad = stroke + 8
    width = int(max(d.textlength(l, font=font) for l in lines)) + pad * 2
    height = lh * len(lines) + pad * 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dd = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        lw = dd.textlength(line, font=font)
        dd.text(((width - lw) / 2, pad + i * lh), line, font=font,
                fill=(255, 255, 255, 255), stroke_width=stroke,
                stroke_fill=(0, 0, 0, 235))
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
                    help="seconds to add to every manifest timecode "
                         "(use 3600 for a Resolve timeline starting at 01:00:00:00)")
    ap.add_argument("--fps", type=float, help="frame rate for HH:MM:SS:FF timecodes")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--font-size", type=int, default=66)
    ap.add_argument("--caption-y", type=int, default=1400)
    ap.add_argument("--max-words", type=int, default=3)
    ap.add_argument("--max-chars", type=int, default=26)
    ap.add_argument("--gap", type=float, default=0.6, help="silence that breaks a cue")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--only", help="comma-separated clip ids to render")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        die("no such file: %s" % args.src)
    src_w, src_h, src_fps = probe(args.src)
    fps = args.fps or src_fps

    clips = read_manifest(args.manifest, fps)
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
        if not os.path.isfile(args.font):
            die("font not found: %s" % args.font)
        font = ImageFont.truetype(args.font, args.font_size)

    chain, mode = reframe_chain(src_w, src_h, args.frame, args.x)
    os.makedirs(args.outdir, exist_ok=True)
    print("vertcut: source %dx%d @ %.3f fps, reframe=%s -> %dx%d"
          % (src_w, src_h, src_fps, mode, W, H))

    for n, clip in enumerate(clips, 1):
        start = clip["start"] + args.offset
        end = clip["end"] + args.offset
        dur = end - start
        name = "%s-%s" % (slug(clip["id"], "clip"), slug(clip["title"], "clip"))
        out = os.path.join(args.outdir, name + ".mp4")

        cues = []
        if words is not None:
            cues = group_cues(words, start, end, args.max_words, args.max_chars, args.gap)

        tmp = tempfile.mkdtemp(prefix="vertcut-")
        try:
            filters = [chain]
            inputs = ["-ss", "%.3f" % start, "-to", "%.3f" % end, "-i", args.src]
            label = "[base]"
            for i, cue in enumerate(cues):
                png = os.path.join(tmp, "cue%03d.png" % i)
                render_cue(cue["text"], font, png, W - 140, max(4, args.font_size // 11))
                inputs += ["-loop", "1", "-i", png]
                nxt = "[v%d]" % i
                filters.append(
                    "%s[%d:v]overlay=x=(W-w)/2:y=%d:enable='between(t,%.3f,%.3f)'%s"
                    % (label, i + 1, args.caption_y, cue["start"], cue["end"], nxt))
                label = nxt

            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y"]
                   + inputs
                   + ["-filter_complex", ";".join(filters),
                      "-map", label, "-map", "0:a?",
                      "-t", "%.3f" % dur,
                      "-c:v", "libx264", "-crf", str(args.crf), "-preset", "veryfast",
                      "-pix_fmt", "yuv420p", "-r", "%.4f" % min(src_fps, 30.0),
                      "-c:a", "aac", "-b:a", "192k",
                      "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                      "-movflags", "+faststart", out])

            print("vertcut: [%d/%d] %s  %.2fs  %d cues"
                  % (n, len(clips), os.path.basename(out), dur, len(cues)))
            if args.dry_run:
                continue
            r = subprocess.run(cmd)
            if r.returncode != 0:
                die("ffmpeg failed on clip %s" % clip["id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if cues and not args.dry_run:
            with open(os.path.splitext(out)[0] + ".srt", "w") as fh:
                for i, cue in enumerate(cues, 1):
                    fh.write("%d\n%s --> %s\n%s\n\n"
                             % (i, srt_tc(cue["start"]), srt_tc(cue["end"]), cue["text"]))

    print("vertcut: done -> %s" % args.outdir)


if __name__ == "__main__":
    main()
