"""Tests for rpresolve.proxies. Run: /usr/bin/python3 -m unittest discover production/tests -v"""

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import filmed, proxies  # noqa: E402


class TestProxies(unittest.TestCase):
    def test_stills_and_other_media_get_no_proxy(self):
        self.assertEqual(proxies.braw_clips(["/c/A.braw", "/c/Stills/S.braw", "/c/B.mov"]), ["/c/A.braw"])

    def test_nothing_to_do_starts_nothing(self):
        self.assertIsNone(proxies.start("P", ["/c/B.mov"], popen=self.fail))

    def test_unbuilt_decoder_is_reported_not_started(self):
        r = proxies.start("P", ["/c/A.braw"], popen=self.fail, exists=lambda p: False)
        self.assertIn("build.sh", r["error"])

    def test_project_name_makes_a_safe_folder(self):
        self.assertTrue(proxies.proxy_dir("Minimalist Claude Test").endswith("/Minimalist-Claude-Test"))

    def test_plan(self):
        d = "/p"
        clips = [(1, {"File Path": "/c/A.braw"}), (2, {"File Path": "/c/B.braw"}),
                 (3, {"File Path": "/c/C.braw", "Proxy Media Path": "/p/C.mov"}),
                 (4, {"File Path": "/c/D.mov"}), (5, {"File Path": ""})]
        rows = proxies.plan_links(clips, d, exists=lambda p: p == "/p/A.mov")
        self.assertEqual([r["action"] for r in rows], ["link", "no proxy yet", "linked already"])


class TestFilmed(unittest.TestCase):
    def rows(self):
        return [{"name": "A", "uid": "a", "path": "/a", "fps": 24.0, "frames": 660, "length": 660},
                {"name": "B", "uid": "b", "path": "/b", "fps": 24.0, "frames": 100, "length": 100}]

    def test_snap(self):
        self.assertEqual(filmed.snap(23.999), "24")
        self.assertEqual(filmed.snap(29.97003), "29.97")
        self.assertEqual(filmed.snap(12.5), "12.5")

    def test_camera_frame_time_beats_a_drifting_average(self):
        def run(cmd, **kw):
            out = "0.0416666666666667" if "-OffspeedFrameTime" in cmd else "1128500/47047,2257"
            return mock.Mock(stdout=out)
        self.assertEqual(filmed.read("/a", run=run), {"fps": "24", "frames": 2257})

    def test_no_camera_frame_time_uses_the_average(self):
        def run(cmd, **kw):
            return mock.Mock(stdout="" if "-OffspeedFrameTime" in cmd else "25/1,50")
        self.assertEqual(filmed.read("/a", run=run), {"fps": "25", "frames": 50})

    def test_mixed_rates_are_refused(self):
        rates = {"/a": "24", "/b": "25"}
        with self.assertRaisesRegex(ValueError, "different rates"):
            filmed.timeline_rate(self.rows(), lambda p: {"fps": rates[p], "frames": 1})

    def test_a_clip_at_the_wrong_rate_is_refused(self):
        rows = self.rows()
        rows[0]["fps"] = 29.97
        with self.assertRaisesRegex(ValueError, "link_proxies"):
            filmed.timeline_rate(rows, lambda p: {"fps": "24", "frames": 1})

    def test_trim_cuts_to_real_frames_and_moves_the_next_row(self):
        rows = self.rows()
        filmed.trim(rows, {"a": 529, "b": 100})
        self.assertEqual((rows[0]["end"], rows[0]["length"], rows[1]["record"]), (529, 529, 529))
        self.assertNotIn("end", rows[1])


if __name__ == "__main__":
    unittest.main()
