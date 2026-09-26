"""
Offline unit tests for resolve_workflow.py's pure-logic functions — the
pieces that don't touch DaVinciResolveScript, so they run without Resolve
installed or running. Everything Resolve-shaped (connection, media pool,
timeline calls) is deliberately excluded from this file; test it by hand
against a live instance instead.

stdlib unittest only — no pytest/dependency to install for a public kit.

Run: python3 production/tests/test_resolve_workflow_pure.py
"""

import argparse
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve_workflow import (
    pick_codec,
    collect_media_files,
    duration_to_frames,
    parse_framerate,
    safe_fps,
    load_config,
    build_survey_command,
    DEFAULT_CONFIG,
    SURVEY_PYTHON,
    SURVEY_SCRIPT,
)


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


class TestCollectMediaFiles(unittest.TestCase):
    def test_recursive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "DCIM" / "100_PANA").mkdir(parents=True)
            (root / "DCIM" / "100_PANA" / "clip1.mov").write_bytes(b"x")
            (root / "DCIM" / "100_PANA" / "notes.txt").write_bytes(b"x")
            (root / "top.mp4").write_bytes(b"x")

            files, skipped = collect_media_files([str(root)])
            names = sorted(Path(f).name for f in files)

            self.assertEqual(names, ["clip1.mov", "top.mp4"])
            self.assertEqual(skipped, [])

    def test_reports_missing_paths(self):
        files, skipped = collect_media_files(["/definitely/not/a/real/path.mov"])
        self.assertEqual(files, [])
        self.assertEqual(skipped, ["/definitely/not/a/real/path.mov"])

    def test_single_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "clip.mov"
            f.write_bytes(b"x")
            files, skipped = collect_media_files([str(f)])
            # collect_media_files resolves paths (e.g. macOS /tmp -> /private/tmp),
            # so compare against the same resolution rather than the raw string.
            self.assertEqual(files, [str(f.resolve())])


class TestDurationAndFramerate(unittest.TestCase):
    def test_duration_to_frames(self):
        self.assertEqual(duration_to_frames(4.0, 24), 96)
        self.assertEqual(duration_to_frames(4.0, 23.976), 96)
        self.assertEqual(duration_to_frames(1.5, 30), 45)

    def test_parse_framerate_known(self):
        value, known = parse_framerate("23.976")
        self.assertEqual(value, "23.976")
        self.assertTrue(known)

    def test_parse_framerate_unknown_still_passes_through(self):
        value, known = parse_framerate("24fps")
        self.assertEqual(value, "24fps")
        self.assertFalse(known)

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


if __name__ == "__main__":
    unittest.main()
