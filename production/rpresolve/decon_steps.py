"""
rpresolve.decon_steps: the steps of a screen recording that are no cut (`decon recipe --screen`,
`decon compare --key --screen`). Offline.

    find(video, cuts)   [seconds] where part of the picture changes at once and then holds

A menu or panel opening changes a block of the screen in one frame while the rest holds still; at
the shot detector's 32-pixel grey frames that is a 3 to 5 percent blip. Frames are read STEP_W wide
instead; a step is a frame step where at least SHARE of the pixels move by more than LEVEL, with
the two steps before it and the HOLD after it calm (under CALM). A step within GAP_S of a cut is
the cut. Captions change the same way, so this is for screen recordings only: on 2026-11-22 it
found 3 of 6 menu steps a cut detector missed on one Resolve tutorial, with no step near no key step.
"""

import subprocess

import numpy as np

from . import delivercheck as dc

STEP_W = 96      # frames are read this wide (grey); a menu is a few hundred pixels at this size
LEVEL = 12       # grey levels (of 255) a pixel must move to count as changed
SHARE = 0.02     # share of pixels that changes in a step
CALM = 0.005     # share under which a frame step is calm
HOLD = 6         # calm frame steps after a step: the new screen holds
GAP_S = 0.25     # a step this near a cut is that cut


def changes(video):
    """(share of pixels changed per frame step, fps), read STEP_W wide."""
    info = dc.probe(video)
    v = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), None)
    fps = dc.rate(v.get("avg_frame_rate")) if v else None
    if not v or not fps or not v.get("width") or not v.get("height"):
        raise dc.CheckError(f"{video} has no readable video stream")
    h = max(2, round(STEP_W * int(v["height"]) / int(v["width"]) / 2) * 2)
    proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", dc._src(video), "-map", "0:v:0",
                           "-vf", f"scale={STEP_W}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not read {video}: {proc.stderr.decode(errors='replace')[-200:]}")
    g = np.frombuffer(proc.stdout, np.uint8)
    g = g[:len(g) // (STEP_W * h) * STEP_W * h].reshape(-1, STEP_W * h).astype(np.int16)
    return (np.abs(np.diff(g, axis=0)) > LEVEL).mean(axis=1), float(fps)


def from_changes(ch, fps, cuts=()):
    """[seconds] of steps in a per-step change share (see the module docstring)."""
    out = []
    for i in range(2, len(ch) - HOLD):
        if ch[i] >= SHARE and ch[i - 2:i].max() < CALM and ch[i + 1:i + 1 + HOLD].mean() < CALM:
            t = round((i + 1) / fps, 3)
            if all(abs(t - c) > GAP_S for c in cuts):
                out.append(t)
    return out


def find(video, cuts=()):
    ch, fps = changes(video)
    return from_changes(ch, fps, cuts)
