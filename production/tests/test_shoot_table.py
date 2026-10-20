"""
Hermetic tests for rpresolve.shoot_table and the shoot-table command.
Clips are made at test time with ffmpeg lavfi (a VFR clip, a silent clip, a
long clip, a normal one); exiftool is a stub script that returns fixed
tags, so the golden CSV is stable and no real footage is read. Tests that
need ffmpeg skip themselves when it is missing.

Run: python3 -m unittest production.tests.test_shoot_table -v
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import shoot_table  # noqa: E402

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("RPRESOLVE_FFPROBE") or shutil.which("ffprobe")
WORKFLOW = HERE.parent / "resolve_workflow.py"
GOLDEN = HERE / "fixtures" / "shoot_table" / "golden.csv"

# Prints one JSON doc per non-option argument; the creation time and lens
# are fixed so the golden file does not move.
STUB_EXIFTOOL = """#!/bin/sh
printf '['
sep=''
for a in "$@"; do
  case "$a" in -*|LargeFileSupport=1) continue;; esac
  printf '%s{"SourceFile":"%s","QuickTime:CreateDate":"2026:10:16 10:00:00","ExifIFD:LensModel":"Test 24mm"}' "$sep" "$a"
  sep=','
done
printf ']'
"""


def ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-nostdin", *args], check=True)


@unittest.skipUnless(FFMPEG and FFPROBE, "needs ffmpeg and ffprobe")
class TestShootTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="shoot_table_")
        cls.dir = os.path.join(cls.tmp, "card")
        os.mkdir(cls.dir)
        cls.exiftool = os.path.join(cls.tmp, "exiftool")
        with open(cls.exiftool, "w") as f:
            f.write(STUB_EXIFTOOL)
        os.chmod(cls.exiftool, os.stat(cls.exiftool).st_mode | stat.S_IXUSR)
        cls.enc = ["-fflags", "+bitexact", "-metadata:s:v:0", "handler_name=OBS Video Handler", "-c:a", "pcm_s16le"]
        tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
        vid = ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=25:duration=2"]
        cls.p = {n: os.path.join(cls.dir, n) for n in
                 ("a_normal.mov", "b_silent.mov", "c_vfr.mov", "d_long.mov", "e_noaudio.mov", "f_derived.mov")}
        ff(*vid, *tone, "-shortest", *cls.enc, cls.p["a_normal.mov"])
        ff(*vid, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-shortest", *cls.enc, cls.p["b_silent.mov"])
        # VFR: drop every third frame, keep the timestamps (-fps_mode vfr)
        for flag in ("-fps_mode", "-vsync"):  # the first needs ffmpeg 5.1; CI's apt ffmpeg may be older
            try:
                ff(*vid, *tone, "-vf", "select='mod(n,3)'", flag, "vfr", *cls.enc, cls.p["c_vfr.mov"])
                break
            except subprocess.CalledProcessError:
                if flag == "-vsync":
                    raise
        # 701 s of 1 fps 16x16 video: long by duration alone, a second to encode
        ff("-f", "lavfi", "-i", "color=c=black:size=16x16:rate=1:duration=701", "-an",
           *cls.enc[:4], cls.p["d_long.mov"])
        ff(*vid, "-an", *cls.enc[:4], cls.p["e_noaudio.mov"])
        ff(*vid, *tone, "-shortest", "-c:a", "pcm_s16le", cls.p["f_derived.mov"])  # keeps the Lavf encoder tag

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self, **kw):
        return shoot_table.build([self.dir], ffprobe=FFPROBE, exiftool=self.exiftool, ffmpeg=FFMPEG, **kw)

    def rows(self, **kw):
        rows, missing = self.build(**kw)
        self.assertEqual(missing, [])
        return {os.path.basename(r["file"]): r for r in rows}

    def test_columns_and_clean_row(self):
        r = self.rows()["a_normal.mov"]
        self.assertEqual(list(r), list(shoot_table.COLUMNS))
        self.assertEqual((r["camera"], r["fps"], r["size"], r["duration_s"]),
                         ("OBS (capture card)", "25", "64x48", "2.000"))
        self.assertEqual((r["local_start"], r["lens"], r["flags"]), ("2026-10-16T10:00:00", "Test 24mm", ""))
        self.assertTrue(r["audio_source"].startswith("embedded pcm_s16le 1ch"))

    def test_flags(self):
        rows = self.rows()
        self.assertEqual(rows["b_silent.mov"]["flags"], "weak-audio")
        self.assertIn("vfr", rows["c_vfr.mov"]["flags"].split(";"))
        self.assertEqual(rows["d_long.mov"]["flags"], "long-take;no-audio")
        self.assertEqual(rows["e_noaudio.mov"]["flags"], "no-audio")
        self.assertEqual(rows["e_noaudio.mov"]["audio_source"], "none")
        self.assertEqual(rows["f_derived.mov"]["flags"], "unexpected")

    def test_long_threshold_is_minutes(self):
        self.assertNotIn("long-take", self.rows(long_min=12)["d_long.mov"]["flags"])
        self.assertIn("long-take", self.rows(long_min=0.01)["a_normal.mov"]["flags"])

    def test_offset_by_camera(self):
        r = self.rows(offsets={"obs (capture card)": 90})
        self.assertEqual(r["a_normal.mov"]["local_start"], "2026-10-16T10:01:30")
        self.assertEqual(shoot_table.unknown_offsets(list(r.values()), {"GH7": 1}), ["GH7"])

    def test_golden_csv(self):
        rows, _ = self.build()
        text = re.sub(r"Lavf[\d.]+", "Lavf", shoot_table.format_csv(rows).replace(self.dir, "CARD"))
        if os.environ.get("UPDATE_GOLDEN"):
            GOLDEN.write_text(text, encoding="utf-8")
        self.assertEqual(text, GOLDEN.read_text(encoding="utf-8"))

    def test_command_exit_status_and_offset_typo(self):
        env = dict(os.environ, RPRESOLVE_FFPROBE=FFPROBE, RPRESOLVE_EXIFTOOL=self.exiftool,
                   RPRESOLVE_FFMPEG=FFMPEG)
        run = lambda *a: subprocess.run([sys.executable, str(WORKFLOW), "shoot-table", self.dir, *a],
                                        capture_output=True, text=True, env=env)
        ok = run()
        self.assertEqual(ok.returncode, 2, ok.stderr)  # flagged rows
        self.assertTrue(ok.stdout.startswith(",".join(shoot_table.COLUMNS)))
        bad = run("--offset", "GH7=3")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("names no camera", bad.stderr)
        self.assertEqual(run("--offset", "nonsense").returncode, 1)


class TestPure(unittest.TestCase):
    def test_parse_start(self):
        p = shoot_table.parse_start
        self.assertEqual(p("2026:10:16 10:00:00-04:00").isoformat(), "2026-10-16T10:00:00")
        self.assertEqual(p("2026-10-16T10:00:00.000000Z").isoformat(), "2026-10-16T10:00:00")
        self.assertIsNone(p("0000:00:00 00:00:00"))
        self.assertIsNone(p(""))


if __name__ == "__main__":
    unittest.main()
