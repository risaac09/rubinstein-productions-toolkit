"""
Manifest v2 with several sources through cut, interchange and pacing: one
[auto] timeline whose items each play from their own source's media-pool
clip, span-ordered interchange events with one asset per source, and pacing
over each source's own words. Synthetic files and the shared Resolve fakes.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("RPRESOLVE_LOCK", os.path.join(
    tempfile.gettempdir(), f"rpresolve-test-{os.getpid()}.lock"))

import resolve_fakes as rf  # noqa: E402
from rpresolve import cut, cutlist, interchange as ic, pacing, workflows  # noqa: E402

A = [("hello", 1.0, 1.5), ("world.", 1.6, 2.0)]
B = [("again", 0.5, 0.9), ("friend.", 1.0, 1.4)]


def write_words(path, words):
    with open(path, "w") as f:
        json.dump({"segments": [{"words": [{"word": w, "start": a, "end": b}
                                           for w, a, b in words]}]}, f)


def media(rate=Fraction(25), tc=None, frames=None, audio=True, size=(1920, 1080)):
    return {"rate": rate, "tc": tc, "frames": frames, "size": size,
            "audio": {"streams": 1, "channels": 2, "rate": 48000} if audio else None}


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        d = self.dir = os.path.realpath(self.tmp.name)
        self.paths = {}
        for sid, words in (("a", A), ("b", B)):
            self.paths[sid] = os.path.join(d, f"{sid}.mp4")
            with open(self.paths[sid], "wb") as f:
                f.write(b"synthetic " + sid.encode())
            write_words(os.path.join(d, f"{sid}.json"), words)
        approved = os.path.join(d, "approved.md")
        with open(approved, "w") as f:
            f.write("Hello world. Again friend.")
        self.m = {"version": 2, "fps": 25, "sources": [
            {"id": sid, "path": self.paths[sid], "sha256": cutlist.sha256_file(self.paths[sid]),
             "words": os.path.join(d, f"{sid}.json"), "approved_text": approved}
            for sid in ("a", "b")],
            "clips": [{"name": "mix", "spans": [
                {"source": "b", "in": 0.4, "out": 1.5}, {"source": "a", "in": 0.9, "out": 2.1},
                {"source": "b", "in": 1.6, "out": 2.2}]}]}
        self.path = os.path.join(d, "m.json")
        with open(self.path, "w") as f:
            json.dump(self.m, f)
        clips = [rf.Clip(f"{sid}.mp4", f"clip-{sid}", {"File Path": self.paths[sid],
                                                      "Resolution": "1280x720", "FPS": 25,
                                                      "Type": "Video"}) for sid in ("a", "b")]
        self.home = rf.Timeline("Home", "tl-home")
        self.pool = rf.Folder("Master", clips)
        self.project = rf.Project(timelines=[self.home], current=self.home, root=self.pool)
        self.resolve = rf.Resolve(self.project, page="edit")

    def tearDown(self):
        self.tmp.cleanup()

    def cut(self, **kw):
        return workflows.cut(self.resolve, "RP Automation Sandbox", self.path, prefix="T",
                             audio=False, make_9x16=False, force=True, **kw)


class TestCut(Base):
    def test_items_play_from_their_own_source_in_span_order(self):
        r = self.cut()
        self.assertEqual(r["exit_status"], 0, r)
        (res,) = r["results"]
        self.assertTrue(res["ok"], res["reason"])
        self.assertEqual(res["name"], "T_mix [auto]")
        items = cut.timeline_names(self.project)["T_mix [auto]"].GetItemListInTrack("video", 1)
        self.assertEqual([i.GetMediaPoolItem().GetName() for i in items],
                         ["b.mp4", "a.mp4", "b.mp4"])
        self.assertEqual([(i.GetSourceStartFrame(), i.GetDuration()) for i in items],
                         [(10, 28), (23, 30), (40, 15)])
        self.assertEqual([i.GetStart() for i in items][1:],
                         [items[0].GetStart() + 28, items[0].GetStart() + 58])
        self.assertEqual([x["id"] for x in r["source_items"]], ["a", "b"])

    def test_dry_run_creates_nothing_and_the_sha_binds_the_run(self):
        dry = self.cut(dry_run=True)
        self.assertEqual(dry["would_create"], ["T_mix [auto]"])
        self.assertNotIn("T_mix [auto]", cut.timeline_names(self.project))
        self.assertTrue(self.cut(expect_sha=dry["plan_sha"])["results"][0]["ok"])

    def test_a_second_run_leaves_the_timeline_alone(self):
        self.cut()
        r = self.cut()
        self.assertEqual(r["skipped_existing"], ["T_mix [auto]"])
        self.assertEqual(r["results"], [])

    def test_a_missing_source_clip_refuses(self):
        self.pool.clips.pop()
        with self.assertRaisesRegex(workflows.Refused, "source b's sha256"):
            self.cut()

    def test_the_third_span_has_no_words_so_the_gate_blocks_it_unforced(self):
        with self.assertRaisesRegex(workflows.Refused, "endcheck"):
            workflows.cut_gate(self.path, audio=False)

    def test_a_changed_source_file_refuses_at_the_gate(self):
        with open(self.paths["b"], "wb") as f:
            f.write(b"changed")
        with self.assertRaisesRegex(workflows.Refused, "no longer matches"):
            workflows.cut_gate(self.path, audio=False)

    def test_aspects_are_named_unreframed_for_several_sources(self):
        r = workflows.cut(self.resolve, "RP Automation Sandbox", self.path, prefix="T",
                          audio=False, force=True, dry_run=True)
        self.assertEqual(r["would_create"], ["T_mix [auto]"])
        self.assertIn("several sources", r["unreframed"][0])


class TestInterchange(unittest.TestCase):
    def setUp(self):
        self.m = {"version": 2, "fps": 25, "sources": [
            {"id": "a", "path": "/x/a_one.mov", "sha256": "0" * 64, "words": "w"},
            {"id": "b", "path": "/x/b_two.mov", "sha256": "0" * 64, "words": "w"}],
            "clips": [{"name": "mix", "spans": [
                {"source": "b", "in": 1.0, "out": 2.0}, {"source": "a", "in": 3.0, "out": 5.0}]}]}
        self.media = {"a": media(tc="01:00:00:00"), "b": media(tc="00:10:00:00")}
        self.tl = ic.timeline(self.m, media=self.media)

    def test_events_follow_the_spans_with_their_own_source(self):
        self.assertEqual([e["src"] for e in self.tl["events"]], ["b", "a"])
        self.assertEqual([(e["rec_in"], e["rec_out"]) for e in self.tl["events"]], [(0, 25), (25, 75)])
        self.assertEqual(self.tl["frames"], 75)

    def test_edl_uses_each_sources_reel_and_timecode(self):
        lines = ic.edl(self.tl).splitlines()
        self.assertTrue(any("B_TWO" in l and "00:10:01:00 00:10:02:00" in l for l in lines))
        self.assertTrue(any("A_ONE" in l and "01:00:03:00 01:00:05:00" in l for l in lines))
        self.assertIn("* FROM CLIP NAME: b_two.mov", lines)

    def test_fcpxml_has_one_asset_per_source(self):
        text = ic.fcpxml(self.tl)
        self.assertEqual(text.count("<asset "), 2)
        clips = [l for l in text.splitlines() if "<asset-clip" in l]
        self.assertIn('ref="r3"', clips[0])  # b is the second source in the manifest
        self.assertIn('name="b_two.mov"', clips[0])
        self.assertIn('ref="r2"', clips[1])

    def test_otio_clips_name_their_own_files(self):
        doc = json.loads(ic.otio(self.tl))
        v1 = doc["tracks"]["children"][0]["children"]
        self.assertEqual([c["media_references"]["DEFAULT_MEDIA"]["target_url"].rsplit("/", 1)[1] for c in v1],
                         ["b_two.mov", "a_one.mov"])
        self.assertEqual(len(doc["tracks"]["children"]), 2)

    def test_otio_refuses_sources_that_differ_in_having_sound(self):
        tl = ic.timeline(self.m, media={"a": media(), "b": media(audio=False)})
        with self.assertRaisesRegex(ic.InterchangeError, "some sources have sound"):
            ic.otio(tl)

    def test_one_source_past_its_own_end_names_that_source(self):
        with self.assertRaisesRegex(ic.InterchangeError, "b_two.mov"):
            ic.timeline(self.m, media={"a": media(), "b": media(frames=30)})

    def test_a_split_edit_at_a_change_of_source_is_refused(self):
        self.m["clips"][0]["spans"][0]["audio_tail"] = 0.2
        with self.assertRaisesRegex(cutlist.CutlistError, "change of source"):
            ic.timeline(self.m, media=self.media)

    def test_a_split_edit_inside_one_source_still_works(self):
        self.m["clips"][0]["spans"] = [{"source": "a", "in": 1.0, "out": 2.0, "audio_tail": 0.2},
                                       {"source": "a", "in": 3.0, "out": 4.0}]
        tl = ic.timeline(self.m, media=self.media)
        self.assertEqual(tl["events"][0]["a_out"], 55)

    def test_unprobed_sources_warn_once_each(self):
        tl = ic.timeline(self.m, media=None)
        self.assertEqual(len(tl["warnings"]), 2)

    def test_sources_with_one_reel_get_distinct_reels(self):
        self.m["sources"][0]["path"] = "/x/clip_001.mov"
        self.m["sources"][1]["path"] = "/x/clip_001.mp4"
        tl = ic.timeline(self.m, media=self.media)
        self.assertEqual(len({s["reel"] for s in tl["sources"].values()}), 2)

    def test_report_lists_the_sources(self):
        self.assertEqual(ic.report(self.tl, "edl")["sources"], ["a_one.mov", "b_two.mov"])


class TestPacing(Base):
    def test_each_span_reads_its_own_sources_words(self):
        rep = pacing.build(manifest_path=self.path)
        # b 0.4-1.5 holds both b words; a 0.9-2.1 holds both a words; b 1.6-2.2 holds none
        self.assertEqual(rep["words"], 4)
        self.assertAlmostEqual(rep["duration_s"], 1.1 + 1.2 + 0.6, places=3)

    def test_words_override_is_refused_for_several_sources(self):
        with self.assertRaisesRegex(pacing.PacingError, "2 sources"):
            pacing.build(words_path=os.path.join(self.dir, "a.json"), manifest_path=self.path)

    def test_a_split_edit_at_a_change_of_source_is_refused(self):
        self.m["clips"][0]["spans"][0]["audio_tail"] = 0.1
        with open(self.path, "w") as f:
            json.dump(self.m, f)
        with self.assertRaisesRegex(cutlist.CutlistError, "change of source"):
            pacing.build(manifest_path=self.path)


if __name__ == "__main__":
    unittest.main()
