"""
rpresolve.reelab: a blind A/B packet of 2 to 4 renders of one reel, for a phone.
Standard library only (ffmpeg and ffprobe through rpresolve.delivercheck).

The video counterpart of mixbuild.ab_packet. The same span is cut from each
render and written as A.mp4, B.mp4 ... (720x1280 H.264 High, 8-bit 4:2:0, AAC,
faststart, under 25 MiB), with the integrated loudness matched within 0.5 LU by
turning the louder ones down. The letter order is shuffled by seed.

The reviewer sees DIR (the files, packet.json, README.txt, optionally
frame0.jpg). The key goes to DIR.key/key.json, beside the folder and never in
it, the layout rpresolve.reviewlog reads; packet.json is the stimulus the
review-log line hashes. Both outputs refuse any git tree and an existing path.

rpstills.absheet.order is repeated here (rpresolve does not import rpstills, and
the stdlib-only test job has no Pillow); a test holds the two equal. The loudness
measure is mixbuild's ebur128 summary, read through delivercheck so numpy stays
out of this module. --opening writes frame0.jpg through rpstills.absheet, which
needs the rp-tools venv.
"""

import hashlib
import json
import os
import random
import secrets
import shutil
import tempfile

from . import delivercheck as dc
from . import paths

LETTERS = "ABCD"
TOLERANCE_LU = 0.5  # mixbuild.AB_TOLERANCE_LU
MAX_SECONDS = 30.0
MAX_BYTES = 25 * 1024 * 1024
SIZE = (720, 1280)
TIMEOUT_S = 600


class ReelAbError(ValueError):
    """An input or output that must be refused."""


def order(n, seed):
    """rpstills.absheet.order: a permutation of range(n), never the identity for n >= 2."""
    perm = list(range(n))
    random.Random(seed).shuffle(perm)
    if n >= 2 and perm == sorted(perm):
        perm = perm[1:] + perm[:1]
    return perm


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def key_dir(out_dir):
    return os.path.abspath(out_dir).rstrip(os.sep) + ".key"


def _lufs(path, start=None, seconds=None):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}", "-t", f"{seconds:.3f}"]
    proc = dc._run(cmd + ["-i", dc._src(path), "-map", "0:a:0", "-af", "ebur128=peak=true:framelog=quiet",
                          "-f", "null", "-"], TIMEOUT_S)
    try:
        v = dc.parse_ebur128(proc.stderr)["integrated_lufs"]
    except dc.CheckError as e:
        raise ReelAbError(f"could not measure loudness in {path}: {e}")
    if proc.returncode != 0 or v is None or v < -69.9:
        raise ReelAbError(f"{path} is silent in that span; pick another --start")
    return v


def _encode(src, out, start, seconds, gain_db):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
           "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", dc._src(src), "-map", "0:v:0", "-map", "0:a:0",
           "-vf", f"scale={SIZE[0]}:{SIZE[1]}:flags=lanczos,setsar=1,format=yuv420p",
           "-af", f"volume={gain_db:.6f}dB", "-c:v", "libx264", "-profile:v", "high", "-crf", "23",
           "-maxrate", "4M", "-bufsize", "8M", "-preset", "medium", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           "-map_metadata", "-1", "-movflags", "+faststart", "-t", f"{seconds:.3f}", out]
    proc = dc._run(cmd, TIMEOUT_S)
    if proc.returncode != 0:
        raise ReelAbError(f"could not encode {src}: {proc.stderr.strip()[-300:]}")


def _inspect(path):
    """(width, height, duration s) of a render; ReelAbError when it lacks video or sound."""
    try:
        info = dc.probe(path)
    except dc.CheckError as e:
        raise ReelAbError(str(e))
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    if not {"video", "audio"} <= kinds:
        raise ReelAbError(f"{path} needs a video stream and an audio stream")
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    try:
        return int(v["width"]), int(v["height"]), float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        raise ReelAbError(f"{path} has no readable size or duration")


def _frame0(files, out_jpg, seed):
    """frame0.jpg: each file's first frame as an absheet sheet whose cell letters are the file letters."""
    try:
        from rpstills import absheet  # rp-tools venv only; rpresolve itself never imports it
    except ImportError as e:
        raise ReelAbError(f"--opening needs Pillow and rpstills (the rp-tools venv): {e}")
    perm = order(len(files), seed)
    with tempfile.TemporaryDirectory() as tmp:
        shots = []
        for k, f in enumerate(files):
            jpg = os.path.join(tmp, f"{k}.jpg")
            proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", dc._src(f),
                            "-frames:v", "1", "-q:v", "2", jpg], TIMEOUT_S)
            if proc.returncode != 0:
                raise ReelAbError(f"could not take frame 0 of {f}: {proc.stderr.strip()[-300:]}")
            shots.append(jpg)
        # absheet shows images[perm[k]] in cell k; hand it the frames so cell k is letter k
        sheet = os.path.join(tmp, "sheet")
        try:
            absheet.build([shots[perm.index(j)] for j in range(len(shots))], sheet, seed=seed)
        except ValueError as e:
            raise ReelAbError(f"frame0.jpg: {e}")
        shutil.copyfile(os.path.join(sheet, "sheet.jpg"), out_jpg)


def build(files, out_dir, start=0.0, seconds=10.0, seed=None, opening=False):
    """Write the packet in out_dir and the key in key_dir(out_dir); returns packet.json's content.
    ReelAbError for any refusal."""
    dc.check_tools(ffmpeg=True)
    out_dir = os.path.abspath(out_dir)
    kdir = key_dir(out_dir)
    if not 2 <= len(files) <= len(LETTERS):
        raise ReelAbError(f"{len(files)} renders; a packet takes 2 to {len(LETTERS)}")
    if not 0 < seconds <= MAX_SECONDS or not start >= 0:
        raise ReelAbError(f"--seconds must be above 0 and at most {MAX_SECONDS:g}, and --start 0 or more")
    for p in (out_dir, kdir):
        if os.path.lexists(p):
            raise ReelAbError(f"{p} exists: a packet and its key are never replaced. Use a new folder.")
        problem = paths.out_problem(os.path.join(p, "x"))
        if problem:
            raise ReelAbError(problem)
    if opening:  # fail before the encodes, not after
        try:
            from rpstills import absheet  # noqa: F401
        except ImportError as e:
            raise ReelAbError(f"--opening needs Pillow and rpstills (the rp-tools venv): {e}")
    files = [os.path.abspath(f) for f in files]
    if len({os.path.realpath(f) for f in files}) < len(files):
        raise ReelAbError("a render is named twice; an A/B of one file with itself says nothing")
    for f in files:
        if not os.path.isfile(f):
            raise ReelAbError(f"not found: {f}")
    dims = [_inspect(f) for f in files]
    for f, (w, h, _) in zip(files, dims):
        if abs(w * 16 - h * 9) > h * 0.09:  # 9:16 within 1%
            raise ReelAbError(f"{f} is {w}x{h}; the packet is 9:16")
    shortest = min(d for _, _, d in dims)
    if start + seconds > shortest + 0.001:
        raise ReelAbError(f"{start:g} s + {seconds:g} s runs past the shortest render ({shortest:.2f} s)")
    seed = secrets.randbits(31) if seed is None else int(seed)
    perm = order(len(files), seed)  # letter k holds files[perm[k]]
    labels = list(LETTERS[:len(files)])
    loud = [_lufs(f, start, seconds) for f in files]
    gains = [min(loud) - v for v in loud]  # only turning down
    created = []  # only what this run made is removed on failure
    try:
        os.makedirs(out_dir)
        created.append(out_dir)
        os.makedirs(kdir)
        created.append(kdir)
        outs = [os.path.join(out_dir, f"{lab}.mp4") for lab in labels]
        measured = [0.0] * len(labels)
        todo = set(range(len(labels)))
        for attempt in range(3):  # the encoder moves loudness a little; trim up to twice
            for k in sorted(todo):
                _encode(files[perm[k]], outs[k], start, seconds, gains[perm[k]])
                measured[k] = _lufs(outs[k])
            if max(measured) - min(measured) <= 0.1 or attempt == 2:
                break
            low = min(measured)
            todo = {k for k in range(len(labels)) if measured[k] - low > 0.05}
            for k in todo:
                gains[perm[k]] += low - measured[k]
        spread = round(max(measured) - min(measured), 3)
        if spread > TOLERANCE_LU:
            raise ReelAbError(f"loudness could not be matched within {TOLERANCE_LU} LU (spread {spread} LU)")
        for o in outs:
            if os.path.getsize(o) >= MAX_BYTES:
                raise ReelAbError(f"{o} is over 25 MiB; use a shorter --seconds")
        if opening:
            _frame0(outs, os.path.join(out_dir, "frame0.jpg"), seed)
        packet = {"labels": labels, "start_s": start, "seconds": seconds, "size": list(SIZE),
                  "matched_lufs": {lab: round(measured[k], 2) for k, lab in enumerate(labels)},
                  "spread_lu": spread,
                  "files": {lab: {"file": f"{lab}.mp4", "bytes": os.path.getsize(outs[k]), "sha256": sha256(outs[k])}
                            for k, lab in enumerate(labels)}}
        key = {"tool": "reel-ab", "seed": seed, "start_s": start, "seconds": seconds,
               "sheet": {"file": "packet.json"},  # reviewlog hashes this as the stimulus
               "labels": {lab: {"source": files[perm[k]], "input_index": perm[k], "gain_db": round(gains[perm[k]], 3)}
                          for k, lab in enumerate(labels)}}
        key_path = os.path.join(kdir, "key.json")
        with open(key_path, "w", encoding="utf-8") as f:
            json.dump(key, f, indent=1, sort_keys=True)
        os.chmod(key_path, 0o400)
        packet["key_sha256"] = sha256(key_path)
        with open(os.path.join(out_dir, "packet.json"), "w", encoding="utf-8") as f:
            json.dump(packet, f, indent=1)
        with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
            f.write("Blind A/B of one reel. Watch the files in any order, say which you prefer and why. "
                    f"Levels are matched within {TOLERANCE_LU} LU, so loudness is not a clue. "
                    "The key is kept apart from this folder.\n")
    except BaseException:
        for d in created:
            shutil.rmtree(d, ignore_errors=True)
        raise
    return packet
