"""
rpresolve.picreview: a picture of every cut, for Claude to read. Offline:
it flags and never cuts, and it reads nothing itself. Agents judge edits
badly from transcripts alone, so each cut gets one JPEG pairing the last
frame before it with the first frame after it, 640 px wide each at display
aspect (an anamorphic squeeze is undone), with a thin rule-of-thirds grid.
No text is drawn. The reader looks for screen direction, eye trace and
match-cut candidates, and writes them on the report's empty reading line.

Run from production/:
    python3 -m rpresolve.picreview CUT [--render FILE] [--fps R] [--sheets DIR] [--out FILE]

CUT is anything rpresolve.rhythm reads (a render, an EDL, a timeline_items
JSON, a paper-edit TSV). Only a render has pictures: for the others pass the
render of that cut with --render, on the cut's own clock, or the tool says
so and stops. Cuts of a render are hard cuts (ffmpeg scdet at qc's
threshold); a dissolve is not found.

Shot scale is a proxy: the largest face's share of the frame, measured at
each shot's middle frame with macOS Vision (rpresolve.vision). Off macOS the
report says the scale track was not measured. A shot with no face is "none"
and breaks every flag. Two flags, both asked as questions:
    - jump: neighbouring shots in the same scale band (a jump-cut risk)
    - progression: three or more shots in a row that grow or shrink
Sheets and the report name client media. Both are refused inside any git
tree.
"""

import argparse
import os
import subprocess
import sys
import tempfile

from . import cutlist, delivercheck as dc, paths, rhythm, storypacket

SHEET_WIDTH = 640
SHEET_TIMEOUT_S = 120
# Face area as a share of the frame, upper edges of the bands wide to tight.
BANDS = (0.005, 0.02, 0.06, 0.15)
BAND_NAMES = ("wide", "medium wide", "medium", "close", "tight")
SQUEEZE = "scale='trunc(iw*sar/2)*2':ih,setsar=1,scale=%d:-2" % SHEET_WIDTH
GRID = "drawgrid=w=iw/3:h=ih/3:t=1:c=white@0.6"


class PicError(ValueError):
    pass


def render_fps(path):
    info = dc.probe(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    num, _, den = ((video or {}).get("avg_frame_rate") or "0/1").partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def _ffmpeg(cmd, out, what):
    if os.path.exists(out):
        os.remove(out)  # so a file from an earlier run never passes for this one
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=SHEET_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise PicError(f"ffmpeg timed out making {what}")
    if proc.returncode != 0 or not os.path.isfile(out):
        raise PicError(f"ffmpeg could not make {what}: "
                       + proc.stderr.decode(errors="replace").strip()[-160:])


def make_sheet(render, t, fps, out, ffmpeg=None):
    """The frame before the cut at t and the frame at it, side by side in
    one JPEG at out. An input seek lands on the first frame at or after the
    time, so half a frame early picks each frame whatever the rounding."""
    half = 0.5 / fps
    cmd = [ffmpeg or cutlist.FFMPEG, "-v", "error", "-y"]
    for at in (max(0.0, t - 1 / fps - half), t - half):
        cmd += ["-ss", f"{at:.4f}", "-i", "file:" + render]
    graph = (f"[0:v:0]{SQUEEZE},{GRID}[a];[1:v:0]{SQUEEZE},{GRID}[b];[a][b]hstack[out]")
    cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", out]
    _ffmpeg(cmd, out, f"the sheet at {t:.2f} s")


def band(area):
    """0 for no face, else 1 (wide) to 5 (tight)."""
    if not area:
        return 0
    return 1 + sum(area >= edge for edge in BANDS)


def face_areas(render, times, ffmpeg=None):
    """Largest face's share of the frame at each time (0.0 for none, None
    for a frame ffmpeg could not read), or None when Vision cannot run here."""
    from . import vision
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, t in enumerate(times):
            jpg = os.path.join(tmp, f"{i:04d}.jpg")
            try:
                _ffmpeg([ffmpeg or cutlist.FFMPEG, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i",
                         "file:" + render, "-frames:v", "1", "-vf", SQUEEZE, "-q:v", "3", jpg],
                        jpg, f"the frame at {t:.2f} s")
                frames.append(jpg)
            except PicError:
                frames.append(None)  # that shot reads "not measured", the rest go on
        boxes = vision.face_boxes([f for f in frames if f])
    if boxes is None:
        return None
    return [None if f is None else
            max((b["w"] * b["h"] for b in boxes.get(f) or []), default=0.0) for f in frames]


def flags(bands, cuts):
    """Questions from the shots' scale bands. bands[i] is shot i's band;
    cuts[i] is the time of the cut into shot i+1."""
    q = []
    for i in range(len(bands) - 1):
        if bands[i] and bands[i] == bands[i + 1]:
            q.append(f"Shots {i + 1} and {i + 2} are both {BAND_NAMES[bands[i] - 1]} across the "
                     f"cut at {storypacket.clock(cuts[i])}. A jump cut, or meant?")
    i = 0
    while i < len(bands) - 2:
        if not bands[i]:
            i += 1
            continue
        j = i + 1
        sign = (bands[j] > bands[i]) - (bands[j] < bands[i])
        while sign and j + 1 < len(bands) and bands[j] and \
                ((bands[j + 1] > bands[j]) - (bands[j + 1] < bands[j])) == sign:
            j += 1
        if sign and j - i >= 2:
            q.append(f"Shots {i + 1} to {j + 1} {'grow' if sign > 0 else 'shrink'} one band at a "
                     f"time, from {BAND_NAMES[bands[i] - 1]} to {BAND_NAMES[bands[j] - 1]} "
                     f"({storypacket.clock(cuts[i - 1] if i else 0)} to "
                     f"{storypacket.clock(cuts[j - 1])}). A progression you built?")
        i = j if sign else i + 1
    return q


def label(areas, bands, shot):
    if areas is None or areas[shot] is None:
        return "not measured"
    return "none" if not bands[shot] else \
        f"{BAND_NAMES[bands[shot] - 1]} ({areas[shot]:.1%} of frame)"


def build(cut, render=None, fps=None, sheets=None, out=None, ffmpeg=None):
    """The report as Markdown, and a summary line."""
    durs, rate = rhythm.read_cut(cut, fps)
    is_render = os.path.splitext(cut)[1].lower() not in (".edl", ".json", ".tsv")
    render = render or (cut if is_render else None)
    if not render:
        raise PicError(f"{os.path.basename(cut)} gives cut times and no pictures: pass the render "
                       "of that cut with --render")
    rate = rate or render_fps(render)
    if not rate:
        raise PicError("no frame rate: pass --fps")
    starts = rhythm.starts(durs)
    cuts = starts[1:]
    mids = [s + d / 2 for s, d in zip(starts, durs)]
    areas = face_areas(render, mids, ffmpeg) if cuts else None
    bands = [band(a) for a in areas] if areas else []  # band(None) is 0
    link_base = os.path.dirname(os.path.abspath(out)) if out else None
    lines = [f"# Picture review: {os.path.basename(cut)}", "",
             "Each sheet pairs the last frame before a cut (left) with the first frame after it "
             "(right), a thirds grid over both. Read each one and write what you see on its "
             "reading line: screen direction (does the subject keep moving the same way across "
             "the cut), eye trace (does the eye land where it was led), match-cut candidates. "
             "The tool judges none of this. Cuts are hard cuts only.", "",
             f"- {len(durs)} shots, {len(cuts)} cuts, {rate:g} fps"]
    if areas is None:
        lines.append("- shot scale: not measured (macOS Vision is not available here)")
    else:
        lines.append("- shot scale: the largest face's share of the frame at each shot's middle "
                     "frame. A shot without a face is none. A proxy, not a size label.")
    q = flags(bands, cuts) if areas else []
    lines += ["", "## Questions", ""] + ([f"- {x}" for x in q] or ["- none"]) + ["", "## Cuts", ""]
    made = 0
    for n, t in enumerate(cuts, 1):
        lines.append(f"### {n:03d} · {storypacket.clock(t)} ({t:.2f} s)")
        lines.append("")
        if sheets:
            jpg = os.path.join(sheets, f"{n:03d}_{t:.3f}.jpg")
            try:
                make_sheet(render, t, rate, jpg, ffmpeg)
                made += 1
                link = os.path.relpath(jpg, link_base) if link_base else jpg
                lines += [f"![cut {n}](<{link}>)", ""]
            except PicError as e:
                lines += [f"sheet: {e}", ""]
        lines += [f"- before: shot {n}, {label(areas, bands, n - 1)}",
                  f"- after: shot {n + 1}, {label(areas, bands, n)}",
                  "- reading:", ""]
    if not cuts:
        lines += ["No cuts found.", ""]
    return "\n".join(lines).rstrip() + "\n", \
        f"picreview: {len(cuts)} cut(s), {made} sheet(s), {len(q)} question(s)"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.picreview",
                                 description="A sheet per cut for Claude to read, and a shot-scale "
                                             "track. Offline; flags only.")
    ap.add_argument("cut", help="A render, or an EDL, timeline_items JSON or paper-edit TSV")
    ap.add_argument("--render", help="The render of that cut, for the pictures (needed unless "
                                     "CUT is itself a render)")
    ap.add_argument("--fps", type=float, help="Frame rate (needed for an EDL)")
    ap.add_argument("--sheets", help="Write one JPEG per cut into this folder (refused in any "
                                     "git tree)")
    ap.add_argument("--out", help="Write the report here (refused in any git tree)")
    args = ap.parse_args(argv)
    problems = []
    if args.sheets:
        problems.append(paths.out_problem(os.path.join(args.sheets, "x.jpg"), any_git_tree=True))
    if args.out:
        problems.append(paths.out_problem(args.out, any_git_tree=True))
    problem = next((p for p in problems if p), None)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        if args.sheets:
            os.makedirs(args.sheets, exist_ok=True)
        text, summary = build(args.cut, args.render, args.fps, args.sheets, args.out)
    except (PicError, rhythm.RhythmError, dc.CheckError, cutlist.CutlistError, OSError,
            ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.out:
        paths.write_private(args.out, text, any_git_tree=True)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
