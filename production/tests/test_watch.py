"""
watch sheets, ack and status on a synthetic clip whose grey level is 20 + 3 x frame number
(no client media). Sheets need ffmpeg and Pillow; ack and status need neither.

Run: /usr/bin/python3 -m unittest -v production.tests.test_watch
"""

import contextlib
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

from rpresolve import cli, delivercheck  # noqa: E402
from rpresolve import cmd_watch as w  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
HAVE_FF = all(os.access(p, os.X_OK) for p in (delivercheck.FFMPEG, delivercheck.FFPROBE))
FPS = 25
CUTS = [0, 10, 30, 31, 50, 60, 64]  # 6 shots; shot 3 is one frame long


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["watch", *map(str, argv)])
    return code, out.getvalue(), err.getvalue()


def make_video(path, size="320x180"):
    # grey level = 20 + 3 x frame number, held through a lossless codec
    subprocess.run([delivercheck.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:size={size}:rate={FPS}:duration=3,format=gray,geq=lum='20+3*N',format=gray",
                    "-c:v", "ffv1", path], check=True)


def grey(img):
    p = subprocess.run([delivercheck.FFMPEG, "-v", "error", "-i", img, "-vf", "scale=1:1", "-pix_fmt", "gray",
                        "-f", "rawvideo", "-"], stdout=subprocess.PIPE, check=True)
    return p.stdout[0]


class Pure(unittest.TestCase):
    def test_shot_frames(self):
        self.assertEqual(w.shot_frames([0, 10, 30, 31]), [[0, 4, 9], [10, 19, 29], [30, 30, 30]])

    def test_cut_frames_from_times_and_json(self):
        self.assertEqual(w.cut_frames("0,0.4,1.2", 25), [0, 10, 30])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "c.json")
            p.write_text(json.dumps({"cut_times": [0, 0.4, 1.2]}))
            self.assertEqual(w.cut_frames(str(p), 25), [0, 10, 30])
            p.write_text(json.dumps({"cut_frames": [5, 6]}))
            self.assertEqual(w.cut_frames(str(p), 25), [5, 6])
        with self.assertRaises(w.WatchError):
            w.cut_frames("0,10,10", 25)


class AckStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.video = self.d / "v.bin"
        self.video.write_bytes(b"x" * 5000)
        self.out = self.d / "out"
        self.out.mkdir()
        (self.out / "watch-01.jpg").write_bytes(b"jpg")
        w._save(str(self.out), {
            "video": str(self.video), "video_sha256_first1mib_and_size": w.fingerprint(str(self.video)),
            "fps": 25.0, "shots": [{"index": 1, "frames": [0, 1, 2], "sheet": "watch-01.jpg"},
                                   {"index": 2, "frames": [3, 4, 5], "sheet": "watch-01.jpg"}],
            "sheets": ["watch-01.jpg"], "acked": False})
        self.notes = self.d / "notes.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_wrong_line_count(self):
        self.notes.write_text("one\n\n")
        code, _, err = run("ack", self.out, "--notes", self.notes)
        self.assertEqual(code, 2)
        self.assertIn("1 note lines for 2 shots", err)
        self.assertEqual(run("status", self.out)[0], 1)

    def test_ack_then_status_then_changed_video(self):
        code, out, _ = run("status", self.out)
        self.assertEqual((code, out.strip()), (1, "not acked: no ack recorded"))
        self.notes.write_text("a\n\nb\n")
        self.assertEqual(run("ack", self.out, "--notes", self.notes)[0], 0)
        doc = json.loads((self.out / "watch.json").read_text())
        self.assertTrue(doc["acked"])
        self.assertEqual(doc["notes"], ["a", "b"])
        self.assertEqual(run("status", self.out), (0, "acked\n", ""))
        self.video.write_bytes(b"y" * 5000)
        code, out, _ = run("status", self.out)
        self.assertEqual(code, 1)
        self.assertIn("video changed", out)

    def test_unreadable_watch_json_is_a_refusal(self):
        (self.out / "watch.json").write_text('{"acked": true}')
        self.notes.write_text("a\nb\n")
        self.assertEqual(run("ack", self.out, "--notes", self.notes)[0], 2)
        code, out, _ = run("status", self.out)
        self.assertEqual(code, 1)
        self.assertIn("no readable watch.json", out)

    def test_ack_refuses_changed_video_and_missing_sheet(self):
        self.notes.write_text("a\nb\n")
        self.video.write_bytes(b"y" * 5000)
        code, _, err = run("ack", self.out, "--notes", self.notes)
        self.assertEqual(code, 2)
        self.assertIn("video changed", err)
        self.video.write_bytes(b"x" * 5000)
        (self.out / "watch-01.jpg").unlink()
        code, _, err = run("ack", self.out, "--notes", self.notes)
        self.assertEqual(code, 2)
        self.assertIn("watch-01.jpg is missing", err)


@unittest.skipUnless(HAVE_FF, "ffmpeg or ffprobe missing")
@unittest.skipUnless(HAVE_PIL, "Pillow missing")  # its own line: the 3.9 CI job has ffmpeg and no Pillow
class Sheets(unittest.TestCase):
    def test_sheets_frames_and_dimensions(self):
        with tempfile.TemporaryDirectory() as d:
            v, out = os.path.join(d, "v.mkv"), os.path.join(d, "out")
            make_video(v)
            cuts = ",".join(str(c / FPS) for c in CUTS)
            code, _, err = run("sheets", v, "--cuts", cuts, "--out", out, "--per-sheet", 4)
            self.assertEqual(code, 0, err)
            doc = json.loads(Path(out, "watch.json").read_text())
            self.assertEqual([s["frames"] for s in doc["shots"]],
                             [[0, 4, 9], [10, 19, 29], [30, 30, 30], [31, 40, 49], [50, 54, 59], [60, 61, 63]])
            self.assertEqual(doc["sheets"], ["watch-01.jpg", "watch-02.jpg"])
            self.assertEqual([s["sheet"] for s in doc["shots"]], ["watch-01.jpg"] * 4 + ["watch-02.jpg"] * 2)
            self.assertFalse(doc["acked"])
            from PIL import Image
            with contextlib.ExitStack() as st:
                sizes = [st.enter_context(Image.open(os.path.join(out, s))).size for s in doc["sheets"]]
            self.assertLessEqual(max(max(s) for s in sizes), 1920)
            self.assertEqual(sizes[0][0], sizes[1][0])
            self.assertEqual(sizes[0][1], 2 * sizes[1][1])  # 4 rows, then 2
            # exact frames: grab() returns the frame asked for, whatever the seek
            seen = []
            for f in (0, 1, 19, 30, 63):
                png = os.path.join(d, "g.png")
                w.grab(v, f, FPS, png)
                ref = os.path.join(d, "r.png")  # the same frame by decoding from the start and counting
                subprocess.run([delivercheck.FFMPEG, "-v", "error", "-nostdin", "-i", v, "-vf", f"select=eq(n\\,{f})",
                                "-frames:v", "1", "-y", ref], check=True, stdin=subprocess.DEVNULL)
                seen.append(grey(png))
                self.assertEqual(seen[-1], grey(ref), f)
                os.unlink(png)
            self.assertEqual(len(set(seen)), len(seen))  # the levels tell the frames apart

    def test_no_pillow_is_one_refusal(self):
        import unittest.mock as m
        with m.patch.dict(sys.modules, {"PIL": None}):
            code, _, err = run("sheets", "x.mkv", "--cuts", "0,1", "--out", "/nonexistent/o")
        self.assertEqual(code, 2)
        self.assertIn("needs Pillow", err)

    def test_start_time_offset_is_honoured(self):
        with tempfile.TemporaryDirectory() as d:
            v = os.path.join(d, "o.mkv")
            subprocess.run([delivercheck.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c=black:size=160x90:rate={FPS}:duration=2,format=gray,geq=lum='20+3*N',format=gray",
                            "-output_ts_offset", "10", "-c:v", "ffv1", v], check=True)
            png = os.path.join(d, "g.png")
            w.grab(v, 10, FPS, png)
            self.assertEqual(grey(png), 50)

    def test_portrait_six_shots_under_2000(self):
        with tempfile.TemporaryDirectory() as d:
            v, out = os.path.join(d, "v.mkv"), os.path.join(d, "out")
            make_video(v, "180x320")
            code, _, err = run("sheets", v, "--cuts", ",".join(str(c / FPS) for c in CUTS), "--out", out)
            self.assertEqual(code, 0, err)
            from PIL import Image
            with Image.open(os.path.join(out, "watch-01.jpg")) as im:
                self.assertEqual(im.size, (540, 1920))


if __name__ == "__main__":
    unittest.main()
