"""
The card record: loader, validator, the five standards and the card command, on synthetic
folders only. Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROD = HERE.parent
sys.path.insert(0, str(PROD))

from rpresolve import card, cmd_card, sidecar  # noqa: E402

REAL_READ_CAPTURE = card.read_capture  # Base stubs card.read_capture; these tests want the real one

CARD_ID = "0123456789ab"
LHASH = "f" * 64


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_card(parent, name="CAMA-012345", cam="CAMA", files=None, record=True, sums=True,
              offsite=False, schema2=False, braw_proxy=False):
    """A synthetic card copy with the sidecars offload.sh would write."""
    files = files or {"DCIM/100/AAA0001.MOV": b"one", "DCIM/100/AAA0002.MOV": b"twotwo"}
    root = Path(parent) / name
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    if sums:
        (Path(parent) / (name + ".SHA256SUMS")).write_text(
            "".join(f"{sha(d)}  {r}\n" for r, d in sorted(files.items())))
    if record:
        rec = {"card_id": CARD_ID, "listing_hash": LHASH, "volume_uuid": "U", "cam": cam,
               "files": len(files), "bytes": sum(len(d) for d in files.values()),
               "offloaded_utc": "2026-01-02T03:04:05Z"}
        if schema2:
            rec.update(schema=dict(card.SCHEMA), origin="offload",
                       shoot={"first_utc": None, "last_utc": None, "days": [], "cameras": []},
                       project=None, answers={},
                       clips=card.derive_clips(f"{cam}-{CARD_ID[:6]}", sorted(files)), history=[])
        (Path(parent) / (name + ".card.json")).write_text(json.dumps(rec, indent=2) + "\n")
    if offsite:
        (Path(parent) / (name + ".offsite.txt")).write_text(
            f"card_id {CARD_ID}\nremote b2:bucket\nchecked_utc 2026-01-03T00:00:00Z\n")
    return root


def run_cmd(*argv):
    out, err = io.StringIO(), io.StringIO()
    p = __import__("argparse").ArgumentParser()
    cmd_card.add_arguments(p)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cmd_card.run(p.parse_args(list(argv)))
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cardtest-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # no exiftool or ffprobe on the synthetic bytes: shoot falls back to file times
        patch = mock.patch.object(card, "read_capture", lambda paths: {p: (None, "") for p in paths})
        patch.start()
        self.addCleanup(patch.stop)


class Loader(Base):
    def test_schema1_loads_and_validates(self):
        root = make_card(self.tmp)
        c = card.load(str(root / "DCIM/100/AAA0001.MOV"))
        self.assertEqual((c["version"], c["problems"], c["name"]), (1, [], "CAMA-012345"))
        self.assertEqual([k["clip_id"] for k in c["clips"]], ["CAMA-012345/AAA0001", "CAMA-012345/AAA0002"])
        self.assertIn("squeeze CAMA-012345/AAA0001", c["open"])

    def test_found_from_beside_the_folder(self):
        root = make_card(self.tmp)
        for p in (root, str(root) + ".card.json", str(root) + ".SHA256SUMS", root / "DCIM"):
            self.assertEqual(card.load(str(p))["root"], str(root))
        with self.assertRaises(card.CardError):
            card.load(self.tmp)

    def test_schema2_and_validation(self):
        make_card(self.tmp, schema2=True)
        c = card.load(os.path.join(self.tmp, "CAMA-012345"))
        self.assertEqual((c["version"], c["problems"]), (2, []))
        rec = dict(c["record"])
        for bad, why in ((dict(rec, origin="x"), "origin"), (dict(rec, project="Bad Slug"), "project"),
                         (dict(rec, extra=1), "unknown"), (dict(rec, cam="x"), "cam"),
                         (dict(rec, clips=rec["clips"] * 2), "twice"),
                         (dict(rec, answers={"squeeze": {"default": 9, "clips": {}, "by": "p", "at": None}}), "squeeze"),
                         (dict(rec, answers={"squeeze": {"default": None, "clips": {"CAMA-012345/NOPE": 1.5},
                                                         "by": "p", "at": None}}), "not a clip")):
            self.assertTrue(any(why in p for p in card.validate(bad)), (why, card.validate(bad)))
        self.assertEqual(card.validate({k: v for k, v in rec.items() if k != "clips"}), ["clips missing"])

    def test_legacy_squeeze_folds_in(self):
        make_card(self.tmp)
        Path(self.tmp, "CAMA-012345.squeeze.json").write_text(
            json.dumps({"default": 1, "clips": {"AAA0002.MOV": 1.5}}))
        c = card.load(os.path.join(self.tmp, "CAMA-012345"))
        self.assertEqual(c["answers"]["squeeze"]["clips"], {"CAMA-012345/AAA0002": 1.5})
        self.assertEqual(c["open"], ["offsite", "project"])
        self.assertEqual(card.squeeze_answer(c, c["clips"][1]), (1.5, "record"))

    def test_proxy_belongs_to_its_original(self):
        files = {"X/clip1.braw": b"a", "X/Proxy/clip1.mp4": b"b", "X/Proxy/lone.mp4": b"c", "X/notes.txt": b"d"}
        clips = card.derive_clips("P-abcdef", sorted(files))
        self.assertEqual([(c["clip_id"], c["proxy"]) for c in clips],
                         [("P-abcdef/clip1", "X/Proxy/clip1.mp4")])
        self.assertEqual(card.split_clips("P-abcdef", sorted(files))[1], ["X/Proxy/lone.mp4"])

    def test_a_proxy_folder_far_from_its_originals_and_a_mixed_card(self):
        files = {"Rolls/a/A001_C001.braw": b"1", "Rolls/b/A001_C002.braw": b"2", "Proxy/A001_C001.mp4": b"p",
                 "Proxy/a001_c002.MP4": b"q", "Proxy/NOPE.mp4": b"r", "DCIM/100/P0001.MOV": b"m",
                 "DCIM/100/proxy/P0001.mp4": b"x"}
        clips, orphans = card.split_clips("Z-000000", sorted(files))
        self.assertEqual([(c["clip_id"], c["proxy"]) for c in clips],
                         [("Z-000000/P0001", "DCIM/100/proxy/P0001.mp4"),
                          ("Z-000000/A001_C001", "Proxy/A001_C001.mp4"), ("Z-000000/A001_C002", None)])  # stems match exactly
        self.assertEqual(orphans, ["Proxy/NOPE.mp4", "Proxy/a001_c002.MP4"])

    def test_no_record_is_an_s1_failure(self):
        make_card(self.tmp, record=False)
        self.assertEqual(card.check(card.load(os.path.join(self.tmp, "CAMA-012345")))["S1"],
                         ["no card.json beside the folder"])


class SumsPaths(Base):
    def test_dot_slash_repeated_dot_slash_and_binary_mark_verify(self):
        root = make_card(self.tmp, offsite=True, schema2=True)
        sums = Path(self.tmp, "CAMA-012345.SHA256SUMS")
        lines = sums.read_text().splitlines()
        h1, _, n1 = lines[0].partition("  ")
        h2, _, n2 = lines[1].partition("  ")
        text = f"{h1}  ./{n1}\n{h2} *././{n2}\n"
        sums.write_text(text)
        c = card.load(str(root))
        self.assertEqual(sorted(c["sums"]["files"]), sorted([n1, n2]))
        self.assertEqual(c["sums"]["bad"], [])
        self.assertEqual(card.check(c)["S2"], [])
        self.assertEqual(sidecar.source_sha256(str(root / n1)), h1)
        self.assertEqual(sums.read_text(), text)  # never rewritten

    def test_retrofit_accepts_a_dot_slash_file_and_still_refuses_a_bad_one(self):
        root = make_card(self.tmp, record=False)
        sums = Path(self.tmp, "CAMA-012345.SHA256SUMS")
        sums.write_text("".join(f"{h}  ./{n}\n" for h, _, n in (l.partition("  ") for l in sums.read_text().splitlines())))
        self.assertEqual(run_cmd("init", "--retrofit", "--cam", "RETRO", str(root), "--apply")[0], 0)
        self.assertEqual(card.validate(json.loads(Path(str(root) + ".card.json").read_text())), [])
        self.assertTrue(sums.read_text().startswith(sums.read_text()[:64] + "  ./"))

    def test_an_orphan_proxy_is_a_note_not_a_failure(self):
        files = {"A/c1.MOV": b"1", "Proxy/c1.mp4": b"p", "Proxy/lost.mp4": b"l"}
        root = make_card(self.tmp, files=files, schema2=True, offsite=True)
        Path(self.tmp, "CAMA-012345.squeeze.json").write_text(json.dumps({"default": 1}))
        rc, out, _ = run_cmd("check", str(root))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "PASS  " + str(root) + "  S1 S2 S3 S4 S5  (note: 1 proxy file(s) with no original)")
        self.assertIn("note      proxy with no original: Proxy/lost.mp4", run_cmd("show", str(root))[1])


def capture_of(table):
    """A read_capture stand-in: {file name: (datetime, camera)} for the clips of a test card."""
    return lambda paths: {p: table.get(os.path.basename(p), (None, "")) for p in paths}


class ShootFromCapture(Base):
    FILES = {"DCIM/100/AAA0001.MOV": b"1", "DCIM/100/AAA0002.MOV": b"2", "DCIM/101/AAA0003.MOV": b"3"}

    def card(self, **kw):
        root = make_card(self.tmp, files=self.FILES, schema2=True, **kw)
        for r in self.FILES:  # every file was copied on the same day, long after the shoot
            os.utime(root / r, (1893456000, 1893456000))  # 2030-01-01
        return root

    def test_capture_times_beat_file_times_and_cameras_are_sorted(self):
        import datetime as dt
        root = self.card()
        table = {"AAA0001.MOV": (dt.datetime(2026, 11, 7, 9, 30), "Maker Zed"),
                 "AAA0002.MOV": (dt.datetime(2026, 11, 5, 23, 59, 58), "Maker Alpha"),
                 "AAA0003.MOV": (dt.datetime(2026, 11, 7, 18, 0), "Maker Zed")}
        with mock.patch.object(card, "read_capture", capture_of(table)):
            sh = card.shoot_from(str(root), card.load(str(root))["media"])
        self.assertEqual(sh, {"first_utc": "2026-11-05T23:59:58Z", "last_utc": "2026-11-07T18:00:00Z",
                              "days": ["2026-11-05", "2026-11-07"], "cameras": ["Maker Alpha", "Maker Zed"],
                              "source": "capture"})
        self.assertEqual(card._shoot_problems(sh), [])

    def test_file_times_only_when_no_clip_has_a_capture_time(self):
        root = self.card()
        sh = card.shoot_from(str(root), card.load(str(root))["media"])
        self.assertEqual((sh["source"], sh["days"], sh["cameras"]), ("file-times", ["2030-01-01"], []))
        self.assertEqual(card._shoot_problems(sh), [])
        self.assertEqual(card._shoot_problems({k: v for k, v in sh.items() if k != "source"}), [])  # older records
        self.assertEqual(card._shoot_problems(dict(sh, source="guess")), ["shoot.source must be capture or file-times"])

    def test_refresh_changes_only_the_shoot_and_set_applies_it(self):
        import datetime as dt
        root = self.card()
        before = json.loads(Path(str(root) + ".card.json").read_text())
        table = {"AAA0001.MOV": (dt.datetime(2026, 1, 2, 3, 4, 5), "Maker Q")}
        with mock.patch.object(card, "read_capture", capture_of(table)):
            rec, how = card.refresh_shoot(card.load(str(root)))
            self.assertEqual(how, "capture")
            self.assertEqual({k: v for k, v in rec.items() if k != "shoot"},
                             {k: v for k, v in before.items() if k != "shoot"})
            self.assertEqual(rec["shoot"]["days"], ["2026-01-02"])
            rc, out, _ = run_cmd("set", str(root), "--refresh-shoot")
            self.assertEqual((rc, json.loads(Path(str(root) + ".card.json").read_text())), (0, before))
            self.assertIn("shoot from capture", out)
            self.assertEqual(run_cmd("set", str(root), "--refresh-shoot", "--apply")[0], 0)
        after = json.loads(Path(str(root) + ".card.json").read_text())
        self.assertEqual(after["shoot"]["source"], "capture")
        self.assertEqual(card.validate(after), [])

    def stills_card(self, with_video=False):
        files = {"DCIM/100/IMG_0001.JPG": b"1", "DCIM/100/IMG_0002.dng": b"2", "DCIM/100/Proxy/x.jpg": b"p"}
        if with_video:
            files["DCIM/101/AAA0001.MOV"] = b"v"
        shutil.rmtree(self.tmp); os.mkdir(self.tmp)
        root = make_card(self.tmp, files=files, schema2=True)
        for r in files:
            os.utime(root / r, (1893456000, 1893456000))
        return root

    def test_a_stills_only_card_reads_its_photos_and_names_the_camera(self):
        import datetime as dt
        root = self.stills_card()
        c = card.load(str(root))
        self.assertEqual((c["clips"], c["media"]), ([], ["DCIM/100/IMG_0001.JPG", "DCIM/100/IMG_0002.dng", "DCIM/100/Proxy/x.jpg"]))
        table = {"IMG_0001.JPG": (dt.datetime(2026, 6, 1, 7, 0), "Maker S1"), "IMG_0002.dng": (dt.datetime(2026, 6, 3, 8, 0), "Maker S1")}
        with mock.patch.object(card, "read_capture", capture_of(table)):
            rec, how = card.refresh_shoot(c)
        self.assertEqual((how, rec["shoot"]["days"], rec["shoot"]["cameras"]), ("capture", ["2026-06-01", "2026-06-03"], ["Maker S1"]))

    def test_a_mixed_card_takes_the_videos_and_the_stills(self):
        import datetime as dt
        root = self.stills_card(with_video=True)
        table = {"IMG_0001.JPG": (dt.datetime(2026, 6, 1, 7, 0), "Maker S1"), "AAA0001.MOV": (dt.datetime(2026, 5, 30, 9, 0), "Maker V2")}
        with mock.patch.object(card, "read_capture", capture_of(table)):
            sh = card.shoot_from(str(root), card.load(str(root))["media"])
        self.assertEqual((sh["first_utc"], sh["days"], sh["cameras"]), ("2026-05-30T09:00:00Z", ["2026-05-30", "2026-06-01"], ["Maker S1", "Maker V2"]))

    def test_a_refresh_never_swaps_a_real_shoot_for_file_times_or_nothing(self):
        root = self.stills_card()
        real = {"first_utc": "2026-06-01T07:00:00Z", "last_utc": "2026-06-03T08:00:00Z",
                "days": ["2026-06-01", "2026-06-03"], "cameras": ["Maker S1"], "source": "capture"}
        rec_path = str(root) + ".card.json"
        rec = json.loads(Path(rec_path).read_text())
        Path(rec_path).write_text(json.dumps(dict(rec, shoot=real)))
        c = card.load(str(root))
        new, how = card.refresh_shoot(c)  # no capture times anywhere (the stub): kept
        self.assertEqual((how, new["shoot"]), ("kept", real))
        rc, out, _ = run_cmd("set", str(root), "--refresh-shoot")
        self.assertIn("shoot kept as it is", out)
        self.assertEqual(run_cmd("set", str(root), "--refresh-shoot", "--apply")[0], 0)
        self.assertEqual(json.loads(Path(rec_path).read_text())["shoot"], real)
        # a file-times shoot is replaced by fresh file times; so is an empty one
        for old in (dict(real, source="file-times"), dict(real, first_utc=None, last_utc=None, days=[], cameras=[])):
            Path(rec_path).write_text(json.dumps(dict(rec, shoot=old)))
            new, how = card.refresh_shoot(card.load(str(root)))
            self.assertEqual((how, new["shoot"]["source"], new["shoot"]["days"]), ("file-times", "file-times", ["2030-01-01"]))
        # a card with no media at all keeps what it has
        Path(rec_path).write_text(json.dumps(dict(rec, shoot=real)))
        for r in ("DCIM/100/IMG_0001.JPG", "DCIM/100/IMG_0002.dng", "DCIM/100/Proxy/x.jpg"):
            os.remove(root / r)
        Path(str(root) + ".SHA256SUMS").write_text("")
        self.assertEqual(card.refresh_shoot(card.load(str(root)))[1], "kept")

    def test_read_capture_prefers_exiftool_tags_in_order_then_ffprobe(self):
        import datetime as dt
        from rpresolve import detect
        a, b, c, d = (os.path.join(self.tmp, n) for n in ("a.mov", "b.mov", "c.mov", "d.mov"))
        exif = {a: {"QuickTime:CreateDate": "2026:03:01 01:00:00", "QuickTime:CreationDate": "2026:03:02 08:00:00-05:00",
                    "IFD0:Make": "Maker", "IFD0:Model": "Maker X1"},
                b: {"IFD0:Model": "Plain"}}
        probe = {c: {"format": {"tags": {"creation_time": "2026-04-05T06:07:08.000000Z"}}}}
        with mock.patch.object(detect, "EXIFTOOL", "/bin/sh"), \
                mock.patch.object(detect, "run_exiftool", lambda paths, exiftool=None: exif), \
                mock.patch.object(detect, "run_ffprobe", lambda p: (probe.get(p), "")):
            got = REAL_READ_CAPTURE([a, b, c, d])
        self.assertEqual(got, {a: (dt.datetime(2026, 3, 2, 8, 0), "Maker X1"),  # CreationDate wins; no doubled make
                               b: (None, "Plain"), c: (dt.datetime(2026, 4, 5, 6, 7, 8), ""), d: (None, "")})

    def test_read_capture_real_ffprobe_over_a_clip_whose_file_time_disagrees(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("needs ffmpeg")
        from rpresolve import detect
        clip = os.path.join(self.tmp, "t.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1:r=10",
                        "-metadata", "creation_time=2025-03-04T05:06:07Z", clip], check=True)
        os.utime(clip, (1893456000, 1893456000))
        with mock.patch.object(detect, "EXIFTOOL", "/nonexistent/exiftool"), \
                mock.patch.object(detect, "FFPROBE", shutil.which("ffprobe")), \
                mock.patch("shutil.which", lambda n: None if n == "exiftool" else "/usr/bin/" + n):
            got = REAL_READ_CAPTURE([clip])
        self.assertEqual(got[clip][0].isoformat(), "2025-03-04T05:06:07")


class CardKey(Base):
    def test_record_names_a_folder_whatever_it_is_called(self):
        root = make_card(self.tmp, name="2026-10-03-OLD-FOLDER")
        key = sidecar.card_key(str(root / "DCIM/100/AAA0001.MOV"))
        self.assertEqual(key, "CAMA-012345/AAA0001")

    def test_unrecorded_folders_keep_the_old_rules(self):
        self.assertEqual(sidecar.card_key("/x/S5II-a1b2c3/DCIM/100_PANA/P1.MOV"), "S5II-a1b2c3/P1")
        self.assertEqual(sidecar.card_key("/x/loose/P1.MOV"), "loose/P1")


class Standards(Base):
    def standards(self, **kw):
        root = make_card(self.tmp, **kw)
        c = card.load(str(root))
        return {k: v for k, v in card.check(c).items() if v}

    def test_a_card_to_standard_passes(self):
        root = make_card(self.tmp, schema2=True, offsite=True)
        Path(self.tmp, "CAMA-012345.squeeze.json").write_text(json.dumps({"default": 1, "clips": {}}))
        self.assertEqual({k: v for k, v in card.check(card.load(str(root))).items() if v}, {})

    def test_each_standard_fails_on_its_own(self):
        self.assertEqual(sorted(self.standards(record=False, offsite=True)), ["S1", "S3", "S4", "S5"])
        shutil.rmtree(self.tmp); os.mkdir(self.tmp)
        self.assertEqual(sorted(self.standards(sums=False)), ["S2", "S3", "S4"])

    def test_s2_catches_a_changed_file_a_stray_and_a_miscount(self):
        root = make_card(self.tmp, offsite=True)
        (root / "DCIM/100/AAA0001.MOV").write_bytes(b"ONE")   # same size, other bytes
        res = card.check(card.load(str(root)))
        self.assertTrue(res["S2"][0].startswith("1 file(s) differ"), res["S2"])
        self.assertEqual(card.check(card.load(str(root)), full=False)["S2"], [])  # quick skips hashing
        (root / "stray.txt").write_text("x")
        self.assertIn("on disk but not listed", card.check(card.load(str(root)))["S2"][0])

    def test_s4_needs_this_cards_id(self):
        root = make_card(self.tmp, offsite=True)
        Path(self.tmp, "CAMA-012345.offsite.txt").write_text("card_id ffffffffffff\nremote b2:x\nchecked_utc t\n")
        self.assertEqual(len(card.check(card.load(str(root)))["S4"]), 1)

    def test_s5_old_name_fails_but_loads(self):
        res = card.check(card.load(str(make_card(self.tmp, name="OLD-NAME"))))
        self.assertIn("standard name is CAMA-012345", res["S5"][0])


class Command(Base):
    def test_check_prints_one_line_per_card_and_exits_1(self):
        make_card(self.tmp, name="CAMA-012345", schema2=True, offsite=True)
        Path(self.tmp, "CAMA-012345.squeeze.json").write_text(json.dumps({"default": 1}))
        os.makedirs(os.path.join(self.tmp, "sub"))
        make_card(os.path.join(self.tmp, "sub"), name="OLD-NAME")
        rc, out, err = run_cmd("check", self.tmp)
        lines = out.splitlines()
        self.assertEqual(rc, 1)
        self.assertEqual(lines[0], "PASS  CAMA-012345  S1 S2 S3 S4 S5")
        self.assertTrue(lines[1].startswith("FAIL  sub/OLD-NAME  S3 S4 S5:"), lines[1])
        self.assertEqual(len(lines), 2)
        self.assertIn("2 card(s), 1 fail", err)
        self.assertEqual(run_cmd("check", str(Path(self.tmp, "CAMA-012345")))[0], 0)

    def test_a_root_walk_skips_trash_folders_at_any_depth(self):
        make_card(self.tmp, name="CAMA-012345", schema2=True, offsite=True)
        for trash in ("#recycle", "sub/\uff20Recycle", ".Trashes", ".Trash-1000", "deep/er/@Recycle"):
            d = os.path.join(self.tmp, trash)
            os.makedirs(d)
            make_card(d, name="CAMA-012345", schema2=True)  # the replaced sidecars a NAS keeps
        self.assertEqual(card.cards(self.tmp), [os.path.join(self.tmp, "CAMA-012345")])
        rc, out, err = run_cmd("check", self.tmp)
        self.assertIn("1 card(s)", err)

    def test_show_names_what_is_open(self):
        make_card(self.tmp)
        rc, out, _ = run_cmd("show", os.path.join(self.tmp, "CAMA-012345"))
        self.assertEqual(rc, 0)
        self.assertIn("open      squeeze CAMA-012345/AAA0001", out)
        self.assertEqual(json.loads(run_cmd("show", "--json", os.path.join(self.tmp, "CAMA-012345"))[1])["version"], 1)

    def test_set_is_a_dry_run_then_upgrades_and_keeps_the_seven_keys(self):
        make_card(self.tmp)
        rec_path = Path(self.tmp, "CAMA-012345.card.json")
        before = json.loads(rec_path.read_text())
        rc, out, _ = run_cmd("set", self.tmp + "/CAMA-012345", "--squeeze", "1.5")
        self.assertEqual((rc, json.loads(rec_path.read_text())), (0, before))
        self.assertIn("dry run", out)
        self.assertEqual(run_cmd("set", self.tmp + "/CAMA-012345", "--squeeze", "1.5", "--clip", "NOPE.MOV", "--apply")[0], 1)
        self.assertEqual(run_cmd("set", self.tmp + "/CAMA-012345", "--squeeze", "1.5", "--clip", "AAA0002.MOV", "--project", "demo", "--apply")[0], 0)
        after = json.loads(rec_path.read_text())
        self.assertTrue(all(after[k] == before[k] for k in card.SEVEN))
        self.assertEqual((after["schema"], after["project"]), (card.SCHEMA, "demo"))
        self.assertEqual(after["answers"]["squeeze"]["clips"], {"CAMA-012345/AAA0002": 1.5})
        self.assertEqual(card.validate(after), [])
        self.assertEqual(run_cmd("set", self.tmp + "/CAMA-012345", "--squeeze", "3", "--apply")[0], 1)

    def test_set_on_schema2_keeps_the_legacy_answers(self):
        make_card(self.tmp, schema2=True)
        Path(self.tmp, "CAMA-012345.squeeze.json").write_text(json.dumps({"default": None, "clips": {"AAA0001.MOV": 1.5}}))
        self.assertEqual(run_cmd("set", self.tmp + "/CAMA-012345", "--squeeze", "1", "--clip", "AAA0002.MOV", "--apply")[0], 0)
        c = card.load(self.tmp + "/CAMA-012345")
        self.assertEqual(c["answers"]["squeeze"]["clips"], {"CAMA-012345/AAA0001": 1.5, "CAMA-012345/AAA0002": 1})

    def test_init_retrofit_matches_offload_sh_and_never_overwrites(self):
        root = make_card(self.tmp, record=False, sums=False, files={"b/two.MOV": b"22", "a/one.MOV": b"1", "a/.DS_Store": b"j"})
        self.assertEqual(run_cmd("init", "--retrofit", str(root))[0], 1)  # --cam missing
        rc, out, _ = run_cmd("init", "--retrofit", "--cam", "RETRO", str(root))
        self.assertEqual((rc, os.path.exists(str(root) + ".card.json")), (0, False))
        self.assertEqual(run_cmd("init", "--retrofit", "--cam", "RETRO", str(root), "--apply")[0], 0)
        rec = json.loads(Path(str(root) + ".card.json").read_text())
        self.assertEqual((rec["origin"], rec["volume_uuid"], rec["files"], rec["bytes"]), ("retrofit", None, 2, 3))
        self.assertEqual(rec["card_id"], rec["listing_hash"][:12])
        self.assertEqual(card.validate(rec), [])
        self.assertEqual(run_cmd("init", "--retrofit", "--cam", "RETRO", str(root), "--apply")[0], 1)
        off = subprocess.run(["bash", str(PROD / "offload.sh"), "id", str(root)], capture_output=True, text=True,
                             env={**os.environ, "DISKUTIL_BIN": "true"})
        if off.returncode == 0:
            self.assertIn(f"listing_hash {rec['listing_hash']}", off.stdout)  # one listing hash, two writers
        c = card.load(str(root))
        self.assertEqual(card.check(c)["S2"], [])
        self.assertEqual(c["name"], os.path.basename(str(root)))

    def test_init_keeps_a_good_existing_sums_file_and_refuses_a_bad_one(self):
        root = make_card(self.tmp, record=False)
        self.assertEqual(run_cmd("init", "--retrofit", "--cam", "RETRO", str(root), "--apply")[0], 0)
        os.remove(str(root) + ".card.json")
        (root / "DCIM/100/AAA0001.MOV").write_bytes(b"ONE")
        before = Path(str(root) + ".SHA256SUMS").read_text()
        self.assertEqual(run_cmd("init", "--retrofit", "--cam", "RETRO", str(root), "--apply")[0], 1)
        self.assertEqual(Path(str(root) + ".SHA256SUMS").read_text(), before)

    def test_writes_refuse_a_git_tree(self):
        git = Path(self.tmp, "repo")
        (git / ".git").mkdir(parents=True)
        root = make_card(git, record=False)
        rc, _, err = run_cmd("init", "--retrofit", "--cam", "RETRO", str(root), "--apply")
        self.assertEqual((rc, os.path.exists(str(root) + ".card.json")), (1, False))
        self.assertIn("git working tree", err)

    def test_mv_moves_the_folder_with_its_siblings_and_records_it(self):
        src, dst = Path(self.tmp, "src"), Path(self.tmp, "dst")
        src.mkdir(); dst.mkdir()
        root = make_card(src, schema2=True, offsite=True)
        self.assertEqual(run_cmd("mv", str(root), str(dst))[0], 0)  # dry run
        self.assertTrue(root.exists())
        self.assertEqual(run_cmd("mv", str(root), str(dst), "--apply")[0], 0)
        self.assertFalse(root.exists())
        c = card.load(str(dst / "CAMA-012345"))
        self.assertEqual((c["problems"], c["record"]["history"][0]["event"]), ([], "moved"))
        self.assertEqual(c["record"]["history"][0]["to"], str(dst / "CAMA-012345"))
        self.assertTrue(c["offsite"]["ok"])
        self.assertEqual(sorted(os.listdir(src)), [])

    def test_mv_refuses_a_clash_and_a_missing_destination(self):
        src, dst = Path(self.tmp, "src"), Path(self.tmp, "dst")
        src.mkdir(); dst.mkdir()
        root = make_card(src)
        (dst / "CAMA-012345.card.json").write_text("{}")
        self.assertEqual(run_cmd("mv", str(root), str(dst), "--apply")[0], 1)
        self.assertTrue(root.exists())
        self.assertEqual(run_cmd("mv", str(root), str(dst / "nope"), "--apply")[0], 1)

    def test_mv_that_does_not_verify_is_undone(self):
        src, dst = Path(self.tmp, "src"), Path(self.tmp, "dst")
        src.mkdir(); dst.mkdir()
        root = make_card(src)
        (root / "DCIM/100/AAA0001.MOV").write_bytes(b"ONE")   # rotted since the sums were written
        self.assertEqual(run_cmd("mv", str(root), str(dst), "--apply")[0], 1)  # quick pre-check passes, the hash does not
        self.assertTrue(root.exists())
        self.assertEqual(os.listdir(dst), [])
        self.assertTrue(os.path.exists(str(root) + ".card.json"))


if __name__ == "__main__":
    unittest.main()
