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

Folders: each destination renders into its own subfolder of the target
folder, named by its "subfolder" (default: the destination key, such as
youtube_16x9/), so one clip delivered to several platforms keeps one name
per aspect without two files colliding. "subfolder": null renders straight
into the target folder.

Captions modes: "sidecar" (an .srt beside the file, named <stem>.srt),
"burnin" (drawn into the picture by Resolve) and "none". Any caption file
already beside the target (caption_files: .srt, .vtt, .scc, .ttml or .xml
under the file's stem, in any case) blocks a sidecar job, and fails the
check of a burn-in or no-captions file. Resolve 21.0.4.5 writes a sidecar
as "<stem>_<subtitle track name>.ttml" (resolve_sidecars()), timed from the
timeline's timecode; rpresolve.captions turns it into <stem>.srt.

Auto captions (workflows.create_captions) take their line length and
line breaks from "deliver" "captions", one entry per frame shape
(caption_settings()): a 9:16 frame holds about 16 characters of burnt-in
text at Resolve's default size, a 16:9 frame the default 42.
"""

import os
import re

from . import paths

ASPECTS = ("16x9", "9x16", "1x1")
ASPECT_RATIO = {"16x9": 16 / 9, "9x16": 9 / 16, "1x1": 1.0}
# How far two width:height ratios may sit apart and still count as one
# shape. 1920x1088 counts as 16:9; DCI 4096x2160, 6.7% wider, falls outside.
ASPECT_TOLERANCE = 0.01
CAPTIONS = ("sidecar", "burnin", "none")
# Extensions of caption files that may sit beside a deliverable.
CAPTION_EXTS = ("srt", "vtt", "scc", "ttml", "xml")
FORMATS = ("mp4", "mov")
NAMINGS = ("clip", "master")
AUDIO_CODECS = ("aac", "lpcm")
SUBTITLE_FORMAT = {"sidecar": "SeparateFile", "burnin": "BurnIn"}
# A destination subfolder: one plain folder name, no path.
SUBFOLDER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

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
    (CAPTION_EXTS), in any case, dangling links included. Resolve names
    its sidecar <stem>_<track>.ttml (resolve_sidecars, seen live); the
    match stays broad so another tool's <stem>.en.srt or <stem>_x.vtt
    counts too. Sorted paths; [] when the folder cannot be read."""
    folder, base = os.path.split(output_path)
    stem = os.path.splitext(base)[0].casefold()
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return []
    return sorted(os.path.join(folder, n) for n in names
                  if n.casefold().startswith(stem) and "." in n
                  and n.rsplit(".", 1)[1].casefold() in CAPTION_EXTS)


# The name Resolve gives a sidecar it renders: <stem>_<subtitle track name>.ttml.
RESOLVE_SIDECAR_EXT = ".ttml"


def resolve_sidecars(output_path):
    """The TTML sidecars Resolve wrote beside output_path, as sorted
    [(track name, path)]: every "<stem>_<track>.ttml" there (the stem as
    written, the extension in any case). Spike 20 (2026-09-29) saw
    "<stem>_Subtitle 1.ttml" for a track named "Subtitle 1". [] when the
    folder cannot be read."""
    folder, base = os.path.split(output_path)
    head = os.path.splitext(base)[0] + "_"
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return []
    found = []
    for n in names:
        stem, ext = os.path.splitext(n)
        if ext.casefold() == RESOLVE_SIDECAR_EXT and stem.startswith(head) and len(stem) > len(head):
            found.append((stem[len(head):], os.path.join(folder, n)))
    return sorted(found)


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
            "frame_rates": house.get("frame_rates") or [],
            "data_burn_in": house.get("data_burn_in", "None"), "resolve": {}, "expect": {}}
    dest = merge(base, raw)
    dest["key"] = key
    if "subfolder" not in dest:
        dest["subfolder"] = key
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
    elif res != "timeline" and dest.get("aspect") in ASPECT_RATIO:
        want = ASPECT_RATIO[dest["aspect"]]
        if abs(res["width"] / res["height"] - want) / want > ASPECT_TOLERANCE:
            p.append(f"resolution {res['width']}x{res['height']} is not {dest['aspect']}, the "
                     "aspect its files are named for")
    audio = dest.get("audio") or {}
    if audio.get("codec") not in AUDIO_CODECS:
        p.append(f"audio.codec must be one of {', '.join(AUDIO_CODECS)}")
    if not _num(audio.get("sample_rate")):
        p.append("audio.sample_rate is missing")
    if audio.get("codec") == "lpcm" and audio.get("bit_depth") not in (16, 24, 32):
        p.append("LPCM audio needs audio.bit_depth 16, 24 or 32")
    if dest.get("captions") not in CAPTIONS:
        p.append(f"captions must be one of {', '.join(CAPTIONS)}")
    burn = dest.get("data_burn_in")
    if burn is not None and not (isinstance(burn, str) and burn.strip()):
        p.append('data_burn_in must be a Data Burn-in setting such as "None", or null to '
                 "leave the Deliver page's")
    loud = dest.get("loudness") or {}
    if loud.get("integrated_lufs") is not None:
        for k in ("integrated_lufs", "tolerance_lu", "true_peak_max_dbtp"):
            if not _num(loud.get(k)):
                p.append(f"loudness.{k} must be a number")
    color = dest.get("color") or {}
    if not isinstance(color.get("resolve"), dict) or not isinstance(color.get("expect"), dict):
        p.append("color needs a resolve block (tag strings) and an expect block (ffprobe values)")
    sub = dest.get("subfolder")
    if sub is not None and not (isinstance(sub, str) and SUBFOLDER_RE.fullmatch(sub)):
        p.append("subfolder must be one folder name (lowercase letters, digits, _ and -), "
                 "or null to render straight into the target folder")
    if dest.get("target_dir") is not None and not os.path.isabs(
            os.path.expanduser(str(dest["target_dir"]))):
        p.append("target_dir must be an absolute path")
    return p


def has_loudness_target(dest):
    return (dest.get("loudness") or {}).get("integrated_lufs") is not None


# ---------------------------------------------------------------------------
# Before queueing: where the file goes
# ---------------------------------------------------------------------------

# The Data volume's firmlink: /System/Volumes/Data/Volumes/Work is
# /Volumes/Work, and os.path.realpath() does not fold one into the other.
FIRMLINK = "/System/Volumes/Data"


def _folded(path):
    """path's real path, casefolded, with the firmlink prefix taken off, so
    every spelling of one folder on macOS compares equal as a string."""
    real = os.path.realpath(path).casefold()
    firm = FIRMLINK.casefold()
    return real[len(firm):] if real.startswith(firm + "/") else real


def volume_root(path, volumes_root="/Volumes"):
    """/Volumes/<name> when path sits on one, else None. Matched whatever
    the case (APFS and SMB here are case-insensitive, and realpath() does
    not fold case) and through the firmlink. The result keeps path's own
    spelling, so os.path.ismount() looks at the folder the path names."""
    root = _folded(volumes_root).rstrip("/")
    if not _folded(path).startswith(root + "/"):
        return None
    real = os.path.realpath(path)
    extra = FIRMLINK.count("/") if real.casefold().startswith(FIRMLINK.casefold() + "/") else 0
    return "/".join(real.split("/")[:root.count("/") + 2 + extra])


def same_output(a, b):
    """Whether two output paths name one file: the same name whatever the
    case, in the same folder (os.path.samefile when both folders exist,
    else their folded spellings). Errs toward a clash on a case-sensitive
    volume, which is the safe side for a render."""
    (da, na), (db, nb) = os.path.split(a), os.path.split(b)
    if na.casefold() != nb.casefold():
        return False
    try:
        return os.path.samefile(da, db)
    except OSError:
        return _folded(da) == _folded(db)


def output_folder(target_dir, dest):
    """The folder a destination's file goes in: target_dir/<subfolder>, or
    target_dir itself when the destination has none. A real path; the
    subfolder need not exist yet."""
    real = os.path.realpath(os.path.expanduser(target_dir))
    return os.path.join(real, dest["subfolder"]) if dest.get("subfolder") else real


def subfolders(config):
    """{subfolder: destination key} for every configured destination that
    has one, for telling which destination a folder belongs to."""
    out = {}
    for key, raw in (config.get("destinations") or {}).items():
        if raw:
            sub = raw.get("subfolder", key) if isinstance(raw, dict) else None
            if isinstance(sub, str) and sub:
                out[sub.casefold()] = key
    return out


def output_problems(target_dir, filename, dest, queued=(), volumes_root="/Volumes",
                    subfolder=None):
    """Every reason a render of `filename` into `target_dir` must not be
    queued, as a list (empty: go ahead). With subfolder, the file goes in
    target_dir/subfolder, which may not exist yet (the queue makes it) but
    must not be a file. `queued` holds the paths the render queue already
    writes to, compared by same_output()."""
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
    folder = real
    if subfolder:
        folder = os.path.join(real, subfolder)
        if os.path.islink(folder):
            problems.append(f"{folder} is a link; the destination's files go in a real folder "
                            "there, so the checks above cover where they land.")
            return problems
        if os.path.lexists(folder) and not os.path.isdir(folder):
            problems.append(f"{folder} exists and is not a folder; the destination's files go "
                            "in a folder of that name.")
            return problems
    out = os.path.join(folder, filename)
    if os.path.lexists(out):
        problems.append(f"{out} already exists; nothing is overwritten. Rename or move it, or "
                        "change the name parts.")
    if dest.get("captions") == "sidecar":
        found = caption_files(out)
        if found:
            problems.append(f"a caption file already sits beside it ({', '.join(found)}); "
                            "the sidecar would be overwritten or misnamed.")
    clash = sorted(q for q in queued if same_output(out, q))
    if clash:
        problems.append(f"a job already in the render queue writes {out}" +
                        (f" (as {clash[0]})" if clash[0] != out else "") + ".")
    return problems


def _shape(width, height):
    """'landscape', 'portrait' or 'square' (within ASPECT_TOLERANCE)."""
    ratio = width / height
    if abs(ratio - 1) <= ASPECT_TOLERANCE:
        return "square"
    return "landscape" if ratio > 1 else "portrait"


SHAPES = ("landscape", "portrait", "square")
LINE_BREAKS = ("single", "double")


def frame_shape(size):
    """'landscape', 'portrait' or 'square' for a (width, height), or None
    when either is missing."""
    if not size or not all(size):
        return None
    return _shape(int(size[0]), int(size[1]))


def caption_settings(config, shape):
    """{chars_per_line, line_break} for auto captions on a frame of this
    shape, from config["deliver"]["captions"][shape]. Raises DeliverError
    when the entry is missing or out of range (Resolve takes 1 to 60
    characters per line, and a single or double line break)."""
    if shape not in SHAPES:
        raise DeliverError(f"no caption settings for a frame shape of {shape!r}; the "
                           f"timeline's resolution must be readable ({', '.join(SHAPES)}).")
    entry = ((config.get("deliver") or {}).get("captions") or {}).get(shape)
    if not isinstance(entry, dict):
        raise DeliverError(f'the config has no deliver.captions.{shape} entry '
                           '({"chars_per_line": N, "line_break": "single" or "double"}).')
    chars, brk = entry.get("chars_per_line"), entry.get("line_break")
    problems = []
    if isinstance(chars, bool) or not isinstance(chars, int) or not 1 <= chars <= 60:
        problems.append(f"chars_per_line must be a whole number from 1 to 60 (got {chars!r})")
    if brk not in LINE_BREAKS:
        problems.append(f"line_break must be one of {', '.join(LINE_BREAKS)} (got {brk!r})")
    if problems:
        raise DeliverError(f"deliver.captions.{shape} in the config is not usable: " +
                           "; ".join(problems) + ".")
    return {"chars_per_line": chars, "line_break": brk}


def shape_problems(dest, size, timeline="the timeline"):
    """Refusals that come from the timeline's frame. A destination of a
    fixed size renders the timeline scaled into that frame, with bars where
    the shapes differ (Resolve does not reframe), so the timeline must have
    the destination's shape: the same orientation, and width:height within
    ASPECT_TOLERANCE. size is the timeline's (width, height). A destination
    that renders at the timeline's size has nothing to compare."""
    if dest["resolution"] == "timeline":
        return []
    dw, dh = int(dest["resolution"]["width"]), int(dest["resolution"]["height"])
    label = f"{dest.get('aspect') or _shape(dw, dh)} ({dw}x{dh})"
    if not size or not all(size):
        return [f"'{dest['key']}' renders {label}, and {timeline}'s resolution could not be "
                "read to compare its shape."]
    w, h = int(size[0]), int(size[1])
    want, got = dw / dh, w / h
    if _shape(w, h) != _shape(dw, dh):
        return [f"'{dest['key']}' is {_shape(dw, dh)}, {label}, but {timeline} is {w}x{h}, "
                f"{_shape(w, h)}: Resolve would scale the picture into the frame with bars. "
                f"Queue the timeline made for {dest.get('aspect') or label} instead."]
    if abs(got - want) / want > ASPECT_TOLERANCE:
        return [f"{timeline} is {w}x{h}, {got:.3f}:1, and '{dest['key']}' is {label}, "
                f"{want:.3f}:1: more than {ASPECT_TOLERANCE:.0%} apart, so Resolve would scale "
                "the picture into the frame with bars. Queue a timeline of the destination's "
                "shape."]
    return []


def timeline_problems(dest, subtitle_counts, disabled_counts=()):
    """Refusals that come from the timeline: captions wanted but no
    subtitle track, only empty ones, or captions only on disabled tracks.
    subtitle_counts: items per enabled track; disabled_counts: items per
    disabled track, which Resolve does not render."""
    if dest.get("captions") not in ("sidecar", "burnin"):
        return []
    if not any(subtitle_counts) and any(disabled_counts):
        return [f"'{dest['key']}' wants {dest['captions']} captions but the timeline's only "
                "subtitle items are on disabled track(s), which Resolve does not render. "
                "Enable the caption track first."]
    if not subtitle_counts:
        return [f"'{dest['key']}' wants {dest['captions']} captions but the timeline has no "
                "subtitle track. Add the captions first: resolve_workflow.py captions, or the "
                "MCP create_captions, on an [auto] timeline (duplicate_timeline_auto makes "
                "one from a timeline that is not)."]
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


# The NTSC rates Resolve spells short ('23.976', '29.97', '59.94'): n x 1000/1001.
NTSC_RATES = tuple(n * 1000.0 / 1001 for n in (24, 30, 48, 60, 96, 120))


def exact_fps(value):
    """A frame rate as Resolve spells it ('29.97', '23.976', '29.97 DF',
    25) as the rate it stands for, for frame math: an NTSC rate is n x
    1000/1001 (29.97 is 29.97002997...), and the plain float of '29.97'
    puts a frame 0.108 frame off per hour of offset. Other rates as
    fps_number reads them; None when unreadable."""
    f = fps_number(value)
    if f is None:
        return None
    near = min(NTSC_RATES, key=lambda q: abs(q - f))
    return near if abs(near - f) < 0.005 else f


# Every SetRenderSettings key the scripting README lists. The Deliver page
# keeps each one until something sets it again, so a key a job does not set
# comes from whatever the page last held (carried_over()).
RENDER_SETTING_KEYS = (
    "SelectAllFrames", "MarkIn", "MarkOut", "TargetDir", "CustomName", "UniqueFilenameStyle",
    "ExportVideo", "ExportAudio", "FormatWidth", "FormatHeight", "FrameRate",
    "PixelAspectRatio", "VideoQuality", "AudioCodec", "AudioBitDepth", "AudioSampleRate",
    "ColorSpaceTag", "GammaTag", "ExportAlpha", "EncodingProfile", "MultiPassEncode",
    "AlphaMode", "NetworkOptimization", "ClipStartFrame", "TimelineStartTimecode",
    "ReplaceExistingFilesInPlace", "ExportSubtitle", "SubtitleFormat", "UseFullExtents",
    "AddFrameHandles", "DataBurnIn")
# The render mode a destination job is queued in (Get/SetCurrentRenderMode:
# 0 Individual clips, 1 Single clip): one file per job.
SINGLE_CLIP = 1


def render_steps(dest, target_dir, filename, size=None, fps=None):
    """The SetRenderSettings calls that set up `dest`, in order, as
    [{"settings": {...}, "required": bool}]. The first carries the core
    fields; each later call carries one concern, so a refusal names it. A
    required call that fails stops the job from being queued; an optional
    one is a warning. size is (width, height) for a "timeline" resolution;
    fps is the timeline's frame rate. The Deliver page keeps every field
    until something sets it again, and these steps do not set them all:
    carried_over() names the rest, which the job takes from whatever the
    page last held. The render mode is not a render setting; the queue
    sets it to Single clip (SINGLE_CLIP) on its own."""
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
    if dest.get("data_burn_in") is not None:
        steps.append({"settings": {"DataBurnIn": dest["data_burn_in"]}, "required": True})
    for key, value in sorted((dest.get("resolve") or {}).items()):
        if value is not None:
            steps.append({"settings": {key: value}, "required": False})
    # ReplaceExistingFilesInPlace is not sent: Resolve 21.0.4.5 refuses it (spike 19), and
    # output_problems already refuses a target file that exists.
    return steps


def changed_fields(steps):
    """Every Deliver field the steps set, in order."""
    out = []
    for s in steps:
        out += [k for k in s["settings"] if k not in out]
    return out


def carried_over(steps):
    """The render settings the steps leave alone, which the job takes from
    whatever the Deliver page last held: every README key not set, less
    MarkIn and MarkOut (ignored once SelectAllFrames is on) and
    SubtitleFormat when subtitles are off."""
    done = {}
    for s in steps:
        done.update(s["settings"])
    moot = set()
    if done.get("SelectAllFrames"):
        moot |= {"MarkIn", "MarkOut"}
    if done.get("ExportSubtitle") is False:
        moot.add("SubtitleFormat")
    return [k for k in RENDER_SETTING_KEYS if k not in done and k not in moot]
