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


def ceiling(dest):
    return float((dest.get("loudness") or {}).get("true_peak_max_dbtp", dc.TRUE_PEAK_DEFAULT_DBTP))


def fix_peak(path, dest, fps=None, size=None, dry_run=False):
    """Returns {original, fixed, true_peak_dbtp, ceiling_dbtp, gain_db, warnings, status,
    ...}. Under the ceiling: fixed None, nothing written, status "pass". Over it: writes
    the copy, checks it as if it had the original's name, and compares the video stream;
    status "pass" or "fail". dry_run measures and stops before writing. Raises
    ToolMissing or CheckError; never overwrites (an existing copy is refused)."""
    dc.check_tools(ffmpeg=True)
    if deliver.has_loudness_target(dest):
        raise dc.CheckError(f"'{dest['key']}' has a loudness target; use the loudness fix.")
    if not os.path.isfile(path):
        raise dc.CheckError(f"{path} is not a file.")
    out = dc.fixed_path(path)
    if os.path.lexists(out):
        raise dc.CheckError(f"{out} already exists; nothing is overwritten. Move it away first.")
    streams = dc.probe(path).get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise dc.CheckError(f"{path} has no audio stream.")
    has_data = any(s.get("codec_type") in ("data", "attachment") for s in streams)
    tp, limit = dc.measure_loudness(path)["true_peak_dbtp"], ceiling(dest)
    r = {"original": os.path.abspath(path), "true_peak_dbtp": tp, "ceiling_dbtp": limit,
         "fixed": None, "gain_db": 0.0, "warnings": [], "status": "pass"}
    n_audio = sum(s.get("codec_type") == "audio" for s in streams)
    if n_audio > 1:
        r["warnings"].append(f"{n_audio} audio streams: only the first is measured and lowered; "
                             "the others are copied as they are.")
    if tp is None:
        raise dc.CheckError(f"no true peak could be read from {path}.")
    if tp <= limit + 1e-9:
        r["nothing_to_fix"] = True
        return r
    r["gain_db"] = gain = math.floor((limit - MARGIN_DB - tp) * 100) / 100  # down, so never short
    r["fixed"] = os.path.abspath(out)
    if dry_run:
        r["dry_run"] = True
        return r
    cmd = dc.fix_command(path, out, dest, has_data, None, 0, 0, 0)
    i = cmd.index("-filter:a:0") + 1
    cmd[i] = f"volume={gain:g}dB,aresample={int(dest['audio']['sample_rate'])}"
    proc = dc._run(cmd, dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        if os.path.exists(out) and os.path.getsize(out) == 0:
            os.unlink(out)  # an empty file ffmpeg created and never wrote to
        raise dc.CheckError(f"ffmpeg could not write {out}: {proc.stderr.strip()[-400:]}")
    r["check"] = dc.check(out, dest, fps=fps, size=size, as_name=os.path.basename(path))
    r["video_identical"] = dc.video_hash(path) == dc.video_hash(out)
    if not r["video_identical"]:
        r["warnings"].append("the video stream differs from the original's; the fix is not used.")
    r["status"] = "pass" if r["check"]["status"] == "pass" and r["video_identical"] else "fail"
    return r
