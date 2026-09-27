"""
rpresolve.detect: offline camera and picture-profile classification from
media file headers. Stdlib only; nothing here imports or calls Resolve, and
no file is ever opened for writing.

Three header sources are read per file:
    - ffprobe -show_format -show_streams -of json
    - exiftool -G1 -a -s -j (vendor maker notes, QuickTime Keys, UserData)
    - the video sample description, parsed here: the colr atom (nclc/nclx
      codes, full-range flag), Apple's logs atom, Dolby Vision dvvC. ffprobe
      shows neither the raw colr codes nor the logs atom.

The decision table below was hand-verified against the footage on the NAS
on 2026-09-26. Rules run in order and the first match wins:

    0a  derived or render: encoder DaVinci Resolve, Lavf or HandBrake, or a
        dji_mimo_* / dji_export_* file. Skip camera assignment.
    0b  corrupt: ffprobe reports "moov atom not found" (truncated or empty).
    0c  proxy: .LRF, or a file in a Proxy/ folder beside a same-basename
        original. Link it; do not grade it.
    1   Blackmagic RAW: sample-entry fourcc brst/brhq/brxq/brlt, or .braw.
    2   BRAW proxy: com.blackmagic-design.camera.codec=braw.
    3   Atomos Ninja V: profile from com.atomos.hdr.gamma/gamut (or the
        raw.intermediate tags on ProRes RAW). Never from colr transfer=2,
        which the Ninja writes on log and Rec.709 clips alike.
    4   Panasonic Lumix: XML CaptureGamma/CaptureGamut, then PhotoStyle
        (GH5 code 10 = V-Log L; code 13 = HLG only when the VUI says
        arib-std-b67), then HybridLogGamma. VUI 709 tags are ignored.
    5   Blackmagic Camera app on iPhone: Apple Log from the v1 tag (logs
        atom or customgamma) or colr 9/2/9; any other logs or customgamma
        value goes to review (Apple Log 2 tagging is UNVERIFIED).
    6   iPhone camera app: Apple Log (logs atom), HLG (colr 9/18/9 or
        dvvC), or SDR Rec.709.
    7   DJI Osmo Pocket 3 / Osmo Action 5 Pro. Normal vs D-Log M is not in
        the headers, so anything without an HLG VUI goes to review.
    8   DJI drone (UserData Model FC####, handler DJI.AVC). Comment
        Type=Normal suggests Rec.709, but the survey grades it Low: review.
    9   GoPro. Colour profile is not pinned: review.
    10  OBS: Rec.709, upstream camera unknown (capture card).
    11  Zoom: Rec.709; the bt470bg primaries/matrix tags are wrong.
    12  iPhone screen recording: sRGB.
    13  unknown.

Never guess. A file the rules cannot pin gets profile "review" and an empty
input_color_space, and the note column names what was missing. A pinned
row is high or medium confidence; low appears only on review rows.

input_color_space holds the Resolve colour-space name to tag the clip with.
Each CS_* name below appears verbatim in the Resolve 21.0.4 binary, except
the BRAW placeholder "auto (camera RAW)": RCM decodes camera RAW without a
per-clip input tag. Sandbox spike 4 (2026-09-26) wrote 'Panasonic
V-Gamut/V-Log' with SetClipProperty('Input Color Space', name) and read it
back exactly; the other names are Resolve's own auto-tag strings (spike 5)
and are not yet written through the API.

Camera SDR (CS_CAMERA_SDR) and screen, Zoom and OBS content (CS_REC709)
are separate constants on purpose. Resolve auto-tags camera SDR
'Rec.709 (Scene)', while non-709 tags fall to 'Rec.709 Gamma 2.4', and
which of the two camera SDR should get is an open decision (spike 7). Both
read 'Rec.709 Gamma 2.4' until it is made; the decision changes one line.

Output columns are COLUMNS: the eight the pipeline plan names, plus a note
saying why a row was pinned or what is missing.
"""

import json
import os
import re
import struct
import subprocess
from fractions import Fraction

FFPROBE = os.environ.get("RPRESOLVE_FFPROBE", "/opt/homebrew/bin/ffprobe")
EXIFTOOL = os.environ.get("RPRESOLVE_EXIFTOOL", "/usr/local/bin/exiftool")

COLUMNS = (
    "path", "camera", "profile", "input_color_space", "data_level", "vfr",
    "confidence", "rule", "note",
)

REVIEW = "review"
CORRUPT = "corrupt"

CS_VLOG = "Panasonic V-Gamut/V-Log"
CS_REC709 = "Rec.709 Gamma 2.4"      # screen, Zoom and OBS content
CS_CAMERA_SDR = "Rec.709 Gamma 2.4"  # camera SDR; open decision, see the docstring
CS_HLG = "Rec.2100 HLG"
CS_APPLE_LOG = "Apple Log"
CS_SRGB = "sRGB"
CS_BMD_FILM_GEN5 = "Blackmagic Design Film Gen 5"
CS_CAMERA_RAW = "auto (camera RAW)"

# What detect walks into when handed a directory. A file named explicitly
# on the command line is always probed, whatever its extension.
VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".mxf", ".braw", ".lrf", ".mkv", ".avi",
    ".mts", ".m2ts",
}
SKIP_DIRS = {"#recycle", "@eaDir", "#snapshot"}

BRAW_FOURCCS = {"brst", "brhq", "brxq", "brlt"}
PRORES_RAW_FOURCCS = {"aprn", "aprh"}
APPLE_LOG_TAG = "com.apple.rec2020.apple-log"
DOVI_COMPAT_HLG = 4  # Dolby Vision bl_signal_compatibility_id for an HLG base
BMD_FILMLOG_TAG = "com.blackmagic-design.camera.filmlog"

# The moov atom is read whole. A multi-hour clip's moov is a few MB; this
# cap only guards against a corrupt size field.
MAX_MOOV_BYTES = 64 * 1024 * 1024

# Container boxes nest trak > mdia > minf > stbl, four deep, in every file
# seen. The walk stops descending past this depth, so a malformed file with
# thousands of nested containers cannot exhaust the Python stack.
MAX_BOX_DEPTH = 8


class DetectError(RuntimeError):
    """Base class for errors raised by this module."""


class ToolMissing(DetectError):
    """ffprobe or exiftool is not installed where detect expects it."""


# ---------------------------------------------------------------------------
# Header collection (shells out; never writes)
# ---------------------------------------------------------------------------

def check_tools(ffprobe=None, exiftool=None):
    """Raise ToolMissing unless both tools exist and are executable."""
    ffprobe = ffprobe or FFPROBE
    exiftool = exiftool or EXIFTOOL
    missing = []
    for label, path, hint in (
        ("ffprobe", ffprobe, "brew install ffmpeg"),
        ("exiftool", exiftool, "install ExifTool from exiftool.org"),
    ):
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            missing.append(f"{label} not found at {path} ({hint}; or set "
                           f"RPRESOLVE_{label.upper()} to its path)")
    if missing:
        raise ToolMissing("; ".join(missing))


def _ffprobe_arg(path):
    # ffprobe reads a leading '-' as an option and a bare 'word:' prefix
    # (clip:1.mov) as a protocol name. The file: protocol takes the rest
    # as a plain path in both cases.
    return "file:" + path


def run_ffprobe(path, ffprobe=None, timeout=120):
    """Return (parsed JSON or None, stderr text)."""
    cmd = [ffprobe or FFPROBE, "-v", "error", "-show_format", "-show_streams",
           "-of", "json", _ffprobe_arg(path)]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"ffprobe timed out after {timeout}s"
    except OSError as e:
        raise ToolMissing(f"cannot run ffprobe: {e}")
    err = (proc.stderr or "").strip()
    try:
        data = json.loads(proc.stdout) if (proc.stdout or "").strip() else None
    except ValueError:
        data = None
    if data is not None and not data.get("streams") and not data.get("format"):
        data = None
    if proc.returncode != 0 and not err:
        err = f"ffprobe exited {proc.returncode}"
    return data, err


def _exiftool_arg(path):
    # exiftool reads a leading '-' as an option.
    return "./" + path if path.startswith("-") else path


def run_exiftool(paths, exiftool=None, timeout=600, chunk=64, single_timeout=60):
    """Return {path: exiftool dict} for the paths exiftool could read. Runs
    in batches to spare perl start-up; a path missing from a batch result
    is retried on its own, with single_timeout, so one stalled read cannot
    cost a full batch timeout per file."""
    exiftool = exiftool or EXIFTOOL
    results = {}

    def _run(batch, timeout=timeout):
        args = [_exiftool_arg(p) for p in batch]
        cmd = [exiftool, "-G1", "-a", "-s", "-j", "-api", "LargeFileSupport=1"] + args
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return []
        except OSError as e:
            raise ToolMissing(f"cannot run exiftool: {e}")
        try:
            docs = json.loads(proc.stdout) if (proc.stdout or "").strip() else []
        except ValueError:
            docs = []
        return docs if isinstance(docs, list) else []

    for i in range(0, len(paths), chunk):
        batch = list(paths[i:i + chunk])
        by_arg = {_exiftool_arg(p): p for p in batch}
        for doc in _run(batch):
            src = doc.get("SourceFile") if isinstance(doc, dict) else None
            if src in by_arg:
                results[by_arg[src]] = doc
        for p in batch:
            if p not in results:
                docs = _run([p], timeout=min(timeout, single_timeout))
                if docs and isinstance(docs[0], dict):
                    results[p] = docs[0]
    return results


# ---------------------------------------------------------------------------
# Sample-description atoms (ISO BMFF / QuickTime), parsed in memory
# ---------------------------------------------------------------------------

_CONTAINERS = {b"trak", b"mdia", b"minf", b"stbl"}


def _boxes(buf, start, end):
    """Yield (type, payload_start, box_end) for the boxes in buf[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[pos:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield typ, pos + header, min(pos + size, end)
        pos += size


def _fourcc(raw):
    return raw.decode("latin-1")


def _clean_text(raw):
    """Decode a handler or compressor name: Pascal or C string, stripped of
    control characters and padding."""
    if raw and raw[0] == len(raw) - 1:
        raw = raw[1:]
    text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    return clean_label(text)


def clean_label(text):
    """Strip control characters (QuickTime Pascal-string length bytes show
    up in ffprobe's handler_name) and surrounding whitespace."""
    return re.sub(r"[\x00-\x1f\x7f]", "", text or "").strip()


def _parse_colr(payload):
    kind = _fourcc(payload[:4])
    out = {"type": kind}
    if kind in ("nclc", "nclx") and len(payload) >= 10:
        p, t, m = struct.unpack(">HHH", payload[4:10])
        out.update(primaries=p, transfer=t, matrix=m)
        if kind == "nclx" and len(payload) >= 11:
            out["full_range"] = bool(payload[10] & 0x80)
    return out


def _parse_dovi(payload):
    if len(payload) < 4:
        return {}
    bits = struct.unpack(">H", payload[2:4])[0]
    out = {"profile": bits >> 9}
    if len(payload) >= 5:
        out["compatibility_id"] = payload[4] >> 4
    return out


def _parse_video_entry(buf, start, end):
    """VisualSampleEntry: 8-byte box header, 8 bytes of reserved and data
    reference index, then 70 bytes of fields; child boxes follow."""
    info = {}
    if start + 86 > end:
        return info
    info["vendor"] = _fourcc(buf[start + 20:start + 24])
    name_len = min(buf[start + 50], 31)
    info["compressor"] = clean_label(
        buf[start + 51:start + 51 + name_len].decode("utf-8", errors="replace"))
    for typ, s, e in _boxes(buf, start + 86, end):
        if typ == b"colr" and "colr" not in info:
            info["colr"] = _parse_colr(buf[s:e])
        elif typ == b"logs":
            info["logs"] = buf[s:e].split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        elif typ in (b"dvvC", b"dvcC", b"dvwC"):
            info["dolby_vision"] = _parse_dovi(buf[s:e])
    return info


def _parse_trak(buf, start, end):
    track = {}

    def walk(s0, e0, parent, depth):
        for typ, s, e in _boxes(buf, s0, e0):
            if typ == b"hdlr" and parent == b"mdia" and e - s >= 12:
                track["handler"] = _fourcc(buf[s + 8:s + 12])
                track["handler_name"] = _clean_text(buf[s + 24:e])
            elif typ == b"stsd" and "fourcc" not in track and e - s >= 16:
                entry_size, fmt = struct.unpack(">I4s", buf[s + 8:s + 16])
                track["fourcc"] = _fourcc(fmt)
                entry_end = min(s + 8 + entry_size, e)
                if track.get("handler") == "vide":
                    track.update(_parse_video_entry(buf, s + 8, entry_end))
            elif typ in _CONTAINERS:
                if depth >= MAX_BOX_DEPTH:
                    track["error"] = f"containers nest past {MAX_BOX_DEPTH} levels"
                    continue
                walk(s, e, typ, depth + 1)

    walk(start, end, b"trak", 1)
    return track


def read_atoms(path, max_moov=MAX_MOOV_BYTES):
    """Walk the top-level boxes (seeking past mdat) and parse the moov
    sample descriptions. Returns {"ftyp", "moov", "tracks"} plus "error"
    when the file cannot be read as ISO BMFF/QuickTime. Reads headers only."""
    out = {"ftyp": None, "moov": False, "tracks": []}
    try:
        f = open(path, "rb")
    except OSError as e:
        out["error"] = f"cannot open: {e.strerror or e}"
        return out
    with f:
        try:
            size = os.fstat(f.fileno()).st_size
            pos = 0
            while pos + 8 <= size:
                f.seek(pos)
                head = f.read(16)
                if len(head) < 8:
                    break
                box_size, typ = struct.unpack(">I4s", head[:8])
                header = 8
                if box_size == 1:
                    if len(head) < 16:
                        break
                    box_size = struct.unpack(">Q", head[8:16])[0]
                    header = 16
                elif box_size == 0:
                    box_size = size - pos
                if box_size < header:
                    out["error"] = f"bad box size at offset {pos}"
                    break
                if typ == b"ftyp":
                    f.seek(pos + header)
                    body = f.read(min(box_size - header, 256))
                    out["ftyp"] = {
                        "major": _fourcc(body[:4]),
                        "compatible": [_fourcc(body[i:i + 4])
                                       for i in range(8, len(body) - 3, 4)],
                    }
                elif typ == b"moov":
                    out["moov"] = True
                    if box_size > max_moov:
                        out["error"] = f"moov is {box_size} bytes, over the parse cap"
                        break
                    f.seek(pos)
                    buf = f.read(box_size)
                    out["tracks"] = [_parse_trak(buf, s, e)
                                     for t, s, e in _boxes(buf, header, len(buf))
                                     if t == b"trak"]
                    break
                pos += box_size
        except (OSError, struct.error, ValueError, RecursionError) as e:
            out["error"] = f"read error: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Proxy originals and file collection
# ---------------------------------------------------------------------------

def _scan(dirpath, cache):
    """[(name, is_dir)] for one directory, cached so a folder of proxies
    lists each neighbour folder once."""
    if dirpath not in cache:
        entries = []
        try:
            with os.scandir(dirpath) as it:
                for entry in it:
                    try:
                        entries.append((entry.name, entry.is_dir()))
                    except OSError:
                        continue
        except OSError:
            pass
        cache[dirpath] = entries
    return cache[dirpath]


def find_proxy_original(path, cache=None):
    """For a file inside a folder named Proxy, return the relative path of a
    same-basename original in the Proxy folder's parent or in one of that
    parent's other subfolders; otherwise None."""
    cache = {} if cache is None else cache
    here = os.path.dirname(os.path.abspath(path))
    if os.path.basename(here).lower() != "proxy":
        return None
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    base = os.path.dirname(here)

    def match(name):
        root, ext = os.path.splitext(name)
        return (not name.startswith("._") and root.lower() == stem
                and ext.lower() in VIDEO_EXTENSIONS and ext.lower() != ".lrf")

    for name, is_dir in sorted(_scan(base, cache)):
        if not is_dir and match(name):
            return os.path.relpath(os.path.join(base, name), here)
    for name, is_dir in sorted(_scan(base, cache)):
        sub = os.path.join(base, name)
        if not is_dir or name.startswith(".") or os.path.normcase(sub) == os.path.normcase(here):
            continue
        for sub_name, sub_is_dir in sorted(_scan(sub, cache)):
            if not sub_is_dir and match(sub_name):
                return os.path.relpath(os.path.join(sub, sub_name), here)
    return None


def collect_files(paths, extensions=VIDEO_EXTENSIONS):
    """Expand directories recursively into video files, skipping AppleDouble
    (._*) files and hidden or NAS-system folders. Returns (files, missing)."""
    files, missing = [], []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
                for name in sorted(names):
                    if name.startswith("."):
                        continue
                    if os.path.splitext(name)[1].lower() in extensions:
                        files.append(os.path.join(root, name))
        elif os.path.isfile(p):
            files.append(p)
        else:
            missing.append(p)
    return files, missing


def probe(path, exif=None, ffprobe=None, dir_cache=None):
    """Collect every header source for one file into the document that
    classify() reads. Test fixtures use this same shape."""
    data, err = run_ffprobe(path, ffprobe=ffprobe)
    return {
        "path": path,
        "ffprobe": data,
        "ffprobe_error": err,
        "exiftool": exif or {},
        "atoms": read_atoms(path),
        "proxy_original": find_proxy_original(path, dir_cache),
    }


# ---------------------------------------------------------------------------
# Header accessors
# ---------------------------------------------------------------------------

def _fraction(value):
    try:
        frac = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return frac if frac > 0 else None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


class Headers:
    """Read-only view over one probe document with fallbacks between
    ffprobe, exiftool and the parsed atoms."""

    def __init__(self, doc):
        self.path = doc.get("path") or ""
        self.name = os.path.basename(self.path)
        self.ext = os.path.splitext(self.name)[1].lower()
        ff = doc.get("ffprobe") or {}
        self.ffprobe_error = doc.get("ffprobe_error") or ""
        self.format = ff.get("format") or {}
        self.format_tags = self.format.get("tags") or {}
        self.streams = ff.get("streams") or []
        self.exif = doc.get("exiftool") or {}
        self.atoms = doc.get("atoms") or {}
        self.proxy_original = doc.get("proxy_original")
        self._lower_tags = {k.lower(): v for k, v in self.format_tags.items()}
        self.video = self._primary_video()
        self.video_track = next((t for t in self.atoms.get("tracks") or []
                                 if t.get("handler") == "vide"), {})

    def _primary_video(self):
        for s in self.streams:
            if s.get("codec_type") != "video":
                continue
            if (s.get("disposition") or {}).get("attached_pic") == 1:
                continue
            if s.get("codec_name") == "mjpeg" and s.get("codec_tag_string") in ("[0][0][0][0]", None):
                continue  # DJI cover art, even when disposition is absent
            return s
        return {}

    # -- raw lookups --------------------------------------------------------

    def tag(self, key):
        """Format-level tag, case-insensitive, stripped; '' when absent."""
        value = self.format_tags.get(key)
        if value is None:
            value = self._lower_tags.get(key.lower())
        return str(value).strip() if value is not None else ""

    def exif_value(self, *keys):
        """First non-empty exiftool value among exact Group:Tag keys."""
        for key in keys:
            value = self.exif.get(key)
            if value not in (None, ""):
                if isinstance(value, list):
                    return ";".join(str(v) for v in value)
                return str(value).strip()
        return ""

    def exif_values(self, tag_name):
        """Every exiftool value whose tag name (any group) is tag_name."""
        out = []
        for key, value in self.exif.items():
            if key.split(":", 1)[-1] == tag_name:
                out.extend(_as_list(value))
        return out

    # -- derived views ------------------------------------------------------

    def handlers(self):
        names = set()
        for s in self.streams:
            names.add(clean_label((s.get("tags") or {}).get("handler_name", "")))
        for v in self.exif_values("HandlerDescription"):
            names.add(clean_label(v))
        for t in self.atoms.get("tracks") or []:
            names.add(clean_label(t.get("handler_name", "")))
        names.discard("")
        return names

    def compressors(self):
        names = set()
        if self.video:
            names.add(clean_label((self.video.get("tags") or {}).get("encoder", "")))
        names.add(clean_label(self.video_track.get("compressor", "")))
        for v in self.exif_values("CompressorName"):
            names.add(clean_label(v))
        names.discard("")
        return names

    def vendors(self):
        names = set()
        if self.video:
            names.add(clean_label((self.video.get("tags") or {}).get("vendor_id", "")))
        names.add(self.video_track.get("vendor", "").strip("\x00 "))
        for v in self.exif_values("VendorID"):
            m = re.search(r"\((\w{4})\)", v)
            names.add(m.group(1) if m else v.strip())
        names.discard("")
        return names

    def stream_fourccs(self):
        tags = {s.get("codec_tag_string") for s in self.streams}
        tags.update(t.get("fourcc") for t in self.atoms.get("tracks") or [])
        tags.update(self.exif_values("MetaFormat"))
        tags.discard(None)
        return tags

    def compatible_brands(self):
        brands = set()
        ftyp = self.atoms.get("ftyp") or {}
        brands.update(b.strip() for b in ftyp.get("compatible") or [])
        raw = self.tag("compatible_brands")
        brands.update(raw[i:i + 4].strip() for i in range(0, len(raw), 4))
        brands.update(b.strip() for b in _as_list(self.exif.get("QuickTime:CompatibleBrands")))
        brands.discard("")
        return brands

    def video_fourcc(self):
        return (self.video_track.get("fourcc")
                or (self.video.get("codec_tag_string") if self.video else "") or "")

    def colr(self):
        """(primaries, transfer, matrix) codes from the colr atom, or None."""
        c = self.video_track.get("colr") or {}
        if "primaries" not in c:
            return None
        return (c["primaries"], c["transfer"], c["matrix"])

    def transfer(self):
        return (self.video or {}).get("color_transfer") or ""

    def primaries(self):
        return (self.video or {}).get("color_primaries") or ""

    def logs(self):
        return self.video_track.get("logs") or ""

    def dolby_vision(self):
        if self.video_track.get("dolby_vision") is not None:
            return True
        for s in self.streams:
            for sd in s.get("side_data_list") or []:
                if "DOVI" in (sd.get("side_data_type") or ""):
                    return True
        return False

    def dolby_vision_hlg_base(self):
        """True only when the Dolby Vision record names an HLG-compatible
        base layer (bl_signal_compatibility_id 4, as iPhone profile 8.4
        writes). Profiles 5 and 8.1 carry IPT or PQ bases, which are not
        HLG; a record without the id proves nothing."""
        dv = self.video_track.get("dolby_vision") or {}
        if dv.get("compatibility_id") == DOVI_COMPAT_HLG:
            return True
        for s in self.streams:
            for sd in s.get("side_data_list") or []:
                if ("DOVI" in (sd.get("side_data_type") or "")
                        and sd.get("dv_bl_signal_compatibility_id") == DOVI_COMPAT_HLG):
                    return True
        return False

    def is_prores_raw(self):
        return (self.video.get("codec_name") == "prores_raw"
                or self.video_fourcc() in PRORES_RAW_FOURCCS)

    def bit_depth(self):
        v = self.video or {}
        raw = v.get("bits_per_raw_sample")
        if raw and str(raw).isdigit():
            return int(raw)
        pix = v.get("pix_fmt") or ""
        m = re.search(r"p(\d{2})(le|be)$", pix)
        if m:
            return int(m.group(1))
        return 8 if pix.startswith(("yuv420p", "yuvj420p", "yuv422p", "nv12")) else None

    def data_level(self):
        rng = (self.video or {}).get("color_range")
        if rng == "pc":
            return "full"
        if rng == "tv":
            return "video"
        c = self.video_track.get("colr") or {}
        if "full_range" in c:
            return "full" if c["full_range"] else "video"
        return "unknown"

    def vfr(self):
        """'yes' when avg_frame_rate differs from r_frame_rate at all. The
        comparison is exact on purpose: a real iPhone VFR clip (the HLG
        fixture) differs by only 0.018%, so any tolerance wide enough to
        absorb container rounding would also hide true VFR."""
        if not self.video:
            return "unknown"
        r = _fraction(self.video.get("r_frame_rate"))
        a = _fraction(self.video.get("avg_frame_rate"))
        if r is None or a is None:
            return "unknown"
        return "no" if r == a else "yes"

    def is_hlg(self):
        c = self.colr()
        if c is not None:
            return c[1] == 18 or self.dolby_vision_hlg_base()
        return self.transfer() == "arib-std-b67" or self.dolby_vision_hlg_base()

    def is_rec709(self):
        c = self.colr()
        if c is not None:
            return c[0] == 1 and c[1] == 1
        return self.transfer() == "bt709" and self.primaries() in ("bt709", "")

    def codec_summary(self):
        v = self.video or {}
        parts = [v.get("codec_name") or self.video_fourcc() or "no video stream"]
        brand = self.tag("major_brand") or (self.atoms.get("ftyp") or {}).get("major", "")
        if brand.strip():
            parts.append(f"brand {brand.strip()}")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Rules (first match wins)
# ---------------------------------------------------------------------------

def _row(rule, camera, profile, input_cs, confidence, note=""):
    return {"rule": rule, "camera": camera, "profile": profile,
            "input_color_space": input_cs, "confidence": confidence, "note": note}


def _review(rule, camera, note):
    return _row(rule, camera, REVIEW, "", "low", note)


def _join(*parts):
    return "; ".join(p for p in parts if p)


def rule_0a_derived(h):
    encoder = h.tag("encoder")
    lower = h.name.lower()
    if encoder.startswith("Blackmagic Design DaVinci Resolve"):
        what = "DaVinci Resolve render"
    elif encoder.startswith("Lavf"):
        what = "ffmpeg (Lavf) output"
    elif encoder.startswith("HandBrake"):
        what = "HandBrake output"
    elif lower.startswith(("dji_mimo_", "dji_export_")):
        what = "DJI Mimo export"
    else:
        return None
    return _row("0a", what, "derived", "", "high",
                _join("derived file: skip camera assignment",
                      f"encoder {encoder}" if encoder else ""))


def rule_0b_corrupt(h):
    if "moov atom not found" not in h.ffprobe_error.lower():
        return None
    return _row("0b", "unknown", CORRUPT, "", "high",
                "truncated or empty: ffprobe reports moov atom not found")


def rule_0c_proxy(h):
    if h.ext == ".lrf":
        return _row("0c", "DJI low-res proxy (.LRF)", "proxy", "", "high",
                    "camera proxy: link to the same-basename .MP4, do not grade")
    if h.proxy_original:
        return _row("0c", "camera proxy", "proxy", "", "high",
                    f"proxy of {h.proxy_original}: link, do not grade")
    return None


def rule_1_braw(h):
    fourcc = h.video_fourcc()
    if fourcc not in BRAW_FOURCCS and h.ext != ".braw":
        return None
    camera = h.tag("camera_type") or h.exif_value("Keys:CameraType")
    note = []
    if not camera:
        if re.match(r"^[A-Z]\d{3}_\d{8}_[CS]\d{3}\.braw$", h.name, re.I):
            camera = "Blackmagic camera (model not in header)"
        else:
            camera = "Blackmagic RAW (camera not in header)"
    gamma = h.tag("viewing_gamma") or h.exif_value("Keys:ViewingGamma")
    gamut = h.tag("viewing_gamut") or h.exif_value("Keys:ViewingGamut")
    if gamma or gamut:
        note.append(f"recorded viewing {gamma or '?'} / {gamut or '?'}")
    ratio = h.tag("braw_compression_ratio") or h.exif_value("Keys:BrawCompressionRatio")
    if ratio:
        note.append(f"BRAW {ratio}")
    if re.search(r"_S\d{3}\.braw$", h.name, re.I):
        note.append("still frame")
    note.append("decode in Camera RAW")
    return _row("1", camera, "BRAW", CS_CAMERA_RAW, "high", _join(*note))


def rule_2_braw_proxy(h):
    codec = (h.tag("com.blackmagic-design.camera.codec")
             or h.exif_value("Keys:Blackmagic-designCameraCodec"))
    if codec.lower() != "braw":
        return None
    camera = (h.tag("com.blackmagic-design.camera.cameraType")
              or h.exif_value("Keys:Blackmagic-designCameraCameraType")
              or h.tag("com.apple.proapps.modelname")
              or h.exif_value("Keys:AppleProappsModelname")
              or "Blackmagic camera")
    camera = f"{camera} (BRAW proxy)"
    gamma = h.tag("com.apple.proapps.customgamma") or h.exif_value("Keys:AppleProappsCustomgamma")
    science = (h.tag("com.blackmagic-design.camera.colorScience")
               or h.exif_value("Keys:Blackmagic-designCameraColorScience"))
    if gamma == BMD_FILMLOG_TAG and "gen 5" in science.lower():
        return _row("2", camera, "BMD Film", CS_BMD_FILM_GEN5, "medium",
                    "BRAW proxy: log-encoded though tagged Rec.709; gamma from "
                    "customgamma (visually UNVERIFIED); link to the .braw original")
    return _review("2", camera,
                   _join("BRAW proxy: customgamma "
                         f"'{gamma or 'absent'}', colour science '{science or 'absent'}' "
                         "not in the verified table", "link to the .braw original"))


_ATOMOS_PROFILES = {
    ("vlog", "vgamut"): ("V-Log", CS_VLOG),
    ("rec709", "rec709"): ("Rec.709", CS_CAMERA_SDR),
}


def rule_3_atomos(h):
    make = h.tag("make") or h.exif_value("UserData:Make")
    encoder = h.tag("encoder") or h.exif_value("UserData:SoftwareVersion")
    if make != "Atomos" and not encoder.startswith("NinjaV"):
        return None
    model = h.tag("com.apple.proapps.modelname") or h.exif_value("Keys:AppleProappsModelname")
    maker = h.tag("com.atomos.hdr.camera") or h.exif_value("Keys:AtomosHdrCamera")
    if model:
        camera = f"Atomos Ninja V / {model}"
    elif maker:
        camera = f"Atomos Ninja V / {maker} (body model not recorded)"
    else:
        camera = "Atomos Ninja V (camera not recorded)"
    raw = h.is_prores_raw()
    if raw:
        gamma = h.tag("com.atomos.raw.intermediate_oetf") or h.exif_value("Keys:AtomosRawIntermediateOetf")
        gamut = h.tag("com.atomos.raw.intermediate_gamut") or h.exif_value("Keys:AtomosRawIntermediateGamut")
    else:
        gamma = h.tag("com.atomos.hdr.gamma") or h.exif_value("Keys:AtomosHdrGamma")
        gamut = h.tag("com.atomos.hdr.gamut") or h.exif_value("Keys:AtomosHdrGamut")
    if not gamma and not gamut:
        return _review("3", camera,
                       "no com.atomos gamma/gamut tags (no HDMI metadata reached the "
                       "recorder); check the folder or the footage; colr transfer=2 is no log flag")
    hit = _ATOMOS_PROFILES.get((gamma.lower(), gamut.lower()))
    if not hit:
        return _review("3", camera,
                       f"Atomos gamma '{gamma or 'absent'}' / gamut '{gamut or 'absent'}' "
                       "not in the verified table; colr transfer=2 is no log flag")
    profile, cs = hit
    if raw:
        return _row("3", camera, f"{profile} (ProRes RAW)", cs, "medium",
                    f"ProRes RAW: decode with RAW to Log = {profile}; RAW path untested")
    return _row("3", camera, profile, cs, "high",
                "profile from HDMI metadata (com.atomos.hdr)")


_PANA_XML_PROFILES = {
    # CaptureGamma: (profile, colour space, expected CaptureGamut)
    "v-log": ("V-Log", CS_VLOG, "v-gamut"),
    "v-logl": ("V-Log L", CS_VLOG, "v-gamut"),
    "natural": ("Natural", CS_CAMERA_SDR, "bt.709"),
    "cinelike_v": ("Cinelike V", CS_CAMERA_SDR, "bt.709"),
}

_VLOGL_CURVE = "V-Log L uses the V-Log curve"
_VLOGL_GAMUT_GAP = "V-Gamut for V-Log L is UNVERIFIED"

_PANA_PHOTOSTYLES = {
    "v-log": ("V-Log", CS_VLOG),
    "natural": ("Natural", CS_CAMERA_SDR),
    "cinelike v": ("Cinelike V", CS_CAMERA_SDR),
}


def _xml_field(xml, tag):
    m = re.search(r"<(?:[\w-]+:)?%s\b[^>]*>([^<]*)</" % re.escape(tag), xml)
    return m.group(1).strip() if m else ""


def _panasonic_profile(h, model):
    """Return (profile, colour space, confidence, note) or None."""
    xml = (h.tag("com.panasonic.Semi-Pro.metadata.xml")
           or h.exif_value("Keys:PanasonicSemi-ProMetadataXml"))
    if xml:
        gamma = _xml_field(xml, "CaptureGamma")
        gamut = _xml_field(xml, "CaptureGamut")
        hit = _PANA_XML_PROFILES.get(gamma.lower())
        if hit and (not gamut or gamut.lower() == hit[2]):
            codec = _xml_field(xml, "Codec")
            note = _join(f"XML CaptureGamma {gamma} / CaptureGamut {gamut or '?'}",
                         f"codec {codec}" if codec else "")
            if hit[0] == "V-Log L":
                # A CaptureGamut of V-Gamut is the camera's own word on the
                # gamut; only an XML without one leaves it open.
                note = _join(note, _VLOGL_CURVE, "" if gamut else _VLOGL_GAMUT_GAP)
            return hit[0], hit[1], "high", note
        if gamma:
            return None, None, None, f"XML CaptureGamma '{gamma}' / CaptureGamut '{gamut}' not in the verified table"
    style = h.exif_value("Panasonic:PhotoStyle")
    hit = _PANA_PHOTOSTYLES.get(style.lower())
    if hit:
        return hit[0], hit[1], "medium", f"PhotoStyle {style}; no XML"
    if model == "DC-GH5" and style == "Unknown (10)":
        return "V-Log L", CS_VLOG, "medium", _join(
            "GH5 PhotoStyle code 10 = V-Log L; no XML", _VLOGL_CURVE, _VLOGL_GAMUT_GAP)
    if model == "DC-GH5" and style == "Unknown (13)":
        if h.transfer() == "arib-std-b67":
            return "HLG", CS_HLG, "medium", "GH5 PhotoStyle code 13 with VUI arib-std-b67"
        return None, None, None, "GH5 PhotoStyle code 13 without an HLG VUI"
    hlg = h.exif_value("Panasonic:HybridLogGamma")
    if hlg.lower() == "on":
        return "HLG", CS_HLG, "medium", "HybridLogGamma On"
    return None, None, None, f"PhotoStyle '{style or 'absent'}' not in the verified table"


def rule_4_panasonic(h):
    if "pana" not in h.compatible_brands() and h.exif_value("IFD0:Make") != "Panasonic":
        return None
    xml = (h.tag("com.panasonic.Semi-Pro.metadata.xml")
           or h.exif_value("Keys:PanasonicSemi-ProMetadataXml"))
    model = (h.exif_value("Panasonic:Model") or h.exif_value("IFD0:Model")
             or _xml_field(xml, "ModelName"))
    camera = f"Panasonic {model}" if model else "Panasonic (model not in header)"
    profile, cs, confidence, note = _panasonic_profile(h, model)
    if not profile:
        return _review("4", camera, note)
    if h.is_prores_raw():
        return _row("4", camera, f"{profile} (ProRes RAW)", cs, "medium",
                    _join(note, f"ProRes RAW: decode with RAW to Log = {profile}; RAW path untested"))
    return _row("4", camera, profile, cs, confidence, note)


def _iphone_device(model):
    """'Apple iPhone 16 Pro Max 24mm' -> ('iPhone 16 Pro Max', '24mm')."""
    text = re.sub(r"^Apple\s+", "", model.strip())
    m = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?mm)$", text)
    return (m.group(1), m.group(2)) if m else (text, "")


def rule_5_blackmagic_cam(h):
    software = h.tag("com.apple.quicktime.software") or h.exif_value("Keys:Software")
    if not software.startswith("Blackmagic Cam"):
        return None
    model = h.tag("com.apple.quicktime.model") or h.exif_value("Keys:Model")
    device, lens = _iphone_device(model) if model else ("device not in header", "")
    camera = f"{device} (Blackmagic Cam)"
    custom = h.tag("com.apple.proapps.customgamma") or h.exif_value("Keys:AppleProappsCustomgamma")
    lens_note = f"lens {lens}" if lens else ""
    logs = h.logs()
    # Only the Apple Log v1 tag is verified. Apple Log 2 (iPhone 17 Pro,
    # wider gamut) has no clip in the survey, so its tags are unknown.
    if logs and logs != APPLE_LOG_TAG:
        return _review("5", camera, _join(software, f"logs atom '{logs}' not in the verified table"))
    if custom and custom != APPLE_LOG_TAG:
        return _review("5", camera, _join(software, f"customgamma '{custom}' not in the verified table"))
    if logs:
        signal = "logs atom apple-log"
    elif custom:
        signal = "customgamma apple-log"
    elif h.colr() == (9, 2, 9):
        signal = "colr 9/2/9 without a logs atom"
    else:
        signal = ""
    if signal:
        return _row("5", camera, "Apple Log", CS_APPLE_LOG, "high", _join(software, lens_note, signal))
    if h.is_hlg():
        return _review("5", camera, _join(software, "HLG-tagged Blackmagic Cam clip is not in the verified table"))
    if h.is_rec709():
        return _row("5", camera, "Rec.709", CS_CAMERA_SDR, "high", _join(software, lens_note))
    return _review("5", camera, _join(software, f"colour tags {h.colr() or h.transfer() or 'absent'} not in the verified table"))


def rule_6_iphone(h):
    make = h.tag("com.apple.quicktime.make") or h.exif_value("Keys:Make")
    model = h.tag("com.apple.quicktime.model") or h.exif_value("Keys:Model")
    if make != "Apple" or not model:
        return None
    software = h.tag("com.apple.quicktime.software") or h.exif_value("Keys:Software")
    ios = f"iOS {software}" if software else ""
    logs = h.logs()
    if logs == APPLE_LOG_TAG:
        return _row("6", model, "Apple Log", CS_APPLE_LOG, "high", _join(ios, "logs atom apple-log"))
    if logs:
        return _review("6", model, _join(ios, f"logs atom '{logs}' not in the verified table"))
    if h.is_hlg():
        dv = "Dolby Vision (dvvC) on HLG base" if h.dolby_vision() else ""
        return _row("6", model, "HLG", CS_HLG, "high", _join(ios, dv))
    if h.colr() == (9, 2, 9):
        return _review("6", model, _join(ios, "colr 9/2/9 without a logs atom"))
    if h.is_rec709():
        return _row("6", model, "SDR", CS_CAMERA_SDR, "high", ios)
    return _review("6", model, _join(ios, f"colour tags {h.colr() or h.transfer() or 'absent'} not in the verified table"))


_DJI_MODELS = {
    "DJI OsmoPocket3": "DJI Osmo Pocket 3",
    "PP-101": "DJI Osmo Pocket 3",
    "DJI OsmoAction5 Pro": "DJI Osmo Action 5 Pro",
    "AC004": "DJI Osmo Action 5 Pro",
}


def rule_7_dji_osmo(h):
    encoder = h.tag("encoder") or h.exif_value("ItemList:Encoder")
    category = ";".join(_as_list(h.exif.get("Microsoft:Category")))
    m = re.search(r"model_name:([^;]+)", category)
    model_name = m.group(1).strip() if m else ""
    camera = _DJI_MODELS.get(encoder) or _DJI_MODELS.get(model_name)
    if not camera:
        return None
    if h.transfer() == "arib-std-b67" or (h.colr() or (0, 0, 0))[1] == 18:
        return _row("7", camera, "HLG", CS_HLG, "medium", "HLG from VUI arib-std-b67")
    depth = h.bit_depth()
    return _review("7", camera, _join(
        "Normal vs D-Log M is not in the headers (UNRESOLVED)",
        f"{depth}-bit stream" if depth else ""))


def rule_8_dji_drone(h):
    model = h.exif_value("UserData:Model")
    if not re.match(r"^FC\d{4}$", model) or "DJI.AVC" not in h.handlers():
        return None
    camera = f"DJI drone ({model})"
    comment = h.tag("comment") or h.exif_value("ItemList:Comment", "UserData:Comment")
    m = re.search(r"\bType=([\w-]+)", comment)
    kind = m.group(1) if m else ""
    if kind == "Normal":
        # The survey grades this Low: a tag at that grade would be a guess.
        return _review("8", camera, f"Comment Type=Normal suggests {CS_REC709}; "
                                    "survey grade Low, product name UNVERIFIED")
    return _review("8", camera, f"Comment Type '{kind or 'absent'}' not in the verified table")


def rule_9_gopro(h):
    model = h.exif_value("GoPro:Model")
    gopro_handler = any(n.startswith(("GoPro AVC", "GoPro H.265")) for n in h.handlers())
    if not (model or gopro_handler or "gpmd" in h.stream_fourccs()):
        return None
    camera = f"GoPro {model}" if model else "GoPro (model not in header)"
    settings = []
    for tag in ("ColorMode", "Protune"):
        value = h.exif_value(f"GoPro:{tag}")
        if value:
            settings.append(f"{tag}={value}")
    return _review("9", camera, _join(
        "GoPro colour vs Flat to Resolve input is UNVERIFIED",
        "header " + ", ".join(settings) if settings else ""))


def rule_10_obs(h):
    encoder = h.tag("encoder") or h.exif_value("UserData:SoftwareVersion")
    if not (encoder.startswith("OBS Studio") or "OBS Video Handler" in h.handlers()
            or "OBSS" in h.vendors()):
        return None
    return _row("10", "OBS (capture card)", "Rec.709", CS_REC709, "high",
                _join(encoder, "upstream camera not recorded"))


def rule_11_zoom(h):
    if not ("AVC Coding" in h.compressors() and "H.264/AVC video" in h.handlers()
            and re.match(r"^video\d+\.mp4$", h.name, re.I)):
        return None
    return _row("11", "Zoom recording", "Rec.709", CS_REC709, "high",
                "ignore the bt470bg primaries/matrix tags")


def rule_12_screen_recording(h):
    srgb = h.transfer() == "iec61966-2-1" or (h.colr() or (0, 0, 0))[1] == 13
    if not (srgb and h.name.startswith("ScreenRecording_")):
        return None
    return _row("12", "iPhone screen recording", "sRGB", CS_SRGB, "high", "")


_NAME_HINTS = (
    (r"^IMG_\d{4}\.MOV$", "IMG_ name suggests iPhone, but no com.apple.quicktime make/model"),
    (r"^P1\d{6}\.(MOV|MP4)$", "P1 name suggests some Lumix (name only)"),
    (r"^A\d{3}_\d{8}_C\d{3}\.mov$", "A001_ name suggests Blackmagic Cam (name only)"),
    (r"^DJI_\d{14}_\d{4}_D\.MP4$", "name suggests DJI Pocket 3 or Action 5 Pro (cannot tell which)"),
    (r"^DJI_\d{4}\.MP4$", "name suggests an older DJI drone"),
    (r"^G[HX]\d{6}\.MP4$", "name suggests GoPro"),
    (r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}\.(mov|mkv|mp4)$", "name suggests OBS (renames are common)"),
    (r"^.+_\d{8}\.mov$", "name suggests a Resolve render"),
    (r"^ScreenRecording_", "iPhone screen recording by name; rule 12 pins sRGB-tagged ones only"),
)


def rule_13_unknown(h):
    hints = []
    for pattern, hint in _NAME_HINTS:
        if re.match(pattern, h.name, re.I):
            hints.append(hint)
            break
    if h.ffprobe_error and not h.video:
        hints.append("ffprobe: " + h.ffprobe_error.splitlines()[-1][:120])
    else:
        hints.append(h.codec_summary())
    handlers = sorted(n for n in h.handlers() if not n.startswith("Core Media"))
    if handlers:
        hints.append("handler " + handlers[0])
    return _review("13", "unknown", _join(*hints))


RULES = (
    rule_0a_derived, rule_0b_corrupt, rule_0c_proxy, rule_1_braw,
    rule_2_braw_proxy, rule_3_atomos, rule_4_panasonic, rule_5_blackmagic_cam,
    rule_6_iphone, rule_7_dji_osmo, rule_8_dji_drone, rule_9_gopro,
    rule_10_obs, rule_11_zoom, rule_12_screen_recording, rule_13_unknown,
)


def classify(doc):
    """Classify one probe document. Returns a dict keyed by COLUMNS."""
    h = Headers(doc)
    for rule in RULES:
        row = rule(h)
        if row:
            break
    if row["profile"] == CORRUPT:
        data_level, vfr = "unknown", "unknown"
    else:
        data_level, vfr = h.data_level(), h.vfr()
    if row["rule"] == "10" and data_level == "unknown":
        data_level = "video"  # OBS records partial range (basic.ini)
    if vfr == "yes" and row["profile"] not in (REVIEW, CORRUPT, "derived", "proxy"):
        row["note"] = _join(row["note"], "VFR: conform to CFR before editing")
    out = {"path": h.path, "data_level": data_level, "vfr": vfr}
    out.update(row)
    return {c: out.get(c, "") for c in COLUMNS}


def needs_review(row):
    """True for review and corrupt rows, and for any row pinned at low
    confidence. No rule pins at low today; this keeps a future one from
    passing a guess through with exit status 0."""
    return row.get("profile") in (REVIEW, CORRUPT) or row.get("confidence") == "low"


# ---------------------------------------------------------------------------
# Batch entry point and output
# ---------------------------------------------------------------------------

def failed_row(path, exc):
    """Rule-13 review row for a file whose probe or classification raised."""
    out = {"path": path, "data_level": "unknown", "vfr": "unknown"}
    out.update(_review("13", "unknown",
                       f"detect failed on this file: {type(exc).__name__}: {str(exc)[:160]}"))
    return {c: out.get(c, "") for c in COLUMNS}


def detect_paths(paths, ffprobe=None, exiftool=None):
    """Probe and classify every video file under paths. Returns
    (rows, missing) where missing lists inputs that do not exist. A file
    that raises becomes a review row, so one bad file never discards the
    batch; a missing tool still stops the run."""
    ffprobe = ffprobe or FFPROBE
    exiftool = exiftool or EXIFTOOL
    check_tools(ffprobe, exiftool)
    files, missing = collect_files(paths)
    exif = run_exiftool(files, exiftool=exiftool) if files else {}
    cache = {}
    rows = []
    for f in files:
        try:
            rows.append(classify(probe(f, exif.get(f), ffprobe=ffprobe, dir_cache=cache)))
        except ToolMissing:
            raise
        except Exception as e:
            rows.append(failed_row(f, e))
    return rows, missing


def _cell(value):
    return re.sub(r"[\t\r\n]+", " ", str(value if value is not None else ""))


def format_tsv(rows):
    lines = ["\t".join(COLUMNS)]
    lines.extend("\t".join(_cell(r.get(c, "")) for c in COLUMNS) for r in rows)
    return "\n".join(lines) + "\n"


def format_json(rows):
    return json.dumps([{c: r.get(c, "") for c in COLUMNS} for r in rows], indent=2) + "\n"


def summarize(rows):
    corrupt = sum(1 for r in rows if r["profile"] == CORRUPT)
    review = sum(1 for r in rows if needs_review(r)) - corrupt
    pinned = len(rows) - review - corrupt
    return f"detect: {len(rows)} file(s): {pinned} pinned, {review} review, {corrupt} corrupt"
