"""
rpresolve.decon_moves: stages D3 and D4 of decon. Per shot, the camera move read from feature
tracking between frames (OpenCV), as the edit-method vocabulary. Offline; reads the picture with
ffmpeg and never writes to Resolve.

    measure(report, video)      per shot: zoom, pan, tilt, jitter, a speed ramp, a move class
    apply(recipe, moves)        the recipe's slots with move, role, scale and speed filled in
                                (methods.validate passes on the result)

Each shot is read at ANALYSIS_W pixels wide, trimmed off its blends, at most MAX_STEPS frame
pairs. For each pair, corners (goodFeaturesToTrack) are tracked with pyramidal Lucas-Kanade and
a similarity (scale, rotation, shift) is fitted with RANSAC; the steps chain into one transform
from the first frame read to the last. From it:

    scale       the picture's size at the end over its size at the start (1.15: a 15 percent push)
    centre      the zoom's fixed point, 0 to 1 across the frame (zoom-push's `centre`)
    pan, tilt   how far the content at the frame centre moved, as a share of the frame width or
                height; the crop centre moves the other way, so reframe-pan's pan_to is 0.5 - pan
    jitter      the RMS of each step's shift less its 5-step running mean, a share of the width:
                the shake a hand puts in and a tripod, gimbal or keyframe does not
    ramp        the smoothed motion's speed in the last third over the first, when they differ by
                RAMP or more: a speed ramp (retime's speed_to over speed); null otherwise

The class (D3) is the largest of |ln scale|, |pan| and |tilt| when it reaches MOVE_MIN (push, pull,
pan, tilt), handheld when jitter reaches JITTER first, else static. It maps to a slot's `move`
and `role` by MOVE_WORDS: the move vocabulary has no pull, so a pull is a static shot whose
`scale` runs from 1/scale to 1 (a zoom out keyframed in the rebuild); a push is `push-in` with
`scale` [1, scale]; a tilt down is still `tilt-up`, the only tilt word. The role is the commonest
role for that move in the shipped methods (a placeholder until a captioner reads the picture: the
footage-recall captioner may describe the author's own footage only, never a downloaded reference).
"""

import math
import statistics
import subprocess

from . import delivercheck as dc, methods

ANALYSIS_W = 320       # frames are tracked this wide; a move is a share of the frame, not pixels
MAX_STEPS = 120        # frame pairs per shot; a longer shot is read every Nth frame
TRIM_FRAMES = 2        # frames left off each end of a shot (a cut's neighbour, a blend's tail)
REKEY_SHIFT = 0.1      # the keyframe moves on once the picture has shifted this share of its width
REKEY_SCALE = 0.08     # or grown or shrunk this much
MIN_POINTS = 12       # tracked corners a step needs; fewer and the step counts as no motion
MOVE_MIN = 0.03        # |ln scale|, |pan| or |tilt| this large over the shot is a move
JITTER = 0.0025        # step-shift RMS (share of width) this large is a hand on the camera
RAMP = 2.5             # speed ratio, last third over first, that reads as a ramp (a hand-eased pan stays under it)
RAMP_FLOOR = 0.002     # per-frame motion (share of width) below which speed is not judged

# class -> (move word, role): the role is the commonest one for that move in the shipped methods
# (2026-11-22), a placeholder that a person or a captioner of the author's own footage replaces
MOVE_WORDS = {"static": ("static", "face"), "push": ("push-in", "establishing"),
              "pull": ("static", "establishing"), "pan": ("pan", "group"),
              "tilt": ("tilt-up", "detail"), "handheld": ("handheld", "group")}


class MovesError(ValueError):
    pass


def _frames(video, t0, t1, fps, size):
    """Grey frames from t0 to t1 at size (w, h), every step-th frame: (frames, step). The exact size,
    not W:-2, so the bytes always slice as w x h (an autorotated phone clip would not; shortcut: its
    axes are then squeezed, not swapped, until _dims reads the rotation)."""
    n = max(0, int(round((t1 - t0) * fps)))
    step = max(1, math.ceil(n / MAX_STEPS))
    vf = f"scale={size[0]}:{size[1]},format=gray"
    if step > 1:
        vf = f"select='not(mod(n\\,{step}))'," + vf
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{t0:.6f}",
                        "-i", dc._src(video), "-t", f"{max(0.0, t1 - t0):.6f}", "-map", "0:v:0",
                        "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=dc.READ_TIMEOUT_S)
    if p.returncode != 0:
        raise MovesError(f"could not read {video} at {t0:.2f} s: {p.stderr.decode(errors='replace')[-200:]}")
    return p.stdout, step


def _step(cv2, np, a, b):
    """The 3x3 similarity taking frame a's points to frame b's, or None when it cannot be fitted."""
    pts = cv2.goodFeaturesToTrack(a, 400, 0.01, 6)
    if pts is None or len(pts) < MIN_POINTS:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None, winSize=(21, 21), maxLevel=3)
    ok = st.reshape(-1) == 1
    if ok.sum() < MIN_POINTS:
        return None
    m, inl = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if m is None or inl is None or inl.sum() < MIN_POINTS:
        return None
    return np.vstack([m, [0, 0, 1]])


def track(raw, w, h, cv2, np):
    """Per step: a 3x3 similarity or None, for a stack of w x h grey frames as bytes. Each frame is
    fitted against a keyframe, not its neighbour, so small per-step biases (a car driving at the
    lens, grass in the wind) do not add up over a static shot; the keyframe moves on when the
    picture has moved REKEY_SHIFT or REKEY_SCALE from it, or the fit fails."""
    n = len(raw) // (w * h)
    fr = np.frombuffer(raw[:n * w * h], dtype=np.uint8).reshape(n, h, w)
    key, at_key, prev, steps = 0, np.eye(3), np.eye(3), []
    for i in range(1, n):
        a = _step(cv2, np, fr[key], fr[i])
        if a is None and key != i - 1:  # too far from the keyframe: re-key on the previous frame
            key, at_key = i - 1, prev
            a = _step(cv2, np, fr[key], fr[i])
        if a is None:
            steps.append(None)
            key, at_key = i, prev  # no motion this step; carry on from here
            continue
        cur = a @ at_key
        steps.append(cur @ np.linalg.inv(prev))
        prev = cur
        s = math.sqrt(abs(np.linalg.det(a[:2, :2])))
        c = a @ np.array([w / 2, h / 2, 1.0])
        if abs(s - 1) > REKEY_SCALE or math.hypot(c[0] - w / 2, c[1] - h / 2) > REKEY_SHIFT * w:
            key, at_key = i, cur
    return steps


def summarise(steps, w, h, step_frames=1):
    """The shot's move from its per-step similarities (see the module doc)."""
    import numpy as np
    total = np.eye(3)
    shifts = []
    for m in steps:
        m = np.eye(3) if m is None else m
        total = m @ total
        s = math.sqrt(abs(np.linalg.det(m[:2, :2])))
        # the frame centre's shift this step, and the edge shift of its zoom, as a share of the width
        c = m @ np.array([w / 2, h / 2, 1.0])
        shifts.append(((c[0] - w / 2) / w, (c[1] - h / 2) / w, (s - 1) / 2))
    scale = math.sqrt(abs(np.linalg.det(total[:2, :2])))
    c = total @ np.array([w / 2, h / 2, 1.0])
    pan, tilt = (c[0] - w / 2) / w, (c[1] - h / 2) / h
    centre = 0.5
    if abs(scale - 1) > 1e-3:  # the fixed point of x -> s x + t, from the shift alone (rotation ignored)
        centre = min(1.0, max(0.0, total[0, 2] / (1 - total[0, 0]) / w))
    jitter = 0.0
    if len(shifts) >= 5:
        xy = np.array([[a, b] for a, b, _ in shifts])
        k = np.ones(5) / 5
        smooth = np.stack([np.convolve(xy[:, i], k, mode="same") for i in range(2)], 1)
        jitter = float(np.sqrt(((xy - smooth)[2:-2] ** 2).sum(1).mean())) / step_frames
    ramp = None
    if len(shifts) >= 9:  # judged on every shot; only a shot that moves keeps it (below)
        sp = [math.hypot(a, b) + abs(z) for a, b, z in shifts]
        sm = list(np.convolve(sp, np.ones(3) / 3, mode="same"))[1:-1]
        k = len(sm) // 3
        first, last = statistics.median(sm[:k]) / step_frames, statistics.median(sm[-k:]) / step_frames
        if max(first, last) >= RAMP_FLOOR and min(first, last) > 0:
            r = last / first
            if r >= RAMP or r <= 1 / RAMP:
                ramp = round(r, 2)
    zoom = abs(math.log(scale)) if scale > 0 else 0.0
    size, cls = max((zoom, "push" if scale > 1 else "pull"), (abs(pan), "pan"), (abs(tilt), "tilt"))
    if jitter >= JITTER and size < MOVE_MIN * 3:
        cls = "handheld"
    elif size < MOVE_MIN:
        cls = "static"
    if cls in ("static", "handheld"):
        ramp = None  # a ramp is a change of speed in a move; shake and wind have no speed to ramp
    untracked = sum(m is None for m in steps)
    return {"class": cls, "scale": round(scale, 4), "centre": round(centre, 3), "pan": round(pan, 4),
            "tilt": round(tilt, 4), "jitter": round(jitter, 5), "ramp": ramp,
            "steps": len(steps), "untracked": untracked}


def _blended(shot):
    return shot is not None and shot.get("into") in ("dissolve", "dip") and shot.get("blend_in") != ""


def _span(shot, nxt, fps):
    """Seconds to read: the shot inside its blends (a dissolve's boundary is its middle, so both
    neighbours hold half of it), less TRIM_FRAMES at each end."""
    t0 = shot["blend_out"] if _blended(shot) else shot["in"]
    t1 = nxt["blend_in"] if _blended(nxt) else shot["out"]
    return t0 + TRIM_FRAMES / fps, t1 - TRIM_FRAMES / fps


def measure(report, video, n=None):
    """One move per shot of a shots.build report (the first n shots when n is given)."""
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise MovesError(f"decon moves needs {e.name}, which this Python does not have") from e
    from .decon import _dims
    fps = report.get("fps")
    if not fps:
        raise MovesError(f"{video} has no frame rate")
    w0, h0 = _dims(video)
    w = ANALYSIS_W
    h = max(2, int(round(h0 * w / w0 / 2)) * 2)
    out = []
    all_shots = report["shots"]
    for i, shot in enumerate(all_shots[:n]):
        t0, t1 = _span(shot, all_shots[i + 1] if i + 1 < len(all_shots) else None, fps)
        row = {"shot": shot["shot"], "in": shot["in"], "out": shot["out"]}
        if t1 - t0 < 3 / fps:
            row.update(summarise([], w, h), note="too short to track")
        else:
            raw, step = _frames(video, t0, t1, fps, (w, h))
            row.update(summarise(track(raw, w, h, cv2, np), w, h, step))
        if row["steps"] and row["untracked"] > row["steps"] / 2:
            row["note"] = "most steps had too little texture to track; read as static"
        out.append(_words(row))
    return {"video_size": [w0, h0], "fps": fps, "analysis_width": w, "shots": out}


def _words(row):
    """Add the method words and the effect params a rebuild would use."""
    move, role = MOVE_WORDS[row["class"]]
    s = row["scale"]
    scale = None
    if row["class"] == "push":
        scale = [1.0, round(min(10.0, s), 3)]
    elif row["class"] == "pull":
        scale = [round(min(10.0, 1 / s), 3), 1.0]
    effects = []
    if row["class"] == "push":
        effects.append({"effect": "zoom-push", "params": {"centre": row["centre"]},
                        "note": f"the macro pushes 1.0 to 1.15 over 48 frames; this shot measured {s:g}"})
    if row["class"] == "pan":
        effects.append({"effect": "reframe-pan",
                        "params": {"pan_from": 0.5, "pan_to": round(min(1.0, max(0.0, 0.5 - row["pan"])), 4)},
                        "note": "as a share of this frame's width; on a wider source, times the crop's share of it"})
    if row["ramp"]:
        effects.append({"effect": "retime", "params": {"speed": 1.0, "speed_to": row["ramp"]},
                        "note": "relative: the ramp's ratio, not the source's absolute speed"})
    return dict(row, move=move, role=role, slot_scale=scale, effects=effects)


def apply(rec, moves):
    """rec (a decon recipe) with each slot's move, role and scale from moves, validated."""
    shots = moves["shots"]
    if len(shots) < len(rec["slots"]):
        raise MovesError(f"{len(shots)} shots measured for {len(rec['slots'])} slots")
    out = dict(rec, slots=[dict(s, move=m["move"], role=m["role"], scale=m["slot_scale"])
                           for s, m in zip(rec["slots"], shots)])
    placeholder = "Role, move, query and fits are placeholders until shot description exists."
    if placeholder not in rec["summary"]:
        raise MovesError("the recipe summary no longer holds the placeholder sentence decon_moves rewrites")
    out["summary"] = rec["summary"].replace(
        placeholder,
        "Move and scale are measured by feature tracking (decon moves); role is the commonest one "
        "for that move in the shipped methods, and query and fits are placeholders.")
    problems = methods.validate(out)
    if problems:
        raise MovesError("the recipe with moves does not validate: " + "; ".join(problems))
    return out


def summary(moves):
    from collections import Counter
    c = Counter(s["class"] for s in moves["shots"])
    ramps = sum(1 for s in moves["shots"] if s["ramp"])
    return f"moves: {', '.join(f'{k} {v}' for k, v in c.most_common())}" + (f"; {ramps} speed ramps" if ramps else "")
