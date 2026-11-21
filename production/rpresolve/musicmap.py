"""
rpresolve.musicmap: a music map of a finished video, as a proposal. Offline: ffmpeg
decodes the audio, numpy does the rest (the beat grid is rpresolve/beats.py).

    build(video)              {beats_s, bpm, sections, ...}; beats_s is what rhythm.load_beats reads
    cut_table(shots, mm)      each cut's nearest beat, offset in frames, and section

Sections: RMS energy in WINDOW_S windows, in dB, smoothed by a median of three, binned low,
mid or high by thirds of the file's own range (a range under FLAT_DB is all mid). A change
of bin is a boundary; a section shorter than MIN_SECTION_S joins the one before it. Labels
are neutral (s1, s2, ...): it hears loudness, not verse or chorus.

Run from production/:
    python3 -m rpresolve.cmd_music_map VIDEO --out music-map.json [--shots shots.json]
"""

import bisect
import os

import numpy as np

from . import beats as beatsmod, cutlist, delivercheck as dc, trimreview

SR = beatsmod.SR
WINDOW_S = 1.0
MIN_SECTION_S = 4.0
FLAT_DB = 6.0   # a file whose windows span less than this has no sections worth marking


class MusicMapError(ValueError):
    pass


def has_audio(video):
    info = dc.probe(video)
    return any(s.get("codec_type") == "audio" for s in info.get("streams") or [])


def sections(y, window=WINDOW_S, min_s=MIN_SECTION_S):
    """[{label, start_s, end_s, level, rms_db}] covering the samples."""
    n = len(y) // int(SR * window)
    if n < 1:
        raise MusicMapError("too little audio for sections")
    w = y[:n * int(SR * window)].reshape(n, -1)
    db = 20 * np.log10(np.sqrt((w ** 2).mean(axis=1)) + 1e-9)
    pad = np.pad(db, 1, mode="edge")
    db_s = np.median(np.stack([pad[:-2], pad[1:-1], pad[2:]]), axis=0)
    lo, hi = float(db_s.min()), float(db_s.max())
    names = ("low", "mid", "high")
    lv = [1] * n if hi - lo < FLAT_DB else [min(2, int(3 * (d - lo) / (hi - lo))) for d in db_s]
    runs = []                                   # [level, first window, last window + 1]
    for i, v in enumerate(lv):
        if runs and runs[-1][0] == v:
            runs[-1][2] = i + 1
        else:
            runs.append([v, i, i + 1])
    merged = []
    for r in runs:
        if merged and (r[2] - r[1]) * window < min_s:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    if len(merged) > 1 and (merged[0][2] - merged[0][1]) * window < min_s:
        merged[1][1] = merged[0][1]             # a short opening joins the next section
        del merged[0]
    out = []
    for r in merged:
        seg = db[r[1]:r[2]]
        mean = float(np.sqrt(np.mean(10 ** (seg / 10))))
        out.append({"label": f"s{len(out) + 1}", "start_s": round(r[1] * window, 3),
                    "end_s": round(r[2] * window, 3), "level": names[int(np.median(lv[r[1]:r[2]]))],
                    "rms_db": round(20 * float(np.log10(mean + 1e-9)), 1)})
    return out


def build(video, prior_bpm=beatsmod.PRIOR_BPM, ffmpeg=None):
    """The music map of a video's audio. Raises MusicMapError (no audio) or BeatsError."""
    if not os.path.isfile(video):
        raise MusicMapError(f"{video} is not a file")
    if not has_audio(video):
        raise MusicMapError(f"{video} has no audio stream")
    raw = cutlist.decode_pcm(video, 0.0, None, ffmpeg, timeout=trimreview.DECODE_TIMEOUT_S)
    y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if len(y) < beatsmod.MIN_SECONDS * SR:
        raise MusicMapError(f"{video} has {len(y) / SR:.1f} s of audio; a music map needs "
                            f"{beatsmod.MIN_SECONDS:g} s or more")
    bpm, bt, phase = beatsmod.beat_map(y, prior_bpm)
    gaps = np.diff(bt)
    return {"file": os.path.abspath(video), "duration_s": round(len(y) / SR, 3),
            "bpm": round(float(60 / np.median(gaps)) if len(gaps) else float(bpm), 2),
            "beats_s": [round(float(t), 3) for t in bt], "phase": phase,
            "sections": sections(y), "method": beatsmod.METHOD, "proposal": True}


def cut_table(report, mm):
    """Per cut of a shots report: {cut, t, nearest_beat_s, offset_frames, section}. The
    offset is signed (cut minus beat), in frames; None without beats."""
    fps, bt = report["fps"], mm["beats_s"]
    rows = []
    for s in report["shots"][1:]:
        t = s["in"]
        i = bisect.bisect_left(bt, t)
        near = min((bt[j] for j in (i - 1, i) if 0 <= j < len(bt)), key=lambda b: abs(b - t), default=None)
        sec = next((x["label"] for x in mm["sections"] if x["start_s"] <= t < x["end_s"]),
                   mm["sections"][-1]["label"] if mm["sections"] and t >= mm["sections"][-1]["end_s"] else None)
        rows.append({"cut": s["shot"], "t": round(t, 3), "nearest_beat_s": near,
                     "offset_frames": None if near is None else round((t - near) * fps, 2), "section": sec})
    return rows


def summary(mm, table=None):
    secs = ", ".join(f"{s['label']} {s['level']} {s['start_s']:g}-{s['end_s']:g} s" for s in mm["sections"])
    out = (f"music map: {mm['bpm']:g} BPM, {len(mm['beats_s'])} beat(s), {len(mm['sections'])} section(s) "
           f"({secs}). A proposal: check it by ear.")
    if table:
        worst = max((abs(r["offset_frames"]) for r in table if r["offset_frames"] is not None), default=0)
        out += f"\n{len(table)} cut(s) tabled; the farthest from a beat is {worst:g} frames."
    return out
