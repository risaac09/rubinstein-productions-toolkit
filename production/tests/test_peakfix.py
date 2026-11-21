"""
Tests for the peak-only loudness fix (a destination with no loudness target) and its
two front doors, on ProRes masters made here with ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import delivercheck as dc, peakfix  # noqa: E402
from test_delivercheck import needs_ffmpeg, statuses, tiny  # noqa: E402
from test_deliver_check_peak_clipping import SR  # noqa: E402
from test_delivercheck import TAGS  # noqa: E402


def make_master(path, expr, seconds=3):
    """A tiny tagged ProRes master with 24-bit PCM sound of the aevalsrc `expr`."""
    subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size=64x36:rate=24000/1001:duration={seconds}",
                    "-f", "lavfi", "-i", f"aevalsrc=exprs={expr}:c=stereo:s={SR}:d={seconds}",
                    "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
                    "-vf", TAGS["bt709"],
                    "-c:a", "pcm_s24le", "-shortest", path],
                   stdin=subprocess.DEVNULL, check=True)
    return path

NAME = "Client_example_master.mov"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@needs_ffmpeg
class TestPeakFix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.dest = tiny("client_master")
        # 0.9 amplitude is about -0.9 dBFS: over the -1.5 ceiling
        self.hot = make_master(os.path.join(self.dir, NAME), "0.9*sin(2*PI*997*t)")

    def tearDown(self):
        self.tmp.cleanup()

    def peak_row(self, path):
        return next(r for r in dc.check(path, self.dest, as_name=NAME)["checks"]
                    if r["check"] == "true_peak")

    def vhash(self, path):
        return subprocess.run([dc.FFMPEG, "-v", "error", "-i", path, "-map", "0:v", "-c", "copy",
                               "-f", "streamhash", "-hash", "sha256", "-"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              check=True).stdout

    def test_a_master_over_the_ceiling_gets_a_passing_copy(self):
        self.assertEqual(self.peak_row(self.hot)["status"], dc.FAIL)
        before, v = sha(self.hot), self.vhash(self.hot)
        r = peakfix.fix_peak(self.hot, self.dest)
        self.assertEqual(r["status"], "pass", dc.format_report(r["check"]))
        self.assertEqual(r["fixed"], os.path.join(self.dir, NAME[:-4] + ".loudfix.mov"))
        self.assertLess(r["gain_db"], 0)
        self.assertEqual(self.peak_row(r["fixed"])["status"], dc.PASS)
        self.assertTrue(r["video_identical"])
        self.assertEqual(self.vhash(r["fixed"]), v)
        self.assertEqual(sha(self.hot), before)
        with self.assertRaisesRegex(dc.CheckError, "already exists"):
            peakfix.fix_peak(self.hot, self.dest)

    def test_a_master_under_the_ceiling_writes_nothing(self):
        quiet = make_master(os.path.join(self.dir, "q.mov"), "0.5*sin(2*PI*997*t)")
        r = peakfix.fix_peak(quiet, self.dest)
        self.assertTrue(r["nothing_to_fix"])
        self.assertIsNone(r["fixed"])
        self.assertEqual(sorted(os.listdir(self.dir)), sorted([NAME, "q.mov"]))

    def test_dry_run_measures_and_writes_nothing(self):
        r = peakfix.fix_peak(self.hot, self.dest, dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertLess(r["gain_db"], 0)
        self.assertFalse(os.path.exists(r["fixed"]))

    def two_audio(self, second):
        quiet = make_master(os.path.join(self.dir, "q.mov"), second)
        two = os.path.join(self.dir, "Client_two_audio_master.mov")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-i", self.hot, "-i", quiet,
                        "-map", "0", "-map", "1:a", "-c", "copy", two],
                       stdin=subprocess.DEVNULL, check=True)
        return two

    def test_a_second_audio_stream_is_named_as_left_alone(self):
        two = self.two_audio("0.1*sin(2*PI*440*t)")
        r = peakfix.fix_peak(two, self.dest, dry_run=True)
        self.assertTrue(any("2 audio streams" in w for w in r["warnings"]))

    def test_a_second_audio_stream_over_the_ceiling_fails_closed_and_writes_nothing(self):
        two = self.two_audio("0.99*sin(2*PI*440*t)")
        with self.assertRaisesRegex(dc.CheckError, "audio stream 2"):
            peakfix.fix_peak(two, self.dest)
        self.assertFalse(os.path.exists(dc.fixed_path(two)))

    def test_a_peak_equal_to_the_ceiling_at_the_meters_rounding_is_still_lowered(self):
        near = make_master(os.path.join(self.dir, "Client_near_master.mov"),
                           "0.84528*sin(2*PI*997*t)")  # true peak -1.459, printed -1.5
        r = peakfix.fix_peak(near, self.dest)
        self.assertFalse(r.get("nothing_to_fix"))
        self.assertLess(dc.measure_loudness(r["fixed"])["true_peak_dbtp"], -1.5)
        self.assertEqual(r["status"], "pass")

    def test_a_mov_with_a_chapter_track_is_fixed_and_keeps_its_chapters(self):
        meta = os.path.join(self.dir, "meta.txt")
        with open(meta, "w") as f:
            f.write(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=One\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Two\n")
        chap = os.path.join(self.dir, "Client_chap_master.mov")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-i", self.hot, "-f", "ffmetadata", "-i", meta,
                        "-map", "0", "-map_chapters", "1", "-c", "copy", chap],
                       stdin=subprocess.DEVNULL, check=True)
        r = peakfix.fix_peak(chap, self.dest)
        self.assertEqual(r["status"], "pass")
        self.assertTrue(any("data streams" in w for w in r["warnings"]))
        chapters = json.loads(subprocess.run(
            [dc.FFPROBE, "-v", "error", "-show_chapters", "-of", "json", r["fixed"]],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True).stdout)["chapters"]
        self.assertEqual(len(chapters), 2)

    def test_a_null_ceiling_is_a_clear_error(self):
        dest = dict(self.dest, loudness={"integrated_lufs": None, "true_peak_max_dbtp": None})
        with self.assertRaisesRegex(dc.CheckError, "true_peak_max_dbtp must be a number"):
            peakfix.fix_peak(self.hot, dest)

    def over_once(self, times):
        """measure_loudness, except the first `times` reads of the written copy say -0.7 dBTP:
        a lossy encode landing above the gain's estimate."""
        real, seen = dc.measure_loudness, []

        def fake(path, stream=0):
            m = real(path, stream)
            if path.endswith(".loudfix.mov"):
                seen.append(path)
                if len(seen) <= times:
                    m = dict(m, true_peak_dbtp=-0.7)
            return m
        return mock.patch.object(dc, "measure_loudness", fake)

    def test_a_copy_that_lands_over_the_ceiling_gets_one_second_pass_from_the_original(self):
        before = sha(self.hot)
        with self.over_once(1):
            r = peakfix.fix_peak(self.hot, self.dest)
        self.assertEqual(r["status"], "pass")
        self.assertLessEqual(r["gain_db"], -1.7)  # the first -0.75 plus the 0.85 overshoot and the margin
        self.assertTrue(any("second pass" in w for w in r["warnings"]))
        self.assertEqual(sha(self.hot), before)

    def test_a_copy_still_over_after_the_second_pass_is_removed_and_the_run_fails(self):
        before = sha(self.hot)
        with self.over_once(2):
            with self.assertRaisesRegex(dc.CheckError, "copy was removed"):
                peakfix.fix_peak(self.hot, self.dest)
        self.assertFalse(os.path.exists(dc.fixed_path(self.hot)))
        self.assertEqual(sha(self.hot), before)  # and a rerun is not refused
        self.assertEqual(peakfix.fix_peak(self.hot, self.dest)["status"], "pass")

    def test_an_error_after_the_copy_is_written_removes_the_copy(self):
        with mock.patch.object(dc, "video_hash", side_effect=dc.CheckError("boom")):
            with self.assertRaisesRegex(dc.CheckError, "boom.*removed"):
                peakfix.fix_peak(self.hot, self.dest)
        self.assertFalse(os.path.exists(dc.fixed_path(self.hot)))

    def test_a_resampled_rate_is_named(self):
        odd = os.path.join(self.dir, "Client_441_master.mov")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=24:duration=3",
                        "-f", "lavfi", "-i", "aevalsrc=0.9*sin(2*PI*997*t):c=stereo:s=44100:d=3",
                        "-c:v", "prores_ks", "-vf", TAGS["bt709"], "-c:a", "pcm_s24le", "-shortest", odd],
                       stdin=subprocess.DEVNULL, check=True)
        r = peakfix.fix_peak(odd, self.dest)
        self.assertTrue(any("resampled from 44100" in w for w in r["warnings"]))

    def test_a_destination_with_a_target_is_not_this_fix(self):
        with self.assertRaisesRegex(dc.CheckError, "has a loudness target"):
            peakfix.fix_peak(self.hot, tiny("youtube_16x9"))

    def test_the_cli_routes_a_destination_with_no_target_here(self):
        from rpresolve import cmd_deliver_fix_loudness as cmd
        import argparse
        ap = argparse.ArgumentParser()
        cmd.add_arguments(ap)
        args = ap.parse_args([self.hot, "--dest", "client_master", "--json"])
        args.config = None
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd.run(args)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "pass")
        self.assertTrue(os.path.isfile(dc.fixed_path(self.hot)))
        args = ap.parse_args([self.hot, "--dest", "client_master", "--replace"])
        args.config = None
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cmd.run(args), 1)

    def test_the_mcp_tool_plans_then_writes_a_peak_copy(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        env = {"RPRESOLVE_MCP_JOURNAL": os.path.join(self.dir, "j.jsonl")}
        tool = server.build_registry().get("deliver_fix_loudness")

        def call(args):
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            with mock.patch.dict(os.environ, env):
                return tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())

        base = {"file": self.hot, "destination": "client_master"}
        before = sha(self.hot)
        dry = call(base)
        self.assertTrue(dry["dry_run"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertFalse(os.path.exists(dc.fixed_path(self.hot)))
        with self.assertRaisesRegex(Exception, "plan_sha"):
            call({**base, "dry_run": False})
        r = call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertEqual(r["status"], "pass", r["summary"])
        self.assertEqual(self.peak_row(dc.fixed_path(self.hot))["status"], dc.PASS)
        self.assertEqual(sha(self.hot), before)
        quiet = make_master(os.path.join(self.dir, "Client_quiet_master.mov"), "0.5*sin(2*PI*997*t)")
        r = call({"file": quiet, "destination": "client_master"})
        self.assertTrue(r["nothing_to_fix"])
        self.assertFalse(os.path.exists(dc.fixed_path(quiet)))


if __name__ == "__main__":
    unittest.main()
