"""
rpresolve.decon_sound: the sound map of a reference video, stage D8 of decon. Offline: ffmpeg
reads the audio, numpy finds the onsets; it never writes to Resolve.

    build(video, report)    {integrated_lufs, lra_lu, curve_db, spans, hits, ...} for a video
                            with audio, given its shots.build report (the cuts)
    for_recipe(sm, n)       the part a recipe carries as its `sound` field (methods validates it)
    compare_part(a, b)      0 to 1, how near two sound maps are (decon.compare's `sound` part)

The loudness curve is ebur128's momentary loudness (qc's and audioreport's own pass), the
mean of each second's 100 ms readings in LUFS; a second with no reading is null. Spans are
what `vertcut kinds` says (speech, sung, music, hallucination, uncertain), as time ranges
only: from a <name>.kinds.json beside the video or named with `kinds`, else `vertcut kinds`'s
own audio-only classifier (segkind.py, run on the video; no transcript is read or made).
Audio-only kinds are tuned for recall of singing and cannot tell a voice from a pad, so read
spans without a transcript as a proposal.

A hit is an audio onset within HIT_TOL_FRAMES of a cut: the short-time RMS (10 ms windows,
5 ms hop) rising ONSET_DB or more within 20 ms to above FLOOR_DB, at least MIN_GAP_S after
the last onset. Its offset is signed, in frames, onset minus cut. The onset time is good to
about 10 ms; a swell or a reverb tail is not an onset.
"""

import json
import math
import os
import re
import subprocess
import sys

import numpy as np

from . import audioreport as ar, cutlist, delivercheck as dc, trimreview

HIT_TOL_FRAMES = 2
HOP = 80                 # 5 ms at 16 kHz
WIN = 160                # 10 ms
ONSET_DB = 10.0          # rise within BACK frames
BACK = 4
STEP_DB = 6.0            # and one step of this much into the rise
FLOOR_DB = -45.0         # an onset must reach this (dBFS RMS)
MIN_GAP_S = 0.05
LUFS_SCALE = 6.0        # an integrated loudness this far apart scores zero
MAX_SECONDS = 1800.0
KINDS = ("speech", "sung", "music", "hallucination", "uncertain")
SEGKIND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "tools", "vertcut", "lib", "segkind.py")
_RANGE = re.compile(r"^\s+(\d+(?:\.\d+)?)-\s*(\d+(?:\.\d+)?)\s+(" + "|".join(KINDS) + r")\b")


class SoundError(ValueError):
    pass


def onsets(y, sr=16000):
    """Onset times (s) in mono samples y (float, -1 to 1)."""
    n = (len(y) - WIN) // HOP + 1
    if n < BACK + 2:
        return []
    idx = np.arange(n)[:, None] * HOP + np.arange(WIN)[None, :]
    env = 10 * np.log10((y[idx] ** 2).mean(axis=1) + 1e-12)
    out, last = [], -1e9
    for j in np.flatnonzero(env[1:] - env[:-1] >= STEP_DB):
        low = env[max(0, j - BACK):j + 1].min()
        if env[j + 1] < FLOOR_DB or env[j + 1] - low < ONSET_DB:
            continue
        t = ((j + 1) * HOP + WIN / 2) / sr
        if t - last >= MIN_GAP_S:
            out.append(round(float(t), 3))
            last = t
    return out


def hits_at_cuts(ons, cuts, fps):
    """[{cut, t, onset_s, offset_frames}] for each cut (1-based, cut k is the start of shot k + 1)
    with an onset within HIT_TOL_FRAMES, the nearest."""
    tol, out = HIT_TOL_FRAMES / fps + 1e-6, []
    for k, t in enumerate(cuts, 1):
        near = min(ons, key=lambda o: abs(o - t), default=None)
        if near is not None and abs(near - t) <= tol:
            out.append({"cut": k, "t": round(t, 3), "onset_s": near,
                        "offset_frames": round((near - t) * fps, 2)})
    return out


def curve(frames):
    """Mean momentary loudness (LUFS) of each whole second from ebur128's (t, M, S) frames; None
    where a second has no reading."""
    bins = {}
    for t, m, _ in frames:
        if m > ar.NO_READING_LUFS:
            bins.setdefault(int(t), []).append(m)
    n = max(bins) + 1 if bins else 0
    return [round(sum(bins[i]) / len(bins[i]), 1) if i in bins else None for i in range(n)]


def merge(spans, gap=0.5):
    """Spans of one kind that touch within gap seconds, joined."""
    out = []
    for s in sorted(spans, key=lambda x: x["start_s"]):
        if out and out[-1]["kind"] == s["kind"] and s["start_s"] - out[-1]["end_s"] <= gap:
            out[-1]["end_s"] = max(out[-1]["end_s"], s["end_s"])
        else:
            out.append(dict(s))
    return out


def kinds_spans(video, kinds=None):
    """([{start_s, end_s, kind}], source). From a kinds sidecar (given, or <video stem>.kinds.json
    beside the video), else segkind's audio-only classifier on the video. ([], reason) when neither
    can run; never text."""
    path = kinds or os.path.splitext(video)[0] + ".kinds.json"
    if kinds or os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rows = [{"start_s": float(s["start"]), "end_s": float(s["end"]), "kind": s["kind"]}
                    for s in data["spans"]]
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise SoundError(f"{os.path.basename(path)} is not a kinds sidecar: {e}")
        return merge([r for r in rows if r["kind"] in KINDS and r["end_s"] > r["start_s"] >= 0]), os.path.basename(path)
    if not os.path.isfile(SEGKIND):
        return [], "vertcut's segkind.py is not beside this toolkit"
    try:
        p = subprocess.run([sys.executable, SEGKIND, "kinds", video], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=dc.READ_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as e:
        return [], f"vertcut kinds did not run: {type(e).__name__}"
    if p.returncode != 0:
        return [], "vertcut kinds failed: " + p.stderr.strip()[-120:]
    rows = [{"start_s": float(m[1]), "end_s": float(m[2]), "kind": m[3]}
            for m in map(_RANGE.match, p.stdout.splitlines()) if m]
    return merge(rows), "vertcut kinds (audio only, no transcript)"


def build(video, report, kinds=None):
    """The sound map. report: shots.build's. kinds: a kinds sidecar path, None to look beside the
    video then classify the audio, False to leave spans out (decon compare does not use them). Raises SoundError (no audio, too long, no frame rate)
    or ReportError, CutlistError, CheckError from ffmpeg."""
    name = os.path.basename(video)
    fps = report.get("fps")
    if not fps:
        raise SoundError(f"{name} has no frame rate, so hits at cuts cannot be given in frames")
    info = dc.probe(video)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams") or []):
        raise SoundError(f"{name} has no audio stream")
    if float((info.get("format") or {}).get("duration") or 0) > MAX_SECONDS:
        raise SoundError(f"{name} is longer than {MAX_SECONDS:g} s; cut the part to map first")
    summary, frames = ar.loudness(video)
    summary = {k: (v if v is not None and math.isfinite(v) else None) for k, v in summary.items()}  # -inf: silence
    raw = cutlist.decode_pcm(video, 0.0, None, None, timeout=trimreview.DECODE_TIMEOUT_S)
    y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    ons = onsets(y)
    cuts = [s["in"] for s in report["shots"][1:]]
    hits = hits_at_cuts(ons, cuts, fps)
    spans, source = ([], "not read") if kinds is False else kinds_spans(video, kinds)
    return {"file": name, "duration_s": round(len(y) / 16000, 3),
            "integrated_lufs": summary["integrated_lufs"], "lra_lu": summary["lra_lu"],
            "true_peak_dbtp": summary["true_peak_dbtp"], "curve_db": curve(frames),
            "spans": spans, "spans_from": source, "onsets": len(ons), "cuts": len(cuts),
            "hits": hits, "hit_share": round(len(hits) / len(cuts), 3) if cuts else None,
            "hit_tol_frames": HIT_TOL_FRAMES, "proposal": True}


def for_recipe(sm, slots):
    """The recipe's `sound` field: the map without its bookkeeping. A hit is named by the slot that
    starts at its cut; a cut into the end black (past the last slot) is left out."""
    return {"integrated_lufs": sm["integrated_lufs"], "lra_lu": sm["lra_lu"], "curve_db": sm["curve_db"],
            "spans": sm["spans"],
            "hits": [{"slot": h["cut"] + 1, "offset_frames": h["offset_frames"]}
                     for h in sm["hits"] if h["cut"] + 1 <= slots]}


def compare_part(a, b):
    """0 to 1: the mean of how near the integrated loudness is (zero at LUFS_SCALE apart) and how
    near the share of cuts with a hit is (1 minus the difference). None when either is unread."""
    if None in (a["integrated_lufs"], b["integrated_lufs"], a["hit_share"], b["hit_share"]):
        return None
    loud = max(0.0, 1 - abs(a["integrated_lufs"] - b["integrated_lufs"]) / LUFS_SCALE)
    return (loud + max(0.0, 1 - abs(a["hit_share"] - b["hit_share"]))) / 2


def summary(sm):
    kinds = {}
    for s in sm["spans"]:
        kinds[s["kind"]] = kinds.get(s["kind"], 0.0) + s["end_s"] - s["start_s"]
    return (f"sound: {sm['integrated_lufs']} LUFS, LRA {sm['lra_lu']} LU, {sm['onsets']} onset(s); "
            f"{len(sm['hits'])} of {sm['cuts']} cut(s) have a hit within {sm['hit_tol_frames']} frames; "
            f"spans from {sm['spans_from']}: "
            + (", ".join(f"{k} {v:.1f} s" for k, v in kinds.items()) or "none")
            + ". A proposal: check it by ear.")
