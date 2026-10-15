"""
Framing for any delivery shape (reframe.py): the master shape, the head
rule, do-nothing, squeeze, stills as frames, and Pan and Tilt in Resolve's
units. Faces are given, not found: a fake reader and detector stand in for
ffmpeg and Vision, except in the stills test, which needs ffmpeg.

    /usr/bin/python3 -m unittest tests.test_framing
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpresolve import reframe  # noqa: E402

PYXIS = (6048, 4032)          # the Pyxis 3:2 picture as Resolve reads it
MASTER = (5952, 2646)         # a 2.25:1 festival timeline
S5II = (5952, 3968)           # the S5II 3:2 frame, recorded 1.5x squeezed
LUMA = (60, 40)               # the fake reader's grey frames: any size of the shape


class Reader:
    """Frames of a uniform grey picture (no bars) at luma_size, for a source
    of `size`; read() writes nothing, the detector answers by name."""

    def __init__(self, size, luma_size=LUMA, squeeze=None):
        self.size, self.luma_size, self.duration, self.squeeze = size, luma_size, 0.0, squeeze

    def read(self, times, folder):
        grey = bytes([128]) * (self.luma_size[0] * self.luma_size[1])
        out = []
        for t in times:
            path = os.path.join(folder, f"t{t:012.3f}.png")
            open(path, "wb").close()
            out.append((t, path, grey))
        return out


def detector(box, src):
    """Every frame holds one face: box (x0, y0, x1, y1) in source pixels."""
    x0, y0, x1, y1 = box
    face = {"x": x0 / src[0], "y": y0 / src[1], "w": (x1 - x0) / src[0], "h": (y1 - y0) / src[1],
            "confidence": 0.9}
    return lambda paths: {p: [dict(face)] for p in paths}


def master_item(box, src=PYXIS, scaling=reframe.SCALE_TO_CROP, squeeze=None, stored=None,
                **kw):
    """One item planned for that master shape."""
    stored = stored or src
    return reframe.plan_items(
        [{"name": "c", "path": "/media/c.braw", "in": 0.0, "out": 8.0, "squeeze": squeeze}],
        aspect=reframe.MASTER, master_size=MASTER, scaling=scaling,
        reader_for=lambda it: Reader(stored), detect=detector(box, src), **kw)["items"][0]


def window_top(row_or_item, out, scale):
    """The source row at the top of the crop window."""
    return row_or_item["y"] - out[1] / (2.0 * scale)


class TestMasterShape(unittest.TestCase):
    # A standing performer on a festival stage: Vision's box from row 725 to 1083 (0.18 to 0.27 of
    # the height). Centre fill at scaleToCrop shows rows 672 to 3360, so the box is in but
    # the head above the brows is not.
    STANDING = (2900.0, 725.0, 3140.0, 1083.0)

    def test_centre_fill_cuts_the_head_and_the_plan_tilts(self):
        it = master_item(self.STANDING)
        e, row = it["entry"], it["row"]
        self.assertEqual((it["status"], it["move"]), (reframe.PASS, "tilt"), it["reason"])
        scale = e["scale"]
        self.assertAlmostEqual(scale, 0.9842, places=4)            # the fill scale: zoom 1
        self.assertAlmostEqual(row["zoom"], 1.0, places=3)
        self.assertEqual(row["pan"], 0.0)                         # the width is already full
        self.assertLess(row["tilt"], 0)                           # negative Tilt shows the top
        # The head (0.6 face heights above the box) and 0.65 face heights of room fit.
        h = self.STANDING[3] - self.STANDING[1]
        head = self.STANDING[1] - reframe.HEAD_ABOVE * h
        top = window_top(e, MASTER, scale)
        self.assertAlmostEqual(head - top, reframe.HEADROOM * h, delta=0.2)
        self.assertGreaterEqual(e["headroom_px"], row["headroom_px"])
        # In Resolve's units: the picture moves down by (centre row - middle) * scale output
        # pixels, and a Tilt of T moves it by T * (4032 * 0.98413) / 2646, so T is that
        # move times 2646 / 3968.
        move_px = (PYXIS[1] / 2.0 - e["y"]) * scale
        fit = MASTER[0] / PYXIS[0]
        self.assertAlmostEqual(row["tilt"], -move_px * MASTER[1] / (PYXIS[1] * fit), places=1)

    def test_a_seated_subject_needs_nothing(self):
        # The box from row 1700: head top at 1520, room wanted to 1325, below the window's
        # top at 672. Centre fill is the answer and the plan says so.
        it = master_item((2900.0, 1700.0, 3200.0, 2000.0))
        self.assertEqual((it["status"], it["move"]), (reframe.PASS, "none"), it["reason"])
        row = it["row"]
        self.assertEqual((row["pan"], row["tilt"]), (0.0, 0.0))
        self.assertAlmostEqual(row["zoom"], 1.0, places=3)

    def test_a_subject_at_the_top_of_the_sensor_is_a_fallback_with_its_reason(self):
        # The box starts 30 rows down: the head runs past the top of the sensor.
        it = master_item((2900.0, 30.0, 3200.0, 400.0))
        self.assertEqual(it["status"], reframe.FALLBACK)
        self.assertIn("the subject is at the top edge of the picture", it["reason"])
        e = it["entry"]
        self.assertAlmostEqual(window_top(e, MASTER, e["scale"]), 0.0, delta=0.6)  # as high as it goes

    def test_short_room_says_why_without_cutting(self):
        # The head is in the picture but has less room above it than wanted (the box starts
        # at row 300, head top at 78): the crop rises to the top and passes, kept as shot.
        it = master_item((2900.0, 300.0, 3270.0, 670.0))
        self.assertEqual(it["status"], reframe.PASS, it["reason"])
        self.assertIn("kept as shot", it["reason"])

    def test_scale_to_fit_zooms_to_fill_and_counts_pan_and_tilt_against_the_fitted_picture(self):
        # At scaleToFit the 3:2 picture is fit by height (0.65625) and pillarboxed; the plan
        # zooms to fill the width (0.9842 / 0.65625 = 1.4997), and the fitted picture is
        # 3969 px wide on a 5952 timeline, so Pan and Tilt are not output pixels.
        it = master_item(self.STANDING, scaling=reframe.SCALE_TO_FIT)
        row, e = it["row"], it["entry"]
        self.assertEqual(it["move"], "zoom+tilt")
        self.assertAlmostEqual(row["zoom"], 1.4997, places=3)
        fit = MASTER[1] / PYXIS[1]
        move_px = (PYXIS[1] / 2.0 - e["y"]) * e["scale"]
        self.assertAlmostEqual(row["tilt"], -move_px * MASTER[1] / (PYXIS[1] * fit), places=1)


class TestPlanItems(unittest.TestCase):
    def test_a_file_needs_its_range(self):
        with self.assertRaisesRegex(reframe.ReframeError, "give in and out"):
            reframe.plan_items([{"name": "c", "path": "/media/c.mov"}], aspect="9x16",
                               reader_for=lambda it: Reader(PYXIS),
                               detect=detector((0, 0, 1, 1), PYXIS))
        with self.assertRaisesRegex(reframe.ReframeError, "must be after in"):
            reframe.plan_items([{"name": "c", "path": "/media/c.mov", "in": 5, "out": 5}],
                               aspect="9x16", reader_for=lambda it: Reader(PYXIS),
                               detect=detector((0, 0, 1, 1), PYXIS))

    def test_a_squeezed_manifest_entry_carries_the_file_size_for_cut(self):
        m = {"source_path": "/m.mov", "squeeze": 1.5,
             "clips": [{"name": "c", "spans": [{"in": 0, "out": 4}]}]}
        rep = reframe.plan(m, aspects=["9x16"], reader=Reader(S5II),
                           detect=detector((4000.0, 1500.0, 4400.0, 1900.0), (8928, 3968)))
        e = reframe.apply(m, rep)["clips"][0]["spans"][0]["reframe"]["9x16"]
        self.assertEqual((e["src"], e["stored"], e["squeeze"]), ([8928, 3968], list(S5II), 1.5))
        # cut checks the clip against the stored size and places the crop in the picture
        # it was planned on.
        from rpresolve import cut
        clip = reframe.apply(m, rep)["clips"][0]
        plans = cut.item_plans(clip, "9x16", S5II)
        want = reframe.resolve_props((8928, 3968), (1080, 1920), e["scale"], (e["x"], e["y"]))
        self.assertEqual(plans[0]["props"], want)


class TestShapes(unittest.TestCase):
    def test_the_table_and_the_master(self):
        self.assertEqual(reframe.size_of("4x5"), (1080, 1350))
        self.assertEqual(reframe.size_of("16x9"), (1920, 1080))
        self.assertEqual(reframe.size_of(reframe.MASTER, [5952, 2646]), MASTER)
        with self.assertRaisesRegex(reframe.ReframeError, "master_size"):
            reframe.size_of(reframe.MASTER)
        with self.assertRaisesRegex(reframe.ReframeError, "shape '3x2'"):
            reframe.size_of("3x2")
        self.assertEqual(reframe.anchor_of(reframe.MASTER), reframe.ANCHOR_MIDDLE)
        self.assertEqual(reframe.anchor_of("9x16"), reframe.ANCHOR_FACE)

    def test_9x16_from_a_tight_crop_tilts_for_the_head(self):
        # 1280x720 at 2.25 into 1:1: a 480-row window. Centred on a 160-row face whose box
        # starts at row 250, the window's top is row 90 and the head (row 154) has 64 rows
        # of room where 104 are wanted, so the crop rises 40 rows; the Tilt is in Resolve's
        # units, with no unconfirmed note.
        src = (1280, 720)
        boxes = [(t, (580.0, 250.0, 740.0, 410.0)) for t in range(8)]
        p = reframe.plan_aspect(boxes, (660.0, 330.0), src, (0, 0) + src, "1x1", scale=2.25)
        self.assertEqual(p["y"], 290.0)
        self.assertEqual(p["status"], reframe.PASS, p["reason"])
        self.assertIn("tilt", p["move"])
        fit = min(1080 / 1280.0, 1080 / 720.0)
        move_px = (src[1] / 2.0 - p["y"]) * 2.25
        self.assertAlmostEqual(p["props"]["Tilt"], round(-move_px * 1080 / (720 * fit), 4))
        head = 250 - reframe.HEAD_ABOVE * 160
        self.assertAlmostEqual(head - window_top(p, (1080, 1080), 2.25),
                               reframe.HEADROOM * 160, delta=0.2)
        self.assertTrue(reframe.TILT_CONFIRMED)
        rows = reframe.rows({"spans": [{"clip": "c", "span": 1, "in": 0, "out": 1, "face": None,
                                        "review": [], "samples": 8, "face_samples": 8,
                                        "hand_face_x": None, "aspects": {"1x1": p}}]})
        self.assertNotIn("no render has confirmed", rows[0]["reason"])


class TestSqueeze(unittest.TestCase):
    def test_an_anamorphic_s5ii_clip_is_planned_desqueezed(self):
        # 5952x3968 recorded, 1.5x: 8928x3968 desqueezed, 2.25:1, the master's own shape.
        # A face at a quarter of the width sits at x 2232 of the desqueezed picture.
        box = (2100.0, 1700.0, 2364.0, 2000.0)      # desqueezed pixels
        it = master_item(box, src=(8928, 3968), stored=S5II, squeeze=1.5)
        self.assertEqual((it["size"], it["stored_size"]), ([8928, 3968], list(S5II)))
        self.assertEqual((it["squeeze"], it["squeeze_from"]), (1.5, "manifest"))
        self.assertEqual(it["move"], "none")        # same shape, seated: centre fill holds
        self.assertAlmostEqual(it["row"]["zoom"], 1.0, places=3)
        # Without the squeeze the same file is planned as a 3:2 picture, cropped to 2646 of
        # its 3968 rows at scale 1, not shown whole at 0.6669.
        flat = master_item(box, src=(8928, 3968), stored=S5II, squeeze=1.0)
        self.assertEqual(flat["size"], list(S5II))
        self.assertAlmostEqual(it["entry"]["scale"], 0.6669, places=4)  # 2646 / 3968, rounded up
        self.assertAlmostEqual(flat["entry"]["scale"], 1.0, places=4)

    def test_the_squeeze_comes_from_the_file_tag_or_is_named_as_assumed(self):
        self.assertEqual(reframe.squeeze_tag({"streams": [{"tags": {"anamorphic": "1.5x"}}]}), 1.5)
        self.assertEqual(reframe.squeeze_tag({"format": {"tags": {"com.blackmagic.anamorphic":
                                                                   "1.33"}}}), 1.33)
        self.assertIsNone(reframe.squeeze_tag({"streams": [{"tags": {"lens_type": "Sigma"}}]}))
        tagged = master_item((2900.0, 1700.0, 3200.0, 2000.0))
        self.assertEqual(tagged["squeeze_from"], "assumed")
        m = {"source_path": "/m.mov", "clips": [{"name": "c", "spans": [{"in": 0, "out": 4}]}]}
        rep = reframe.plan(m, aspects=["9x16"], reader=Reader(PYXIS, squeeze=1.5),
                           detect=detector((2900.0, 1700.0, 3200.0, 2000.0), (9072, 4032)))
        self.assertEqual((rep["squeeze"], rep["squeeze_from"], rep["size"]),
                         (1.5, "file tag", [9072, 4032]))
        self.assertIn("squeeze 1.5 (file tag)", reframe.summary(rep))
        with self.assertRaisesRegex(reframe.ReframeError, "squeeze"):
            reframe.plan(dict(m, squeeze=9), aspects=["9x16"], reader=Reader(PYXIS),
                         detect=detector((0, 0, 1, 1), PYXIS))


FFMPEG = reframe.FFMPEG


@unittest.skipUnless(os.path.exists(FFMPEG), "needs ffmpeg")
class TestStills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def still(self, name, size):
        path = os.path.join(self.dir, name)
        w, h = size
        with open(path, "wb") as f:
            f.write(b"P6 %d %d 255\n" % (w, h) + bytes([128]) * (w * h * 3))
        return path

    def test_stills_are_read_at_their_size_judged_at_the_source_s_and_never_removed(self):
        paths = [self.still(f"STAND_f{i}.ppm", (90, 60)) for i in range(3)]
        p = reframe.plan_items([{"name": "STAND", "path": "/media/STAND.braw", "stills": paths,
                                 "src": list(PYXIS)}],
                               aspect=reframe.MASTER, master_size=MASTER,
                               scaling=reframe.SCALE_TO_CROP,
                               detect=detector(TestMasterShape.STANDING, PYXIS))
        it = p["items"][0]
        self.assertEqual((it["status"], it["move"], it["size"]), (reframe.PASS, "tilt", list(PYXIS)))
        self.assertEqual(it["row"]["samples"], 3)
        self.assertEqual(it["entry"]["area"], [0.0, 0.0, 6048.0, 4032.0])
        for path in paths:
            self.assertTrue(os.path.exists(path), path)

    def test_a_still_of_another_shape_is_refused(self):
        with self.assertRaisesRegex(reframe.ReframeError, "not the same shape"):
            reframe.StillsReader([self.still("a.ppm", (160, 90))], PYXIS)
        with self.assertRaisesRegex(reframe.ReframeError, "not all one size"):
            reframe.StillsReader([self.still("a.ppm", (90, 60)), self.still("b.ppm", (60, 40))])


if __name__ == "__main__":
    unittest.main()
