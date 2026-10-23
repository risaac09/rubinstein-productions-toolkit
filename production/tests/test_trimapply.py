"""
Offline tests for rpresolve.trimapply. Synthetic manifests and review TSVs;
no media, no Resolve.

Run: python3 -m unittest discover production/tests -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import cutlist, trimapply as ta, trimreview as tr
from test_manifest_v2 import two_sources, v1, write


def row(kind="silence", s=2.0, e=3.0, sug="tighten", conf="high", clip="c1", span=1, source=None):
    r = {"start": s, "end": e, "kind": kind, "text": "t", "confidence": conf, "suggestion": sug,
         "clip": clip, "span": span, "source_start": s, "source_end": e}
    if source:
        r["source"] = source
    return r


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, rows, manifest=None, spans=((1.0, 6.0),)):
        m = manifest or v1(self.d, spans=spans)
        self.mpath = os.path.join(self.d, "m.json")
        write(self.mpath, json.dumps(m))
        self.tsv = os.path.join(self.d, "review.tsv")
        write(self.tsv, tr.tsv(rows))
        self.out = os.path.join(self.d, "new.json")
        return m

    def go(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ta.main([self.mpath, self.tsv, "--out", self.out, *extra])
        return code, err.getvalue()

    def refused(self, *extra, pattern):
        code, err = self.go(*extra)
        self.assertEqual(code, 2, err)
        self.assertRegex(err, pattern)
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.out + ta.APPLIED))

    def spans(self):
        with open(self.out) as f:
            return [(s["in"], s["out"]) for s in json.load(f)["clips"][0]["spans"]]


class TestGate(Base):
    def test_needs_an_explicit_list(self):
        self.files([row()])
        self.refused(pattern="exactly one of --accept or --accepted-file")
        write(os.path.join(self.d, "a.txt"), "1\n")
        self.refused("--accept", "1", "--accepted-file", os.path.join(self.d, "a.txt"),
                     pattern="exactly one")

    def test_bad_lists(self):
        self.files([row(), row(s=4, e=5)])
        self.refused("--accept", ",", pattern="empty")
        self.refused("--accept", "1,1", pattern="twice")
        self.refused("--accept", "x", pattern="not a row number")
        self.refused("--accept", "0", pattern="counted from 1")
        self.refused("--accept", "3", pattern="row 3 does not exist")

    def test_accepted_file_with_comments_and_ranges(self):
        self.files([row(s=2, e=3.5), row(s=4, e=5.5, sug="cut-candidate")])
        write(os.path.join(self.d, "a.txt"), "# looked at both\n1-2  # ok\n")
        code, err = self.go("--accepted-file", os.path.join(self.d, "a.txt"))
        self.assertEqual(code, 0, err)

    def test_rows_that_are_not_cuttable(self):
        self.files([row(kind="asr-loop", sug="keep"), row(sug="keep"), row(conf="low"),
                    row(kind="filler", s=2, e=2.3, conf="high", sug="cut-candidate")])
        self.refused("--accept", "1", pattern="row 1 is kind asr-loop")
        self.refused("--accept", "2", pattern="row 2 is suggested keep")
        self.refused("--accept", "3", pattern="row 3 has low confidence")

    def test_outputs(self):
        self.files([row(s=2, e=3.5)])
        write(self.out, "taken")
        code, err = self.go("--accept", "1")
        self.assertEqual((code, read(self.out)), (2, "taken"))
        os.remove(self.out)
        self.out = self.mpath
        code, err = self.go("--accept", "1")
        self.assertEqual(code, 2)
        self.assertIn("is the manifest", err)
        self.out = self.tsv
        self.assertEqual(self.go("--accept", "1")[0], 2)

    def test_refuses_a_git_tree(self):
        self.files([row(s=2, e=3.5)])
        self.out = str(Path(__file__).resolve().parent / "never-written.json")
        code, err = self.go("--accept", "1")
        self.assertEqual(code, 2)
        self.assertIn("git working tree", err)
        self.assertFalse(os.path.exists(self.out))

    def test_review_must_match_the_manifest(self):
        self.files([row(clip="nope")])
        self.refused("--accept", "1", pattern="not in the manifest")
        self.files([row(s=9, e=10.5)])
        self.refused("--accept", "1", pattern="outside c1 span 1")
        self.files([row(span=4)])
        self.refused("--accept", "1", pattern="not in the manifest")

    def test_header_and_whole_source_reviews(self):
        self.files([row()])
        write(self.tsv, "a\tb\n1\t2\n")
        self.refused("--accept", "1", pattern="trim-review header")
        write(self.tsv, tr.tsv([{**row(), "clip": "", "span": ""}]))
        self.refused("--accept", "1", pattern="whole source")

    def test_speech_false_manifest(self):
        m = {"version": 1, "speech": False, "source_path": "/x.mov",
             "clips": [{"name": "c1", "spans": [{"in": 1, "out": 6}]}]}
        self.files([row()], manifest=m)
        self.refused("--accept", "1", pattern="speech")

    def test_overlapping_rows(self):
        self.files([row(s=2, e=3.5), row(s=3.0, e=4.5)])
        self.refused("--accept", "1-2", pattern="rows 1 and 2 overlap")

    def test_remnant_under_a_frame_and_empty_clip(self):
        self.files([row(kind="filler", s=1.0, e=5.99, sug="cut-candidate")])
        self.refused("--accept", "1", pattern="under one frame")
        self.files([row(kind="filler", s=1.0, e=6.0, sug="cut-candidate")])
        self.refused("--accept", "1", pattern="nothing would be left")

    def test_silence_too_short_for_the_pad(self):
        self.files([row(s=2.0, e=2.2)])
        self.refused("--accept", "1", pattern="too short")


class TestApply(Base):
    def test_middle_cut_with_pad_and_cleared_fields(self):
        m = self.files([row(s=2.0, e=4.0)])
        m["clips"][0]["spans"][0].update(end_words="end", source_text="x", audio_tail=0.1)
        write(self.mpath, json.dumps(m))
        before = read(self.mpath, "rb")
        code, err = self.go("--accept", "1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.spans(), [(1.0, 2.12), (3.88, 6.0)])
        with open(self.out) as f:
            new = json.load(f)
        first, last = new["clips"][0]["spans"]
        self.assertEqual((first.get("end_words"), first.get("source_text")), (None, None))
        self.assertEqual((last.get("end_words"), last.get("source_text")), ("end", None))
        self.assertEqual(last["audio_tail"], 0.1)
        self.assertEqual(new["version"], 1)
        with open(self.out + ta.APPLIED) as f:
            side = json.load(f)
        self.assertEqual(side["accepted"], [1])
        self.assertEqual(side["pad"], 0.12)
        self.assertEqual(side["manifest_sha256"], cutlist.sha256_file(self.mpath))
        self.assertEqual(side["review_sha256"], cutlist.sha256_file(self.tsv))
        self.assertEqual(side["frames_removed_per_clip"]["c1"], 125 - 28 - 53)
        self.assertEqual(side["rows"][0]["removed"], [2.12, 3.88])
        self.assertTrue(side["cleared_fields"])
        self.assertEqual(read(self.mpath, "rb"), before)  # never in place

    def test_head_and_tail_cuts_keep_one_piece(self):
        self.files([row(kind="filler", s=1.0, e=1.5, sug="cut-candidate"),
                    row(kind="filler", s=5.5, e=6.0, sug="cut-candidate")])
        self.assertEqual(self.go("--accept", "1-2")[0], 0)
        self.assertEqual(self.spans(), [(1.5, 5.5)])

    def test_two_rows_in_one_span(self):
        self.files([row(kind="filler", s=2.0, e=2.4, sug="cut-candidate"),
                    row(kind="repeat", s=4.0, e=4.4, sug="tighten", conf="medium")])
        self.assertEqual(self.go("--accept", "1,2")[0], 0)
        self.assertEqual(self.spans(), [(1.0, 2.0), (2.4, 4.0), (4.4, 6.0)])

    def test_only_accepted_rows_are_applied(self):
        self.files([row(kind="filler", s=2.0, e=2.4, sug="cut-candidate"),
                    row(kind="filler", s=4.0, e=4.4, sug="cut-candidate")])
        self.assertEqual(self.go("--accept", "2")[0], 0)
        self.assertEqual(self.spans(), [(1.0, 4.0), (4.4, 6.0)])

    def test_result_validates_and_endcheck_can_read_it(self):
        self.files([row(s=2.0, e=4.0)])
        self.go("--accept", "1")
        m = cutlist.load_manifest(self.out)
        self.assertEqual(cutlist.clip_frames(m["clips"][0], m["fps"]), 28 + 53)

    def test_version_2_in_version_2_out_with_source_column(self):
        m = two_sources(self.d)
        m["clips"][0]["spans"] = [{"source": "main", "in": 1.0, "out": 6.0},
                                  {"source": "b", "in": 1.0, "out": 6.0}]
        self.files([row(s=2.0, e=4.0, source="b", span=2)], manifest=m)
        self.assertEqual(self.go("--accept", "1")[0], 0)
        with open(self.out) as f:
            new = json.load(f)
        self.assertEqual(new["version"], 2)
        self.assertEqual([(s["source"], s["in"], s["out"]) for s in new["clips"][0]["spans"]],
                         [("main", 1.0, 6.0), ("b", 1.0, 2.12), ("b", 3.88, 6.0)])
        os.remove(self.out)
        os.remove(self.out + ta.APPLIED)
        self.files([row(s=2.0, e=4.0, source="main", span=2)], manifest=m)
        self.refused("--accept", "1", pattern="not the span's")


if __name__ == "__main__":
    unittest.main()
