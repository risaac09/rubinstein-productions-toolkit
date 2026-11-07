"""
rpresolve.reel: a talking-head cut manifest and a music track into a
beat-aligned social reel plan, and a 9:16 preview of it. Offline; stdlib for
the plan, ffmpeg for the preview. Resolve gets the plan through
rpresolve.interchange (--format fcpxml) and the MCP import_timeline tool.
Run from production/:
    python3 -m rpresolve.reel plan MANIFEST --music TRACK --beats BEATS.json --out DIR
        [--max-s 45] [--transition dissolve:6|cut] [--punch 1.0,1.2]
        [--cutout 2,5] [--downbeat-offset 0] [--music-in 0] [--clip NAME] [--fps RATE]
    python3 -m rpresolve.reel duck REEL.json --out DIR [--depth 14]
    python3 -m rpresolve.reel preview REEL.json --out FILE.mp4 [--lufs -14] [--crf 18]

plan: the clip's spans play in manifest order from record 0, which is also
the music's time 0 (less --music-in). Every cut lands on a beat of the map
(rpresolve.beats): a span's out-point may sit anywhere in the pause after its
last word, from the word's end plus EDGE_S to the next word's start less
EDGE_S (or MAX_PAUSE_S past the word when no word follows), and its in-point
may move earlier, down to the previous word's end plus EDGE_S, never later.
Two consecutive spans either side of one pause split it, so their edges cannot
cross.
That gives each span a range of lengths; a dynamic programme over (span,
beat) picks the beats that change the original lengths least. The first cut,
the end of the hook, must be a downbeat: every BEATS_PER_BAR-th beat from
--downbeat-offset, since the beat map carries no bar phase. A span with no
beat in its range stops the plan and is named (exit 2); nothing is trimmed
into a word. The last span is the exception: the reel's end is not a cut, so
with no beat in range it ends where its words allow, with a warning. Over
--max-s whole trailing spans are dropped with a warning.
Run wordsnap on the words first: Whisper's edges drift into pauses.

Dressing, in the fields interchange writes (production/interchange.md, Reel
fields): --transition dissolve:N puts an N-frame Cross Dissolve on every cut
whose pauses hold ceil(N/2) frames on both sides, else that cut stays a cut
and the report says so; --punch a,b alternates a static scale over the spans that take one;
--cutout marks spans (cutout: true) for the cutout tool, which take no
punch-in; music goes in as {path, in}.

Output: DIR/reel.json (the manifest with the moved spans and the new
fields, so endcheck, pacing and interchange read it unchanged, plus a
"reel" block: tempo, beats file, one row per cut), DIR/reel-plan.tsv (span,
in, out, record, beat, offset ms, length change, transition, scale, cutout,
the words at the cut). The folder is refused inside any git working tree.

duck: the bed as a stem for the timeline, since FCPXML carries no ducking: the
reel's dialogue is built on the plan's frames, rpresolve.duckplan ducks the
music from its in-point under it, and DIR/reel-ducked.json is the plan with
its music pointing at DIR/music-ducked.wav ({path, in: 0, ducked: true,
source}). Use that file for interchange and preview. Needs numpy.

preview: the plan as a 1080x1920 H.264 file: each span filled and cropped to
9:16 (times its scale), cuts joined by concat and dissolves by xfade over
the same handles the FCPXML uses (so record positions match), a layer with
alpha overlaid where a span names one, the music bed ducked under the
dialogue by sidechaincompress (a stem from duck goes in as it is), loudnorm
to --lufs, AAC 320k 48 kHz stereo,
bt709 tags, faststart. No captions: those come from Resolve's
create_captions after the import, or from vertcut.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from fractions import Fraction

from . import cutlist, paths
from . import delivercheck as dc
from . import interchange as ic

BEATS_PER_BAR = 4
DEFAULT_MAX_S = 45.0
MAX_PAUSE_S = 1.0       # an out-point may drift this far past the last word with no word after it
EDGE_S = cutlist.EDGE_S
W, H = 1080, 1920
DEFAULT_LUFS, TP, LRA = -14.0, -1.5, 11.0   # short-form feeds; -16 for LinkedIn
DUCK = "sidechaincompress=threshold=0.02:ratio=6:attack=30:release=500:level_sc=1"
DUCK_DB = 14.0          # the stem's depth; duckplan calls 12 to 18 a common start under talk
# setparams is what makes the tags reach the file; the encoder flags alone leave them unspecified
COLOR_TAG = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


class ReelError(ValueError):
    pass


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def load_beats(path):
    with open(path, encoding="utf-8") as f:
        b = json.load(f)
    beats = b.get("beats_s") if isinstance(b, dict) else None
    if not isinstance(beats, list) or len(beats) < 2 or not b.get("tempo"):
        raise ReelError(f"{path} is not a beats.json (needs beats_s and tempo)")
    if beats != sorted(beats):
        raise ReelError(f"{path}: beats_s is not in time order")
    b["beats_path"] = os.path.abspath(path)
    return b


def windows(words, span, fps):
    """How far a span's edges may move, in source frames: {in_lo, in_hi,
    out_lo, out_hi, d_orig, first, last}, and how far a dissolve's handles
    may reach before touching a word: {hold_lo, hold_hi}."""
    inside = cutlist.words_between(words, span["in"], span["out"])
    if not inside:
        raise ReelError(f"no words inside {span['in']}-{span['out']} s")
    first, last = inside[0], inside[-1]
    before = [w for w in words if cutlist.midpoint(w) < span["in"]]
    after = [w for w in words if cutlist.midpoint(w) >= span["out"]]
    prev_end = before[-1]["end"] if before else 0.0
    next_start = after[0]["start"] if after else None
    in_lo = max(0.0, prev_end + EDGE_S)
    in_hi = max(in_lo, min(span["in"], first["start"] - EDGE_S))
    out_lo = last["end"] + EDGE_S
    out_hi = (next_start - EDGE_S) if next_start is not None else last["end"] + MAX_PAUSE_S
    out_hi = max(out_hi, out_lo)
    f = lambda t: cutlist.frame(t, fps)
    lo, hi = math.ceil(in_lo * fps - 1e-9), math.floor(out_hi * fps + 1e-9)
    return {"in_lo": lo, "in_hi": f(in_hi), "out_lo": math.ceil(out_lo * fps - 1e-9), "out_hi": hi,
            "d_orig": f(span["out"]) - f(span["in"]), "first": first["word"].strip(),
            "last": last["word"].strip(), "hold_lo": lo, "hold_hi": hi}


def place(win, d):
    """(in_f, out_f) of a span given its chosen length d in frames: the
    out-point moves first, the in-point only when the out-point cannot."""
    out = min(win["out_hi"], max(win["out_lo"], win["in_hi"] + d))
    return out - d, out


def snap(wins, beat_f, offset):
    """The beat index each cut lands on, by dynamic programme: cut k is a
    beat whose distance from cut k-1 (record 0 for the first) lies in span
    k's length range; the first cut is a downbeat. Minimises the summed
    change of length. Returns [beat index per span] or raises ReelError
    naming the first span with no beat in range."""
    n = len(beat_f)
    prev = {None: (0, [])}  # beat index of the last cut -> (cost, path); None is record 0
    for k, w in enumerate(wins):
        lo, hi = w["out_lo"] - w["in_hi"], w["out_hi"] - w["in_lo"]
        if lo > hi:
            raise ReelError(f"span {k + 1}: the words leave it no room ({lo} to {hi} frames)")
        cur = {}
        for last, (cost, path) in prev.items():
            t0 = 0 if last is None else beat_f[last]
            for j in range(0 if last is None else last + 1, n):
                d = beat_f[j] - t0
                if d < lo:
                    continue
                if d > hi:
                    break
                if k == 0 and j % BEATS_PER_BAR != offset % BEATS_PER_BAR:
                    continue
                c = cost + abs(d - w["d_orig"])
                if j not in cur or c < cur[j][0]:
                    cur[j] = (c, path + [j])
        if not cur:
            raise ReelError(f"span {k + 1} ({w['first']} ... {w['last']}): no beat lands {lo} to "
                            f"{hi} frames after the cut before it" +
                            (" on a downbeat" if k == 0 else "") +
                            "; shorten or lengthen the span, or change --downbeat-offset")
        prev = cur
    return min(prev.values())[1]


def plan(manifest, music, beats, out_dir, max_s=DEFAULT_MAX_S, transition="cut", punch=None,
         cutout=(), downbeat_offset=0, music_in=0.0, clip=None, fps=None):
    """The reel plan. Returns (reel manifest dict, rows, warnings)."""
    clips = manifest["clips"]
    if clip is None:
        if len(clips) != 1:
            raise ReelError(f"the manifest holds {len(clips)} clips; name one with --clip")
        c = clips[0]
    else:
        c = next((x for x in clips if x["name"] == clip), None)
        if c is None:
            raise ReelError(f"no clip named {clip!r} in the manifest")
    if not (fps or manifest.get("fps")):
        raise ReelError("the manifest names no fps; pass --fps")
    rate = ic.parse_rate(fps or manifest["fps"])
    fr = manifest.get("fps")
    fr = fr if type(fr) in (int, float) and abs(fr - rate) < ic.NTSC_TOLERANCE else rate
    tr_type, tr_frames = _transition(transition)
    scales = _punch(punch)
    srcs = {s["id"]: s for s in cutlist.sources(manifest)}
    words_by = {}
    for s in srcs.values():
        if not s.get("words"):
            raise ReelError(f"source {s['id']} has no words file; the plan moves cuts between words")
        words_by[s["id"]] = cutlist.load_words(s["words"])
    spans = c["spans"]
    bad = [x for x in cutout if not 1 <= x <= len(spans)]
    if bad:
        raise ReelError(f"--cutout {bad[0]}: the clip has {len(spans)} span(s)")
    # the words stop before the file does; a source not on this machine is checked at preview
    length = {i: ic.probe(s["path"])["frames"] for i, s in srcs.items() if os.path.isfile(s["path"])}
    wins = []
    for s in spans:
        sid = cutlist.source_for(manifest, s)["id"]
        w = windows(words_by[sid], s, fr)
        if length.get(sid):
            w["out_hi"] = w["hold_hi"] = max(w["out_lo"], min(w["out_hi"], length[sid]))
        wins.append(w)
    _share_pauses(manifest, spans, wins)
    # the beats on the record clock; `skipped` keeps the downbeat phase of the file's own map
    beat_s = [b - music_in for b in beats["beats_s"] if b >= music_in]
    skipped = len(beats["beats_s"]) - len(beat_s)
    if len(beat_s) < 2:
        raise ReelError(f"--music-in {music_in:g}: the beat map ends before it")
    beat_f = [cutlist.frame(b, fr) for b in beat_s]
    warnings = []
    cap = cutlist.frame(max_s, fr)
    while True:
        try:
            picks = snap(wins, beat_f, downbeat_offset - skipped)
            ends = [beat_f[j] for j in picks]
        except ReelError:
            if len(wins) == 1:
                raise
            # the reel's end is not a cut: the last span may end where its words allow
            picks = snap(wins[:-1], beat_f, downbeat_offset - skipped)
            ends = [beat_f[j] for j in picks]
            w = wins[-1]
            ends.append(ends[-1] + min(w["out_hi"] - w["in_lo"],
                                       max(w["out_lo"] - w["in_hi"], w["d_orig"])))
            picks = picks + [None]
        if ends[-1] <= cap:
            break
        if len(wins) == 1:
            warnings.append(f"the one span left runs past {max_s:g} s; nothing to drop")
            break
        warnings.append(f"span {len(wins)} dropped: the reel would run past {max_s:g} s")
        wins, spans = wins[:-1], spans[:-1]
    if picks[-1] is None:
        warnings.append(f"span {len(wins)} ends off the beat: no beat in its range")
    new_spans, rows = [], []
    t_prev = punched = 0
    half = (tr_frames + 1) // 2 if tr_type == "dissolve" else 0
    for k, (s, w, j, end) in enumerate(zip(spans, wins, picks, ends)):
        d = end - t_prev
        in_f, out_f = place(w, d)
        ns = {kk: v for kk, v in s.items() if kk not in ("in", "out", "transition", "scale")}
        ns["in"], ns["out"] = round(in_f / fr, 4), round(out_f / fr, 4)
        tr = "cut"
        if k and half:
            room_after = wins[k - 1]["hold_hi"] - prev_out
            room_before = in_f - w["hold_lo"]
            if room_after >= half and room_before >= half:
                ns["transition"] = {"type": "dissolve", "frames": tr_frames}
                tr = f"dissolve {tr_frames}"
            else:
                warnings.append(f"span {k + 1}: the pause holds {min(room_after, room_before)} "
                                f"frame(s), a {tr_frames}-frame dissolve needs {half}; cut instead")
        is_cut = (k + 1) in cutout or bool(s.get("cutout"))
        if is_cut:
            ns["cutout"] = True
        if scales and not is_cut:
            ns["scale"] = scales[punched % len(scales)]
            punched += 1
        new_spans.append(ns)
        rows.append({"span": k + 1, "in": ns["in"], "out": ns["out"], "record_s": round(t_prev / fr, 4),
                     "cut_s": round(end / fr, 4), "beat": None if j is None else j + skipped,
                     "offset_ms": None if j is None else round((end / fr - beat_s[j]) * 1000, 1),
                     "change_frames": d - w["d_orig"], "transition": tr,
                     "scale": ns.get("scale"), "cutout": is_cut,
                     "words": f"{w['first']} ... {w['last']}"})
        t_prev, prev_out = end, out_f
    reel = dict(manifest)
    reel["clips"] = [dict(x, spans=new_spans) if x is c else x for x in clips]
    reel["music"] = {"path": os.path.abspath(music), "in": float(music_in)}
    reel["reel"] = {"beats": beats.get("beats_path"),
                    "tempo": beats["tempo"], "downbeat_offset": downbeat_offset % BEATS_PER_BAR,
                    "max_s": max_s, "frames": t_prev, "seconds": round(t_prev / fr, 3),
                    "cuts": [{"span": r["span"], "beat": r["beat"], "cut_s": r["cut_s"]}
                             for r in rows], "proposal": True}
    return reel, rows, warnings


def _share_pauses(manifest, spans, wins):
    """Two consecutive spans of one source that sit either side of the same
    pause split it at its middle, or at the later span's in-point when that
    comes first (an in-point never moves later), so the edges cannot cross.
    Dissolve handles may still overlap in the pause: hold_lo and hold_hi stay."""
    for k in range(1, len(spans)):
        a, b = spans[k - 1], spans[k]
        if cutlist.source_for(manifest, a)["id"] != cutlist.source_for(manifest, b)["id"]:
            continue
        first, second = (k - 1, k) if a["in"] <= b["in"] else (k, k - 1)
        wa, wb = wins[first], wins[second]
        if wb["in_lo"] < wa["out_hi"]:  # the ranges overlap: one pause between them
            mid = min((wa["out_lo"] + wa["out_hi"]) // 2, wb["in_hi"])
            wa["out_hi"] = max(wa["out_lo"], mid)
            wb["in_lo"] = max(wb["in_lo"], mid)


def _transition(spec):
    if spec in (None, "cut"):
        return "cut", 0
    kind, _, n = spec.partition(":")
    if kind != "dissolve" or not n.isdigit() or int(n) < 2:
        raise ReelError(f"--transition {spec!r}: use cut or dissolve:N (N frames, 2 or more)")
    return "dissolve", int(n)


def _punch(spec):
    if not spec or spec == "none":
        return None
    try:
        scales = [float(x) for x in spec.split(",")]
    except ValueError:
        raise ReelError(f"--punch {spec!r}: numbers separated by commas, such as 1.0,1.2")
    if len(scales) < 2 or any(not 0.1 <= s <= 10 for s in scales):
        raise ReelError("--punch needs two or more scales from 0.1 to 10")
    return scales


def tsv(rows):
    cols = ["span", "in", "out", "record_s", "cut_s", "beat", "offset_ms", "change_frames",
            "transition", "scale", "cutout", "words"]
    lines = ["\t".join(cols)]
    for r in rows:
        lines.append("\t".join("" if r[c] is None else str(r[c]) for c in cols))
    return "\n".join(lines) + "\n"


def write_plan(reel, rows, out_dir):
    rp = os.path.join(out_dir, "reel.json")
    problem = paths.out_problem(rp, any_git_tree=True)  # the file, so a second run may reuse the folder
    if problem:
        raise paths.OutputRefused(problem)
    os.makedirs(out_dir, exist_ok=True)
    from resolve_survey import write_report  # atomic: temp file, then replace
    write_report(rp, json.dumps(reel, indent=2) + "\n")
    write_report(os.path.join(out_dir, "reel-plan.tsv"), tsv(rows))
    return rp


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------

def out_rate(src_fps):
    """The source's rate, capped at 30 (59.94 goes to 30000/1001)."""
    fr = Fraction(src_fps).limit_denominator(1001)
    if fr <= 30:
        return fr
    return Fraction(30000, 1001) if fr.denominator == 1001 else Fraction(30)


def preview_command(reel, out, lufs=DEFAULT_LUFS, crf=18, clip=None, ffmpeg=None,
                    dialogue_only=False):
    """The ffmpeg command for the preview, built on interchange.timeline so
    the frames match the FCPXML. Returns (argv, seconds). dialogue_only
    writes the sound of a reel with no music as it stands, to a WAV."""
    media = {s["id"]: ic.probe(s["path"]) if os.path.isfile(s["path"]) else None
             for s in cutlist.sources(reel)}
    missing = [s["path"] for s in cutlist.sources(reel) if media[s["id"]] is None]
    if missing:
        raise ReelError(f"source not on this machine: {missing[0]}")
    tl = ic.timeline(reel, clip=clip, media=media)
    rate = out_rate(tl["rate"])
    inputs, idx = [], {}

    def inp(path):
        if path not in idx:
            idx[path] = len(inputs)
            inputs.append(path)
        return idx[path]

    for s in tl["sources"].values():
        inp(s["path"])
    f, chain = [], []
    fps = float(tl["rate"])
    fill = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
            f"fps={rate.numerator}/{rate.denominator},format=yuv420p")
    audio_fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    events = tl["events"]
    # a dissolve of N frames starts ceil(N/2) before the cut (interchange's rec_in) and runs
    # N: the clip after it starts that early, the clip before it runs on for the rest of N
    head = [(e["transition"]["frames"] + 1) // 2 if e.get("transition") else 0 for e in events]
    tail = [events[k + 1]["transition"]["frames"] - head[k + 1]
            if k + 1 < len(events) and events[k + 1].get("transition") else 0
            for k in range(len(events))]
    for k, e in enumerate(events):
        src = tl["sources"][e["src"]]
        i = inp(src["path"])
        tr = e.get("transition")
        v_in, v_out = e["v_in"] - head[k], e["v_out"] + tail[k]
        vf = f"[{i}:v]trim=start_frame={v_in}:end_frame={v_out},setpts=PTS-STARTPTS,{fill}"
        if e.get("scale"):
            s = e["scale"]
            vf += f",scale=trunc(iw*{s}/2)*2:trunc(ih*{s}/2)*2," + (
                f"crop={W}:{H}" if s > 1 else f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2")
        if e.get("layer"):
            li = inp(e["layer"]["path"])
            # its first frame sits on the span's in-point, after the dissolve handle
            f.append(f"[{li}:v]trim=start_frame=0:end_frame={e['layer']['frames']},"
                     f"setpts=PTS-STARTPTS+{head[k] / fps:.6f}/TB,"
                     f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0,format=yuva420p,"
                     f"fps={rate.numerator}/{rate.denominator}[l{k}]")
            vf += f"[p{k}];[p{k}][l{k}]overlay=0:0:eof_action=pass"
        f.append(vf + f",settb=AVTB[v{k}]")  # concat's own timebase: xfade needs both inputs alike
        af = (f"[{i}:a]atrim=start={v_in / fps:.6f}:end={v_out / fps:.6f},asetpts=PTS-STARTPTS,"
              f"{audio_fmt}[a{k}]" if src["audio"] else
              f"anullsrc=r=48000:cl=stereo,atrim=end={(v_out - v_in) / fps:.6f}[a{k}]")
        f.append(af)
        chain.append((k, tr))
    v, a, length = "v0", "a0", events[0]["v_out"] - events[0]["v_in"] + tail[0]
    for k, tr in chain[1:]:
        e = events[k]
        body = e["v_out"] - e["v_in"] + head[k] + tail[k]
        if tr:
            d = tr["frames"] / fps
            f.append(f"[{v}][v{k}]xfade=transition=fade:duration={d:.6f}:"
                     f"offset={(length - tr['frames']) / fps:.6f}[x{k}]")
            f.append(f"[{a}][a{k}]acrossfade=d={d:.6f}:c1=tri:c2=tri[y{k}]")
            length += body - tr["frames"]
        else:
            f.append(f"[{v}][{a}][v{k}][a{k}]concat=n=2:v=1:a=1[x{k}][y{k}]")
            length += body
        v, a = f"x{k}", f"y{k}"
    assert length == tl["frames"], (length, tl["frames"])  # the preview is the timeline's length
    total_s = tl["frames"] / fps
    if tl.get("music"):
        m = tl["music"]
        mi = inp(m["path"])
        f.append(f"[{mi}:a]atrim=start={m['in'] / fps:.6f}:end={(m['in'] + m['frames']) / fps:.6f},"
                 f"asetpts=PTS-STARTPTS,{audio_fmt},apad=whole_dur={total_s:.6f}[mus]")
        mix = "amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
        if reel["music"].get("ducked"):
            f.append(f"[mus][{a}]{mix}")
        else:
            f.append(f"[{a}]asplit[d1][d2]")
            f.append(f"[mus][d1]{DUCK}[duck]")
            f.append(f"[duck][d2]{mix}")
        a = "mix"
    f.append(f"[{v}]{COLOR_TAG}[vout]")
    argv = [ffmpeg or dc.FFMPEG, "-v", "error", "-y"]
    for p in inputs:
        argv += ["-i", p]
    if dialogue_only:  # an unmapped picture would stop the graph, so it goes to null
        return argv + ["-filter_complex", ";".join(f), "-map", "[vout]", "-f", "null", "-",
                       "-map", f"[{a}]", "-c:a", "pcm_s24le", "-ar", "48000", out], total_s
    f.append(f"[{a}]loudnorm=I={lufs:g}:TP={TP:g}:LRA={LRA:g}[aout]")
    argv += ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p",
             "-r", f"{rate.numerator}/{rate.denominator}",
             "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
             "-movflags", "+faststart", out]
    return argv, total_s


def preview(reel, out, **kw):
    problem = paths.out_problem(out, any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    argv, seconds = preview_command(reel, out, **kw)
    _ffmpeg(argv, out)
    return seconds


def _ffmpeg(argv, out):
    proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if proc.returncode or not os.path.isfile(out):
        raise ReelError("ffmpeg failed: " + (proc.stderr or "").strip()[:800])  # the cause comes first


def duck(reel, out_dir, depth=DUCK_DB, clip=None):
    """The bed ducked under the reel's dialogue, as a stem. Returns the path
    of reel-ducked.json, the plan with its music pointing at the stem."""
    from . import duckplan  # numpy; the plan and the preview stay stdlib
    music = reel.get("music")
    if not music:
        raise ReelError("the plan has no music to duck")
    if music.get("ducked"):
        raise ReelError("the plan's music is already a ducked stem")
    out_dir = os.path.abspath(out_dir)  # the stem's path goes into the plan
    rp = os.path.join(out_dir, "reel-ducked.json")
    problem = paths.out_problem(rp, any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    os.makedirs(out_dir, exist_ok=True)
    dialogue, bed = os.path.join(out_dir, "dialogue.wav"), os.path.join(out_dir, "bed.wav")
    argv, seconds = preview_command({k: v for k, v in reel.items() if k != "music"}, dialogue,
                                    clip=clip, dialogue_only=True)
    _ffmpeg(argv, dialogue)
    _ffmpeg([dc.FFMPEG, "-v", "error", "-y", "-ss", f"{music.get('in') or 0:.6f}",
             "-t", f"{seconds:.6f}", "-i", music["path"], "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_s24le", bed], bed)
    p = duckplan.render(bed, dialogue, out_dir, depth_db=depth)
    from resolve_survey import write_report  # atomic: temp file, then replace
    write_report(rp, json.dumps(dict(reel, music={"path": p["out"], "in": 0.0, "ducked": True,
                                                  "source": music}), indent=2) + "\n")
    return rp


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def add_arguments(p):
    sub = p.add_subparsers(dest="step", required=True)
    a = sub.add_parser("plan", help="beat-aligned cuts, transitions, punch-ins, music: reel.json")
    a.add_argument("manifest", help="a cut manifest with words (run wordsnap first)")
    a.add_argument("--music", required=True, help="the music file (any format ffmpeg reads)")
    a.add_argument("--beats", required=True, help="its beats.json from rpresolve.beats")
    a.add_argument("--out", required=True, help="folder for reel.json and reel-plan.tsv, "
                                                "outside any git tree")
    a.add_argument("--max-s", type=float, default=DEFAULT_MAX_S,
                   help=f"longest reel in seconds (default {DEFAULT_MAX_S:g})")
    a.add_argument("--transition", default="cut", help="cut (default) or dissolve:N frames")
    a.add_argument("--punch", help="alternating scales by span, such as 1.0,1.2 (default none)")
    a.add_argument("--cutout", default="", help="span numbers to mark cutout: true, such as 2,5")
    a.add_argument("--downbeat-offset", type=int, default=0,
                   help=f"which beat of the map is a downbeat (0 to {BEATS_PER_BAR - 1})")
    a.add_argument("--music-in", type=float, default=0.0, help="seconds into the music at record 0")
    a.add_argument("--clip", help="the manifest clip to plan (needed when it holds several)")
    a.add_argument("--fps", help="frame rate (default: the manifest's)")
    b = sub.add_parser("preview", help="reel.json as a 1080x1920 H.264 file through ffmpeg")
    b.add_argument("reel", help="reel.json from plan")
    b.add_argument("--out", required=True, help="the .mp4 to write, outside any git tree")
    b.add_argument("--lufs", type=float, default=DEFAULT_LUFS,
                   help=f"loudness target (default {DEFAULT_LUFS:g}; -16 for LinkedIn)")
    b.add_argument("--crf", type=int, default=18)
    b.add_argument("--clip", help="the manifest clip (needed when it holds several)")
    c = sub.add_parser("duck", help="the bed as a stem ducked under the dialogue, for the timeline")
    c.add_argument("reel", help="reel.json from plan")
    c.add_argument("--out", required=True, help="folder for the stem and reel-ducked.json, outside "
                                                "any git tree")
    c.add_argument("--depth", type=float, default=DUCK_DB, help=f"dB (default {DUCK_DB:g})")
    c.add_argument("--clip", help="the manifest clip (needed when it holds several)")


def run(args):
    try:
        if args.step == "plan":
            if os.path.realpath(args.manifest) == os.path.realpath(os.path.join(args.out, "reel.json")):
                raise ReelError(f"{args.manifest} is the plan's own output; give another --out")
            manifest = cutlist.load_manifest(args.manifest)
            beats = load_beats(args.beats)
            if not os.path.isfile(args.music):
                raise ReelError(f"{args.music} is not a file")
            cut = [int(x) for x in args.cutout.split(",") if x.strip()] if args.cutout else []
            reel, rows, warnings = plan(manifest, args.music, beats, args.out, max_s=args.max_s,
                                        transition=args.transition, punch=args.punch, cutout=cut,
                                        downbeat_offset=args.downbeat_offset,
                                        music_in=args.music_in, clip=args.clip, fps=args.fps)
            path = write_plan(reel, rows, args.out)
            for w in warnings:
                print(f"warning: {w}", file=sys.stderr)
            r = reel["reel"]
            print(f"reel: {len(rows)} span(s), {r['seconds']} s at {r['tempo']} BPM, every cut on a "
                  f"beat, {sum(1 for x in rows if x['transition'] != 'cut')} dissolve(s) -> {path}")
            return 0
        with open(args.reel, encoding="utf-8") as fh:
            reel = json.load(fh)
        cutlist.validate_manifest(reel, allow_no_speech=True)
        if args.step == "duck":
            print(f"duck: {args.depth:g} dB -> {duck(reel, args.out, args.depth, args.clip)}")
            return 0
        seconds = preview(reel, args.out, lufs=args.lufs, crf=args.crf, clip=args.clip)
        print(f"preview: {seconds:.3f} s, {W}x{H} -> {args.out}")
        return 0
    # ReelError, CutlistError, InterchangeError, OutputRefused; RuntimeError from duckplan's tools
    except (OSError, ValueError, RuntimeError) as e:
        print(f"reel: {e}", file=sys.stderr)
        return 2


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.reel",
                                description=__doc__.split("\n\n")[0])
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
