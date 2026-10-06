#!/usr/bin/env python3
"""vertcut cut — turn a cuts manifest + a source video into 9:16 captioned clips.

Reads a TSV manifest (id, in, out, title), slices the source, reframes to
1080x1920, burns word-grouped captions from a Whisper word-timestamp JSON, and
writes an .srt sidecar next to each export.

The words come only through segkind: text it withheld (singing, music,
hallucination) is a [label], never words. A label is drawn as a bracketed card
([singing], [music]) and a clip that overlaps singing or music is refused
unless --allow-music is given.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import segkind  # noqa: E402  (beside this file)

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
    """([{start, end, word}] in source-time order, [{start, end, kind, label}]
    for each segment withheld from the transcript). The JSON goes through
    segkind.load_guarded: a safe JSON is read as it is, a raw Whisper JSON is
    screened in memory (Whisper numbers and text counts, no audio) and its
    withheld segments are dropped before any word is read. The .raw.json
    itself is refused."""
    try:
        data, results, checked = segkind.load_guarded(path)
    except segkind.SegkindError as e:
        die(str(e))
    if not checked:
        sys.stderr.write("vertcut: warning: %s was not screened for singing from audio "
                         "(run `vertcut transcribe`, or `vertcut kinds`)\n" % os.path.basename(path))
    words = []
    for seg in data.get("segments") or []:
        for w in seg.get("words") or []:
            text = (w.get("word") or w.get("text") or "").strip()
            if not text or w.get("start") is None or w.get("end") is None:
                continue
            words.append({"start": float(w["start"]), "end": float(w["end"]), "word": text})
    blocks = [{"start": r["start"], "end": r["end"], "kind": r["kind"], "label": r["label"]}
              for r in results if r["withheld"] and not r.get("empty")
              and r["start"] is not None and r["end"] is not None]
    if not words and not blocks:
        die("no word timestamps in %s (transcribe with --word-timestamps True)" % path)
    return words, blocks


# --labels: which withheld spans get a bracketed card on the clip
LABEL_SETS = {"none": (), "music": ("sung", "music"),
              "all": ("sung", "music", "hallucination", "uncertain")}
REFUSE_KINDS = ("sung", "music", "uncertain")   # uncertain here is withheld, i.e. sung-leaning


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


def group_cues(words, start, end, max_words, max_chars, gap, blocks=(), label_kinds=()):
    """Slice words to [start,end], rebase to 0, and group into caption cues.
    Withheld spans in `blocks` whose kind is in label_kinds become bracketed
    label cues in time order."""
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
    cues = [c for c in cues if c["end"] > c["start"]]
    if blocks and label_kinds:
        cues = sorted(cues + label_cues(blocks, start, end, label_kinds),
                      key=lambda c: (c["start"], c["end"]))
        for i, c in enumerate(cues[:-1]):       # a label never runs over the next cue
            c["end"] = min(c["end"], cues[i + 1]["start"]) if c.get("label") else c["end"]
        cues = [c for c in cues if c["end"] > c["start"]]
    return cues


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


LABEL_FILL = (255, 255, 255, 200)     # a label card is the caption style, a little quieter


def render_cue(text, font, path, max_w, stroke, label=False):
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
                fill=LABEL_FILL if label else (255, 255, 255, 255), stroke_width=stroke,
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
    ap.add_argument("--words", help="safe transcript JSON from `vertcut transcribe` "
                                    "(a raw Whisper JSON is screened in memory; a .raw.json is refused)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--allow-music", action="store_true",
                    help="render a clip that overlaps singing or music; its words stay "
                         "withheld and the span gets a bracketed label card")
    ap.add_argument("--labels", choices=sorted(LABEL_SETS), default="music",
                    help="which withheld spans get a [label] card: music = [singing] and "
                         "[music] (default), all = also [inaudible], none = no cards")
    ap.add_argument("--frame", choices=["crop", "fit"], default="crop")
    ap.add_argument("--x", type=float, default=0.0,
                    help="crop offset, -1 (full left) .. 1 (full right)")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="seconds to add to every manifest timecode, when the "
                         "manifest's zero is not the media file's zero; verify "
                         "the sign on one clip before rendering the set")
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
    has_audio = has_audio_stream(args.src)
    fps = args.fps or src_fps

    clips = read_manifest(args.manifest, fps)
    if args.only:
        want = {c.strip() for c in args.only.split(",")}
        clips = [c for c in clips if c["id"] in want]
        if not clips:
            die("--only matched no clip ids")

    words, blocks = None, []
    if not args.no_captions:
        if not args.words:
            die("captions need --words FILE (or pass --no-captions)")
        words, blocks = load_words(args.words)
        if not os.path.isfile(args.font):
            die("font not found: %s" % args.font)
        font = ImageFont.truetype(args.font, args.font_size)
        label_font = ImageFont.truetype(args.font, max(12, int(args.font_size * 0.8)))
        if not args.allow_music:
            hit = []
            for clip in clips:
                for b in music_overlaps(blocks, clip["start"] + args.offset,
                                        clip["end"] + args.offset):
                    hit.append("clip %s: %s %.1f-%.1f s" % (clip["id"], b["kind"],
                                                            b["start"], b["end"]))
            if hit:
                die("these clips overlap singing or music (text withheld):\n  %s\n"
                    "pass --allow-music to render them with a label card and no words, or "
                    "correct the span with `vertcut kinds <json> --apply-overrides sung.tsv`"
                    % "\n  ".join(hit))

    chain, mode = reframe_chain(src_w, src_h, args.frame, args.x)
    os.makedirs(args.outdir, exist_ok=True)
    print("vertcut: source %dx%d @ %.3f fps, reframe=%s -> %dx%d"
          % (src_w, src_h, src_fps, mode, W, H))

    for n, clip in enumerate(clips, 1):
        start = clip["start"] + args.offset
        end = clip["end"] + args.offset
        dur = end - start
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", clip["id"]).strip("-") or "clip"
        name = "%s-%s" % (safe_id, slug(clip["title"], "clip"))
        out = os.path.join(args.outdir, name + ".mp4")

        cues = []
        if words is not None:
            cues = group_cues(words, start, end, args.max_words, args.max_chars, args.gap,
                              blocks, LABEL_SETS[args.labels])

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
                is_label = bool(cue.get("label"))
                render_cue(cue["text"], label_font if is_label else font, png, W - 140,
                           max(4, args.font_size // 11), label=is_label)
                inputs += ["-loop", "1", "-i", png]
                nxt = "[v%d]" % i
                filters.append(
                    "%s[%d:v]overlay=x=(W-w)/2:y=%d:enable='between(t,%.3f,%.3f)'%s"
                    % (label, i + 1, args.caption_y, cue["start"], cue["end"], nxt))
                label = nxt

            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y"]
                   + inputs
                   + ["-filter_complex", ";".join(filters),
                      "-map", label] + (["-map", "0:a"] if has_audio else []) + [
                      "-t", "%.3f" % dur,
                      "-c:v", "libx264", "-crf", str(args.crf), "-preset", "veryfast",
                      "-pix_fmt", "yuv420p", "-r", "%.4f" % min(src_fps, 30.0),
                      "-movflags", "+faststart"]
                   + (["-c:a", "aac", "-b:a", "192k",
                       "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if has_audio else ["-an"])
                   + [out])

            r = subprocess.run(cmd)
            if r.returncode != 0:
                die("ffmpeg failed on clip %s" % clip["id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if cues:
            with open(os.path.splitext(out)[0] + ".srt", "w") as fh:
                for i, cue in enumerate(cues, 1):
                    fh.write("%d\n%s --> %s\n%s\n\n"
                             % (i, srt_tc(cue["start"]), srt_tc(cue["end"]), cue["text"]))

    print("vertcut: done -> %s" % args.outdir)


if __name__ == "__main__":
    main()
