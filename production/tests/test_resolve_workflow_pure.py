"""
Offline unit tests for resolve_workflow.py: its pure-logic functions (the
pieces that don't touch DaVinciResolveScript, so they run without Resolve
installed or running), the codec and config code it shares with the
library, and what is left of the script's surface once its write commands
are retired: the parser holds only the read-only and offline commands, and
each retired command, and trim-review --markers with its flags, exits 2 without
connecting to Resolve, naming the MCP tool that replaced it or, for the
commands that have no tool, saying so and what to do by hand.

stdlib unittest only — no pytest/dependency to install for a public kit.

Run: python3 production/tests/test_resolve_workflow_pure.py
"""

import argparse
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import resolve_workflow
from resolve_workflow import (
    safe_fps,
    build_survey_command,
    SURVEY_PYTHON,
    SURVEY_SCRIPT,
)
from rpresolve import config as rpconfig
from rpresolve.config import DEFAULT_CONFIG
from rpresolve.mcp import server
from rpresolve.render import pick_codec


def load_config(path):
    """resolve-config.json merged over the defaults; a malformed file prints a
    warning and degrades to the defaults (rpresolve.config)."""
    return rpconfig.load_config(path, warn=print)


class TestPickCodec(unittest.TestCase):
    def test_exact_match(self):
        codecs = {"Apple ProRes 422 HQ": "ProRes422HQ", "H.264": "H264"}
        desc, name, matched = pick_codec("H264", ["AVC"], codecs)
        self.assertEqual(name, "H264")
        self.assertEqual(desc, "H.264")
        self.assertTrue(matched)

    def test_falls_back_to_alternate_name(self):
        codecs = {"HEVC": "HEVC", "H.264": "H264"}
        desc, name, matched = pick_codec("H265", ["HEVC", "H.265"], codecs)
        self.assertEqual(name, "HEVC")
        self.assertTrue(matched)

    def test_last_resort_when_nothing_matches(self):
        codecs = {"Apple ProRes 422 HQ": "ProRes422HQ"}
        desc, name, matched = pick_codec("H264", ["AVC"], codecs)
        self.assertEqual(name, "ProRes422HQ")
        self.assertFalse(matched)

    def test_empty_dict(self):
        desc, name, matched = pick_codec("H264", [], {})
        self.assertIsNone(name)
        self.assertFalse(matched)

    def test_never_matches_against_description_side(self):
        # Regression for the original bug: matching "H264" against a dict
        # whose KEYS are human descriptions must not accidentally succeed
        # just because a description string happens to equal what we want.
        codecs = {"H264": "SomeVendorInternalName"}
        desc, name, matched = pick_codec("H264", [], codecs)
        self.assertEqual(name, "SomeVendorInternalName")
        self.assertFalse(matched)


class TestSafeFps(unittest.TestCase):
    def test_safe_fps_valid(self):
        self.assertEqual(safe_fps("23.976"), 23.976)

    def test_safe_fps_invalid_falls_back(self):
        self.assertEqual(safe_fps("not-a-number", default=24.0), 24.0)
        self.assertEqual(safe_fps(None, default=30.0), 30.0)


class TestLoadConfig(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        config = load_config("/definitely/not/a/config.json")
        self.assertEqual(config["cameras"]["iphone"]["clip_color"], "Blue")
        self.assertEqual(config, DEFAULT_CONFIG)

    def test_merges_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "resolve-config.json"
            p.write_text('{"default_framerate": "29.97"}')
            config = load_config(str(p))
            self.assertEqual(config["default_framerate"], "29.97")
            # untouched keys still fall back to defaults
            self.assertEqual(config["cameras"]["gh7"]["clip_color"], "Orange")

    def test_malformed_file_warns_and_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "resolve-config.json"
            p.write_text("{not valid json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                config = load_config(str(p))
            self.assertEqual(config, DEFAULT_CONFIG)
            self.assertIn("WARNING", buf.getvalue())


class TestSurveyCommand(unittest.TestCase):
    """survey hands off to resolve_survey.py under Python 3.14; only the
    hand-off is tested here (the survey itself: test_resolve_survey.py)."""

    def ns(self, **kw):
        base = dict(out="/elsewhere/survey.md", json=None, projects=None,
                    projects_dir=None, metadata_cache=None, no_metadata_cache=False,
                    tree_labels=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_minimal_command(self):
        self.assertEqual(build_survey_command(self.ns(), python="py", script="s.py"),
                         ["py", "s.py", "--out", "/elsewhere/survey.md"])

    def test_all_options_pass_through(self):
        cmd = build_survey_command(
            self.ns(json="/elsewhere/s.json", projects=["Two Words", "B"],
                    projects_dir="/lib/Projects", metadata_cache="/lib/Metadata.db",
                    no_metadata_cache=True, tree_labels=["CST IN", "CST OUT"]),
            python="py", script="s.py")
        self.assertEqual(cmd, ["py", "s.py", "--out", "/elsewhere/survey.md",
                               "--json", "/elsewhere/s.json", "--projects", "Two Words", "B",
                               "--projects-dir", "/lib/Projects",
                               "--metadata-cache", "/lib/Metadata.db", "--no-metadata-cache",
                               "--tree-labels", "CST IN", "CST OUT"])

    def test_defaults_point_at_sibling_script(self):
        self.assertEqual(SURVEY_SCRIPT.name, "resolve_survey.py")
        self.assertEqual(SURVEY_SCRIPT.parent, Path(__file__).resolve().parent.parent)

    @unittest.skipUnless(os.path.exists(SURVEY_PYTHON), "survey interpreter not installed")
    def test_survey_refuses_output_in_repo_end_to_end(self):
        script = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
        if not any((d / ".git").exists() for d in script.parents):
            self.skipTest("not running from a git checkout; the in-repo refusal cannot apply")
        target = script.parent / "tests" / "should-not-exist.md"
        with tempfile.TemporaryDirectory() as empty:
            # An empty projects folder: even if the refusal failed, no real
            # Resolve data could be read or written into the repo.
            proc = subprocess.run([sys.executable, str(script), "survey", "--out", str(target),
                                   "--projects-dir", empty, "--no-metadata-cache"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b"inside this repository", proc.stderr)
        self.assertFalse(target.exists())

    def test_survey_refusal_needs_no_zstd(self):
        # Run resolve_survey.py under this interpreter. Under the system 3.9
        # it has no compression.zstd, which is the case where SURVEY_PYTHON
        # lacks it: the in-repo refusal must still be the answer.
        script = SURVEY_SCRIPT
        if not any((d / ".git").exists() for d in script.parents):
            self.skipTest("not running from a git checkout; the in-repo refusal cannot apply")
        target = script.parent / "tests" / "should-not-exist.md"
        with tempfile.TemporaryDirectory() as empty:
            proc = subprocess.run([sys.executable, str(script), "--out", str(target),
                                   "--projects-dir", empty, "--no-metadata-cache"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b"inside this repository", proc.stderr)
        self.assertFalse(target.exists())


KEPT = ["list-projects", "list-timelines", "list-render-formats", "info", "survey", "detect",
        "measure", "manifest", "endcheck", "selects", "reframe-plan", "deliver-check",
        "deliver-captions", "deliver-fix-loudness", "sync-measure", "trim-review"]
RETIRED_COMMANDS = ["new-project", "import-media", "build-timeline", "add-subtitles",
                    "auto-subtitle", "render", "render-all", "clear-queue", "apply-lut",
                    "apply-drx", "open-page", "export-project", "ingest", "cut", "deliver-queue",
                    "captions", "sync"]


def run_main(*argv):
    """main() with this argv; (exit status, stdout, stderr). Resolve is not
    reachable: a call to get_resolve fails the test."""
    out, err = io.StringIO(), io.StringIO()
    connect = mock.Mock(side_effect=AssertionError("connected to Resolve"))
    # main() leaves through os._exit; here it raises SystemExit so the runner survives.
    leave = mock.Mock(side_effect=lambda code=0: sys.exit(code))
    status = None
    with mock.patch.object(resolve_workflow, "get_resolve", connect), \
            mock.patch.object(resolve_workflow.rpapi, "exit_clean", leave), \
            mock.patch.object(sys, "argv", ["resolve_workflow.py", *argv]), \
            redirect_stdout(out), redirect_stderr(err):
        try:
            resolve_workflow.main()
        except SystemExit as e:
            status = e.code
    return status, out.getvalue(), err.getvalue()


class TestOnlyReadAndOfflineCommandsRemain(unittest.TestCase):
    def parser_commands(self):
        status, _, err = run_main("not-a-command")
        self.assertEqual(status, 2)
        return err

    def test_the_parser_holds_exactly_the_kept_commands(self):
        err = self.parser_commands()
        listed = err.split("(choose from ")[1].rstrip(")\n").replace("'", "").split(", ")
        self.assertEqual(listed, KEPT)

    def test_every_retired_command_exits_2_and_names_its_replacement(self):
        self.assertEqual(sorted(resolve_workflow.RETIRED), sorted(RETIRED_COMMANDS))
        for name in RETIRED_COMMANDS:
            with self.subTest(command=name):
                status, out, err = run_main(name)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
                self.assertIn(f"'{name}' is retired", err)
                self.assertIn(resolve_workflow.RETIRED[name], err)
                self.assertNotIn("only way to write to Resolve", err)
                self.assertNotIn("Traceback", err)

    def test_a_command_with_no_tool_says_so_and_the_others_name_their_tool(self):
        self.assertEqual(sorted(resolve_workflow.BY_HAND),
                         ["build-timeline", "clear-queue", "export-project", "new-project",
                          "open-page"])
        tools = {t.name for t in server.build_registry().list()}
        for name in RETIRED_COMMANDS:
            with self.subTest(command=name):
                _, _, err = run_main(name)
                text = resolve_workflow.RETIRED[name]
                if name in resolve_workflow.BY_HAND:
                    self.assertIn(f"'{name}' is retired and has no MCP tool; do it by hand "
                                  f"in Resolve: {text}", err)
                    self.assertNotIn("use the MCP", text)
                else:
                    self.assertNotIn("no MCP tool", err)
                    named = re.findall(r"MCP (\w+) tool", text)
                    self.assertTrue(named, text)
                    for tool in named:
                        self.assertIn(tool, tools)

    def test_a_retired_command_with_its_old_arguments_still_stops_at_the_parser(self):
        for argv in (["new-project", "Example"], ["render-all", "--output", "/x", "--start"],
                     ["ingest", "/card", "--project", "P", "--dry-run"],
                     ["--config", "/x.json", "cut", "m.json", "--project", "P"]):
            with self.subTest(argv=argv):
                status, _, err = run_main(*argv)
                self.assertEqual(status, 2)
                self.assertIn("is retired", err)

    def test_trim_review_markers_is_retired_and_the_tsv_flags_are_not(self):
        status, _, err = run_main("trim-review", "m.json", "--markers", "--timeline", "T",
                                  "--project", "P")
        self.assertEqual(status, 2)
        self.assertIn("trim-review no longer takes", err)
        self.assertIn("trim_review_markers", err)
        self.assertNotIn("only way to write to Resolve", err)
        status, out, _ = run_main("trim-review", "--help")
        self.assertEqual(status, 0)
        for flag in ("--words", "--out", "--silence-db", "--no-audio"):
            self.assertIn(flag, out)
        for flag in ("--markers", "--timeline", "--project", "--dry-run", "--plan-sha"):
            self.assertNotIn(flag, out)

    def test_each_removed_trim_review_flag_alone_gets_the_pointer(self):
        for flag, value in (("--markers", None), ("--timeline", "T"), ("--project", "P"),
                            ("--project-id", "id"), ("--dry-run", None), ("--plan-sha", "abc")):
            for argv in (["trim-review", "m.json", flag] + ([value] if value else []),
                         ["trim-review", "m.json", f"{flag}=x"]):
                with self.subTest(argv=argv):
                    status, out, err = run_main(*argv)
                    self.assertEqual(status, 2)
                    self.assertEqual(out, "")
                    self.assertIn("trim-review no longer takes", err)
                    self.assertIn("trim_review_markers", err)

    def test_the_trim_review_pointer_fires_for_no_other_command_or_flag(self):
        for argv in (["detect", "/x", "--markers"], ["info", "--markers"],
                     ["info", "--project", "P"], ["list-timelines", "--dry-run"],
                     ["measure", "r.mov", "--timeline", "T"],
                     ["trim-review", "m.json", "--bogus"]):
            with self.subTest(argv=argv):
                status, out, err = run_main(*argv)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
                self.assertIn("unrecognized arguments", err)
                self.assertNotIn("no longer takes", err)
                self.assertNotIn("trim_review_markers", err)

    def test_each_kept_command_still_has_a_help_page_without_resolve(self):
        for name in KEPT:
            with self.subTest(command=name):
                status, out, _ = run_main(name, "--help")
                self.assertEqual(status, 0)
                self.assertIn(f"resolve_workflow {name}", out)

    def test_the_help_says_where_writes_go(self):
        status, out, _ = run_main("--help")
        self.assertEqual(status, 0)
        self.assertIn("does not write to Resolve", out)
        self.assertIn("production/resolve_mcp.py", out)
        for name in RETIRED_COMMANDS:
            self.assertNotIn(f"resolve_workflow.py {name} ", out)


if __name__ == "__main__":
    unittest.main()
