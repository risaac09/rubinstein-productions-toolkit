"""
rpresolve.cutout: the person in a span of a clip, as a ProRes 4444 clip with
alpha that Resolve and ffmpeg lay over an edit. Offline: ffmpeg and macOS
Vision, never Resolve.

ffmpeg extracts the span's frames as PNGs into a temp folder under --out-dir,
scaled to --height (aspect kept). The Swift helper vision_person.swift (built
once into ~/Library/Caches/rpresolve, like vision_faces.swift) runs
VNGeneratePersonSegmentationRequest over them at the chosen quality and writes
one 8-bit matte PNG per frame. ffmpeg alphamerge joins picture and matte
(--feather box-blurs the matte first) and prores_ks profile 4444 writes

    <out-dir>/<stem>_cutout_<in>-<out>.mov    ProRes 4444 with alpha, bt709 tags, picture only

(encoded from yuva444p10le; ffprobe reads a 4444 stream back as yuva444p12le)

at the source's frame rate, with cutout-report.json beside it. The temp
frames are removed. Output is refused inside any git working tree and never
written over the source.

Run from production/:
    python3 -m rpresolve.cutout SOURCE --in S --out S --out-dir DIR
        [--quality accurate|balanced|fast] [--feather PX] [--height 1080]

Exit 0 written; 1 when ffmpeg fails or the helper cannot run (no swiftc, not
macOS); 2 when the output path is refused.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import paths, swiftbuild

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
FFPROBE = os.environ.get("RPRESOLVE_FFPROBE", "/opt/homebrew/bin/ffprobe")
SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
PERSON_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_person.swift")
CACHE_DIR = os.path.expanduser("~/Library/Caches/rpresolve")
QUALITIES = ("accurate", "balanced", "fast")
NO_PERSON_COVERAGE = 0.001   # a frame whose matte averages under this had no person
HELPER_TIMEOUT_S = 3600      # one call over every frame of the span
FFMPEG_TIMEOUT_S = 1800
REPORT_NAME = "cutout-report.json"


class CutoutError(ValueError):
    pass


def person_binary():
    """Path of the compiled person-segmentation helper, built on first use.
    None when swiftc is missing or the build fails."""
    if sys.platform != "darwin":
        return None
    return swiftbuild.build(PERSON_SOURCE, CACHE_DIR, SWIFTC)


def _run(cmd, timeout):
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except OSError as e:
        hint = " (set RPRESOLVE_FFMPEG and RPRESOLVE_FFPROBE)" if cmd[0] in (FFMPEG, FFPROBE) else ""
        raise CutoutError(f"{cmd[0]}: {e}{hint}")
    except subprocess.TimeoutExpired:
        raise CutoutError(f"{os.path.basename(cmd[0])} ran past {timeout} s")
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-1:] or ["no message"]
        raise CutoutError(f"{os.path.basename(cmd[0])} failed: {tail[0]}")
    return proc.stdout


def frame_rate(source, ffprobe=None):
    """The source's r_frame_rate as ffprobe prints it ('30000/1001')."""
    out = _run([ffprobe or FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=r_frame_rate", "-of", "json", source], FFMPEG_TIMEOUT_S)
    try:
        rate = json.loads(out)["streams"][0]["r_frame_rate"]
    except (ValueError, KeyError, IndexError):
        raise CutoutError(f"{source}: no video stream")
    num, _, den = rate.partition("/")
    if not num.isdigit() or int(num) == 0 or (den and (not den.isdigit() or int(den) == 0)):
        raise CutoutError(f"{source}: frame rate {rate!r} is not usable")
    return rate


def span_frames(rate, t_in, t_out):
    """(frames in the span at rate, frames per second)."""
    num, _, den = rate.partition("/")
    fps = int(num) / int(den or 1)
    return max(1, int(round((t_out - t_in) * fps))), fps


def extract_frames(source, t_in, t_out, height, folder, rate, ffmpeg=None):
    """The span's frames as folder/000001.png ..., scaled to height; their paths. The count is
    the span's length in frames at rate: -t alone drops the last frame when the span's end
    falls a rounding error short of its timestamp."""
    count, fps = span_frames(rate, t_in, t_out)
    # shortcut: 8-bit PNG frames, so a 10-bit source lands as 8-bit picture; move to rgb48le
    # when a cut-out is graded after the fact rather than laid over a finished edit
    _run([ffmpeg or FFMPEG, "-v", "error", "-ss", f"{t_in:.6f}", "-i", source,
          "-t", f"{t_out - t_in + 1 / fps:.6f}", "-frames:v", str(count),
          "-an", "-vf", f"scale=-2:{int(height)}", "-pix_fmt", "rgb24", "-start_number", "1",
          os.path.join(folder, "%06d.png")], FFMPEG_TIMEOUT_S)
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".png")]


def segment(frames, mattes_dir, quality):
    """[{"path", "matte", "coverage"} per frame] from the Vision helper, in frame order.
    CutoutError names the frame when the helper could not matte one."""
    binary = person_binary()
    if not binary:
        raise CutoutError("the person-segmentation helper cannot run here: it needs macOS and swiftc "
                          "(Xcode or the command line tools)")
    out = _run([binary, "--quality", quality, mattes_dir, *frames], HELPER_TIMEOUT_S)
    by_path = {}
    for line in out.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        by_path[doc.get("path")] = doc
    rows = []
    for f in frames:
        doc = by_path.get(f)
        if not doc or "matte" not in doc:
            raise CutoutError(f"no matte for {os.path.basename(f)}: {(doc or {}).get('error', 'helper printed nothing')}")
        rows.append(doc)
    return rows


def compose(frames_dir, mattes_dir, rate, feather, out_path, ffmpeg=None):
    """Picture PNGs plus matte PNGs (same names, same size) into a ProRes 4444 clip with alpha."""
    matte = "format=gray" + (f",boxblur=luma_radius={int(feather)}:luma_power=1" if feather > 0 else "")
    # setparams: the -color_* flags alone leave primaries and transfer unknown after a filter graph
    graph = (f"[1:v]{matte}[m];[0:v][m]alphamerge,format=yuva444p10le,"
             "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    with paths.atomic_path(out_path, suffix=".part.mov") as tmp:
        _run([ffmpeg or FFMPEG, "-v", "error", "-framerate", rate, "-i", os.path.join(frames_dir, "%06d.png"),
              "-framerate", rate, "-i", os.path.join(mattes_dir, "%06d.png"),
              "-filter_complex", graph, "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
              "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
              "-r", rate, "-f", "mov", "-y", tmp], FFMPEG_TIMEOUT_S)
    return out_path


def output_name(source, t_in, t_out):
    stem = os.path.splitext(os.path.basename(source))[0]
    return f"{stem}_cutout_{t_in:g}-{t_out:g}.mov"


def guard(source, out_dir, t_in, t_out):
    """The output path, or OutputRefused: inside a git tree, or the source itself."""
    if paths.in_any_git_tree(out_dir):
        raise paths.OutputRefused(f"{out_dir} is inside a git working tree. The output names media; "
                                  "write it outside any repository.")
    os.makedirs(out_dir, exist_ok=True)
    outs = [os.path.join(out_dir, output_name(source, t_in, t_out)), os.path.join(out_dir, REPORT_NAME)]
    for o in outs:
        problem = paths.out_problem(o, any_git_tree=True) or paths.source_problem([o], [source])
        if problem:
            raise paths.OutputRefused(problem)
    return outs[0]


def build(source, t_in, t_out, out_dir, quality="accurate", feather=0, height=1080, segmenter=segment):
    """Cut the person out of source between t_in and t_out into out_dir; the report dict.
    segmenter(frames, mattes_dir, quality) -> rows is the Vision helper, or a stand-in."""
    if quality not in QUALITIES:
        raise CutoutError(f"--quality takes {', '.join(QUALITIES)}")
    if not os.path.isfile(source):
        raise CutoutError(f"{source} is not a file")
    if not 0 <= t_in < t_out:
        raise CutoutError("--in must be at or after 0 and before --out")
    if feather < 0 or height < 16:
        raise CutoutError("--feather takes 0 or more pixels; --height at least 16")
    out_path = guard(source, out_dir, t_in, t_out)
    rate = frame_rate(source)
    work = tempfile.mkdtemp(prefix=".cutout-", dir=out_dir)
    try:
        frames_dir, mattes_dir = os.path.join(work, "frames"), os.path.join(work, "mattes")
        os.mkdir(frames_dir)
        os.mkdir(mattes_dir)
        frames = extract_frames(source, t_in, t_out, height, frames_dir, rate)
        if not frames:
            raise CutoutError(f"no frames between {t_in:g} and {t_out:g} s in {source}")
        rows = segmenter(frames, mattes_dir, quality)
        compose(frames_dir, mattes_dir, rate, feather, out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    coverage = [float(r.get("coverage", 0.0)) for r in rows]
    report = {"source": os.path.abspath(source), "in": t_in, "out": t_out, "rate": rate, "height": int(height),
              "quality": quality, "feather_px": int(feather), "frames": len(frames),
              "expected_frames": span_frames(rate, t_in, t_out)[0],
              "mean_coverage": round(sum(coverage) / len(coverage), 4),
              "no_person_frames": [i + 1 for i, c in enumerate(coverage) if c < NO_PERSON_COVERAGE],
              "output": out_path}
    paths.write_private(os.path.join(out_dir, REPORT_NAME), json.dumps(report, indent=2) + "\n")
    return report


def summary(report):
    lines = [f"cutout: {report['frames']} frame(s) at {report['rate']} fps, quality {report['quality']}, "
             f"mean matte coverage {report['mean_coverage']:.1%}, feather {report['feather_px']} px."]
    if report["frames"] < report["expected_frames"]:
        lines.append(f"WARN: {report['expected_frames']} frame(s) expected in the span, {report['frames']} read: "
                     "the span runs past the end of the clip")
    missing = report["no_person_frames"]
    if missing:
        lines.append(f"WARN: no person found in {len(missing)} frame(s): "
                     f"{', '.join(map(str, missing[:12]))}{', ...' if len(missing) > 12 else ''}")
    lines.append(f"Wrote {report['output']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_arguments(p):
    p.add_argument("source", help="the clip (any video ffmpeg reads)")
    p.add_argument("--in", dest="t_in", type=float, required=True, help="span start, seconds")
    p.add_argument("--out", dest="t_out", type=float, required=True, help="span end, seconds")
    p.add_argument("--out-dir", required=True, help="folder for the clip and its report, outside any git tree")
    p.add_argument("--quality", choices=QUALITIES, default="accurate",
                   help="Vision quality level (default accurate; fast is a coarse, quick matte)")
    p.add_argument("--feather", type=int, default=0, help="box-blur the matte by this many pixels (default 0)")
    p.add_argument("--height", type=int, default=1080, help="frame height of the cut-out, aspect kept (default 1080)")
    return p


def run(args):
    try:
        report = build(args.source, args.t_in, args.t_out, args.out_dir, args.quality, args.feather, args.height)
    except paths.OutputRefused as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (CutoutError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(summary(report))
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.cutout",
                                 description="Cut the person out of a span of a clip into a ProRes 4444 "
                                             "clip with alpha. Offline: ffmpeg and macOS Vision.")
    add_arguments(ap)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
