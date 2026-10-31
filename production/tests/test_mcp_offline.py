"""
Tests for the MCP offline tools (rpresolve.mcp.tools_offline): detect,
survey, measure, endcheck and selects (and shoot_table, qc, pipeline_proof,
deliver_manifest, perceive, audio_report and overlay_check), called as the protocol calls them
and once through the server as a subprocess. Synthetic words, text and
media only; no Resolve. measure's clip test needs numpy and ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import cutlist, paths, workflows  # noqa: E402
from rpresolve.mcp import schema, server, tools_offline  # noqa: E402
from rpresolve.mcp.registry import Cancelled, ToolContext  # noqa: E402
from test_cutlist import APPROVED, SPEECH, words_from  # noqa: E402

SERVER = HERE.parent / "resolve_mcp.py"
REG = server.build_registry()
# A second sentence inside the approved text, after the one SPEECH ends on.
MORE = [("I", 7.0, 7.1), ("kept", 7.1, 7.4), ("that", 7.4, 7.6), ("very", 7.6, 7.8),
        ("quiet.", 7.8, 8.3)]


def call(name, args, ctx=None):
    """Validate and default args as the protocol does, then run the tool."""
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, args)
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    return tool.handler(schema.with_defaults(tool.input_schema, args), ctx or ToolContext())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.outdir = os.path.join(self.dir, "cache")
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_OUT": self.outdir})
        env.start()
        self.addCleanup(env.stop)
        self.words = os.path.join(self.dir, "w.json")
        with open(self.words, "w") as f:
            json.dump(words_from(SPEECH + MORE), f)
        self.approved = os.path.join(self.dir, "essay.md")
        with open(self.approved, "w") as f:
            f.write(APPROVED)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self):
        spans = {"quiet": [(3.55, 4.95)], "map": [(0.95, 2.75)]}
        m = cutlist.build_manifest(spans, os.path.join(self.dir, "src.mov"), 25,
                                   self.words, self.approved, source_sha256="0" * 64)
        p = os.path.join(self.dir, "m.json")
        with open(p, "w") as f:
            json.dump(m, f)
        return p


class TestRegistry(unittest.TestCase):
    def test_offline_tools_registered_and_read_only(self):
        for name in ("detect", "survey", "measure", "endcheck", "selects"):
            t = REG.get(name)
            self.assertIsNotNone(t, name)
            self.assertTrue(t.annotations["readOnlyHint"], name)
            self.assertEqual(schema.validate({"type": "object"}, t.input_schema), [])


class TestPaths(Base):
    def test_relative_and_missing_paths_are_refused(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            call("selects", {"words": "w.json", "approved": self.approved})
        with self.assertRaisesRegex(ValueError, "does not exist"):
            call("selects", {"words": os.path.join(self.dir, "nope.json"),
                             "approved": self.approved})

    def test_out_inside_any_git_tree_is_refused(self):
        repo = os.path.join(self.dir, "other-repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaises(paths.OutputRefused):
            call("selects", {"words": self.words, "approved": self.approved,
                             "out": os.path.join(repo, "sub", "s.tsv")})
        with self.assertRaises(paths.OutputRefused):
            call("selects", {"words": self.words, "approved": self.approved,
                             "out": str(HERE / "s.tsv")})
        self.assertFalse(os.path.exists(os.path.join(repo, "sub")))


class TestSelects(Base):
    def test_matches_the_library_and_pages_to_a_file(self):
        expect = cutlist.selects(cutlist.load_words(self.words),
                                 cutlist.load_approved(self.approved), 1.0, 2.0)
        self.assertGreater(len(expect), 1)
        r = call("selects", {"words": self.words, "approved": self.approved,
                             "min_seconds": 1, "max_seconds": 2, "limit": 1})
        self.assertEqual(r["total"], len(expect))
        self.assertEqual(r["rows"], expect[:1])
        self.assertEqual(r["next_offset"], 1)
        self.assertTrue(r["file"].startswith(self.outdir))
        with open(r["file"]) as f:
            self.assertEqual(f.read(), cutlist.selects_tsv(expect))
        rest = call("selects", {"words": self.words, "approved": self.approved,
                                "min_seconds": 1, "max_seconds": 2, "offset": 1, "limit": 50})
        self.assertEqual(rest["rows"], expect[1:])
        self.assertIsNone(rest["next_offset"])
        self.assertNotIn("file", rest)

    def test_bounds(self):
        with self.assertRaisesRegex(ValueError, "less than"):
            call("selects", {"words": self.words, "approved": self.approved,
                             "min_seconds": 10, "max_seconds": 10})

    def test_tsv_cells_have_no_tabs_or_newlines(self):
        text = cutlist.selects_tsv([{"in": 1, "out": 2, "seconds": 1, "coverage": 1,
                                     "end_words": "a\tb", "text": "x\ny"}])
        self.assertEqual(text.splitlines()[1].split("\t")[-2:], ["a b", "x y"])


class TestEndcheck(Base):
    def test_rows_lines_and_clip_filter(self):
        m = self.manifest()
        seen = []
        ctx = ToolContext(send_progress=lambda d, t, msg: seen.append((d, t)))
        r = call("endcheck", {"manifest": m, "audio": False}, ctx)
        rows = cutlist.endcheck(cutlist.load_manifest(m), audio=False)
        self.assertEqual(r["rows"], json.loads(json.dumps(rows, default=str)))
        self.assertEqual(r["lines"], [cutlist.endcheck_line(x) for x in rows])
        self.assertEqual(r["counts"], cutlist.endcheck_counts(rows))
        self.assertIn(r["verdict"], ("pass", "review", "fail"))
        self.assertIn("audio not checked", r["summary"])
        self.assertEqual(seen[-1], (2, 2))
        page = call("endcheck", {"manifest": m, "audio": False, "limit": 1, "offset": 1})
        self.assertEqual(page["lines"], [cutlist.endcheck_line(rows[1])])
        self.assertEqual(page["counts"], r["counts"])
        one = call("endcheck", {"manifest": m, "audio": False, "clips": ["quiet"]})
        self.assertEqual([x["clip"] for x in one["rows"]], ["quiet"])
        with self.assertRaisesRegex(ValueError, "not in the manifest: nope"):
            call("endcheck", {"manifest": m, "clips": ["nope"]})

    def test_cancel_stops_before_the_first_span(self):
        ctx = ToolContext(is_cancelled=lambda: True)
        with self.assertRaises(Cancelled):
            call("endcheck", {"manifest": self.manifest(), "audio": False}, ctx)

    def test_cli_line_format_is_unchanged(self):
        row = {"clip": "twenty_30", "span": 2, "out": 2226.5, "ok": False, "review": False,
               "reason": "ends inside a word",
               "end": {"nearest": {"before": {"t": 2226.245, "ends": "very quiet."}}},
               "text": {"verdict": "review", "coverage": 0.7, "unmatched": "being"}}
        self.assertEqual(cutlist.endcheck_line(row),
                         'FAIL   twenty_30      span 2  out 2226.5    ends inside a word  | '
                         'clean before 2226.245 ends "very quiet."; essay review 0.70: [being]')


class TestDetect(Base):
    ROWS = [{"path": f"/m/c{i}.mov", "profile": "V-Log", "confidence": "high"} for i in range(5)]

    def fake(self, targets):
        self.targets = targets
        return {"rows": self.ROWS, "missing": ["/m/gone"], "summary": "detect: 5 file(s)",
                "counts": {"pinned": 5, "review": 0, "corrupt": 0}, "exit_status": 0}

    def test_pages_and_writes_all_rows(self):
        out = os.path.join(self.dir, "d.json")
        with mock.patch.object(workflows, "detect", self.fake):
            r = call("detect", {"paths": ["~/m"], "limit": 2, "out": out})
        self.assertEqual(self.targets, [os.path.expanduser("~/m")])
        self.assertEqual((r["total"], r["returned"], r["next_offset"]), (5, 2, 2))
        self.assertEqual(r["status"], "pinned")
        self.assertEqual(r["file"], out)
        with open(out) as f:
            self.assertEqual(len(json.load(f)), 5)

    def test_small_result_writes_nothing(self):
        with mock.patch.object(workflows, "detect", self.fake):
            r = call("detect", {"paths": ["/m"]})
        self.assertNotIn("file", r)
        self.assertFalse(os.path.exists(self.outdir) and os.listdir(self.outdir))


class TestSurvey(Base):
    def test_paths_and_arguments(self):
        seen = {}

        def fake(out, json_out=None, **kw):
            seen.update(out=out, json_out=json_out, **kw)
            return {"returncode": 0, "out": out, "json_out": json_out,
                    "stdout_tail": "", "stderr_tail": ""}
        out = os.path.join(self.dir, "s.md")
        with mock.patch.object(workflows, "survey", fake):
            r = call("survey", {"out": out, "projects": ["A"]})
        self.assertEqual(seen["json_out"], os.path.join(self.dir, "s.json"))
        self.assertEqual(seen["projects"], ["A"])
        self.assertTrue(seen["capture"])
        self.assertEqual(seen["timeout"], 1800)
        self.assertTrue(r["ok"])
        with mock.patch.object(workflows, "survey", fake):
            r = call("survey", {"json": False})
        self.assertTrue(seen["out"].startswith(self.outdir))
        self.assertIsNone(seen["json_out"])
        with self.assertRaisesRegex(ValueError, "does not end in .json"):
            call("survey", {"out": os.path.join(self.dir, "s.json")})

    def test_free_survey_values_cannot_smuggle_an_option(self):
        for field in ("projects", "tree_labels"):
            for bad in (["A", "--out", "/elsewhere/x.md"], ["-x"], [""], []):
                tool = REG.get("survey")
                self.assertTrue(schema.validate(tool.input_schema, {field: bad}), (field, bad))
                kw = {field: bad}
                with self.assertRaises(workflows.Refused):
                    workflows.survey_command("/o/r.md", **kw)
        argv = workflows.survey_command("/o/r.md", projects=["A"], tree_labels=["L"])
        self.assertEqual(argv.count("--out"), 1)

    def test_empty_items_are_refused_by_schema_and_workflow(self):
        for name in ("apply_grade", "reframe_apply", "stabilize"):
            props = REG.get(name).input_schema["properties"]
            self.assertEqual(props["items"]["minItems"], 1, name)
            errs = schema.validate(REG.get(name).input_schema, {"items": []})
            self.assertTrue(any("items" in e and "fewer than 1" in e for e in errs), name)
        with self.assertRaises(workflows.Refused):
            workflows._wanted_items([], 3)
        self.assertEqual(workflows._wanted_items(None, 3), [1, 2, 3])
        self.assertEqual(workflows._wanted_items([2, 1, 2], 3), [1, 2])

    def test_failed_survey_leaves_no_empty_report(self):
        def failing(out, json_out=None, **kw):
            return {"returncode": 1, "out": out, "json_out": json_out,
                    "stdout_tail": "", "stderr_tail": "boom"}

        def refusing(out, json_out=None, **kw):
            raise workflows.Refused("no python3.14")
        for fake in (failing, refusing):
            try:
                with mock.patch.object(workflows, "survey", fake):
                    r = call("survey", {})
                self.assertFalse(r["ok"])
            except workflows.Refused:
                pass
            self.assertEqual(os.listdir(self.outdir), [])


try:
    import numpy  # noqa: F401
    from rpresolve import measure as rpmeasure
except ImportError:
    rpmeasure = None


@unittest.skipIf(rpmeasure is None, "numpy not installed")
class TestMeasure(Base):
    def test_hero_must_be_a_segment(self):
        with self.assertRaisesRegex(ValueError, "hero"):
            call("measure", {"file": __file__, "segments": ["a=0-1"], "hero": "b"})

    def test_segment_pattern(self):
        with self.assertRaises(AssertionError):
            call("measure", {"file": __file__, "segments": ["a:0-1"]})

    @unittest.skipUnless(rpmeasure and os.access(rpmeasure.FFMPEG, os.X_OK), "ffmpeg missing")
    def test_generated_clip(self):
        clip = os.path.join(self.dir, "grey.mov")
        subprocess.run([rpmeasure.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x808080:size=320x180:rate=24:duration=2",
                        "-pix_fmt", "yuv422p10le", "-c:v", "prores_ks", "-y", clip], check=True)
        seen = []
        ctx = ToolContext(send_progress=lambda d, t, msg: seen.append((d, t)))
        r = call("measure", {"file": clip, "samples": 2, "faces": False}, ctx)
        self.assertEqual(r["report"]["frames_sampled"], 2)
        self.assertEqual(len(r["report"]["frames"]), 2)
        self.assertTrue(r["file"].startswith(self.outdir))
        with open(r["file"]) as f:
            self.assertEqual(json.load(f)["frames_sampled"], 2)
        self.assertIn("luma", r["summary"].lower())
        self.assertEqual(seen, [(1, 2)])
        with self.assertRaises(Cancelled):
            call("measure", {"file": clip, "samples": 2, "faces": False},
                 ToolContext(is_cancelled=lambda: True))


SEVEN = {"shoot_table": {"paths": ["rel/a.mov"]}, "qc": {"file": "rel/a.mov"},
         "pipeline_proof": {"test": "rel/a.mov", "reference": "rel/b.mov"},
         "deliver_manifest": {"folder": "rel"},
         "perceive": {"file": "rel/a.mov", "out": "/abs/out"},
         "audio_report": {"file": "rel/a.mov", "out": "/abs/out"},
         "overlay_check": {"file": "rel/a.mov"}}


class TestSevenMore(Base):
    def test_registered_read_only_and_refuse_relative_paths(self):
        for name, args in SEVEN.items():
            t = REG.get(name)
            self.assertIsNotNone(t, name)
            self.assertTrue(t.annotations["readOnlyHint"], name)
            if name in ("pipeline_proof", "overlay_check", "perceive", "audio_report"):
                try:
                    import numpy  # noqa: F401
                except ImportError:
                    continue  # these raise the numpy message before the path check
            with self.assertRaisesRegex(ValueError, "absolute"):
                call(name, args)

    def test_shoot_table_wants_cam_equals_seconds(self):
        with self.assertRaisesRegex(ValueError, "CAM=SECONDS"):
            call("shoot_table", {"paths": [self.dir], "camera_offset": ["cam-a"]})

    def test_qc_fps_needs_a_destination(self):
        with self.assertRaisesRegex(ValueError, "destination"):
            call("qc", {"file": self.words, "fps": "25"})

    def test_deliver_manifest_verify_reads_and_writes_nothing(self):
        sub = os.path.join(self.dir, "deliv", "dest")
        os.makedirs(sub)
        with open(os.path.join(sub, "a.mp4"), "wb") as f:
            f.write(b"abc")
        sha = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        with open(os.path.join(self.dir, "deliv", "checksums.txt"), "w") as f:
            f.write(f"{sha}  dest/a.mp4\n")
        root = os.path.join(self.dir, "deliv")
        r = call("deliver_manifest", {"folder": root, "verify": True})
        self.assertTrue(r["ok"])
        with open(os.path.join(sub, "a.mp4"), "wb") as f:
            f.write(b"abd")
        self.assertFalse(call("deliver_manifest", {"folder": root, "verify": True})["ok"])
        self.assertFalse(os.path.exists(os.path.join(root, "manifest.md")))

    def test_deliver_manifest_preview_names_what_is_missing(self):
        with self.assertRaisesRegex(ValueError, "project, approval"):
            call("deliver_manifest", {"folder": self.dir})


class TestEndToEnd(Base):
    def test_selects_through_the_server(self):
        env = dict(os.environ, RPRESOLVE_MCP_NO_RESOLVE="1")
        p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "selects", "arguments": {
                     "words": self.words, "approved": self.approved,
                     "min_seconds": 1, "max_seconds": 5}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "selects", "arguments": {
                     "words": "w.json", "approved": self.approved}}}]
        out, err = p.communicate(b"".join(json.dumps(r).encode() + b"\n" for r in reqs), timeout=60)
        by_id = {m["id"]: m for m in (json.loads(l) for l in out.decode().splitlines())}
        self.assertEqual(p.returncode, 0, err.decode()[-500:])
        self.assertIn("selects", [t["name"] for t in by_id[2]["result"]["tools"]])
        ok = by_id[3]["result"]
        self.assertFalse(ok["isError"])
        self.assertGreater(ok["structuredContent"]["total"], 0)
        bad = by_id[4]["result"]
        self.assertTrue(bad["isError"])
        self.assertIn("absolute", bad["content"][0]["text"])


class TestDeliverCheckWarnSummary(Base):
    def test_summary_names_warn_count_and_description_says_warn_is_advice(self):
        def stub(warn):
            rows = [{"check": "fps_floor", "status": "WARN" if warn else "PASS"}]
            counts = {"PASS": 0 if warn else 1, "FAIL": 0, "SKIP": 0, "WARN": 1 if warn else 0}
            return {"checks": rows, "counts": counts, "status": "pass"}
        f = os.path.join(self.dir, "SW999_Test_a.mp4")
        open(f, "w").close()
        for warn in (True, False):
            with mock.patch("rpresolve.delivercheck.check", return_value=stub(warn)):
                r = call("deliver_check", {"file": f, "destination": "social_9x16",
                                           "loudness": False})
            self.assertEqual("1 warn" in r["summary"], warn, r["summary"])
            self.assertEqual("every asserted rule passes" in r["summary"], not warn, r["summary"])
        desc = REG.get("deliver_check").description
        self.assertIn("WARN", desc)
        self.assertIn("never changes the outcome", desc)


if __name__ == "__main__":
    unittest.main()
