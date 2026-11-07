"""Tests for rpresolve.proxies. Run: /usr/bin/python3 -m unittest discover production/tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import proxies  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
