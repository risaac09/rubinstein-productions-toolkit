"""
The consumers read the card record: squeeze.answer and unanswered, the draft farm's proxy
lookup, `squeeze set` through the record, and the card_show, card_check, card_set and sheets
tools. Synthetic folders only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import card, cmd_squeeze, drafts, squeeze  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402
from test_card import make_card, run_cmd  # noqa: E402

FILES = {"DCIM/100/AAA0001.MOV": b"one", "DCIM/100/Proxy/AAA0001.mp4": b"px", "DCIM/100/AAA0002.MOV": b"two"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cardcons-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = str(make_card(self.tmp, files=FILES, schema2=True))
        self.a1 = os.path.join(self.root, "DCIM/100/AAA0001.MOV")
        self.px = os.path.join(self.root, "DCIM/100/Proxy/AAA0001.mp4")
        self.a2 = os.path.join(self.root, "DCIM/100/AAA0002.MOV")

    def answer(self, **kw):
        rec_path = self.root + card.RECORD
        rec = json.loads(Path(rec_path).read_text())
        rec["answers"] = {"squeeze": {"default": None, "clips": {}, "by": "person", "at": None, **kw}}
        Path(rec_path).write_text(json.dumps(rec))


class SqueezeReadsTheRecord(Base):
    def test_clip_id_default_and_proxy(self):
        self.answer(clips={"CAMA-012345/AAA0001": 1.5})
        self.assertEqual(squeeze.answer(self.a1), (1.5, "record"))
        self.assertEqual(squeeze.answer(self.px), (1.5, "record"))  # a proxy answers as its original
        self.assertEqual(squeeze.answer(self.a2), (None, None))
        self.answer(default=1, clips={"CAMA-012345/AAA0001": 1.5})
        self.assertEqual(squeeze.answer(self.a2), (1.0, "record"))

    def test_the_proxy_is_never_unanswered_but_its_original_is(self):
        self.assertEqual([squeeze.unanswered(p) for p in (self.a1, self.px, self.a2)], [True, False, True])
        self.answer(clips={"CAMA-012345/AAA0001": 1.5})
        self.assertEqual([squeeze.unanswered(p) for p in (self.a1, self.px, self.a2)], [False, False, True])

    def test_a_legacy_file_is_folded_in_for_a_schema_1_card(self):
        shutil.rmtree(self.tmp); os.mkdir(self.tmp)
        root = str(make_card(self.tmp, files=FILES))
        Path(root + ".squeeze.json").write_text(json.dumps({"default": None, "clips": {"AAA0001.MOV": 2}}))
        self.assertEqual(squeeze.answer(os.path.join(root, "DCIM/100/AAA0001.MOV")), (2.0, "record"))
        self.assertFalse(squeeze.unanswered(os.path.join(root, "DCIM/100/AAA0001.MOV")))

    def test_a_bad_factor_in_the_record_is_loud(self):
        self.answer(default=9)
        with self.assertRaises(squeeze.SqueezeError):
            squeeze.answer(self.a1)

    def test_the_record_is_reread_when_it_changes(self):
        self.assertEqual(squeeze.answer(self.a1), (None, None))
        self.answer(default=1.5)
        os.utime(self.root + card.RECORD, (1, 2))  # a changed mtime is the cache key
        self.assertEqual(squeeze.answer(self.a1), (1.5, "record"))

    def test_loose_footage_is_not_asked(self):
        loose = os.path.join(self.tmp, "loose.MOV")
        Path(loose).write_bytes(b"x")
        self.assertEqual((squeeze.answer(loose), squeeze.unanswered(loose)), ((None, None), False))


class DraftProxy(Base):
    def test_the_record_names_the_proxy(self):
        rec = json.loads(Path(self.root + card.RECORD).read_text())
        rec["clips"][0]["relpath"] = "DCIM/100/AAA0001.braw"  # a BRAW original with its proxy named
        Path(self.root + card.RECORD).write_text(json.dumps(rec))
        braw = os.path.join(self.root, "DCIM/100/AAA0001.braw")
        Path(braw).write_bytes(b"b")
        self.assertEqual(drafts.proxy_for(braw), self.px)
        os.remove(self.px)  # a proxy that is gone is not named, and none sits beside it
        self.assertIsNone(drafts.proxy_for(braw))

    def test_without_a_named_proxy_the_folder_search_still_works(self):
        braw = os.path.join(self.root, "DCIM/100/AAA0002.braw")
        Path(braw).write_bytes(b"b")
        Path(os.path.join(self.root, "Proxy")).mkdir()
        want = os.path.join(self.root, "Proxy", "AAA0002.mov")
        Path(want).write_bytes(b"p")
        self.assertEqual(drafts.proxy_for(braw), want)


class SqueezeSet(Base):
    def run_squeeze(self, *argv):
        import argparse, contextlib, io
        p = argparse.ArgumentParser()
        cmd_squeeze.add_arguments(p)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cmd_squeeze, "_exif_for", lambda files: {f: {} for f in files}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cmd_squeeze.run(p.parse_args(list(argv)))
        return rc, out.getvalue(), err.getvalue()

    def test_a_card_with_a_record_is_a_dry_run_until_apply(self):
        before = Path(self.root + card.RECORD).read_text()
        rc, out, _ = self.run_squeeze("set", self.root, "--factor", "1.5", "--clip", "AAA0001.MOV")
        self.assertEqual((rc, Path(self.root + card.RECORD).read_text()), (0, before))
        self.assertIn("dry run", out)
        self.assertEqual(self.run_squeeze("set", self.root, "--factor", "1.5", "--clip", "AAA0001.MOV", "--apply")[0], 0)
        self.assertFalse(os.path.exists(self.root + ".squeeze.json"))  # the record is the one place
        self.assertEqual(squeeze.answer(self.a1), (1.5, "record"))
        self.assertEqual(card.load(self.root)["problems"], [])
        rc, out, _ = self.run_squeeze("show", self.root)
        self.assertEqual(rc, 2)  # AAA0002 is still open; the proxy is not listed
        self.assertIn("1 unanswered", out + _)

    def test_a_folder_with_no_record_writes_squeeze_json_as_before(self):
        shutil.rmtree(self.tmp); os.mkdir(self.tmp)
        root = str(make_card(self.tmp, files=FILES, record=False))
        self.assertEqual(self.run_squeeze("set", root, "--factor", "1")[0], 0)
        self.assertEqual(json.loads(Path(root + ".squeeze.json").read_text())["default"], 1)


class Tools(Base):
    def call(self, name, args, env=None):
        tool = server.build_registry().get(name)
        errors = schema.validate(tool.input_schema, args)
        if errors:
            raise AssertionError(errors)
        with mock.patch.dict(os.environ, env or {}):
            return tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())

    def test_the_tools_are_registered_and_none_has_a_project(self):
        reg = server.build_registry()
        for n in ("card_show", "card_check", "card_set", "sheets"):
            self.assertNotIn("project", reg.get(n).input_schema["properties"])
        self.assertTrue(reg.get("card_show").annotations["readOnlyHint"])
        self.assertFalse(reg.get("card_set").annotations["readOnlyHint"])

    def test_show_and_check(self):
        r = self.call("card_show", {"path": self.a1})
        self.assertEqual((r["version"], r["name"], len(r["clips"])), (2, "CAMA-012345", 2))
        self.assertIn("squeeze CAMA-012345/AAA0001", r["open"])
        r = self.call("card_check", {"path": self.tmp})
        self.assertTrue(r["quick"])
        self.assertEqual((r["total"], r["rows"][0]["pass"], sorted(r["rows"][0]["failing"])), (1, False, ["S3", "S4"]))

    def test_set_plans_then_writes_with_the_plan_sha_and_journals(self):
        journal = os.path.join(self.tmp, "writes.jsonl")
        env = {"RPRESOLVE_MCP_JOURNAL": journal}
        base = {"path": self.root, "squeeze": 1.5, "clips": ["AAA0001.MOV"], "project_slug": "demo"}
        before = Path(self.root + card.RECORD).read_text()
        dry = self.call("card_set", base, env)
        self.assertIn("would replace", dry["summary"])
        self.assertEqual(Path(self.root + card.RECORD).read_text(), before)
        self.assertFalse(os.path.exists(journal))
        with self.assertRaisesRegex(Exception, "plan_sha"):
            self.call("card_set", {**base, "dry_run": False}, env)
        with self.assertRaisesRegex(Exception, "plan has changed"):
            self.call("card_set", {**base, "dry_run": False, "plan_sha": "0" * 64}, env)
        with mock.patch.object(card, "now", lambda: "2031-01-01T00:00:00Z"):  # a later second than the plan
            r = self.call("card_set", {**base, "dry_run": False, "plan_sha": dry["plan_sha"]}, env)
        self.assertIn("read back and matching", r["summary"])
        c = card.load(self.root)
        self.assertEqual((c["record"]["project"], c["answers"]["squeeze"]["clips"]), ("demo", {"CAMA-012345/AAA0001": 1.5}))
        events = [json.loads(line) for line in Path(journal).read_text().splitlines()]
        self.assertEqual([(e["event"], e["tool"]) for e in events], [("started", "card_set"), ("finished", "card_set")])
        self.assertEqual(events[1]["status"], "ok")
        # the same plan again is refused: the record has changed under it
        with self.assertRaisesRegex(Exception, "plan has changed"):
            self.call("card_set", {**base, "dry_run": False, "plan_sha": dry["plan_sha"]}, env)

    def test_set_refuses_a_stranger_clip_and_a_git_tree(self):
        with self.assertRaisesRegex(Exception, "no such clip"):
            self.call("card_set", {"path": self.root, "squeeze": 1.5, "clips": ["NOPE.MOV"]})
        git = Path(self.tmp, "repo")
        (git / ".git").mkdir(parents=True)
        inside = str(make_card(git, files=FILES, schema2=True))
        with self.assertRaisesRegex(Exception, "git working tree"):
            self.call("card_set", {"path": inside, "squeeze": 1})

    def test_sheets_refuse_a_volume_and_a_git_tree(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow missing")
        with self.assertRaisesRegex(Exception, "mounted volume"):
            self.call("sheets", {"folder": self.root, "out": "/Volumes/Nope/out"})
        git = Path(self.tmp, "repo2")
        (git / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(Exception, "git working tree"):
            self.call("sheets", {"folder": self.root, "out": str(git / "out")})


if __name__ == "__main__":
    unittest.main()
