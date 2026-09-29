"""
rpresolve.delivercheck: check a rendered deliverable against its
destination, and fix its loudness. Offline: ffprobe and ffmpeg only, never
Resolve. Stdlib only.

check() reports every rule as PASS, FAIL or SKIP with what it found and
what it expected:
    name          the file name follows the naming rule for the destination
    container     .mp4 or .mov, and the brand ffprobe reads agrees
    video_codec   codec (and profile, where the destination fixes one)
    size          width x height (a "timeline" size needs --size to assert)
    fps           an exact rational; a standard rate, and --fps when given
    fps_constant  average and nominal frame rate agree (no VFR)
    pix_fmt       chroma family and bit depth, where the destination fixes them
    color_*       primaries, transfer and matrix tags against the config;
                  an expected value of null is reported, not asserted
    audio_*       codec, channels, sample rate, bit depth for LPCM
    captions      sidecar: <stem>.srt beside the file parses, has cues, and
                  runs in time order; burn-in or none: no sidecar and no
                  subtitle stream (burnt-in text is not read from pixels)
    loudness      ffmpeg ebur128 with peak=true: integrated LUFS within the
                  tolerance, true peak at or under the maximum; SKIP when the
                  destination has no target

fix_loudness() is a two-pass loudnorm (measure, then linear with the
measured values) that copies the video stream untouched and writes
<stem>.loudfix<ext> beside the original; it never overwrites anything.
"""

import json
import os
import re
import shutil
import subprocess
import time
from fractions import Fraction

from . import deliver

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
FFPROBE = os.environ.get("RPRESOLVE_FFPROBE", "/opt/homebrew/bin/ffprobe")
PROBE_TIMEOUT_S = 120
# Loudness reads the whole file; a long render on a share takes a while.
READ_TIMEOUT_S = 3600

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
# Timeline rates as written in Resolve, as exact rationals.
NTSC = {"23.976": "24000/1001", "23.98": "24000/1001", "29.97": "30000/1001",
        "47.952": "48000/1001", "59.94": "60000/1001", "119.88": "120000/1001"}
PCM = {16: ("pcm_s16le", "pcm_s16be"), 24: ("pcm_s24le", "pcm_s24be"),
       32: ("pcm_s32le", "pcm_s32be")}
CAPTION_TAGS = {"c608", "c708", "tx3g", "wvtt", "stpp"}


class ToolMissing(RuntimeError):
    """ffprobe or ffmpeg is not where this module expects it."""


class CheckError(RuntimeError):
    """The file could not be read, or a fix could not be made."""


def check_tools(ffmpeg=True):
    """Raise ToolMissing unless ffprobe (and ffmpeg when needed) run."""
    need = [("ffprobe", FFPROBE)] + ([("ffmpeg", FFMPEG)] if ffmpeg else [])
    missing = [f"{name} not found at {path} (brew install ffmpeg, or set "
               f"RPRESOLVE_{name.upper()})" for name, path in need
               if not (os.path.isfile(path) and os.access(path, os.X_OK))]
    if missing:
        raise ToolMissing("; ".join(missing))


def _run(cmd, timeout):
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CheckError(f"{os.path.basename(cmd[0])} timed out after {timeout}s")


def _src(path):
    return "file:" + os.path.abspath(path)


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

def probe(path):
    """ffprobe's streams and format for path. Raises CheckError."""
    proc = _run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json",
                 _src(path)], PROBE_TIMEOUT_S)
    if proc.returncode != 0:
        raise CheckError(f"ffprobe could not read {path}: {proc.stderr.strip()[-300:]}")
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError as e:
        raise CheckError(f"ffprobe returned unreadable JSON for {path}: {e}")


def parse_ebur128(text):
    """{integrated_lufs, true_peak_dbtp, lra_lu} from ebur128's summary.
    Raises CheckError when there is no summary."""
    i = text.rfind("Summary:")
    if i < 0:
        raise CheckError("ffmpeg printed no ebur128 summary: " + text.strip()[-300:])
    body = text[i:]

    def grab(section, label):
        m = re.search(section + r":.*?" + label + r":\s*(-?(?:inf|\d+(?:\.\d+)?))", body, re.S)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab("Integrated loudness", "I"),
            "true_peak_dbtp": grab("True peak", "Peak"),
            "lra_lu": grab("Loudness range", "LRA")}


def measure_loudness(path, stream=0):
    """Integrated loudness, true peak and range of an audio stream."""
    proc = _run([FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", _src(path),
                 "-map", f"0:a:{stream}", "-af", "ebur128=peak=true:framelog=quiet",
                 "-f", "null", "-"], READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise CheckError(f"ffmpeg could not measure loudness in {path}: "
                         f"{proc.stderr.strip()[-300:]}")
    return parse_ebur128(proc.stderr)


def video_hash(path):
    """sha256 of the first video stream's packets (ffmpeg streamhash):
    equal hashes mean the video stream is bit-identical."""
    proc = _run([FFMPEG, "-v", "error", "-nostdin", "-i", _src(path), "-map", "0:v:0",
                 "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"], READ_TIMEOUT_S)
    if proc.returncode != 0 or "SHA256=" not in proc.stdout:
        raise CheckError(f"ffmpeg could not hash the video of {path}: "
                         f"{proc.stderr.strip()[-300:]}")
    return proc.stdout.strip().split("SHA256=")[-1]


# ---------------------------------------------------------------------------
# Small parsers
# ---------------------------------------------------------------------------

def rate(text):
    """'24000/1001' -> Fraction, or None for 0/0 and the unreadable."""
    try:
        f = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def parse_fps(value):
    """A frame rate as given by a person ('23.976', '29.97', '25',
    '24000/1001') -> exact Fraction. Raises ValueError."""
    s = str(value).strip()
    s = NTSC.get(s, s)
    f = rate(s)
    if f is None:
        raise ValueError(f"cannot read {value!r} as a frame rate")
    return f


def fmt_rate(f):
    return "?" if f is None else (f"{f.numerator}/{f.denominator}" if f.denominator != 1
                                  else str(f.numerator)) + f" ({float(f):.3f} fps)"


def pix_fmt_family(pix_fmt):
    """('420' | '422' | '444', bit depth) for a pix_fmt, (None, None) when
    unknown."""
    s = str(pix_fmt or "")
    m = re.fullmatch(r"yuva?j?(4[24][024])p(\d+)?(le|be)?", s)
    if m:
        return m.group(1), int(m.group(2) or 8)
    semi = {"nv12": ("420", 8), "nv21": ("420", 8), "p010le": ("420", 10),
            "p010be": ("420", 10), "nv16": ("422", 8), "p210le": ("422", 10),
            "nv24": ("444", 8), "p410le": ("444", 10)}
    return semi.get(s, (None, None))


SRT_TIME = r"(\d{1,2}):(\d{2}):(\d{2}),(\d{3})"
SRT_LINE = re.compile(rf"^{SRT_TIME}\s*-->\s*{SRT_TIME}(?:\s.*)?$")


def parse_srt(text):
    """(cues, problems) for SubRip text. A cue is (start_s, end_s, text).
    Problems name what is malformed: a block without a timing line, a cue
    that ends before it starts, cues out of time order, no cues."""
    cues, problems = [], []
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    for n, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        m = SRT_LINE.match(lines[0].strip()) if lines else None
        if not m:
            problems.append(f"block {n}: no 'HH:MM:SS,mmm --> HH:MM:SS,mmm' timing line")
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        if end <= start:
            problems.append(f"block {n}: ends at {end:.3f}s, not after its start {start:.3f}s")
        if cues and start < cues[-1][0]:
            problems.append(f"block {n}: starts at {start:.3f}s, before the cue above it")
        if len(lines) < 2:
            problems.append(f"block {n}: no caption text")
        cues.append((start, end, "\n".join(lines[1:])))
    if not cues and not problems:
        problems.append("no cues")
    return cues, problems


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _row(name, ok, found, expected, note=None):
    status = SKIP if ok is None else (PASS if ok else FAIL)
    return {"check": name, "status": status, "found": found, "expected": expected,
            **({"note": note} if note else {})}


def _one_of(value, want):
    """want may be a value or a list of acceptable values."""
    return value in want if isinstance(want, (list, tuple)) else value == want


def check(path, dest, fps=None, size=None, as_name=None, loudness=True):
    """Check path against the merged destination dest. fps: the timeline's
    frame rate to assert (any form parse_fps reads). size: (width, height)
    to assert when the destination renders at the timeline's size.
    as_name: judge the name and the sidecar as if the file were called
    this (a fixed file that is still beside its original). loudness=False
    skips the ffmpeg read. Returns {file, destination, checks, counts,
    status}; raises ToolMissing or CheckError."""
    need_ffmpeg = loudness and deliver.has_loudness_target(dest)
    check_tools(ffmpeg=need_ffmpeg)
    if not os.path.isfile(path):
        raise CheckError(f"{path} is not a file.")
    info = probe(path)
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    audio = audios[0] if audios else None
    name = os.path.basename(as_name or path)
    rows = []

    # Name
    problems = deliver.name_problems(name, dest)
    rows.append(_row("name", not problems, name, f"the {dest['naming']} naming rule, "
                     f"{dest.get('aspect') or 'master'}, .{dest['format']}",
                     "; ".join(problems) or None))

    # Container: mov and mp4 share ffprobe's format name; the brand differs.
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    brand = ((fmt.get("tags") or {}).get("major_brand") or "").strip()
    family = "mov" in str(fmt.get("format_name", "")).split(",")
    is_qt = brand == "qt"
    ok = family and ext == dest["format"] and (is_qt if dest["format"] == "mov" else not is_qt)
    rows.append(_row("container", ok, f".{ext}, brand '{brand or 'none'}'",
                     f".{dest['format']}" + (", QuickTime brand 'qt'" if dest["format"] == "mov"
                                             else ", an ISO brand (not 'qt')")))

    # Video
    exp = dest.get("expect") or {}
    if video is None:
        rows.append(_row("video_codec", False, "no video stream", exp.get("codec_name")))
    else:
        vc, prof = video.get("codec_name"), video.get("profile")
        ok = _one_of(vc, exp.get("codec_name")) if exp.get("codec_name") else None
        if exp.get("profile") and ok is not None:
            ok = ok and _one_of(prof, exp["profile"])
        rows.append(_row("video_codec", ok, f"{vc} ({prof or 'no profile'})",
                         f"{exp.get('codec_name')}" +
                         (f" ({exp['profile']})" if exp.get("profile") else "")))

        w, h = video.get("width"), video.get("height")
        if dest["resolution"] == "timeline":
            want = tuple(size) if size else None
            rows.append(_row("size", (w, h) == want if want else None, f"{w}x{h}",
                             f"{want[0]}x{want[1]}" if want else "the timeline's size",
                             None if want else "native timeline size; pass --size to assert"))
        else:
            want = (dest["resolution"]["width"], dest["resolution"]["height"])
            rows.append(_row("size", (w, h) == want, f"{w}x{h}", f"{want[0]}x{want[1]}"))

        r_rate, avg = rate(video.get("r_frame_rate")), rate(video.get("avg_frame_rate"))
        standard = [parse_fps(x) for x in dest.get("frame_rates") or []]
        if fps is not None:
            want = parse_fps(fps)
            rows.append(_row("fps", r_rate == want, fmt_rate(r_rate), fmt_rate(want)))
        else:
            rows.append(_row("fps", r_rate in standard if standard else None, fmt_rate(r_rate),
                             "a standard rate (pass --fps to assert the timeline's)"))
        steady = None if not (avg and r_rate) else abs(avg - r_rate) / r_rate < Fraction(1, 1000)
        rows.append(_row("fps_constant", steady, fmt_rate(avg), f"within 0.1% of {fmt_rate(r_rate)}"))

        chroma, bits = pix_fmt_family(video.get("pix_fmt"))
        want_c, want_b = exp.get("pix_fmt_family"), exp.get("bit_depth")
        if want_c or want_b:
            ok = (not want_c or chroma == str(want_c)) and (not want_b or bits == int(want_b))
            rows.append(_row("pix_fmt", ok, f"{video.get('pix_fmt')} ({chroma or '?'}, "
                             f"{bits or '?'}-bit)",
                             f"{want_c or 'any'} chroma" + (f", {want_b}-bit" if want_b else "")))
        else:
            rows.append(_row("pix_fmt", None, video.get("pix_fmt"), "not fixed"))

        cexp = (dest.get("color") or {}).get("expect") or {}
        for key in ("color_primaries", "color_transfer", "color_space"):
            found = video.get(key) or "unset"
            want = cexp.get(key)
            rows.append(_row(key, None if want is None else _one_of(found, want), found,
                             want if want is not None else "not asserted",
                             None if want is not None else "reported only; see the open "
                             "question on the Gamma 2.4 transfer tag"))

    # Audio
    a = dest["audio"]
    if audio is None:
        rows.append(_row("audio_codec", False, "no audio stream", a["codec"]))
    else:
        ac = audio.get("codec_name")
        want = (list(PCM.get(int(a.get("bit_depth") or 24), ())) if a["codec"] == "lpcm"
                else ["aac"])
        rows.append(_row("audio_codec", ac in want, ac, " or ".join(want)))
        ch = audio.get("channels")
        rows.append(_row("audio_channels", None if a.get("channels") is None
                         else ch == a["channels"], ch,
                         a.get("channels") if a.get("channels") is not None else "not asserted"))
        sr = int(audio.get("sample_rate") or 0)
        rows.append(_row("audio_sample_rate", sr == int(a["sample_rate"]), sr, a["sample_rate"]))
        if a["codec"] == "lpcm":
            bd = int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0)
            rows.append(_row("audio_bit_depth", bd == int(a["bit_depth"]), bd, a["bit_depth"]))
        if len(audios) > 1:
            rows.append(_row("audio_streams", None, len(audios), 1,
                             "only the first audio stream is checked"))

    # Captions
    rows.append(_captions(path, name, dest, streams))

    # Loudness
    loud = dest.get("loudness") or {}
    if not deliver.has_loudness_target(dest):
        rows.append(_row("loudness", None, "not measured", "no target (unnormalized)"))
    elif not loudness:
        rows.append(_row("loudness", None, "not measured", f"{loud['integrated_lufs']} LUFS",
                         "skipped on request"))
    elif audio is None:
        rows.append(_row("loudness", False, "no audio stream", f"{loud['integrated_lufs']} LUFS"))
    else:
        m = measure_loudness(path)
        target, tol = float(loud["integrated_lufs"]), float(loud["tolerance_lu"])
        i, tp = m["integrated_lufs"], m["true_peak_dbtp"]
        rows.append(_row("loudness", i is not None and abs(i - target) <= tol + 1e-9,
                         f"{i} LUFS", f"{target:g} LUFS +/- {tol:g} LU"))
        peak_max = float(loud["true_peak_max_dbtp"])
        rows.append(_row("true_peak", tp is not None and tp <= peak_max + 1e-9,
                         f"{tp} dBTP", f"<= {peak_max:g} dBTP"))

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (PASS, FAIL, SKIP)}
    return {"file": os.path.abspath(path), "destination": dest["key"], "checks": rows,
            "counts": counts, "status": "fail" if counts[FAIL] else "pass"}


def _captions(path, name, dest, streams):
    folder = os.path.dirname(os.path.abspath(path))
    sidecar = deliver.sidecar_path(os.path.join(folder, name))
    subs = [s for s in streams if s.get("codec_type") == "subtitle"
            or str(s.get("codec_tag_string", "")).lower() in CAPTION_TAGS]
    sub_desc = ", ".join(f"#{s.get('index')} {s.get('codec_name') or s.get('codec_tag_string')}"
                         for s in subs)
    if dest["captions"] == "sidecar":
        if not os.path.isfile(sidecar):
            near = sorted(f for f in os.listdir(folder)
                          if f.lower().endswith(".srt") and f.startswith(os.path.splitext(name)[0]))
            return _row("captions", False, "no sidecar" + (f" (found {', '.join(near)})"
                                                           if near else ""),
                        os.path.basename(sidecar))
        try:
            with open(sidecar, encoding="utf-8") as f:
                cues, problems = parse_srt(f.read())
        except (OSError, UnicodeDecodeError) as e:
            cues, problems = [], [f"unreadable: {e}"]
        return _row("captions", not problems,
                    f"{os.path.basename(sidecar)}: {len(cues)} cue(s)" +
                    (f"; {'; '.join(problems[:3])}" if problems else ""),
                    "a parseable, non-empty, time-ordered .srt beside the file")
    found = []
    if os.path.isfile(sidecar):
        found.append(f"sidecar {os.path.basename(sidecar)}")
    if subs:
        found.append(f"subtitle stream(s) {sub_desc}")
    return _row("captions", not found, "; ".join(found) or "no sidecar, no subtitle stream",
                "no sidecar and no subtitle stream (" + dest["captions"] + ")",
                "burnt-in captions are not read from the picture" if dest["captions"] ==
                "burnin" else None)


def format_report(result):
    """The check as aligned text lines."""
    lines = [f"{result['file']}  ->  {result['destination']}"]
    for r in result["checks"]:
        lines.append(f"  {r['status']:<4}  {r['check']:<17} found {r['found']}  "
                     f"(expected {r['expected']})" + (f"  [{r['note']}]" if r.get("note") else ""))
    c = result["counts"]
    lines.append(f"deliver-check: {c[PASS]} pass, {c[FAIL]} fail, {c[SKIP]} skipped: "
                 f"{result['status'].upper()}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Loudness fix
# ---------------------------------------------------------------------------

def fixed_path(path):
    stem, ext = os.path.splitext(path)
    return f"{stem}.loudfix{ext}"


def _loudnorm_json(text):
    i = text.rfind("{")
    j = text.rfind("}")
    if i < 0 or j < i:
        raise CheckError("loudnorm printed no measurement: " + text.strip()[-300:])
    try:
        return json.loads(text[i:j + 1])
    except ValueError as e:
        raise CheckError(f"loudnorm's measurement is unreadable: {e}")


def loudnorm_filter(target, tp, lra, measured=None):
    """The loudnorm filter string: measuring when measured is None, else
    the linear second pass with the first pass's values."""
    f = f"loudnorm=I={target:g}:TP={tp:g}:LRA={lra:g}"
    if measured:
        f += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
              f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
              f":offset={measured['target_offset']}:linear=true")
    return f + ":print_format=json"


def fix_command(path, out, dest, has_data, measured, target, tp, lra):
    """The second-pass ffmpeg argv: every stream mapped and copied except
    the first audio stream, which is normalised and re-encoded to the
    destination's audio settings. An mp4 cannot carry a copied timecode
    (tmcd) data track, so there data streams are left out and the muxer
    writes the timecode again from the video stream's timecode tag."""
    a = dest["audio"]
    if a["codec"] == "lpcm":
        enc = ["-c:a:0", PCM[int(a["bit_depth"])][0]]
    else:
        enc = ["-c:a:0", "aac"] + (["-b:a:0", f"{int(a['bitrate_kbps'])}k"]
                                   if a.get("bitrate_kbps") else [])
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-n", "-i", _src(path), "-map", "0"]
    if dest["format"] == "mp4" and has_data:
        cmd += ["-map", "-0:d"]
    cmd += ["-c", "copy"] + enc
    cmd += ["-filter:a:0", loudnorm_filter(target, tp, lra, measured) +
            f",aresample={int(a['sample_rate'])}"]
    cmd += ["-map_metadata", "0"]
    if dest["format"] == "mp4" and (dest.get("resolve") or {}).get("NetworkOptimization"):
        cmd += ["-movflags", "+faststart"]
    return cmd + ["file:" + os.path.abspath(out)]


def _trash(path, trash_root, stamp):
    """Move path into <trash_root>/deliver-loudfix-<stamp>/ and return the
    new path. Across devices it copies, compares sizes, then removes the
    original."""
    base = os.path.join(trash_root, f"deliver-loudfix-{stamp}")
    folder, n = base, 1
    while os.path.exists(folder):
        n += 1
        folder = f"{base}-{n}"
    os.makedirs(folder)
    dest = os.path.join(folder, os.path.basename(path))
    try:
        os.rename(path, dest)
    except OSError:
        shutil.copy2(path, dest)
        if os.path.getsize(dest) != os.path.getsize(path):
            raise CheckError(f"the copy of {path} in the Trash is incomplete; the original "
                             "was left in place.")
        os.unlink(path)
    return dest


def fix_loudness(path, dest, replace=False, fps=None, size=None, trash_root=None, now=None):
    """Two-pass loudnorm of the first audio stream to the destination's
    target, everything else copied. Writes <stem>.loudfix<ext> beside the
    original, checks it as if it had the original's name, and confirms the
    video stream is bit-identical. With replace, and only when all of that
    passed, the original goes to <trash_root>/deliver-loudfix-<timestamp>/
    and the fixed file takes its name. Returns {original, fixed, measured,
    result_loudnorm, check, video_identical, replaced, trashed, warnings,
    status}; raises ToolMissing or CheckError."""
    check_tools(ffmpeg=True)
    if not deliver.has_loudness_target(dest):
        raise CheckError(f"'{dest['key']}' has no loudness target (it is delivered "
                         "unnormalized); there is nothing to fix.")
    if not os.path.isfile(path):
        raise CheckError(f"{path} is not a file.")
    out = fixed_path(path)
    if os.path.lexists(out):
        raise CheckError(f"{out} already exists; nothing is overwritten. Move it away first.")
    info = probe(path)
    streams = info.get("streams") or []
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not audios:
        raise CheckError(f"{path} has no audio stream.")
    has_data = any(s.get("codec_type") in ("data", "attachment") for s in streams)
    warnings = []
    if len(audios) > 1:
        warnings.append(f"{len(audios)} audio streams: only the first is normalised; the "
                        "others are copied as they are.")
    if dest["format"] == "mp4" and has_data:
        warnings.append("data streams (such as a timecode track) are not copied into an mp4; "
                        "ffmpeg writes the timecode track again from the video stream's tag.")

    loud = dest["loudness"]
    target = float(loud["integrated_lufs"])
    tp = float(loud["true_peak_max_dbtp"]) - float(loud.get("fix_true_peak_margin_db") or 0)
    proc = _run([FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", _src(path),
                 "-map", "0:a:0", "-af", loudnorm_filter(target, tp, 11), "-f", "null", "-"],
                READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise CheckError(f"loudnorm could not measure {path}: {proc.stderr.strip()[-300:]}")
    measured = _loudnorm_json(proc.stderr)
    # linear mode needs a target range no lower than the source's
    lra = min(50.0, max(11.0, float(measured["input_lra"]) + 1.0))

    proc = _run(fix_command(path, out, dest, has_data, measured, target, tp, lra), READ_TIMEOUT_S)
    if proc.returncode != 0:
        if os.path.exists(out) and os.path.getsize(out) == 0:
            os.unlink(out)  # an empty file ffmpeg created and never wrote to
        left = " A partial file may remain there." if os.path.exists(out) else ""
        raise CheckError(f"ffmpeg could not write {out}: {proc.stderr.strip()[-400:]}{left}")
    second = _loudnorm_json(proc.stderr)
    if second.get("normalization_type") != "linear":
        warnings.append(f"loudnorm used {second.get('normalization_type')} mode: a linear gain "
                        "would have pushed the true peak over the limit, so it compressed the "
                        "peaks. Listen before delivering.")

    result = check(out, dest, fps=fps, size=size, as_name=os.path.basename(path))
    same = video_hash(path) == video_hash(out)
    passed = result["status"] == "pass" and same
    r = {"original": os.path.abspath(path), "fixed": os.path.abspath(out),
         "measured": measured, "result_loudnorm": second, "check": result,
         "video_identical": same, "replaced": False, "trashed": None,
         "warnings": warnings, "status": "pass" if passed else "fail"}
    if not same:
        warnings.append("the video stream differs from the original's; the fix is not used.")
    if replace and passed:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        r["trashed"] = _trash(path, trash_root or os.path.expanduser("~/.Trash"), stamp)
        os.rename(out, path)
        r["fixed"], r["replaced"] = os.path.abspath(path), True
    elif replace:
        warnings.append("not replaced: the fixed file did not pass; the original is untouched.")
    return r
