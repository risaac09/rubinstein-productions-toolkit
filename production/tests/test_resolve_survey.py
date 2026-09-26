"""
Offline unit tests for resolve_survey.py. Every blob and database here is
synthetic, built in the test from the documented formats; no Resolve
install and no real project data is needed or read.

The survey decodes zstd blobs with compression.zstd, which exists only in
Python 3.14+. Under an older interpreter (the system /usr/bin/python3 is
3.9) this whole module skips itself.

stdlib unittest only.

Run: /opt/homebrew/bin/python3.14 -m unittest discover production/tests -v
"""

import unittest

try:
    from compression import zstd
except ImportError:
    raise unittest.SkipTest(
        "resolve_survey needs compression.zstd (Python 3.14+); run "
        "/opt/homebrew/bin/python3.14 -m unittest discover production/tests")

import io
import json
import os
import sqlite3
import stat
import struct
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import resolve_survey as rs  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic encoders (the inverse of what the survey decodes)
# ---------------------------------------------------------------------------

def varint(n):
    out = bytearray()
    while True:
        low, n = n & 0x7F, n >> 7
        out.append(low | (0x80 if n else 0))
        if not n:
            return bytes(out)


def fld(num, value):
    """One protobuf field: int -> varint, str/bytes -> length-delimited."""
    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return varint(num << 3 | 2) + varint(len(value)) + value


def fixed32(num, raw):
    return varint(num << 3 | 5) + raw


def msg(*parts):
    return b"".join(parts)


def zblob(payload):
    return b"\x81" + zstd.compress(payload)


def fields_blob(payload, tag=2, compressed=True):
    body = zblob(payload) if compressed else b"\x80" + payload
    return struct.pack(">II", tag, len(body)) + body


def qkey(key):
    raw = key.encode("utf-16-be")
    return struct.pack(">I", len(raw)) + raw


def qmap(entries):
    """QVariantMap as Resolve writes it; entries are (key, qtype, value)."""
    out = struct.pack(">II", 1, len(entries))
    for key, qtype, value in entries:
        out += qkey(key) + struct.pack(">I", qtype) + b"\x00"
        if qtype == 12:
            out += struct.pack(">I", len(value)) + value
        elif qtype == 10:
            raw = value.encode("utf-16-be")
            out += struct.pack(">I", len(raw)) + raw
        elif qtype == 4:
            out += struct.pack(">q", value)
        else:
            raise ValueError(qtype)
    return out


def lut_param(path):
    """Node field 9 with a LUT path nested the way Resolve stores it:
    9 -> 1 -> 6 -> 2 -> 3 -> {1: param id, 2: {5: path}}."""
    value = msg(fld(1, 0x8B000A1), fld(2, msg(fld(5, path))))
    return msg(fld(1, msg(fld(1, 1), fld(6, msg(fld(2, msg(fld(1, 1), fld(3, value))))))))


def ofx_stack(plugin_id):
    """Node field 10 with one OFX instance: the plugin id appears once as a
    plain parameter string and once in the context message that counts."""
    instance = msg(fld(5, "OfxImageEffectContextFilter_0000_3"),
                   fld(21, msg(fld(2, plugin_id), fld(3, "OfxImageEffectContextFilter"),
                               fld(5, msg(fld(1, "resolvefxVersion"), fld(2, msg(fld(5, "1.4"))))))))
    return msg(fld(1, msg(fld(1, 7), fld(2, msg(fld(5, plugin_id))))),
               fld(1, msg(fld(1, 8), fld(2, instance))))


def node(node_id, label=None, node_type=44, luts=(), ofx=()):
    parts = [fld(1, node_id), fld(2, 1)]
    if label is not None:
        parts.append(fld(6, label))
    parts.append(fld(8, node_type))
    parts.append(fld(9, b"".join(lut_param(p) for p in luts) or msg(fld(1, 0))))
    parts.append(fld(10, b"".join(ofx_stack(p) for p in ofx) or b""))
    return msg(*parts)


def edge(src, dst, input_index=0):
    parts = [fld(1, src), fld(3, dst)]
    if input_index:
        parts.append(fld(4, input_index))
    return msg(*parts, fld(5, 64))


def grade_body(nodes, edges, compressed=True):
    graph = msg(fld(1, 1), fld(2, 1), *(fld(7, n) for n in nodes), *(fld(8, e) for e in edges))
    payload = msg(fld(1, graph), fld(3, msg(fld(1, 1), fld(8, 84))))
    return zblob(payload) if compressed else b"\x80" + payload


HOUSE_LUT = "Vendor/Camera Log to 709.cube"
LOOK_LUT = "LOOK.cube"
CST = "com.blackmagicdesign.resolvefx.colorspacetransformv2"


def house_grade():
    """A small house-tree grade, stored out of signal-flow order."""
    nodes = [
        node(7, "LUT", luts=[LOOK_LUT]),
        node(1),
        node(2, "709", luts=[HOUSE_LUT]),
        node(3, "CST IN", ofx=[CST]),
        node(4, "EXP"),
        node(5, "WB"),
        node(8, "CON"),
        node(9, "HSV SAT"),
        node(6, "CST OUT", ofx=[CST]),
    ]
    edges = [edge(1, 2), edge(2, 3), edge(3, 4), edge(4, 5), edge(5, 8), edge(8, 9),
             edge(9, 6), edge(6, 7)]
    return grade_body(nodes, edges)


def setup_blob(fields_payload):
    return qmap([("SetupBA", 12, fields_blob(fields_payload, tag=1)),
                 ("CustomOverridableLastModTimeForCache", 4, 12345)])


def clip_blob(directory, filename, fourcc=None):
    parts = [fld(1, directory), fld(2, filename), fld(3, "Mon Jan  1 00:00:00 2026")]
    if fourcc:
        parts.append(fld(5, fourcc))
    return fields_blob(msg(*parts), tag=2)


def geometry_blob(w, h):
    return qmap([("UniqueId", 10, "0000"), ("Resolution", 12, struct.pack(">qq", w, h))])


SCHEMA = [
    'create table SM_Config (FieldsBlob blob)',
    'create table Sm2Timeline (Name text, Sequence text)',
    'create table Sm2Sequence (Sm2Sequence_id text, Resolution blob, FrameRate blob)',
    'create table Sm2TiTrack (Sm2TiTrack_id text, Type integer, Sequence text)',
    'create table Sm2TiItem (Sm2TiItem_id text, Sm2TiTrack_id text)',
    'create table "ListMgt::LmVersionTable" ("ListMgt::LmVersionTable_id" text, pActive text, '
    'Sm2TiItem_id text, Sm2MpMedia_id text, Sm2Sequence_id text)',
    'create table "ListMgt::LmVersion" ("ListMgt::LmVersion_id" text, '
    '"ListMgt::LmVersionTable_id" text, HasCorrection boolean, VerType text, Body blob)',
    'create table BtVideoInfo (Clip blob, Geometry blob)',
]


def build_project_db(path, corrupt_grade=True):
    """A tiny Project.db with only the tables and columns the survey reads."""
    con = sqlite3.connect(path)
    for sql in SCHEMA:
        con.execute(sql)
    con.execute("insert into SM_Config values (?)", (setup_blob(msg(
        fld(1, "3840 x 2160 Ultra HD"), fixed32(248, struct.pack("<f", 29.97)), fld(203, 14))),))
    fps_2997 = struct.pack("<d", 30000 / 1001) + b"\x00" * 8
    con.executemany("insert into Sm2Sequence values (?,?,?)", [
        ("seq-a", struct.pack(">qq", 3840, 2160), fps_2997),
        ("seq-b", struct.pack(">qq", 0, 0), struct.pack("<d", 24.0) + b"\x00" * 8),
    ])
    con.executemany("insert into Sm2Timeline values (?,?)", [("Main", "seq-a"), ("Vertical", "seq-b")])
    con.executemany("insert into Sm2TiTrack values (?,?,?)", [
        ("trk-v", 0, "seq-a"), ("trk-s", 2, "seq-a"), ("trk-v2", 0, "seq-b")])
    con.executemany("insert into Sm2TiItem values (?,?)", [
        ("item-1", "trk-v"), ("item-2", "trk-v"), ("sub-1", "trk-s"), ("item-3", "trk-v2")])
    tables = [
        ("vt-1", "v-1b", "item-1", None, None),   # clip grade; v-1a is an inactive version
        ("vt-2", "v-2", "item-2", None, None),    # clip grade, single corrector
        ("vt-3", "v-3", None, "media-1", None),   # media-pool grade
        ("vt-4", "v-4", None, None, "seq-a"),     # timeline version without corrections
    ]
    versions = [
        ("v-1a", "vt-1", 1, "0", grade_body([node(1, "OLD")], [])),
        ("v-1b", "vt-1", 1, "0", house_grade()),
        ("v-2", "vt-2", 1, "0", grade_body([node(1, "SOLO")], [], compressed=False)),
        ("v-3", "vt-3", 1, "0", grade_body([node(1), node(2)], [edge(1, 2)])),
        ("v-4", "vt-4", 0, "1", grade_body([node(1)], [])),
    ]
    if corrupt_grade:
        tables.append(("vt-5", "v-5", "item-3", None, None))
        versions.append(("v-5", "vt-5", 1, "0", b"\x81not zstd"))
    con.executemany('insert into "ListMgt::LmVersionTable" values (?,?,?,?,?)', tables)
    con.executemany('insert into "ListMgt::LmVersion" values (?,?,?,?,?)', versions)
    con.executemany("insert into BtVideoInfo values (?,?)", [
        (clip_blob("/mnt/CARD_A/DCIM/100", "C0001.MOV", "hvc1"), geometry_blob(5760, 4320)),
        (clip_blob("/home/u/Desktop/stills", "P0001.RW2"), geometry_blob(6000, 4000)),
        (b"\x00\x00", None),
    ])
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProtobuf(unittest.TestCase):
    def test_varint_roundtrip(self):
        for n in (0, 1, 127, 128, 300, 2 ** 32, 2 ** 63 - 1):
            self.assertEqual(rs.read_varint(varint(n), 0), (n, len(varint(n))))

    def test_truncated_varint(self):
        with self.assertRaises(rs.DecodeError):
            rs.read_varint(b"\xac", 0)

    def test_parse_all_wire_types(self):
        buf = msg(fld(1, 150), varint(2 << 3 | 1) + b"\x01" * 8, fld(3, "hi"),
                  fixed32(4, b"\x02" * 4))
        self.assertEqual(rs.parse_message(buf), [
            (1, 0, 150), (2, 1, b"\x01" * 8), (3, 2, b"hi"), (4, 5, b"\x02" * 4)])

    def test_group_wire_type_rejected(self):
        with self.assertRaises(rs.DecodeError):
            rs.parse_message(varint(1 << 3 | 3))

    def test_overrun_rejected(self):
        with self.assertRaises(rs.DecodeError):
            rs.parse_message(varint(1 << 3 | 2) + varint(10) + b"abc")
        self.assertIsNone(rs.try_parse_message(varint(1 << 3 | 2) + varint(10) + b"abc"))

    def test_first_fields_keeps_first(self):
        self.assertEqual(rs.first_fields([(1, 0, 5), (1, 0, 6), (2, 2, b"x")]), {1: 5, 2: b"x"})

    def test_non_bytes_rejected_without_allocating(self):
        # bytes(2 ** 62) would try to allocate 4 EiB; bytes("x") raises TypeError.
        for bad in (2 ** 62, 2 ** 40, "text", 1.5, None):
            with self.assertRaises(rs.DecodeError):
                rs.parse_message(bad)
            self.assertIsNone(rs.try_parse_message(bad))
        self.assertEqual(rs.parse_message(bytearray(fld(1, 5))), [(1, 0, 5)])
        self.assertEqual(rs.parse_message(memoryview(fld(1, 5))), [(1, 0, 5)])


class TestFraming(unittest.TestCase):
    def test_zstd_unwrap(self):
        payload = msg(fld(1, "hello"), fld(2, 42)) * 50
        self.assertEqual(rs.unwrap_blob(zblob(payload)), payload)

    def test_raw_unwrap(self):
        self.assertEqual(rs.unwrap_blob(b"\x80abc"), b"abc")

    def test_unknown_prefix_and_bad_zstd(self):
        for bad in (b"", b"\x7fabc", b"\x81not a zstd frame"):
            with self.assertRaises(rs.DecodeError):
                rs.unwrap_blob(bad)

    def test_fields_blob_tags_and_payload(self):
        payload = msg(fld(1, "/mnt/CARD"), fld(2, "C0001.MOV"))
        self.assertEqual(rs.unwrap_fields_blob(fields_blob(payload, tag=2)), payload)
        self.assertEqual(rs.unwrap_fields_blob(fields_blob(payload, tag=1)), payload)
        self.assertEqual(rs.unwrap_fields_blob(fields_blob(payload, compressed=False)), payload)

    def test_fields_blob_rejects_bad_frames(self):
        good = fields_blob(b"\x08\x01")
        for bad in (good[:6], good[:-1], struct.pack(">I", 3) + good[4:]):
            with self.assertRaises(rs.DecodeError):
                rs.unwrap_fields_blob(bad)

    def test_qvariant_map(self):
        blob = qmap([("SetupBA", 12, b"\x01\x02"), ("Name", 10, "Timeline 1"), ("Mod", 4, -5)])
        self.assertEqual(rs.parse_qvariant_map(blob),
                         {"SetupBA": b"\x01\x02", "Name": "Timeline 1", "Mod": -5})

    def test_decoders_reject_text_and_integer_columns(self):
        # SQLite hands back str for TEXT and int for INTEGER columns.
        decoders = (rs.unwrap_blob, rs.unwrap_fields_blob, rs.parse_qvariant_map,
                    rs.decode_be_resolution, rs.decode_frame_rate, rs.decode_grade_body,
                    rs.decode_media_clip, rs.decode_media_geometry, rs.decode_setup_fields)
        for decode in decoders:
            for bad in ("x" * 16, "GRF text", 2 ** 40, 7, 1.5):
                with self.assertRaises(rs.DecodeError, msg=f"{decode.__name__}({bad!r})"):
                    decode(bad)

    def test_qvariant_map_rejects_unknown_type_and_version(self):
        blob = struct.pack(">II", 1, 1) + qkey("X") + struct.pack(">I", 99) + b"\x00"
        with self.assertRaises(rs.DecodeError):
            rs.parse_qvariant_map(blob)
        with self.assertRaises(rs.DecodeError):
            rs.parse_qvariant_map(struct.pack(">II", 2, 0))


class TestFieldDecoders(unittest.TestCase):
    def test_resolution(self):
        self.assertEqual(rs.decode_be_resolution(struct.pack(">qq", 1080, 1920)), (1080, 1920))
        self.assertIsNone(rs.decode_be_resolution(struct.pack(">qq", 0, 0)))
        for bad in (b"", struct.pack(">q", 1920), struct.pack(">qq", -1, 1080)):
            with self.assertRaises(rs.DecodeError):
                rs.decode_be_resolution(bad)

    def test_frame_rate(self):
        self.assertAlmostEqual(rs.decode_frame_rate(struct.pack("<d", 30000 / 1001) + b"\x00" * 8),
                               29.97, places=2)
        self.assertEqual(rs.decode_frame_rate(struct.pack("<d", 24.0)), 24.0)
        for bad in (b"\x00" * 4, struct.pack("<d", 0.0), struct.pack("<d", float("nan"))):
            with self.assertRaises(rs.DecodeError):
                rs.decode_frame_rate(bad)

    def test_media_clip_and_geometry(self):
        self.assertEqual(rs.decode_media_clip(clip_blob("/mnt/CARD_A/DCIM", "C1.MOV", "hvc1")),
                         ("/mnt/CARD_A/DCIM", "C1.MOV", "hvc1"))
        self.assertEqual(rs.decode_media_clip(clip_blob("/mnt/CARD_A", "P1.RW2")),
                         ("/mnt/CARD_A", "P1.RW2", None))
        self.assertEqual(rs.decode_media_geometry(geometry_blob(5760, 4320)), (5760, 4320))

    def test_media_root(self):
        self.assertEqual(rs.media_root("/mnt/CARD_A/DCIM/100", home="/home/u"), "/mnt/CARD_A")
        self.assertEqual(rs.media_root("/home/u/Desktop/x", home="/home/u"), "~/Desktop")
        self.assertEqual(rs.media_root("/home/u", home="/home/u"), "~")
        self.assertEqual(rs.media_root("", home="/home/u"), rs.UNDECODED)

    def test_settings_with_fields(self):
        fields = rs.decode_setup_fields(setup_blob(msg(
            fld(1, "3840 x 2160 Ultra HD"), fixed32(248, struct.pack("<f", 23.976)),
            fld(46, 2), fld(122, "No Output Transform"))))
        s = rs.interpret_project_settings(fields)
        self.assertEqual(s["resolution"], "3840x2160")
        self.assertEqual(s["fps"], 23.976)
        self.assertEqual(s["color_science"], "davinciYRGBColorManagedv2")
        self.assertEqual(s["input_color_space"], rs.UNDECODED)
        self.assertEqual(s["input_color_space_evidence"], "field 203 absent")
        self.assertEqual(s["output_transform_label"], "No Output Transform")
        self.assertEqual(s["color_management_fields"], [122])

    def test_settings_defaults_and_unknown_codes(self):
        s = rs.interpret_project_settings({})
        self.assertEqual((s["resolution"], s["fps"], s["color_science"]),
                         ("1920x1080", 24.0, "davinciYRGB"))
        self.assertIn("inferred", s["color_science_evidence"])
        s = rs.interpret_project_settings({46: [(0, 7)], 203: [(0, 14)], 1: [(2, b"Custom")]})
        self.assertEqual(s["color_science"], rs.UNDECODED)
        self.assertEqual(s["color_science_evidence"], "field 46 = 7")
        self.assertEqual(s["input_color_space"], "Rec.709 Gamma 2.4")
        self.assertEqual(s["resolution"], rs.UNDECODED)

    def test_present_field_with_unexpected_wire_type_is_undecoded(self):
        s = rs.interpret_project_settings({
            1: [(0, 5)], 248: [(1, struct.pack("<d", 29.97))], 46: [(2, b"x")], 203: [(5, b"\x00" * 4)]})
        for key in ("resolution", "fps", "color_science", "input_color_space"):
            self.assertEqual(s[key], rs.UNDECODED, key)
        self.assertEqual(s["fps_evidence"], "field 248 present with wire type 1")
        self.assertEqual(s["color_science_evidence"], "field 46 present with wire type 2")


class TestNodeGraph(unittest.TestCase):
    def test_lut_path_unwrapped_once(self):
        self.assertEqual(rs.extract_lut_paths(lut_param("FILMLOOK.cube")), ["FILMLOOK.cube"])

    def test_printable_wrapper_unwrapped(self):
        # 32..126-byte paths make the {5: path} wrapper printable text itself.
        path = "Pack/Camera/Log to Rec709 v3 65.cube"
        self.assertTrue(32 <= len(path) <= 126)
        self.assertEqual(rs.extract_lut_paths(lut_param(path)), [path])

    def test_path_that_parses_as_protobuf_stays_whole(self):
        # "R" reads as field 10 (length-delimited) and "+" as length 43, and
        # "(Ab(" as two fields; both are paths and must come back whole.
        rest = "Looks/Film Emulation/Kodak 2383 D65 v2.cube"
        tricky = ["R" + chr(len(rest)) + rest,
                  "(Ab(" + "Kodak 2383 D65 print film emulation.cube"]
        self.assertIsNotNone(rs.try_parse_message(tricky[0].encode()))
        for path in tricky:
            self.assertEqual(rs.extract_lut_paths(lut_param(path)), [path])

    def test_long_lut_path(self):
        path = "Pack/" + "x" * 200 + ".cube"
        self.assertEqual(rs.extract_lut_paths(lut_param(path)), [path])

    def test_no_lut(self):
        self.assertEqual(rs.extract_lut_paths(msg(fld(1, msg(fld(5, "not a lut"))))), [])

    def test_ofx_counted_once_per_instance(self):
        self.assertEqual(rs.extract_ofx_plugins(ofx_stack(CST)), [CST])
        self.assertEqual(rs.extract_ofx_plugins(ofx_stack(CST) + ofx_stack("com.x:y")), [CST, "com.x:y"])
        self.assertEqual(rs.extract_ofx_plugins(b""), [])

    def test_graph_decode_order_labels_luts_ofx_edges(self):
        g = rs.decode_grade_body(house_grade())
        self.assertEqual(g["kind"], "graph")
        self.assertEqual([n["label"] for n in g["nodes"]],
                         ["", "709", "CST IN", "EXP", "WB", "CON", "HSV SAT", "CST OUT", "LUT"])
        by_label = {n["label"]: n for n in g["nodes"]}
        self.assertEqual(by_label["709"]["luts"], [HOUSE_LUT])
        self.assertEqual(by_label["LUT"]["luts"], [LOOK_LUT])
        self.assertEqual(by_label["CST IN"]["ofx"], [CST])
        self.assertEqual(by_label["EXP"]["luts"], [])
        self.assertEqual(by_label["EXP"]["type_name"], "corrector")
        self.assertIn((1, 2, 0), g["edges"])
        self.assertEqual(len(g["edges"]), 8)

    def test_parallel_mixer_order_and_input_index(self):
        nodes = [node(4, node_type=68), node(3, "B"), node(2, "A"), node(1, "IN")]
        g = rs.decode_grade_body(grade_body(nodes, [edge(1, 2), edge(1, 3), edge(2, 4), edge(3, 4, 1)]))
        self.assertEqual([n["label"] for n in g["nodes"]], ["IN", "A", "B", ""])
        self.assertEqual(g["nodes"][-1]["type_name"], "mixer")
        self.assertIn((3, 4, 1), g["edges"])

    def test_raw_legacy_and_bad_bodies(self):
        self.assertEqual(len(rs.decode_grade_body(grade_body([node(1, "X")], [], compressed=False))["nodes"]), 1)
        self.assertEqual(rs.decode_grade_body(b"GRF X{AID{1}}")["kind"], "legacy")
        for bad in (None, b"\x81garbage", zblob(msg(fld(2, 1)))):
            with self.assertRaises(rs.DecodeError):
                rs.decode_grade_body(bad)

    def test_node_payload_with_wrong_wire_type_is_undecoded(self):
        # Node fields 9 (LUT params) and 10 (OFX stack) stored as varints:
        # the values must never reach parse_message, and the node's LUTs and
        # OFX read as UNDECODED (None) with the problem noted.
        bad = msg(fld(1, 1), fld(6, "X"), fld(8, 44), fld(9, 2 ** 62), fld(10, 2 ** 40))
        g = rs.decode_grade_body(grade_body([bad, node(2, "Y", luts=[LOOK_LUT])], [edge(1, 2)]))
        by_label = {n["label"]: n for n in g["nodes"]}
        self.assertIsNone(by_label["X"]["luts"])
        self.assertIsNone(by_label["X"]["ofx"])
        self.assertEqual(by_label["Y"]["luts"], [LOOK_LUT])
        self.assertEqual(g["problems"], ["node LUT params (field 9) with wire type 0",
                                         "node OFX stack (field 10) with wire type 0"])
        absent = rs.decode_grade_body(grade_body([msg(fld(1, 1), fld(8, 44))], []))
        self.assertEqual((absent["nodes"][0]["luts"], absent["nodes"][0]["ofx"]), ([], []))
        self.assertEqual(absent["problems"], [])

    def test_graph_order_cycle_falls_back_to_id(self):
        nodes = [{"id": 1}, {"id": 2}, {"id": 3}]
        self.assertEqual(rs.graph_order(nodes, [(2, 3, 0), (3, 2, 0)]), [1, 2, 3])


class TestSnapshotAndProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                os.chmod(os.path.join(root, name), 0o755)
        self._tmp.cleanup()

    def make_project(self, name, **kw):
        folder = self.tmp / "Projects" / name
        folder.mkdir(parents=True)
        build_project_db(str(folder / "Project.db"), **kw)
        return folder

    def test_snapshot_of_read_only_source(self):
        folder = self.make_project("A & B #1")
        live = folder / "Project.db"
        before = live.read_bytes()
        os.chmod(live, stat.S_IRUSR)
        dest = str(self.tmp / "copy.db")
        rs.snapshot_db(str(live), dest)
        self.assertEqual(live.read_bytes(), before)
        con = sqlite3.connect(dest)
        self.assertEqual(con.execute("select count(*) from Sm2Timeline").fetchone()[0], 2)
        con.close()

    def test_snapshot_gives_up_on_a_locked_database(self):
        folder = self.make_project("Locked")
        live = str(folder / "Project.db")
        writer = sqlite3.connect(live, isolation_level=None)
        writer.execute("BEGIN EXCLUSIVE")
        try:
            start = time.monotonic()
            with self.assertRaises(sqlite3.OperationalError):
                rs.snapshot_db(live, str(self.tmp / "copy.db"), timeout=0.2)
            self.assertLess(time.monotonic() - start, 5)
            work = self.tmp / "work"
            work.mkdir()
            p = rs.survey_project(str(folder), str(work))
            self.assertFalse(p["read"])
            self.assertIn("locked", p["errors"][0])
        finally:
            writer.execute("ROLLBACK")
            writer.close()

    def test_survey_project(self):
        folder = self.make_project("Synthetic")
        work = self.tmp / "work"
        work.mkdir()
        p = rs.survey_project(str(folder), str(work), home="/home/u")
        self.assertTrue(p["read"])
        self.assertEqual(os.listdir(work), [], "snapshot left behind")
        self.assertEqual(p["settings"]["resolution"], "3840x2160")
        self.assertEqual(p["settings"]["fps"], 29.97)
        self.assertEqual(p["settings"]["input_color_space"], "Rec.709 Gamma 2.4")
        tl = {t["name"]: t for t in p["timelines"]}
        self.assertEqual((tl["Main"]["resolution"], tl["Main"]["fps"]), ("3840x2160", 29.97))
        self.assertEqual(tl["Main"]["items"], {"video": 2, "subtitle": 1})
        self.assertEqual((tl["Vertical"]["resolution"], tl["Vertical"]["fps"]), ("project", 24.0))
        g = p["grades"]
        self.assertEqual(g["graded"], {"clip": 3, "pool": 1, "timeline": 0, "other": 0})
        self.assertEqual(g["undecoded_grades"], 1)
        self.assertEqual(g["node_count"], {"min": 1, "median": 2, "max": 9, "grades": 3})
        self.assertEqual(g["luts"], {HOUSE_LUT: 1, LOOK_LUT: 1})
        self.assertEqual(g["ofx"], {CST: 2})
        self.assertNotIn("OLD", " ".join(g["label_sequences"]))
        self.assertEqual(p["media"]["roots"], {"/mnt/CARD_A": 1, "~/Desktop": 1, rs.UNDECODED: 1})
        self.assertEqual(p["media"]["codecs"], {"hvc1": 1, "ext RW2": 1})
        self.assertTrue(any("UNDECODED" in e for e in p["errors"]))

    def survey_mutated(self, name, *statements):
        """survey_project on the synthetic DB after running statements."""
        folder = self.make_project(name, corrupt_grade=False)
        con = sqlite3.connect(folder / "Project.db")
        for sql, params in statements:
            con.execute(sql, params)
        con.commit()
        con.close()
        work = self.tmp / ("work-" + name)
        work.mkdir()
        return rs.survey_project(str(folder), str(work), home="/home/u")

    def test_malformed_values_keep_the_project_row(self):
        set_body = 'update "ListMgt::LmVersion" set Body = ? where "ListMgt::LmVersion_id" = ?'
        huge_node = grade_body([msg(fld(1, 1), fld(6, "X"), fld(8, 44), fld(9, 2 ** 62))], [])
        p = self.survey_mutated("Huge Varint", (set_body, (huge_node, "v-2")))
        self.assertTrue(p["read"])
        self.assertEqual(p["grades"]["undecoded_lut_nodes"], 1)
        self.assertEqual(p["grades"]["luts"], {HOUSE_LUT: 1, LOOK_LUT: 1})
        self.assertIn("UNDECODED: node LUT params (field 9) with wire type 0 (1x)", p["errors"])

        p = self.survey_mutated(
            "Text Columns",
            ("update Sm2Sequence set Resolution = ? where Sm2Sequence_id = 'seq-a'", ("x" * 16,)),
            ("update Sm2Sequence set FrameRate = ? where Sm2Sequence_id = 'seq-b'", ("24",)),
            (set_body, ("GRF but stored as TEXT", "v-2")),
            ("update SM_Config set FieldsBlob = ?", ("not a blob",)),
            ("update Sm2Timeline set Name = ? where Name = 'Vertical'", (b"\xff\xfe",)),
            ("insert into BtVideoInfo values (?, ?)", ("/text/clip", 5)))
        self.assertTrue(p["read"])
        self.assertEqual(p["settings"]["resolution"], rs.UNDECODED)
        tl = {t["name"]: t for t in p["timelines"]}
        self.assertEqual(tl["Main"]["resolution"], rs.UNDECODED)
        self.assertEqual(tl[rs.UNDECODED]["fps"], rs.UNDECODED)
        self.assertEqual(p["grades"]["undecoded_grades"], 1)
        self.assertEqual(p["media"]["count"], 4)
        self.assertEqual(p["media"]["roots"][rs.UNDECODED], 2)
        text = " ".join(p["errors"])
        for expected in ("resolution stored as str", "frame rate stored as str",
                         "grade body stored as str", "QVariantMap stored as str",
                         "timeline name stored as bytes"):
            self.assertIn(expected, text)
        json.dumps(rs._json_ready({"projects": [p]}))  # nothing unserializable reached the report

    def test_unexpected_errors_are_contained(self):
        folder = self.make_project("Contained", corrupt_grade=False)
        work = self.tmp / "work"
        work.mkdir()
        real = rs.decode_grade_body

        def flaky(body):
            if rs.unwrap_blob(body).count(b"SOLO"):
                raise MemoryError("synthetic")
            return real(body)

        with mock.patch.object(rs, "decode_grade_body", flaky), \
                mock.patch.object(rs, "decode_media_clip", side_effect=TypeError("synthetic")):
            p = rs.survey_project(str(folder), str(work), home="/home/u")
        self.assertTrue(p["read"])
        self.assertEqual(p["grades"]["undecoded_grades"], 1)
        self.assertEqual(p["grades"]["graded"]["clip"], 2)
        self.assertIsNone(p["media"])
        self.assertIsNotNone(p["timelines"])
        self.assertIn("1 grade body UNDECODED: MemoryError: synthetic", p["errors"])
        self.assertIn("media: TypeError: synthetic", p["errors"])
        with mock.patch.object(rs, "survey_snapshot", side_effect=RuntimeError("synthetic")):
            p = rs.survey_project(str(folder), str(work))
        self.assertFalse(p["read"])
        self.assertEqual(p["errors"], ["survey failed: RuntimeError: synthetic"])
        self.assertEqual(os.listdir(work), [])

    def test_metadata_cache_with_text_values(self):
        cache = self.tmp / "Metadata.db"
        con = sqlite3.connect(cache)
        con.execute("create table project_metadata (key, width, height, fps)")
        con.executemany("insert into project_metadata values (?, ?, ?, ?)", [
            ("Text", "3840", 2160, "29.97"), (b"blob key", 1920, 1080, 24.0)])
        con.commit()
        con.close()
        rows, error = rs.read_metadata_cache(str(cache), str(self.tmp))
        self.assertIsNone(error)
        self.assertEqual(rows, {"Text": {"width": None, "height": 2160, "fps": None}})
        self.assertEqual(rs.cache_disagreements({"resolution": "1920x1080", "fps": 24.0},
                                                rows["Text"]), [])

    def test_missing_db_is_reported(self):
        folder = self.tmp / "Projects" / "Empty"
        folder.mkdir(parents=True)
        p = rs.survey_project(str(folder), str(self.tmp))
        self.assertFalse(p["read"])
        self.assertTrue(p["errors"])

    def test_main_end_to_end(self):
        self.make_project("First")
        self.make_project("Second", corrupt_grade=False)
        (self.tmp / "Projects" / "Folder Only").mkdir()
        out, js = self.tmp / "out" / "survey.md", self.tmp / "out" / "survey.json"
        with redirect_stdout(io.StringIO()):
            rc = rs.main(["--out", str(out), "--json", str(js), "--no-metadata-cache",
                          "--projects-dir", str(self.tmp / "Projects")])
        self.assertEqual(rc, 0)
        md = out.read_text()
        self.assertIn("| First |", md)
        self.assertIn(rs.UNDECODED, md)
        self.assertIn("Folder Only", md)
        self.assertNotIn(chr(0x2014), md)  # no em-dashes in the report
        data = json.loads(js.read_text())
        tree = data["patterns"]["house_tree"]
        self.assertEqual((tree["project_count"], tree["grade_count"]), (2, 2))
        recurring = data["patterns"]["recurring_label_sequences"]
        self.assertEqual(recurring[0]["sequence"],
                         "709 > CST IN > EXP > WB > CON > HSV SAT > CST OUT > LUT")
        self.assertEqual(recurring[0]["projects"], 2)
        luts = {r["path"]: r["count"] for r in data["patterns"]["luts"]}
        self.assertEqual(luts, {HOUSE_LUT: 2, LOOK_LUT: 2})
        self.assertNotIn("grades", data["projects"][0]["grades"])

    def test_main_rejects_unknown_project(self):
        self.make_project("Only")
        with redirect_stderr(io.StringIO()):
            rc = rs.main(["--out", str(self.tmp / "o.md"), "--no-metadata-cache",
                          "--projects-dir", str(self.tmp / "Projects"), "--projects", "Nope"])
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / "o.md").exists())

    def test_metadata_cache_cross_check(self):
        cache = self.tmp / "Metadata.db"
        con = sqlite3.connect(cache)
        con.execute("create table project_metadata (key text, width integer, height integer, fps real)")
        con.execute("insert into project_metadata values ('Synthetic', 3840, 2160, 60.0)")
        con.commit()
        con.close()
        rows, error = rs.read_metadata_cache(str(cache), str(self.tmp))
        self.assertIsNone(error)
        settings = {"resolution": "3840x2160", "fps": 29.97}
        self.assertEqual(rs.cache_disagreements(settings, rows["Synthetic"]), ["cache fps 60"])
        self.assertEqual(rs.read_metadata_cache(str(self.tmp / "none.db"), str(self.tmp))[0], {})

    def test_main_without_zstd(self):
        saved = rs.zstd
        rs.zstd = None
        try:
            with redirect_stderr(io.StringIO()):
                rc = rs.main(["--out", str(self.tmp / "o.md")])
        finally:
            rs.zstd = saved
        self.assertEqual(rc, 2)


class TestOutputGuard(unittest.TestCase):
    def test_plain_and_linked_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            (repo / ".git" / "worktrees" / "wt").mkdir(parents=True)
            (repo / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
            wt = tmp / "wt"
            wt.mkdir()
            (wt / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'wt'}\n")
            other = tmp / "other"
            (other / ".git").mkdir(parents=True)
            self.assertTrue(rs.inside_repo(repo / "new" / "dir" / "out.md", repo_dir=repo))
            self.assertTrue(rs.inside_repo(wt / "out.md", repo_dir=repo))
            self.assertFalse(rs.inside_repo(other / "out.md", repo_dir=repo))
            self.assertFalse(rs.inside_repo(tmp / "loose.md", repo_dir=repo))

    def test_symlinks_resolve_to_where_the_write_lands(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / "kept.md").write_text("repo file\n")
            outside = tmp / "outside"
            outside.mkdir()
            dangling = outside / "dangling.md"
            dangling.symlink_to(repo / "new.md")          # target not created yet
            existing = outside / "existing.md"
            existing.symlink_to(repo / "kept.md")
            linked_dir = outside / "dir"
            linked_dir.symlink_to(repo)
            escape = repo / "escape.md"
            escape.symlink_to(outside / "report.md")
            self.assertTrue(rs.inside_repo(dangling, repo_dir=repo))
            self.assertTrue(rs.inside_repo(existing, repo_dir=repo))
            self.assertTrue(rs.inside_repo(linked_dir / "out.md", repo_dir=repo))
            self.assertFalse(rs.inside_repo(escape, repo_dir=repo))

    def test_main_refuses_a_symlink_into_this_repo(self):
        here = Path(rs.__file__).resolve().parent
        if rs.git_common_dir(here) is None:
            self.skipTest("not running from a git checkout")
        target = here / "tests" / "should-not-exist.md"
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(err):
            link = Path(tmp) / "out.md"
            link.symlink_to(target)
            empty = Path(tmp) / "Projects"
            empty.mkdir()
            rc = rs.main(["--out", str(link), "--projects-dir", str(empty), "--no-metadata-cache"])
        self.assertEqual(rc, 2)
        self.assertIn("inside this repository", err.getvalue())
        self.assertFalse(target.exists())

    def test_write_report_leaves_hard_links_and_symlinks_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            twin = tmp / "twin.md"            # stands in for a file in the repo
            twin.write_text("old\n")
            out = tmp / "out.md"
            os.link(twin, out)
            rs.write_report(str(out), "report\n")
            self.assertEqual(out.read_text(), "report\n")
            self.assertEqual(twin.read_text(), "old\n")
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            real = tmp / "elsewhere" / "report.md"
            link = tmp / "link.md"
            link.symlink_to(real)
            rs.write_report(str(link), "via link\n")
            self.assertTrue(link.is_symlink())
            self.assertEqual(real.read_text(), "via link\n")
            self.assertEqual(sorted(p.name for p in tmp.rglob(".resolve-survey-*")), [])

    def test_main_refuses_output_in_this_repo(self):
        here = Path(rs.__file__).resolve().parent
        if rs.git_common_dir(here) is None:
            self.skipTest("not running from a git checkout")
        target = here / "tests" / "should-not-exist.md"
        with tempfile.TemporaryDirectory() as empty, redirect_stderr(io.StringIO()):
            # An empty projects folder keeps real Resolve data out of reach
            # even if the refusal were to fail.
            rc = rs.main(["--out", str(target), "--projects-dir", empty, "--no-metadata-cache"])
        self.assertEqual(rc, 2)
        self.assertFalse(target.exists())

    def test_refusal_does_not_depend_on_zstd(self):
        # resolve_workflow.py's end-to-end test runs the survey under
        # SURVEY_PYTHON; if that interpreter lacks zstd, the in-repo refusal
        # must still be what stops the run.
        here = Path(rs.__file__).resolve().parent
        if rs.git_common_dir(here) is None:
            self.skipTest("not running from a git checkout")
        target = here / "tests" / "should-not-exist.md"
        saved, err = rs.zstd, io.StringIO()
        rs.zstd = None
        try:
            with tempfile.TemporaryDirectory() as empty, redirect_stderr(err):
                rc = rs.main(["--out", str(target), "--projects-dir", empty, "--no-metadata-cache"])
        finally:
            rs.zstd = saved
        self.assertEqual(rc, 2)
        self.assertIn("inside this repository", err.getvalue())
        self.assertNotIn("zstd", err.getvalue())
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
