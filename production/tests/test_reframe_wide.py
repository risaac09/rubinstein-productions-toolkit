"""
Offline tests for the reframe changes that came out of wide, dark footage with
an audience and no speech: the scale derived from the source, a picture area
that tells black bars from a dark scene, a margin that stops at the edge of
the frame, a minimum of checked samples before a crop can pass, steering the
subject, the no-speech manifest and the Tilt fixture. Synthetic boxes and
generated frames only: no real media, no Resolve.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import cutlist, reframe  # noqa: E402
from test_reframe import FakeReader, face, fake_detect, full, TALL  # noqa: E402

WIDE = (5952, 2646)   # a wide anamorphic source
UHD = (3840, 2160)


def samples(src, per_sample):
    """[(t, [Vision box])] with per_sample[i] a list of (cx, cy, w, h) for sample i."""
    return [(float(t), [face(*f, src) for f in fs]) for t, fs in enumerate(per_sample)]


class TestAutoScale(unittest.TestCase):
    def test_the_default_is_the_widest_scale_that_fills_the_frame(self):
        # 5952x2646 into 9:16: the window is the full 2646 rows, 1.9 / 1.4 of them wide enough
        # for 1080 / 0.7257 = 1488 px. A zoom of about 4, not the 12.4 that 2.25 gave.
        sp = reframe.plan_span(samples(WIDE, [[(3000, 900, 220, 220)]] * 8), WIDE, full(WIDE),
                               ["9x16"], scale=reframe.AUTO)
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["scale"]), (reframe.PASS, 0.7257))
        self.assertEqual(p["props"]["ZoomX"], 3.9994)
        self.assertTrue(p["fills"])
        self.assertEqual([t["label"] for t in p["tried"]], ["default"])

    def test_the_old_default_is_still_there_and_still_absurd_on_a_wide_source(self):
        sp = reframe.plan_span(samples(WIDE, [[(3000, 900, 220, 220)]] * 8), WIDE, full(WIDE),
                               ["9x16"], scale=reframe.DEFAULT_SCALE)
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["scale"]), (reframe.PASS, 2.25))
        self.assertEqual(p["props"]["ZoomX"], 12.4)

    def test_square_and_720p_derive_their_own_scale(self):
        for src, aspect, want in (((1280, 720), "9x16", 2.6667), (UHD, "9x16", 0.8889),
                                  (WIDE, "1x1", 0.4082), (UHD, "1x1", 0.5)):
            sp = reframe.plan_span(samples(src, [[(src[0] / 2, src[1] / 2, 160, 160)]] * 8), src,
                                   full(src), [aspect], scale=reframe.AUTO)
            self.assertEqual(sp["aspects"][aspect]["scale"], want, (src, aspect))

    def test_auto_follows_the_picture_area_not_the_frame(self):
        area = (0, 180, 1280, 540)   # a letterboxed call recording
        sp = reframe.plan_span(samples((1280, 720), [[(400, 360, 160, 160)]] * 6), (1280, 720), area,
                               ["9x16"], scale=reframe.AUTO)
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["scale"], p["fills"]), (reframe.PASS, 5.3334, True))

    def test_auto_names_itself_in_a_failure(self):
        # One 600 px face walking 440 px a sample (it stays on one track): 3080 px of travel,
        # more than the 1488 px a 9:16 window holds at the fill scale.
        xs = [1000 + 440 * i for i in range(8)]
        sp = reframe.plan_span(samples(WIDE, [[(x, 900, 600, 600)] for x in xs]), WIDE, full(WIDE),
                               ["9x16"], scale=reframe.AUTO)
        self.assertEqual(sp["tracks"], 1)
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        self.assertIn("at the widest filled scale 0.7257 the face leaves the crop", p["reason"])
        self.assertNotIn("default scale", p["reason"])

    def test_clip_scale(self):
        self.assertEqual(reframe.clip_scale({"name": "a"}), reframe.AUTO)
        self.assertEqual(reframe.clip_scale({"reframe": {"face_x": 270}}), reframe.AUTO)
        self.assertEqual(reframe.clip_scale({"reframe": {"scale": 3}}), 3.0)
        self.assertEqual(reframe.clip_scale({"reframe": {"scale": 3}}, allow_bars=True), 3.0)
        # allow_bars with no scale is the Ep 002 look; "auto" asks for the derived one regardless.
        self.assertEqual(reframe.clip_scale({"name": "a"}, allow_bars=True), reframe.DEFAULT_SCALE)
        self.assertEqual(reframe.clip_scale({"reframe": {"scale": "auto"}}, allow_bars=True), reframe.AUTO)

    def test_a_clip_that_names_a_scale_keeps_it_through_plan(self):
        with tempfile.TemporaryDirectory() as d:
            m = {"source_path": os.path.join(d, "s.mp4"),
                 "clips": [{"name": "c", "reframe": {"scale": 3.0},
                            "spans": [{"in": 10.0, "out": 14.0}]}]}
            report = reframe.plan(m, aspects=["9x16"], reader=FakeReader(), detect=fake_detect)
        # A 3.0 punch-in on the letterboxed fake source leaves bars, so it falls to the fill scale.
        self.assertEqual(report["rows"][0]["status"], "fallback")
        self.assertEqual(report["rows"][0]["scale"], 5.3334)


class TestPictureArea(unittest.TestCase):
    W, H = 64, 36

    def frame(self, f):
        return bytes(f(r, c) for r in range(self.H) for c in range(self.W))

    def test_a_dark_scene_keeps_its_whole_frame(self):
        # Mean luma 18.75, below the 24 an earlier version treated as picture, but every row and
        # column holds pixels well above a bar's level: lights and sensor speckle.
        dark = self.frame(lambda r, c: 80 if (r + c) % 8 == 0 else 10)
        self.assertLess(sum(dark) / len(dark), 24)
        self.assertEqual(reframe.picture_area([dark], (self.W, self.H)), (0, 0, self.W, self.H))

    def test_a_lit_band_in_a_dark_frame_is_not_the_picture(self):
        # The night-stage case: the top rows are lit, the rest is dark but not black.
        dark = self.frame(lambda r, c: 100 if r < 9 else (70 if (r * 3 + c) % 5 == 0 else 12))
        self.assertEqual(reframe.picture_area([dark], (self.W, self.H)), (0, 0, self.W, self.H))

    def test_noisy_bars_are_still_bars(self):
        # Encoder noise up to 30 in the bars; the picture is mid grey.
        f = self.frame(lambda r, c: 120 if 9 <= r < 27 else (r * 31 + c * 17) % 31)
        self.assertEqual(reframe.picture_area([f], (self.W, self.H)), (0, 9, self.W, 27))

    def test_pillar_bars_and_both_at_once(self):
        f = self.frame(lambda r, c: 120 if 8 <= c < 56 and 4 <= r < 30 else 16)
        self.assertEqual(reframe.picture_area([f], (self.W, self.H)), (8, 4, 56, 30))

    def test_a_black_stripe_inside_the_picture_stays_inside(self):
        f = self.frame(lambda r, c: 16 if (r < 6 or r >= 30 or 15 <= r < 18) else 120)
        self.assertEqual(reframe.picture_area([f], (self.W, self.H)), (0, 6, self.W, 30))

    def test_a_row_is_a_bar_only_if_it_is_dark_in_every_sample(self):
        bar = self.frame(lambda r, c: 16)
        lit_top = self.frame(lambda r, c: 120 if r < 9 else 16)
        self.assertEqual(reframe.picture_area([bar, lit_top], (self.W, self.H)), (0, 0, self.W, 9))

    def test_all_black_has_no_picture(self):
        self.assertIsNone(reframe.picture_area([self.frame(lambda r, c: 0),
                                                self.frame(lambda r, c: 38)], (self.W, self.H)))

    def test_a_bright_edge_row_is_picture(self):
        f = self.frame(lambda r, c: 255 if r == 0 else 16)
        self.assertEqual(reframe.picture_area([f], (self.W, self.H)), (0, 0, self.W, 1))


class TestEdgeMargin(unittest.TestCase):
    HD = (1920, 1080)

    def test_a_margin_the_frame_does_not_hold_cannot_be_required(self):
        # A 200 px face whose box starts 60 px above the top of the picture. Grown by 10% it
        # reaches 80 px above; at the fill scale the window is the full frame height.
        b = (860.0, -60.0, 1060.0, 140.0)
        centre = (960.0, 540.0)
        scale = 1.7778
        self.assertLess(reframe.slack(b, TALL, scale, centre), 0)
        self.assertGreaterEqual(reframe.slack(b, TALL, scale, centre, area=full(self.HD)), -1e-6)

    def test_a_face_at_the_top_edge_is_a_fallback_and_says_so(self):
        # The box top is 40 px down, so the head (0.6 face heights above it) runs past the
        # top of the picture: no crop keeps the whole head.
        sp = reframe.plan_span(samples(self.HD, [[(960, 40, 200, 200)]] * 8), self.HD, full(self.HD),
                               ["9x16"], scale=reframe.AUTO)
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.FALLBACK)
        self.assertIn("the subject is at the top edge of the picture", p["reason"])
        self.assertEqual(p["check"]["worst_px"], 0.0)
        self.assertTrue(any("reaches past the edge of the picture" in r for r in sp["review"]), sp["review"])

    def test_a_face_outside_the_picture_still_fails(self):
        area = (0, 0, 1000, 1080)
        sp = reframe.plan_span(samples(self.HD, [[(1500, 540, 200, 200)]] * 8), self.HD, area,
                               ["9x16"], scale=reframe.AUTO)
        self.assertEqual(sp["aspects"]["9x16"]["status"], reframe.MANUAL)

    def test_a_pixel_of_overshoot_is_not_an_edge(self):
        # Vision's box may overshoot the frame by float noise: 0.0005 of 1080 rows is half a pixel.
        sp = reframe.plan_span(samples(self.HD, [[(960, 100 - 0.5 + 0.0, 200, 200)]] * 8), self.HD,
                               full(self.HD), ["9x16"], scale=reframe.AUTO)
        self.assertEqual(sp["review"], [])

    def test_a_face_that_fits_inside_the_picture_notes_nothing(self):
        sp = reframe.plan_span(samples(self.HD, [[(960, 540, 200, 200)]] * 8), self.HD, full(self.HD),
                               ["9x16"], scale=reframe.AUTO)
        self.assertEqual(sp["review"], [])


class TestEvidenceMinimum(unittest.TestCase):
    HD = (1920, 1080)

    def spec(self, seen, total):
        """The same steady face in `seen` of `total` samples."""
        return samples(self.HD, [[(960, 540, 200, 200)] if i < seen else [] for i in range(total)])

    def test_needed(self):
        self.assertEqual([reframe.needed(n) for n in (0, 1, 2, 3, 4, 8, 15, 30)],
                         [0, 1, 2, 3, 3, 4, 8, 15])
        self.assertEqual(reframe.needed(15, 1.0), 15)
        self.assertEqual(reframe.needed(15, 0.0), 3)

    def test_one_face_in_fifteen_samples_is_not_a_pass(self):
        sp = reframe.plan_span(self.spec(1, 15), self.HD, full(self.HD), ["9x16"], scale=reframe.AUTO)
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        self.assertIn("checked on only 1 of 15 samples (at least 8 needed)", p["reason"])
        self.assertEqual(p["check"]["unchecked"], 14)
        # It is also flagged, as before.
        self.assertTrue(any("no face is checked at" in r for r in sp["review"]))

    def test_exactly_enough_passes(self):
        p = reframe.plan_span(self.spec(8, 15), self.HD, full(self.HD), ["9x16"],
                              scale=reframe.AUTO)["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.PASS)
        p = reframe.plan_span(self.spec(7, 15), self.HD, full(self.HD), ["9x16"],
                              scale=reframe.AUTO)["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)

    def test_the_share_is_a_parameter(self):
        spec = self.spec(7, 15)
        low = reframe.plan_span(spec, self.HD, full(self.HD), ["9x16"], scale=reframe.AUTO,
                                min_checked=0.3)["aspects"]["9x16"]
        self.assertEqual(low["status"], reframe.PASS)
        high = reframe.plan_span(self.spec(14, 15), self.HD, full(self.HD), ["9x16"],
                                 scale=reframe.AUTO, min_checked=1.0)["aspects"]["9x16"]
        self.assertEqual(high["status"], reframe.MANUAL)
        self.assertIn("at least 15 needed", high["reason"])

    def test_never_fewer_than_three(self):
        p = reframe.plan_span(self.spec(2, 15), self.HD, full(self.HD), ["9x16"], scale=reframe.AUTO,
                              min_checked=0.0)["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        p = reframe.plan_span(self.spec(3, 15), self.HD, full(self.HD), ["9x16"], scale=reframe.AUTO,
                              min_checked=0.0)["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.PASS)

    def test_a_span_with_few_samples_needs_all_of_them(self):
        p = reframe.plan_span(self.spec(2, 2), self.HD, full(self.HD), ["9x16"],
                              scale=reframe.AUTO)["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.PASS)

    def test_the_fallback_respects_it_too(self):
        # A face that leaves the default crop but not the fill scale, seen in 2 of 8 samples.
        spec = samples(self.HD, [[(690 + 80 * i, 400, 150, 180)] if i < 2 else [] for i in range(8)])
        p = reframe.plan_span(spec, self.HD, full(self.HD), ["9x16"])["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        self.assertIn("checked on only 2 of 8 samples", p["reason"])

    def test_held_without_a_need_keeps_the_old_meaning(self):
        self.assertTrue(reframe.held({"inside": 1, "checked": 1}))
        self.assertFalse(reframe.held({"inside": 0, "checked": 1}))
        self.assertFalse(reframe.held({"inside": 0, "checked": 0}))

    def test_through_the_planner_the_report_says_why(self):
        with tempfile.TemporaryDirectory() as d:
            m = {"source_path": os.path.join(d, "s.mp4"),
                 "clips": [{"name": "c", "spans": [{"in": 10.0, "out": 20.0}]}]}
            once = lambda paths: {p: ([face(400, 360, 160, 160, (1280, 720))] if i == 0 else [])
                                  for i, p in enumerate(sorted(paths))}
            report = reframe.plan(m, aspects=["9x16"], reader=FakeReader(), detect=once)
        r = report["rows"][0]
        self.assertEqual(r["status"], "manual")
        self.assertIn("checked on only 1 of 8", r["reason"])
        self.assertEqual(report["min_checked"], 0.5)
        self.assertEqual(reframe.summary(report).count("1 manual"), 1)


class TestSubject(unittest.TestCase):
    """A stage: a performer in 6 of 8 samples, a dozen audience faces in all 8."""

    def stage(self):
        crowd = [(500 + 450 * i, 2000, 70, 70) for i in range(12)]
        per_sample = []
        for t in range(8):
            fs = list(crowd)
            if t < 6:
                fs.append((3000, 900, 220, 220))
            per_sample.append(fs)
        return samples(WIDE, per_sample)

    def x_of(self, sp):
        return sp["face"]["x"]

    def test_without_help_the_audience_wins_on_presence(self):
        sp = reframe.plan_span(self.stage(), WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO)
        self.assertLess(sp["face"]["w"], 100)   # an audience member, the failure the options fix
        self.assertEqual(sp["ignored"], 0)

    def test_min_face_height_leaves_out_the_audience(self):
        sp = reframe.plan_span(self.stage(), WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               min_face_height=0.05)   # 5% of 2646 = 132 px; the crowd is 70
        self.assertEqual(self.x_of(sp), 3000.0)
        self.assertEqual(sp["ignored"], 96)
        self.assertEqual(sp["tracks"], 1)
        self.assertEqual(sp["aspects"]["9x16"]["status"], reframe.PASS)

    def test_subject_region_names_where_the_subject_is(self):
        sp = reframe.plan_span(self.stage(), WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               subject_region=(2500, 500, 3500, 1300))
        self.assertEqual(self.x_of(sp), 3000.0)
        self.assertEqual(sp["ignored"], 96)

    def test_prefer_largest_picks_the_performer(self):
        sp = reframe.plan_span(self.stage(), WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               prefer=reframe.PREFER_LARGEST)
        self.assertEqual(self.x_of(sp), 3000.0)
        self.assertEqual(sp["rule"], reframe.RULE_LARGEST)

    def test_prefer_largest_ignores_a_giant_that_is_in_one_frame(self):
        per_sample = [[(500, 900, 180, 180)] for _ in range(8)]
        per_sample[3].append((3000, 900, 900, 900))   # one false detection, huge
        sp = reframe.plan_span(samples(WIDE, per_sample), WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               prefer=reframe.PREFER_LARGEST)
        self.assertEqual(self.x_of(sp), 500.0)

    def test_a_point_names_the_face_in_both_axes(self):
        # Two faces at the same x, one high and one low.
        per_sample = [[(1000, 400, 200, 200), (1000, 1900, 200, 200)] for _ in range(8)]
        spec = samples(WIDE, per_sample)
        sp = reframe.plan_span(spec, WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               speaker_x=1000, speaker_y=1900)
        self.assertEqual(sp["face"]["y"], 1900.0)
        self.assertIn("speaker_y 1900", sp["rule"])
        sp = reframe.plan_span(spec, WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               speaker_x=1000, speaker_y=400)
        self.assertEqual(sp["face"]["y"], 400.0)

    def test_speaker_y_without_speaker_x_is_refused(self):
        m = {"source_path": "/x/s.mp4", "clips": [{"name": "c", "spans": [{"in": 0, "out": 5}]}]}
        with self.assertRaisesRegex(reframe.ReframeError, "together with speaker_x"):
            reframe.plan(m, reader=FakeReader(), detect=fake_detect, speaker_y=900)

    def test_speaker_x_alone_is_unchanged(self):
        spec = samples(WIDE, [[(1000, 400, 200, 200), (3000, 400, 200, 200)] for _ in range(8)])
        sp = reframe.plan_span(spec, WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO, speaker_x=3000)
        self.assertEqual(sp["rule"], "the face nearest speaker_x 3000, among faces in at least "
                                     "half as many samples as the most present")

    def test_nothing_left_after_the_filters_says_so(self):
        sp = reframe.plan_span(self.stage(), WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO,
                               subject_region=(0, 0, 100, 100))
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.NO_FACE)
        self.assertIn("inside the subject region", p["reason"])
        self.assertIn("102 face boxes ignored", p["reason"])

    def test_the_ambiguity_note_offers_the_region(self):
        spec = samples(WIDE, [[(1000, 900, 200, 200), (3000, 900, 190, 190)] for _ in range(8)])
        sp = reframe.plan_span(spec, WIDE, full(WIDE), ["9x16"], scale=reframe.AUTO)
        self.assertTrue(any("subject_region" in r for r in sp["review"]), sp["review"])

    def test_admit(self):
        b = (900.0, 800.0, 1100.0, 1000.0)   # 200 px tall, centre (1000, 900)
        self.assertTrue(reframe.admit(b, WIDE))
        self.assertTrue(reframe.admit(b, WIDE, (900, 800, 1100, 1000), 0.07))
        self.assertFalse(reframe.admit(b, WIDE, None, 0.08))        # 0.08 * 2646 = 212 px
        self.assertFalse(reframe.admit(b, WIDE, (0, 0, 999, 2646)))

    def test_bad_arguments_are_refused_before_any_work(self):
        m = {"source_path": "/x/s.mp4", "clips": [{"name": "c", "spans": [{"in": 0, "out": 5}]}]}
        for kw in ({"min_checked": 1.5}, {"min_checked": -0.1}, {"min_checked": True},
                   {"min_face_height": 0}, {"min_face_height": 1.5},
                   {"subject_region": [1, 2, 3]}, {"subject_region": [5, 5, 1, 9]},
                   {"subject_region": ["a", 1, 2, 3]}, {"prefer": "loudest"}):
            with self.subTest(kw=kw):
                with self.assertRaises(reframe.ReframeError):
                    reframe.plan(m, reader=FakeReader(), detect=fake_detect, **kw)

    def test_the_report_and_the_manifest_copy_record_the_steering(self):
        with tempfile.TemporaryDirectory() as d:
            m = {"source_path": os.path.join(d, "s.mp4"),
                 "clips": [{"name": "c", "spans": [{"in": 10.0, "out": 20.0}]}]}
            report = reframe.plan(m, aspects=["9x16"], reader=FakeReader(), detect=fake_detect,
                                  subject_region=[0, 0, 600, 720], min_face_height=0.05,
                                  prefer="largest")
            copy = reframe.apply(m, report)
        self.assertEqual(copy["reframe_plan"]["subject_region"], [0.0, 0.0, 600.0, 720.0])
        self.assertEqual((copy["reframe_plan"]["min_face_height"], copy["reframe_plan"]["prefer"]),
                         (0.05, "largest"))
        self.assertGreater(report["spans"][0]["ignored"], 0)


class TestNoSpeechManifest(unittest.TestCase):
    def manifest(self, d, **extra):
        m = {"speech": False, "source_path": os.path.join(d, "s.mp4"),
             "clips": [{"name": "c", "spans": [{"in": 10.0, "out": 20.0}]}]}
        m.update(extra)
        return m

    def test_only_the_source_and_clips_are_needed(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.manifest(d)
            cutlist.validate_manifest(m, allow_no_speech=True)
            for key in ("source_sha256", "fps", "words", "approved_text"):
                self.assertNotIn(key, m)

    def test_other_tools_refuse_it_and_say_why(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(cutlist.CutlistError, "only reframe_plan reads"):
                cutlist.validate_manifest(self.manifest(d))
            path = os.path.join(d, "m.json")
            with open(path, "w") as f:
                json.dump(self.manifest(d), f)
            with self.assertRaises(cutlist.CutlistError):
                cutlist.load_manifest(path)
            self.assertEqual(cutlist.load_manifest(path, allow_no_speech=True)["speech"], False)

    def test_a_no_speech_manifest_still_needs_a_source_and_clips(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.manifest(d)
            del m["clips"]
            with self.assertRaisesRegex(cutlist.CutlistError, "no 'clips'"):
                cutlist.validate_manifest(m, allow_no_speech=True)
            m = self.manifest(d)
            m["clips"][0]["spans"][0]["out"] = 5.0
            with self.assertRaisesRegex(cutlist.CutlistError, "ends before it starts"):
                cutlist.validate_manifest(m, allow_no_speech=True)

    def test_a_speech_manifest_is_as_strict_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.manifest(d)
            m["speech"] = True
            with self.assertRaisesRegex(cutlist.CutlistError, "no 'source_sha256'"):
                cutlist.validate_manifest(m, allow_no_speech=True)
            del m["speech"]
            with self.assertRaisesRegex(cutlist.CutlistError, "no 'source_sha256'"):
                cutlist.validate_manifest(m, allow_no_speech=True)

    def test_the_planner_runs_on_it_and_the_copy_stays_a_no_speech_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.manifest(d)
            report = reframe.plan(m, aspects=["9x16"], reader=FakeReader(), detect=fake_detect)
            copy = reframe.apply(m, report)
        self.assertEqual(copy["speech"], False)
        self.assertIn("reframe", copy["clips"][0]["spans"][0])
        cutlist.validate_manifest(copy, allow_no_speech=True)

    def test_the_cli_takes_it(self):
        import resolve_workflow as rw
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            path = os.path.join(d, "m.json")
            with open(path, "w") as f:
                json.dump(self.manifest(d), f)
            out = os.path.join(d, "out", "m.reframed.json")
            os.makedirs(os.path.dirname(out))
            args = argparse.Namespace(manifest=path, out=out, aspects=["9x16"], samples=8,
                                      per_second=0.5, speaker_x=None, only=None, allow_bars=False,
                                      speaker_y=None, subject_region=None, min_face_height=None,
                                      min_checked=0.5, prefer="present")
            with mock.patch.object(reframe, "SourceReader", lambda p: FakeReader()), \
                    mock.patch.object(reframe.vision, "face_boxes", fake_detect):
                with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                    rc = rw.cmd_reframe_plan(args)
            self.assertIn(rc, (0, 2))
            self.assertTrue(os.path.exists(out))

    def test_the_mcp_tool_takes_it(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("reframe_plan")
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            path = os.path.join(d, "m.json")
            with open(path, "w") as f:
                json.dump(self.manifest(d), f)
            args = {"manifest": path, "out": os.path.join(d, "copy.json"), "aspects": ["9x16"],
                    "subject_region": [0, 0, 600, 720], "min_face_height": 0.05, "prefer": "largest"}
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            with mock.patch.object(reframe, "SourceReader", lambda p: FakeReader()), \
                    mock.patch.object(reframe.vision, "face_boxes", fake_detect):
                r = tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())
            self.assertTrue(os.path.exists(r["manifest"]))

    def test_the_mcp_schema_bounds_the_new_arguments(self):
        from rpresolve.mcp import schema, server
        tool = server.build_registry().get("reframe_plan")
        base = {"manifest": "/x/m.json"}
        for bad in ({"min_checked": 1.5}, {"subject_region": [1, 2, 3]}, {"min_face_height": 0},
                    {"prefer": "loudest"}, {"speaker_y": -1}):
            with self.subTest(bad=bad):
                self.assertTrue(schema.validate(tool.input_schema, dict(base, **bad)))
        props = tool.input_schema["properties"]
        self.assertEqual(props["min_checked"]["default"], reframe.MIN_CHECKED_SHARE)
        self.assertEqual(props["prefer"]["default"], reframe.PREFER_PRESENT)

    def test_the_cli_default_matches_the_module(self):
        # The parser cannot import reframe at build time, so it carries the number as a literal;
        # the help text states it, and this keeps the two from drifting.
        text = subprocess.run([sys.executable, str(HERE.parent / "resolve_workflow.py"),
                               "reframe-plan", "--help"], capture_output=True, text=True).stdout
        self.assertIn(f"default {reframe.MIN_CHECKED_SHARE:g}", text)
        src = (HERE.parent / "resolve_workflow.py").read_text()
        self.assertIn(f'"--min-checked", type=float, default={reframe.MIN_CHECKED_SHARE:g}', src)


class TestTiltFixture(unittest.TestCase):
    def test_expected_numbers(self):
        e = reframe.tilt_expect()
        self.assertEqual(e["scale"], 0.8889)
        self.assertEqual(e["centre"], [1766.4, 648.0])
        self.assertEqual(e["props"], {"ZoomX": 3.1605, "ZoomY": 3.1605, "Pan": 136.535, "Tilt": -1213.6448})
        # The marker at the crop centre lands in the middle of the output; the source's middle
        # lands where Pan and Tilt put it.
        self.assertEqual(e["expect"]["a"], [540.0, 960.0])
        self.assertEqual(e["expect"]["b"], [676.54, 1344.0])
        # Under a flipped Tilt the same markers sit far apart on the vertical axis, so a render
        # tells the two readings apart by hundreds of pixels, not a few.
        self.assertEqual(e["if_tilt_flipped"]["a"], [540.0, 191.99])
        self.assertGreater(abs(e["if_tilt_flipped"]["a"][1] - e["expect"]["a"][1]), 500)
        self.assertEqual(e["if_pan_flipped"]["a"][1], e["expect"]["a"][1])
        self.assertGreater(abs(e["if_pan_flipped"]["a"][0] - e["expect"]["a"][0]), 200)
        # A fixture that set no Tilt could not tell the readings apart.
        self.assertGreater(abs(e["props"]["Tilt"]), 100)
        self.assertGreater(abs(e["props"]["Pan"]), 100)

    def test_the_six_fixtures_resolve_confirmed(self):
        # The Zoom, Pan and Tilt typed into Resolve 21.1.1.10 on 2026-10-15; every marker
        # landed within a pixel of tilt_expect on the exported stills.
        confirmed = {
            "9x16": {"ZoomX": 3.1605, "ZoomY": 3.1605, "Pan": 136.535, "Tilt": -1213.6448},
            "master": {"ZoomX": 1.0001, "ZoomY": 1.0001, "Pan": 238.0977, "Tilt": -264.6196},
            "1x1": {"ZoomX": 1.7778, "ZoomY": 1.7778, "Pan": 76.8, "Tilt": -288.0},
            "9x16-6k": {"ZoomX": 2.6667, "ZoomY": 2.6667, "Pan": 115.2023, "Tilt": -768.0154},
            "master-fit": {"ZoomX": 1.4997, "ZoomY": 1.4997, "Pan": 357.0565, "Tilt": -396.8294},
            "16x9-crop": {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 76.8, "Tilt": -162.0},
        }
        for name, v in reframe.TILT_VARIANTS.items():
            e = reframe.tilt_expect(v["src"], centre_share=v["centre"], scaling=v["scaling"],
                                    out=v["out"])
            self.assertEqual(e["props"], confirmed[name], name)

    def test_every_marker_stays_on_the_frame_under_every_reading(self):
        e = reframe.tilt_expect()
        half = e["marker_side"] * e["scale"] / 2.0
        w, h = e["out"]
        for table in (e["expect"], e["if_tilt_flipped"], e["if_pan_flipped"]):
            for key in ("a", "b"):
                x, y = table[key]
                self.assertTrue(half <= x <= w - half and half <= y <= h - half, (key, x, y))

    def test_the_note_shows_only_while_the_constant_is_false(self):
        # Confirmed on six fixtures exported from Resolve 21.1.1.10 on 2026-10-15.
        self.assertIs(reframe.TILT_CONFIRMED, True)
        sp = reframe.plan_span(samples(UHD, [[(1500, 400, 200, 200)]] * 8), UHD, full(UHD), ["9x16"],
                               scale=2.25)
        p = sp["aspects"]["9x16"]
        rows = reframe.rows({"spans": [dict(sp, clip="c", span=1, hand_face_x=None, **{"in": 0, "out": 1})]})
        self.assertNotEqual(p["props"]["Tilt"], 0)
        self.assertNotIn("no render has confirmed", rows[0]["reason"])
        self.assertEqual(reframe.counts(rows)["tilt"], 0)
        with mock.patch.object(reframe, "TILT_CONFIRMED", False):
            rows = reframe.rows({"spans": [dict(sp, clip="c", span=1, hand_face_x=None, **{"in": 0, "out": 1})]})
            self.assertIn("no render has confirmed", rows[0]["reason"])
            self.assertEqual(reframe.counts(rows)["tilt"], 1)


def ppm(path, size, squares):
    """Write a P6 image: black with filled squares {(cx, cy): (r, g, b, side)}."""
    w, h = size
    rows = [bytearray(w * 3) for _ in range(h)]
    for (cx, cy), (r, g, b, side) in squares.items():
        for y in range(max(0, int(cy - side / 2)), min(h, int(cy + side / 2))):
            for x in range(max(0, int(cx - side / 2)), min(w, int(cx + side / 2))):
                rows[y][x * 3:x * 3 + 3] = bytes((r, g, b))
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h))
        for r in rows:
            f.write(bytes(r))


FFMPEG = reframe.FFMPEG


@unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")
class TestTiltCheck(unittest.TestCase):
    OUT = (270, 480)   # a quarter-size render: positions and markers scale with it

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.expect = reframe.tilt_expect()
        self.k = self.OUT[0] / float(self.expect["out"][0])

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, a, b, name="r.ppm"):
        side = self.expect["marker_side"] * self.expect["scale"] * self.k
        path = os.path.join(self.dir, name)
        ppm(path, self.OUT, {(a[0] * self.k, a[1] * self.k): (255, 255, 255, side),
                             (b[0] * self.k, b[1] * self.k): (255, 0, 0, side)})
        return reframe.read_rgb(path)

    def check(self, table, **kw):
        w, h, rgb = self.render(table["a"], table["b"])
        return reframe.tilt_check(rgb, (w, h), self.expect, **kw)

    def test_the_markers_where_the_convention_puts_them(self):
        r = self.check(self.expect["expect"])
        self.assertEqual((r["pan"], r["tilt"], r["ok"]), ("confirmed", "confirmed", True), r)
        self.assertEqual(r["size"], [270, 480])
        self.assertAlmostEqual(r["found"]["a"][0], 135.0, delta=1.5)
        self.assertAlmostEqual(r["found"]["a"][1], 240.0, delta=1.5)

    def test_a_flipped_tilt_is_named(self):
        r = self.check(self.expect["if_tilt_flipped"])
        self.assertEqual((r["pan"], r["tilt"], r["ok"]), ("confirmed", "inverted", False), r)

    def test_a_flipped_pan_is_named(self):
        r = self.check(self.expect["if_pan_flipped"])
        self.assertEqual((r["pan"], r["tilt"]), ("inverted", "confirmed"), r)

    def test_both_flipped_is_not_confirmed_either(self):
        both = {"a": [self.expect["if_pan_flipped"]["a"][0], self.expect["if_tilt_flipped"]["a"][1]],
                "b": [self.expect["if_pan_flipped"]["b"][0], self.expect["if_tilt_flipped"]["b"][1]]}
        r = self.check(both)
        self.assertEqual((r["pan"], r["tilt"], r["ok"]), ("inverted", "inverted", False), r)

    def test_somewhere_else_is_unclear_and_says_what_to_recheck(self):
        r = self.check({"a": [300.0, 700.0], "b": [800.0, 1500.0]})
        self.assertEqual((r["pan"], r["tilt"], r["ok"]), ("unclear", "unclear", False))
        self.assertTrue(any("scaleToFit" in n for n in r["notes"]), r["notes"])

    def test_a_missing_marker_is_unclear(self):
        w, h = self.OUT
        path = os.path.join(self.dir, "empty.ppm")
        ppm(path, self.OUT, {})
        w, h, rgb = reframe.read_rgb(path)
        r = reframe.tilt_check(rgb, (w, h), self.expect)
        self.assertEqual((r["pan"], r["tilt"], r["ok"]), ("unclear", "unclear", False))
        self.assertTrue(any("marker a (white) not found" in n for n in r["notes"]))

    def test_a_render_of_another_shape_is_refused(self):
        with self.assertRaisesRegex(reframe.ReframeError, "any size of that shape"):
            reframe.tilt_check(bytes(100 * 100 * 3), (100, 100), self.expect)

    def test_the_tolerance_is_a_parameter(self):
        # Both markers 40 px low at full size (10 px in this quarter-size render): outside the
        # default tolerance of 4 px, inside an explicit 14.
        low = {k: [v[0], v[1] + 40] for k, v in self.expect["expect"].items()}
        self.assertEqual(self.check(low)["tilt"], "unclear")
        self.assertEqual(self.check(low, tolerance=14)["tilt"], "confirmed")
        self.assertEqual(self.check(low)["tolerance"], 4.0)

    def test_an_end_to_end_render_emulated_with_ffmpeg(self):
        """The fixture clip, put through the transform this module describes by ffmpeg
        (scale, then place so the crop centre lands mid-frame), reads as confirmed; the same
        clip placed as a flipped Tilt would places it reads inverted."""
        folder = os.path.join(self.dir, "fixture")
        e = reframe.tilt_fixture(folder)
        self.assertEqual(e, reframe.tilt_expect())
        with open(os.path.join(folder, reframe.TILT_EXPECT_FILE)) as f:
            self.assertEqual(json.load(f), e)
        info = reframe.probe(os.path.join(folder, reframe.TILT_VIDEO))
        self.assertEqual((info["width"], info["height"]), tuple(e["source"]))
        s = e["scale"]
        sw, sh = e["source"]
        cx, cy = e["centre"]
        outw, outh = e["out"]

        def emulate(name, tilt_dir):
            # Place the scaled source so that its pixel (x, y) lands at the module's formula:
            # moved by the crop's shift in output pixels (Pan and Tilt are not pixels).
            dx, dy = reframe.shift_px((sw, sh), s, (cx, cy))
            ox = outw / 2.0 + (0 - sw / 2.0) * s + dx
            oy = outh / 2.0 + (0 - sh / 2.0) * s + tilt_dir * dy
            path = os.path.join(self.dir, name)
            fc = (f"color=c=black:s={outw}x{outh}:r=1:d=1[bg];"
                  f"[0:v]scale={round(sw * s)}:{round(sh * s)}[fg];"
                  f"[bg][fg]overlay=x={ox:.1f}:y={oy:.1f}:shortest=1,scale={self.OUT[0]}:{self.OUT[1]}")
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "0.5", "-i",
                            os.path.join(folder, reframe.TILT_VIDEO), "-filter_complex", fc,
                            "-frames:v", "1", path], check=True)
            w, h, rgb = reframe.read_rgb(path)
            return reframe.tilt_check(rgb, (w, h), e)

        good = emulate("good.png", 1)
        self.assertEqual((good["pan"], good["tilt"]), ("confirmed", "confirmed"), good)
        flipped = emulate("flipped.png", -1)
        self.assertEqual((flipped["pan"], flipped["tilt"]), ("confirmed", "inverted"), flipped)

    def test_the_cli_writes_the_result_and_exits_by_it(self):
        import resolve_workflow as rw
        folder = os.path.join(self.dir, "fx")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rw.cmd_tilt_fixture(argparse.Namespace(out=folder)), 0)
        self.assertIn("ONE still", out.getvalue())
        self.assertIn(str(self.expect["props"]["Pan"]), out.getvalue())
        self.render(self.expect["expect"]["a"], self.expect["expect"]["b"], "ok.ppm")
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            rc = rw.cmd_tilt_check(argparse.Namespace(still=os.path.join(self.dir, "ok.ppm"),
                                                      fixture=folder, tolerance=None))
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("Pan CONFIRMED, Tilt CONFIRMED", out.getvalue())
        with open(os.path.join(folder, reframe.TILT_RESULT_FILE)) as f:
            self.assertTrue(json.load(f)["ok"])
        self.render(self.expect["if_tilt_flipped"]["a"], self.expect["if_tilt_flipped"]["b"], "bad.ppm")
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            rc = rw.cmd_tilt_check(argparse.Namespace(still=os.path.join(self.dir, "bad.ppm"),
                                                      fixture=folder, tolerance=None))
        self.assertEqual(rc, 2)
        self.assertIn("Tilt INVERTED", out.getvalue())
        self.assertIs(reframe.TILT_CONFIRMED, True)   # a check never changes it

    def test_the_fixture_command_refuses_a_folder_in_a_repository(self):
        import resolve_workflow as rw
        inside = HERE.parent / "tilt-fixture-should-not-exist"
        with redirect_stderr(io.StringIO()) as err:
            rc = rw.cmd_tilt_fixture(argparse.Namespace(out=str(inside)))
        self.assertEqual(rc, 1)
        self.assertIn("inside", err.getvalue())
        self.assertFalse(inside.exists())


if __name__ == "__main__":
    unittest.main()
