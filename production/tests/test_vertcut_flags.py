"""
Tests for vertcut's loudness and preset flags. The defaults must stay what the
tool always wrote (-16 LUFS, -1.5 dBTP, LRA 11, crf 20, veryfast); the flags
exist so a destination that wants -14 LUFS is not a source edit.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUT = ROOT / "tools" / "vertcut" / "lib" / "vertcut_cut.py"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def load():
    spec = importlib.util.spec_from_file_location("vertcut_cut", CUT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
class TestFlags(unittest.TestCase):
    def setUp(self):
        self.v = load()

    def test_the_defaults_are_what_the_tool_always_wrote(self):
        self.assertEqual(self.v.loudnorm_filter(), "loudnorm=I=-16:TP=-1.5:LRA=11")
        self.assertEqual(self.v.video_codec_args(20, "veryfast"),
                         ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast"])

    def test_a_different_target(self):
        self.assertEqual(self.v.loudnorm_filter(-14, -1.0, 9), "loudnorm=I=-14:TP=-1:LRA=9")
        self.assertEqual(self.v.video_codec_args(18, "slow")[-2:], ["-preset", "slow"])

    def test_out_of_range_targets_are_refused(self):
        for args in ((5, -1.5, 11), (-40, -1.5, 11), (-16, 1.0, 11), (-16, -12.0, 11), (-16, -1.5, 0.5),
                     (-16, -1.5, 40)):
            with self.subTest(args=args):
                with self.assertRaises(SystemExit):
                    self.v.loudnorm_filter(*args)

    def test_the_house_name_comes_from_the_rule_in_rpresolve(self):
        dest = self.v.load_destination("linkedin_9x16")
        parts = {"show": "SW", "episode": "1", "guest": "Guest", "index": 3}
        self.assertEqual(self.v.clip_name(dest, parts, "An example clip!"),
                         "SW001_Guest_03_an-example-clip_9x16.mp4")
        self.assertEqual(self.v.clip_name(dest, parts, ""), "SW001_Guest_03_clip_9x16.mp4")

    def test_a_part_that_breaks_the_rule_stops_the_run(self):
        dest = self.v.load_destination("linkedin_9x16")
        with self.assertRaises(SystemExit):
            self.v.clip_name(dest, {"show": "sw", "episode": "1", "guest": "Guest", "index": 1}, "x")

    def test_a_long_title_still_gives_a_valid_slug(self):
        self.assertTrue(self.v.slug("word " * 30, "clip").islower())
        self.assertFalse(self.v.slug("a" * 59 + " b", "clip").endswith("-"))

    def test_the_audio_args_follow_the_destination(self):
        dest = self.v.load_destination("linkedin_9x16")
        self.assertEqual(self.v.audio_codec_args(dest),
                         ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2"])

    def test_a_destination_vertcut_cannot_write_is_refused(self):
        self.v.check_destination(self.v.load_destination("linkedin_9x16"))
        for key in ("linkedin_1x1", "youtube_16x9", "client_master"):
            with self.subTest(key=key):
                with self.assertRaises(SystemExit):
                    self.v.check_destination(self.v.load_destination(key))

    def test_the_colour_tags_are_explicit_bt709_limited_range(self):
        a = self.v.COLOR_TAG_ARGS
        for flag, val in (("-color_primaries", "bt709"), ("-color_trc", "bt709"),
                          ("-colorspace", "bt709"), ("-color_range", "tv")):
            self.assertEqual(a[a.index(flag) + 1], val)
        self.assertIn("color_trc=bt709", self.v.COLOR_TAG_FILTER)

    def test_a_burnin_destination_keeps_its_srt_out_from_beside_the_clip(self):
        dest = self.v.load_destination("linkedin_9x16")
        self.assertEqual(dest["captions"], "burnin")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "SW001_Guest_01_a-test_9x16.mp4")
        self.assertEqual(self.v.srt_path(out, dest),
                         os.path.join(tmp.name, "review-srt", "SW001_Guest_01_a-test_9x16.srt"))
        self.assertEqual(self.v.srt_path(out, dict(dest, captions="sidecar")),
                         os.path.join(tmp.name, "SW001_Guest_01_a-test_9x16.srt"))

    def test_the_presets_are_x264s(self):
        self.assertIn("veryfast", self.v.X264_PRESETS)
        self.assertEqual(len(self.v.X264_PRESETS), 9)


@unittest.skipUnless(HAVE_PIL and os.access(FFMPEG, os.X_OK), "Pillow or ffmpeg missing")
class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.src = os.path.join(d, "src.mp4")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", self.src], check=True)
        self.tsv = os.path.join(d, "clips.tsv")
        Path(self.tsv).write_text("1\t0.2\t1.5\tA test\n")
        self.out = os.path.join(d, "out")

    def tearDown(self):
        self.tmp.cleanup()

    NAME = ("--show", "SW", "--episode", "1", "--guest", "Guest")

    def run_cut(self, *extra):
        return subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions",
                               *self.NAME, *extra], capture_output=True, text=True)

    def test_a_cut_with_the_new_flags_encodes(self):
        p = self.run_cut("--lufs", "-14", "--preset", "ultrafast")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(os.listdir(self.out)), 1)

    def test_the_output_has_the_house_name_and_the_destinations_tags_and_audio(self):
        p = self.run_cut()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.listdir(self.out), ["SW001_Guest_01_a-test_9x16.mp4"])
        probe = subprocess.run(
            [shutil.which("ffprobe") or FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries",
             "stream=codec_name,color_primaries,color_transfer,color_space,color_range,sample_rate,channels",
             "-of", "default=nw=1", os.path.join(self.out, os.listdir(self.out)[0])],
            capture_output=True, text=True).stdout
        for line in ("color_primaries=bt709", "color_transfer=bt709", "color_space=bt709",
                     "color_range=tv", "sample_rate=48000", "channels=2"):
            self.assertIn(line, probe)

    def test_missing_name_parts_are_named(self):
        p = subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions",
                            "--show", "SW"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--episode, --guest", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_draft_names_keep_the_old_name(self):
        p = subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions",
                            "--draft-names"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.listdir(self.out), ["1-a-test.mp4"])

    def test_a_bad_target_stops_before_any_work(self):
        p = self.run_cut("--lufs", "5")
        self.assertEqual(p.returncode, 1)
        self.assertIn("--lufs 5 is outside -30 .. -5", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_a_bad_preset_is_an_argument_error(self):
        p = self.run_cut("--preset", "warp")
        self.assertEqual(p.returncode, 2)

    def test_an_output_folder_in_a_git_tree_is_refused(self):
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        self.out = os.path.join(repo, "vertcut-out")
        p = self.run_cut()
        self.assertEqual(p.returncode, 1)
        self.assertIn("inside a git work tree", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    # --- the batch review gate: over 30 minutes, or a replacement, needs a gate-packet answered go
    def long_tsv(self):
        path = os.path.join(self.tmp.name, "long.tsv")   # 100 minutes of spans, 100 / 3 = 33 min estimated
        Path(path).write_text("".join("%d\t%d\t%d\tpart %d\n" % (i, i * 600, i * 600 + 600, i) for i in range(10)))
        return path

    def make_gate(self, answer="go", sub="gate", runtime=6000.0, cuts=None):
        cuts_sha = hashlib.sha256(Path(cuts or self.long_tsv()).read_bytes()).hexdigest()
        src_sha = hashlib.sha256(Path(self.src).read_bytes()).hexdigest()
        d = os.path.join(self.tmp.name, sub)
        os.makedirs(d)
        packet = os.path.join(d, "packet.json")
        Path(packet).write_text('{"version": 1, "files": []}')
        sha = hashlib.sha256(Path(packet).read_bytes()).hexdigest()
        gate = os.path.join(d, "gate.json")
        Path(gate).write_text(json.dumps({"version": 1, "project": "P-0001", "packet_sha256": sha,
                                          "total_runtime_s": runtime,
                                          "inputs": [{"path": "src.mp4", "sha256": src_sha},
                                                     {"path": "cuts.tsv", "sha256": cuts_sha}]}))
        log = os.path.join(d, "events.jsonl")
        line = {"ts": "2026-10-31T10:00:00Z", "project": "P-0001", "step": "gate", "options": {},
                "choice": answer, "device": "desktop", "stimulus": sha}
        Path(log).write_text(json.dumps(line) + "\n" if answer else "")
        return gate, log

    def dry(self, tsv, *extra):
        cmd = [sys.executable, str(CUT), self.src, tsv, "-o", self.out, "--no-captions", *self.NAME, "--dry-run", *extra]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_a_long_batch_is_refused_with_the_gate_packet_command(self):
        p = self.dry(self.long_tsv())
        self.assertEqual(p.returncode, 1)
        self.assertIn("estimated 33 min is over the 30 min gate", p.stderr)
        self.assertIn("resolve_workflow.py gate-packet --out GATE_DIR --project P-0000 --source %s --cuts %s"
                      % (self.src, self.long_tsv()), p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_a_short_batch_prints_its_estimate_and_needs_no_gate(self):
        p = self.dry(self.tsv)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("estimated 0.0 min to cut", p.stdout)

    def test_a_go_outside_the_tree_passes_the_gate(self):
        gate, log = self.make_gate()
        p = self.dry(self.long_tsv(), "--reviewed", gate, "--review-log", log)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("estimated 33.3 min to cut", p.stdout)

    def test_a_gate_without_a_go_is_refused(self):
        for answer in (None, "hold"):
            with self.subTest(answer=answer):
                gate, log = self.make_gate(answer, sub="gate-%s" % answer)
                p = self.dry(self.long_tsv(), "--reviewed", gate, "--review-log", log)
                self.assertEqual(p.returncode, 1)
                self.assertIn("no go for this gate-packet", p.stderr)

    def test_a_gate_that_covers_less_than_the_batch_is_refused(self):
        gate, log = self.make_gate(runtime=600.0)
        p = self.dry(self.long_tsv(), "--reviewed", gate, "--review-log", log)
        self.assertEqual(p.returncode, 1)
        self.assertIn("covers 600 s of clips", p.stderr)

    def test_a_gate_built_from_other_cuts_is_refused(self):
        gate, log = self.make_gate(cuts=self.tsv)
        p = self.dry(self.long_tsv(), "--reviewed", gate, "--review-log", log)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not built from this cuts file", p.stderr)

    def test_a_gate_or_log_inside_a_repository_is_refused(self):
        gate, log = self.make_gate()
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        shutil.copy(os.path.join(os.path.dirname(gate), "packet.json"), repo)
        inside = (shutil.copy(gate, repo), shutil.copy(log, repo))
        for gate_arg, log_arg in ((inside[0], log), (gate, inside[1])):
            with self.subTest(gate=gate_arg, log=log_arg):
                p = self.dry(self.long_tsv(), "--reviewed", gate_arg, "--review-log", log_arg)
                self.assertEqual(p.returncode, 1)
                self.assertIn("inside a git work tree", p.stderr)

    def test_an_existing_output_name_triggers_the_gate(self):
        os.makedirs(self.out)
        Path(self.out, "SW001_Guest_01_a-test_9x16.mp4").write_bytes(b"x")
        p = self.dry(self.tsv)
        self.assertEqual(p.returncode, 1)
        self.assertIn("SW001_Guest_01_a-test_9x16.mp4 already exists", p.stderr)
        gate, log = self.make_gate(runtime=2.0, cuts=self.tsv)
        self.assertEqual(self.dry(self.tsv, "--reviewed", gate, "--review-log", log).returncode, 0)


if __name__ == "__main__":
    unittest.main()
