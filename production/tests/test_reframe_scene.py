"""
Offline tests for reframe_plan's use of scene reads (composition.py and the
scene hooks in reframe.py): a span with no face is cropped on the saliency
centre, a face still wins, a tie between faces is broken toward the saliency
centre, each span records a move type (hold, pan, track) and composition
numbers, and a plan with no scene data is what it was, byte for byte.
Synthetic reads and boxes only; no media, no Resolve.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import composition, cutlist, reframe  # noqa: E402
from test_reframe import (HD, HD720, FakeReader, face, fake_detect, full,  # noqa: E402
                          synthetic_manifest)


def read(cx, cy, w, h, src=HD, mad=None, ratio=2.0):
    """A scene.read() dict whose attention box is centred on (cx, cy) source px."""
    out = {"saliency": {"attention": [{"x": (cx - w / 2.0) / src[0], "y": (cy - h / 2.0) / src[1],
                                       "w": w / float(src[0]), "h": h / float(src[1])}],
                        "objectness": []},
           "sharpness": {"ratio": ratio}, "depth": {"depth": None, "reason": "no model"}}
    if mad is not None:
        out["motion"] = {"mad": mad}
    return out


def reads(centres, w=200, h=300, src=HD, mad=0.05):
    return [(float(t), read(cx, cy, w, h, src, mad)) for t, (cx, cy) in enumerate(centres)]


def faces(per_sample, src=HD):
    return [(float(t), [face(*f, src) for f in fs]) for t, fs in enumerate(per_sample)]


class TestSceneSpan(unittest.TestCase):
    def test_no_face_the_saliency_centre_drives_the_crop(self):
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"],
                               scene=reads([(1400, 540)] * 8))
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["x"], p["y"]), (reframe.SCENE, 1400.0, 540.0))
        self.assertIn("no face", p["reason"])
        self.assertEqual(sp["scene"]["subject"], "saliency")
        self.assertEqual(sp["scene"]["centre"], {"x": 1400.0, "y": 540.0})
        self.assertIsNone(sp["scene"]["depth"])
        self.assertEqual(sp["face_samples"], 0)
        self.assertIn("saliency", sp["review"][0])  # weaker than a face: a person looks
        self.assertIn("x", reframe.entry(p, HD, full(HD), sp["face"], sp["review"]))

    def test_filtered_faces_stay_no_face_with_a_scene_read(self):
        # the faces sit outside the subject region; saliency lands on those same faces
        sp = reframe.plan_span(faces([[(1500, 540, 200, 220)]] * 8), HD, full(HD), ["9x16"],
                               subject_region=(0, 0, 900, 1080), scene=reads([(1500, 540)] * 8))
        self.assertEqual(sp["aspects"]["9x16"]["status"], reframe.NO_FACE)
        self.assertEqual(sp["ignored"], 8)
        self.assertNotIn("scene", sp)

    def test_a_wide_attention_box_with_a_good_centre_passes_for_review(self):
        # a box 1000 px wide cannot sit inside a 9x16 crop; its centre does
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"],
                               scene=reads([(1000, 540)] * 8, w=1000, h=900))
        self.assertEqual(sp["aspects"]["9x16"]["status"], reframe.SCENE)
        self.assertTrue(sp["review"])  # weaker than a face: a person looks

    def test_a_face_still_wins(self):
        sp = reframe.plan_span(faces([[(500, 540, 200, 220)]] * 8), HD, full(HD), ["9x16"],
                               scene=reads([(1400, 540)] * 8))
        p = sp["aspects"]["9x16"]
        plain = reframe.plan_span(faces([[(500, 540, 200, 220)]] * 8), HD, full(HD), ["9x16"])
        self.assertEqual((p["x"], p["y"], p["status"]),
                         (plain["aspects"]["9x16"]["x"], plain["aspects"]["9x16"]["y"], reframe.PASS))
        self.assertEqual(p["x"], 500.0)
        self.assertEqual(sp["scene"]["subject"], "face")
        self.assertEqual(sp["review"], [])

    def test_a_tie_between_faces_goes_to_the_saliency_centre(self):
        two = [[(400, 540, 160, 160), (1400, 540, 160, 160)]] * 8
        plain = reframe.plan_span(faces(two), HD, full(HD), ["9x16"])
        near = reframe.plan_span(faces(two), HD, full(HD), ["9x16"], scene=reads([(1380, 540)] * 8))
        other = reframe.plan_span(faces(two), HD, full(HD), ["9x16"], scene=reads([(420, 540)] * 8))
        self.assertEqual(near["aspects"]["9x16"]["x"], 1400.0)
        self.assertEqual(other["aspects"]["9x16"]["x"], 400.0)
        self.assertIn("saliency centre", near["review"][0])
        self.assertIn("chosen one at x 1400 (in 8 samples) and another at x 400", near["review"][0])
        self.assertNotIn("saliency", plain["review"][0])
        # an explicit speaker still overrides
        named = reframe.plan_span(faces(two), HD, full(HD), ["9x16"], speaker_x=400,
                                  scene=reads([(1380, 540)] * 8))
        self.assertEqual(named["aspects"]["9x16"]["x"], 400.0)

    def test_a_still_subject_holds(self):
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"],
                               scene=reads([(1000, 540)] * 8, mad=0.001))
        self.assertEqual(sp["scene"]["move_type"], composition.HOLD)
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["move_type"], "hold")
        self.assertIsNone(p["composition"]["lead_px"])
        # centre wobble in a frame that barely changes is noise, not a pan
        wob = reads([(1000 + 90 * (i % 2), 540) for i in range(8)], mad=0.001)
        self.assertEqual(reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"],
                                           scene=wob)["scene"]["move_type"], "hold")

    def test_a_moving_subject_pans_or_tracks(self):
        pan = reads([(900 + 25 * i, 540) for i in range(8)])       # 175 px: 9% of the frame
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"], scene=pan)
        self.assertEqual(sp["scene"]["move_type"], "pan")
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["move_type"]), (reframe.SCENE, "pan"))
        self.assertGreater(p["composition"]["lead_px"], 0)  # room ahead on the right
        track = reads([(500 + 120 * i, 540) for i in range(8)])    # 840 px: 44% of the frame
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"], scene=track)
        self.assertEqual(sp["scene"]["move_type"], "track")
        self.assertEqual(sp["scene"]["motion"], 0.05)
        # the crop stays static and the plan says it cannot hold a subject that travels
        self.assertEqual(sp["aspects"]["9x16"]["status"], reframe.MANUAL)

    def test_a_face_that_moves_records_its_move_with_the_face_path(self):
        per = [[(700 + 30 * i, 540, 200, 220)] for i in range(8)]
        sp = reframe.plan_span(faces(per), HD, full(HD), ["9x16"], scene=reads([(900, 540)] * 8))
        self.assertEqual(sp["scene"]["subject"], "face")
        self.assertEqual(sp["scene"]["move_type"], "pan")  # 210 px of 1920

    def test_composition_numbers(self):
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"],
                               scene=reads([(960, 540)] * 8, w=200, h=300))
        c = sp["aspects"]["9x16"]["composition"]
        p = sp["aspects"]["9x16"]
        half_w = 540.0 / p["scale"]
        self.assertAlmostEqual(c["left_px"], (960 - 100 - (960 - half_w)) * p["scale"], delta=0.2)
        self.assertEqual(c["left_px"], c["right_px"])
        self.assertEqual(c["safe"], 1.0)
        want = 1.0 - (200 * 300 * p["scale"] ** 2) / (1080 * 1920)
        self.assertAlmostEqual(c["negative_space"], want, places=3)

    def test_no_scene_data_is_the_plan_it_was(self):
        for per in ([[]] * 8, [[(700, 540, 200, 220)]] * 8,
                    [[(400, 540, 160, 160), (1400, 540, 160, 160)]] * 8):
            base = reframe.plan_span(faces(per), HD, full(HD), ["9x16", "1x1"])
            for scene in (None, [], [(float(t), None) for t in range(8)]):
                got = reframe.plan_span(faces(per), HD, full(HD), ["9x16", "1x1"], scene=scene)
                self.assertEqual(json.dumps(got, sort_keys=True), json.dumps(base, sort_keys=True))
            self.assertNotIn("scene", base)
            self.assertNotIn("composition", base["aspects"]["9x16"])

    def test_a_read_with_no_subject_is_no_scene(self):
        empty = [(float(t), {"saliency": {"attention": [], "objectness": []}}) for t in range(8)]
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"], scene=empty)
        self.assertEqual(sp["aspects"]["9x16"]["status"], reframe.NO_FACE)

    def test_objectness_stands_in_for_attention(self):
        r = read(1000, 540, 200, 300)
        r["saliency"] = {"attention": [], "objectness": r["saliency"]["attention"]}
        sp = reframe.plan_span(faces([[]] * 8), HD, full(HD), ["9x16"], scene=[(float(t), r) for t in range(8)])
        self.assertEqual(sp["aspects"]["9x16"]["x"], 1000.0)


class TestPlanWithScene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = synthetic_manifest(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def scene(self, image, previous):
        return read(900, 360, 120, 200, HD720, mad=0.0 if previous is None else 0.001)

    def test_plan_report_and_manifest_copy(self):
        report = reframe.plan(self.m, reader=FakeReader(), detect=fake_detect, scene=self.scene)
        self.assertTrue(report["scene"])
        rows = report["rows"]
        # two equal-ish faces at 400 and 900: the scene's centre picks the one at 900
        self.assertEqual(rows[0]["face_x"], 900.0)
        self.assertIn("saliency centre", rows[0]["review"])
        self.assertEqual(rows[0]["move_type"], "hold")
        # the span past 100 s has no face: cropped on the saliency centre
        self.assertEqual([r["status"] for r in rows if r["clip"] == "b_30"], ["scene", "scene"])
        copy = reframe.apply(self.m, report)
        cutlist.validate_manifest(copy)
        e = copy["clips"][1]["spans"][0]["reframe"]["9x16"]
        self.assertEqual((e["x"], e["move_type"]), (900.0, "hold"))
        self.assertIn("composition", e)
        self.assertTrue(copy["reframe_plan"]["scene"])
        cols = reframe.tsv(rows).splitlines()[0].split("\t")
        self.assertEqual(cols, list(reframe.COLUMNS + reframe.SCENE_COLUMNS))

    def test_plan_without_scene_is_unchanged(self):
        a = reframe.plan(self.m, reader=FakeReader(), detect=fake_detect)
        b = reframe.plan(self.m, reader=FakeReader(), detect=fake_detect, scene=lambda i, p: None)
        self.assertNotIn("scene", a)
        self.assertIs(b.pop("scene"), False)  # asked for and unavailable: the report says so
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertIn("scene reads unavailable", reframe.summary({**a, "scene": False}))
        self.assertNotIn("scene reads unavailable", reframe.summary(a))
        self.assertEqual(reframe.tsv(a["rows"]).splitlines()[0].split("\t"), list(reframe.COLUMNS))
        self.assertNotIn("scene", reframe.apply(self.m, a)["reframe_plan"])

    def test_framing_plan_says_when_scene_reads_are_unavailable(self):
        item = [{"name": "a", "path": "/x/a.mov", "in": 0.0, "out": 10.0}]
        kw = dict(aspect="9x16", reader_for=lambda _: FakeReader(), detect=fake_detect)
        self.assertIs(reframe.plan_items(item, scene=lambda i, p: None, **kw)["scene"], False)
        self.assertTrue(reframe.plan_items(item, scene=self.scene, **kw)["scene"])
        self.assertNotIn("scene", reframe.plan_items(item, **kw))


if __name__ == "__main__":
    unittest.main()
