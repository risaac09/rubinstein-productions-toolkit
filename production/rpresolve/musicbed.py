"""
rpresolve.musicbed: the music under a method edit, cut and faded by ffmpeg into one
24-bit 48 kHz WAV, so assemble_timeline places a file whose end is already quiet.

Why not TimelineItem.SetFades: the call returns True and GetFades reads the frames back,
and eight masters rendered from it still ended on a hard cut (resolve-traps.md T18). A
read-back proves the property round-trips, not that the render fades. A fade in the file
cannot go missing. The draft farm fades its music with ffmpeg the same way (drafts.py
music_args), so a draft and a build share the one second.

bake() makes the bed; spec() names it. The name holds the source, its size and date, the
in point, the length and the fade, so the same inputs find the same file and a changed
source or length makes a new one. Stdlib only; ffmpeg and ffprobe as delivercheck finds them.
"""

import hashlib
import os

from . import delivercheck as dc
from . import paths

FADE_S = 1.0       # the operator's length for the fade out (2026-11-10); the draft plays the same
RATE = 48000
CODEC = "pcm_s24le"  # 24-bit PCM: the bed is cut once and nothing re-compresses it
SHORT_S = 0.05     # a bed this much shorter than asked means the source ran out of music
TIMEOUT_S = 300


class BedError(RuntimeError):
    """The bed could not be made."""


def fade_for(seconds):
    """The fade out for a bed of this length: FADE_S, never more than a quarter of it."""
    return min(FADE_S, seconds / 4)


def spec(src, in_s, seconds, bed_dir):
    """What bake() will make: {src, in, seconds, fade_s, path}. Reads src's size and date for
    the name only; raises BedError when src cannot be read."""
    try:
        st = os.stat(src)
    except OSError as e:
        raise BedError(f"cannot read the music {src}: {e}")
    fade = fade_for(seconds)
    key = f"{src}|{st.st_size}|{st.st_mtime_ns}|{in_s:.4f}|{seconds:.4f}|{fade:.4f}"
    stem = os.path.splitext(os.path.basename(src))[0][:60]
    name = f"{stem}.bed-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:10]}.wav"
    return {"src": src, "in": float(in_s), "seconds": seconds, "fade_s": fade,
            "path": os.path.join(bed_dir, name)}


def bake(src, in_s, seconds, fade_s, out_path):
    """Cut `seconds` of src from `in_s`, fade the last `fade_s` seconds out, and write it as
    a 24-bit 48 kHz WAV at out_path (atomically; a failure leaves nothing there). Checks the
    result runs as long as asked. Returns the length it read. Raises BedError."""
    if not (0 < fade_s <= seconds / 4 + 1e-9):
        raise BedError(f"the fade ({fade_s:g} s) must be above 0 and at most a quarter of the "
                       f"bed ({seconds:g} s)")
    try:
        dc.check_tools()
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with paths.atomic_path(out_path) as tmp:
            proc = dc._run([dc.FFMPEG, "-v", "error", "-nostdin", "-y", "-ss", f"{in_s:.4f}",
                            "-t", f"{seconds:.4f}", "-i", dc._src(src), "-vn",
                            "-af", f"afade=t=out:st={seconds - fade_s:.4f}:d={fade_s:.4f}",
                            "-c:a", CODEC, "-ar", str(RATE), "-f", "wav", tmp], TIMEOUT_S)
            if proc.returncode != 0:
                raise BedError(f"ffmpeg could not cut the bed from {os.path.basename(src)}: "
                               f"{proc.stderr.strip()[-300:]}")
            got = dc.duration(dc.probe(tmp))
            if got is None or got < seconds - SHORT_S:
                raise BedError(f"{os.path.basename(src)} holds {got or 0:.2f} s of music from "
                               f"{in_s:g} s, the bed needs {seconds:.2f} s")
    except (dc.ToolMissing, dc.CheckError, OSError) as e:
        raise BedError(str(e))
    return got
