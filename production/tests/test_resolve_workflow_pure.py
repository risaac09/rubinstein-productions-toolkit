"""
Offline unit tests for resolve_workflow.py's pure-logic functions — the
pieces that don't touch DaVinciResolveScript, so they run without Resolve
installed or running. Everything Resolve-shaped (connection, media pool,
timeline calls) is deliberately excluded from this file; test it by hand
against a live instance instead.

stdlib unittest only — no pytest/dependency to install for a public kit.

Run: python3 production/tests/test_resolve_workflow_pure.py
"""

import io
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
    DEFAULT_CONFIG,
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


if __name__ == "__main__":
    unittest.main()
