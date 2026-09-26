"""
Offline unit tests for rpresolve.detect: the decision table, the atom
parser, proxy-original lookup, output formatting, and the resolve_workflow
detect subcommand. Nothing here reads real footage or calls Resolve.

Fixtures under fixtures/detect/ are probe documents (ffprobe JSON, exiftool
-G1 -j fields, parsed atoms) reduced to the fields the rules read. Each one
carries the classification the 2026-09-26 hand-verified camera survey
expects, under "expect". test_fixtures_do_not_leak fails on any private
path, serial, location or owner string in them.

stdlib unittest only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import detect

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "detect"
WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
LEAK = re.compile(r"Clients/|Personal/|/Volumes/|ISO6709|Serial|isaacrubinstein", re.IGNORECASE)


def load(name):
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def all_fixtures():
    return sorted(FIXTURES.glob("*.json"))


# ---------------------------------------------------------------------------
# Fixture hygiene
# ---------------------------------------------------------------------------

class TestFixtureHygiene(unittest.TestCase):
    def test_fixtures_exist(self):
        self.assertGreaterEqual(len(all_fixtures()), 40)

    def test_fixtures_do_not_leak(self):
        leaks = []
        for path in all_fixtures():
            text = path.read_text(encoding="utf-8")
            for m in LEAK.finditer(text):
                leaks.append(f"{path.name}: {m.group(0)!r}")
        self.assertEqual(leaks, [], "private strings in fixtures:\n" + "\n".join(leaks))

    def test_fixtures_carry_no_location_or_ids(self):
        banned = re.compile(r"com\.apple\.quicktime\.location|GPS|SN=|camera_id|uuid|creation_time",
                            re.IGNORECASE)
        hits = [p.name for p in all_fixtures() if banned.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_fixture_paths_are_relative_and_generic(self):
        for path in all_fixtures():
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(doc["path"].startswith("/"), path.name)
            self.assertIn("expect", doc, path.name)
            self.assertIn("description", doc, path.name)


# ---------------------------------------------------------------------------
# Decision table, driven by the fixtures
# ---------------------------------------------------------------------------

class TestFixtureClassification(unittest.TestCase):
    def test_every_fixture_matches_its_expectation(self):
        for path in all_fixtures():
            doc = json.loads(path.read_text(encoding="utf-8"))
            row = detect.classify(doc)
            with self.subTest(fixture=path.name):
                for key, want in doc["expect"].items():
                    self.assertEqual(row[key], want, f"{path.name}: {key} (note: {row['note']})")

    def test_every_rule_has_a_fixture(self):
        rules = {json.loads(p.read_text(encoding="utf-8"))["expect"]["rule"] for p in all_fixtures()}
        want = {"0a", "0b", "0c", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13"}
        self.assertEqual(want - rules, set())

    def test_review_rows_have_no_input_color_space(self):
        for path in all_fixtures():
            row = detect.classify(json.loads(path.read_text(encoding="utf-8")))
            if row["profile"] == detect.REVIEW:
                with self.subTest(fixture=path.name):
                    self.assertEqual(row["input_color_space"], "")
                    self.assertEqual(row["confidence"], "low")
                    self.assertTrue(row["note"], "a review row must say what was missing")

    def test_rows_use_only_the_documented_values(self):
        for path in all_fixtures():
            row = detect.classify(json.loads(path.read_text(encoding="utf-8")))
            with self.subTest(fixture=path.name):
                self.assertEqual(tuple(row), detect.COLUMNS)
                self.assertIn(row["data_level"], ("full", "video", "unknown"))
                self.assertIn(row["vfr"], ("yes", "no", "unknown"))
                self.assertIn(row["confidence"], ("high", "medium", "low"))


class TestTraps(unittest.TestCase):
    """The traps section of the camera survey, one test each."""

    def _colr(self, doc):
        return next(t["colr"] for t in doc["atoms"]["tracks"] if t.get("handler") == "vide")

    def test_ninja_transfer_2_on_log_and_709_is_not_a_log_flag(self):
        vlog = load("r3_ninja_vlog_colr_1_2_1")
        rec709 = load("r3_ninja_rec709_colr_1_2_1")
        # Same colr on both files ...
        self.assertEqual(self._colr(vlog)["transfer"], 2)
        self.assertEqual(self._colr(rec709)["transfer"], 2)
        # ... different profiles, from the Atomos HDMI metadata.
        self.assertEqual(detect.classify(vlog)["profile"], "V-Log")
        self.assertEqual(detect.classify(rec709)["profile"], "Rec.709")

    def test_ninja_without_metadata_never_falls_back_to_colr(self):
        row = detect.classify(load("r3_ninja_no_hdmi_metadata"))
        self.assertEqual(row["profile"], detect.REVIEW)
        self.assertIn("colr transfer=2 is no log flag", row["note"])

    def test_lumix_volume_label_holding_braw_is_braw(self):
        doc = load("r1_braw_on_lumix_card_trap")
        self.assertIn("LUMIX", doc["path"])
        row = detect.classify(doc)
        self.assertEqual((row["rule"], row["profile"]), ("1", "BRAW"))
        self.assertNotIn("Panasonic", row["camera"])

    def test_vp9_img_impostor_is_not_an_iphone_original(self):
        doc = load("r13_vp9_img_impostor")
        tags = doc["ffprobe"]["format"]["tags"]
        # The impostor even says make=Apple, just not in com.apple.quicktime.*
        self.assertEqual(tags.get("make"), "Apple")
        self.assertTrue(re.match(r"^IMG_\d{4}\.MOV$", Path(doc["path"]).name))
        row = detect.classify(doc)
        self.assertEqual((row["rule"], row["profile"]), ("13", detect.REVIEW))
        self.assertIn("vp9", row["note"])

    def test_truncated_moov_is_corrupt(self):
        doc = load("r0b_truncated_moov")
        self.assertFalse(doc["atoms"]["moov"])
        row = detect.classify(doc)
        self.assertEqual((row["rule"], row["profile"]), ("0b", detect.CORRUPT))
        self.assertTrue(detect.needs_review(row))

    def test_pocket3_and_action5_share_a_name_but_not_an_encoder(self):
        p3 = detect.classify(load("r7_pocket3_review"))
        a5 = detect.classify(load("r7_action5pro_review"))
        self.assertEqual(p3["camera"], "DJI Osmo Pocket 3")
        self.assertEqual(a5["camera"], "DJI Osmo Action 5 Pro")
        self.assertEqual(p3["profile"], detect.REVIEW)
        self.assertIn("D-Log M", p3["note"])

    def test_panasonic_log_ignores_the_709_vui(self):
        doc = load("r4_gh7_hevc_xml_vlog")
        video = next(s for s in doc["ffprobe"]["streams"] if s["codec_type"] == "video")
        self.assertEqual(video["color_transfer"], "bt709")
        self.assertEqual(detect.classify(doc)["input_color_space"], detect.CS_VLOG)

    def test_zoom_ignores_bt470bg(self):
        doc = load("r11_zoom")
        video = next(s for s in doc["ffprobe"]["streams"] if s["codec_type"] == "video")
        self.assertEqual(video["color_primaries"], "bt470bg")
        self.assertEqual(detect.classify(doc)["input_color_space"], detect.CS_REC709)

    def test_braw_proxy_ignores_the_709_tag(self):
        row = detect.classify(load("r2_braw_proxy_orphan"))
        self.assertEqual(row["input_color_space"], detect.CS_BMD_FILM_GEN5)
        self.assertEqual(row["confidence"], "medium")


class TestRuleOrder(unittest.TestCase):
    def test_render_named_like_a_camera_file_is_derived(self):
        row = detect.classify(load("r0a_resolve_render"))
        self.assertEqual(row["rule"], "0a")

    def test_derived_wins_over_panasonic(self):
        doc = load("r4_gh7_hevc_xml_vlog")
        doc["ffprobe"]["format"]["tags"]["encoder"] = "Lavf61.7.100"
        self.assertEqual(detect.classify(doc)["rule"], "0a")

    def test_proxy_folder_wins_over_braw_proxy_rule(self):
        self.assertEqual(detect.classify(load("r0c_braw_proxy_beside_original"))["rule"], "0c")
        self.assertEqual(detect.classify(load("r2_braw_proxy_orphan"))["rule"], "2")

    def test_blackmagic_cam_wins_over_iphone(self):
        doc = load("r5_bmcam_rec709")
        doc["ffprobe"]["format"]["tags"]["com.apple.quicktime.make"] = "Apple"
        self.assertEqual(detect.classify(doc)["rule"], "5")

    def test_empty_document_is_unknown(self):
        row = detect.classify({"path": "x/clip.mov"})
        self.assertEqual((row["rule"], row["profile"], row["vfr"]), ("13", detect.REVIEW, "unknown"))


class TestHeaders(unittest.TestCase):
    def test_handler_names_lose_pascal_length_bytes(self):
        h = detect.Headers(load("r8_drone_fc7703"))
        self.assertIn("DJI.AVC", h.handlers())

    def test_vfr_compares_rationals(self):
        doc = {"ffprobe": {"streams": [{"codec_type": "video", "r_frame_rate": "30000/1001",
                                        "avg_frame_rate": "60000/2002"}]}}
        self.assertEqual(detect.Headers(doc).vfr(), "no")
        doc["ffprobe"]["streams"][0]["avg_frame_rate"] = "0/0"
        self.assertEqual(detect.Headers(doc).vfr(), "unknown")

    def test_data_level_falls_back_to_nclx_flag(self):
        doc = {"ffprobe": {"streams": [{"codec_type": "video"}]},
               "atoms": {"tracks": [{"handler": "vide", "colr": {"type": "nclx", "primaries": 1,
                                                                 "transfer": 1, "matrix": 1,
                                                                 "full_range": True}}]}}
        self.assertEqual(detect.Headers(doc).data_level(), "full")

    def test_dji_cover_art_is_not_the_video_stream(self):
        h = detect.Headers(load("r7_pocket3_review"))
        self.assertEqual(h.video["codec_name"], "hevc")


# ---------------------------------------------------------------------------
# Atom parser on synthetic files
# ---------------------------------------------------------------------------

def box(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def video_entry(fourcc, children, vendor=b"appl", compressor=b"Apple ProRes 422 HQ"):
    fields = (b"\x00" * 6 + b"\x00\x01"               # reserved, data reference index
              + b"\x00\x00\x00\x00" + vendor            # version, revision, vendor
              + b"\x00" * 8                             # temporal and spatial quality
              + struct.pack(">HH", 3840, 2160)
              + b"\x00\x48\x00\x00\x00\x48\x00\x00"     # 72 dpi, 72 dpi
              + b"\x00" * 4 + b"\x00\x01"               # data size, frame count
              + bytes([len(compressor)]) + compressor.ljust(31, b"\x00")
              + b"\x00\x18\xff\xff")                    # depth 24, colour table -1
    return box(fourcc, fields + b"".join(children))


def hdlr(kind, name):
    return box(b"hdlr", b"\x00" * 8 + kind + b"\x00" * 12 + bytes([len(name)]) + name)


def trak(kind, name, entry):
    stsd = box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + entry)
    stbl = box(b"stbl", stsd)
    minf = box(b"minf", hdlr(b"alis", b"Core Media Data Handler") + stbl)
    return box(b"trak", box(b"mdia", hdlr(kind, name) + minf))


def mov(children, moov=True, mdat=b"\x00" * 64):
    ftyp = box(b"ftyp", b"qt  " + b"\x00\x00\x00\x00" + b"qt  ")
    data = ftyp + box(b"mdat", mdat)
    if moov:
        data += box(b"moov", box(b"mvhd", b"\x00" * 100) + b"".join(children))
    return data


def nested_trak(levels):
    """A malformed trak holding `levels` stbl boxes, each inside the last."""
    inner = b""
    for _ in range(levels):
        inner = box(b"stbl", inner)
    return box(b"trak", inner)


class TestReadAtoms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_apple_log_colr_and_logs(self):
        colr = box(b"colr", b"nclc" + struct.pack(">HHH", 9, 2, 9))
        logs = box(b"logs", detect.APPLE_LOG_TAG.encode())
        video = trak(b"vide", b"Core Media Video", video_entry(b"apch", [colr, box(b"fiel", b"\x01\x00"), logs]))
        audio = trak(b"soun", b"Core Media Audio", box(b"lpcm", b"\x00" * 28))
        atoms = detect.read_atoms(self.write("log.mov", mov([video, audio])))
        self.assertEqual(atoms["ftyp"]["major"], "qt  ")
        self.assertTrue(atoms["moov"])
        v = atoms["tracks"][0]
        self.assertEqual((v["handler"], v["handler_name"], v["fourcc"]), ("vide", "Core Media Video", "apch"))
        self.assertEqual((v["vendor"], v["compressor"]), ("appl", "Apple ProRes 422 HQ"))
        self.assertEqual(v["colr"], {"type": "nclc", "primaries": 9, "transfer": 2, "matrix": 9})
        self.assertEqual(v["logs"], detect.APPLE_LOG_TAG)
        self.assertEqual(atoms["tracks"][1]["fourcc"], "lpcm")

    def test_nclx_full_range_and_dolby_vision(self):
        colr = box(b"colr", b"nclx" + struct.pack(">HHH", 9, 18, 9) + b"\x80")
        dvvc = box(b"dvvC", b"\x01\x00\x10\x25\x40" + b"\x00" * 19)
        video = trak(b"vide", b"Core Media Video", video_entry(b"hvc1", [dvvc, colr], vendor=b"\x00" * 4, compressor=b"HEVC"))
        v = detect.read_atoms(self.write("hlg.mov", mov([video])))["tracks"][0]
        self.assertTrue(v["colr"]["full_range"])
        self.assertEqual(v["colr"]["transfer"], 18)
        self.assertEqual(v["dolby_vision"], {"profile": 8, "compatibility_id": 4})

    def test_missing_moov(self):
        atoms = detect.read_atoms(self.write("cut.mov", mov([], moov=False)))
        self.assertFalse(atoms["moov"])
        self.assertEqual(atoms["tracks"], [])

    def test_mdat_size_past_end_of_file(self):
        data = box(b"ftyp", b"qt  \x00\x00\x00\x00") + struct.pack(">I4s", 10_000_000, b"mdat") + b"\x00" * 32
        atoms = detect.read_atoms(self.write("short.mov", data))
        self.assertFalse(atoms["moov"])

    def test_empty_and_missing_files(self):
        self.assertFalse(detect.read_atoms(self.write("empty.mov", b""))["moov"])
        self.assertIn("error", detect.read_atoms(os.path.join(self.tmp.name, "nope.mov")))

    def test_deeply_nested_containers_stop_at_the_depth_cap(self):
        # 5,000 nested stbl boxes once raised RecursionError out of read_atoms.
        colr = box(b"colr", b"nclc" + struct.pack(">HHH", 1, 1, 1))
        video = trak(b"vide", b"Core Media Video", video_entry(b"apch", [colr]))
        atoms = detect.read_atoms(self.write("deep.mov", mov([nested_trak(5000), video])))
        self.assertNotIn("error", atoms)
        bad, good = atoms["tracks"]
        self.assertIn("nest past", bad["error"])
        self.assertNotIn("fourcc", bad)
        # The real track beside it, four containers deep, still parses.
        self.assertEqual((good["fourcc"], good["colr"]["transfer"]), ("apch", 1))
        self.assertNotIn("error", good)

    def test_real_parse_feeds_classify(self):
        colr = box(b"colr", b"nclc" + struct.pack(">HHH", 9, 2, 9))
        logs = box(b"logs", detect.APPLE_LOG_TAG.encode())
        video = trak(b"vide", b"Core Media Video", video_entry(b"apch", [colr, logs]))
        path = self.write("IMG_0001.MOV", mov([video]))
        doc = {"path": path, "atoms": detect.read_atoms(path),
               "ffprobe": {"format": {"tags": {"com.apple.quicktime.make": "Apple",
                                               "com.apple.quicktime.model": "iPhone 16 Pro Max"}},
                           "streams": [{"codec_type": "video", "codec_name": "prores",
                                        "color_range": "tv", "r_frame_rate": "24/1",
                                        "avg_frame_rate": "24/1"}]}}
        row = detect.classify(doc)
        self.assertEqual((row["profile"], row["input_color_space"]), ("Apple Log", detect.CS_APPLE_LOG))


# ---------------------------------------------------------------------------
# Files on disk: proxy originals and collection
# ---------------------------------------------------------------------------

class TestFilesOnDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def touch(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return str(p)

    def test_proxy_original_in_sibling_folder(self):
        self.touch("01-Camera/BRAW/A001_01011200_C001.braw")
        proxy = self.touch("01-Camera/Proxy/A001_01011200_C001.mp4")
        self.assertEqual(detect.find_proxy_original(proxy),
                         os.path.join("..", "BRAW", "A001_01011200_C001.braw"))

    def test_proxy_original_in_parent_folder(self):
        self.touch("cam/A001_01011000_C004.mov")
        proxy = self.touch("cam/Proxy/A001_01011000_C004.mov")
        self.assertEqual(detect.find_proxy_original(proxy), os.path.join("..", "A001_01011000_C004.mov"))

    def test_proxy_without_original_or_outside_proxy_folder(self):
        orphan = self.touch("cam/Proxy/A001_01011000_C009.mov")
        self.assertIsNone(detect.find_proxy_original(orphan))
        self.touch("cam/B/A001_01011000_C010.mov")
        plain = self.touch("cam/A/A001_01011000_C010.mov")
        self.assertIsNone(detect.find_proxy_original(plain))

    def test_appledouble_twin_is_no_original(self):
        self.touch("cam/._A001_01011000_C011.braw")
        proxy = self.touch("cam/Proxy/A001_01011000_C011.mp4")
        self.assertIsNone(detect.find_proxy_original(proxy))

    def test_collect_skips_appledouble_hidden_and_non_video(self):
        keep = [self.touch("card/DCIM/100_PANA/P1000001.MOV"), self.touch("card/DJI_0001.LRF")]
        self.touch("card/DCIM/100_PANA/._P1000001.MOV")
        self.touch("card/.Spotlight-V100/x.mov")
        self.touch("card/#recycle/old.mov")
        self.touch("card/notes.txt")
        self.touch("card/DCIM/100_PANA/P1000001.JPG")
        files, missing = detect.collect_files([str(self.root / "card"), str(self.root / "gone.mov")])
        self.assertEqual(sorted(files), sorted(keep))
        self.assertEqual(missing, [str(self.root / "gone.mov")])

    def test_explicit_file_is_kept_whatever_its_extension(self):
        odd = self.touch("clip.xyz")
        self.assertEqual(detect.collect_files([odd])[0], [odd])


# ---------------------------------------------------------------------------
# Output and tools
# ---------------------------------------------------------------------------

class TestOutput(unittest.TestCase):
    def test_tsv_header_and_cell_cleaning(self):
        row = detect.classify(load("r11_zoom"))
        row["note"] = "two\tparts\nand a line"
        text = detect.format_tsv([row])
        lines = text.splitlines()
        self.assertEqual(lines[0].split("\t"), list(detect.COLUMNS))
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(lines[1].split("\t")), len(detect.COLUMNS))
        self.assertTrue(lines[1].endswith("two parts and a line"))

    def test_json_round_trip(self):
        rows = [detect.classify(load("r10_obs")), detect.classify(load("r9_gopro_hero9"))]
        self.assertEqual(json.loads(detect.format_json(rows)), rows)
        self.assertIn("1 review", detect.summarize(rows))


class TestTools(unittest.TestCase):
    def test_missing_tools_are_named(self):
        with self.assertRaises(detect.ToolMissing) as ctx:
            detect.check_tools("/nonexistent/ffprobe", "/nonexistent/exiftool")
        msg = str(ctx.exception)
        self.assertIn("/nonexistent/ffprobe", msg)
        self.assertIn("/nonexistent/exiftool", msg)

    def test_exiftool_arg_guards_leading_dash(self):
        self.assertEqual(detect._exiftool_arg("-odd.mov"), "./-odd.mov")
        self.assertEqual(detect._exiftool_arg("/a/b.mov"), "/a/b.mov")


def fake_tool(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write("#!/usr/bin/python3\nimport json, sys\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


FAKE_FFPROBE = r'''
path = sys.argv[-1]
if path.endswith("cut.mov"):
    sys.stderr.write("[mov,mp4,m4a,3gp,3g2,mj2 @ 0x0] moov atom not found\n")
    print("{}")
    sys.exit(1)
tags = {"encoder": "OBS Studio (32.1.2)"}
print(json.dumps({"format": {"tags": tags}, "streams": [{"codec_type": "video", "codec_name": "h264",
      "color_range": "tv", "r_frame_rate": "60/1", "avg_frame_rate": "60/1"}]}))
'''

FAKE_EXIFTOOL = r'''
files = [a for a in sys.argv[1:] if not a.startswith("-") and a != "LargeFileSupport=1"]
print(json.dumps([{"SourceFile": f, "QuickTime:MajorBrand": "Apple QuickTime (.MOV/QT)"} for f in files]))
'''


class TestDetectPathsWithFakeTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.ffprobe = fake_tool(d, "ffprobe", FAKE_FFPROBE)
        self.exiftool = fake_tool(d, "exiftool", FAKE_EXIFTOOL)
        self.media = os.path.join(d, "media")
        os.makedirs(self.media)
        for name in ("2026-01-01 12-00-00.mov", "cut.mov"):
            with open(os.path.join(self.media, name), "wb") as f:
                f.write(mov([], moov=False))

    def test_detect_paths(self):
        rows, missing = detect.detect_paths([self.media], ffprobe=self.ffprobe, exiftool=self.exiftool)
        self.assertEqual(missing, [])
        by_name = {os.path.basename(r["path"]): r for r in rows}
        self.assertEqual(by_name["2026-01-01 12-00-00.mov"]["rule"], "10")
        self.assertEqual(by_name["cut.mov"]["rule"], "0b")

    def test_cli_exit_status_and_tsv(self):
        env = dict(os.environ, RPRESOLVE_FFPROBE=self.ffprobe, RPRESOLVE_EXIFTOOL=self.exiftool)
        out = os.path.join(self.tmp.name, "detect.tsv")
        proc = subprocess.run([sys.executable, str(WORKFLOW), "detect", self.media, "--out", out],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)  # the truncated file needs attention
        with open(out, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0].split("\t"), list(detect.COLUMNS))
        self.assertEqual(len(lines), 3)
        self.assertIn("1 corrupt", proc.stderr)

        clean = os.path.join(self.media, "2026-01-01 12-00-00.mov")
        proc = subprocess.run([sys.executable, str(WORKFLOW), "detect", clean, "--json"],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)[0]["profile"], "Rec.709")

    def test_cli_writes_the_table_past_a_malformed_file(self):
        with open(os.path.join(self.media, "deep.mov"), "wb") as f:
            f.write(mov([nested_trak(5000)]))
        env = dict(os.environ, RPRESOLVE_FFPROBE=self.ffprobe, RPRESOLVE_EXIFTOOL=self.exiftool)
        out = os.path.join(self.tmp.name, "detect.tsv")
        proc = subprocess.run([sys.executable, str(WORKFLOW), "detect", self.media, "--out", out],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 4)

    def test_a_file_that_raises_becomes_a_review_row(self):
        real = detect.classify

        def classify(doc):
            if doc["path"].endswith("cut.mov"):
                raise ValueError("synthetic parse failure")
            return real(doc)

        with mock.patch.object(detect, "classify", classify):
            rows, _ = detect.detect_paths([self.media], ffprobe=self.ffprobe, exiftool=self.exiftool)
        by_name = {os.path.basename(r["path"]): r for r in rows}
        self.assertEqual(by_name["2026-01-01 12-00-00.mov"]["rule"], "10")
        bad = by_name["cut.mov"]
        self.assertEqual((bad["rule"], bad["profile"], bad["input_color_space"]), ("13", detect.REVIEW, ""))
        self.assertIn("ValueError: synthetic parse failure", bad["note"])
        self.assertEqual(tuple(bad), detect.COLUMNS)

    def test_missing_tool_mid_batch_still_stops_the_run(self):
        def run_ffprobe(path, ffprobe=None):
            raise detect.ToolMissing("cannot run ffprobe: gone")

        with mock.patch.object(detect, "run_ffprobe", run_ffprobe):
            with self.assertRaises(detect.ToolMissing):
                detect.detect_paths([self.media], ffprobe=self.ffprobe, exiftool=self.exiftool)

    def test_cli_missing_tools_exit_1(self):
        env = dict(os.environ, RPRESOLVE_FFPROBE="/nonexistent/ffprobe",
                   RPRESOLVE_EXIFTOOL=self.exiftool)
        proc = subprocess.run([sys.executable, str(WORKFLOW), "detect", self.media],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ffprobe not found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
