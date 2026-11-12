"""
watch: make the operator look at the picture before a draft or render goes to a reviewer.

    watch sheets VIDEO --cuts SRC --out DIR [--fps F] [--per-sheet 6]
        Draws the first (the cut frame), middle and last (the frame before the next
        cut) frame of every shot, exact frames by accurate seek, one row of three per
        shot with the shot number, frame numbers and timecodes on each tile, native
        aspect, 6 shots per sheet at most 1920 px on the long side. Writes
        DIR/watch-01.jpg ... and DIR/watch.json. SRC is a JSON with cut_frames
        (shots + 1 timeline frames at --fps) or cut_times (seconds), or a comma list
        of cut times in seconds. --fps defaults to the video's own rate.
    watch ack DIR --notes FILE
        FILE has exactly one non-empty line per shot: what was seen. Refuses on a
        different line count, a missing sheet, or a video that changed since the
        sheets were drawn. Otherwise records the notes and acked: true.
    watch status DIR
        Prints acked or "not acked: <reason>"; exit 0 only if acked and the video is
        unchanged. A review step calls this before sending anything.

Needs ffmpeg and Pillow (sheets only). The video fingerprint is the sha256 of its
first 1 MiB plus its size, so a re-render is caught without reading a whole file.
Exit 2 on a refusal.
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from fractions import Fraction

from rpresolve import cli

NAME = "watch"
HELP = "Draw first, middle and last frame of every shot on sheets; ack them before review"
LONG_SIDE = 1920


class WatchError(ValueError):
    """A sheet, ack or status that cannot be done."""


def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("sheets", help="draw the sheets")
    s.add_argument("video")
    s.add_argument("--cuts", required=True, help="JSON with cut_frames or cut_times, or a comma list of seconds")
    s.add_argument("--out", required=True, help="folder for the sheets and watch.json")
    s.add_argument("--fps", help="frame rate of cut_frames (default: the video's own), e.g. 25 or 30000/1001")
    s.add_argument("--per-sheet", type=int, default=6, help="shots per sheet")
    a = sub.add_parser("ack", help="record that the sheets were read")
    a.add_argument("dir")
    a.add_argument("--notes", required=True, help="text file, one non-empty line per shot")
    t = sub.add_parser("status", help="acked, or why not; exit 0 only if acked")
    t.add_argument("dir")


def fingerprint(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
    h.update(str(os.path.getsize(path)).encode())
    return h.hexdigest()


def timecode(frame, fps):
    ms = int(round(frame * 1000 / fps))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def cut_frames(src, fps):
    """The cut frames from a JSON file (cut_frames or cut_times) or a comma list of seconds."""
    try:
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as f:
                doc = json.load(f)
            if "cut_frames" in doc:
                cuts = [int(x) for x in doc["cut_frames"]]
            else:
                cuts = [round(float(x) * fps) for x in doc["cut_times"]]
        else:
            cuts = [round(float(x) * fps) for x in src.split(",")]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise WatchError(f"--cuts {src!r}: need cut_frames, cut_times or a comma list of seconds ({e})")
    if len(cuts) < 2 or any(b <= a for a, b in zip(cuts, cuts[1:])) or cuts[0] < 0:
        raise WatchError("cuts must be at least two increasing, non-negative frames")
    return cuts


def shot_frames(cuts):
    """[first, middle, last] per shot: the cut frame, the midpoint, the frame before the next cut."""
    return [[a, (a + b - 1) // 2, b - 1] for a, b in zip(cuts, cuts[1:])]


def grab(video, frame, fps, out):
    """Write one exact frame as a PNG. Accurate seek lands on the first frame at or
    after the time, so ask half a frame early; the decoder drops the earlier ones."""
    from rpresolve import delivercheck
    t = max(0.0, (frame - 0.5) / fps)
    p = subprocess.run([delivercheck.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.6f}",
                        "-i", video, "-map", "0:v:0", "-frames:v", "1", out],
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0 or not os.path.exists(out):
        raise WatchError(f"no frame {frame} in {video}: {p.stderr.strip()[-200:]}")


def sheets(args):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise WatchError("sheets needs Pillow (pip install Pillow) for this Python")
    from rpresolve import delivercheck, gatepacket
    video, out = os.path.abspath(args.video), os.path.abspath(args.out)
    if args.per_sheet < 1:
        raise WatchError("--per-sheet must be at least 1")
    try:
        delivercheck.check_tools()
        _, vfps = gatepacket.probe_clip(video)
    except (delivercheck.ToolMissing, gatepacket.GateError) as e:
        raise WatchError(str(e))
    try:
        fps = Fraction(args.fps) if args.fps else vfps
    except (ValueError, ZeroDivisionError):
        raise WatchError(f"--fps {args.fps!r} is not a rate")
    if fps <= 0:
        raise WatchError("--fps must be positive")
    fps = float(fps)
    if not args.fps:  # shortcut: r_frame_rate is the nominal rate, on a variable-rate file pass --fps
        print(f"watch: frame rate {vfps} from the file's r_frame_rate; on variable-rate files pass --fps",
              file=sys.stderr)
    shots = shot_frames(cut_frames(args.cuts, fps))
    os.makedirs(out, exist_ok=True)
    names, tile_h = [], None
    with tempfile.TemporaryDirectory() as tmp:
        for s0 in range(0, len(shots), args.per_sheet):
            chunk = shots[s0:s0 + args.per_sheet]
            canvas = None
            for r, frames in enumerate(chunk):
                for c, f in enumerate(frames):
                    png = os.path.join(tmp, "f.png")
                    grab(video, f, fps, png)
                    with Image.open(png) as im:
                        im.load()
                        if tile_h is None:  # one tile size for every sheet, from the first frame's aspect
                            tile_h = max(32, min(LONG_SIDE // args.per_sheet,
                                                 int(LONG_SIDE / 3 * im.height / im.width))) // 2 * 2
                        tw = max(2, round(im.width * tile_h / im.height) // 2 * 2)
                        tile = im.convert("RGB").resize((tw, tile_h), Image.LANCZOS)
                    os.unlink(png)
                    if canvas is None:
                        canvas = Image.new("RGB", (3 * tw, len(chunk) * tile_h))
                    size = max(9, min(tw // 15, tile_h // 12))
                    x, y = c * tw, r * tile_h
                    canvas.paste(tile, (x, y))
                    d = ImageDraw.Draw(canvas)
                    d.rectangle([x, y + tile_h - size - 4, x + tw, y + tile_h], fill=(0, 0, 0))
                    d.text((x + 3, y + tile_h - size - 3), f"S{s0 + r + 1:02d} f{f} {timecode(f, fps)}",
                           fill=(255, 255, 255), font=ImageFont.load_default(size=size))
            names.append(f"watch-{len(names) + 1:02d}.jpg")
            canvas.save(os.path.join(out, names[-1]), quality=85)
    doc = {"video": video, "video_sha256_first1mib_and_size": fingerprint(video), "fps": fps,
           "shots": [{"index": i + 1, "frames": fr, "sheet": names[i // args.per_sheet]}
                     for i, fr in enumerate(shots)],
           "sheets": names, "acked": False}
    _save(out, doc)
    print(f"watch: {len(shots)} shots on {len(names)} sheets in {out}")
    return 0


def _save(d, doc):
    with open(os.path.join(d, "watch.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


def _load(d):
    try:
        with open(os.path.join(d, "watch.json"), encoding="utf-8") as f:
            doc = json.load(f)
        doc["video"], doc["video_sha256_first1mib_and_size"], doc["sheets"], len(doc["shots"])
        return doc
    except (OSError, ValueError, KeyError, TypeError):
        raise WatchError(f"{d} has no readable watch.json")


def _problem(d, doc):
    """Why the sheets no longer stand for the video, or None."""
    for s in doc["sheets"]:
        if not os.path.isfile(os.path.join(d, s)):
            return f"sheet {s} is missing"
    try:
        if fingerprint(doc["video"]) != doc["video_sha256_first1mib_and_size"]:
            return "the video changed since the sheets were drawn"
    except OSError:
        return "the video is missing"
    return None


def ack(args):
    doc = _load(args.dir)
    try:
        with open(args.notes, encoding="utf-8") as f:
            notes = [ln.strip() for ln in f if ln.strip()]
    except OSError as e:
        raise WatchError(f"--notes: {e}")
    if len(notes) != len(doc["shots"]):
        raise WatchError(f"{len(notes)} note lines for {len(doc['shots'])} shots; one line per shot")
    problem = _problem(args.dir, doc)
    if problem:
        raise WatchError(problem)
    doc.update(notes=notes, acked=True, acked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    _save(args.dir, doc)
    print(f"watch: acked {len(notes)} shots")
    return 0


def status(args):
    try:
        doc = _load(args.dir)
        why = None if doc.get("acked") else "no ack recorded"
        why = why or _problem(args.dir, doc)
    except WatchError as e:
        why = str(e)
    print("acked" if why is None else f"not acked: {why}")
    return 0 if why is None else 1


def run(args):
    try:
        return {"sheets": sheets, "ack": ack, "status": status}[args.action](args)
    except WatchError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    cli.script(__name__)
