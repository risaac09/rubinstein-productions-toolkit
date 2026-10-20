"""
Offline tests for rpresolve.overlay and the overlay-check command.
Synthetic arrays and ffmpeg-generated ProRes clips only; no Resolve, no
real footage. Skipped where numpy (or, for the clips, ffmpeg) is missing.

Run: /usr/bin/python3 -m unittest production.tests.test_overlay -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import measure, overlay
except ImportError:  # numpy missing on this interpreter
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")
SCRIPT = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
ACCENT = "#3ad06e"  # a test colour (not a brand value) that a BT.601/709 mix-up moves far


def edge_frame(premultiplied=False, size=40):
    """An accent-coloured square with a 3 px alpha ramp on a clear field."""
    y, x = np.mgrid[0:size, 0:size].astype(float)
    inset = np.minimum(np.minimum(x - 10, size - 11 - x), np.minimum(y - 10, size - 11 - y))
    a = np.clip((inset + 1.5) / 3, 0, 1)
    rgba = np.zeros((size, size, 4))
    rgba[..., :3] = overlay.parse_hex(ACCENT)
    if premultiplied:
        rgba[..., :3] *= a[..., None]
    rgba[..., 3] = a
    return rgba


@needs_numpy
class TestPure(unittest.TestCase):
    def test_alpha_formats(self):
        for fmt, bits in (("yuva444p10le", 10), ("yuva444p12le", 12), ("rgba64le", 16),
                          ("rgba", 8), ("gbrap16le", 16), ("ya16le", 16)):
            self.assertTrue(overlay.has_alpha(fmt), fmt)
            self.assertEqual(overlay.alpha_bits(fmt), bits, fmt)
        for fmt in ("yuv422p10le", "yuv420p", "rgb48le", "", None):
            self.assertFalse(overlay.has_alpha(fmt), fmt)

    def test_straight_edge_reads_clean_only_as_straight(self):
        fr = overlay.fringe(edge_frame())
        self.assertGreater(fr["edge_pixels"], 0)
        self.assertLess(fr["straight"], 0.01)
        self.assertGreater(fr["premultiplied"], 100)

    def test_premultiplied_edge_reads_clean_only_as_premultiplied(self):
        fr = overlay.fringe(edge_frame(premultiplied=True))
        self.assertLess(fr["premultiplied"], 0.01)
        self.assertGreater(fr["straight"], 100)

    def test_translucent_line_into_an_opaque_square_is_not_an_edge(self):
        rgba = np.zeros((40, 40, 4))
        rgba[18:20, :, :3], rgba[18:20, :, 3] = 0.66, 0.3   # a grey hairline at 0.3 alpha
        rgba[15:23, 15:23, :3], rgba[15:23, 15:23, 3] = (0.76, 0.27, 0.19), 1.0  # a square
        self.assertEqual(overlay.fringe(rgba)["edge_pixels"], 0)

    def test_parsers(self):
        self.assertEqual(overlay.parse_sample("accent=12,34"), ("accent", 12, 34, None))
        self.assertEqual(overlay.parse_sample("light=1,2@1.5"), ("light", 1, 2, 1.5))
        self.assertEqual(overlay.parse_expect("color_space=bt709"), ("color_space", "bt709"))
        for bad in ("light", "light=1", "=1,2", "light=1,2@x"):
            with self.assertRaises(ValueError):
                overlay.parse_sample(bad)
        with self.assertRaises(ValueError):
            overlay.parse_expect("codec=prores")
        with self.assertRaises(ValueError):
            overlay.parse_hex("#c8923")
        np.testing.assert_allclose(overlay.parse_hex("3AD06E"), [58 / 255, 208 / 255, 110 / 255])

    def test_values_file(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d, "good.json"), Path(d, "bad.json")
            good.write_text(json.dumps({"accent": ACCENT}))
            bad.write_text(json.dumps({"accent": 12}))
            self.assertIn("accent", overlay.load_values(str(good)))
            with self.assertRaises(ValueError):
                overlay.load_values(str(bad))

    def test_sample_box_refuses_the_frame_edge(self):
        with self.assertRaises(ValueError):
            overlay.sample_box(edge_frame(), 1, 20)


TAGS = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709"]
RAMP = "clip((min(min(X-80,239-X),min(Y-45,134-Y))+1.5)/3,0,1)*if(lt(N,1),0,1)"


@unittest.skipUnless(np is not None and os.access(measure.FFMPEG, os.X_OK), "ffmpeg missing")
class TestFiles(unittest.TestCase):
    """Three-frame 320x180 clips: an accent box, clear on frame 0, a 3 px ramp."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        src = "color=c=black@0:s=320x180:r=24000/1001:d=0.125,format=rgba"
        r, g, b = (int(ACCENT[i:i + 2], 16) for i in (1, 3, 5))
        straight = f"geq=r='{r}':g='{g}':b='{b}':a='255*{RAMP}'"
        premult = f"geq=r='{r}*{RAMP}':g='{g}*{RAMP}':b='{b}*{RAMP}':a='255*{RAMP}'"
        # The format sits inside the chain so this scale does the matrix (ffmpeg 6 would
        # otherwise insert its own BT.601 conversion after it).
        to709 = "scale=out_color_matrix=bt709:out_range=tv,format=yuva444p10le"
        alpha = ["-c:v", "prores_ks", "-profile:v", "4444"]
        clips = {
            "straight": (src, f"{straight},{to709}", alpha, TAGS),
            "premult": (src, f"{premult},{to709}", alpha, TAGS),
            "s601": (src, f"{straight},scale=out_color_matrix=bt601:out_range=tv,format=yuva444p10le",
                     alpha, [*TAGS[:2], "-colorspace", "smpte170m", *TAGS[4:]]),
            "opaque": ("testsrc2=s=320x180:r=24000/1001:d=0.125", to709.replace("yuva444p10le", "yuv422p10le"),
                       ["-c:v", "prores_ks", "-profile:v", "3"], TAGS),
        }
        cls.files = {}
        for name, (lavfi, vf, codec, tags) in clips.items():
            out = str(d / f"{name}.mov")
            subprocess.run([measure.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", lavfi,
                            "-vf", vf, *codec, *tags, out], check=True)
            cls.files[name] = out
        # BT.601 values retagged BT.709 in the ProRes frame headers: tags pass, colour does not.
        cls.files["matrix601"] = str(d / "matrix601.mov")
        subprocess.run([measure.FFMPEG, "-v", "error", "-y", "-i", cls.files["s601"], "-c", "copy",
                        "-bsf:v", "prores_metadata=colorspace=bt709", cls.files["matrix601"]], check=True)
        cls.values = str(d / "values.json")
        Path(cls.values).write_text(json.dumps({"accent": ACCENT, "light": "#e8e8e8"}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_check(self, name, **kw):
        kw.setdefault("hold", 0.08)
        kw.setdefault("values", overlay.load_values(self.values))
        kw.setdefault("samples", [("accent", 160, 90, None)])
        return overlay.check(self.files[name], **kw)

    def status(self, report, row):
        return [r["status"] for r in report["rows"] if r["row"] == row]

    def test_a_clean_master_passes(self):
        r = self.run_check("straight", expect=[("color_space", "bt709"), ("color_range", "tv")],
                           fps="24000/1001")
        self.assertEqual(r["status"], "pass", overlay.format_report(r))
        for row in ("alpha", "codec", "clear", "fringe", "brand"):
            self.assertEqual(self.status(r, row), ["PASS"], row)

    def test_an_opaque_file_fails_alpha_and_clear(self):
        r = self.run_check("opaque", samples=[])
        self.assertEqual(self.status(r, "alpha"), ["FAIL"])
        self.assertEqual(self.status(r, "clear"), ["FAIL"])
        self.assertEqual(self.status(r, "codec"), ["WARN"])

    def test_a_premultiplied_file_fails_when_straight_is_expected(self):
        r = self.run_check("premult", samples=[])
        self.assertEqual(self.status(r, "fringe"), ["FAIL"])
        self.assertIn("reads cleaner as premultiplied", overlay.format_report(r))
        r = self.run_check("premult", samples=[], alpha_mode="premultiplied")
        self.assertEqual(self.status(r, "fringe"), ["PASS"])

    def test_the_wrong_matrix_fails_brand_with_the_tags_passing(self):
        r = self.run_check("matrix601", expect=[("color_space", "bt709")])
        self.assertEqual(self.status(r, "tags"), ["PASS"])
        self.assertEqual(self.status(r, "brand"), ["FAIL"])

    def test_a_wrong_tag_or_a_wrong_value_fails(self):
        r = self.run_check("straight", expect=[("color_space", "smpte170m")])
        self.assertEqual(self.status(r, "tags"), ["FAIL"])
        r = self.run_check("straight", samples=[("light", 160, 90, None)])
        self.assertEqual(self.status(r, "brand"), ["FAIL"])

    def test_a_sample_off_the_graphic_fails(self):
        r = self.run_check("straight", samples=[("accent", 20, 20, None)])
        brand = [row for row in r["rows"] if row["row"] == "brand"][0]
        self.assertEqual(brand["status"], "FAIL")
        self.assertIn("not fully opaque", brand["detail"])

    def test_the_command_exit_codes(self):
        env = dict(os.environ, RPRESOLVE_FFMPEG=measure.FFMPEG, RPRESOLVE_FFPROBE=measure.FFPROBE)
        base = [sys.executable, str(SCRIPT), "overlay-check", "--hold", "0.08",
                "--values", self.values, "--sample", "accent=160,90"]
        ok = subprocess.run(base + [self.files["straight"]], env=env, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        bad = subprocess.run(base + [self.files["premult"], "--json"], env=env,
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertEqual(json.loads(bad.stdout)["status"], "fail")
        missing = subprocess.run(base + [self.files["straight"]], capture_output=True, text=True,
                                 env=dict(env, RPRESOLVE_FFPROBE="/nonexistent/ffprobe"))
        self.assertEqual(missing.returncode, 2, missing.stderr)


if __name__ == "__main__":
    unittest.main()
