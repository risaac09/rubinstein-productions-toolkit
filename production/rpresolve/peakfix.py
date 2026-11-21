"""
peakfix: the loudness step for a destination with no loudness target (a client
master). Nothing is normalised; when the true peak is over the ceiling, the audio
gain is lowered just enough to land under it, into the copy delivercheck's
fixed_path() names (<stem>.loudfix<ext>), video copied, the original untouched.
It calls delivercheck's functions as they are.
"""

import math
import os

from rpresolve import deliver, delivercheck as dc

MARGIN_DB = 0.1  # under the ceiling, for the encoder's rounding


METER_ROUND_DB = 0.05  # the meter prints one decimal: a printed value can be this much low


def ceiling(dest):
    return dc.peak_ceiling(dest)


def _write(path, out, dest, has_data, gain):
    """One ffmpeg pass of the gain into out. Returns the process."""
    cmd = dc.fix_command(path, out, dest, has_data, None, 0, 0, 0)
    i = cmd.index("-filter:a:0") + 1
    cmd[i] = f"volume={gain:g}dB,aresample={int(dest['audio']['sample_rate'])}"
    return dc._run(cmd, dc.READ_TIMEOUT_S)


def _gain(limit, tp):
    # down, so never short; tp is as printed, so allow it to be METER_ROUND_DB higher
    return math.floor((limit - MARGIN_DB - tp - METER_ROUND_DB) * 100) / 100


def fix_peak(path, dest, fps=None, size=None, dry_run=False):
    """Returns {original, fixed, true_peak_dbtp, ceiling_dbtp, gain_db, warnings, status,
    ...}. Under the ceiling: fixed None, nothing written, status "pass". Over it: writes
    the copy, measures it (a lossy encode can land above the gain's estimate: one more
    pass from the original with the overshoot added; still over, the copy is removed and
    CheckError raised), checks it as if it had the original's name, and compares the
    video stream; status "pass" or "fail". dry_run measures and stops before writing.
    Raises ToolMissing or CheckError; never overwrites (an existing copy is refused) and
    only ever deletes the copy this call wrote."""
    dc.check_tools(ffmpeg=True)
    if deliver.has_loudness_target(dest):
        raise dc.CheckError(f"'{dest['key']}' has a loudness target; use the loudness fix.")
    if not os.path.isfile(path):
        raise dc.CheckError(f"{path} is not a file.")
    out = dc.fixed_path(path)
    if os.path.lexists(out):
        raise dc.CheckError(f"{out} already exists; nothing is overwritten. Move it away first.")
    streams = dc.probe(path).get("streams") or []
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not audios:
        raise dc.CheckError(f"{path} has no audio stream.")
    has_data = any(s.get("codec_type") in ("data", "attachment") for s in streams)
    limit = ceiling(dest)
    tp = dc.measure_loudness(path)["true_peak_dbtp"]
    r = {"original": os.path.abspath(path), "true_peak_dbtp": tp, "ceiling_dbtp": limit,
         "fixed": None, "gain_db": 0.0, "warnings": [], "status": "pass"}
    for n in range(1, len(audios)):  # fail closed: only the first stream is ever lowered
        other = dc.measure_loudness(path, stream=n)["true_peak_dbtp"]
        if other is None or other + METER_ROUND_DB > limit:
            raise dc.CheckError(f"audio stream {n + 1} has a true peak of {other} dBTP against the "
                                f"{limit:g} ceiling; only the first stream is lowered here. Lower "
                                "it in Resolve.")
    if len(audios) > 1:
        r["warnings"].append(f"{len(audios)} audio streams: only the first is lowered; the "
                             "others are under the ceiling and copied as they are.")
    if tp is None:
        raise dc.CheckError(f"no true peak could be read from {path}.")
    if tp + METER_ROUND_DB <= limit + 1e-9:  # equal at the meter's rounding counts as over
        r["nothing_to_fix"] = True
        return r
    r["gain_db"] = gain = _gain(limit, tp)
    r["fixed"] = os.path.abspath(out)
    if dry_run:
        r["dry_run"] = True
        return r
    if int(audios[0].get("sample_rate") or 0) != int(dest["audio"]["sample_rate"]):
        r["warnings"].append(f"the audio is resampled from {audios[0].get('sample_rate')} Hz to "
                             f"{int(dest['audio']['sample_rate'])} Hz, the destination's rate.")
    if has_data:
        r["warnings"].append("data streams (a timecode track, chapter text) are not copied; ffmpeg "
                             "writes the timecode again from the video stream's tag and carries "
                             "the chapters.")
    try:
        for attempt in (1, 2):
            proc = _write(path, out, dest, has_data, gain)
            if proc.returncode != 0:
                if os.path.exists(out) and os.path.getsize(out) == 0:
                    os.unlink(out)  # an empty file ffmpeg created and never wrote to
                left = f" A partial file may remain at {out}." if os.path.exists(out) else ""
                raise dc.CheckError(f"ffmpeg could not write {out}: {proc.stderr.strip()[-400:]}{left}")
            got = dc.measure_loudness(out)["true_peak_dbtp"]
            if got is not None and got + METER_ROUND_DB <= limit + 1e-9:
                break
            os.unlink(out)  # this run's own copy: ffmpeg -n wrote it just now
            if attempt == 2 or got is None:
                raise dc.CheckError(f"the lowered copy measured {got} dBTP against the {limit:g} "
                                    "ceiling; the copy was removed. Lower the audio in Resolve.")
            gain = r["gain_db"] = math.floor((gain - (got + METER_ROUND_DB - limit) - MARGIN_DB) * 100) / 100
            r["warnings"].append(f"the encoded copy landed at {got} dBTP; a second pass lowered "
                                 f"the gain to {gain:g} dB.")
    except dc.CheckError:
        raise
    try:
        r["check"] = dc.check(out, dest, fps=fps, size=size, as_name=os.path.basename(path))
        r["video_identical"] = dc.video_hash(path) == dc.video_hash(out)
    except Exception as e:  # a copy this run wrote must not be left unmentioned
        if os.path.exists(out):
            os.unlink(out)
        raise dc.CheckError(f"{e} (the copy {out} was removed)")
    if not r["video_identical"]:
        r["warnings"].append("the video stream differs from the original's; the fix is not used.")
    r["status"] = "pass" if r["check"]["status"] == "pass" and r["video_identical"] else "fail"
    if r["status"] == "fail":
        r["warnings"].append(f"{out} failed its check and is not a delivery; it was kept to look at.")
    return r
