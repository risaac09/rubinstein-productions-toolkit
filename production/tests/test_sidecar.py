"""
Offline tests for rpresolve.sidecar: clip ids from card trees, the hash
offload.sh recorded, the envelope header and its validator.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import sidecar as sc  # noqa: E402

H = "a" * 64
BT709 = {"tag": "bt709", "read_as": "BT.1886 display: Rec.709 primaries, 2.4 gamma, D65"}
PRIM = {"tag": "bt709", "read_as": "Rec.709"}


def doc(name, lane, **kw):
    d = sc.header(name, 1, "/Volumes/Work/x/clip.mov", **kw)
    d[lane] = {}
    return d


class CardKey(unittest.TestCase):
    def test_panasonic_tree(self):
        self.assertEqual(sc.card_key("/A/S5II-a1b2c3/DCIM/100_PANA/P1039630.MOV"), "S5II-a1b2c3/P1039630")

    def test_work_copy_matches_archive_copy(self):
        a = sc.card_key("/Volumes/Archive/2026-x/S5II-a1b2c3/DCIM/100_PANA/P1039630.MOV")
        w = sc.card_key("/Volumes/Work/Active/2026-x/01-Source/S5II-a1b2c3/DCIM/100_PANA/P1039630.MOV")
        self.assertEqual(a, w)

    def test_flat_render_folder(self):
        self.assertEqual(sc.card_key("/A/For-review/C016.mov"), "For-review/C016")

    def test_shared_ids(self):
        got = sc.shared_ids(["/a/CARD1/x.wav", "/b/CARD1/x.mov", "/b/CARD1/y.mov"])
        self.assertEqual(got, {"CARD1/x": ["/a/CARD1/x.wav", "/b/CARD1/x.mov"]})
        self.assertEqual(sc.shared_ids(["/a/c-aaaaaa/x.mov", "/a/c-bbbbbb/x.mov"]), {})


class SourceSha(unittest.TestCase):
    def test_reads_sums_and_none_for_render(self):
        with tempfile.TemporaryDirectory() as d:
            card = os.path.join(d, "S5II-a1b2c3", "DCIM", "100_PANA")
            os.makedirs(card)
            clip = os.path.join(card, "P1039630.MOV")
            Path(clip).write_bytes(b"x")
            Path(d, "S5II-a1b2c3.SHA256SUMS").write_text(
                f"{'b' * 64}  DCIM/100_PANA/P1039629.MOV\n{H}  DCIM/100_PANA/P1039630.MOV\n")
            self.assertEqual(sc.source_sha256(clip), H)
            self.assertIsNone(sc.source_sha256(os.path.join(card, "P1039631.MOV")))
            render = os.path.join(d, "renders", "v01.mov")
            self.assertIsNone(sc.source_sha256(render))

    def test_no_sums_file(self):
        self.assertIsNone(sc.source_sha256("/nope/S5II-a1b2c3/DCIM/x.MOV"))


class Validate(unittest.TestCase):
    def ok(self, d):
        self.assertEqual(sc.validate(d), [])

    def test_valid_per_lane(self):
        self.ok(doc("cine.perceive", "cine", transfer=BT709, primaries=PRIM))
        self.ok(doc("sound.audio-report", "sound"))
        self.ok(doc("fusion.overlay-check", "fusion"))
        s = doc("story.story-packet", "story", clip_id=None)
        s["story"] = {"records": [{"clip_id": "S5II-a1b2c3/P1"}]}
        self.ok(s)

    def test_header_fields(self):
        d = doc("sound.audio-report", "sound")
        self.assertEqual(d["clip_id"], "x/clip")
        self.assertEqual(d["source"], {"path": "/Volumes/Work/x/clip.mov", "sha256": None})
        self.assertEqual(d["generated_by"]["tool"], "rpresolve.audio-report")

    def test_rejections(self):
        good = doc("cine.perceive", "cine", transfer=BT709, primaries=PRIM)
        d = copy.deepcopy(good); del d["schema"]
        self.assertIn("schema missing", sc.validate(d))
        d = copy.deepcopy(good); d["sound"] = {}
        self.assertTrue(any("exactly one lane" in p for p in sc.validate(d)))
        d = copy.deepcopy(good); d["sound"] = d.pop("cine")
        self.assertTrue(any("does not match" in p for p in sc.validate(d)))
        d = copy.deepcopy(good); d["transfer"] = None
        self.assertTrue(any("cine sidecar must name transfer" in p for p in sc.validate(d)))
        d = copy.deepcopy(good); d["primaries"] = None
        self.assertTrue(any("must name primaries" in p for p in sc.validate(d)))

    def test_other_rules(self):
        good = doc("sound.audio-report", "sound")
        for key, val in [("extra", 1), ("generated_at", "2026-10-20 19:40"), ("clip_id", 3),
                         ("transfer", {"tag": "x"}), ("source", {"path": "rel/x.mov", "sha256": None})]:
            d = copy.deepcopy(good); d[key] = val
            self.assertTrue(sc.validate(d), key)
        d = copy.deepcopy(good); d["schema"]["version"] = "2"
        self.assertTrue(sc.validate(d))
        self.assertEqual(sc.validate([]), ["sidecar is not an object"])


if __name__ == "__main__":
    unittest.main()
