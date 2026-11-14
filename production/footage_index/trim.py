"""In and out points inside a chosen clip, from a low-resolution motion curve of the region around its keyframe.
Read-only on the footage. A burst centres on the action apex; a hold opens on a settled frame and does not end mid-smear."""
import hashlib
import os
import subprocess

import numpy as np

from . import roots, store

FPS, WIDTH, HEIGHT = 12, 160, 90   # motion curve sampling; the picture is squashed to a fixed size, motion only
EDGE = 0.2          # never closer than this to either end of the take
BURST_S = 0.5       # slots shorter than this are bursts, the rest are holds
SMEAR = 0.07        # motion above this is the camera whipping, not the subject moving: 11 of 12 smeared burst
                    # centres measured 0.078 to 0.172, every readable one 0.073 or less
CONTEXT = 3.0       # seconds of curve each side of the keyframe (more for a long slot)
SETTLE_S = 0.3      # opening and closing windows a hold is judged on
END_W = 0.5         # weight of the closing motion against the opening motion
MID_W = 1.0         # weight of the motion between the opening and closing windows: a pan mid-hold reads badly on a static slot
NEAR_W = 0.01       # cost per second a hold sits from the keyframe, so equal stillness stays near the picture
# Frames of the outgoing shot a window effect shows through. Keep equal to PLATE_FRAMES in the toolkit's
# production/rpresolve/windowplate.py (scale-window full frame at macro frame 37, crop-window at 26; fusion-macros.md).
# The draft farm (drafts._check_plate) refuses a plan whose previous take ends inside this plate.
PLATE_FRAMES = {"scale-window": 37, "crop-window": 26}
PLATE_MARGIN = 0.1  # seconds left past the plate: covers frame rounding and the file running a hair shorter than the index says
ROOTS = None  # a test may set a list of mount dirs; None reads each relpath's root from the configuration


def plate_need(effect, span_seconds, fps=24):
    """Seconds of the outgoing take that must follow its out point for a window on a span of span_seconds, margin included;
    0.0 when effect is not a window. The toolkit counts min(PLATE_FRAMES, the span's frames) frames of it."""
    if effect not in PLATE_FRAMES:
        return 0.0
    return min(PLATE_FRAMES[effect], int(round(span_seconds * fps))) / fps + PLATE_MARGIN


def media_path(relpath):
    for p in [os.path.join(r, relpath) for r in ROOTS] if ROOTS else roots.local_paths(relpath):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(relpath)


def curve(path, t0, t1):
    """(times, motion): mean absolute difference between consecutive FPS frames, grey, WIDTH px wide; times[i] is frame i."""
    n = HEIGHT * WIDTH
    # Kept beside the index (never in git): a re-plan or a second variant asks for the same windows again.
    hit = os.path.join(os.path.dirname(store.DB), "trim-cache", hashlib.sha1(
        f"{path}|{t0:.3f}|{t1:.3f}|{FPS}|{WIDTH}x{HEIGHT}".encode()).hexdigest() + ".npy")
    if os.path.exists(hit):
        m = np.load(hit)
        return t0 + np.arange(1, len(m) + 1) / FPS, m
    for hw in (["-hwaccel", "videotoolbox"], []):  # VideoToolbox where it works
        r = subprocess.run(["ffmpeg", "-v", "error", *hw, "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", "file:" + path,
                            "-vf", f"fps={FPS},scale={WIDTH}:{HEIGHT},format=gray", "-f", "rawvideo", "-"],
                           capture_output=True, timeout=180)
        if r.returncode == 0 and len(r.stdout) >= 4 * n:
            break
    else:
        err = r.stderr.decode(errors="replace").strip().splitlines()
        raise ValueError("no motion curve: " + (err[-1] if err else "ffmpeg gave fewer than 4 frames"))
    f = np.frombuffer(r.stdout[: len(r.stdout) // n * n], np.uint8).reshape(-1, n).astype(np.float32) / 255.0
    m = np.abs(np.diff(f, axis=0)).mean(axis=1)
    os.makedirs(os.path.dirname(hit), exist_ok=True)
    np.save(hit, m)
    return t0 + np.arange(1, len(f)) / FPS, m


def beat_units(seconds, bpm):
    """A slot's length in beats of the method's own tempo, to the half beat, never under half a beat. Counting in the
    method's tempo keeps a burst a burst when the track is slower or faster than the edit the method came from."""
    return max(0.5, round(seconds * bpm / 60 * 2) / 2)


def fit_grid(beats, bpm):
    """The track's beats, every other beat, or beats plus half beats: whichever tempo sits nearest the method's, so a slow
    method is laid in half time instead of being squeezed onto a fast track."""
    if len(beats) < 2:
        raise ValueError(f"beats: {len(beats)} beat(s); a grid needs at least two")
    track = 60 / float(np.median(np.diff(beats)))
    k = min((0.5, 1, 2), key=lambda m: abs(np.log(bpm / (track * m))))
    if k == 0.5:
        return list(beats[::2])
    if k == 2:
        return sorted(list(beats) + [(x + y) / 2 for x, y in zip(beats, beats[1:])])
    return list(beats)


def beat_time(beats, i):
    """Time of beat index i on a track's beat list; half beats are interpolated, the ends run on at the local tempo."""
    n = len(beats)
    if i <= 0:
        return beats[0] + i * (beats[1] - beats[0])
    if i >= n - 1:
        return beats[-1] + (i - (n - 1)) * (beats[-1] - beats[-2])
    k = int(i)
    return beats[k] + (i - k) * (beats[k + 1] - beats[k])


def choose(ts, m, key_t, length, dur, handle=0.0):
    """(in, out, reason) inside [EDGE, dur - EDGE] from a motion curve; ValueError when the take is too short. handle: seconds
    the take must keep playing after the out point (a window on the next slot), when that is more than EDGE."""
    lo, hi = EDGE, dur - max(EDGE, handle)
    if hi - lo < length - 1e-9:
        raise ValueError(f"take of {dur:.2f}s cannot hold {length:.2f}s with {EDGE}s before and {max(EDGE, handle):.2f}s after")
    if length < BURST_S:
        sm = np.convolve(m, np.ones(3) / 3, mode="same")
        feasible = (ts >= lo + length / 2) & (ts <= hi - length / 2)
        if not feasible.any():
            a = min(max(key_t - length / 2, lo), hi - length)
            return round(a, 3), round(a + length, 3), "burst centred on keyframe (curve misses the feasible span)"
        pool = feasible & (sm <= SMEAR)
        if pool.any():
            apex, why = ts[pool][int(np.argmax(sm[pool]))], "motion peak"
        else:  # the whole window is a whip: the least smeared frame beats the fastest
            apex, why = ts[feasible][int(np.argmin(sm[feasible]))], "least smeared frame (window over the smear cap)"
        a = min(max(apex - length / 2, lo), hi - length)
        return round(a, 3), round(a + length, 3), f"burst centred on {why} at {apex:.2f}s"
    best = None
    for s in np.arange(lo, hi - length + 1e-9, 1 / FPS):
        op = m[(ts >= s) & (ts < s + SETTLE_S)]
        en = m[(ts > s + length - SETTLE_S) & (ts <= s + length)]
        if not len(op) or not len(en):
            continue
        mid = m[(ts >= s + SETTLE_S) & (ts <= s + length - SETTLE_S)]  # empty on a hold under 2 x SETTLE_S: no penalty
        cost = op.mean() + END_W * en.mean() + (MID_W * mid.mean() if len(mid) else 0.0) + NEAR_W * abs(s + length / 2 - key_t)
        if best is None or cost < best[0]:
            best = (cost, s, op.mean(), en.mean())
    if best is None:
        raise ValueError("motion curve does not cover any hold position")
    _c, s, o, e = best
    return round(float(s), 3), round(float(s) + length, 3), f"hold opens on settled frame at {s:.2f}s (motion {o:.3f} in, {e:.3f} out)"


def continue_span(prev_out, length, dur, handle=0.0):
    """A 'same take' slot keeps in = the previous out; None when the take runs out (handle: as in choose)."""
    if prev_out + length > dur - max(EDGE, handle):
        return None
    return round(prev_out, 3), round(prev_out + length, 3), "continues the previous take (in = previous out)"


def span(r, length, handle=0.0):
    """(in, out, reason) for a candidate row (needs relpath, duration, t); raises OSError/ValueError when it cannot read."""
    dur, half = r["duration"], max(CONTEXT, length / 2 + 1)
    if not dur:
        raise ValueError("no duration")
    t0, t1 = max(0.0, r["t"] - half), min(dur, r["t"] + half)
    ts, m = curve(media_path(r["relpath"]), t0, t1)
    return choose(ts, m, r["t"], length, dur, handle)
