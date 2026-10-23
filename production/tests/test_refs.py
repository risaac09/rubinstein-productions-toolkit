"""
Tests for rpresolve.refs and its users: the one refusal of a path inside a
marked reference library, in the render plan's destination checks, the
deliver-manifest writer and perceive's --out; and perceive --targets. Synthetic
arrays and lavfi frames only.

Run: /usr/bin/python3 -m unittest production.tests.test_refs -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import deliver, manifest as mf, refs  # noqa: E402
from test_delivercheck import CFG  # noqa: E402

try:
    import numpy as np
    from rpresolve import measure, perceive
except ImportError:
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")
NAME = "SW001_Guest_01_example-clip_16x9.mp4"
YT = deliver.destination(CFG, "youtube_16x9")


class TestLibrary(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = os.path.realpath(os.path.join(tmp.name, "library"))
        self.out = os.path.realpath(os.path.join(tmp.name, "elsewhere"))
        os.makedirs(os.path.join(self.lib, "ref1", "deep"))
        os.makedirs(self.out)
        Path(self.lib, refs.MARKER).write_text("A private reference library.\n")

    def test_is_reference(self):
        self.assertTrue(refs.is_reference(self.lib))
        self.assertTrue(refs.is_reference(os.path.join(self.lib, "ref1", "deep")))
        self.assertTrue(refs.is_reference(os.path.join(self.lib, "ref1", "not-yet", "x.json")))
        self.assertFalse(refs.is_reference(self.out))
        self.assertFalse(refs.is_reference(os.path.join(self.out, "new", "x.mp4")))
        self.assertIsNone(refs.reference_problem(self.out))
        self.assertIn(refs.MARKER, refs.reference_problem(self.lib))

    def test_a_link_into_the_library_is_refused(self):
        link = os.path.join(self.out, "link")
        os.symlink(os.path.join(self.lib, "ref1"), link)
        self.assertTrue(refs.is_reference(link))

    def test_render_plan_destination_checks(self):
        inside = deliver.output_problems(os.path.join(self.lib, "ref1"), NAME, YT)
        self.assertTrue(any("reference library" in p for p in inside), inside)
        self.assertEqual(deliver.output_problems(self.out, NAME, YT), [])

    def test_deliver_manifest_refuses_inside_and_the_writer_too(self):
        root = os.path.join(self.lib, "ref1")
        with self.assertRaisesRegex(mf.ManifestError, "reference library"):
            mf.build(root, CFG, "p", "A-1", "me", "r.mp4", "a" * 64, "2026-01-01")
        with self.assertRaisesRegex(mf.ManifestError, "reference library"):
            mf.write(root, "text", [])
        self.assertEqual(os.listdir(root), ["deep"])
        # Outside the library the same call gets past the refusal (this folder has no
        # destination subfolders, so it stops later, for another reason).
        with self.assertRaises(mf.ManifestError) as cm:
            mf.build(self.out, CFG, "p", "A-1", "me", "r.mp4", "a" * 64, "2026-01-01")
        self.assertNotIn("reference library", str(cm.exception))

    @needs_numpy
    def test_perceive_out(self):
        self.assertIn("reference library", perceive.out_dir_problem(os.path.join(self.lib, "pkt")))
        self.assertIsNone(perceive.out_dir_problem(os.path.join(self.out, "pkt")))


@needs_numpy
class TestTargets(unittest.TestCase):
    def numbers(self, rgb, source=None):
        with tempfile.TemporaryDirectory() as tmp:
            return perceive.analyse(rgb, tmp, faces=False)

    def frame(self):
        # Cool shadows, warm highlights: a ramp with the hue pushed each way.
        t = np.linspace(0, 1, 96)[None, :, None] * np.ones((54, 1, 3))
        return np.clip(t * np.array([1.0, 0.95, 0.85]) + (1 - t) * np.array([0.0, 0.01, 0.08]) * 0.5, 0, 1)

    def test_fields_and_hue_shift(self):
        rgb = self.frame()
        source = {"transfer": {"tag": "bt709", "read_as": perceive.READ_AS}}
        t = perceive.targets(rgb, source, self.numbers(rgb))
        self.assertEqual(t["schema"], {"name": "cine.targets", "version": 1})
        for k in ("transfer", "generated_by", "generated_at", "luma_ire", "display_contrast_stops",
                  "palette", "bands", "hue_shift_shadows_vs_highlights_deg", "neutral_cast", "skin_lab",
                  "bands_split"):
            self.assertIn(k, t)
        self.assertEqual(set(t["luma_ire"]), {"p1", "p10", "p50", "p90", "p99"})
        self.assertEqual(set(t["bands"]), {"shadows", "mids", "highlights"})
        self.assertIsNone(t["skin_lab"])
        self.assertGreater(abs(t["hue_shift_shadows_vs_highlights_deg"]), 90)

    def test_non_rec709_withholds_bands(self):
        rgb = self.frame()
        source = {"transfer": {"tag": "smpte2084"}, "transfer_note": "tagged PQ"}
        t = perceive.targets(rgb, source, self.numbers(rgb))
        self.assertIsNone(t["bands"])
        self.assertIsNone(t["hue_shift_shadows_vs_highlights_deg"])

    @unittest.skipUnless(os.access(measure.FFMPEG if np is not None else "", os.X_OK), "ffmpeg missing")
    def test_two_runs_match_and_hold_no_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            still = os.path.join(tmp, "hero.png")
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90",
                            "-frames:v", "1", "-y", still], check=True)
            docs = []
            for n in ("a", "b"):
                out = os.path.join(tmp, n)
                perceive.perceive(still, out, faces=False, with_targets=True)
                text = Path(out, "targets.json").read_text()
                docs.append(json.loads(text))
                self.assertLess(len(text), 8000)
            for d in docs:
                d.pop("generated_at")
            self.assertEqual(docs[0], docs[1])
            self.assertFalse([k for k in docs[0] if "png" in k or "pixel" in k or "frame" in k])
            self.assertNotIn(".png", json.dumps(docs[0]))
            self.assertNotIn(tmp, json.dumps(docs[0]))  # no source path either


if __name__ == "__main__":
    unittest.main()
