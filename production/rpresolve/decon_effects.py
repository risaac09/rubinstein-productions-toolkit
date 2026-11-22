"""
rpresolve.decon_effects: find freeze frames and speed ramps in a finished video, and place
them, with its flashes, on the slots of a recipe (`decon recipe --effects`). Offline.

    freezes(video, duration)        [(start, seconds)] from ffmpeg freezedetect, as qc runs it
    ramps(grey, fps, shots, frozen) a rate change inside one shot, from frame differences
    detect(report, video)           {freezes, ramps, flashes}, each placed on a shot
    place(slots, shots, found)      writes the slot fields: exposure-flash, ramp, freeze

A freeze counts only inside a shot after its first frames of motion: a shot that is still from
its first frame is a still picture, not a held frame. A ramp is read as the ratio of the mean
frame difference at the end of a shot to the start (frozen frames left out): a relative speed,
1 at the start, not the source's own speed, and only where there is motion to measure (a
frame-difference ratio saturates for large moves, so a ramp past about 5x reads low). A flash
becomes the slot's exposure-flash only within FLASH_SLOT_S of a slot's start, where that macro
puts it; a flash further in is reported and left off the slot.
"""

import numpy as np

from . import delivercheck as dc, qc, shots as shots_mod

FREEZE_MIN_S = 0.3        # a hold shorter than this is a still moment, not a freeze frame
FREEZE_NOISE_DB = -50.0   # freezedetect n=: frames this alike are the same frame
MOTION_FRAMES = 2         # frames of motion before a freeze, else the shot is a still
EDGE = 3                  # frames at each end of a shot left out of the rate reading (blends, cuts)
RAMP_MIN_FRAMES = 12      # a shot shorter than this has no rate to read
RAMP_RATIO = 1.8          # end rate over start rate, on the fitted line,, either way, at which a shot is a ramp
MIN_MOTION = 0.15         # mean grey levels per frame below which there is no motion to measure
FLASH_SLOT_S = 0.25


def freezes(video, duration):
    """[(start, seconds)] of every freeze of FREEZE_MIN_S or more, a hold that runs to the end
    closed at the duration."""
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(video), "-map", "0:v:0",
                    "-vf", f"freezedetect=n={FREEZE_NOISE_DB}dB:d={FREEZE_MIN_S}", "-f", "null", "-"],
                   dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not scan {video}: {proc.stderr.strip()[-300:]}")
    return [(a, (duration - a) if d is None else d) for a, d in qc.parse_freeze(proc.stderr)]


def ramps(grey, fps, shot_list, frozen):
    """[{shot, from, to, start, end}] for shots whose rate of change differs by RAMP_RATIO between
    the two ends of a line fitted to the shot's frame differences. `from` is 1 and `to` the ratio; start and end are the seconds
    where the rate crosses 10 and 90 percent of the way."""
    diff = np.abs(np.diff(grey, axis=0)).mean(axis=1)
    held = np.zeros(len(diff), bool)
    for a, d in frozen:
        held[max(0, int(round(a * fps))):int(round((a + d) * fps))] = True
    out = []
    for s in shot_list:
        lo, hi = int(round(s["in"] * fps)) + EDGE, min(int(round(s["out"] * fps)) - EDGE, len(diff))
        idx = [i for i in range(lo, hi) if not held[i]]
        if len(idx) < RAMP_MIN_FRAMES:
            continue
        seg = diff[idx]
        sm = np.array([np.median(seg[max(0, j - 2):j + 3]) for j in range(len(seg))])
        slope, icpt = np.polyfit(np.arange(len(sm)), sm, 1)  # a line through the rate: its two ends
        a, b = float(icpt), float(icpt + slope * (len(sm) - 1))
        if min(a, b) < MIN_MOTION or max(a, b) / min(a, b) < RAMP_RATIO:
            continue
        frac = (sm - a) / (b - a)
        first = next((j for j, f in enumerate(frac) if f >= 0.1), 0)
        last = next((j for j in range(len(frac) - 1, -1, -1) if frac[j] <= 0.9), len(frac) - 1)
        end = idx[max(first, min(last + 1, len(idx) - 1))]
        out.append({"shot": s["shot"], "from": 1.0, "to": round(b / a, 2),
                    "start": round(idx[first] / fps, 3), "end": round(end / fps, 3)})
    return out


def detect(report, video):
    """{freezes, ramps, flashes}, each entry with its `shot`. Freezes that cover a shot from its
    first frames are left out (a still picture)."""
    fps, shot_list = report["fps"], report["shots"]
    frozen = freezes(video, report["duration_s"])
    frames = shots_mod.grey_frames(video, report["duration_s"])
    held = []
    for a, d in frozen:
        s = next((x for x in shot_list if x["in"] <= a < x["out"]), None)
        if s and a - s["in"] >= MOTION_FRAMES / fps and a + d <= s["out"] + 1.0 / fps:
            held.append({"shot": s["shot"], "start": round(a, 3), "seconds": round(d, 3)})
    flashes = []
    for t in report.get("flashes", []):
        s = next((x for x in shot_list if x["in"] <= t < x["out"]), None)
        if s:
            flashes.append({"shot": s["shot"], "at": t})
    return {"freezes": held, "flashes": flashes,
            "ramps": ramps(frames, fps, shot_list, [(f["start"], f["seconds"]) for f in held])}


def place(slots, shot_list, found):
    """Write the slot fields: the shots before the end black are slots 0 to len(slots) - 1. A flash
    near a slot's start is its exposure-flash (not on a ramp slot, whose one comp is the retime);
    a ramp is `ramp`; a freeze is `freeze` {at, seconds}. Returns what was placed and what was not."""
    at = {s["shot"]: i for i, s in enumerate(shot_list[:len(slots)])}
    start = {s["shot"]: s["in"] for s in shot_list}
    placed, left = [], []
    for r in found["ramps"]:
        if r["shot"] in at:
            slots[at[r["shot"]]]["ramp"] = [r["from"], max(0.1, min(10.0, r["to"]))]
            placed.append({"kind": "ramp", "slot": at[r["shot"]], "from": r["from"], "to": r["to"]})
    for f in found["freezes"]:
        if f["shot"] in at:
            slot = slots[at[f["shot"]]]
            off = round(f["start"] - start[f["shot"]], 3)
            slot["freeze"] = {"at": off, "seconds": min(f["seconds"], round(slot["seconds"] - off, 3))}
            placed.append({"kind": "freeze", "slot": at[f["shot"]], "at": off, "seconds": slot["freeze"]["seconds"]})
    for f in found["flashes"]:
        i = at.get(f["shot"])
        if i is not None and f["at"] - start[f["shot"]] <= FLASH_SLOT_S and "ramp" not in slots[i] \
                and slots[i]["effect"] is None and slots[i]["speed"] == 1:
            slots[i]["effect"] = "exposure-flash"
            placed.append({"kind": "flash", "slot": i, "at": f["at"]})
        else:
            left.append({"kind": "flash", "at": f["at"]})
    return {"placed": placed, "left_off": left}
