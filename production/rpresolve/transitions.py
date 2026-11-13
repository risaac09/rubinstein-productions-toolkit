"""
rpresolve.transitions: three transitions between two shots, drawn on a finished draft so a
person can judge them before anything is built in Resolve. This module is the draft side only;
the Resolve build of each is its own change.

    hyper-slide    snippets of the neighbouring shots slide two frame widths sideways, mirrored
                   at the edges, with full motion blur (default 10 frames)
    smoke-displace a drifting generated noise field wipes shot B in with a soft edge, bends both
                   pictures, leaves a faint veil, and B arrives a touch bright (default 14)
    luma-fade      B comes in through its own brightness, the colour channels split sideways
                   mid-way, and a two-colour tint fades out (default 16)

A manifest `effects` entry {"span_index": i, "effect": <id>, "params": {"frames": N}} is the
transition INTO span i, centred on the cut between span i - 1 and span i. `frames` is optional,
even, 4 to 48. The ids, defaults, planning and refusals need no numpy; the picture math imports
it when a draft is drawn. Pixel constants are for a 720-wide frame and scale by width / 720.
"""

import random
import subprocess

IDS = ("hyper-slide", "smoke-displace", "luma-fade")
DEFAULT_FRAMES = {"hyper-slide": 10, "smoke-displace": 14, "luma-fade": 16}
MIN_FRAMES, MAX_FRAMES = 4, 48
BASE_WIDTH = 720

SLIDE_SUB = 48                  # sub-frame samples averaged for the motion blur
SLIDE_SEED = 7
SMOKE_SOFT, SMOKE_PUSH, SMOKE_VEIL, SMOKE_HOT = 0.35, 40, 0.25, 0.3
SMOKE_SEED = 11
LUMA_SOFT, LUMA_SPLIT, LUMA_TINT = 0.3, 22, 0.3
LUMA_BLUR = 12                  # box-blur radius of the brightness the wipe follows
TINT_FROM = (40, 60, 255)       # blue ...
TINT_TO = (255, 60, 200)        # ... to magenta
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)


class TransitionError(ValueError):
    """A transition the draft cannot draw; the message is one line."""


def frames_problem(frames):
    """Why `frames` is not a transition length, or None."""
    if not isinstance(frames, int) or isinstance(frames, bool) or frames % 2 \
            or not MIN_FRAMES <= frames <= MAX_FRAMES:
        return f"frames must be an even whole number from {MIN_FRAMES} to {MAX_FRAMES}"
    return None


def plan(effects, span_frames):
    """The transitions in a manifest's `effects`, as drawn on a draft whose spans run
    span_frames frames each: [{span, id, frames, cut, start, end}] by cut, where cut is the
    first frame of the span, start and end bound the two shots. Refuses span 0, a bad length,
    two transitions into one span, a shot shorter than half the window, and windows that
    overlap, before any ffmpeg work."""
    out, seen = [], set()
    for e in effects or []:
        if e.get("effect") not in IDS:
            continue
        i, tid, params = e["span_index"], e["effect"], e.get("params") or {}
        if i == 0:
            raise TransitionError(f"span 0: {tid} needs a shot before it")
        if not isinstance(i, int) or not 0 < i < len(span_frames):
            raise TransitionError(f"span {i}: no such span for the {tid}")
        if i in seen:
            raise TransitionError(f"span {i}: one transition into a span")
        seen.add(i)
        if set(params) - {"frames"}:
            raise TransitionError(f"span {i}: {tid} takes only frames")
        n = params.get("frames", DEFAULT_FRAMES[tid])
        if frames_problem(n):
            raise TransitionError(f"span {i}: {tid} {frames_problem(n)}")
        cut = sum(span_frames[:i])
        for j, side in ((i - 1, "before"), (i, "after")):
            if span_frames[j] < n // 2:
                raise TransitionError(f"span {i}: the {tid} needs {n // 2} frames of the shot "
                                      f"{side} the cut and span {j} has {span_frames[j]}")
        out.append({"span": i, "id": tid, "frames": n, "cut": cut,
                    "start": cut - span_frames[i - 1], "end": cut + span_frames[i]})
    out.sort(key=lambda t: t["cut"])
    for a, b in zip(out, out[1:]):
        if a["cut"] + a["frames"] // 2 > b["cut"] - b["frames"] // 2:
            raise TransitionError(f"span {b['span']}: its {b['id']} overlaps the {a['id']} "
                                  f"into span {a['span']}")
    return out


def note(t):
    """The draft's note for one planned transition."""
    return (f"span {t['span']}: the {t['id']} into it ({t['frames']} frames, centred on the "
            f"cut) is {LEDGER_NOTE}")


LEDGER_NOTE = ("drawn as the draft's own picture math on the finished draft; the Resolve build "
               "follows its own route and will differ")


# ---------------------------------------------------------------------------
# Picture math (numpy; frames are H x W x 3 uint8, windows are lists of them)
# ---------------------------------------------------------------------------

def slide_picks(rng, n, start, cut, end):
    """The source frame each of the n window frames shows for hyper-slide: a random frame of
    the two shots outside the window, held for two window frames. rng: a random.Random."""
    pool = list(range(start, cut - n // 2)) + list(range(cut + n // 2, end)) \
        or list(range(start, end))  # shots exactly half a window long have no outside frames
    out = []
    for k in range(n):
        out.append(rng.choice(pool) if k % 2 == 0 else out[-1])
    return out


def slide(img, k, n):
    """Window frame k of n of hyper-slide: img slid two frame widths, mirrored, blurred."""
    import numpy as np
    h, w = img.shape[:2]
    img = img.astype(np.float32)
    tile = np.concatenate([img, img[:, ::-1]], axis=1)  # mirrored edges, period 2w
    acc = np.zeros(img.shape, np.float32)
    for s in range(SLIDE_SUB):
        shift = int(round((k + s / SLIDE_SUB) / n * 2 * w))
        acc += tile[:, (np.arange(w) + shift) % (2 * w)]
    return (acc / SLIDE_SUB).astype(np.uint8)


def _up(a, h, w):
    """Bilinear upsample of a small grid to h x w."""
    import numpy as np
    ys = np.linspace(0, a.shape[0] - 1.001, h)
    xs = np.linspace(0, a.shape[1] - 1.001, w)
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    return (a[y0][:, x0] * (1 - fy) * (1 - fx) + a[y0 + 1][:, x0] * fy * (1 - fx)
            + a[y0][:, x0 + 1] * (1 - fy) * fx + a[y0 + 1][:, x0 + 1] * fy * fx)


def smoke_grids(rng, h, w):
    """Four octaves of random grids for one smoke-displace, cells the size they are at 720
    wide times width / 720. rng: a numpy Generator."""
    sc = w / BASE_WIDTH
    return [rng.random((round(2 * h / (320 * sc / 2 ** i)) + 2,
                        round(w / (240 * sc / 2 ** i)) + 2)).astype("float32") for i in range(4)]


def _smoke(grids, t, h, w):
    """Fractal noise with its octaves drifting upward at different speeds, rank-flattened to
    0..1 so the wipe runs the whole window."""
    import numpy as np
    f = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for i, g in enumerate(grids):
        big = _up(g, h * 2, w)
        off = int(t * h * (0.25 + 0.2 * i)) % h
        f += amp * big[off:off + h]
        tot += amp
        amp *= 0.45
    f /= tot
    r = np.empty(f.size, np.float32)
    r[np.argsort(f, axis=None)] = np.linspace(0, 1, f.size, dtype=np.float32)
    return r.reshape(f.shape)


def displace(a, b, k, n, grids):
    """Window frame k of n of smoke-displace, from the outgoing frame a and incoming b."""
    import numpy as np
    h, w = a.shape[:2]
    sc = w / BASE_WIDTH
    yy, xx = np.mgrid[0:h, 0:w]
    p = (k + 0.5) / n
    e = p * p * (3 - 2 * p)
    noise = _smoke(grids, p, h, w)
    bend = np.sin(np.pi * p) * SMOKE_PUSH * sc
    dx = ((noise - 0.5) * bend).astype(int)
    dy = ((np.roll(noise, 97, axis=1) - 0.5) * bend).astype(int)
    ix, iy = np.clip(xx + dx, 0, w - 1), np.clip(yy + dy, 0, h - 1)
    a, b = a.astype(np.float32)[iy, ix], b.astype(np.float32)[iy, ix]
    m = np.clip((p * (1 + SMOKE_SOFT) - noise) / SMOKE_SOFT, 0, 1)[..., None]
    m = m * m * (3 - 2 * m)
    b = b * (1 + SMOKE_HOT * (1 - e))
    o = a * (1 - m) + b * m
    veil = (noise ** 2 * SMOKE_VEIL * np.sin(np.pi * p))[..., None] * 255
    o = 255 - (255 - np.clip(o, 0, 255)) * (255 - veil) / 255  # screen
    return np.clip(o, 0, 255).astype(np.uint8)


def _box(a, r):
    """Box blur of radius r, twice over both axes, by cumulative sums with edge padding."""
    import numpy as np
    for _ in range(2):
        for ax in (0, 1):
            pad = [(r + 1, r) if i == ax else (0, 0) for i in range(a.ndim)]
            c = np.cumsum(np.pad(a, pad, mode="edge"), axis=ax)
            hi = np.take(c, range(2 * r + 1, c.shape[ax]), axis=ax)
            lo = np.take(c, range(0, c.shape[ax] - 2 * r - 1), axis=ax)
            a = (hi - lo) / (2 * r + 1)
    return a


def _split(img, px):
    """Red slid left and blue right by px."""
    import numpy as np
    o = img.copy()
    o[..., 0] = np.roll(img[..., 0], -px, axis=1)
    o[..., 2] = np.roll(img[..., 2], px, axis=1)
    return o


def lumafade(a, b, k, n):
    """Window frame k of n of luma-fade, from the outgoing frame a and incoming b."""
    import numpy as np
    sc = a.shape[1] / BASE_WIDTH
    a, b = a.astype(np.float32), b.astype(np.float32)
    p = (k + 0.5) / n
    lum = _box(b @ np.array(LUMA_WEIGHTS, np.float32), max(1, round(LUMA_BLUR * sc)))
    r = np.empty(lum.size, np.float32)
    r[np.argsort(lum, axis=None)] = np.linspace(1, 0, lum.size, dtype=np.float32)  # brightest first
    rank = r.reshape(lum.shape)
    m = np.clip((p * (1 + LUMA_SOFT) - rank) / LUMA_SOFT, 0, 1)
    m = (m * m * (3 - 2 * m))[..., None]
    px = int(round(np.sin(np.pi * p) * LUMA_SPLIT * sc))
    o = _split(a, px) * (1 - m) + _split(b, px) * m
    g = (1 - rank)[..., None]
    tint = np.array(TINT_FROM, np.float32) * (1 - g) + np.array(TINT_TO, np.float32) * g
    amt = LUMA_TINT * np.sin(np.pi * p) * m
    o = o * (1 - amt) + tint * amt
    return np.clip(o, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# The post-pass
# ---------------------------------------------------------------------------

def _decode(src, w, h):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield buf
    p.stdout.close()
    if p.wait() != 0:
        raise TransitionError(f"ffmpeg could not decode {src}")


def apply(src, dst, size, fps, items, total):
    """Rewrite the frames around each planned cut (plan()'s rows) of the draft src into dst,
    keeping its audio and every other frame as they are. Two streaming decodes of src: the
    first keeps only the window and pick frames, the second re-encodes the draft with the
    drawn frames swapped in, so the draft itself is never held whole.
    shortcut: all windows and picks are held at once (about 3 MB a frame at 720p), so dozens of
    long transitions in one draft reach gigabytes; upgrade to decoding one cut at a time."""
    import numpy as np
    w, h = size
    pyrng, nprng = random.Random(SLIDE_SEED), np.random.default_rng(SMOKE_SEED)
    want, picks = set(), {}
    for t in items:
        lo = t["cut"] - t["frames"] // 2
        want.update(range(lo, lo + t["frames"]))
        if t["id"] == "hyper-slide":
            picks[t["span"]] = slide_picks(pyrng, t["frames"], t["start"], t["cut"], t["end"])
            want.update(picks[t["span"]])
    have, count = {}, 0
    for i, buf in enumerate(_decode(src, w, h)):
        count = i + 1
        if i in want:
            have[i] = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    if count != total:
        raise TransitionError(f"the draft has {count} frames and the plan {total}")
    drawn = {}
    for t in items:
        n, c = t["frames"], t["cut"]
        lo = c - n // 2
        grids = smoke_grids(nprng, h, w) if t["id"] == "smoke-displace" else None
        for k in range(n):
            a, b = have[min(lo + k, c - 1)], have[max(lo + k, c)]
            if t["id"] == "hyper-slide":
                img = slide(have[picks[t["span"]][k]], k, n)
            elif grids is not None:
                img = displace(a, b, k, n, grids)
            else:
                img = lumafade(a, b, k, n)
            drawn[lo + k] = img.tobytes()
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-i", src, "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", dst],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for i, buf in enumerate(_decode(src, w, h)):
            enc.stdin.write(drawn.get(i, buf))
    except BrokenPipeError:  # the encoder quit; its exit status below says so in one line
        pass
    finally:
        enc.stdin.close()
    if enc.wait() != 0:
        raise TransitionError(f"ffmpeg could not write {dst}")
