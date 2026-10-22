"""
Tests for rpresolve.pacing on synthetic word lists and a synthetic manifest.

Run: python3 -m unittest production.tests.test_pacing -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import pacing  # noqa: E402


def words(*spans):
    return [{"start": a, "end": b, "word": f"w{i}"} for i, (a, b) in enumerate(spans)]


class PacingTest(unittest.TestCase):
    def test_wpm_and_pause_ratio(self):
        # 4 words in 10 s; a 0.1 s gap is joined, a 2 s gap is a pause.
        w = words((1.0, 1.5), (1.6, 2.0), (4.0, 4.5), (4.5, 5.0))
        r = pacing.report(w, 10.0)
        self.assertEqual(r["wpm"], 24.0)
        self.assertEqual(r["pauses"], 1)
        self.assertEqual(r["longest_pause_s"], 2.0)
        self.assertAlmostEqual(r["pause_ratio"], 0.8)        # 2 s of speech in 10
        self.assertEqual(r["speaking_wpm"], 120.0)

    def test_manifest_spans_keep_words_by_middle(self):
        w = words((0.0, 0.4), (0.9, 1.3), (5.0, 5.5), (9.0, 9.5))
        kept, length = pacing.cut_words(w, [{"in": 1.0, "out": 2.0}, {"in": 5.0, "out": 6.0}])
        self.assertEqual(length, 2.0)
        self.assertEqual([k["word"] for k in kept], ["w1", "w2"])  # w1's middle is 1.1
        self.assertEqual(kept[0]["start"], 0.0)                     # clipped to the span
        self.assertAlmostEqual(kept[0]["end"], 0.3)
        self.assertEqual((kept[1]["start"], kept[1]["end"]), (1.0, 1.5))

    def test_split_edit_moves_words_with_the_sound(self):
        # Span 2 has a J cut: its sound starts 0.5 s early, over the end of span 1.
        w = words((1.6, 1.8), (4.6, 4.8))
        kept, length = pacing.cut_words(w, [{"in": 0.0, "out": 2.0},
                                            {"in": 5.0, "out": 6.0, "audio_lead": 0.5}])
        self.assertEqual(length, 3.0)
        self.assertEqual([k["word"] for k in kept], ["w1"])  # span 1's sound stops at 1.5
        self.assertAlmostEqual(kept[0]["start"], 1.6)         # 2.0 - 0.5 + (4.6 - 4.5)

    def test_caption_speeds(self):
        c = pacing.caption_speeds([(0, 1, "a" * 10), (1, 2, "b" * 30), (2, 4, "c" * 20)])
        self.assertEqual((c["cps_median"], c["cps_max"], c["over"]), (10.0, 30.0, 1))

    def test_build_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            wpath = os.path.join(tmp, "w.json")
            with open(wpath, "w") as f:
                json.dump({"segments": [{"words": words((0.5, 1.0), (1.0, 1.5))}]}, f)
            mpath = os.path.join(tmp, "m.json")
            with open(mpath, "w") as f:
                json.dump({"speech": False, "source_path": "/x.mov", "words": wpath,
                           "clips": [{"name": "a", "spans": [{"in": 0.0, "out": 3.0}]}]}, f)
            self.assertEqual(pacing.build(manifest_path=mpath)["wpm"], 40.0)
            self.assertEqual(pacing.build(words_path=wpath)["duration_s"], 1.5)
            with self.assertRaises(pacing.PacingError):
                pacing.build(words_path=wpath, duration=1.0)  # a word past the end


if __name__ == "__main__":
    unittest.main()
