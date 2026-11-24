"""
TT-05 / VP-114: deliver-check as the gate for an outbound file. The safe-zone
predicate (rpresolve.safezone), the safe_zone and video_bitrate rows, and
the planned failures: an untagged HyperFrames-style MOV (ProRes 4444, alpha,
no colour tags) and a tag-stripped master, beside a passing control. Media is
made at test time in a temp dir by ffmpeg lavfi; none is committed. Skipped
where ffmpeg, ffprobe or its prores_ks and libx264 encoders are missing.

Run: /usr/bin/python3 -m unittest production.tests.test_deliver_gate -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # test_delivercheck, run on its own

from rpresolve import delivercheck as dc, safezone  # noqa: E402
from test_delivercheck import CFG, failed, make, needs_ffmpeg, statuses, tiny  # noqa: E402

MASTER = "Client_example-slug_master.mov"
VERT = "SW001_Guest_01_example-clip_9x16.mp4"


class TestSafezone(unittest.TestCase):
    def test_margins_follow_the_frame_shape(self):
        self.assertEqual(safezone.margins(1080, 1920), (0.14, 0.35, 0.06))
        self.assertEqual(safezone.margins(1920, 1080), (0.05,) * 3)
        self.assertEqual(safezone.margins(1080, 1080), (0.05,) * 3)
        self.assertEqual(safezone.margins(1080, 1350), (0.05,) * 3)  # 4:5 feed: no UI bands

    def test_empty_box_list_passes_and_none_skips(self):
        self.assertEqual(dc._safe_zone(1080, 1920, [])["status"], dc.PASS)
        self.assertEqual(dc._safe_zone(1080, 1920, None)["status"], dc.SKIP)

    def test_outside_names_the_sides(self):
        m = safezone.margins(1080, 1920)
        self.assertEqual(safezone.outside((0.1, 0.2, 0.9, 0.6), m), [])
        self.assertEqual(safezone.outside((0.1, 0.2, 0.9, 0.7), m), ["bottom"])
        self.assertEqual(safezone.outside((0.0, 0.0, 1.0, 0.5), m), ["top", "left", "right"])

    def test_qc_reads_the_same_margins(self):
        from rpresolve import qc
        self.assertEqual((qc.THRESHOLDS["safe_top"], qc.THRESHOLDS["safe_bottom"],
                          qc.THRESHOLDS["safe_side"]),
                         tuple(safezone.PORTRAIT[k] for k in ("top", "bottom", "side")))

    def test_load_boxes(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = os.path.join(d, "g.json"), os.path.join(d, "b.json")
            Path(good).write_text(json.dumps([{"label": "lower third", "box": [0.1, 0.5, 0.8, 0.6]}]))
            Path(bad).write_text(json.dumps([{"box": [0.1, 0.5, 0.8]}]))
            self.assertEqual(safezone.load_boxes(good), [("lower third", (0.1, 0.5, 0.8, 0.6))])
            with self.assertRaises(ValueError):
                safezone.load_boxes(bad)
            with self.assertRaises(ValueError):
                safezone.load_boxes(os.path.join(d, "missing.json"))
            for box in ([0.9, 0.9, 0.1, 0.1], [0, 0, 2, 1], [float("nan")] * 4):
                Path(bad).write_text(json.dumps([{"box": box}]))
                with self.assertRaises(ValueError):
                    safezone.load_boxes(bad)

    def test_write_boxes_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "b.json")
            safezone.write_boxes(out, [("a", (0.1, 0.2, 0.5, 0.6)), (3, (-0.5, 0.0, 0.4, 1.5))])
            self.assertEqual(safezone.load_boxes(out), [("a", (0.1, 0.2, 0.5, 0.6)), ("3", (0.0, 0.0, 0.4, 1.0))])
            with self.assertRaises(ValueError):
                safezone.write_boxes(out, [("flat", (0.5, 0.2, 0.5, 0.6))])


@needs_ffmpeg
class TestGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def path(self, name):
        return os.path.join(tempfile.mkdtemp(dir=self.root), name)

    def master(self, **kw):
        return make(self.path(MASTER), codec="prores", lufs=-20.0, **kw)

    def check(self, path, key="client_master", **kw):
        return dc.check(path, tiny(key), size=(64, 36), fps="23.976", **kw)

    def test_control_passes(self):
        r = self.check(self.master(), boxes=[("lower third", (0.1, 0.7, 0.6, 0.8))])
        self.assertEqual(failed(r), [], dc.format_report(r))
        s = statuses(r)
        self.assertEqual((s["safe_zone"], s["video_bitrate"], s["color_primaries"]),
                         (dc.PASS, dc.SKIP, dc.PASS))

    def test_untagged_hyperframes_style_mov_fails(self):
        path = self.path("overlay_example_master.mov")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red@0.5:size=64x36:rate=24000/1001:duration=2.5,format=yuva444p10le",
                        "-f", "lavfi", "-i", "aevalsrc=0.1*sin(2*PI*1000*t):c=stereo:s=48000:d=2.5",
                        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                        "-c:a", "pcm_s24le", "-shortest", path], stdin=subprocess.DEVNULL, check=True)
        r = self.check(path, loudness=False)
        self.assertEqual(r["status"], "fail")
        self.assertTrue({"color_primaries", "color_space", "video_codec", "pix_fmt"} <= set(failed(r)),
                        dc.format_report(r))

    def test_tag_stripped_master_fails(self):
        tagged = self.master()
        self.assertEqual(failed(self.check(tagged)), [])
        stripped = self.path(MASTER)
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-i", tagged, "-c:a", "copy", "-c:v", "prores_ks", "-profile:v", "3", "-vf",
                        "setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown",
                        stripped], stdin=subprocess.DEVNULL, check=True)
        r = self.check(stripped)
        self.assertEqual(failed(r), ["color_primaries", "color_space"], dc.format_report(r))

    def test_super_outside_the_vertical_safe_zone_fails(self):
        path = make(self.path(VERT), size="36x64", lufs=-16.0)
        dest = tiny("linkedin_9x16")
        ok = dc.check(path, dest, boxes=[("lower third", (0.1, 0.2, 0.9, 0.6))])
        self.assertEqual(failed(ok), [], dc.format_report(ok))
        low = dc.check(path, dest, boxes=[("lower third", (0.1, 0.7, 0.9, 0.8))])
        self.assertEqual(failed(low), ["safe_zone"], dc.format_report(low))
        self.assertIn("lower third: crosses bottom", dc.format_report(low))
        self.assertEqual(statuses(dc.check(path, dest))["safe_zone"], dc.SKIP)

    def test_video_bitrate_floor_only_when_a_destination_sets_one(self):
        path = self.master()
        self.assertEqual(statuses(self.check(path))["video_bitrate"], dc.SKIP)
        dest = tiny("client_master")
        dest["expect"]["min_video_kbps"] = 1
        self.assertEqual(statuses(dc.check(path, dest, size=(64, 36)))["video_bitrate"], dc.PASS)
        dest["expect"]["min_video_kbps"] = 10 ** 9
        r = dc.check(path, dest, size=(64, 36), loudness=False)
        self.assertEqual((failed(r), statuses(r)["video_bitrate"]), ([], dc.WARN))

    def test_platform_limits_warn_and_never_fail(self):
        path = make(self.path(VERT), size="36x64", rate="30000/1001", lufs=-14.0)
        dest = tiny("social_9x16")
        s = statuses(dc.check(path, dest, loudness=False))
        self.assertEqual((s["video_bitrate_max"], s["file_size"], s["duration"]),
                         (dc.PASS, dc.PASS, dc.PASS))
        dest["expect"]["max_duration_s"] = 2.5  # the clip's own length passes with a frame's slack
        self.assertEqual(statuses(dc.check(path, dest, loudness=False))["duration"], dc.PASS)
        dest["expect"].update(max_video_kbps=0.001, max_file_mb=1e-6, max_duration_s=1)
        r = dc.check(path, dest, loudness=False)
        s = statuses(r)
        self.assertEqual((s["video_bitrate_max"], s["file_size"], s["duration"]),
                         (dc.WARN, dc.WARN, dc.WARN), dc.format_report(r))
        self.assertNotIn("duration", failed(r))
        none = statuses(dc.check(path, tiny("linkedin_9x16"), loudness=False))
        self.assertEqual((none["video_bitrate_max"], none["file_size"], none["duration"]),
                         (dc.SKIP, dc.SKIP, dc.SKIP))
        found = {x["check"]: x["found"] for x in r["checks"]}
        self.assertTrue(found["video_bitrate"].endswith("kbps"), found["video_bitrate"])


class TestPlatformValues(unittest.TestCase):
    """The per-platform values in the config (sources in platform-specs.md)."""

    def test_each_platform_gets_its_values(self):
        d = {k: dc.deliver.destination(CFG, k) for k in dc.deliver.destination_keys(CFG)}
        for k in ("youtube_16x9", "youtube_16x9_music", "youtube_9x16"):
            self.assertEqual((d[k]["resolve"]["VideoQuality"], d[k]["audio"]["bitrate_kbps"],
                              d[k]["expect"]["min_video_kbps"]), (60000, 384, 35000), k)
        self.assertEqual((d["youtube_16x9_hd"]["resolve"]["VideoQuality"],
                          d["youtube_16x9_hd"]["expect"]["min_video_kbps"]), (16000, 8000))
        for k, x in d.items():
            self.assertEqual(dc.deliver.destination_problems(x), [], k)
        bad = dict(d["social_9x16"], expect={**d["social_9x16"]["expect"], "max_file_mb": "300"})
        self.assertIn("expect.max_file_mb must be a positive number",
                      dc.deliver.destination_problems(bad))
        for k in ("social_9x16", "social_9x16_music", "social_4x5", "social_1x1"):
            self.assertEqual(d[k]["resolve"], {"NetworkOptimization": True, "VideoQuality": 16000,
                                               "EncodingProfile": "High"}, k)
            self.assertLessEqual(d[k]["resolution"]["width"], 1920, k)  # Meta's column limit
            self.assertEqual((d[k]["expect"]["max_video_kbps"], d[k]["expect"]["max_file_mb"],
                              d[k]["expect"]["max_duration_s"]), (25000, 300, 180), k)
            self.assertLess(d[k]["resolve"]["VideoQuality"], d[k]["expect"]["max_video_kbps"])
        shorts = d["youtube_9x16"]
        self.assertEqual((shorts["resolution"], shorts["codec"], shorts["captions"],
                          shorts.get("min_fps"), shorts["expect"]["max_duration_s"]),
                         ({"width": 2160, "height": 3840}, "H265", "burnin", None, 180))
        for k in ("linkedin_16x9", "linkedin_9x16", "linkedin_1x1", "substack_16x9"):
            self.assertNotIn("VideoQuality", d[k]["resolve"], k)  # not assessed here

    def test_upscale_warns_and_a_native_timeline_does_not(self):
        shorts = dc.deliver.destination(CFG, "youtube_9x16")
        self.assertIn("scale the picture up", dc.deliver.upscale_warnings(shorts, (1080, 1920))[0])
        self.assertEqual(dc.deliver.upscale_warnings(shorts, (2160, 3840)), [])
        self.assertEqual(dc.deliver.upscale_warnings(shorts, None), [])
        self.assertEqual(dc.deliver.upscale_warnings(
            dc.deliver.destination(CFG, "social_9x16"), (2160, 3840)), [])


if __name__ == "__main__":
    unittest.main()
