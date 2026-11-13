"""
rpresolve.drafts: the draft farm. Each method plan becomes a 720p H.264 proxy through
ffmpeg, fast, before anything is built in Resolve; the drafts go out as one lettered
sheet of their midpoints for a pick (edit-methods.md#drafts).

    python3 -m rpresolve.drafts PLAN_DIR [PLAN_DIR ...] --out DIR
        [--camera Pyxis | --lut FILE.cube] [--lut-root DIR] [--proxy-dir DIR] [--lanes N] [--no-hw]
        [--exclude FILE] [--crops FILE]

A plan directory holds manifest.json, what methods.to_manifest returned (the spans,
`effects` and `method`), and optionally method.json, the method it was made from (else
the method is loaded by the manifest's method id). For each plan it writes
DIR/<plan>.mp4 (720p at the method's rate, the end black included; silent unless the
manifest carries `music` {path, in}, which is laid under it and faded out) and
DIR/<plan>.notes.txt naming everything the draft does not show; then DIR/drafts.json, whose
entry for each draft holds an `effects` ledger (the manifest's effects and dissolves as
drawn, approximated with a note, or not_drawn, each with its span indices). For two or more
drafts it also writes a lettered sheet of their midpoints (rpstills absheet) in DIR/sheet
with its key sealed beside it in DIR/sheet.key, and DIR/send: the drafts copied as A.mp4,
B.mp4 ... in the sheet's letters, each with a <letter>.notes.txt that opens with its note
count. The letter-to-plan mapping lives only in the sealed key; nothing here prints or logs
it, and drafts.json carries no letters.

Every span is its own ffmpeg job: VideoToolbox decode and h264_videotoolbox encode
when this ffmpeg has them (--no-hw for software), fill-and-crop to the frame, the
span's static scale, the effects ffmpeg can approximate (DRAWN below; APPROX says how each
one differs from its macro), and the look's cube through lut3d. A scale-window or
crop-window after the first span opens over the previous shot playing on from its out point
(windowplate); on the first span it opens from black. The jobs run in parallel lanes: one
lane is timed first, then lanes are added while throughput rises (--lanes N fixes the
count). The spans are joined without re-encoding.

A 9:16 draft is a centre crop of each span. With --crops FILE (the crops.json `vertical plan`
writes: one item per span, in order, each with its centre cx as a share of the source width)
every span is cropped at its own centre instead, a window's plate at the centre of the shot it
continues (what vertical_apply gives the V1 plate), and the notes and the `crops` entry of
drafts.json say which span used which centre. A crops file whose item count or files differ
from the plan's spans is refused; a 16:9 plan ignores it, and says so.

The three transitions (transitions.py: hyper-slide, smoke-displace, luma-fade; a manifest effect
on span i is the transition into it) are in no span's job: after the spans are joined they are
drawn on the finished draft as picture math, the frames around each cut rewritten and the video
re-encoded, audio kept. The ledger lists them under `approximated`.

A source with a recorded anamorphic squeeze (rpresolve/squeeze.py: a person's answer kept
beside the card copy) is stretched to its real shape before it is scaled and cropped, and the
notes say which spans. A plan that draws from a copied card with no recorded answer is refused:
nobody should pick a cut from a squeezed picture.

The look is the method's lutlab look for one camera: `<look name> [<camera>].cube`
under --lut-root (Resolve's LUT/lutlab folder). Without --camera, or with no such cube,
the draft is ungraded and its notes say so. --lut FILE (an absolute .cube path, not with
--camera) draws every span through that one cube instead, skips the lookup, and the notes
add that the build must apply its own look. ffmpeg cannot decode BRAW: a .braw source
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

from . import cutlist, methods, transitions, windowplate
from . import squeeze as rpsqueeze
from . import paths as rppaths

LUT_ROOT = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/lutlab"
SIZES = {"16:9": (1280, 720), "9:16": (720, 1280)}
DRAWN = {"zoom-push", "crop-window", "dip-to-black", "exposure-flash", "text-word",
         "person-matte-text", "scale-window", "retime", "reframe-pan"}
WINDOW_DRIFT = "the window stays centred where the macro's centre drifts about 0.05"
BOX_RATE = "the box opens and closes at a constant rate where the macro eases both ends"
# A scale-window or crop-window after span 0 opens over the previous shot playing on from its out
# point (windowplate), with that shot's own effects not drawn on it. On span 0 there is no
# previous shot: it opens from black, and APPROX says so.
PLAYS_ON = "over the previous shot playing on; its own effects are not drawn on it"
WINDOW_OVER = {"scale-window": PLAYS_ON + "; " + WINDOW_DRIFT,
               "crop-window": PLAYS_ON + "; " + BOX_RATE}
# Drawn, but only as an approximation: what the notes and the ledger say about each. An
# effect in DRAWN with no entry here is drawn as its macro does. "dissolve" is no macro: it is
# the manifest's transition into a span, drawn as a cut.
APPROX = {"text-word": "the word is a white box",
          "person-matte-text": "the title is a red-orange box IN FRONT of the subject, where the "
                               "macro puts the word (its width a guess from the letter count of the "
                               "word, or of the macro's stored WILD when the fill gives none); "
                               "the matte is not drawn, so the draft cannot show the word go "
                               "behind anyone",
          "scale-window": "drawn over black (no previous shot to grow over); " + WINDOW_DRIFT,
          "crop-window": "opens from black (no previous shot to show through); " + BOX_RATE,
          "exposure-flash": "a gain falling to 1 over 12 frames, drawn as the macro's "
                            "multiply (gamma 2.4, about video black, each channel clipped at "
                            "white, T28), after the look when a cube exists for the camera; "
                            "colour management is not drawn, so the absolute levels sit "
                            "below the build's",
          "dip-to-black": "the macro's even-stop gain keys, drawn as a display-space "
                          "multiply fitted on one dark frame (T28)",
          "dissolve": "drawn as a cut"}
# The three transitions (transitions.py) are no macros: the post-pass draws them on the finished
# draft, so they are approximated by definition, and not in DRAWN (no per-span filter).
APPROX.update({t: transitions.LEDGER_NOTE for t in transitions.IDS})

MATTE_SIZE, MATTE_WORD = 0.32, "WILD"  # person-matte-text's Text1 Size and stored word
MATTE_COLOUR = "0xD7371F"  # its Red1/Green1/Blue1 (0.843, 0.216, 0.122) as 8-bit

# Gain in the draft (trap T28). The macros multiply by a gain g in linear light; for a power-law
# display curve that is a multiply by g**(1/gamma) on the encoded values. The flash draws the
# measured one: FLASH_GAMMA 2.4, about video black, each channel (not luma) clipped at video
# white with no roll-off, as the build's master reads (T28). The dip still uses the older
# fitted multiply on luma, GAMMA, which eq draws around black (code 16): fitted to two
# measurements of the flash on one flat dark frame (gain 2 read 65 and gain 10 read 117, 2.55
# draws 62 and 120), never measured on a dip.
FLASH_GAMMA = 2.4
GAMMA = 2.55
FLASH_GAIN = 2        # the macro's own default peak
FLASH_FRAMES = 12     # the macro keys the gain from the peak at frame 0 to 1 at frame 12
DIP_FRAMES = 18       # dip-to-black: the gain keys span 18 frames, 3 apart, ending on the last
DIP_STEP = 3
DIP_KEYS = (1, 0.5, 0.25, 0.125, 0.06, 0.02, 0)  # even in stops (T23)
BLACK_CODE = 16 / 255  # eq's contrast pivots at mid-grey; this puts the multiply on video black
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

def gain_filter(gain):
    """An eq filter that draws a linear-light gain, an ffmpeg expression in the output frame
    number j = round(t*fps), as a display-space multiply (see GAMMA): the video levels around
    black, and the chroma around grey, times gain**(1/GAMMA), per frame. Chroma is held at 3,
    eq's limit, which a gain of 20 would pass (3.2); levels clip at white, and the macro's
    highlight roll-off, if it has one, is not drawn."""
    k = f"pow(max({gain},0),{1 / GAMMA:.4f})"
    return (f"eq=contrast='{k}':brightness='({k}-1)*{0.5 - BLACK_CODE:.4f}':"
            f"saturation='min(3,{k})':eval=frame")


def flash_filter(g, fps):
    """The exposure-flash macro's multiply, drawn per channel: gain g at the first frame,
    smoothstep down to 1 at FLASH_FRAMES, times level**(1/FLASH_GAMMA) about video black, clipped
    at video white. In planar RGB (full range, so black is 0 and white is 255) that is
    clip(v*k): geq does it exactly, and only for the frames of the fall (eq cannot clip a
    channel: it reads the second and third plane as chroma). Rounded to the nearest level; the
    round trip through RGB moves an untouched level by one at most."""
    u = f"min(round(T*{fps})/{FLASH_FRAMES},1)"
    k = f"pow(1+({g:g}-1)*(1-({u})*({u})*(3-2*({u}))),{1 / FLASH_GAMMA:.6f})"
    ch = ":".join(f"{c}='clip({c}(X,Y)*{k}+0.5,0,255)'" for c in "gbr")
    return f"format=gbrp,geq={ch}:enable='lt(round(t*{fps}),{FLASH_FRAMES})'"


def _piecewise(x, ys, step):
    """An ffmpeg expression for the line through ys at x = 0, step, 2*step ...; ys[0] before
    0 and ys[-1] after the last key."""
    e = f"{ys[-1]:g}"
    for k in range(len(ys) - 2, -1, -1):
        e = (f"if(lt({x},{(k + 1) * step}),{ys[k]:g}+({ys[k + 1] - ys[k]:g})*({x}-{k * step})/"
             f"{step},{e})")
    return e


def crop_outside(fps):
    """The crop-window's test for a pixel outside its box (an ffmpeg expression on X, Y, W,
    H and T): width 0 to 0.25 by frame 8, held to 20, full by 26; height 0.6 to 1.0 over
    20 to 26."""
    a, b, c = 8 / fps, 20 / fps, 26 / fps
    fw = (f"if(lt(T,{a:.4f}),0.25*T/{a:.4f},if(lt(T,{b:.4f}),0.25,"
          f"if(lt(T,{c:.4f}),0.25+0.75*(T-{b:.4f})/{c - b:.4f},1)))")
    fh = f"if(lt(T,{b:.4f}),0.6,if(lt(T,{c:.4f}),0.6+0.4*(T-{b:.4f})/{c - b:.4f},1))"
    return f"gt(abs(X-W/2),W*({fw})/2)+gt(abs(Y-H/2),H*({fh})/2)"


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
        # the centre moves linearly; clamped inside the frame like centre_crop. pan_base is
        # the Pan the item already holds (it only offsets the Fusion path), so it is not used.
        crop = (f"crop={w}:{h}:x='clip(({a}+({b}-{a})*t/{seconds:.4f})*iw-ow/2\\,0\\,iw-ow)'"
                f":y='(ih-oh)/2'")
        d = min(float(p.get("fade_out") or 0), seconds)  # the macro's fade to black at the end
        return crop + (f",fade=t=out:st={seconds - d:.4f}:d={d:.4f}" if d > 0 else "")
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
    if effect == "crop-window":
        if over:  # span_filter blends the span onto the previous shot through this box
            return None
        # drawbox has no time variable (its `t` is the border thickness), so a size built
        # from t never moved; geq reads the frame time T. Outside the box is black.
        o = crop_outside(fps)
        return (f"format=yuv420p,geq=lum='if({o},0,lum(X,Y))':cb='if({o},128,cb(X,Y))':"
                f"cr='if({o},128,cr(X,Y))'")
    if effect == "dip-to-black":  # the macro's even-stop gain keys, ending black on the last frame
        n = round(seconds * fps)
        x = f"max(round(t*{fps})-{n - DIP_FRAMES - 1},0)"
        return gain_filter(_piecewise(x, DIP_KEYS, DIP_STEP))
    if effect == "exposure-flash":  # gain g at the first frame, smooth to 1 by frame 12
        return flash_filter(float(p.get("gain", FLASH_GAIN)), fps)
    if effect == "text-word":  # a box where the word goes (drawtext is not in every ffmpeg)
        return (f"drawbox=x={int(w * 0.25)}:y={int(h * 0.34)}:w={int(w * 0.5)}:"
                f"h={int(h * 0.08)}:color=white@0.75:t=fill")
    if effect == "person-matte-text":  # the macro's title as a box, in front of the picture
        # Text1: Center 0.55/0.55 (Fusion's y counts from the bottom), Size 0.32 of the frame
        # height, red-orange. shortcut: cap height and a flat 0.72 em per letter stand in for
        # Arial Bold's metrics; upgrade when drawtext can be relied on. At 9:16 a four-letter
        # word is wider than the frame, as the macro's would be, so the box runs full width.
        # A box side is at least 1: drawbox reads 0 as "to the frame edge".
        em = float(p.get("size", MATTE_SIZE)) * h
        bh = min(h, max(1, int(em * 0.72)))
        bw = min(w, max(1, int(len(str(p.get("text") or MATTE_WORD)) * em * 0.72)))
        x, y = min(max(0, int(w * 0.55 - bw / 2)), w - bw), max(0, int(h * 0.45 - bh / 2))
        return f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color={MATTE_COLOUR}@0.85:t=fill"
    return None


def centre_crop(w, h, cx):
    """The crop of a w x h window whose centre is cx (a share of the scaled width), kept
    inside the frame; the vertical crop is centred."""
    return f"crop={w}:{h}:x='clip({cx:.4f}*iw-ow/2\\,0\\,iw-ow)':y='(ih-oh)/2'"


def span_filter(span, effects, w, h, fps, seconds, lut=None, back=None, cx=None, squeeze=None):
    """(vf, not_drawn) for one span. effects: the span's manifest entries, {effect,
    params?} (a bare macro id is also taken). cx: the crop centre from a crops file (a
    reframe-pan replaces it). back: the filter chain of the previous
    shot's picture, read from input 1 starting at that shot's out point. A scale-window
    or crop-window then opens over that shot playing on (windowplate: as many frames as
    the window needs), and vf is a filter_complex graph (it starts with "[0:v]") instead
    of a plain chain. squeeze: the source's anamorphic factor; the picture is stretched
    to its real shape before anything is scaled or cropped, so cx and a pan stay shares
    of the desqueezed width."""
    ents = [e if isinstance(e, dict) else {"effect": e} for e in effects]
    params = {e["effect"]: e.get("params") or {} for e in ents}
    crop = f"crop={w}:{h}" if cx is None else centre_crop(w, h, cx)
    vf = []
    if "retime" in params:  # before the scale: it moves timestamps, not pixels
        vf.append(effect_filter("retime", seconds, fps, w, h, params["retime"]))
    if "reframe-pan" in params:
        crop = effect_filter("reframe-pan", seconds, fps, w, h, params["reframe-pan"])
    if squeeze and squeeze != 1:
        vf.append(f"scale=trunc(iw*{squeeze:g}/2)*2:ih")
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
    win = next((e for e in windowplate.PLATE_FRAMES if e in params), None)
    over = bool(back) and win is not None
    at = 0  # the window's place in vf: vf[:at] is the span's own picture
    for e in params:
        if e not in ("retime", "reframe-pan"):
            f = effect_filter(e, seconds, fps, w, h, params[e], over=over and e == win)
            if f:
                vf.append(f)
            if over and e == win:
                at = len(vf)
        if e not in DRAWN:
            missing.append(f"{e} (no ffmpeg approximation)")
        elif over and e == win:
            missing.append(f"{e}: {WINDOW_OVER[e]}")
        elif e in APPROX:
            missing.append(f"{e}: {APPROX[e]}")
    # The window layer starts on the draw rate's grid at 0: after -ss it starts a part of a
    # source frame in (29.97 fps), and the window's clock reads the draw rate, not the source's
    # (the full-frame snap landed a frame late). A retime's setpts stays first: it moves the
    # timestamps the fps reads. Over a plate the plate gets the same, below; the fps at the
    # end stays for the other effects.
    grid = f"fps={fps}:start_time=0"
    if win:
        vf.insert(int("retime" in params), grid)
        at += 1  # only the over-a-plate branch reads it
    vf += [f"fps={fps}", "format=yuv420p"]
    if not over:
        return ",".join(vf), missing
    # The plate: the previous shot from its out point, for as many frames as the window
    # needs. After that the span is full frame and covers it, so the last frame is padded
    # on, a little past the span's length and no further (an endless pad never ends when
    # the span's own input is empty; the mix ends with its shorter input, which would cost
    # the span its last frame); it is never on screen.
    total = round(seconds * fps)
    n = windowplate.plate_frames(win, total)
    # The overlay or blend pairs frames by timestamp, so a layer that began a grid step late
    # would leave frame 0 to the other layer alone: the plate is on the grid too.
    plate = (f"[1:v]{back},{grid},trim=end_frame={n},"
             f"tpad=stop_mode=clone:stop={total - n + 2}[bg]")
    if win == "scale-window":
        mix = "[bg][fg]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame:shortest=1"
        fg = ",".join(vf[:at])  # ends on the growing scale: no format or fps after it, which
        # would hold the first frame's size
    else:  # the span where the box is, the plate outside it
        mix = f"[fg][bg]blend=all_expr='if({crop_outside(fps)},B,A)':shortest=1"
        fg = ",".join(vf[:at] + ["format=yuv420p"])
    return ",".join([f"[0:v]{fg}[fg];{plate};{mix}"] + vf[at:]), missing


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


def ledger(manifest):
    """What a draft does with the manifest's effects, by name and span index: {drawn,
    approximated, not_drawn}, each a list of {effect, spans} (approximated also has the
    note APPROX gives). A dissolve into a span counts as the effect "dissolve". A
    scale-window or crop-window is two rows when it is on span 0 (from black) and later (over
    the previous shot playing on, as plan_jobs draws it), so each span carries the note the
    draft's own notes give."""
    spans = {}
    for e in manifest.get("effects") or []:
        spans.setdefault(e["effect"], []).append(e["span_index"])
    for i, sp in enumerate(manifest["clips"][0]["spans"]):
        if (sp.get("transition") or {}).get("type") == "dissolve":
            spans.setdefault("dissolve", []).append(i)
    out = {"drawn": [], "approximated": [], "not_drawn": []}
    for name in sorted(spans):
        idx = sorted(set(spans[name]))
        if name in WINDOW_OVER:
            parts = [([i for i in idx if not i], APPROX[name]),
                     ([i for i in idx if i], WINDOW_OVER[name])]
        else:
            parts = [(idx, APPROX.get(name))]
        for sp, note in parts:
            if not sp:
                continue
            row = {"effect": name, "spans": sp}
            if note:
                out["approximated"].append({**row, "note": note})
            else:
                out["drawn" if name in DRAWN else "not_drawn"].append(row)
    return out


def _check_plate(path, start_s, frames, fps, where):
    """Refuse a window whose outgoing shot's file ends inside the plate: ffmpeg would end
    the span early or pad a frozen frame, and the build refuses the same span. A file whose
    length ffprobe cannot read passes; run_job's frame count still catches a short span."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True)
        have = float(r.stdout.split()[0]) - start_s
    except (OSError, ValueError, IndexError):
        return
    if have < frames / fps - 0.5 / fps:
        raise DraftError(f"{where}: the previous shot has {max(have, 0):.2f} s after its out "
                         f"point and the window needs {frames / fps:.2f} s of it playing on")


def crop_notes(centres, by_span):
    """(notes, rows) for a crops file's centres: one note naming the span runs and their
    centre, one for each span whose reframe-pan sets the crop instead, and the rows
    `drafts.json` keeps ({span, cx, used})."""
    pan = {i for i, es in by_span.items() if any(e["effect"] == "reframe-pan" for e in es)}
    rows = [{"span": i, "cx": c, "used": i not in pan} for i, c in enumerate(centres)]
    runs = []
    for r in rows:
        if r["used"] and runs and runs[-1][1] == r["cx"] and runs[-1][2] == r["span"] - 1:
            runs[-1][2] = r["span"]
        elif r["used"]:
            runs.append([r["span"], r["cx"], r["span"]])
    notes = ["9:16 crops from the crops file: " + "; ".join(
        (f"span {a}" if a == b else f"spans {a}-{b}") + f" at centre {c:.4f}" for a, c, b in runs)]
    notes += [f"span {i}: reframe-pan sets the crop; the crops file's centre is not used"
              for i in sorted(pan)]
    return notes, rows


def plan_jobs(plan, work, hw, camera=None, lut_root=LUT_ROOT, proxy_dir=None, centres=None,
              lut_file=None):
    """(jobs, info) for one plan: a job per span plus one for the end black, each
    {cmd, out, seconds}; info holds the draft's size, frames, look and notes. centres: a
    crop centre per span (a share of the source width) for a 9:16 plan, else the middle."""
    man, m = plan["manifest"], plan["method"]
    fps = m["fps"]
    w, h = SIZES[m["aspect"]]
    lut_src = lut_file or find_lut(m["look"], camera, lut_root)
    notes = []
    if lut_file:
        notes.append(f"drawn through {os.path.basename(lut_file)}, a conversion cube given "
                     "with --lut; the build must apply its own look"
                     + (f" (the method's look {m['look']} is not drawn)" if m["look"] else ""))
    elif m["look"] and not lut_src:
        notes.append(f"look {m['look']} not applied (no cube for camera "
                     f"{camera or '(none given)'})")
    lut = None
    if lut_src:  # a plain name: a cube path with spaces and brackets breaks a filtergraph
        lut = os.path.join(work, "look.cube")
        shutil.copyfile(lut_src, lut)
    srcs = {s["id"]: s["path"] for s in cutlist.sources(man)}
    by_span = {}
    for e in man.get("effects") or []:
        if e["effect"] not in transitions.IDS:  # drawn by the post-pass, not in the span's job
            by_span.setdefault(e["span_index"], []).append(e)
    enc = encoder(hw)
    jobs, frames = [], 0
    spans = man["clips"][0]["spans"]
    open_cards = sorted({rpsqueeze.card_root(p) for p in srcs.values() if rpsqueeze.unanswered(p)})
    if open_cards:  # before any ffmpeg job: a squeezed picture must not be judged as it is
        raise DraftError(f"{plan['name']}: no squeeze recorded for {', '.join(open_cards)}: say "
                         "whether the card is anamorphic (squeeze set FOLDER --factor N; 1 is spherical)")
    try:
        sq = {sid: rpsqueeze.factor(p) for sid, p in srcs.items()}
    except rpsqueeze.SqueezeError as e:
        raise DraftError(f"{plan['name']}: {e}")
    wide = sorted({f for f in sq.values() if f and f != 1})
    if wide:
        notes.append("desqueezed " + ", ".join(
            f"{f:g}x: span(s) " + ", ".join(str(i) for i, sp in enumerate(spans)
                                              if sq.get(sp.get("source") or next(iter(srcs))) == f)
            for f in wide) + " (the squeeze record beside the card)")
    for i, span in enumerate(spans):
        sid = span.get("source") or next(iter(srcs))
        src = media_path(srcs[sid], proxy_dir)
        n = cutlist.frame(span["out"], fps) - cutlist.frame(span["in"], fps)
        seconds = n / fps
        back = prev = None
        win = windowplate.window_effect(man.get("effects"), i)
        if i and win:
            prev = spans[i - 1]  # the window opens over the outgoing shot, playing on
            back = span_filter(prev, [], w, h, fps, 1 / fps, lut,
                               cx=centres[i - 1] if centres else None,
                               squeeze=sq.get(prev.get("source") or next(iter(srcs))))[0]
        vf, missing = span_filter(span, by_span.get(i, []), w, h, fps, seconds, lut, back,
                                  cx=centres[i] if centres else None, squeeze=sq.get(sid))
        notes += [f"span {i}: {x}" for x in missing]
        if back:
            _check_plate(media_path(srcs[prev.get("source") or next(iter(srcs))], proxy_dir),
                         cutlist.frame(prev["out"], fps) / fps,
                         windowplate.plate_frames(win, n), fps,
                         f"{plan['name']} span {i}")
        if (span.get("transition") or {}).get("type") == "dissolve":
            notes.append(f"span {i}: the dissolve into it is {APPROX['dissolve']}")
        out = os.path.join(work, f"{i:03d}.mp4")
        cmd = (["ffmpeg", "-v", "error", "-y"] + (["-hwaccel", "videotoolbox"] if hw else [])
               + ["-ss", f"{span['in']:.4f}", "-i", src]
               + (["-ss", f"{cutlist.frame(prev['out'], fps) / fps:.4f}", "-i",
                   media_path(srcs[prev.get("source") or next(iter(srcs))], proxy_dir)]
                  if back else [])
               + ["-an", "-filter_complex" if back else "-vf", vf,
                  "-frames:v", str(n), "-r", str(fps)] + enc
               + ["-write_tmcd", "0", out])  # no timecode track: every part joins alike
        jobs.append({"cmd": cmd, "out": out, "seconds": seconds, "frames": n, "cwd": work})
        frames += n
    try:  # before any ffmpeg job: a window the shots cannot hold is refused here
        planned = transitions.plan(man.get("effects"), [j["frames"] for j in jobs])
    except transitions.TransitionError as e:
        raise DraftError(f"{plan['name']} {e}")
    if planned:
        try:
            import numpy  # noqa: F401  the post-pass needs it; say so before any ffmpeg job
        except ImportError:
            raise DraftError(f"{plan['name']}: a transition needs numpy, which this Python lacks")
    notes += [transitions.note(t) for t in planned]
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
    info = {"size": [w, h], "fps": fps, "frames": frames, "lut": lut_src, "notes": notes,
            "effects": ledger(man), "transitions": planned}
    if centres:
        more, info["crops"] = crop_notes(centres, by_span)
        notes += more
    return jobs, info


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


def _send(folder, labels, lettered):
    """DIR/send: each draft copied as <letter>.mp4 in the sheet's letters, with a
    <letter>.notes.txt that opens with its note count. labels is the sheet key's letter ->
    {input_index}; lettered the drafts in the order they went onto the sheet. The mapping
    stays in the sealed key: returns only the file names."""
    os.makedirs(folder)
    files = []
    for letter, label in sorted(labels.items()):
        d = lettered[label["input_index"]]
        shutil.copyfile(d["file"], os.path.join(folder, letter + ".mp4"))
        n = len(d["notes"])
        head = f"{n} note{'s' * (n != 1)}" if n else "everything drawn"
        with open(os.path.join(folder, letter + ".notes.txt"), "w", encoding="utf-8") as f:
            f.write(head + "\n" + "".join(x + "\n" for x in d["notes"]))
        files += [letter + ".mp4", letter + ".notes.txt"]
    return {"dir": folder, "files": files}


def span_centres(plan, crops):
    """The crop centre of each span of a 9:16 plan from a crops.json (a loaded dict): item N
    is span N - 1, so the counts and the files must agree. None for a 16:9 plan."""
    if plan["method"]["aspect"] != "9:16":
        return None
    from . import ingest
    man = plan["manifest"]
    srcs = {s["id"]: s["path"] for s in cutlist.sources(man)}
    spans, items = man["clips"][0]["spans"], crops["items"]
    if len(items) != len(spans):
        raise DraftError(f"{plan['name']}: the crops file has {len(items)} item(s), the plan "
                         f"{len(spans)} span(s)")
    for i, (sp, it) in enumerate(zip(spans, items)):
        src = srcs[sp.get("source") or next(iter(srcs))]
        if it.get("path") and ingest.norm_path(it["path"]) != ingest.norm_path(src):
            raise DraftError(f"{plan['name']} span {i}: the crops file's item {i + 1} is another "
                             f"file ({os.path.basename(it['path'])}) than the span's "
                             f"({os.path.basename(src)})")
    return [float(it["cx"]) for it in items]


def build(plan_dirs, out, camera=None, lut_root=LUT_ROOT, proxy_dir=None, lanes=None,
          hw=None, cap=None, exclude=None, crops=None, lut=None):
    """Render every plan into out; returns the drafts.json document. `exclude` is the
    operator's exclude list (cutlist.load_exclude); a plan naming an excluded take is
    refused before any ffmpeg job runs. `crops` is a crops.json (path): each 9:16 plan is
    cropped span by span at its centres (span_centres) instead of at the middle. `lut` is
    one absolute .cube path drawn over every span in place of the look lookup."""
    if lut:
        if camera:
            raise DraftError("--lut and --camera both name a look; give one")
        if not os.path.isabs(lut):
            raise DraftError(f"--lut {lut} is not an absolute path")
        if not lut.lower().endswith(".cube"):
            raise DraftError(f"--lut {os.path.basename(lut)} does not end .cube")
        if not os.path.isfile(lut):
            raise DraftError(f"--lut {lut} does not exist")
    if rppaths.in_any_git_tree(out):
        raise DraftError(f"{out} is inside a git working tree. Drafts show client media; "
                         "write them outside any repository.")
    if os.path.isdir(out) and os.listdir(out):
        raise DraftError(f"{out} is not empty; give a new folder")
    plans = [load_plan(d) for d in plan_dirs]
    ex = None
    if exclude:
        try:
            ex = cutlist.load_exclude(exclude)
        except cutlist.CutlistError as e:
            raise DraftError(str(e))
        hits = [f"{p['name']} span {i}: {os.path.basename(src)} is excluded ({take})"
                for p in plans for i, src, take in cutlist.exclude_matches(p["manifest"], ex["takes"])]
        hits += [f"{p['name']} music: {os.path.basename(src)} is excluded ({take})"
                 for p in plans for src, take in cutlist.exclude_music_matches(p["manifest"], ex["takes"])]
        if hits:
            raise DraftError("excluded take(s) in the plan: " + "; ".join(hits))
    centres = {}
    if crops:
        from . import cmd_vertical
        try:
            c = cmd_vertical.load_crops(crops)
        except cmd_vertical.Refused as e:
            raise DraftError(str(e))
        centres = {id(p): span_centres(p, c) for p in plans}
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
            jobs, info = plan_jobs(p, d, hw, camera, lut_root, proxy_dir, centres.get(id(p)), lut)
            if crops and not centres[id(p)]:
                info["notes"].append("the crops file is not used: the method is 16:9")
            if not ex:
                info["notes"].append(cutlist.NO_EXCLUDE)
            elif ex["not_checked"]:
                info["notes"].append(ex["not_checked"])
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
            if info["transitions"]:
                joined = os.path.join(work, name + ".joined.mp4")
                _concat([j["out"] for j in jobs], joined, work, music, info["frames"] / info["fps"])
                try:
                    transitions.apply(joined, video, info["size"], info["fps"],
                                      info["transitions"], info["frames"])
                except transitions.TransitionError as e:
                    raise DraftError(f"{name}: {e}")
            else:
                _concat([j["out"] for j in jobs], video, work, music, info["frames"] / info["fps"])
            with open(os.path.join(out, name + ".notes.txt"), "w", encoding="utf-8") as f:
                f.write("".join(n + "\n" for n in info["notes"]) or "everything drawn\n")
            drafts.append({"plan": name, "method": p["method"]["id"], "file": video,
                           "seconds": round(info["frames"] / info["fps"], 3), **info})
        doc = {"drafts": drafts, "encoder": "h264_videotoolbox" if hw else "libx264",
               "lanes": lane["lanes"], "lane_probe": lane["probe"],
               "seconds": round(time.monotonic() - began, 2), "sheet": None, "send": None}
        mids, lettered = [], []
        for d in drafts:
            jpg = os.path.join(work, d["plan"] + ".jpg")
            if _midpoint(d["file"], d["seconds"], jpg):
                mids.append(jpg)
                lettered.append(d)
        if len(mids) >= 2:
            try:
                from rpstills import absheet
                sheet = absheet.build(mids, os.path.join(out, "sheet"),
                                      title="Drafts: pick a letter")
                # no absheet.summary: it names each letter's plan, and this file sits beside the folder
                doc["sheet"] = {"dir": os.path.join(out, "sheet"),
                                "key": absheet.key_dir(os.path.join(out, "sheet")),
                                "images": len(mids)}
                doc["send"] = _send(os.path.join(out, "send"), sheet["labels"], lettered)
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
    p.add_argument("--lut", help="one absolute .cube path drawn over every span instead of "
                   "the look lookup (not with --camera); the build applies its own look")
    p.add_argument("--lut-root", default=LUT_ROOT)
    p.add_argument("--proxy-dir", help="where BRAW proxies are (<name>.mov)")
    p.add_argument("--lanes", type=int, help="parallel ffmpeg jobs (default: tuned)")
    p.add_argument("--no-hw", action="store_true", help="software decode and encode")
    p.add_argument("--exclude", help="the operator's exclude list (JSON with `takes`); a plan "
                   "naming an excluded take is refused")
    p.add_argument("--crops", help="a crops.json from `vertical plan`: a 9:16 plan is cropped "
                   "at each span's centre instead of the middle")


def run(args):
    try:
        doc = build(args.plans, args.out, args.camera, args.lut_root, args.proxy_dir,
                    args.lanes, False if args.no_hw else None, exclude=args.exclude,
                    crops=args.crops, lut=args.lut)
    except (DraftError, OSError, ValueError) as e:
        print(f"drafts: {e}", file=sys.stderr)
        return 2
    for d in doc["drafts"]:
        print(f"{d['plan']}: {d['seconds']} s, {len(d['notes'])} note(s)")
    print(f"{len(doc['drafts'])} draft(s) in {doc['seconds']} s, {doc['lanes']} lane(s), "
          f"{doc['encoder']}" + (f"; sheet {doc['sheet']['dir']}" if (doc['sheet'] or {}).get('dir')
                                 else "") + (f"; send {doc['send']['dir']}" if doc.get("send")
                                             else ""))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.drafts", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
