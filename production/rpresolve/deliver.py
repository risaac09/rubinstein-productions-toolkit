"""
rpresolve.deliver: delivery destinations, deliverable names, and the checks
that run before a render is queued. Stdlib only. Nothing here touches
Resolve or reads media; delivercheck.py checks the rendered file.

A destination (resolve-config.json "destinations") is what one platform
wants: container, codec, size, audio, loudness target, captions mode and
colour tags. destination() lays it over the house rules in "deliver", so a
destination only states what differs.

Deliverable names, checked strictly both ways (deliverable_name builds one,
parse_name checks a file name against the rule):

    clip    SW001_Guest_01_example-clip_16x9.mp4
            show code (1 to 8 capital letters), episode (3 ASCII digits),
            guest ([A-Za-z0-9]+), index (2 digits, 01 to 99), slug (lowercase
            letters and digits in words joined by single hyphens), aspect
            (16x9, 9x16 or 1x1), extension from the destination.
    master  Client_example-slug_master.mov
            client ([A-Za-z0-9]+), slug as above.

Captions modes: "sidecar" (an .srt beside the file, named <stem>.srt),
"burnin" (drawn into the picture by Resolve) and "none". Any caption file
already beside the target (caption_files: .srt, .vtt, .scc, .ttml or .xml
under the file's stem, in any case) blocks a sidecar job, and fails the
check of a burn-in or no-captions file.
"""

import os
import re

from . import paths

ASPECTS = ("16x9", "9x16", "1x1")
CAPTIONS = ("sidecar", "burnin", "none")
# Extensions of caption files that may sit beside a deliverable.
CAPTION_EXTS = ("srt", "vtt", "scc", "ttml", "xml")
FORMATS = ("mp4", "mov")
NAMINGS = ("clip", "master")
AUDIO_CODECS = ("aac", "lpcm")
SUBTITLE_FORMAT = {"sidecar": "SeparateFile", "burnin": "BurnIn"}

SHOW = r"[A-Z]{1,8}"
WORD = r"[A-Za-z0-9]+"
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SLUG_MAX = 60
# Digits are written [0-9]: in a str pattern \d also matches fullwidth and
# other Unicode digits, which the rule does not allow.
CLIP_RE = re.compile(rf"^(?P<show>{SHOW})(?P<episode>[0-9]{{3}})_(?P<guest>{WORD})"
                     rf"_(?P<index>[0-9]{{2}})_(?P<slug>{SLUG})_(?P<aspect>16x9|9x16|1x1)"
                     rf"\.(?P<ext>mp4|mov)$")
MASTER_RE = re.compile(rf"^(?P<client>{WORD})_(?P<slug>{SLUG})_master\.(?P<ext>mov)$")

CLIP_PARTS = ("show", "episode", "guest", "index", "slug")
MASTER_PARTS = ("client", "slug")


class DeliverError(ValueError):
    """A destination, a name or a target folder that cannot be used."""


class NameRuleError(DeliverError):
    """A deliverable name that breaks the naming rule."""


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _digits(value, field, width, low, high):
    if isinstance(value, bool):
        raise NameRuleError(f"{field} must be a number, got {value!r}.")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,%d}" % width, value):
        value = int(value)
    if not isinstance(value, int) or not low <= value <= high:
        raise NameRuleError(f"{field} must be a whole number from {low} to {high} "
                            f"(written as {width} digits), got {value!r}.")
    return f"{value:0{width}d}"


def _match(value, field, pattern, rule):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise NameRuleError(f"{field} {value!r} breaks the rule: {rule}.")
    return value


def _slug(slug):
    _match(slug, "slug", SLUG, "lowercase letters and digits in words joined by single "
           "hyphens, such as example-clip")
    if len(slug) > SLUG_MAX:
        raise NameRuleError(f"slug is {len(slug)} characters; keep it to {SLUG_MAX}.")
    return slug


def _ext(ext):
    ext = str(ext or "").lstrip(".")
    if ext not in FORMATS:
        raise NameRuleError(f"extension {ext!r} must be one of {', '.join(FORMATS)}.")
    return ext


def deliverable_name(show, episode, guest, index, slug, aspect, ext):
    """SW001_Guest_01_example-clip_16x9.mp4 from its parts. Raises
    NameRuleError naming the part that breaks the rule."""
    show = _match(show, "show", SHOW, "1 to 8 capital letters, such as SW")
    episode = _digits(episode, "episode", 3, 0, 999)
    guest = _match(guest, "guest", WORD, "letters and digits only, no spaces or punctuation")
    index = _digits(index, "index", 2, 1, 99)
    slug = _slug(slug)
    if aspect not in ASPECTS:
        raise NameRuleError(f"aspect {aspect!r} must be one of {', '.join(ASPECTS)}.")
    return f"{show}{episode}_{guest}_{index}_{slug}_{aspect}.{_ext(ext)}"


def master_name(client, slug, ext="mov"):
    """Client_example-slug_master.mov. Raises NameRuleError."""
    client = _match(client, "client", WORD, "letters and digits only, no spaces or punctuation")
    ext = _ext(ext)
    if ext != "mov":
        raise NameRuleError(f"a client master is a .mov (got .{ext}).")
    return f"{client}_{_slug(slug)}_master.{ext}"


def parse_name(filename):
    """The parts of a deliverable file name: {kind: "clip", show, episode,
    guest, index, slug, aspect, ext} or {kind: "master", client, slug, ext}.
    Raises NameRuleError saying what is wrong."""
    base = os.path.basename(str(filename))
    m = CLIP_RE.match(base)
    if m:
        parts = m.groupdict()
        if parts["index"] == "00":
            raise NameRuleError(f"{base}: index 00; clip indexes run 01 to 99.")
        if len(parts["slug"]) > SLUG_MAX:
            raise NameRuleError(f"{base}: slug is longer than {SLUG_MAX} characters.")
        return {"kind": "clip", **parts, "episode": int(parts["episode"]),
                "index": int(parts["index"])}
    m = MASTER_RE.match(base)
    if m:
        if len(m.group("slug")) > SLUG_MAX:
            raise NameRuleError(f"{base}: slug is longer than {SLUG_MAX} characters.")
        return {"kind": "master", **m.groupdict()}
    raise NameRuleError(f"{base}: {_why_not(base)}")


def _why_not(base):
    """The first reason a name fails both rules, for the error message."""
    stem, dot, ext = base.rpartition(".")
    if not dot:
        return "no extension; expected .mp4 or .mov."
    if ext not in FORMATS:
        return f"extension .{ext}; expected .mp4 or .mov."
    fields = stem.split("_")
    if fields[-1] == "master":
        if len(fields) != 3:
            return "a master name is <Client>_<slug>_master.mov (three parts)."
        if not re.fullmatch(WORD, fields[0]):
            return f"client {fields[0]!r} must be letters and digits only."
        if not re.fullmatch(SLUG, fields[1]):
            return f"slug {fields[1]!r} must be lowercase words joined by single hyphens."
        return "a client master is a .mov."
    if len(fields) != 5:
        return ("expected <SHOW><episode>_<Guest>_<index>_<slug>_<aspect>, five parts "
                f"joined by underscores (found {len(fields)}); or <Client>_<slug>_master.mov.")
    head, guest, index, slug, aspect = fields
    if not re.fullmatch(rf"{SHOW}[0-9]{{3}}", head):
        return f"{head!r} must be the show code in capitals then a 3-digit episode, as SW001."
    if not re.fullmatch(WORD, guest):
        return f"guest {guest!r} must be letters and digits only."
    if not re.fullmatch(r"[0-9]{2}", index) or index == "00":
        return f"index {index!r} must be 2 digits, 01 to 99."
    if not re.fullmatch(SLUG, slug):
        return f"slug {slug!r} must be lowercase words joined by single hyphens."
    if aspect not in ASPECTS:
        return f"aspect {aspect!r} must be one of {', '.join(ASPECTS)}."
    return "does not match the naming rule."


def name_for(dest, parts):
    """The file name for a destination from name parts: show, episode,
    guest, index and slug for a clip; client and slug for a master. The
    aspect and extension come from the destination."""
    parts = dict(parts or {})
    want = CLIP_PARTS if dest["naming"] == "clip" else MASTER_PARTS
    missing = [p for p in want if parts.get(p) in (None, "")]
    extra = sorted(set(parts) - set(want))
    if missing or extra:
        raise NameRuleError(
            f"destination '{dest['key']}' is named from {', '.join(want)}" +
            (f"; missing {', '.join(missing)}" if missing else "") +
            (f"; not used: {', '.join(extra)}" if extra else "") + ".")
    if dest["naming"] == "clip":
        return deliverable_name(parts["show"], parts["episode"], parts["guest"],
                                parts["index"], parts["slug"], dest["aspect"], dest["format"])
    return master_name(parts["client"], parts["slug"], dest["format"])


def name_problems(filename, dest):
    """Why filename is not a valid name for dest, as a list (empty: fine)."""
    try:
        parts = parse_name(filename)
    except NameRuleError as e:
        return [str(e)]
    problems = []
    if parts["kind"] != dest["naming"]:
        problems.append(f"a {parts['kind']} name, but '{dest['key']}' takes a "
                        f"{dest['naming']} name.")
    if parts["ext"] != dest["format"]:
        problems.append(f"extension .{parts['ext']}, but '{dest['key']}' is .{dest['format']}.")
    if parts["kind"] == "clip" and dest["naming"] == "clip" and parts["aspect"] != dest["aspect"]:
        problems.append(f"aspect {parts['aspect']}, but '{dest['key']}' is {dest['aspect']}.")
    return problems


def sidecar_path(output_path):
    """Where a sidecar caption file belongs: <stem>.srt beside the file."""
    return os.path.splitext(output_path)[0] + ".srt"


def caption_files(output_path):
    """Every caption file already beside output_path that could belong to
    it: a name that starts with its stem and ends in a caption extension
    (CAPTION_EXTS), in any case, dangling links included. Resolve's own
    sidecar name is unverified, so <stem>.en.srt and <stem>_x.vtt count
    too. Sorted paths; [] when the folder cannot be read."""
    folder, base = os.path.split(output_path)
    stem = os.path.splitext(base)[0].casefold()
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return []
    return sorted(os.path.join(folder, n) for n in names
                  if n.casefold().startswith(stem) and "." in n
                  and n.rsplit(".", 1)[1].casefold() in CAPTION_EXTS)


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------

def destination_keys(config):
    return sorted(k for k, v in (config.get("destinations") or {}).items() if v)


def destination(config, key):
    """The destination `key` laid over the house rules, with "key" added.
    Raises DeliverError when it is unknown or incomplete."""
    from .config import merge
    dests = config.get("destinations") or {}
    raw = dests.get(key)
    if not raw:
        raise DeliverError(f"unknown destination '{key}'. Configured: "
                           f"{', '.join(destination_keys(config)) or 'none'}.")
    house = config.get("deliver") or {}
    base = {"loudness": house.get("loudness") or {}, "color": house.get("color") or {},
            "frame_rates": house.get("frame_rates") or [], "resolve": {}, "expect": {}}
    dest = merge(base, raw)
    dest["key"] = key
    problems = destination_problems(dest)
    if problems:
        raise DeliverError(f"destination '{key}' in the config is not usable: " +
                           "; ".join(problems))
    return dest


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def destination_problems(dest):
    """What makes a merged destination unusable, as a list."""
    p = []
    if dest.get("naming") not in NAMINGS:
        p.append(f"naming must be one of {', '.join(NAMINGS)}")
    if dest.get("naming") == "clip" and dest.get("aspect") not in ASPECTS:
        p.append(f"aspect must be one of {', '.join(ASPECTS)}")
    if dest.get("format") not in FORMATS:
        p.append(f"format must be one of {', '.join(FORMATS)}")
    if dest.get("naming") == "master" and dest.get("format") != "mov":
        p.append("a client master is a .mov")
    if not isinstance(dest.get("codec"), str) or not dest["codec"]:
        p.append("codec (Resolve's codec name) is missing")
    res = dest.get("resolution")
    if res != "timeline" and not (isinstance(res, dict) and _num(res.get("width")) and
                                  _num(res.get("height")) and res["width"] > 0 and
                                  res["height"] > 0):
        p.append('resolution must be {"width": W, "height": H} or "timeline"')
    audio = dest.get("audio") or {}
    if audio.get("codec") not in AUDIO_CODECS:
        p.append(f"audio.codec must be one of {', '.join(AUDIO_CODECS)}")
    if not _num(audio.get("sample_rate")):
        p.append("audio.sample_rate is missing")
    if audio.get("codec") == "lpcm" and audio.get("bit_depth") not in (16, 24, 32):
        p.append("LPCM audio needs audio.bit_depth 16, 24 or 32")
    if dest.get("captions") not in CAPTIONS:
        p.append(f"captions must be one of {', '.join(CAPTIONS)}")
    loud = dest.get("loudness") or {}
    if loud.get("integrated_lufs") is not None:
        for k in ("integrated_lufs", "tolerance_lu", "true_peak_max_dbtp"):
            if not _num(loud.get(k)):
                p.append(f"loudness.{k} must be a number")
    color = dest.get("color") or {}
    if not isinstance(color.get("resolve"), dict) or not isinstance(color.get("expect"), dict):
        p.append("color needs a resolve block (tag strings) and an expect block (ffprobe values)")
    if dest.get("target_dir") is not None and not os.path.isabs(
            os.path.expanduser(str(dest["target_dir"]))):
        p.append("target_dir must be an absolute path")
    return p


def has_loudness_target(dest):
    return (dest.get("loudness") or {}).get("integrated_lufs") is not None


# ---------------------------------------------------------------------------
# Before queueing: where the file goes
# ---------------------------------------------------------------------------

def volume_root(path, volumes_root="/Volumes"):
    """/Volumes/<name> when path sits on one, else None."""
    root = os.path.realpath(volumes_root).rstrip("/")
    real = os.path.realpath(path)
    if not real.startswith(root + "/"):
        return None
    return os.path.join(root, real[len(root) + 1:].split("/", 1)[0])


def output_problems(target_dir, filename, dest, queued=(), volumes_root="/Volumes"):
    """Every reason a render of `filename` into `target_dir` must not be
    queued, as a list (empty: go ahead). `queued` holds the real paths the
    render queue already writes to."""
    if not os.path.isabs(os.path.expanduser(target_dir)):
        return [f"target dir {target_dir!r} must be an absolute path."]
    real = os.path.realpath(os.path.expanduser(target_dir))
    if not os.path.isdir(real):
        return [f"target dir {target_dir} does not exist or is not a folder; make it first."]
    problems = []
    vol = volume_root(real, volumes_root)
    if vol and not os.path.ismount(vol):
        problems.append(f"{target_dir} is under {vol}, which is not mounted: that folder is on "
                        "the boot disk (a share that dropped leaves its folders behind). "
                        "Mount the share first.")
    if paths.in_any_git_tree(real):
        problems.append(f"{target_dir} is inside a git working tree; deliverables go outside "
                        "any repository.")
    out = os.path.join(real, filename)
    if os.path.lexists(out):
        problems.append(f"{out} already exists; nothing is overwritten. Rename or move it, or "
                        "change the name parts.")
    if dest.get("captions") == "sidecar":
        found = caption_files(out)
        if found:
            problems.append(f"a caption file already sits beside it ({', '.join(found)}); "
                            "the sidecar would be overwritten or misnamed.")
    if out in {os.path.realpath(q) for q in queued}:
        problems.append(f"a job already in the render queue writes {out}.")
    return problems


def timeline_problems(dest, subtitle_counts):
    """Refusals that come from the timeline: captions wanted but no
    subtitle track, or only empty ones. subtitle_counts: items per track."""
    if dest.get("captions") not in ("sidecar", "burnin"):
        return []
    if not subtitle_counts:
        return [f"'{dest['key']}' wants {dest['captions']} captions but the timeline has no "
                "subtitle track. Add the captions (an .srt on a subtitle track) first."]
    if not any(subtitle_counts):
        return [f"'{dest['key']}' wants {dest['captions']} captions but the timeline's "
                f"{len(subtitle_counts)} subtitle track(s) are empty."]
    return []


# ---------------------------------------------------------------------------
# The Deliver page settings for a destination
# ---------------------------------------------------------------------------

def fps_number(value):
    """A timeline frame rate setting ('23.976', '29.97 DF', 25) as a float,
    or None."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(value if value is not None else ""))
    return float(m.group(1)) if m else None


def render_steps(dest, target_dir, filename, size=None, fps=None):
    """The SetRenderSettings calls that set up `dest`, in order, as
    [{"settings": {...}, "required": bool}]. The first carries the core
    fields; each later call carries one concern, so a refusal names it. A
    required call that fails stops the job from being queued; an optional
    one is a warning. size is (width, height) for a "timeline" resolution;
    fps is the timeline's frame rate. Every field is set every time,
    because the Deliver page keeps whatever the last job left."""
    if dest["resolution"] == "timeline":
        if not size or not all(size):
            raise DeliverError(f"'{dest['key']}' renders at the timeline's size, and the "
                               "timeline's resolution could not be read.")
        width, height = int(size[0]), int(size[1])
    else:
        width, height = int(dest["resolution"]["width"]), int(dest["resolution"]["height"])
    audio = dest["audio"]
    steps = [{"settings": {"SelectAllFrames": True,
                           "TargetDir": os.path.realpath(target_dir),
                           "CustomName": os.path.splitext(filename)[0],
                           "FormatWidth": width, "FormatHeight": height,
                           "ExportVideo": True, "ExportAudio": True}, "required": True}]
    if fps:
        steps.append({"settings": {"FrameRate": float(fps)}, "required": False})
    steps.append({"settings": {"AudioCodec": audio.get("resolve_codec") or audio["codec"]},
                  "required": True})
    steps.append({"settings": {"AudioSampleRate": int(audio["sample_rate"])}, "required": True})
    if audio.get("bit_depth"):
        steps.append({"settings": {"AudioBitDepth": int(audio["bit_depth"])}, "required": True})
    for key in ("ColorSpaceTag", "GammaTag"):
        tag = (dest["color"].get("resolve") or {}).get(key)
        if tag:
            steps.append({"settings": {key: tag}, "required": True})
    mode = dest["captions"]
    steps.append({"settings": ({"ExportSubtitle": True, "SubtitleFormat": SUBTITLE_FORMAT[mode]}
                               if mode in SUBTITLE_FORMAT else {"ExportSubtitle": False}),
                  "required": True})
    for key, value in sorted((dest.get("resolve") or {}).items()):
        if value is not None:
            steps.append({"settings": {key: value}, "required": False})
    steps.append({"settings": {"ReplaceExistingFilesInPlace": False}, "required": False})
    return steps


def changed_fields(steps):
    """Every Deliver field the steps set, in order."""
    out = []
    for s in steps:
        out += [k for k in s["settings"] if k not in out]
    return out
