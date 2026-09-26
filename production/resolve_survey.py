#!/opt/homebrew/bin/python3.14
"""
resolve_survey.py: read-only survey of every DaVinci Resolve project in the
local disk database. It decodes timelines, grades (node graphs, labels,
LUTs, OFX), color science fields and media roots straight from each
project's Project.db, with no Resolve API call and no write anywhere near
Resolve's files.

Usage:
    resolve_survey.py --out /path/outside/this/repo/survey.md [--json survey.json]
                      [--projects NAME ...] [--projects-dir DIR]
                      [--metadata-cache PATH | --no-metadata-cache]

    resolve_workflow.py survey ... runs this script with the interpreter in
    the shebang (stdlib only, but it needs compression.zstd, so Python 3.14
    or newer; the system /usr/bin/python3 is 3.9 and lacks it).

    --out is required and must sit outside this repository's git working
    trees: the report names projects, media paths and LUT files, and this
    repo is public.

How the databases are read:
    Resolve may be saving a Project.db while the survey runs, so no live
    file is ever read directly. Each DB is opened with a "file:...?mode=ro"
    URI (timeout 5 s) and copied with SQLite's online backup API into a
    temporary file; every query then runs against that copy, and the copy
    is deleted afterwards. immutable=1 is never used on a live file, since
    it disables locking and allows torn reads while Resolve writes.

Blob formats (Resolve 21, disk database):
    0x81 + zstd frame       zstd-compressed protobuf (grade bodies)
    0x80 + protobuf         uncompressed protobuf (rare, older projects)
    "GRF ..."               legacy text grade (gallery stills, old projects)
    FieldsBlob frame        BE uint32 tag (1 or 2) + BE uint32 length, then
                            a 0x81/0x80 blob of that length
    Qt QVariantMap          BE uint32 version (1) + BE uint32 count, then
                            per entry a UTF-16BE key and a typed QVariant

Where each surveyed field lives:
    Timelines   Sm2Timeline.Sequence -> Sm2Sequence. Resolution is two BE
                int64 (0,0 = use the project setting); FrameRate starts
                with an LE double.
    Tracks      Sm2TiItem.Sm2TiTrack_id -> Sm2TiTrack (Type 0 video,
                1 audio, 2 subtitle; Sequence = owning Sm2Sequence).
    Grades      ListMgt::LmVersionTable.pActive -> ListMgt::LmVersion
                (HasCorrection, VerType, Body). The table's owner column
                says what is graded: Sm2TiItem_id = a clip on a timeline,
                Sm2MpMedia_id = a media-pool clip, Sm2Sequence_id = a
                timeline grade.
    Node graph  grade body field 1. Inside it, 7[] are nodes (1 id,
                6 label, 8 type: 44 corrector, 68 mixer; 9 correction
                params, which hold LUT path strings; 10 OFX stack) and
                8[] are edges (1 source id, 3 destination id, 4 input
                index). The API's Graph.GetNumNodes() equals the number
                of 7[] nodes. Top-level field 3 (one type-84 node per
                clip) is not a graph node and is ignored.
    OFX         inside node field 10, a message whose field 3 is an
                OfxImageEffectContext* name carries the plugin id in
                field 2 (params in 5[] as {1 name, 2 value}).
    Media       BtVideoInfo.Clip (FieldsBlob) -> protobuf 1 directory,
                2 filename, 5 codec fourcc (absent for stills).
                BtVideoInfo.Geometry is a QVariantMap whose "Resolution"
                QByteArray holds two BE int64.
    Settings    SM_Config.FieldsBlob -> QVariantMap "SetupBA" -> FieldsBlob
                -> protobuf with numbered fields only. Resolve omits fields
                left at their default. Decoded: 1 timeline resolution label
                (absent = 1920 x 1080), 248 timeline frame rate as float32
                (absent = 24), 46 color science mode (absent = davinciYRGB,
                2 = davinciYRGBColorManagedv2), 203 input color space
                (14 = Rec.709 Gamma 2.4). 23/26/122/190/225 appear once a
                project's color management settings have been touched.
    Cache       ProjectMetadataCache/Metadata.db::project_metadata holds
                Resolve's own width/height/fps per project. It is used only
                as a cross-check and can be stale for the open project.

Evidence behind the decodes (checked against the live API on two projects,
2026-09-26): timeline counts, resolutions and frame rates, graded-clip
counts, node counts and node labels matched. Color science names rest on
those two projects only, so the absent-field-46 mapping is reported as
inferred. Anything that fails to decode prints as UNDECODED.

Tests (offline, synthetic blobs only):
    /opt/homebrew/bin/python3.14 -m unittest discover production/tests
    Under /usr/bin/python3 (3.9) the survey tests skip themselves.
"""

import argparse
import json
import math
import os
import re
import sqlite3
import statistics
import struct
import sys
import tempfile
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

try:
    from compression import zstd
except ImportError:  # Python < 3.14
    zstd = None

UNDECODED = "UNDECODED"

RESOLVE_LIBRARY = (Path.home() / "Library" / "Application Support" / "Blackmagic Design"
                   / "DaVinci Resolve" / "Resolve Project Library" / "Resolve Projects"
                   / "Users" / "guest")
PROJECTS_DIR_DEFAULT = RESOLVE_LIBRARY / "Projects"
METADATA_CACHE_DEFAULT = RESOLVE_LIBRARY / "ProjectMetadataCache" / "Metadata.db"

SNAPSHOT_TIMEOUT_S = 5

# Labels that mark the house node tree when a grade carries all of them.
HOUSE_TREE_LABELS = ("CST IN", "EXP", "WB", "CON", "HSV SAT", "CST OUT")

LUT_EXTENSIONS = (".cube", ".3dl", ".dctl", ".mga", ".m3d", ".ilut", ".olut", ".csp")
OFX_CONTEXT_RE = re.compile(rb"^OfxImageEffectContext[A-Za-z]+$")
SINGLE_PROJECT_SEQUENCES_SHOWN = 10
NODE_TYPES = {44: "corrector", 68: "mixer"}
TRACK_TYPES = {0: "video", 1: "audio", 2: "subtitle"}
OFX_VENDOR_PREFIX = "com.blackmagicdesign."

# SetupBA numbered fields (see the module docstring for the evidence).
SETUP_RESOLUTION_LABEL = 1
SETUP_COLOR_SCIENCE = 46
SETUP_INPUT_COLOR_SPACE = 203
SETUP_FRAME_RATE = 248
SETUP_OUTPUT_TRANSFORM = 122
SETUP_COLOR_MANAGEMENT_FIELDS = (23, 26, 122, 190, 225)
DEFAULT_PROJECT_RESOLUTION = (1920, 1080)
DEFAULT_PROJECT_FPS = 24.0
COLOR_SCIENCE_CODES = {None: "davinciYRGB", 2: "davinciYRGBColorManagedv2"}
INPUT_COLOR_SPACE_CODES = {14: "Rec.709 Gamma 2.4"}

WIRE_VARINT, WIRE_FIXED64, WIRE_LEN, WIRE_FIXED32 = 0, 1, 2, 5
MAX_WALK_DEPTH = 24

QT_TYPES_FIXED = {1: ">?", 2: ">i", 3: ">I", 4: ">q", 5: ">Q", 6: ">d"}
QT_STRING, QT_BYTEARRAY = 10, 12
QT_NULL_LENGTH = 0xFFFFFFFF


class DecodeError(ValueError):
    """A blob did not have the shape this survey knows how to read."""


def _as_bytes(value, what="blob"):
    """value as bytes when SQLite handed back a BLOB (or a slice of one).
    A NULL, TEXT, INTEGER or REAL value raises DecodeError: bytes() of an
    int allocates that many zero bytes, and bytes() of a str raises
    TypeError, so no decoder may see one."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if value is None:
        raise DecodeError(f"{what} is NULL")
    raise DecodeError(f"{what} stored as {type(value).__name__}, expected a BLOB")


# ---------------------------------------------------------------------------
# Snapshot (the only code that touches a live database file)
# ---------------------------------------------------------------------------

def snapshot_db(live_path, dest_path, timeout=SNAPSHOT_TIMEOUT_S):
    """Copy a live SQLite database to dest_path with the online backup API.
    The source is opened read-only through a mode=ro URI, never with
    immutable=1, so SQLite's locking protects the copy from a concurrent
    Resolve save. A read transaction is opened first: it waits at most
    `timeout` seconds for a writer and then raises sqlite3.OperationalError,
    and the backup runs under that shared lock. (Connection.backup on its
    own retries a busy database forever.) Returns dest_path."""
    uri = "file:" + urllib.parse.quote(os.path.abspath(live_path)) + "?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
    try:
        src.execute("BEGIN")
        src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
        src.execute("COMMIT")
    finally:
        src.close()
    return dest_path


# ---------------------------------------------------------------------------
# Protobuf (schema-less) parsing
# ---------------------------------------------------------------------------

def read_varint(buf, pos):
    """Decode a base-128 varint at buf[pos]. Returns (value, next_pos)."""
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise DecodeError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise DecodeError("varint longer than 10 bytes")


def parse_message(buf):
    """Parse one protobuf message without a schema into a list of
    (field_number, wire_type, value). Varints come back as int, fixed64 and
    fixed32 as raw bytes, length-delimited values as bytes. Group wire
    types, field number 0, any overrun and a buf that is not bytes-like
    raise DecodeError."""
    buf = _as_bytes(buf, "protobuf message")
    pos, out = 0, []
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise DecodeError("field number 0")
        if wire == WIRE_VARINT:
            value, pos = read_varint(buf, pos)
        elif wire == WIRE_FIXED64:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == WIRE_FIXED32:
            value, pos = buf[pos:pos + 4], pos + 4
        elif wire == WIRE_LEN:
            length, pos = read_varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        else:
            raise DecodeError(f"unsupported wire type {wire}")
        if pos > len(buf):
            raise DecodeError("field overruns message")
        out.append((field, wire, value))
    return out


def try_parse_message(buf):
    """parse_message, or None when buf is not a well-formed message."""
    try:
        return parse_message(buf)
    except DecodeError:
        return None


def first_fields(fields):
    """{field_number: value} keeping the first occurrence of each field."""
    out = {}
    for field, _, value in fields:
        out.setdefault(field, value)
    return out


def iter_messages(buf, depth=0, max_depth=MAX_WALK_DEPTH):
    """Yield the parsed field list of buf and of every length-delimited
    value nested inside it that also parses as a message, depth first.
    Text values sometimes parse as messages by accident; callers match on
    exact shapes, so the extra candidates are harmless."""
    fields = try_parse_message(buf)
    if not fields:
        return
    yield fields
    if depth >= max_depth:
        return
    for _, wire, value in fields:
        if wire == WIRE_LEN and len(value) >= 2:
            yield from iter_messages(value, depth + 1, max_depth)


def _as_text(value):
    """value decoded as printable UTF-8 text, or None."""
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if text and text.isprintable() else None


# ---------------------------------------------------------------------------
# Blob framing
# ---------------------------------------------------------------------------

def unwrap_blob(blob):
    """Strip Resolve's one-byte blob prefix: 0x81 = zstd-compressed payload,
    0x80 = raw payload. Anything else raises DecodeError."""
    blob = _as_bytes(blob)
    if not blob:
        raise DecodeError("empty blob")
    prefix = blob[0]
    if prefix == 0x81:
        if zstd is None:
            raise DecodeError("compression.zstd unavailable (needs Python 3.14+)")
        try:
            return zstd.decompress(bytes(blob[1:]))
        except zstd.ZstdError as e:
            raise DecodeError(f"zstd: {e}")
    if prefix == 0x80:
        return bytes(blob[1:])
    raise DecodeError(f"unknown blob prefix 0x{prefix:02x}")


def unwrap_fields_blob(blob):
    """Unwrap the FieldsBlob frame: BE uint32 tag (1 or 2), BE uint32
    payload length, then a 0x81/0x80 payload. Returns the protobuf bytes."""
    blob = _as_bytes(blob, "FieldsBlob")
    if len(blob) < 8:
        raise DecodeError("FieldsBlob shorter than its 8-byte header")
    tag, length = struct.unpack_from(">II", blob, 0)
    if tag not in (1, 2):
        raise DecodeError(f"FieldsBlob tag {tag}")
    if len(blob) < 8 + length:
        raise DecodeError("FieldsBlob payload truncated")
    return unwrap_blob(blob[8:8 + length])


def parse_qvariant_map(blob):
    """Decode a Qt QDataStream QVariantMap as Resolve writes it: BE uint32
    version (1), BE uint32 entry count, then per entry a QString key
    (BE uint32 byte length + UTF-16BE) and a QVariant (BE uint32 type id,
    one is-null byte, value). Supports bool, int, uint, qlonglong,
    qulonglong, double, QString and QByteArray; any other type stops the
    parse with DecodeError, since its size is unknown."""
    blob = _as_bytes(blob, "QVariantMap")
    if len(blob) < 8:
        raise DecodeError("QVariantMap shorter than its header")
    version, count = struct.unpack_from(">II", blob, 0)
    if version != 1:
        raise DecodeError(f"QVariantMap version {version}")
    pos, out = 8, {}
    try:
        for _ in range(count):
            (key_len,), pos = struct.unpack_from(">I", blob, pos), pos + 4
            key = bytes(blob[pos:pos + key_len]).decode("utf-16-be")
            pos += key_len
            (qtype,), pos = struct.unpack_from(">I", blob, pos), pos + 4
            pos += 1  # is-null flag
            if qtype in (QT_STRING, QT_BYTEARRAY):
                (length,), pos = struct.unpack_from(">I", blob, pos), pos + 4
                if length == QT_NULL_LENGTH:
                    value = None
                else:
                    value = bytes(blob[pos:pos + length])
                    if len(value) != length:
                        raise DecodeError(f"QVariantMap value for {key!r} truncated")
                    pos += length
                    if qtype == QT_STRING:
                        value = value.decode("utf-16-be")
            elif qtype in QT_TYPES_FIXED:
                fmt = QT_TYPES_FIXED[qtype]
                (value,) = struct.unpack_from(fmt, blob, pos)
                pos += struct.calcsize(fmt)
            else:
                raise DecodeError(f"QVariant type {qtype} for key {key!r}")
            out[key] = value
    except (struct.error, UnicodeDecodeError) as e:
        raise DecodeError(f"QVariantMap: {e}")
    return out


# ---------------------------------------------------------------------------
# Field decoders
# ---------------------------------------------------------------------------

def decode_be_resolution(blob):
    """Two BE int64 (width, height). Returns (w, h), or None for (0, 0),
    which on a timeline means "use the project setting"."""
    blob = _as_bytes(blob, "resolution")
    if len(blob) != 16:
        raise DecodeError(f"resolution blob of {len(blob)} bytes (expected 16)")
    width, height = struct.unpack(">qq", blob)
    if (width, height) == (0, 0):
        return None
    if width <= 0 or height <= 0:
        raise DecodeError(f"resolution {width}x{height}")
    return (width, height)


def decode_frame_rate(blob):
    """Frame rate stored as an LE double in the blob's first 8 bytes."""
    blob = _as_bytes(blob, "frame rate")
    if len(blob) < 8:
        raise DecodeError("frame-rate blob shorter than 8 bytes")
    (fps,) = struct.unpack_from("<d", blob, 0)
    if not math.isfinite(fps) or fps <= 0 or fps > 1000:
        raise DecodeError(f"frame rate {fps!r}")
    return fps


def _unwrap_value_text(value):
    """Resolve stores a string parameter as the value message {5: text}.
    When the text is 32..126 bytes long that message (b'*' + one length
    byte + text) is itself printable, so it would read as text. Returns
    the inner text for exactly that shape, else None."""
    fields = try_parse_message(value) if value[:1] == b"*" else None
    if fields and len(fields) == 1 and fields[0][:2] == (5, WIRE_LEN):
        return _as_text(fields[0][2])
    return None


def _iter_leaf_texts(buf, depth=0):
    """Yield printable text values nested in a protobuf message. Printable
    text is a leaf: it is never re-parsed as a message (a path such as
    "R+Looks/x.cube" happens to parse as field 10), except to unwrap the
    {5: text} value message. Binary values are walked as submessages."""
    fields = try_parse_message(buf)
    if not fields:
        return
    for _, wire, value in fields:
        if wire != WIRE_LEN:
            continue
        text = _as_text(value)
        if text is not None:
            yield _unwrap_value_text(value) or text
        elif depth < MAX_WALK_DEPTH and len(value) >= 2:
            yield from _iter_leaf_texts(value, depth + 1)


def extract_lut_paths(params):
    """LUT paths set on a node, read from its correction params (node
    field 9): the nested text values ending in a LUT extension, each
    listed once."""
    paths = []
    for text in _iter_leaf_texts(params):
        if text.lower().endswith(LUT_EXTENSIONS) and text not in paths:
            paths.append(text)
    return paths


def extract_ofx_plugins(ofx_stack):
    """Plugin ids in a node's OFX stack (node field 10), one entry per
    plugin instance, in stack order."""
    plugins = []
    for fields in iter_messages(ofx_stack):
        top = first_fields(fields)
        context, plugin = top.get(3), top.get(2)
        if isinstance(context, bytes) and OFX_CONTEXT_RE.match(context) and isinstance(plugin, bytes):
            text = _as_text(plugin)
            if text:
                plugins.append(text)
    return plugins


def graph_order(nodes, edges):
    """Node ids in signal-flow order: a topological sort over the edges,
    breaking ties by node id. The live API's node indexes follow this order
    (checked on one project). Nodes left over by a cycle are appended by
    id."""
    ids = [n["id"] for n in nodes]
    known = set(ids)
    indegree = {i: 0 for i in ids}
    successors = defaultdict(list)
    for src, dst, _ in edges:
        if src in known and dst in known and src != dst:
            successors[src].append(dst)
            indegree[dst] += 1
    ready = sorted(i for i in ids if indegree[i] == 0)
    order = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for nxt in successors[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    seen = set(order)
    order.extend(sorted(i for i in ids if i not in seen))
    return order


def _first_with_wire(fields):
    """{field_number: (wire, value)} keeping the first occurrence of each."""
    out = {}
    for field, wire, value in fields:
        out.setdefault(field, (wire, value))
    return out


def _graph_field(fields, num, wire, what, problems):
    """A node or edge field read with its expected wire type: None when
    absent, UNDECODED (with the problem noted) when present with another
    wire type. Absent and malformed stay distinct, as in
    interpret_project_settings."""
    if num not in fields:
        return None
    got, value = fields[num]
    if got != wire:
        problems.append(f"{what} (field {num}) with wire type {got}")
        return UNDECODED
    return value


def _node_payload(node, num, what, extract, problems):
    """extract(value) for a length-delimited node field, or [] when the
    field is absent. A field present with another wire type holds no
    payload this survey can read: it returns None (UNDECODED) and notes
    the problem."""
    value = _graph_field(node, num, WIRE_LEN, f"node {what}", problems)
    if value is None:
        return []
    if value is UNDECODED:
        return None
    return extract(value)


def decode_grade_body(body):
    """Decode a ListMgt::LmVersion.Body. Returns a dict:
        kind      "graph" or "legacy" (the old "GRF" text format)
        nodes     [{id, type, type_name, label, luts, ofx}] in signal-flow
                  order. label is "" only when field 6 is absent;
                  label and type_name are UNDECODED, and luts or ofx
                  None, when that field is present with another wire type
        edges     [(source id, destination id, input index)]
        problems  node and edge fields present with a wire type this
                  survey cannot read, one entry per field
    Raises DecodeError when the body cannot be read."""
    body = _as_bytes(body, "grade body")
    if body[:3] == b"GRF":
        return {"kind": "legacy", "nodes": [], "edges": [], "problems": []}
    top = first_fields(parse_message(unwrap_blob(body)))
    graph = top.get(1)
    if not isinstance(graph, bytes):
        raise DecodeError("grade body has no node graph (field 1)")
    nodes, edges, problems = [], [], []
    for field, wire, value in parse_message(graph):
        if wire != WIRE_LEN:
            continue
        if field == 7:
            node = _first_with_wire(parse_message(value))
            node_id = _graph_field(node, 1, WIRE_VARINT, "node id", problems)
            node_type = _graph_field(node, 8, WIRE_VARINT, "node type", problems)
            label = _graph_field(node, 6, WIRE_LEN, "node label", problems)
            if isinstance(label, bytes):
                label = label.decode("utf-8", "replace")
            nodes.append({
                "id": node_id if isinstance(node_id, int) else None,
                "type": node_type if isinstance(node_type, int) else None,
                "type_name": (UNDECODED if node_type is UNDECODED
                              else NODE_TYPES.get(node_type, f"type {node_type}")),
                "label": "" if label is None else label,
                "luts": _node_payload(node, 9, "LUT params", extract_lut_paths, problems),
                "ofx": _node_payload(node, 10, "OFX stack", extract_ofx_plugins, problems),
            })
        elif field == 8:
            edge = _first_with_wire(parse_message(value))
            src, dst, index = (_graph_field(edge, num, WIRE_VARINT, f"edge {what}", problems)
                               for num, what in ((1, "source"), (3, "destination"),
                                                 (4, "input index")))
            edges.append((src, dst, 0 if index is None else index))
    by_id = {n["id"]: n for n in nodes}
    if len(by_id) == len(nodes) and None not in by_id:
        nodes = [by_id[i] for i in graph_order(nodes, edges)]
    return {"kind": "graph", "nodes": nodes, "edges": edges, "problems": problems}


def decode_media_clip(blob):
    """BtVideoInfo.Clip -> (directory, filename, fourcc or None)."""
    fields = first_fields(parse_message(unwrap_fields_blob(blob)))
    directory, filename, fourcc = fields.get(1), fields.get(2), fields.get(5)
    if not isinstance(directory, bytes) or not isinstance(filename, bytes):
        raise DecodeError("clip blob has no directory/filename")
    return (directory.decode("utf-8", "replace"), filename.decode("utf-8", "replace"),
            fourcc.decode("utf-8", "replace") if isinstance(fourcc, bytes) else None)


def decode_media_geometry(blob):
    """BtVideoInfo.Geometry -> (w, h) from its "Resolution" entry."""
    value = parse_qvariant_map(blob).get("Resolution")
    if not isinstance(value, bytes):
        raise DecodeError("geometry has no Resolution entry")
    res = decode_be_resolution(value)
    if res is None:
        raise DecodeError("geometry resolution 0x0")
    return res


def media_root(directory, home=None):
    """Where a clip lives, cut to its storage root: the first component
    under the home directory ("~/Desktop"), otherwise the first two path
    components (a mounted volume, for example)."""
    if not directory:
        return UNDECODED
    home = str(home or Path.home()).rstrip("/")
    path = os.path.normpath(directory)
    if path == home or path.startswith(home + "/"):
        rest = [p for p in path[len(home):].split("/") if p]
        return "~/" + rest[0] if rest else "~"
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join(parts[:2]) if parts else "/"


def decode_setup_fields(config_fields_blob):
    """SM_Config.FieldsBlob -> {field number: [(wire, value), ...]} from
    the numbered protobuf fields in its "SetupBA" entry."""
    setup = parse_qvariant_map(config_fields_blob).get("SetupBA")
    if not isinstance(setup, bytes):
        raise DecodeError("SM_Config has no SetupBA entry")
    fields = defaultdict(list)
    for field, wire, value in parse_message(unwrap_fields_blob(setup)):
        fields[field].append((wire, value))
    return dict(fields)


def interpret_project_settings(fields):
    """Turn raw SetupBA fields into the surveyed project settings. Every
    value carries the evidence it rests on; a value this survey cannot map
    is UNDECODED with the raw field quoted. A field that is present with a
    wire type other than the expected one is UNDECODED, never read as
    absent (absent means the Resolve default)."""
    def first(num, wire):
        """(value, None) for the first occurrence with the expected wire
        type, (None, None) when the field is absent, and (None, problem)
        when it is present only with other wire types."""
        entries = fields.get(num, ())
        for w, v in entries:
            if w == wire:
                return v, None
        if entries:
            return None, f"field {num} present with wire type {entries[0][0]}"
        return None, None

    out = {"defaulted": []}  # settings printed from an absent field's default
    label, problem = first(SETUP_RESOLUTION_LABEL, WIRE_LEN)
    if problem:
        out["resolution"], out["resolution_evidence"] = UNDECODED, problem
    elif label is None:
        out["resolution"] = "%dx%d" % DEFAULT_PROJECT_RESOLUTION
        out["resolution_evidence"] = "field 1 absent (Resolve default)"
        out["defaulted"].append("resolution")
    else:
        text = label.decode("utf-8", "replace")
        match = re.match(r"\s*(\d+)\s*x\s*(\d+)", text)
        out["resolution"] = f"{match.group(1)}x{match.group(2)}" if match else UNDECODED
        out["resolution_evidence"] = f"field 1 = {text!r}"

    raw_fps, problem = first(SETUP_FRAME_RATE, WIRE_FIXED32)
    if problem:
        out["fps"], out["fps_evidence"] = UNDECODED, problem
    elif raw_fps is None:
        out["fps"] = DEFAULT_PROJECT_FPS
        out["fps_evidence"] = "field 248 absent (default 24)"
        out["defaulted"].append("fps")
    else:
        fps = struct.unpack("<f", raw_fps)[0]
        out["fps"] = round(fps, 3) if math.isfinite(fps) and fps > 0 else UNDECODED
        out["fps_evidence"] = "field 248 (float32)"

    mode, problem = first(SETUP_COLOR_SCIENCE, WIRE_VARINT)
    if problem:
        out["color_science"], out["color_science_evidence"] = UNDECODED, problem
    elif mode in COLOR_SCIENCE_CODES:
        out["color_science"] = COLOR_SCIENCE_CODES[mode]
        out["color_science_evidence"] = ("field 46 absent (inferred)" if mode is None
                                         else f"field 46 = {mode}")
    else:
        out["color_science"] = UNDECODED
        out["color_science_evidence"] = f"field 46 = {mode}"

    space, problem = first(SETUP_INPUT_COLOR_SPACE, WIRE_VARINT)
    out["input_color_space"] = INPUT_COLOR_SPACE_CODES.get(space, UNDECODED)
    out["input_color_space_evidence"] = problem or ("field 203 absent" if space is None
                                                    else f"field 203 = {space}")

    transform, _ = first(SETUP_OUTPUT_TRANSFORM, WIRE_LEN)
    out["output_transform_label"] = transform.decode("utf-8", "replace") if transform else None
    out["color_management_fields"] = [f for f in SETUP_COLOR_MANAGEMENT_FIELDS if f in fields]
    return out


# ---------------------------------------------------------------------------
# Per-project survey (runs against a snapshot)
# ---------------------------------------------------------------------------

ACTIVE_GRADES_SQL = '''
    select t.Sm2TiItem_id, t.Sm2MpMedia_id, t.Sm2Sequence_id, v.VerType, v.Body
    from "ListMgt::LmVersionTable" t
    join "ListMgt::LmVersion" v
      on v."ListMgt::LmVersion_id" = t.pActive
     and v."ListMgt::LmVersionTable_id" = t."ListMgt::LmVersionTable_id"
    where v.HasCorrection = 1
'''

TIMELINES_SQL = '''
    select t.Name, s.Sm2Sequence_id, s.Resolution, s.FrameRate
    from Sm2Timeline t join Sm2Sequence s on s.Sm2Sequence_id = t.Sequence
    order by t.Name
'''

TRACK_ITEMS_SQL = '''
    select tr.Sequence, tr.Type, count(*)
    from Sm2TiItem i join Sm2TiTrack tr on tr.Sm2TiTrack_id = i.Sm2TiTrack_id
    group by tr.Sequence, tr.Type
'''


def grade_owner(item_id, media_id, sequence_id):
    if item_id:
        return "clip"
    if media_id:
        return "pool"
    if sequence_id:
        return "timeline"
    return "other"


def survey_timelines(con, errors):
    items = defaultdict(Counter)
    for seq_id, track_type, count in con.execute(TRACK_ITEMS_SQL):
        items[seq_id][TRACK_TYPES.get(track_type, f"type {track_type}")] += count
    timelines = []
    for name, seq_id, res_blob, fps_blob in con.execute(TIMELINES_SQL):
        if not isinstance(name, str):
            errors.append(f"timeline name stored as {type(name).__name__}, read as {UNDECODED}")
            name = UNDECODED
        try:
            res = decode_be_resolution(res_blob)
            resolution = "project" if res is None else "%dx%d" % res
        except DecodeError as e:
            resolution = UNDECODED
            errors.append(f"timeline {name!r} resolution: {e}")
        try:
            fps = round(decode_frame_rate(fps_blob), 3)
        except DecodeError as e:
            fps = UNDECODED
            errors.append(f"timeline {name!r} frame rate: {e}")
        timelines.append({"name": name, "resolution": resolution, "fps": fps,
                          "items": dict(items.get(seq_id, {}))})
    return timelines


def survey_grades(con, errors):
    counts = Counter()
    node_counts = []
    node_counts_by_owner = defaultdict(list)
    node_types = Counter()
    luts, ofx, sequences = Counter(), Counter(), Counter()
    grades = []
    undecoded, problems = Counter(), Counter()
    undecoded_lut_nodes = undecoded_ofx_nodes = 0
    for item_id, media_id, seq_id, _vertype, body in con.execute(ACTIVE_GRADES_SQL):
        owner = grade_owner(item_id, media_id, seq_id)
        counts[owner] += 1
        try:
            grade = decode_grade_body(body)
        except DecodeError as e:
            undecoded[str(e)] += 1
            continue
        except Exception as e:  # one malformed row must not end the survey
            undecoded[f"{type(e).__name__}: {e}"] += 1
            continue
        if grade["kind"] == "legacy":
            counts["legacy"] += 1
            continue
        problems.update(grade["problems"])
        nodes = grade["nodes"]
        node_counts.append(len(nodes))
        node_counts_by_owner[owner].append(len(nodes))
        labels = [n["label"] for n in nodes]
        for n in nodes:
            node_types[n["type_name"]] += 1
            if n["luts"] is None:
                undecoded_lut_nodes += 1
            else:
                luts.update(n["luts"])
            if n["ofx"] is None:
                undecoded_ofx_nodes += 1
            else:
                ofx.update(n["ofx"])
        sequences[" > ".join(label for label in labels if label)] += 1
        grades.append({"owner": owner, "labels": labels})
    for reason, n in undecoded.items():
        errors.append(f"{n} grade bod{'y' if n == 1 else 'ies'} UNDECODED: {reason}")
    for reason, n in problems.items():
        errors.append(f"{UNDECODED}: {reason} ({n}x)")
    return {
        "graded": {k: counts.get(k, 0) for k in ("clip", "pool", "timeline", "other")},
        "legacy_grades": counts.get("legacy", 0),
        "undecoded_grades": sum(undecoded.values()),
        "undecoded_lut_nodes": undecoded_lut_nodes,
        "undecoded_ofx_nodes": undecoded_ofx_nodes,
        "node_count": _stats(node_counts),
        "node_count_by_owner": {k: _stats(v) for k, v in node_counts_by_owner.items()},
        "node_types": dict(node_types),
        "luts": dict(luts.most_common()),
        "ofx": dict(ofx.most_common()),
        "label_sequences": dict(sequences.most_common()),
        "grades": grades,
    }


def survey_media(con, errors, home=None):
    roots, codecs, codec_res = Counter(), Counter(), Counter()
    total = undecoded = 0
    for clip_blob, geometry_blob in con.execute("select Clip, Geometry from BtVideoInfo"):
        total += 1
        try:
            directory, filename, fourcc = decode_media_clip(clip_blob)
        except DecodeError:
            undecoded += 1
            for counter in (roots, codecs, codec_res):
                counter[UNDECODED] += 1
            continue
        roots[media_root(directory, home)] += 1
        codec = fourcc or ("ext " + (os.path.splitext(filename)[1].lstrip(".").upper() or "?"))
        codecs[codec] += 1
        try:
            res = "%dx%d" % decode_media_geometry(geometry_blob)
        except DecodeError:
            res = UNDECODED
        codec_res[f"{codec} {res}"] += 1
    if undecoded:
        errors.append(f"{undecoded} media clip blob(s) UNDECODED")
    return {"count": total, "roots": dict(roots.most_common()),
            "codecs": dict(codecs.most_common()),
            "codec_resolutions": dict(codec_res.most_common())}


def survey_settings(con, errors):
    rows = [fb for (fb,) in con.execute("select FieldsBlob from SM_Config") if fb]
    if not rows:
        errors.append("no SM_Config row with a FieldsBlob")
        return _undecoded_settings()
    try:
        return interpret_project_settings(decode_setup_fields(rows[0]))
    except DecodeError as e:
        errors.append(f"project settings: {e}")
        return _undecoded_settings()


def _undecoded_settings():
    return {"resolution": UNDECODED, "fps": UNDECODED, "color_science": UNDECODED,
            "input_color_space": UNDECODED, "output_transform_label": None,
            "color_management_fields": [], "defaulted": []}


SURVEY_SECTIONS = ("settings", "timelines", "grades", "media")


def survey_snapshot(db_path, home=None):
    """Survey one project from an already-snapshotted database file. A
    section that fails as a whole (a missing table, say) is None, with the
    reason in "errors"."""
    errors = []
    con = sqlite3.connect(f"file:{urllib.parse.quote(os.path.abspath(db_path))}?mode=ro", uri=True)
    run = {
        "settings": lambda: survey_settings(con, errors),
        "timelines": lambda: survey_timelines(con, errors),
        "grades": lambda: survey_grades(con, errors),
        "media": lambda: survey_media(con, errors, home),
    }
    sections = [(key, run[key]) for key in SURVEY_SECTIONS]
    try:
        result = {}
        for key, section in sections:
            try:
                result[key] = section()
            except Exception as e:  # sqlite errors, and any value no decoder guards
                errors.append(f"{key}: {type(e).__name__}: {e}")
                result[key] = None
    finally:
        con.close()
    result["errors"] = errors
    return result


def survey_project(project_dir, workdir, home=None):
    """Snapshot <project_dir>/Project.db into workdir, survey the copy,
    delete the copy. Never raises for a bad project; failures land in the
    returned dict's "errors". "read" is False when no snapshot was taken
    or every section failed; "failed_sections" lists the sections that
    failed in a project that was otherwise read."""
    name = os.path.basename(os.path.normpath(project_dir))
    live = os.path.join(project_dir, "Project.db")
    entry = {"name": name, "read": False}
    try:
        entry["db_saved"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.stat(live).st_mtime))
    except OSError as e:
        entry["errors"] = [f"Project.db: {e.strerror or e}"]
        return entry
    snap = os.path.join(workdir, "project.snapshot.db")
    try:
        snapshot_db(live, snap)
        entry["snapshot_bytes"] = os.path.getsize(snap)
        entry.update(survey_snapshot(snap, home))
        failed = [key for key in SURVEY_SECTIONS if entry.get(key) is None]
        entry["read"] = len(failed) < len(SURVEY_SECTIONS)
        if entry["read"]:
            entry["failed_sections"] = failed
        else:
            entry["errors"].append("no section could be read")
    except sqlite3.Error as e:
        entry["errors"] = [f"snapshot failed: {type(e).__name__}: {e}"]
    except Exception as e:  # keeps the promise above; the other projects still run
        entry["read"] = False
        entry["errors"] = [f"survey failed: {type(e).__name__}: {e}"]
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(snap + suffix)
            except FileNotFoundError:
                pass
    return entry


def read_metadata_cache(cache_path, workdir):
    """{project name: {width, height, fps}} from Resolve's metadata cache,
    read through a snapshot like every other DB. Returns ({}, error) when
    the cache is missing or unreadable."""
    if not cache_path or not os.path.exists(cache_path):
        return {}, None if not cache_path else f"metadata cache not found: {cache_path}"
    snap = os.path.join(workdir, "metadata.snapshot.db")
    try:
        snapshot_db(cache_path, snap)
        con = sqlite3.connect(snap)
        try:
            rows = con.execute("select key, width, height, fps from project_metadata").fetchall()
        finally:
            con.close()
        # Keep only values of the expected type: a TEXT or BLOB number here
        # would otherwise break the cross-check for every project.
        def num(v, kind):
            return v if isinstance(v, kind) and not isinstance(v, bool) else None
        return {k: {"width": num(w, int), "height": num(h, int), "fps": num(f, (int, float))}
                for k, w, h, f in rows if isinstance(k, str)}, None
    except sqlite3.Error as e:
        return {}, f"metadata cache unreadable: {type(e).__name__}: {e}"
    finally:
        try:
            os.remove(snap)
        except FileNotFoundError:
            pass


def cache_disagreements(settings, cache_row):
    """Where Project.db's settings and the metadata cache disagree, as
    short strings. Empty when they agree or either side is missing."""
    if not cache_row or not settings:
        return []
    out = []
    if cache_row.get("width") is not None and cache_row.get("height") is not None:
        cache_res = f"{cache_row['width']}x{cache_row['height']}"
        if settings.get("resolution") not in (UNDECODED, cache_res):
            out.append(f"cache resolution {cache_res}")
    fps = settings.get("fps")
    if isinstance(fps, (int, float)) and cache_row.get("fps") is not None:
        if not math.isclose(fps, cache_row["fps"], abs_tol=0.002):
            out.append(f"cache fps {_fmt_fps(cache_row['fps'])}")
    return out


# ---------------------------------------------------------------------------
# Cross-project patterns
# ---------------------------------------------------------------------------

def cross_project_patterns(projects, tree_labels=HOUSE_TREE_LABELS):
    luts, lut_projects = Counter(), defaultdict(set)
    ofx, ofx_projects = Counter(), defaultdict(set)
    seq_grades, seq_projects = Counter(), defaultdict(set)
    tree = {"labels": list(tree_labels), "projects": {}, "node_counts": Counter()}
    wanted = set(tree_labels)
    for p in projects:
        grades = p.get("grades") or {}
        for path, n in (grades.get("luts") or {}).items():
            luts[path] += n
            lut_projects[path].add(p["name"])
        for plugin, n in (grades.get("ofx") or {}).items():
            ofx[plugin] += n
            ofx_projects[plugin].add(p["name"])
        for seq, n in (grades.get("label_sequences") or {}).items():
            if seq:
                seq_grades[seq] += n
                seq_projects[seq].add(p["name"])
        for g in grades.get("grades") or []:
            if wanted and wanted.issubset(g["labels"]):
                tree["projects"][p["name"]] = tree["projects"].get(p["name"], 0) + 1
                tree["node_counts"][len(g["labels"])] += 1
    sequences = sorted(
        ({"sequence": s, "projects": len(seq_projects[s]), "grades": seq_grades[s],
          "project_names": sorted(seq_projects[s])} for s in seq_grades),
        key=lambda r: (-r["projects"], -r["grades"], r["sequence"]))
    return {
        "house_tree": {"labels": tree["labels"], "projects": dict(sorted(tree["projects"].items())),
                       "project_count": len(tree["projects"]),
                       "grade_count": sum(tree["projects"].values()),
                       "node_counts": {str(k): v for k, v in sorted(tree["node_counts"].items())}},
        "luts": [{"path": k, "count": v, "projects": len(lut_projects[k])}
                 for k, v in luts.most_common()],
        "ofx": [{"plugin": k, "count": v, "projects": len(ofx_projects[k])}
                for k, v in ofx.most_common()],
        "recurring_label_sequences": [r for r in sequences if r["projects"] >= 2],
        "single_project_label_sequences": [r for r in sequences if r["projects"] == 1],
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _stats(values):
    if not values:
        return None
    return {"min": min(values), "median": statistics.median(values), "max": max(values),
            "grades": len(values)}


def _fmt_fps(fps):
    return fps if fps == UNDECODED else f"{round(float(fps), 3):g}"


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _counts(counter, limit=None, key=lambda k: k):
    items = list(counter.items())
    shown = items[:limit] if limit else items
    text = ", ".join(f"{key(k)} x{v}" for k, v in shown)
    if limit and len(items) > limit:
        text += f", +{len(items) - limit} more"
    return text or "-"


def _gap_cell(text, undecoded, noun="node"):
    """A counts cell plus how many items could not be decoded. Where
    nothing decoded, the cell is the gap alone ('-' would read as none)."""
    if not undecoded:
        return text
    note = f"{undecoded} {noun}{'' if undecoded == 1 else 's'} {UNDECODED}"
    return note if text == "-" else f"{text}; {note}"


def _short_ofx(plugin):
    return plugin[len(OFX_VENDOR_PREFIX):] if plugin.startswith(OFX_VENDOR_PREFIX) else plugin


def _timeline_summary(timelines):
    groups = Counter()
    for t in timelines:
        groups[f"{t['resolution']}@{_fmt_fps(t['fps'])}"] += 1
    return _counts(groups) if groups else "-"


def _project_cell(p):
    """resolution@fps, then in one parenthesis: which of the two are
    absent-field defaults, any metadata-cache disagreement, and a note
    when the cache was read but holds no row for this project."""
    s = p.get("settings")
    if s is None:
        return UNDECODED
    text = f"{s.get('resolution', UNDECODED)}@{_fmt_fps(s.get('fps', UNDECODED))}"
    notes = [f"default {key}" for key in ("resolution", "fps") if key in (s.get("defaulted") or ())]
    notes += p.get("cache_disagreements") or []
    if p.get("cache_check") == "no row":
        notes.append("no cache row")
    if notes:
        text += " (" + "; ".join(notes) + ")"
    return text


def _color_cell(s):
    if not s:
        return UNDECODED
    mode = s.get("color_science", UNDECODED)
    if s.get("color_science_evidence", "").endswith("(inferred)"):
        mode += " (inferred)"
    space = s.get("input_color_space", UNDECODED)
    if space == UNDECODED:
        space = f"{UNDECODED} ({s.get('input_color_space_evidence', '?')})"
    text = f"{mode}; input {space}"
    if s.get("color_management_fields"):
        text += "; CM fields " + "/".join(str(f) for f in s["color_management_fields"])
    return text


def _nodes_cell(grades):
    """min/median/max, plus a note when the count includes node types
    other than correctors and mixers (their API counting is unverified)."""
    stats = grades.get("node_count")
    if not stats:
        return "-"
    text = f"{stats['min']}/{stats['median']:g}/{stats['max']}"
    known = set(NODE_TYPES.values())
    others = {k: v for k, v in (grades.get("node_types") or {}).items() if k not in known}
    if others:
        text += " (incl. " + ", ".join(f"{v} {k}" for k, v in sorted(others.items())) + ")"
    return text


def _graded_cell(grades):
    if grades is None:
        return UNDECODED
    g = grades.get("graded") or {}
    text = f"{g.get('clip', 0)}/{g.get('pool', 0)}/{g.get('timeline', 0)}"
    extras = []
    if grades.get("legacy_grades"):
        extras.append(f"{grades['legacy_grades']} legacy")
    if grades.get("undecoded_grades"):
        extras.append(f"{grades['undecoded_grades']} {UNDECODED}")
    if g.get("other"):
        extras.append(f"{g['other']} other owner")
    return text + (" (" + ", ".join(extras) + ")" if extras else "")


def _grade_cells(grades):
    """The Nodes, LUTs and OFX cells. They count the grades that decoded
    as node graphs. When the grades section failed, or grades exist and
    none of them decoded as a graph (all legacy or UNDECODED), all three
    are UNDECODED: '-' is kept for a project with no grades at all."""
    if grades is None:
        return [UNDECODED] * 3
    graphs = (grades.get("node_count") or {}).get("grades", 0)
    if not graphs and sum((grades.get("graded") or {}).values()):
        return [UNDECODED] * 3
    return [
        _nodes_cell(grades),
        _gap_cell(_counts(grades.get("luts") or {}, key=os.path.basename),
                  grades.get("undecoded_lut_nodes")),
        _gap_cell(_counts(grades.get("ofx") or {}, key=_short_ofx),
                  grades.get("undecoded_ofx_nodes")),
    ]


def _media_cells(media):
    """The Media roots and Codecs cells: UNDECODED when the media section
    failed, '-' when it read and found no clips."""
    if media is None:
        return [UNDECODED] * 2
    return [_counts(media.get("roots") or {}, limit=3), _counts(media.get("codecs") or {}, limit=3)]


def read_status(projects):
    """(read in full, read in part, not read), each a list of entries."""
    full = [p for p in projects if p.get("read") and not p.get("failed_sections")]
    partial = [p for p in projects if p.get("read") and p.get("failed_sections")]
    unread = [p for p in projects if not p.get("read")]
    return full, partial, unread


def _partial_note(p):
    return f"{p['name']} ({', '.join(p['failed_sections'])})"


def render_markdown(report):
    projects = report["projects"]
    patterns = report["patterns"]
    full, partial, unread = read_status(projects)
    status = [
        f"Generated {report['generated']} by resolve_survey.py. {len(projects)} project "
        f"databases in `{report['projects_dir']}`: {len(full)} read in full, {len(partial)} "
        f"read in part, {len(unread)} not read.",
    ]
    if partial:
        status.append("Read in part (the named sections failed and print as "
                      f"{UNDECODED}; reasons under Decode problems): "
                      + ", ".join(_cell(_partial_note(p)) for p in partial) + ".")
    if unread:
        status.append("Not read (every cell prints as "
                      f"{UNDECODED}; reasons under Decode problems): "
                      + ", ".join(_cell(p["name"]) for p in unread) + ".")
    lines = [
        "# Resolve project survey",
        "",
        "\n\n".join(status),
        "",
        "Every Project.db was copied with SQLite's online backup API over a read-only "
        "(`mode=ro`) connection, and the copy was read and then deleted. No Resolve API "
        "call was made. A project saved while the survey ran is a snapshot of that moment.",
        "",
        "## How to read the table",
        "",
        "- **Saved**: the live Project.db's modification time.",
        "- **Timelines**: resolution@fps per timeline, grouped. `project` means the timeline "
        "uses the project resolution.",
        "- **Project**: project resolution@fps from SetupBA fields 1 and 248. `default "
        "resolution` and `default fps` mark a field SetupBA left out: the cell shows the default "
        "this survey assumes for it (%dx%d, %g fps), which was not read from the project. Where "
        "Resolve's metadata cache disagrees, the cache value follows in parentheses; which of the "
        "two is right is unverified. `no cache row`: the cache has no entry for the project, so "
        "nothing cross-checks its value." % (*DEFAULT_PROJECT_RESOLUTION, DEFAULT_PROJECT_FPS)
        + ("" if report.get("metadata_cache_read") else
           " The metadata-cache cross-check did not run for this survey (turned off, or the "
           "cache could not be read), so no Project value is cross-checked."),
        "- **Color**: color science mode (SetupBA field 46) and input color space (field 203). "
        "\"inferred\" marks the absent-field reading, which rests on two API checks. "
        "\"CM fields\" lists color-management fields present in the project.",
        "- **Graded**: active grades with corrections, as clip / media pool / timeline.",
        "- **Nodes**: nodes per active grade (API `GetNumNodes` equivalent), min/median/max. "
        "\"incl.\" flags node types other than correctors and mixers, which are counted but "
        "whose API counting is unverified.",
        "- **LUTs** and **OFX**: node occurrences across active grades. Nodes, LUTs and OFX "
        "count the grades that decoded as node graphs; Graded names the legacy and "
        f"`{UNDECODED}` grades they leave out.",
        "- **Media**: BtVideoInfo rows per storage root; **Codecs**: fourcc, or the file "
        "extension when the clip has no fourcc (stills).",
        f"- `{UNDECODED}`: the value could not be decoded from the database. A cell that is "
        f"`{UNDECODED}` alone means the whole section failed, or nothing in it decoded; `-` "
        "means the section read and found none.",
        "",
        "## Per project",
        "",
        "| Project | Saved | #TL | Timelines | Project | Color | Graded (clip/pool/TL) | Nodes | "
        "LUTs | OFX | Media roots | Codecs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in projects:
        if not p.get("read"):
            lines.append(f"| {_cell(p['name'])} | {p.get('db_saved', '-')} | {UNDECODED} | "
                         + " | ".join([UNDECODED] * 9) + " |")
            continue
        timelines = p.get("timelines")
        row = [
            p["name"],
            p.get("db_saved", "-"),
            len(timelines) if timelines is not None else UNDECODED,
            _timeline_summary(timelines) if timelines is not None else UNDECODED,
            _project_cell(p),
            _color_cell(p.get("settings")),
            _graded_cell(p.get("grades")),
            *_grade_cells(p.get("grades")),
            *_media_cells(p.get("media")),
        ]
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")

    tree = patterns["house_tree"]
    lines += [
        "",
        "## Cross-project patterns",
        "",
        "### House node tree",
        "",
        f"Grades whose node labels include all of: {', '.join(repr(l) for l in tree['labels'])}.",
        "",
        f"- Projects: **{tree['project_count']}**; grades: **{tree['grade_count']}**.",
        "- Node counts among those grades: "
        + (", ".join(f"{k} nodes x{v}" for k, v in tree["node_counts"].items()) or "-") + ".",
        "- Per project: " + (", ".join(f"{_cell(k)} {v}" for k, v in tree["projects"].items()) or "-") + ".",
        "",
        "### LUTs (node occurrences, all active grades)",
        "",
        "| LUT | Count | Projects |",
        "|---|---|---|",
    ]
    lines += [f"| `{_cell(r['path'])}` | {r['count']:,} | {r['projects']} |" for r in patterns["luts"]] or ["| - | 0 | 0 |"]
    lines += ["", "### OFX plugins (instances, all active grades)", "",
              "| Plugin | Count | Projects |", "|---|---|---|"]
    lines += [f"| `{_cell(r['plugin'])}` | {r['count']:,} | {r['projects']} |" for r in patterns["ofx"]] or ["| - | 0 | 0 |"]
    lines += ["", "### Node-label sequences that recur across projects", "",
              "Labeled nodes only, in signal-flow order; unlabeled nodes are left out.", "",
              "| Sequence | Projects | Grades |", "|---|---|---|"]
    lines += [f"| {_cell(r['sequence'])} | {r['projects']} | {r['grades']:,} |"
              for r in patterns["recurring_label_sequences"]] or ["| - | 0 | 0 |"]
    single = patterns["single_project_label_sequences"][:SINGLE_PROJECT_SEQUENCES_SHOWN]
    lines += ["", f"Sequences seen in one project only (top {SINGLE_PROJECT_SEQUENCES_SHOWN} by "
              "grades; the full list is in the JSON):", "",
              "| Sequence | Project | Grades |", "|---|---|---|"]
    lines += [f"| {_cell(r['sequence'])} | {_cell(r['project_names'][0])} | {r['grades']:,} |"
              for r in single] or ["| - | - | 0 |"]

    problems = [(p["name"], e) for p in projects for e in p.get("errors") or []]
    if report.get("notes"):
        problems += [("(survey)", n) for n in report["notes"]]
    lines += ["", "## Decode problems", ""]
    lines += [f"- {_cell(name)}: {_cell(e)}" for name, e in problems] or ["None."]
    if report.get("skipped"):
        lines += ["", "Folders without a Project.db (skipped): "
                  + ", ".join(_cell(s) for s in report["skipped"]) + "."]
    return "\n".join(lines) + "\n"


def _json_ready(report):
    """The report minus per-grade label lists, which only feed the
    cross-project patterns."""
    out = dict(report)
    out["projects"] = []
    for p in report["projects"]:
        p = dict(p)
        if p.get("grades"):
            p["grades"] = {k: v for k, v in p["grades"].items() if k != "grades"}
        out["projects"].append(p)
    return out


# ---------------------------------------------------------------------------
# Output safety
# ---------------------------------------------------------------------------

def git_common_dir(start):
    """The shared .git directory of the git working tree containing the
    directory `start` (following a linked worktree's .git file and its
    commondir), or None outside any working tree."""
    here = Path(os.path.realpath(start))
    for d in (here, *here.parents):
        dotgit = d / ".git"
        if dotgit.is_dir():
            return dotgit.resolve()
        if dotgit.is_file():
            text = dotgit.read_text(errors="replace").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text[len("gitdir:"):].strip())
            gitdir = (gitdir if gitdir.is_absolute() else d / gitdir).resolve()
            commondir = gitdir / "commondir"
            if commondir.is_file():
                c = Path(commondir.read_text(errors="replace").strip())
                return (c if c.is_absolute() else gitdir / c).resolve()
            return gitdir
    return None


def inside_repo(path, repo_dir=None):
    """True when `path` would land inside any working tree of the git
    repository that holds repo_dir (default: this script's directory).
    `path` is resolved first, including a symlink at its last component
    (dangling or not), since a write to the link lands at its target."""
    repo_common = git_common_dir(repo_dir or os.path.dirname(os.path.realpath(__file__)))
    if repo_common is None:
        return False
    parent = Path(os.path.realpath(path)).parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return git_common_dir(parent) == repo_common


def write_report(path, text):
    """Write text to `path`, resolved the way inside_repo resolves it. The
    text goes to a temporary file (mode 0600) beside the target, which then
    replaces the target's directory entry. A file hard-linked to the target
    elsewhere keeps its old content, so a link into the repo cannot carry
    the report there, and a reader never sees a half-written file."""
    target = os.path.realpath(path)
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".resolve-survey-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def list_projects(projects_dir, names=None):
    """(project dirs to survey, skipped folder names, unknown names)."""
    base = Path(projects_dir)
    folders = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: d.name.lower())
    with_db = [d for d in folders if (d / "Project.db").is_file()]
    skipped = [d.name for d in folders if d not in with_db]
    if not names:
        return with_db, skipped, []
    by_name = {d.name: d for d in with_db}
    return ([by_name[n] for n in names if n in by_name], [],
            [n for n in names if n not in by_name])


def run_survey(project_dirs, cache_path=None, tree_labels=HOUSE_TREE_LABELS, home=None):
    notes = []
    with tempfile.TemporaryDirectory(prefix="resolve-survey-") as workdir:
        cache, cache_error = read_metadata_cache(cache_path, workdir)
        if cache_error:
            notes.append(cache_error)
        cache_read = bool(cache_path) and not cache_error
        projects = []
        for d in project_dirs:
            entry = survey_project(str(d), workdir, home)
            if entry.get("read"):
                row = cache.get(entry["name"])
                entry["metadata_cache"] = row
                entry["cache_disagreements"] = cache_disagreements(entry.get("settings"), row)
                # "no row" is kept apart from "checked": both leave
                # cache_disagreements empty, and only one was cross-checked.
                entry["cache_check"] = ("not run" if not cache_read
                                        else "no row" if row is None else "checked")
            projects.append(entry)
    return {"projects": projects, "patterns": cross_project_patterns(projects, tree_labels),
            "notes": notes, "metadata_cache_read": cache_read}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="resolve_survey",
        description="Read-only survey of every DaVinci Resolve project in the local disk "
                    "database (timelines, grades, color science, media). Offline: no Resolve "
                    "API call; each Project.db is read through a snapshot.")
    parser.add_argument("--out", required=True,
                        help="Markdown report path (must be outside this repository)")
    parser.add_argument("--json", dest="json_out", help="Also write the full survey as JSON here")
    parser.add_argument("--projects", nargs="+", metavar="NAME",
                        help="Only these project folders (default: every folder with a Project.db)")
    parser.add_argument("--projects-dir", default=str(PROJECTS_DIR_DEFAULT),
                        help="Resolve's Projects folder (default: the guest user's disk database)")
    parser.add_argument("--metadata-cache", default=str(METADATA_CACHE_DEFAULT),
                        help="Resolve's ProjectMetadataCache/Metadata.db, used as a cross-check")
    parser.add_argument("--no-metadata-cache", action="store_true",
                        help="Skip the metadata-cache cross-check")
    parser.add_argument("--tree-labels", nargs="+", default=list(HOUSE_TREE_LABELS), metavar="LABEL",
                        help="Node labels that mark the house node tree (default: %(default)s)")
    args = parser.parse_args(argv)

    # The output refusal comes first: it guards a public repo and depends on
    # nothing else, so an interpreter without zstd still refuses it.
    for label, path in (("--out", args.out), ("--json", args.json_out)):
        if path and inside_repo(path):
            print(f"ERROR: {label} {path} is inside this repository's git working tree. The "
                  "survey names projects, media and LUT paths; write it somewhere outside "
                  "the repo.", file=sys.stderr)
            return 2
    if zstd is None:
        print(f"ERROR: this Python ({sys.version.split()[0]}) has no compression.zstd. "
              "Run with Python 3.14+, e.g. /opt/homebrew/bin/python3.14.", file=sys.stderr)
        return 2
    if not os.path.isdir(args.projects_dir):
        print(f"ERROR: projects folder not found: {args.projects_dir}", file=sys.stderr)
        return 2

    project_dirs, skipped, unknown = list_projects(args.projects_dir, args.projects)
    if unknown:
        print("ERROR: no Project.db for: " + ", ".join(unknown), file=sys.stderr)
        return 2
    if not project_dirs:
        print(f"ERROR: no projects found under {args.projects_dir}", file=sys.stderr)
        return 2

    cache_path = None if args.no_metadata_cache else args.metadata_cache
    report = run_survey(project_dirs, cache_path, tuple(args.tree_labels))
    report.update({"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                   "projects_dir": args.projects_dir, "skipped": skipped})

    write_report(args.out, render_markdown(report))
    if args.json_out:
        write_report(args.json_out, json.dumps(_json_ready(report), indent=2, sort_keys=False) + "\n")

    full, partial, unread = read_status(report["projects"])
    tree = report["patterns"]["house_tree"]
    print(f"Surveyed {len(report['projects'])} projects: {len(full)} read in full, "
          f"{len(partial)} in part, {len(unread)} not read -> {args.out}"
          + (f" (+ {args.json_out})" if args.json_out else ""))
    print(f"  House node tree: {tree['project_count']} projects, {tree['grade_count']} grades")
    if partial:
        print("  Read in PART (failed sections): " + ", ".join(_partial_note(p) for p in partial),
              file=sys.stderr)
    if unread:
        print("  FAILED to read: " + ", ".join(p["name"] for p in unread), file=sys.stderr)
    return 1 if partial or unread else 0


if __name__ == "__main__":
    sys.exit(main())
