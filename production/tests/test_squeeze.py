"""
Tests for rpresolve.squeeze and the squeeze command: the record is found beside a card
copy or inside a folder, a named clip beats the default, a card with no record is an open
question, a bad factor is refused, and `set` writes what `show` reads back.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cmd_squeeze, squeeze  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.realpath(tmp.name)
        self.card = os.path.join(self.dir, "CAM-abc123")
        os.makedirs(os.path.join(self.card, "DCIM"))
        self.clips = [os.path.join(self.card, "DCIM", n) for n in ("A.MOV", "B.MOV", "C.MP4")]
        for c in self.clips:
            Path(c).write_text("")

    def make_card(self):
        Path(self.card + squeeze.CARD_SUFFIX).write_text("{}")


class TestRecord(Base):
    def test_no_record_is_none_and_only_a_card_is_an_open_question(self):
        self.assertIsNone(squeeze.factor(self.clips[0]))
        self.assertFalse(squeeze.unanswered(self.clips[0]))  # loose footage: nobody is asked
        self.make_card()
        self.assertEqual(squeeze.card_root(self.clips[0]), self.card)
        self.assertTrue(squeeze.unanswered(self.clips[0]))

    def test_a_named_clip_beats_the_default_and_one_answers_the_card(self):
        self.make_card()
        record = squeeze.write(self.card, default=1, clips={"A.MOV": 1.5})
        self.assertEqual(record, self.card + squeeze.SUFFIX)
        self.assertEqual([squeeze.factor(c) for c in self.clips], [1.5, 1.0, 1.0])
        self.assertFalse(any(squeeze.unanswered(c) for c in self.clips))

    def test_write_merges_and_a_record_without_a_default_leaves_the_rest_open(self):
        self.make_card()
        squeeze.write(self.card, clips={"A.MOV": 2})
        self.assertEqual(squeeze.factor(self.clips[0]), 2.0)
        self.assertTrue(squeeze.unanswered(self.clips[1]))
        squeeze.write(self.card, default=1)
        self.assertEqual([squeeze.factor(c) for c in self.clips], [2.0, 1.0, 1.0])

    def test_a_record_inside_a_folder_and_the_nearest_one_wins(self):
        Path(self.card, squeeze.INSIDE).write_text(json.dumps({"default": 1.33}))
        self.assertEqual(squeeze.factor(self.clips[0]), 1.33)
        Path(self.card, "DCIM", squeeze.INSIDE).write_text(json.dumps({"default": 1.8}))
        self.assertEqual(squeeze.factor(self.clips[0]), 1.8)

    def test_refusals(self):
        for bad in (0.5, 3, "1.5", True, None):
            self.assertTrue(squeeze.factor_problem(bad), bad)
        self.assertIsNone(squeeze.factor_problem(1))
        with self.assertRaisesRegex(squeeze.SqueezeError, "a number from 1 to 2.5"):
            squeeze.write(self.card, default=4)
        with self.assertRaisesRegex(squeeze.SqueezeError, "not a folder"):
            squeeze.write(self.clips[0], default=1)
        Path(self.card + squeeze.SUFFIX).write_text("{not json")
        with self.assertRaisesRegex(squeeze.SqueezeError, "cannot read the squeeze record"):
            squeeze.factor(self.clips[0])
        Path(self.card + squeeze.SUFFIX).write_text(json.dumps({"clips": {"A.MOV": 9}}))
        with self.assertRaisesRegex(squeeze.SqueezeError, "A.MOV"):
            squeeze.factor(self.clips[0])


class TestCommand(Base):
    EXIF = {"A.MOV": {}, "B.MOV": {"Panasonic:LensType": "24-70mm"}, "C.MP4": {"ExifIFD:LensModel": ""}}

    def run_cmd(self, *argv):
        import argparse
        p = argparse.ArgumentParser()
        cmd_squeeze.add_arguments(p)
        exif = {c: self.EXIF[os.path.basename(c)] for c in self.clips}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cmd_squeeze, "_exif_for", lambda files: exif), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cmd_squeeze.run(p.parse_args(list(argv)))
        return status, out.getvalue(), err.getvalue()

    def test_show_marks_an_unanswered_card_and_the_lensless_clips(self):
        status, out, err = self.run_cmd("show", self.card)
        self.assertEqual(status, 0)  # not a card yet: nothing is asked
        self.assertIn("none", out)
        self.make_card()
        status, out, err = self.run_cmd("show", self.card)
        self.assertEqual(status, 2)
        self.assertEqual(out.count("unanswered"), 3)
        self.assertEqual(out.count("(no lens data)"), 2)
        self.assertIn("3 unanswered (2 with no lens data", err)

    def test_set_for_the_card_for_named_clips_and_for_manual_lenses(self):
        self.make_card()
        self.assertEqual(self.run_cmd("set", self.card)[0], 1)  # no factor
        self.assertEqual(self.run_cmd("set", self.card, "--factor", "9")[0], 1)
        status, out, _ = self.run_cmd("set", self.card, "--factor", "1.5", "--manual-lens")
        self.assertEqual(status, 0)
        self.assertEqual([squeeze.factor(c) for c in self.clips], [1.5, 1.0, 1.5])
        self.assertEqual(self.run_cmd("show", self.card)[0], 0)
        status, _, _ = self.run_cmd("set", self.card, "--factor", "2", "--clip", "B.MOV")
        self.assertEqual(squeeze.factor(self.clips[1]), 2.0)
        self.assertEqual(json.loads(Path(self.card + squeeze.SUFFIX).read_text())["default"], 1)
        self.run_cmd("set", self.card, "--factor", "1")
        self.assertEqual(squeeze.factor(self.clips[0]), 1.5)  # a named clip outlives a new default

    def test_manual_lens_keeps_a_default_somebody_already_gave(self):
        squeeze.write(self.card, default=1.33)
        self.run_cmd("set", self.card, "--factor", "1.5", "--manual-lens")
        self.assertEqual([squeeze.factor(c) for c in self.clips], [1.5, 1.33, 1.5])


if __name__ == "__main__":
    unittest.main()
