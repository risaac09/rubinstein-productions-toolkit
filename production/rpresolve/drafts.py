"""
rpresolve.drafts: the draft farm. Each method plan becomes a 720p H.264 proxy through
ffmpeg, fast, before anything is built in Resolve; the drafts go out as one lettered
sheet of their midpoints for a pick (edit-methods.md#drafts).

    python3 -m rpresolve.drafts PLAN_DIR [PLAN_DIR ...] --out DIR
        [--camera Pyxis] [--lut-root DIR] [--proxy-dir DIR] [--lanes N] [--no-hw]

A plan directory holds manifest.json, what methods.to_manifest returned (the spans,
`effects` and `method`), and optionally method.json, the method it was made from (else
the method is loaded by the manifest's method id). For each plan it writes
DIR/<plan>.mp4 (720p at the method's rate, the end black included; silent unless the
manifest carries `music` {path, in}, which is laid under it and faded out) and
DIR/<plan>.notes.txt naming everything the draft does not show; then DIR/drafts.json and,
for two or more drafts, a lettered sheet of their midpoints (rpstills absheet) in
DIR/sheet with its key sealed beside it in DIR/sheet.key.

Every span is its own ffmpeg job: VideoToolbox decode and h264_videotoolbox encode
when this ffmpeg has them (--no-hw for software), fill-and-crop to the frame, the
span's static scale, the effects ffmpeg can approximate (zoom-push, crop-window,
dip-to-black, exposure-flash, text-word as a box where the word goes, scale-window over
black, retime with its speed and speed_to, reframe-pan as a moving crop), and the look's
cube through lut3d. The jobs run in parallel lanes: one lane is timed first, then lanes
are added while throughput rises (--lanes N fixes the count). The spans are joined
without re-encoding.

The look is the method's lutlab look for one camera: `<look name> [<camera>].cube`
under --lut-root (Resolve's LUT/lutlab folder). Without --camera, or with no such cube,
the draft is ungraded and its notes say so. ffmpeg cannot decode BRAW: a .braw source
plays from its proxy, <proxy-dir>/<name>.mov (link_proxies' folder), and is refused
without one. Output names client media, so DIR inside any git working tree is refused,
as is a DIR that is not empty.
"""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import cutlist, methods
from . import paths as rppaths

LUT_ROOT = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/lutlab"
SIZES = {"16:9": (1280, 720), "9:16": (720, 1280)}
DRAWN = {"zoom-push", "crop-window", "dip-to-black", "exposure-flash", "text-word",
         "scale-window", "retime", "reframe-pan"}
# Drawn, but only as an approximation: what the sheet's notes say about each.
APPROX = {"text-word": "the word is a white box",
          "scale-window": "drawn over black (no previous shot to grow over)",
          "reframe-pan": "the crop moves from pan_from to pan_to as a share of the source "
                         "width, not Resolve's Pan arithmetic"}
TUNE_GAIN = 1.05  # a lane is kept only when it lifts throughput by 5 percent
HW_BITRATE = "8M"


class DraftError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Plans, looks and media
# ---------------------------------------------------------------------------

def load_plan(plan_dir):
    """{name, manifest, method} for one plan directory."""
    mpath = os.path.join(plan_dir, "manifest.json")
    if not os.path.isfile(mpath):
        raise DraftError(f"{plan_dir}: no manifest.json")
    with open(mpath, encoding="utf-8") as f:
        manifest = json.load(f)
    try:
        cutlist.validate_manifest(manifest, allow_no_speech=True)
    except cutlist.CutlistError as e:
        raise DraftError(f"{mpath}: {e}")
    if len(manifest["clips"]) != 1:
        raise DraftError(f"{mpath}: a plan has one clip")
    own = os.path.join(plan_dir, "method.json")
    ref = own if os.path.isfile(own) else (manifest.get("method") or {}).get("id")
    if not ref:
        raise DraftError(f"{mpath}: no method.json and no method id in the manifest")
    try:
        method = methods.load(ref)
    except ValueError as e:
        raise DraftError(str(e))
    return {"name": os.path.basename(os.path.normpath(plan_dir)), "manifest": manifest,
            "method": method}


def find_lut(look, camera, root=LUT_ROOT):
    """The cube for a lutlab look on one camera, or None."""
    if not look or not camera:
        return None
    with open(os.path.join(methods.LOOK_DIR, look + ".json"), encoding="utf-8") as f:
        name = json.load(f)["name"]
    want = f"{name} [{camera}].cube"
    for folder, _, files in sorted(os.walk(root)):
        if want in files:
            return os.path.join(folder, want)
    return None


def media_path(path, proxy_dir=None):
    """The file ffmpeg reads for a source: the source, or a BRAW source's proxy."""
    if not path.lower().endswith(".braw"):
        return path
    stem = os.path.splitext(os.path.basename(path))[0]
    proxy = os.path.join(proxy_dir, stem + ".mov") if proxy_dir else None
    if not proxy or not os.path.isfile(proxy):
        raise DraftError(f"{path}: ffmpeg cannot decode BRAW, and there is no proxy "
                         f"{proxy or '(give --proxy-dir)'}")
    return proxy


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def effect_filter(effect, seconds, fps, w, h, params=None, over=False):
    """An ffmpeg approximation of a macro over one span, or None when there is none.
    retime is a setpts to put before the scale (source time is the integral of the
    speed, so a ramp is its inverse quadratic); reframe-pan is the span's crop, which
    replaces the centred one."""
    p = params or {}
    if effect == "retime":
        s0 = p.get("speed", 1)
        s1 = p.get("speed_to", s0)
        if s1 == s0:
            return f"setpts=PTS/{s0}"
        k = (s1 - s0) / (2.0 * seconds)  # source time u = s0*t + k*t*t; solve for t
        return f"setpts='(sqrt({s0 * s0}+{4 * k}*T)-{s0})/{2 * k}/TB'"
    if effect == "reframe-pan":
        a, b = p.get("pan_from", 0.5), p.get("pan_to", 0.5)
        return f"crop={w}:{h}:x='(iw-{w})*({a}+({b}-{a})*t/{seconds:.4f})':y='(ih-{h})/2'"
    if effect == "scale-window":  # 0.22 to 0.72 over 36 frames, then full from frame 37
        z = f"if(lt(t*{fps}\\,37)\\,0.22+0.5*min(t*{fps}\\,36)/36\\,1)"
        sc = (f"scale=w='max(2\\,trunc({w}*({z})/2)*2)':h='max(2\\,trunc({h}*({z})/2)*2)':"
              f"eval=frame")
        if over:  # the overlay centres the window on the previous shot
            return sc
        return (sc + f",pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black:eval=frame")  # per frame, or the
        # pad keeps the first frame's window and crops the growing picture to it
    if effect == "zoom-push":  # 1.0 to 1.15 over 48 frames, eased
        u = f"min(t*{fps}/48\\,1)"
        z = f"(1+0.15*({u})*({u})*(3-2*({u})))"
        return (f"scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame,"
                f"crop={w}:{h}")
    if effect == "crop-window":  # width 0 to 0.25 by frame 8, held to 20, full by 26
        a, b, c = 8 / fps, 20 / fps, 26 / fps
        fw = (f"if(lt(T,{a:.4f}),0.25*T/{a:.4f},if(lt(T,{b:.4f}),0.25,"
              f"if(lt(T,{c:.4f}),0.25+0.75*(T-{b:.4f})/{c - b:.4f},1)))")
        fh = f"if(lt(T,{b:.4f}),0.6,if(lt(T,{c:.4f}),0.6+0.4*(T-{b:.4f})/{c - b:.4f},1))"
        # drawbox has no time variable (its `t` is the border thickness), so a size built
        # from t never moved; geq reads the frame time T. Outside the box is black.
        o = f"gt(abs(X-W/2),W*({fw})/2)+gt(abs(Y-H/2),H*({fh})/2)"
        return (f"format=yuv420p,geq=lum='if({o},0,lum(X,Y))':cb='if({o},128,cb(X,Y))':"
                f"cr='if({o},128,cr(X,Y))'")
    if effect == "dip-to-black":  # the last 18 frames to black
        d = min(18 / fps, seconds)
        return f"fade=t=out:st={seconds - d:.4f}:d={d:.4f}"
    if effect == "exposure-flash":  # bright at the first frame, normal by frame 12
        return f"eq=brightness='0.3*max(0\\,1-t*{fps}/12)':eval=frame"
    if effect == "text-word":  # a box where the word goes (drawtext is not in every ffmpeg)
        return (f"drawbox=x={int(w * 0.25)}:y={int(h * 0.34)}:w={int(w * 0.5)}:"
                f"h={int(h * 0.08)}:color=white@0.75:t=fill")
    return None


def span_filter(span, effects, w, h, fps, seconds, lut=None, back=None):
    """(vf, not_drawn) for one span. effects: the span's manifest entries, {effect,
    params?} (a bare macro id is also taken). back: the filter chain of the previous
    shot's picture, read from input 1; a scale-window then grows over it, and vf is a
    filter_complex graph (it starts with "[0:v]") instead of a plain chain."""
    ents = [e if isinstance(e, dict) else {"effect": e} for e in effects]
    params = {e["effect"]: e.get("params") or {} for e in ents}
    crop = f"crop={w}:{h}"
    vf = []
    if "retime" in params:  # before the scale: it moves timestamps, not pixels
        vf.append(effect_filter("retime", seconds, fps, w, h, params["retime"]))
    if "reframe-pan" in params:
        crop = effect_filter("reframe-pan", seconds, fps, w, h, params["reframe-pan"])
    vf += [f"scale={w}:{h}:force_original_aspect_ratio=increase", crop, "setsar=1"]
    s = span.get("scale")
    if s and s > 1:
        vf += [f"scale=trunc({w}*{s}/2)*2:trunc({h}*{s}/2)*2", f"crop={w}:{h}"]
    elif s and s < 1:
        vf += [f"scale=trunc({w}*{s}/2)*2:trunc({h}*{s}/2)*2",
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"]
    if lut:
        vf.append("lut3d=file=look.cube")  # relative: the job runs in the plan's work folder
    missing = []
    over = bool(back) and "scale-window" in params
    for e in params:
        if e not in ("retime", "reframe-pan"):
            f = effect_filter(e, seconds, fps, w, h, params[e], over=over)
            if f:
                vf.append(f)
        if e not in DRAWN:
            missing.append(f"{e} (no ffmpeg approximation)")
        elif e == "scale-window" and over:
            missing.append("scale-window: over the previous shot's last frame, held")
        elif e in APPROX:
            missing.append(f"{e}: {APPROX[e]}")
    vf += [f"fps={fps}", "format=yuv420p"]
    if not over:
        return ",".join(vf), missing
    i = next(k for k, f in enumerate(vf) if f.startswith("scale=w='max(2"))  # the window
    plate = f"[1:v]{back},trim=end_frame=1,loop=loop=-1:size=1[bg]"
    comp = (f"[0:v]{','.join(vf[:i + 1])}[fg];{plate};"
            f"[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame:shortest=1")
    return ",".join([comp] + vf[i + 1:]), missing


# ---------------------------------------------------------------------------
# Jobs and lanes
# ---------------------------------------------------------------------------

def encoder(hw):
    if hw:
        return ["-c:v", "h264_videotoolbox", "-b:v", HW_BITRATE, "-allow_sw", "1"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def has_hw():
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return "h264_videotoolbox" in out


def plan_jobs(plan, work, hw, camera=None, lut_root=LUT_ROOT, proxy_dir=None):
    """(jobs, info) for one plan: a job per span plus one for the end black, each
    {cmd, out, seconds}; info holds the draft's size, frames, look and notes."""
    man, m = plan["manifest"], plan["method"]
    fps = m["fps"]
    w, h = SIZES[m["aspect"]]
    lut_src = find_lut(m["look"], camera, lut_root)
    notes = []
    if m["look"] and not lut_src:
        notes.append(f"look {m['look']} not applied (no cube for camera "
                     f"{camera or '(none given)'})")
    lut = None
    if lut_src:  # a plain name: a cube path with spaces and brackets breaks a filtergraph
        lut = os.path.join(work, "look.cube")
        shutil.copyfile(lut_src, lut)
    srcs = {s["id"]: s["path"] for s in cutlist.sources(man)}
    by_span = {}
    for e in man.get("effects") or []:
        by_span.setdefault(e["span_index"], []).append(e)
    enc = encoder(hw)
    jobs, frames = [], 0
    spans = man["clips"][0]["spans"]
    for i, span in enumerate(spans):
        sid = span.get("source") or next(iter(srcs))
        src = media_path(srcs[sid], proxy_dir)
        n = cutlist.frame(span["out"], fps) - cutlist.frame(span["in"], fps)
        seconds = n / fps
        back = prev = None
        if i and any(e["effect"] == "scale-window" for e in by_span.get(i, [])):
            prev = spans[i - 1]  # the window grows over the outgoing shot's last frame
            back = span_filter(prev, [], w, h, fps, 1 / fps, lut)[0]
        vf, missing = span_filter(span, by_span.get(i, []), w, h, fps, seconds, lut, back)
        notes += [f"span {i}: {x}" for x in missing]
        if (span.get("transition") or {}).get("type") == "dissolve":
            notes.append(f"span {i}: the dissolve into it is drawn as a cut")
        out = os.path.join(work, f"{i:03d}.mp4")
        cmd = (["ffmpeg", "-v", "error", "-y"] + (["-hwaccel", "videotoolbox"] if hw else [])
               + ["-ss", f"{span['in']:.4f}", "-i", src]
               + (["-ss", f"{max(0, cutlist.frame(prev['out'], fps) - 1) / fps:.4f}", "-i",
                   media_path(srcs[prev.get("source") or next(iter(srcs))], proxy_dir)]
                  if back else [])
               + ["-an", "-filter_complex" if back else "-vf", vf,
                  "-frames:v", str(n), "-r", str(fps)] + enc
               + ["-write_tmcd", "0", out])  # no timecode track: every part joins alike
        jobs.append({"cmd": cmd, "out": out, "seconds": seconds, "frames": n, "cwd": work})
        frames += n
    black = methods.end_black(m)
    if black:
        n = int(round(black * fps))
        out = os.path.join(work, "end.mp4")
        jobs.append({"cmd": ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                             f"color=black:s={w}x{h}:r={fps}", "-frames:v", str(n),
                             "-pix_fmt", "yuv420p"] + enc + [out],
                     "out": out, "seconds": n / fps, "frames": n, "cwd": work})
        frames += n
    for step in (man.get("method") or {}).get("ui_steps") or []:
        if step["control"] in ("Zoom keyframes", "Duration"):  # retimes are manifest effects now, and drawn
            notes.append(f"{step['target']}: {step['control']} {step['value']} not drawn")
    return jobs, {"size": [w, h], "fps": fps, "frames": frames, "lut": lut_src,
                  "notes": notes}


def frame_count(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:  # the first line: a source with a timecode track makes ffprobe print the count twice
        return int(r.stdout.split()[0])
    except (ValueError, IndexError):
        return None


def run_job(job):
    r = subprocess.run(job["cmd"], capture_output=True, text=True, cwd=job.get("cwd"))
    if r.returncode != 0 or not os.path.isfile(job["out"]):
        raise DraftError(f"ffmpeg failed on {os.path.basename(job['out'])}: "
                         f"{r.stderr.strip()[-400:]}")
    if job.get("frames"):
        got = frame_count(job["out"])
        if got != job["frames"]:  # ffmpeg stops quietly at the source's end
            raise DraftError(f"{os.path.basename(job['out'])} has {got} frames, the plan "
                             f"{job['frames']}: the fill runs past the end of its source")


def _wave(jobs, lanes, runner):
    began = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(lanes) as pool:
        list(pool.map(runner, jobs))
    wall = max(time.monotonic() - began, 1e-6)
    return sum(j["seconds"] for j in jobs) / wall


def run_lanes(jobs, lanes=None, cap=None, runner=run_job):
    """Run every job. With lanes, that many at once. Without, one job alone is timed,
    then each wave of two jobs per lane adds a lane while throughput (seconds of draft
    per second of wall time) rises by TUNE_GAIN; the best count runs the rest.
    Returns {lanes, probe: [[lanes, throughput]]}."""
    cap = max(1, cap or os.cpu_count() or 1)
    if lanes:
        _wave(jobs, min(lanes, cap), runner)
        return {"lanes": min(lanes, cap), "probe": []}
    todo = list(jobs)
    if not todo:
        return {"lanes": 1, "probe": []}
    best_l, best = 1, _wave(todo[:1], 1, runner)
    probe = [[1, round(best, 3)]]
    todo = todo[1:]
    tuning, n = True, 2
    while todo and tuning and n <= cap:
        wave, todo = todo[:2 * n], todo[2 * n:]
        tp = _wave(wave, n, runner)
        probe.append([n, round(tp, 3)])
        if tp > best * TUNE_GAIN:
            best_l, best, n = n, tp, n + 1
        else:
            tuning = False
    if todo:
        _wave(todo, best_l, runner)
    return {"lanes": best_l, "probe": probe}


# ---------------------------------------------------------------------------
# The farm
# ---------------------------------------------------------------------------

def music_args(music, seconds):
    """ffmpeg arguments that lay the manifest's music {path, in} under a draft of this
    length, faded out at its end; [] without music."""
    if not music:
        return []
    fade = min(1.0, seconds / 4)
    return ["-ss", f"{float(music.get('in') or 0):.4f}", "-t", f"{seconds:.4f}",
            "-i", music["path"], "-map", "0:v", "-map", "1:a",
            "-af", f"afade=t=out:st={seconds - fade:.3f}:d={fade:.3f}",
            "-c:a", "aac", "-b:a", "192k"]


def _concat(parts, out, work, music=None, seconds=0):
    lst = os.path.join(work, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines("file '" + p.replace("'", "'\\''") + "'\n" for p in parts)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst]
                       + music_args(music, seconds)
                       + ["-c:v" if music else "-c", "copy", "-movflags", "+faststart", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise DraftError(f"joining {os.path.basename(out)} failed: {r.stderr.strip()[-400:]}")


def _midpoint(video, seconds, out):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{seconds / 2:.3f}", "-i", video,
                        "-frames:v", "1", "-q:v", "3", out], capture_output=True, text=True)
    return r.returncode == 0 and os.path.isfile(out)


def build(plan_dirs, out, camera=None, lut_root=LUT_ROOT, proxy_dir=None, lanes=None,
          hw=None, cap=None):
    """Render every plan into out; returns the drafts.json document."""
    if rppaths.in_any_git_tree(out):
        raise DraftError(f"{out} is inside a git working tree. Drafts show client media; "
                         "write them outside any repository.")
    if os.path.isdir(out) and os.listdir(out):
        raise DraftError(f"{out} is not empty; give a new folder")
    plans = [load_plan(d) for d in plan_dirs]
    names, seen = [], {}
    for p in plans:
        seen[p["name"]] = seen.get(p["name"], 0) + 1
        names.append(p["name"] if seen[p["name"]] == 1 else f"{p['name']}-{seen[p['name']]}")
    hw = has_hw() if hw is None else hw
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".drafts-", dir=out)
    try:
        all_jobs, infos = [], []
        for name, p in zip(names, plans):
            d = os.path.join(work, name)
            os.makedirs(d)
            jobs, info = plan_jobs(p, d, hw, camera, lut_root, proxy_dir)
            all_jobs += jobs
            infos.append((name, p, jobs, info))
        began = time.monotonic()
        lane = run_lanes(all_jobs, lanes, cap)
        drafts = []
        for name, p, jobs, info in infos:
            video = os.path.join(out, name + ".mp4")
            music = p["manifest"].get("music")
            if music and not os.path.isfile(str(music.get("path"))):
                info["notes"].append("music file not found: the draft is silent")
                music = None
            elif music:
                info["notes"].append(f"music from {float(music.get('in') or 0):.2f} s; "
                                     "check its licence before anything is posted")
            _concat([j["out"] for j in jobs], video, work, music, info["frames"] / info["fps"])
            with open(os.path.join(out, name + ".notes.txt"), "w", encoding="utf-8") as f:
                f.write("".join(n + "\n" for n in info["notes"]) or "everything drawn\n")
            drafts.append({"plan": name, "method": p["method"]["id"], "file": video,
                           "seconds": round(info["frames"] / info["fps"], 3), **info})
        doc = {"drafts": drafts, "encoder": "h264_videotoolbox" if hw else "libx264",
               "lanes": lane["lanes"], "lane_probe": lane["probe"],
               "seconds": round(time.monotonic() - began, 2), "sheet": None}
        mids = []
        for d in drafts:
            jpg = os.path.join(work, d["plan"] + ".jpg")
            if _midpoint(d["file"], d["seconds"], jpg):
                mids.append(jpg)
        if len(mids) >= 2:
            try:
                from rpstills import absheet
                sheet = absheet.build(mids, os.path.join(out, "sheet"),
                                      title="Drafts: pick a letter")
                doc["sheet"] = {"dir": os.path.join(out, "sheet"),
                                "key": absheet.key_dir(os.path.join(out, "sheet")),
                                "summary": absheet.summary(sheet)}
            except (ImportError, ValueError) as e:  # Pillow missing, more than 9 drafts
                doc["sheet"] = {"error": f"no sheet: {e}"}
        with open(os.path.join(out, "drafts.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        return doc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def add_arguments(p):
    p.add_argument("plans", nargs="+", metavar="PLAN_DIR")
    p.add_argument("--out", required=True)
    p.add_argument("--camera", help="the camera whose look cube to use (Pyxis, S5II, RCM)")
    p.add_argument("--lut-root", default=LUT_ROOT)
    p.add_argument("--proxy-dir", help="where BRAW proxies are (<name>.mov)")
    p.add_argument("--lanes", type=int, help="parallel ffmpeg jobs (default: tuned)")
    p.add_argument("--no-hw", action="store_true", help="software decode and encode")


def run(args):
    try:
        doc = build(args.plans, args.out, args.camera, args.lut_root, args.proxy_dir,
                    args.lanes, False if args.no_hw else None)
    except (DraftError, OSError, ValueError) as e:
        print(f"drafts: {e}", file=sys.stderr)
        return 2
    for d in doc["drafts"]:
        print(f"{d['plan']}: {d['seconds']} s, {len(d['notes'])} note(s)")
    print(f"{len(doc['drafts'])} draft(s) in {doc['seconds']} s, {doc['lanes']} lane(s), "
          f"{doc['encoder']}" + (f"; sheet {doc['sheet']['dir']}" if (doc['sheet'] or {}).get('dir')
                                 else ""))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.drafts", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
