"""
Tests for rpresolve.delivercheck on media made here with ffmpeg (lavfi
testsrc2 and a 1 kHz sine, 2.5 s at a tiny frame size): a passing file per
codec family (H.264, H.265, ProRes 422 HQ), then files that must fail on
fps, size, colour tags, loudness 2 LU off, true peak over -1 dBTP, a
missing or malformed sidecar, captions where none belong, and a bad name.
Last, the loudness fix: the fixed file passes and its video stream is
bit-identical to the original's. Skipped where ffmpeg, ffprobe or its
libx264, libx265 and prores_ks encoders are missing.

A stereo 1 kHz sine of peak amplitude A measures 20*log10(A) LUFS, so the
levels below are exact without a calibration pass.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import config as rpconfig, deliver, delivercheck as dc  # noqa: E402

CFG = rpconfig.load_config("/nonexistent/config.json")
SRT = ("1\n00:00:00,000 --> 00:00:01,000\nFirst line\n\n"
       "2\n00:00:01,000 --> 00:00:02,000\nSecond line\n")
NAME16 = "SW001_Guest_01_example-clip_16x9.mp4"


def _encoders():
    if not all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)):
        return ""
    return subprocess.run([dc.FFMPEG, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True).stdout


ENCODERS = _encoders()
needs_ffmpeg = unittest.skipUnless(
    all(e in ENCODERS for e in ("libx264", "libx265", "prores_ks")),
    "ffmpeg or ffprobe (with libx264, libx265 and prores_ks) missing")

TAGS = {"bt709": "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
        "bt2020": "setparams=color_primaries=bt2020:color_trc=bt709:colorspace=bt2020nc:range=tv"}


def make(path, size="64x36", rate="24000/1001", codec="h264", lufs=-16.0, peaks=False,
         tags="bt709", seconds=2.5):
    """A test clip: testsrc2 picture, stereo 1 kHz sine at `lufs`, and with
    peaks a short full-scale pulse twice a second (about -0.3 dBTP)."""
    expr = f"{10 ** (lufs / 20):.5f}*sin(2*PI*1000*t)"
    if peaks:
        expr += "+0.75*lt(mod(t\\,0.5)\\,0.0002)"
    video = {"h264": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
             "hevc": ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
                      "log-level=error", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"],
             "prores": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]}[codec]
    audio = ["-c:a", "pcm_s24le"] if codec == "prores" else ["-c:a", "aac", "-b:a", "192k"]
    cmd = [dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}",
           "-f", "lavfi", "-i", f"aevalsrc=exprs={expr}:c=stereo:s=48000:d={seconds}"]
    cmd += (["-vf", TAGS[tags]] if tags else []) + video + audio + ["-shortest"]
    if path.endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    subprocess.run(cmd + [path], stdin=subprocess.DEVNULL, check=True)
    return path


def tiny(key, size=None):
    """A default destination at a test-sized frame."""
    d = deliver.destination(CFG, key)
    if d["resolution"] != "timeline":
        w, h = size or {"16x9": (64, 36), "9x16": (36, 64), "1x1": (48, 48)}[d["aspect"]]
        d["resolution"] = {"width": w, "height": h}
    return d


def statuses(result):
    return {r["check"]: r["status"] for r in result["checks"]}


def failed(result):
    return sorted(k for k, v in statuses(result).items() if v == dc.FAIL)


class TestParsers(unittest.TestCase):
    def test_srt(self):
        cues, problems = dc.parse_srt("﻿" + SRT.replace("\n", "\r\n"))
        self.assertEqual((len(cues), problems), (2, []))
        self.assertEqual(cues[1][:2], (1.0, 2.0))
        _, problems = dc.parse_srt("1\n00:00:02,000 --> 00:00:03,000\nB\n\n"
                                   "2\n00:00:01,000 --> 00:00:01,500\nA\n")
        self.assertIn("before the cue above", problems[0])
        _, problems = dc.parse_srt("1\n00:00:02,000 --> 00:00:01,000\nB\n")
        self.assertIn("at or before its start", problems[0])
        _, problems = dc.parse_srt("1\n00:00:01.000 --> 00:00:02.000\nWebVTT dots\n")
        self.assertIn("timing line", problems[0])
        self.assertEqual(dc.parse_srt("  \n")[1], ["no cues"])

    def test_rates_and_pixel_formats(self):
        self.assertEqual(dc.parse_fps("23.976"), Fraction(24000, 1001))
        self.assertEqual(dc.parse_fps("29.97"), Fraction(30000, 1001))
        self.assertEqual(dc.parse_fps("25"), 25)
        self.assertEqual(dc.parse_fps("24000/1001"), Fraction(24000, 1001))
        self.assertIsNone(dc.rate("0/0"))
        with self.assertRaises(ValueError):
            dc.parse_fps("fast")
        self.assertEqual(dc.pix_fmt_family("yuv420p"), ("420", 8))
        self.assertEqual(dc.pix_fmt_family("yuv422p10le"), ("422", 10))
        self.assertEqual(dc.pix_fmt_family("yuvj420p"), ("420", 8))
        self.assertEqual(dc.pix_fmt_family("p010le"), ("420", 10))
        self.assertEqual(dc.pix_fmt_family("gbrp"), (None, None))

    def test_ebur128_summary(self):
        text = ("[Parsed_ebur128_0 @ 0x0] Summary:\n\n  Integrated loudness:\n    I:         "
                "-14.2 LUFS\n    Threshold: -24.2 LUFS\n\n  Loudness range:\n    LRA:         "
                "1.5 LU\n\n  True peak:\n    Peak:       -1.3 dBFS\n")
        self.assertEqual(dc.parse_ebur128(text), {"integrated_lufs": -14.2,
                                                  "true_peak_dbtp": -1.3, "lra_lu": 1.5})
        self.assertEqual(dc.parse_ebur128(text.replace("-1.3", "-inf"))["true_peak_dbtp"],
                         float("-inf"))
        with self.assertRaises(dc.CheckError):
            dc.parse_ebur128("no summary here")

    def test_missing_tools_are_named(self):
        old = dc.FFPROBE
        dc.FFPROBE = "/nonexistent/ffprobe"
        try:
            with self.assertRaisesRegex(dc.ToolMissing, "ffprobe"):
                dc.check("/nonexistent/x.mp4", tiny("linkedin_16x9"))
        finally:
            dc.FFPROBE = old


@needs_ffmpeg
class TestCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def folder(self):
        d = tempfile.mkdtemp(dir=self.root)
        return d

    def clip(self, name=NAME16, sidecar=SRT, **kw):
        d = self.folder()
        path = make(os.path.join(d, name), **kw)
        if sidecar is not None:
            with open(deliver.sidecar_path(path), "w", encoding="utf-8") as f:
                f.write(sidecar)
        return path

    def test_h264_passes(self):
        r = dc.check(self.clip(), tiny("linkedin_16x9"), fps="23.976")
        self.assertEqual(failed(r), [], dc.format_report(r))
        s = statuses(r)
        self.assertEqual((s["loudness"], s["true_peak"], s["captions"], s["color_transfer"]),
                         (dc.PASS, dc.PASS, dc.PASS, dc.SKIP))
        self.assertEqual(r["status"], "pass")
        row = next(x for x in r["checks"] if x["check"] == "color_transfer")
        self.assertEqual(row["found"], "bt709")  # reported even though not asserted

    def test_h265_passes(self):
        r = dc.check(self.clip(codec="hevc", lufs=-14.0), tiny("youtube_16x9"))
        self.assertEqual(failed(r), [], dc.format_report(r))

    def test_prores_master_passes(self):
        name = "Client_example-slug_master.mov"
        path = self.clip(name=name, sidecar=None, codec="prores", lufs=-20.0)
        r = dc.check(path, tiny("client_master"), size=(64, 36), fps="23.976")
        self.assertEqual(failed(r), [], dc.format_report(r))
        s = statuses(r)
        self.assertEqual((s["loudness"], s["audio_bit_depth"], s["size"], s["captions"]),
                         (dc.SKIP, dc.PASS, dc.PASS, dc.PASS))
        self.assertEqual(statuses(dc.check(path, tiny("client_master")))["size"], dc.SKIP)
        self.assertEqual(failed(dc.check(path, tiny("client_master"), size=(1920, 1080))),
                         ["size"])

    def test_burn_in_passes_without_a_sidecar(self):
        path = self.clip(name="SW001_Guest_01_example-clip_9x16.mp4", sidecar=None,
                         size="36x64")
        r = dc.check(path, tiny("linkedin_9x16"))
        self.assertEqual(failed(r), [], dc.format_report(r))
        with open(deliver.sidecar_path(path), "w", encoding="utf-8") as f:
            f.write(SRT)
        self.assertEqual(failed(dc.check(path, tiny("linkedin_9x16"), loudness=False)),
                         ["captions"])

    def test_burn_in_fails_on_any_caption_file_for_its_stem(self):
        path = self.clip(name="SW001_Guest_01_example-clip_9x16.mp4", sidecar=None,
                         size="36x64")
        stem = path[:-4]
        cases = [(stem + ".vtt", False), (stem + ".en.srt", False), (stem + ".SRT", False),
                 (stem + ".srt", True)]  # True: a dangling link
        for side, dangling in cases:
            with self.subTest(side=os.path.basename(side), dangling=dangling):
                if dangling:
                    os.symlink(os.path.join(os.path.dirname(path), "gone.srt"), side)
                else:
                    with open(side, "w", encoding="utf-8") as f:
                        f.write(SRT)
                try:
                    r = dc.check(path, tiny("linkedin_9x16"), loudness=False)
                    self.assertEqual(failed(r), ["captions"], dc.format_report(r))
                    row = next(x for x in r["checks"] if x["check"] == "captions")
                    self.assertIn(os.path.basename(side), row["found"])
                finally:
                    os.remove(side)
        self.assertEqual(failed(dc.check(path, tiny("linkedin_9x16"), loudness=False)), [])

    def test_a_dangling_sidecar_link_fails(self):
        path = self.clip(sidecar=None)
        os.symlink(os.path.join(os.path.dirname(path), "gone.srt"), deliver.sidecar_path(path))
        r = dc.check(path, tiny("linkedin_16x9"), loudness=False)
        self.assertEqual(failed(r), ["captions"])
        row = next(x for x in r["checks"] if x["check"] == "captions")
        self.assertIn("no sidecar", row["found"])

    def test_wrong_fps(self):
        path = self.clip(rate="25")
        self.assertEqual(failed(dc.check(path, tiny("linkedin_16x9"), fps="23.976",
                                         loudness=False)), ["fps"])
        self.assertEqual(failed(dc.check(path, tiny("linkedin_16x9"), fps="25",
                                         loudness=False)), [])
        odd = self.clip(rate="15")
        self.assertEqual(failed(dc.check(odd, tiny("linkedin_16x9"), loudness=False)), ["fps"])

    def test_wrong_size(self):
        r = dc.check(self.clip(), tiny("linkedin_16x9", size=(96, 54)), loudness=False)
        self.assertEqual(failed(r), ["size"])

    def test_wrong_or_missing_colour_tags(self):
        r = dc.check(self.clip(tags=None), tiny("linkedin_16x9"), loudness=False)
        self.assertEqual(failed(r), ["color_primaries", "color_space"])
        self.assertIn("unset", [x["found"] for x in r["checks"]
                                if x["check"] == "color_primaries"])
        r = dc.check(self.clip(tags="bt2020"), tiny("linkedin_16x9"), loudness=False)
        self.assertEqual(failed(r), ["color_primaries", "color_space"])
        strict = tiny("linkedin_16x9")
        strict["color"]["expect"]["color_transfer"] = ["unknown"]
        self.assertEqual(failed(dc.check(self.clip(), strict, loudness=False)),
                         ["color_transfer"])

    def test_loudness_two_lu_off(self):
        r = dc.check(self.clip(lufs=-14.0), tiny("linkedin_16x9"))
        self.assertEqual(failed(r), ["loudness"])
        row = next(x for x in r["checks"] if x["check"] == "loudness")
        self.assertIn("-14.0", row["found"])

    def test_true_peak_over_the_limit(self):
        r = dc.check(self.clip(peaks=True), tiny("linkedin_16x9"))
        self.assertEqual(failed(r), ["true_peak"], dc.format_report(r))

    def test_missing_or_malformed_sidecar(self):
        self.assertEqual(failed(dc.check(self.clip(sidecar=None), tiny("linkedin_16x9"),
                                         loudness=False)), ["captions"])
        for bad in ("", "1\nnot a time\nText\n",
                    "1\n00:00:02,000 --> 00:00:03,000\nB\n\n2\n00:00:01,000 --> "
                    "00:00:01,500\nA\n"):
            with self.subTest(bad=bad):
                self.assertEqual(failed(dc.check(self.clip(sidecar=bad), tiny("linkedin_16x9"),
                                                 loudness=False)), ["captions"])

    def test_a_subtitle_stream_where_none_belongs(self):
        d = self.folder()
        src = make(os.path.join(d, "src.mp4"))
        srt = os.path.join(d, "in.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(SRT)
        out = os.path.join(d, "SW001_Guest_01_example-clip_1x1.mp4")
        subprocess.run([dc.FFMPEG, "-v", "error", "-i", src, "-i", srt, "-map", "0", "-map", "1",
                        "-c", "copy", "-c:s", "mov_text", out], stdin=subprocess.DEVNULL,
                       check=True)
        r = dc.check(out, tiny("linkedin_1x1", size=(64, 36)), loudness=False)
        self.assertEqual(failed(r), ["captions"])

    def test_bad_name_and_wrong_container(self):
        path = self.clip(name="SW001_Guest_01_Example_16x9.mp4")
        self.assertEqual(failed(dc.check(path, tiny("linkedin_16x9"), loudness=False)),
                         ["name"])
        mov = self.clip(name="SW001_Guest_01_example-clip_16x9.mov")
        self.assertEqual(failed(dc.check(mov, tiny("linkedin_16x9"), loudness=False)),
                         ["container", "name"])

    def test_report_lines(self):
        text = dc.format_report(dc.check(self.clip(), tiny("linkedin_16x9"), loudness=False))
        self.assertIn("PASS  name", text)
        self.assertIn("SKIP  loudness", text)
        self.assertTrue(text.rstrip().endswith("PASS"))


@needs_ffmpeg
class TestFixLoudness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.path = make(os.path.join(self.dir, NAME16), codec="hevc", lufs=-12.0)
        with open(deliver.sidecar_path(self.path), "w", encoding="utf-8") as f:
            f.write(SRT)
        self.dest = tiny("youtube_16x9")

    def tearDown(self):
        self.tmp.cleanup()

    def streamhash(self, path):
        return subprocess.run([dc.FFMPEG, "-v", "error", "-i", path, "-map", "0:v", "-c", "copy",
                               "-f", "streamhash", "-hash", "sha256", "-"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              check=True).stdout

    def test_fixed_file_passes_and_its_video_is_untouched(self):
        self.assertEqual(failed(dc.check(self.path, self.dest)), ["loudness"])
        before = self.streamhash(self.path)
        r = dc.fix_loudness(self.path, self.dest, fps="23.976")
        self.assertEqual(r["status"], "pass", dc.format_report(r["check"]))
        self.assertEqual(r["fixed"], os.path.join(self.dir, NAME16[:-4] + ".loudfix.mp4"))
        self.assertTrue(r["video_identical"])
        self.assertEqual(self.streamhash(r["fixed"]), before)
        self.assertEqual(self.streamhash(self.path), before)  # the original is untouched
        self.assertEqual(r["result_loudnorm"]["normalization_type"], "linear")
        self.assertFalse(r["replaced"])
        after = dc.probe(r["fixed"])["streams"]
        v = next(s for s in after if s["codec_type"] == "video")
        self.assertEqual((v["color_primaries"], v["color_space"]), ("bt709", "bt709"))
        with self.assertRaisesRegex(dc.CheckError, "already exists"):
            dc.fix_loudness(self.path, self.dest)

    def test_replace_moves_the_original_to_the_trash_first(self):
        before = self.streamhash(self.path)
        trash = os.path.join(self.dir, "Trash")
        os.makedirs(trash)
        r = dc.fix_loudness(self.path, self.dest, replace=True, trash_root=trash, now=0)
        self.assertTrue(r["replaced"])
        self.assertEqual(r["fixed"], self.path)
        self.assertTrue(r["trashed"].startswith(os.path.join(trash, "deliver-loudfix-")))
        self.assertEqual(os.path.basename(r["trashed"]), NAME16)
        # the original in the Trash still measures 2 LU hot (its sidecar stayed behind)
        self.assertEqual(statuses(dc.check(r["trashed"], self.dest))["loudness"], dc.FAIL)
        self.assertTrue(os.path.isfile(deliver.sidecar_path(self.path)))
        self.assertEqual(failed(dc.check(self.path, self.dest)), [])
        self.assertEqual(self.streamhash(self.path), before)
        self.assertFalse(os.path.exists(dc.fixed_path(self.path)))

    def test_nothing_to_fix_without_a_target(self):
        with self.assertRaisesRegex(dc.CheckError, "no loudness target"):
            dc.fix_loudness(self.path, tiny("client_master"))

    def test_mp4_with_a_timecode_track_keeps_its_timecode(self):
        path = os.path.join(self.dir, "SW001_Guest_01_timecode_16x9.mp4")
        subprocess.run([dc.FFMPEG, "-v", "error", "-i", self.path, "-map", "0", "-c", "copy",
                        "-timecode", "00:59:50:00", path], stdin=subprocess.DEVNULL, check=True)
        with open(deliver.sidecar_path(path), "w", encoding="utf-8") as f:
            f.write(SRT)
        r = dc.fix_loudness(path, self.dest)
        self.assertEqual(r["status"], "pass", dc.format_report(r["check"]))
        self.assertTrue(any("timecode" in w for w in r["warnings"]))
        tags = [s.get("tags", {}).get("timecode") for s in dc.probe(r["fixed"])["streams"]]
        self.assertIn("00:59:50:00", tags)


@needs_ffmpeg
class TestMCPDeliverCheck(unittest.TestCase):
    """deliver_check as the MCP server calls it, with a test-size overlay
    given the way the server takes one ($RPRESOLVE_CONFIG)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        overlay = os.path.join(self.dir, "overlay.json")
        with open(overlay, "w", encoding="utf-8") as f:
            f.write('{"destinations": {"linkedin_16x9": {"resolution": '
                    '{"width": 64, "height": 36}}}}')
        env = mock.patch.dict(os.environ, {"RPRESOLVE_CONFIG": overlay})
        env.start()
        self.addCleanup(env.stop)
        self.path = make(os.path.join(self.dir, NAME16))
        with open(deliver.sidecar_path(self.path), "w", encoding="utf-8") as f:
            f.write(SRT)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, args):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("deliver_check")
        errors = schema.validate(tool.input_schema, args)
        if errors:
            raise AssertionError(errors)
        return tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())

    def test_pass_and_fail(self):
        r = self.call({"file": self.path, "destination": "linkedin_16x9", "fps": "23.976"})
        self.assertEqual(r["status"], "pass", r["summary"])
        self.assertIn("every asserted rule passes", r["summary"])
        r = self.call({"file": self.path, "destination": "linkedin_16x9", "fps": "25",
                       "loudness": False})
        self.assertIn("FAILED: fps", r["summary"])
        with self.assertRaises(AssertionError):
            self.call({"file": self.path, "destination": "linkedin_16x9", "size": "big"})
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.call({"file": "relative.mp4", "destination": "linkedin_16x9"})


if __name__ == "__main__":
    unittest.main()
