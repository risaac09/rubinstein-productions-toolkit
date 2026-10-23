"""
Offline tests for manifest version 2 (cutlist.sources, source_for, to_v2,
validate_manifest, endcheck over several sources, trimreview over several
sources, rpresolve.manifestup). Synthetic words, synthetic text, no media.

Run: python3 -m unittest discover production/tests -v
"""

import copy
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

from rpresolve import cutlist, interchange, manifestup, trimreview as tr
from test_cutlist import APPROVED, SPEECH, words_from

SHA = "0" * 64


def write(path, text, mode="w"):
    with open(path, mode) as f:
        f.write(text)


def v1(tmp, spans=((1.0, 2.75), (3.6, 4.95))):
    src = os.path.join(tmp, "a.bin")
    write(src, b"x", "wb")
    words = os.path.join(tmp, "a.words.json")
    write(words, json.dumps(words_from(SPEECH)))
    essay = os.path.join(tmp, "essay.md")
    write(essay, APPROVED)
    return {"version": 1, "source_path": src, "source_sha256": SHA, "fps": 25, "words": words,
            "approved_text": essay,
            "clips": [{"name": "c1", "spans": [
                {"in": a, "out": b, "end_words": "x"} for a, b in spans]}]}


def two_sources(tmp):
    m = cutlist.to_v2(v1(tmp))
    b = os.path.join(tmp, "b.bin")
    write(b, b"y", "wb")
    m["sources"].append({"id": "b", "path": b, "sha256": SHA,
                         "words": m["sources"][0]["words"]})  # no approved text
    m["sources"][0]["id"] = "main"
    for s in m["clips"][0]["spans"]:
        s["source"] = "main"
    return m


class TestAccessors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_v1_is_one_source_main(self):
        m = v1(self.d)
        cutlist.validate_manifest(m)
        (s,) = cutlist.sources(m)
        self.assertEqual((s["id"], s["path"], s["sha256"]), ("main", m["source_path"], SHA))
        self.assertEqual(cutlist.source_for(m, m["clips"][0]["spans"][0]), s)

    def test_no_version_is_v1(self):
        m = v1(self.d)
        del m["version"]
        cutlist.validate_manifest(m)
        self.assertEqual(cutlist.version(m), 1)

    def test_unknown_version_refused(self):
        m = v1(self.d)
        m["version"] = 3
        with self.assertRaisesRegex(cutlist.CutlistError, "version 3"):
            cutlist.validate_manifest(m)

    def test_v1_still_refuses_backwards_spans(self):
        m = v1(self.d)
        m["clips"][0]["spans"][1]["in"] = 2.0
        with self.assertRaises(cutlist.CutlistError):
            cutlist.validate_manifest(m)

    def test_to_v2_round_trip(self):
        old = v1(self.d)
        new = cutlist.to_v2(old)
        self.assertEqual(new["version"], 2)
        for k in ("source_path", "source_sha256", "words", "approved_text"):
            self.assertNotIn(k, new)
        self.assertEqual(cutlist.sources(new), cutlist.sources(old))
        self.assertEqual(new["clips"], old["clips"])
        self.assertEqual(cutlist.to_v2(new), new)
        self.assertIn("source_path", old)  # the input is not touched

    def test_build_manifest_version_2(self):
        old = v1(self.d)
        m = cutlist.build_manifest({"a": [(1.0, 2.75)]}, old["source_path"], 25, old["words"],
                                   old["approved_text"], version=2)
        self.assertEqual(m["version"], 2)
        self.assertEqual(cutlist.sources(m)[0]["words"], old["words"])


class TestValidateV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = two_sources(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def bad(self, edit, pattern):
        m = copy.deepcopy(self.m)
        edit(m)
        with self.assertRaisesRegex(cutlist.CutlistError, pattern):
            cutlist.validate_manifest(m)

    def test_valid(self):
        cutlist.validate_manifest(self.m)

    def test_reordered_spans_are_accepted(self):
        self.m["clips"][0]["spans"].reverse()
        cutlist.validate_manifest(self.m)

    def test_span_must_name_a_source_when_there_are_several(self):
        self.bad(lambda m: m["clips"][0]["spans"][0].pop("source"), "names source None")

    def test_unknown_source(self):
        self.bad(lambda m: m["clips"][0]["spans"][0].update(source="zz"), "names source 'zz'")

    def test_span_source_is_optional_with_one_source(self):
        m = cutlist.to_v2(v1(self.tmp.name))
        cutlist.validate_manifest(m)

    def test_v1_keys_not_allowed_beside_sources(self):
        for k in ("source_path", "source_sha256", "words", "approved_text"):
            self.bad(lambda m, k=k: m.update({k: "x"}), f"not '{k}'")

    def test_source_needs_sha_and_words(self):
        self.bad(lambda m: m["sources"][1].pop("sha256"), "no 'sha256'")
        self.bad(lambda m: m["sources"][1].pop("words"), "no 'words'")

    def test_source_ids(self):
        self.bad(lambda m: m["sources"][1].update(id="main"), "repeated")
        self.bad(lambda m: m["sources"][1].update(id="B B"), "not \\[a-z0-9_-\\]")

    def test_same_footage_twice_needs_reuse(self):
        def dup(m):
            m["clips"][0]["spans"].append({"source": "main", "in": 1.2, "out": 1.6})
        self.bad(dup, "overlap in one source")
        dup(self.m)
        self.m["clips"][0]["spans"][-1]["reuse"] = True
        cutlist.validate_manifest(self.m)

    def test_other_source_may_cover_the_same_seconds(self):
        self.m["clips"][0]["spans"].append({"source": "b", "in": 1.2, "out": 1.6})
        cutlist.validate_manifest(self.m)

    def test_speech_false_needs_only_path(self):
        m = {"version": 2, "speech": False, "sources": [{"id": "a", "path": "/x.mov"}],
             "clips": [{"name": "c", "spans": [{"in": 1, "out": 2}]}]}
        cutlist.validate_manifest(m, allow_no_speech=True)

    def test_single_source_helper_refuses_several(self):
        with self.assertRaisesRegex(cutlist.CutlistError, "2 sources; cut reads one"):
            cutlist.single_source(self.m, "cut")


class TestEndcheckV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = two_sources(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_name_their_source_and_no_text_is_none(self):
        spans = self.m["clips"][0]["spans"]
        spans[0].update(**{"in": 1.0, "out": 2.75, "end_words": "everything."})
        spans[1].update(**{"source": "b", "in": 3.6, "out": 4.95, "end_words": "that very quiet."})
        rows = cutlist.endcheck(self.m, audio=False)
        self.assertEqual([r["source"] for r in rows], ["main", "b"])
        self.assertIn(rows[0]["text"]["verdict"], ("pass", "review"))
        self.assertEqual(rows[1]["text"]["verdict"], "none")
        self.assertTrue(rows[1]["ok"])
        self.assertFalse(rows[1]["review"])
        self.assertEqual(cutlist.endcheck_counts(rows)["fail"], 0)

    def test_a_wrong_end_word_still_fails_without_approved_text(self):
        spans = self.m["clips"][0]["spans"]
        spans[1].update(**{"source": "b", "in": 3.6, "out": 4.95, "end_words": "nonsense here"})
        rows = cutlist.endcheck(self.m, audio=False)
        self.assertFalse(rows[1]["ok"])
        self.assertEqual(rows[1]["text"]["verdict"], "none")

    def test_v1_rows_name_main(self):
        rows = cutlist.endcheck(v1(self.tmp.name), audio=False)
        self.assertEqual({r["source"] for r in rows}, {"main"})


class TestTrimReviewV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def spans(self, m):
        m["clips"][0]["spans"] = [{"source": "main", "in": 0.8, "out": 4.0},
                                  {"source": "b", "in": 0.8, "out": 4.0}]
        return m

    def test_v2_rows_carry_source_and_v1_tsv_is_unchanged(self):
        new = self.spans(two_sources(self.tmp.name))
        got = tr.review_manifest(new, audio=False, soft=True)["rows"]
        self.assertTrue(got)
        self.assertEqual({r["source"] for r in got}, {"main", "b"})
        header = tr.tsv(got).split("\n")[0].split("\t")
        self.assertEqual(tuple(header), tr.V2_COLUMNS)
        old = v1(self.tmp.name, spans=((0.8, 4.0),))
        rows = tr.review_manifest(old, audio=False, soft=True)["rows"]
        self.assertTrue(all("source" not in r for r in rows))
        self.assertEqual(tuple(tr.tsv(rows).split("\n")[0].split("\t")), tr.COLUMNS)

    def test_v1_and_single_source_v2_agree(self):
        old = v1(self.tmp.name, spans=((0.8, 4.0),))
        a = tr.review_manifest(old, audio=False, soft=True)["rows"]
        b = tr.review_manifest(cutlist.to_v2(old), audio=False, soft=True)["rows"]
        self.assertEqual(a, [{k: v for k, v in r.items() if k != "source"} for r in b])


class TestInterchangeV2(unittest.TestCase):
    def test_single_source_v2_matches_v1_and_several_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            old = v1(d)
            old["source_path"] = "/nonexistent-v2-test/clip.mov"  # not probed
            a, _ = interchange.build(old, "edl")
            b, _ = interchange.build(cutlist.to_v2(old), "edl")
            self.assertEqual(a, b)
            with self.assertRaisesRegex(cutlist.CutlistError, "interchange reads one source"):
                interchange.build(two_sources(d), "edl", fps="25")


class TestSingleSourceTools(unittest.TestCase):
    def test_cut_pacing_and_reframe_refuse_several_sources(self):
        from rpresolve import pacing, reframe, workflows
        with tempfile.TemporaryDirectory() as d:
            m = two_sources(d)
            path = os.path.join(d, "m.json")
            write(path, json.dumps(m))
            for call, word in ((lambda: workflows.cut_gate(path), "cut"),
                               (lambda: pacing.build(manifest_path=path), "pacing"),
                               (lambda: reframe.plan(m, reader=object()), "reframe")):
                with self.assertRaisesRegex(cutlist.CutlistError, f"{word} reads one source"):
                    call()

    def test_cut_gate_reads_a_single_source_v2(self):
        from rpresolve import workflows
        with tempfile.TemporaryDirectory() as d:
            m = cutlist.to_v2(v1(d))
            path = os.path.join(d, "m.json")
            write(path, json.dumps(m))
            with self.assertRaisesRegex(workflows.Refused, "no longer matches"):  # SHA is fake
                workflows.cut_gate(path)


class TestManifestUp(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = manifestup.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_writes_a_copy_and_never_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "m.json")
            write(src, json.dumps(v1(d)))
            with open(src, "rb") as f:
                before = f.read()
            out = os.path.join(d, "m2.json")
            code, msg, _ = self.run_cli(src, "--out", out)
            self.assertEqual(code, 0, msg)
            with open(out) as f:
                self.assertEqual(json.load(f)["version"], 2)
            with open(src, "rb") as f:
                self.assertEqual(f.read(), before)
            self.assertEqual(self.run_cli(src, "--out", out)[0], 2)  # exists
            self.assertEqual(self.run_cli(src, "--out", src)[0], 2)  # in place

    def test_refuses_a_git_tree(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "m.json")
            write(src, json.dumps(v1(d)))
            here = Path(__file__).resolve().parent
            code, _, err = self.run_cli(src, "--out", str(here / "never-written.json"))
            self.assertEqual(code, 2)
            self.assertIn("git working tree", err)
            self.assertFalse((here / "never-written.json").exists())


if __name__ == "__main__":
    unittest.main()
