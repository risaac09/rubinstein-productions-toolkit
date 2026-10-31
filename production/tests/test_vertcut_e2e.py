"""
vertcut end to end at social_9x16: a synthetic 1920x1080 29.97 source is cut by
`vertcut cut --destination social_9x16 --lufs -14`, then deliver_check (with a
review log built by rpresolve.reviewlog) and qc read the result. The planted
faults are asserted first: a -16 LUFS render must FAIL loudness, a 23.976 source
must WARN fps_floor and social_fps, and a log without the matching line must WARN
the approval row. Only then are the clean run's rows asserted. Everything is made
by ffmpeg lavfi in a temp dir outside git; the hook text is placeholder text.
vertcut tests run in the 3.14 job.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_vertcut_e2e.py -v
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "production"))
CUT = ROOT / "tools" / "vertcut" / "lib" / "vertcut_cut.py"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FONTS = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")
FONT = next((f for f in FONTS if os.path.isfile(f)), None)

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

DEST = "social_9x16"


def cli_json(*argv):
    from rpresolve import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main([*argv, "--json"])
    return code, {r["check"]: r for r in json.loads(out.getvalue())["checks"]}


@unittest.skipUnless(HAVE_PIL and FONT and os.path.isfile(FFMPEG), "Pillow, a bold font or ffmpeg missing")
class TestVertcutE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = d = os.path.realpath(cls.tmp.name)
        cls.words = os.path.join(d, "words.json")
        Path(cls.words).write_text(json.dumps({"segments": [{"words": [
            {"word": " w%d" % i, "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(30)]}]}))
        cls.tsv = os.path.join(d, "clips.tsv")
        Path(cls.tsv).write_text("1\t0:00\t0:04\tfirst example clip\tAA BB CC|DD EE FF\n"
                                 "2\t0:05\t0:09\tsecond example clip\n")
        cls.style = os.path.join(d, "style.json")
        Path(cls.style).write_text(json.dumps({"font": FONT}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def source(self, name, rate):
        src = os.path.join(self.dir, name)
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=1920x1080:rate=%s:duration=10" % rate, "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=10", "-pix_fmt", "yuv420p", "-shortest", src], check=True)
        return src

    def cut(self, src, out, lufs):
        out = os.path.join(self.dir, out, DEST)  # a folder named for the destination
        r = subprocess.run([sys.executable, str(CUT), src, self.tsv, "--words", self.words, "-o", out,
                            "--show", "SW", "--episode", "1", "--guest", "Test", "--destination", DEST,
                            "--lufs", str(lufs), "--hook-source", "placeholder source",
                            "--caption-style", self.style, "--crf", "30", "--preset", "ultrafast"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        clips = sorted(Path(out).glob("*.mp4"))
        self.assertEqual(len(clips), 2, clips)
        return out, clips[0]

    def review_log(self, mp4, approve=True):
        """A log written by reviewlog: a review copy named by the clip's stem, and an approve line for it."""
        from rpresolve import reviewlog
        proj = os.path.join(self.dir, "proj-%s-%s" % (mp4.stem, approve))
        copy = os.path.join(proj, mp4.name.replace("_9x16", "_review"))
        os.makedirs(proj)
        shutil.copy(mp4, copy)
        if approve:
            reviewlog.append(proj, "P-0001", "gate", "desktop", choice="approve", review_copy=copy)
        else:
            reviewlog.append(proj, "P-0001", "gate", "desktop", choice="none", reason="not yet")
        return os.path.join(proj, "review", "events.jsonl")

    def test_planted_faults_then_clean_run(self):
        # 1. The faults, each seen to fail or warn before the clean run is trusted.
        src_2398 = self.source("SW999_Test_2398.mp4", "24000/1001")
        _, bad = self.cut(src_2398, "o-2398", -14)
        _, dc = cli_json("deliver-check", str(bad), "--dest", DEST)
        self.assertEqual(dc["fps_floor"]["status"], "WARN", dc["fps_floor"])
        self.assertEqual(dc["fps"]["status"], "PASS", dc["fps"])  # 24000/1001 is a standard rate, only under the floor
        _, qc = cli_json("qc", str(bad), "--dest", DEST)
        self.assertEqual(qc["social_fps"]["status"], "WARN", qc["social_fps"])

        src = self.source("SW999_Test_2997.mp4", "30000/1001")
        _, quiet = self.cut(src, "o-quiet", -16)
        code, dc = cli_json("deliver-check", str(quiet), "--dest", DEST)
        self.assertEqual((code, dc["loudness"]["status"]), (1, "FAIL"), dc["loudness"])

        out, clip = self.cut(src, "o-clean", -14)
        none = self.review_log(clip, approve=False)
        _, dc = cli_json("deliver-check", str(clip), "--dest", DEST, "--review-log", none)
        self.assertEqual(dc["approval"]["status"], "WARN", dc["approval"])

        # 2. The clean run: every asserted row passes.
        log = self.review_log(clip)
        code, dc = cli_json("deliver-check", str(clip), "--dest", DEST, "--review-log", log)
        self.assertEqual(code, 0, dc)
        for name in ("fps", "fps_floor", "loudness", "approval", "captions", "name", "folder"):
            self.assertEqual(dc[name]["status"], "PASS", dc[name])
        self.assertTrue(list(Path(out, "review-srt").glob("*.srt")))
        self.assertTrue(list(Path(out, "review-srt").glob("*.hook.json")))
        code, qc = cli_json("qc", str(clip), "--dest", DEST)
        self.assertEqual(code, 0, qc)
        self.assertEqual(qc["social_fps"]["status"], "PASS", qc["social_fps"])
        self.assertEqual(qc["hook_text"]["status"], "PASS", qc["hook_text"])
        # safe_zone runs (SKIP only without numpy); WARN is expected: the burned-in caption bar sits in the bottom band
        self.assertIn(qc["safe_zone"]["status"], ("PASS", "WARN", "SKIP"), qc["safe_zone"])
        self.assertIn("speech_onset", qc)


if __name__ == "__main__":
    unittest.main()
