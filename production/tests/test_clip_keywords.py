"""
Tests for clip_keywords: the plan file (rpresolve.clipkeywords parse and merge),
the offline check command, and the write tool (workflows through the MCP
handler) against the shared Resolve fakes. Nothing here has run against a live
Resolve.

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
from rpresolve import api, clipkeywords as ck, cmd_clip_keywords_check, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import Cancelled, ToolContext  # noqa: E402

REG = server.build_registry()
P = "RP Automation Sandbox"
HEAD = "clip_id\tkeywords\tdescription\n"


class MetaClip(rf.Clip):
    """A pool clip with metadata, as MediaPoolItem.GetMetadata/SetMetadata keep it."""
    refuse_set = False   # SetMetadata answers False and stores nothing
    drop_set = False     # SetMetadata answers True and stores nothing
    mangle = False       # the stored keywords read back with a stray extra word
    refuse_keys = ()     # SetMetadata answers False for these keys only

    def __init__(self, name, uid, path, meta=None):
        super().__init__(name, uid, {"File Path": path, "Type": "Video + Audio"})
        self.meta = dict(meta or {})

    def GetMetadata(self, key=None):
        return dict(self.meta) if key is None else self.meta.get(key, "")

    def SetMetadata(self, key, value=None):
        rf._log(self, "SetMetadata", self.name, key, value)
        if self.refuse_set:
            return False
        if self.drop_set:
            return True
        if not isinstance(key, dict) and key in self.refuse_keys:
            return False
        pairs = key if isinstance(key, dict) else {key: value}
        for k, v in pairs.items():
            self.meta[k] = v + ", oops" if self.mangle and k == "Keywords" else v
        return True


class TestPlanFile(unittest.TestCase):
    def parse(self, text):
        return ck.parse_plan(text)

    def test_a_good_plan(self):
        rows, problems = self.parse(HEAD + "id-1\tcrowd; dusk ;crowd\tWide of the crowd\nid-2\t\tOnly words\n")
        self.assertEqual(problems, [])
        self.assertEqual(rows[0], {"line": 2, "clip_id": "id-1", "keywords": ["crowd", "dusk"],
                                   "description": "Wide of the crowd"})
        self.assertEqual(rows[1]["keywords"], [])

    def test_unknown_column_and_missing_id_column(self):
        _, p = self.parse("clip_id\tkeywords\tmood\nid-1\ta\tb\n")
        self.assertTrue(any("unknown column 'mood'" in x for x in p), p)
        _, p = self.parse("keywords\tdescription\na\tb\n")
        self.assertTrue(any("clip_id" in x for x in p), p)

    def test_empty_id_and_duplicate_id(self):
        _, p = self.parse(HEAD + "\ta\tb\nid-1\ta\tb\nid-1\tc\td\n")
        self.assertTrue(any("line 2" in x and "empty clip_id" in x for x in p), p)
        self.assertTrue(any("line 4" in x and "duplicate" in x and "line 3" in x for x in p), p)

    def test_a_row_that_says_nothing_is_a_problem(self):
        _, p = self.parse(HEAD + "id-1\t \t \n")
        self.assertTrue(any("line 2" in x and "no keywords and no description" in x for x in p), p)

    def test_control_characters(self):
        _, p = self.parse(HEAD + "id-1\tcro\x07wd\tfine\n")
        self.assertTrue(any("control character" in x for x in p), p)

    def test_a_tab_inside_a_value_is_an_extra_cell(self):
        _, p = self.parse(HEAD + "id-1\tcrowd\tthe\tcrowd\n")
        self.assertTrue(any("line 2" in x and "4 cells" in x for x in p), p)

    def test_a_newline_inside_a_value_splits_the_row(self):
        _, p = self.parse(HEAD + "id-1\tcrowd\tthe crowd\nat dusk\n")
        self.assertTrue(any("line 3" in x and "1 cells" in x for x in p), p)

    def test_a_comma_in_a_keyword_is_refused(self):
        _, p = self.parse(HEAD + "id-1\tcrowd, dusk\tx\n")
        self.assertTrue(any("comma" in x for x in p), p)

    def test_over_long_values(self):
        _, p = self.parse(HEAD + "id-1\t" + "k" * (ck.MAX_KEYWORD + 1) + "\tx\n")
        self.assertTrue(any("keyword is" in x for x in p), p)
        _, p = self.parse(HEAD + "id-1\tk\t" + "d" * (ck.MAX_DESCRIPTION + 1) + "\n")
        self.assertTrue(any("description is" in x for x in p), p)
        _, p = self.parse(HEAD + "i" * (ck.MAX_ID + 1) + "\tk\td\n")
        self.assertTrue(any("clip_id is" in x for x in p), p)

    def test_an_empty_plan(self):
        for text in ("", "\n\n", HEAD):
            _, p = self.parse(text)
            self.assertTrue(any("no rows" in x or "header" in x for x in p), (text, p))

    def test_a_bom_before_the_header_is_ignored(self):
        rows, p = self.parse("\ufeffclip_id\tkeywords\tdescription\nid-1\ta\tb\n")
        self.assertEqual((p, len(rows)), ([], 1))

    def test_crlf_and_blank_lines_are_fine(self):
        rows, p = self.parse("clip_id\tkeywords\tdescription\r\n\r\nid-1\ta\tb\r\n")
        self.assertEqual((p, len(rows)), ([], 1))


class TestMerge(unittest.TestCase):
    def test_adds_after_what_is_there_and_keeps_it_byte_for_byte(self):
        new, added = ck.merge_keywords("Beach,  Sunset", ["crowd", "dusk"])
        self.assertEqual(new, "Beach,  Sunset, crowd, dusk")
        self.assertEqual(added, ["crowd", "dusk"])

    def test_case_and_whitespace_do_not_duplicate(self):
        new, added = ck.merge_keywords("Beach, sun  set", ["beach", "SUN set", " Dusk "])
        self.assertEqual((new, added), ("Beach, sun  set, Dusk", ["Dusk"]))

    def test_nothing_to_add_leaves_the_text_alone(self):
        self.assertEqual(ck.merge_keywords("a, b", ["A"]), ("a, b", []))

    def test_existing_text_is_kept_byte_for_byte(self):
        self.assertEqual(ck.merge_keywords("Beach, Sunset, ", ["Crowd"])[0], "Beach, Sunset, Crowd")
        self.assertEqual(ck.merge_keywords("a,", ["b"])[0], "a, b")
        self.assertEqual(ck.merge_keywords("a  ", ["b"])[0], "a  , b")

    def test_empty_existing(self):
        self.assertEqual(ck.merge_keywords("", ["a", "b"]), ("a, b", ["a", "b"]))
        self.assertEqual(ck.merge_keywords(None, ["a"]), ("a", ["a"]))


class TestCheckCommand(unittest.TestCase):
    def test_exit_status_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d, "good.tsv"), Path(d, "bad.tsv")
            good.write_text(HEAD + "id-1\ta\tb\n")
            bad.write_text(HEAD + "id-1\ta\tb\nid-1\ta\tb\n")
            args = mock.Mock()
            args.plan = str(good)
            with mock.patch("builtins.print") as pr:
                self.assertEqual(cmd_clip_keywords_check.run(args), 0)
            self.assertIn("1 row", " ".join(str(c.args[0]) for c in pr.call_args_list))
            args.plan = str(bad)
            with mock.patch("builtins.print"):
                self.assertEqual(cmd_clip_keywords_check.run(args), 1)
            args.plan = str(Path(d, "missing.tsv"))
            with mock.patch("builtins.print"):
                self.assertEqual(cmd_clip_keywords_check.run(args), 1)


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.journal = os.path.join(self.dir, "logs", "writes.jsonl")
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_JOURNAL": self.journal,
                                           "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        self.files = {}
        for n in ("A.MOV", "B.MOV", "C.MOV", "D.MOV", "E.MOV"):
            self.files[n] = os.path.join(self.dir, n)
            Path(self.files[n]).write_bytes(b"x")
        self.a = MetaClip("A.MOV", "id-a", self.files["A.MOV"], {"Keywords": "Beach, Sunset"})
        self.b = MetaClip("B.MOV", "id-b", self.files["B.MOV"], {"Description": "Already described"})
        self.c = MetaClip("C.MOV", "id-c", self.files["C.MOV"])
        self.d1 = MetaClip("D.MOV", "id-d1", self.files["D.MOV"])
        self.d2 = MetaClip("D.MOV", "id-d2", self.files["D.MOV"])
        self.off = MetaClip("E.MOV", "id-e", os.path.join(self.dir, "gone", "E.MOV"))
        self.root = rf.Folder("Master", clips=[self.a, self.b], subs=[
            rf.Folder("More", clips=[self.c, self.d1, self.d2, self.off])])
        self.project = rf.Project(root=self.root)
        self.resolve = rf.Resolve(self.project)
        self.plan = os.path.join(self.dir, "plan.tsv")
        self.write_plan(HEAD + "id-a\tbeach; Crowd\tWide crowd\n"
                               "id-b\tdusk\tNew words\n"
                               "C.MOV\tdawn\tEmpty before\n")

    def write_plan(self, text):
        Path(self.plan).write_text(text)

    def events(self):
        if not os.path.exists(self.journal):
            return []
        with open(self.journal) as f:
            return [json.loads(line) for line in f]

    def sets(self):
        return [c for c in rf.CALLS if c[1] == "SetMetadata"]

    def run_tool(self, cancel=None, **args):
        tool = REG.get("clip_keywords")
        a = {"project": P, "plan": self.plan, **args}
        a = schema.coerce(tool.input_schema, a)
        errors = schema.validate(tool.input_schema, a)
        self.assertFalse(errors, errors)
        return tool.handler(schema.with_defaults(tool.input_schema, a),
                            ToolContext(session=rf.Session(self.resolve), is_cancelled=cancel))

    def real(self, **args):
        dry = self.run_tool()
        return dry, self.run_tool(dry_run=False, plan_sha=dry["plan_sha"], **args)


class TestTool(Base):
    def test_registered_as_a_write_with_a_required_project(self):
        tool = REG.get("clip_keywords")
        self.assertFalse(tool.annotations["readOnlyHint"])
        self.assertIn("project", tool.input_schema["required"])
        self.assertTrue(tool.input_schema["properties"]["dry_run"]["default"])

    def test_dry_run_writes_nothing_and_returns_the_plan(self):
        dry = self.run_tool()
        self.assertTrue(dry["dry_run"])
        self.assertRegex(dry["plan_sha"], "^[0-9a-f]{64}$")
        self.assertEqual(self.sets(), [])
        self.assertEqual(self.events(), [])
        self.assertEqual([r["result"] for r in dry["results"]], ["planned"] * 3)
        self.assertEqual(self.a.meta, {"Keywords": "Beach, Sunset"})

    def test_real_run_adds_keywords_sets_empty_descriptions_and_reads_back(self):
        dry, real = self.real()
        self.assertEqual(real["exit_status"], 2, real)  # B's description was kept: a person looks
        self.assertEqual(self.a.meta["Keywords"], "Beach, Sunset, Crowd")  # beach is already there
        self.assertEqual(self.a.meta["Description"], "Wide crowd")
        self.assertEqual(self.b.meta["Keywords"], "dusk")
        self.assertEqual(self.b.meta["Description"], "Already described")  # never overwritten
        self.assertEqual(self.c.meta, {"Keywords": "dawn", "Description": "Empty before"})
        by = {r["clip"]: r for r in real["results"]}
        self.assertEqual(by["id-b"]["description"], "kept: the clip has another description")
        self.assertEqual(by["id-b"]["result"], "written")
        self.assertEqual([e["event"] for e in self.events()], ["started", "undo", "finished"])
        # only Keywords and Description were ever set
        self.assertEqual({c[2][1] for c in self.sets()}, {"Keywords", "Description"})

    def test_a_description_the_clip_already_has_is_left_alone_even_for_the_same_keywords(self):
        self.write_plan(HEAD + "id-b\t\tDifferent words\n")
        dry, real = self.real()
        self.assertEqual(self.sets(), [])
        self.assertEqual(real["results"][0]["result"], "unchanged")
        self.assertEqual(real["exit_status"], 2)  # reported, a person looks

    def test_a_second_run_changes_nothing(self):
        self.real()
        rf.CALLS.clear()
        again = self.run_tool()
        self.assertEqual({r["result"] for r in again["results"]}, {"unchanged"})
        self.run_tool(dry_run=False, plan_sha=again["plan_sha"])
        self.assertEqual(self.sets(), [])

    def test_a_missing_or_wrong_plan_sha_refuses(self):
        dry = self.run_tool()
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            self.run_tool(dry_run=False)
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(dry_run=False, plan_sha="0" * 64)
        self.assertEqual(self.sets(), [])
        self.assertRegex(dry["plan_sha"], "^[0-9a-f]{64}$")

    def test_a_plan_that_changed_since_the_dry_run_refuses(self):
        dry = self.run_tool()
        self.a.meta["Keywords"] = "Beach, Sunset, Crowd"  # someone edited in Resolve meanwhile
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.sets(), [])

    def test_a_wrong_project_name_refuses(self):
        dry = self.run_tool()
        for kw in ({}, {"dry_run": False, "plan_sha": dry["plan_sha"]}):
            with self.assertRaises(api.ProjectChanged):
                self.run_tool(project="Some Other Project", **kw)
        self.assertEqual(self.sets(), [])

    def test_a_wrong_project_id_refuses(self):
        with self.assertRaises(api.ProjectChanged):
            self.run_tool(project_id="not-this-one")

    def test_a_readback_mismatch_is_a_failed_item(self):
        self.c.mangle = True
        dry, real = self.real()
        by = {r["clip"]: r for r in real["results"]}
        self.assertTrue(by["C.MOV"]["result"].startswith("FAILED"), by["C.MOV"])
        self.assertEqual(by["id-a"]["result"], "written")
        self.assertEqual(real["exit_status"], 1)
        self.assertTrue(real["problems"])

    def test_a_dropped_or_refused_write_is_a_failed_item(self):
        self.c.drop_set = True
        self.b.refuse_set = True
        _, real = self.real()
        by = {r["clip"]: r for r in real["results"]}
        self.assertTrue(by["C.MOV"]["result"].startswith("FAILED"))
        self.assertTrue(by["id-b"]["result"].startswith("FAILED"))
        self.assertEqual(by["id-a"]["result"], "written")
        self.assertEqual(real["exit_status"], 1)

    def test_a_missing_clip_is_reported_and_nothing_else_stops(self):
        self.write_plan(HEAD + "nope\tx\ty\nid-c\tdawn\t\n")
        dry, real = self.real()
        by = {r["clip"]: r for r in real["results"]}
        self.assertEqual(by["nope"]["result"], "not found")
        self.assertEqual(by["id-c"]["result"], "written")
        self.assertEqual(real["exit_status"], 2)

    def test_a_duplicate_clip_name_is_ambiguous_and_never_guessed(self):
        self.write_plan(HEAD + "D.MOV\tx\ty\nid-d2\tz\t\n")
        dry, real = self.real()
        by = {r["clip"]: r for r in real["results"]}
        self.assertEqual(by["D.MOV"]["result"], "ambiguous")
        self.assertIn("2 clips", by["D.MOV"]["reason"])
        self.assertEqual(self.d1.meta, {})
        self.assertEqual(self.d2.meta, {"Keywords": "z"})  # the unique id is exact

    def test_an_offline_clip_is_reported_and_left_alone(self):
        self.write_plan(HEAD + "id-e\tx\ty\n")
        dry, real = self.real()
        self.assertEqual(real["results"][0]["result"], "offline")
        self.assertEqual(self.off.meta, {})
        self.assertEqual(self.sets(), [])

    def test_cancel_between_clips_keeps_what_was_done_and_says_so(self):
        dry = self.run_tool()

        def cancel():
            return len(self.sets()) >= 2  # clip A took two writes; stop before B

        with self.assertRaises(Cancelled) as cm:
            self.run_tool(cancel=cancel, dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual([w["clip"] for w in cm.exception.keywords_written], ["id-a"])
        self.assertIn("already written", str(cm.exception))
        self.assertEqual(self.a.meta["Keywords"], "Beach, Sunset, Crowd")
        self.assertEqual(self.b.meta, {"Description": "Already described"})
        done = self.events()[-1]
        self.assertEqual(done["status"], "error")
        self.assertEqual([w["clip"] for w in done["detail"]["keywords_written"]], ["id-a"])

    def test_one_clip_named_twice_refuses_the_whole_plan(self):
        self.write_plan(HEAD + "id-c\tdawn\tFirst\nid-a\tx\ty\nC.MOV\tdusk\tSecond\n")
        with self.assertRaisesRegex(workflows.Refused, "rows? .*line 2.*line 4|line 2 and line 4"):
            self.run_tool()
        self.assertEqual(self.sets(), [])
        self.assertEqual(self.c.meta, {})

    def test_an_id_that_is_also_another_clips_name_is_ambiguous(self):
        self.c.name = "id-a"
        self.write_plan(HEAD + "id-a\tx\ty\n")
        dry = self.run_tool()
        self.assertEqual(dry["results"][0]["result"], "ambiguous")

    def test_a_rename_between_dry_run_and_real_run_refuses(self):
        dry = self.run_tool()
        self.c.name = "Renamed.MOV"
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.sets(), [])

    def test_the_write_never_removes_a_keyword_or_replaces_a_description_set_since_planning(self):
        dry = self.run_tool()
        e = {"keywords_added": ["dawn"], "kw": ["dawn"], "set_desc": True, "desc": "Mine",
             "new_kw": "dawn", "name": "C.MOV"}
        self.c.meta = {"Keywords": "kept, dawn2", "Description": "Someone's"}
        self.assertEqual(ck._write_one(self.c, e), "written")
        self.assertEqual(self.c.meta, {"Keywords": "kept, dawn2, dawn", "Description": "Someone's"})
        self.assertTrue(dry["plan_sha"])

    def test_a_half_written_clip_is_in_the_undo_record_and_the_failure_says_so(self):
        self.c.refuse_keys = ("Description",)
        _, real = self.real()
        row = {r["clip"]: r for r in real["results"]}["C.MOV"]
        self.assertIn("Keywords was written", row["result"])
        wrote = {u["id"]: u["wrote"] for u in real["undo"]}
        self.assertEqual(wrote["id-c"], ["Keywords"])
        self.assertEqual(wrote["id-a"], ["Keywords", "Description"])

    def test_the_undo_record_holds_before_values_and_is_journalled_before_the_first_write(self):
        seen = []
        orig = MetaClip.SetMetadata

        def spy(clip, key, value=None):
            if not seen:
                seen.append(self.events())
            return orig(clip, key, value)

        dry = self.run_tool()
        self.assertEqual([u["id"] for u in dry["undo"]], ["id-a", "id-b", "id-c"])
        with mock.patch.object(MetaClip, "SetMetadata", spy):
            real = self.run_tool(dry_run=False, plan_sha=dry["plan_sha"])
        before = {u["id"]: u for u in real["undo"]}
        self.assertEqual(before["id-a"]["keywords_before"], "Beach, Sunset")
        self.assertEqual(before["id-b"]["description_before"], "Already described")
        pre = seen[0]
        self.assertEqual([e["event"] for e in pre], ["started", "undo"])
        self.assertEqual({u["id"] for u in pre[1]["detail"]}, {"id-a", "id-b", "id-c"})
        self.assertIn("undo", self.events()[-1]["detail"])

    def test_a_kept_description_is_reported_even_when_keywords_were_written(self):
        _, real = self.real()
        self.assertEqual(real["exit_status"], 2)
        self.assertIn("another description", real["summary"])

    def test_a_whitespace_only_description_is_not_overwritten(self):
        self.b.meta["Description"] = "   "
        self.write_plan(HEAD + "id-b\t\tNew\n")
        dry = self.run_tool()
        self.assertEqual(dry["results"][0]["result"], "unchanged")

    def test_a_plan_with_a_problem_never_reaches_resolve(self):
        self.write_plan(HEAD + "id-a\tx\tone\ttwo\n")
        with self.assertRaisesRegex(ValueError, "4 cells"):
            self.run_tool()
        self.assertEqual(rf.CALLS, [])

    def test_an_empty_plan_is_refused(self):
        self.write_plan(HEAD)
        with self.assertRaisesRegex(ValueError, "no rows"):
            self.run_tool()

    def test_a_relative_plan_path_is_refused(self):
        with self.assertRaises(Exception):
            self.run_tool(plan="plan.tsv")


if __name__ == "__main__":
    unittest.main()
