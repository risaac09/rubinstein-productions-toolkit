"""
rpresolve.stingsync: does an intro's sound sting line up with its picture?
Offline: a lockup file, a cues file and the audio; never Resolve. Stdlib wave
plus numpy.

The contract (VP-146): offset_ms = picture_ms - audio_ms, so a positive
offset means sound leads (ITU-R BT.1359's sign). A lead over LEAD_FAIL_MS or
a lag over LAG_FAIL_MS fails; one frame at 24000/1001 is 41.7 ms, so a
one-frame lead fails by default. A cue may carry intended_offset_ms (a
deliberate lead): the check then judges the deviation from that intent.

Cues are measured, never typed. A hit cue without measured_from is refused.
Every hit onset is re-measured from the audio (the transient stem when
given, else the master) as the first sample, within SEARCH_MS of the picture
event, where the first difference reaches ONSET_FRACTION of its peak in that
window. It fails when the cue's declared onset_ms and the re-measured one
differ by more than TOL_MS.

A row is {cue_id, kind, status, frame, keyframe, keyframe_frame, picture_ms,
audio_ms, declared_ms, offset_ms, offset_frames, deviation_ms, reasons}.
"""

import json
import os
import wave

import numpy as np

LEAD_FAIL_MS, LAG_FAIL_MS = 40.0, 60.0   # working defaults until BT.1359 is read at source
TOL_MS = 1.0                             # declared vs re-measured onset
SEARCH_MS = 150.0
ONSET_FRACTION = 0.25
MATCH_FRAMES = 3                         # a cue lands this near its keyframe's frame

PASS, FAIL = "PASS", "FAIL"


class StingError(Exception):
    """An input is missing, unreadable, or breaks the contract."""


def _json(path, what):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise StingError(f"{what} {path}: {e}")
    if not isinstance(data, dict):
        raise StingError(f"{what} {path}: not a JSON object")
    return data


def read_wav(path):
    """(mono float samples, rate) from a PCM WAV of 16, 24 or 32 bits."""
    try:
        with wave.open(path) as w:
            n, ch, rate, width = w.getnframes(), w.getnchannels(), w.getframerate(), w.getsampwidth()
            raw = w.readframes(n)
    except (OSError, EOFError, wave.Error) as e:
        raise StingError(f"audio {path}: {e}")
    if width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        q = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(q >= 1 << 23, q - (1 << 24), q) / 8388608.0
    elif width in (2, 4):
        x = np.frombuffer(raw, np.int16 if width == 2 else np.int32) / float(1 << (8 * width - 1))
    else:
        raise StingError(f"audio {path}: {8 * width}-bit samples are not supported")
    return x.reshape(-1, ch).mean(axis=1), rate


def onset_ms(x, rate, around_ms):
    """Onset in ms within SEARCH_MS of around_ms, or None when the window is silent."""
    d = np.abs(np.diff(x, prepend=0.0))
    a = max(0, int((around_ms - SEARCH_MS) * rate / 1000))
    w = d[a:max(a, int((around_ms + SEARCH_MS) * rate / 1000))]
    if not len(w) or w.max() <= 0:
        return None
    return (a + int(np.argmax(w >= ONSET_FRACTION * w.max()))) * 1000.0 / rate


def parse_fps(text):
    try:
        n, _, d = str(text).partition("/")
        v = float(n) / float(d or 1)
    except (ValueError, ZeroDivisionError):
        v = 0.0
    if v <= 0:
        raise StingError(f"fps {text!r} is not a rate such as 24000/1001")
    return v


def pick_cut(lockup, cut):
    """The {keyframes, fps} block for one cut, from either lockup shape."""
    block = lockup
    if isinstance(lockup.get("cuts"), dict):
        block = lockup["cuts"].get(cut)
        if not isinstance(block, dict):
            raise StingError(f"lockup has no cut {cut!r} (has: {', '.join(lockup['cuts'])})")
    kfs = block.get("keyframes")
    if not isinstance(kfs, list) or not kfs:
        raise StingError("lockup has no keyframes")
    if not all(isinstance(k, dict) and _num(k.get("frame")) and _num(k.get("ms")) for k in kfs):
        raise StingError("every lockup keyframe needs a numeric frame and ms")
    return kfs


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(lockup, cues, master=None, stem=None, cut=None, lead_ms=LEAD_FAIL_MS, lag_ms=LAG_FAIL_MS,
          tol_ms=TOL_MS):
    """Rows for every hit cue, FAIL first. Raises StingError on bad input."""
    if not (master or stem):
        raise StingError("give --wav or --transient: every hit is re-measured from the audio")
    frame_ms = 1000.0 / parse_fps(lockup.get("fps", cues.get("fps", "24000/1001")))
    kfs = pick_cut(lockup, cut or cues.get("cut") or "long")
    hits = [c for c in cues.get("cues", []) if c.get("kind") == "hit"]
    if not hits:
        raise StingError("cues has no hit cues")
    x, rate = read_wav(stem or master)
    start_ms = float(cues.get("wav_start_frame", 0)) * frame_ms
    rows = []
    for c in hits:
        cid = c.get("cue_id", "?")
        if not c.get("measured_from"):
            raise StingError(f"cue {cid}: no measured_from; cues are measured, never typed")
        if not _num(c.get("frame")) or not all(_num(c.get(k)) or c.get(k) is None
                                                for k in ("onset_ms", "intended_offset_ms")):
            raise StingError(f"cue {cid}: frame, onset_ms and intended_offset_ms must be numbers")
        near = [k for k in kfs if abs(k["frame"] - c["frame"]) <= MATCH_FRAMES]
        kf = min(near, key=lambda k: abs(k["frame"] - c["frame"])) if near else None
        row = {"cue_id": cid, "kind": "hit", "frame": c.get("frame"), "keyframe": None,
               "keyframe_frame": None, "picture_ms": None, "audio_ms": None,
               "declared_ms": c.get("onset_ms"), "offset_ms": None, "offset_frames": None,
               "deviation_ms": None, "reasons": []}
        if not kf:
            row["reasons"].append(f"no lockup keyframe within {MATCH_FRAMES} frames of frame {c.get('frame')}")
        else:
            pic = float(kf["ms"])
            on = onset_ms(x, rate, pic - start_ms)
            if on is None:
                row["reasons"].append(f"no audio within {SEARCH_MS:g} ms of the picture event")
            else:
                aud = on + start_ms
                off = pic - aud
                dev = off - float(c.get("intended_offset_ms", 0.0))
                row.update(keyframe=kf.get("id"), keyframe_frame=kf["frame"], picture_ms=pic,
                           audio_ms=round(aud, 3), offset_ms=round(off, 2),
                           offset_frames=round(off / frame_ms, 2), deviation_ms=round(dev, 2))
                if dev > lead_ms:
                    row["reasons"].append(f"sound leads {dev:.1f} ms (limit {lead_ms:g})")
                elif dev < -lag_ms:
                    row["reasons"].append(f"sound lags {-dev:.1f} ms (limit {lag_ms:g})")
                if c.get("onset_ms") is None or abs(c["onset_ms"] - aud) > tol_ms:
                    row["reasons"].append(f"declared onset {c.get('onset_ms')} ms, measured "
                                          f"{aud:.3f} ms (tolerance {tol_ms:g})")
        row["status"] = FAIL if row["reasons"] else PASS
        rows.append(row)
    rows.sort(key=lambda r: r["status"] != FAIL)
    return rows


def format_table(rows):
    """FAIL-first text table in frames and ms, then the verdict line."""
    f = lambda v, p="{:+.1f}": "-" if v is None else p.format(v)
    lines = [f"{'':4}  {'cue':<12} {'keyframe':<14} {'pic ms':>9} {'audio ms':>9} "
             f"{'offset ms':>9} {'frames':>7}  note"]
    for r in rows:
        lines.append(f"{r['status']:<4}  {r['cue_id']:<12} {str(r['keyframe']):<14} "
                     f"{f(r['picture_ms'], '{:.1f}'):>9} {f(r['audio_ms'], '{:.1f}'):>9} "
                     f"{f(r['offset_ms']):>9} {f(r['offset_frames'], '{:+.2f}'):>7}  "
                     + "; ".join(r["reasons"]))
    n = sum(r["status"] == FAIL for r in rows)
    lines.append(f"sting-sync: {len(rows) - n} pass, {n} fail: {FAIL if n else PASS}"
                 "  (offset = picture - audio; positive = sound leads)")
    return "\n".join(lines) + "\n"


def run(lockup_path, cues_path, **kw):
    return check(_json(lockup_path, "lockup"), _json(cues_path, "cues"), **kw)
