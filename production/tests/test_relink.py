"""
Tests for relink: the pure matching (rpresolve.relink) and the tool
(workflows.relink through the MCP handler) against the shared fakes.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import relink as rl  # noqa: E402
from rpresolve import workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

REG = server.build_registry()

GOOD = {"kind": "video", "tc": "22:10:10:28", "frames": 225, "resolution": (5760, 4320), "fps": 29.97}
CLIP = {"path": "/Volumes/LUMIX/DCIM/P1.MOV", "kind": "video", "tc": "22:10:10:28", "frames": 225,
        "resolution": (5760, 4320), "fps": 29.97}


class TestFacts(unittest.TestCase):
    def test_timecode_drop_or_not_is_one_timecode(self):
        self.assertEqual(rl.normalize_tc("22:10:10;28"), "22:10:10:28")
        self.assertEqual(rl.normalize_tc("22:10:10:28"), "22:10:10:28")
        self.assertEqual(rl.normalize_tc("garbage"), "")
        self.assertEqual(rl.normalize_tc(None), "")

    def test_clip_facts_from_resolve_properties(self):
        f = rl.clip_facts({"File Path": "/x/P1.MOV", "Start TC": "22:10:10;28", "Frames": "225",
                           "Resolution": "5760x4320", "FPS": "29.97", "Type": "Video + Audio"})
        self.assertEqual((f["kind"], f["tc"], f["frames"], f["resolution"], f["fps"]),
                         ("video", "22:10:10:28", 225, (5760, 4320), 29.97))

    def test_kinds_by_extension_first(self):
        self.assertEqual(rl.kind_of("/x/a.RW2", "Video + Audio"), "still")
        self.assertEqual(rl.kind_of("/x/a.wav"), "audio")
        self.assertEqual(rl.kind_of("/x/a.mov", "Audio"), "audio")
        self.assertEqual(rl.kind_of("/x/a.mov", "Video + Audio"), "video")

    def test_probe_facts_frames_fall_back_to_duration(self):
        info = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                             "avg_frame_rate": "30000/1001", "duration": "7.5075"}],
                "format": {}}
        f = rl.probe_facts(info, "01:00:00:00")
        self.assertEqual((f["frames"], f["resolution"]), (225, (1920, 1080)))
        self.assertAlmostEqual(f["fps"], 29.97, places=2)

    def test_probe_facts_without_picture_is_audio(self):
        self.assertEqual(rl.probe_facts({"streams": [{"codec_type": "audio"}]}, "")["kind"], "audio")


class TestCompare(unittest.TestCase):
    def test_all_four_facts_agree(self):
        self.assertEqual(rl.compare(CLIP, GOOD), (True, ""))

    def test_one_frame_of_slack_and_no_more(self):
        self.assertTrue(rl.compare(CLIP, {**GOOD, "frames": 226})[0])
        self.assertFalse(rl.compare(CLIP, {**GOOD, "frames": 227})[0])

    def test_each_fact_can_refuse(self):
        for change in ({"tc": "22:10:11:00"}, {"resolution": (3840, 2160)}, {"fps": 25.0}):
            ok, why = rl.compare(CLIP, {**GOOD, **change})
            self.assertFalse(ok, change)
            self.assertTrue(why, change)

    def test_a_missing_fact_is_never_a_match(self):
        self.assertIn("no tc", rl.compare({**CLIP, "tc": ""}, GOOD)[1])
        self.assertIn("file has no tc", rl.compare(CLIP, {**GOOD, "tc": ""})[1])


class TestDecide(unittest.TestCase):
    def probed(self, *rows):
        return [(p, f, s) for p, f, s in rows]

    def test_one_verified_file(self):
        d = rl.decide(CLIP, self.probed(("/nas/a/P1.MOV", GOOD, 100)), ["/nas"])
        self.assertEqual((d["status"], d["path"], d["alternates"]), (rl.VERIFIED, "/nas/a/P1.MOV", 0))

    def test_same_name_from_another_shoot_is_refused(self):
        other = {**GOOD, "tc": "09:00:00:00"}
        d = rl.decide(CLIP, self.probed(("/nas/b/P1.MOV", other, 100)), ["/nas"])
        self.assertEqual(d["status"], rl.NO_MATCH)
        self.assertEqual(d["path"], "")

    def test_identical_copies_pick_the_earliest_root(self):
        d = rl.decide(CLIP, self.probed(("/nas2/x/P1.MOV", GOOD, 100), ("/nas1/y/P1.MOV", GOOD, 100)),
                      ["/nas1", "/nas2"])
        self.assertEqual((d["status"], d["path"], d["alternates"]), (rl.VERIFIED, "/nas1/y/P1.MOV", 1))

    def test_verified_files_of_different_sizes_are_ambiguous(self):
        d = rl.decide(CLIP, self.probed(("/nas/a/P1.MOV", GOOD, 100), ("/nas/b/P1.MOV", GOOD, 101)), ["/nas"])
        self.assertEqual((d["status"], d["path"]), (rl.AMBIGUOUS, ""))

    def test_stills_and_audio_are_never_relinked(self):
        for kind in ("still", "audio"):
            d = rl.decide({**CLIP, "kind": kind}, self.probed(("/nas/a/P1.MOV", GOOD, 1)), ["/nas"])
            self.assertEqual((d["status"], d["path"]), (rl.UNVERIFIABLE, ""), kind)

    def test_a_clip_with_no_timecode_is_unverifiable_not_guessed(self):
        d = rl.decide({**CLIP, "tc": ""}, self.probed(("/nas/a/P1.MOV", GOOD, 100)), ["/nas"])
        self.assertEqual((d["status"], d["path"]), (rl.UNVERIFIABLE, ""))

    def test_no_candidates(self):
        self.assertEqual(rl.decide(CLIP, [], ["/nas"])["status"], rl.NO_MATCH)

    def test_unreadable_candidate_does_not_verify(self):
        d = rl.decide(CLIP, self.probed(("/nas/a/P1.MOV", None, 100)), ["/nas"])
        self.assertEqual(d["status"], rl.NO_MATCH)


class TestIndex(unittest.TestCase):
    def test_walks_roots_in_order_and_skips_hidden(self):
        with tempfile.TemporaryDirectory() as t:
            t = os.path.realpath(t)
            for rel in ("a/P1.MOV", "a/._P1.MOV", "a/.hid/P1.MOV", "b/P1.MOV"):
                os.makedirs(os.path.join(t, os.path.dirname(rel)), exist_ok=True)
                Path(t, rel).write_bytes(b"x")
            idx = rl.index_roots([os.path.join(t, "b"), os.path.join(t, "a")])
            self.assertEqual(idx["p1.mov"], [os.path.join(t, "b", "P1.MOV"), os.path.join(t, "a", "P1.MOV")])
            self.assertNotIn("._P1.MOV", idx)

    def test_names_are_nfc(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "Café.mov")
        with tempfile.TemporaryDirectory() as t:
            Path(t, nfd).write_bytes(b"x")
            self.assertIn(rl.name_key("CAFÉ.mov"), rl.index_roots([t]))  # case and form do not matter

    def test_rank_prefers_the_folder_holding_most_offline_names(self):
        index = {"a.mov": ["/n/x/A.MOV", "/n/y/A.MOV"], "b.mov": ["/n/y/B.MOV"]}
        votes = rl.folder_votes(["A.MOV", "B.MOV"], index)
        self.assertEqual(rl.rank_candidates(index["a.mov"], votes), ["/n/y/A.MOV", "/n/x/A.MOV"])

    def test_drift_names_only_what_changed(self):
        self.assertEqual(rl.drift({"Start TC": "1", "FPS": "25"}, {"Start TC": "2", "FPS": "25"}),
                         {"Start TC": ("1", "2")})


# ---------------------------------------------------------------------------
# the tool
# ---------------------------------------------------------------------------

def props(path, **more):
    return {"File Path": path, "Type": "Video + Audio", "Start TC": "22:10:10;28", "Frames": "225",
            "Resolution": "5760x4320", "FPS": "29.97", "Input Color Space": "Panasonic V-Gamut/V-Log",
            "Data Level": "Auto", **more}


class Session:
    def __init__(self, resolve):
        self.resolve = resolve

    def get(self):
        return self.resolve


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.journal = os.path.join(self.dir, "logs", "writes.jsonl")
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_JOURNAL": self.journal,
                                           "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        # A NAS folder with the files; Resolve remembers them at a card path that is gone.
        self.nas = os.path.join(self.dir, "nas", "100_PANA")
        os.makedirs(self.nas)
        for n in ("P1.MOV", "P2.MOV", "P3.MOV"):  # P3 is online in the project but also on the NAS
            Path(self.nas, n).write_bytes(b"x" * 100)
        self.online_path = os.path.join(self.dir, "P3.MOV")
        Path(self.online_path).write_bytes(b"x")
        self.c1 = rf.Clip("P1.MOV", "id-1", props("/card/DCIM/P1.MOV"))
        self.c2 = rf.Clip("P2.MOV", "id-2", props("/card/DCIM/P2.MOV"))
        self.c3 = rf.Clip("P3.MOV", "id-3", props(self.online_path))
        self.c4 = rf.Clip("title", "id-4", {"File Path": "", "Type": "Compound"})
        self.root = rf.Folder("Master", subs=[rf.Folder("GH7", clips=[self.c1, self.c2, self.c3, self.c4])])
        self.project = rf.Project(root=self.root)
        self.resolve = rf.Resolve(self.project)
        self.facts = {"P1.MOV": GOOD, "P2.MOV": GOOD, "P3.MOV": GOOD}
        self.probe = mock.patch.object(workflows, "_probe_file",
                                       side_effect=lambda p: self.facts.get(os.path.basename(p)))
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def events(self):
        if not os.path.exists(self.journal):
            return []
        with open(self.journal) as f:
            return [json.loads(line) for line in f]

    def run_tool(self, **args):
        tool = REG.get("relink")
        a = {"project": "RP Automation Sandbox", "search_roots": [os.path.join(self.dir, "nas")], **args}
        a = schema.coerce(tool.input_schema, a)
        errors = schema.validate(tool.input_schema, a)
        self.assertFalse(errors, errors)
        return tool.handler(schema.with_defaults(tool.input_schema, a),
                            ToolContext(session=Session(self.resolve)))


class TestRelinkTool(Base):
    def test_registered_as_a_write_with_a_required_project(self):
        tool = REG.get("relink")
        self.assertFalse(tool.annotations["readOnlyHint"])
        self.assertIn("project", tool.input_schema["required"])
        self.assertTrue(tool.input_schema["properties"]["dry_run"]["default"])

    def test_dry_run_plans_and_changes_nothing(self):
        dry = self.run_tool()
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["status_counts"], {"VERIFIED": 2})
        self.assertEqual(dry["online"], 1)  # P3 is online; the compound clip is not file-backed
        self.assertEqual([r["result"] for r in dry["results"]], ["planned", "planned"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.events(), [])
        self.assertEqual(self.c1.props["File Path"], "/card/DCIM/P1.MOV")

    def test_dry_run_then_real_run_relinks_and_journals_the_pairs(self):
        dry = self.run_tool()
        real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(real["exit_status"], 0)
        self.assertEqual(real["counts"], {"relinked": 2})
        self.assertEqual(self.c1.props["File Path"], os.path.join(self.nas, "P1.MOV"))
        self.assertEqual(self.c2.props["File Path"], os.path.join(self.nas, "P2.MOV"))
        self.assertEqual(self.c3.props["File Path"], self.online_path)  # an online clip is never touched
        calls = [c for c in rf.mutating_calls() if c[1] == "RelinkClips"]
        self.assertEqual([c[2][0] for c in calls], [["P1.MOV"], ["P2.MOV"]])
        ev = self.events()
        self.assertEqual([e["event"] for e in ev], ["started", "finished"])
        pairs = {(r["old_path"], r["new_path"]) for r in ev[1]["detail"]["relinked"]}
        self.assertEqual(pairs, {("/card/DCIM/P1.MOV", os.path.join(self.nas, "P1.MOV")),
                                 ("/card/DCIM/P2.MOV", os.path.join(self.nas, "P2.MOV"))})
        self.assertEqual(ev[1]["status"], "ok")

    def test_a_real_run_needs_a_plan_sha(self):
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            self.run_tool(dry_run=False)
        self.assertEqual(rf.mutating_calls(), [])

    def test_a_stale_plan_sha_is_refused_and_nothing_moves(self):
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(dry_run=False, plan_sha="0" * 64)
        self.assertEqual([c for c in rf.mutating_calls() if c[1] == "RelinkClips"], [])

    def test_the_plan_changes_when_a_file_under_the_roots_changes(self):
        dry = self.run_tool()
        Path(self.nas, "P1.MOV").write_bytes(b"y" * 101)
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])

    def test_the_wrong_project_is_refused(self):
        with self.assertRaises(Exception) as cm:
            self.run_tool(project="Some Client Project")
        self.assertIn("Some Client Project", str(cm.exception))
        self.assertEqual(rf.mutating_calls(), [])

    def test_only_a_verified_clip_is_relinked(self):
        self.facts["P2.MOV"] = {**GOOD, "tc": "09:00:00:00"}  # same name, another shoot
        dry = self.run_tool()
        self.assertEqual(dry["status_counts"], {"VERIFIED": 1, "NO MATCH": 1})
        real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.c2.props["File Path"], "/card/DCIM/P2.MOV")
        self.assertEqual(real["exit_status"], 2)  # one offline clip is still offline

    def test_nothing_is_relinked_on_a_name_alone(self):
        self.facts.clear()  # ffprobe reads nothing
        dry = self.run_tool()
        self.assertEqual(dry["status_counts"], {"NO MATCH": 2})
        self.assertEqual(dry["results"][0]["new_path"], "")

    def test_max_items_relinks_a_sample(self):
        dry = self.run_tool()
        real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"], max_items=1)
        self.assertEqual(real["counts"], {"relinked": 1, "not attempted (max_items)": 1})
        self.assertEqual(real["exit_status"], 2)
        self.assertIn("dry run again", real["summary"])
        self.assertEqual(self.c2.props["File Path"], "/card/DCIM/P2.MOV")

    def test_a_relink_that_does_not_read_back_fails_and_says_so(self):
        self.project.pool.relink_stuck = True
        dry = self.run_tool()
        real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(real["exit_status"], 1)
        self.assertTrue(all(r["result"].startswith("FAILED") for r in real["results"]))
        self.assertEqual(real["relinked"], [])
        self.assertEqual(self.events()[1]["status"], "failed")

    def test_a_refusing_resolve_fails_the_clip(self):
        self.project.pool.relink_ok = False
        dry = self.run_tool()
        real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("returned false", real["results"][0]["result"])

    def test_a_property_that_reads_differently_afterwards_is_reported_not_reset(self):
        self.project.pool.relink_drift = {"Input Color Space": "Rec.709"}
        dry = self.run_tool()
        real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("Input Color Space", real["problems"][0])
        self.assertEqual(self.c1.props["Input Color Space"], "Rec.709")  # nothing was set back
        self.assertEqual([c for c in rf.CALLS if c[1] == "SetClipProperty"], [])

    def test_a_clip_relinked_by_hand_since_the_dry_run_makes_the_plan_stale(self):
        dry = self.run_tool()
        late = os.path.join(self.dir, "late.mov")
        Path(late).write_bytes(b"x")
        self.c1.props["File Path"] = late  # someone relinked it by hand meanwhile
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.c1.props["File Path"], late)
        self.assertEqual([c for c in rf.mutating_calls() if c[1] == "RelinkClips"], [])

    def test_a_clip_that_comes_online_mid_run_is_skipped_not_relinked(self):
        entry = {"new_path": os.path.join(self.nas, "P1.MOV"), "size": 100}
        late = os.path.join(self.dir, "late.mov")
        Path(late).write_bytes(b"x")
        self.c1.props["File Path"] = late
        r = workflows._relink_one(self.project.pool, self.c1, entry, os.path.exists, os.stat)
        self.assertTrue(r["result"].startswith("skipped"))
        self.assertEqual([c for c in rf.mutating_calls() if c[1] == "RelinkClips"], [])

    def test_a_target_that_changed_mid_run_is_not_relinked(self):
        entry = {"new_path": os.path.join(self.nas, "P1.MOV"), "size": 99}  # the plan saw 99, it is 100
        r = workflows._relink_one(self.project.pool, self.c1, entry, os.path.exists, os.stat)
        self.assertEqual(r["result"], "FAILED: the file changed since the plan")
        self.assertEqual(self.c1.props["File Path"], "/card/DCIM/P1.MOV")

    def test_a_stop_part_way_keeps_the_pairs_already_relinked(self):
        dry = self.run_tool()
        def stopping(pin, pm, check_cancel):
            n = [0]

            def check():
                n[0] += 1
                if n[0] == 2:
                    raise workflows.api.ProjectChanged("project switched")
            return check

        with mock.patch.object(workflows, "_check", stopping):
            with self.assertRaisesRegex(workflows.api.ProjectChanged, "already relinked"):
                self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.c1.props["File Path"], os.path.join(self.nas, "P1.MOV"))
        self.assertEqual(self.c2.props["File Path"], "/card/DCIM/P2.MOV")
        ev = self.events()
        self.assertEqual(ev[1]["status"], "error")
        self.assertEqual([r["old_path"] for r in ev[1]["detail"]["relinked"]], ["/card/DCIM/P1.MOV"])

    def test_the_plan_names_where_the_offline_paths_live(self):
        dry = self.run_tool()
        self.assertEqual(dry["offline_volumes"], {"/card/DCIM": 2})
        self.assertIn("/card/DCIM (2)", dry["summary"])
        self.assertIn("is mounted", dry["summary"])

    def test_names_match_without_regard_to_case(self):
        self.c1.props["File Path"] = "/card/DCIM/p1.mov"
        self.assertEqual(self.run_tool()["status_counts"], {"VERIFIED": 2})

    def test_each_candidate_is_probed_once(self):
        seen = []
        self.probe.stop()
        with mock.patch.object(workflows, "_probe_file", side_effect=lambda p: seen.append(p) or GOOD):
            self.c2.props["File Path"] = "/other/P1.MOV"  # a second clip naming the same file
            self.run_tool()
        self.addCleanup(self.probe.start)
        self.assertEqual(len([p for p in seen if p.endswith("P1.MOV")]), 1)

    def test_a_clip_with_no_timecode_is_unverifiable_in_the_plan(self):
        self.c1.props["Start TC"] = ""
        dry = self.run_tool()
        self.assertEqual(dry["status_counts"], {"VERIFIED": 1, "UNVERIFIABLE": 1})
        self.assertIn("no tc", [r for r in dry["results"] if r["status"] == "UNVERIFIABLE"][0]["reason"])

    def test_folder_limits_the_walk(self):
        self.root.subs.append(rf.Folder("Other", clips=[rf.Clip("P9.MOV", "id-9", props("/card/P9.MOV"))]))
        dry = self.run_tool(folder="GH7")
        self.assertEqual(sum(dry["status_counts"].values()), 2)
        with self.assertRaisesRegex(workflows.Refused, "no bin"):
            self.run_tool(folder="Nope")

    def test_search_roots_must_exist_and_be_folders(self):
        with self.assertRaises(ValueError):
            self.run_tool(search_roots=[os.path.join(self.dir, "missing")])
        with self.assertRaises(ValueError):
            self.run_tool(search_roots=[os.path.join(self.nas, "P1.MOV")])

    def test_tool_count(self):
        self.assertIn("relink", REG.tools)


if __name__ == "__main__":
    unittest.main()
