"""
review-log writer on synthetic keys only (no media, no ffmpeg).

Run: /usr/bin/python3 -m unittest -v production.tests.test_review_log
"""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cli, reviewlog, tastedistill  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReviewLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.proj = os.path.join(self.root, "proj")
        self.log = os.path.join(self.proj, "review", "events.jsonl")
        # an absheet-shaped key: DIR/sheet.jpg and DIR.key/key.json
        self.sheet_dir = os.path.join(self.root, "look-01")
        os.makedirs(self.sheet_dir)
        os.makedirs(self.sheet_dir + ".key")
        Path(self.sheet_dir, "sheet.jpg").write_bytes(b"not a real jpeg")
        self.abkey = os.path.join(self.sheet_dir + ".key", "key.json")
        Path(self.abkey).write_text(json.dumps({
            "tool": "rpstills absheet", "seed": 7, "sheet": {"file": "sheet.jpg"},
            "labels": {"A": {"source": "/x/natural-v2.jpg"}, "B": {"source": "/x/warm-v1.jpg"}}}))
        # a mixbuild-shaped key: DIR/sealed/key.json and DIR/packet.json
        self.mix = os.path.join(self.root, "mix-01")
        os.makedirs(os.path.join(self.mix, "sealed"))
        Path(self.mix, "packet.json").write_text("{}")
        self.mixkey = os.path.join(self.mix, "sealed", "key.json")
        Path(self.mixkey).write_text(json.dumps({
            "seed": 1, "noise_type": "rumble",
            "labels": {"A": {"source": "/x/take1.wav", "gain_db": 0}, "B": {"source": "/x/take2.wav", "gain_db": -1}}}))

    def run_cmd(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["review-log", *argv])
        return code, out.getvalue(), err.getvalue()

    def base(self, *extra, project="P-0001", step="look", dev="ipad"):
        return ["--dir", self.proj, "--project", project, "--step", step, "--device", dev, *extra]

    def test_absheet_and_mixbuild_lines_read_back_clean(self):
        self.assertEqual(self.run_cmd(*self.base("--choice", "B", "--key", self.abkey, "--question", "warmth"))[0], 0)
        self.assertEqual(self.run_cmd(*self.base("--choice", "A", "--key", self.mix, "--reason", "less hiss", step="sound"))[0], 0)
        self.assertEqual(self.run_cmd(*self.base("--choice", "approve", step="edit"))[0], 0)
        events, bad = tastedistill.read_events(self.log)
        self.assertEqual((len(events), bad), (3, 0))
        look, sound = events[0][1], events[1][1]
        self.assertEqual(look["options"], {"A": "natural-v2.jpg", "B": "warm-v1.jpg"})
        self.assertEqual(look["stimulus"], sha(os.path.join(self.sheet_dir, "sheet.jpg")))
        self.assertEqual(sound["stimulus"], sha(os.path.join(self.mix, "packet.json")))
        self.assertEqual(sound["noise_type"], "rumble")
        self.assertRegex(look["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_refusals_exit_2_and_write_nothing(self):
        cases = [
            self.base("--choice", "C", "--key", self.abkey),          # letter the key lacks
            self.base("--choice", "A", "--key", self.abkey, step="polish"),   # bad step
            self.base("--choice", "A", "--key", self.abkey, project="P 0001"),  # a space in the id
            self.base("--choice", "A", "--key", self.abkey, project="Ana-1"),   # lower-case, a name
            self.base("--choice", "A", "--key", self.abkey, "--repeat-of", "2020-01-01T00:00:00Z"),  # dangling
            self.base("--choice", "A", "--key", self.abkey, dev="watch"),
            self.base("--choice", "3", "--key", self.abkey),            # a rating on a lettered sheet
            self.base(),                                                 # neither choice nor reason
        ]
        for argv in cases:
            code, out, err = self.run_cmd(*argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertIn("ERROR", err)
        self.assertFalse(os.path.exists(self.log))

    def test_reason_that_is_not_utf8_exits_2_and_writes_nothing(self):
        bad = os.fsdecode(b"caf\xe9")  # what argv gives for a byte that is not UTF-8
        for reason in (bad, "\ud800"):
            code, out, err = self.run_cmd(*self.base("--reason", reason))
            self.assertEqual((code, out), (2, ""))
            self.assertIn("ERROR", err)
            self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.log))

    def test_log_inside_git_tree_refused(self):
        repo = os.path.join(self.root, "repo")
        os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        code, _, err = self.run_cmd("--dir", repo, "--project", "P-1", "--step", "look",
                                    "--device", "desktop", "--choice", "none")
        self.assertEqual(code, 2)
        self.assertIn("git working tree", err)
        self.assertFalse(os.path.exists(os.path.join(repo, "review")))

    def test_repeat_of_resolves_and_append_keeps_the_prefix(self):
        self.run_cmd(*self.base("--choice", "A", "--key", self.abkey))
        prefix = Path(self.log).read_bytes()
        ts = json.loads(prefix)["ts"]
        code, out, _ = self.run_cmd(*self.base("--choice", "A", "--key", self.abkey, "--repeat-of", ts))
        self.assertEqual(code, 0)
        data = Path(self.log).read_bytes()
        self.assertEqual(hashlib.sha256(data[:len(prefix)]).hexdigest(), hashlib.sha256(prefix).hexdigest())
        first, second = [json.loads(x) for x in data.decode().splitlines()]
        self.assertEqual(second["repeat_of"], ts)
        self.assertNotEqual(first["ts"], second["ts"])  # ts names a line, so it is unique

    def test_question_outside_vocabulary_warns_not_refuses(self):
        code, _, err = self.run_cmd(*self.base("--choice", "none", "--question", "vibes"))
        self.assertEqual(code, 0)
        self.assertIn("WARN", err)
        code, _, err = self.run_cmd(*self.base("--choice", "none", "--question", "ending"))
        self.assertEqual((code, err), (0, ""))

    def test_log_without_final_newline_keeps_both_lines(self):
        os.makedirs(os.path.dirname(self.log))
        Path(self.log).write_text('{"torn": ')
        self.run_cmd(*self.base("--choice", "none"))
        lines = Path(self.log).read_text().splitlines()
        self.assertEqual((lines[0], json.loads(lines[1])["choice"]), ('{"torn": ', "none"))

    def test_rating_without_key_is_kept(self):
        self.assertEqual(self.run_cmd(*self.base("--choice", "4", step="grade"))[0], 0)
        events, bad = tastedistill.read_events(self.log)
        self.assertEqual((len(events), bad), (1, 0))

    def test_hard_linked_log_is_refused(self):
        # the other name may sit inside a repository, where the realpath check never looks
        other = os.path.join(self.root, "elsewhere.jsonl")
        Path(other).write_text("")
        os.makedirs(os.path.dirname(self.log))
        os.link(other, self.log)
        code, _, err = self.run_cmd(*self.base("--choice", "none"))
        self.assertEqual((code, Path(other).read_text()), (2, ""))
        self.assertIn("hard link", err)

    def test_concurrent_writers_keep_ts_unique(self):
        procs = [subprocess.Popen([sys.executable, "-c", (
            "import sys; sys.path.insert(0, %r)\n"
            "from rpresolve import reviewlog\n"
            "for i in range(4): reviewlog.append(%r, 'P-0001', 'look', 'desktop', choice='none')"
        ) % (str(HERE.parent), self.proj)]) for _ in range(8)]
        self.assertEqual([p.wait() for p in procs], [0] * 8)
        events, bad = tastedistill.read_events(self.log)
        self.assertEqual((len(events), len({e["ts"] for _, e in events}), bad), (32, 32, 0))


REEL_FIELDS = ("frame_0_image", "text_hook", "why_line", "sendable_line", "sendable_time", "ending", "tempo")


class VocabularyDocTests(unittest.TestCase):
    PROD = HERE.parent

    def test_every_question_key_is_in_the_review_doc(self):
        text = (self.PROD / "media-review-loop.md").read_text()
        for k in reviewlog.QUESTIONS:
            self.assertIn(f"`{k}`", text, k)

    def test_every_reel_field_is_in_story_options(self):
        text = (self.PROD / "story-options.md").read_text()
        for k in REEL_FIELDS:
            self.assertIn(f"`{k}`", text, k)


if __name__ == "__main__":
    unittest.main()
