"""
vertcut: `--plan` takes the static 9x16 crop per row from a reframe-plan manifest copy; caption cues go
through a bounded number of ffmpeg inputs; and three guards (the batch gate names the source file, the
review-srt folder is checked by its real path, a drop-frame timecode is a clean error).

The plan is written by the real code path: rpresolve.reframe.plan on a fake reader and fake faces, then
reframe.apply. Sources are made by ffmpeg lavfi in a temp dir outside git; words are placeholder text.
vertcut tests run in the 3.14 job.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_vertcut_plan.py -v
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "production"))
sys.path.insert(0, str(ROOT / "tools" / "vertcut" / "lib"))
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

NEEDS = unittest.skipUnless(HAVE_PIL and FONT and os.path.isfile(FFMPEG), "Pillow, a bold font or ffmpeg missing")


def ffmpeg(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def make_source(path, size="1280x720", rate=25, secs=6, tone=440):
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=%s:rate=%s:duration=%d" % (size, rate, secs),
           "-f", "lavfi", "-i", "sine=frequency=%d:duration=%d" % (tone, secs),
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)


def run_cut(*argv, check=False):
    r = subprocess.run([sys.executable, str(CUT), *argv], capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


def real_plan(src, spans=((0.5, 3.0), (3.0, 5.5)), size=(1280, 720)):
    """The manifest copy reframe-plan writes: reframe.plan on a fake reader, then reframe.apply. A face at
    x 400 before 3 s and x 900 after, 160 px wide, in a picture with no bars."""
    from rpresolve import reframe

    class Reader:
        duration = 0.0

        def __init__(self):
            self.size = size

        def read(self, times, folder):
            luma = bytes([120] * (size[0] * size[1]))
            out = []
            for t in times:
                png = os.path.join(folder, "%.3f.png" % t)
                open(png, "wb").close()
                out.append((t, png, luma))
            return out

    def detect(paths):
        out = {}
        for p in paths:
            t = float(os.path.basename(p)[:-4])
            cx = 400 if t < 3.0 else 900
            out[p] = [{"x": (cx - 80) / size[0], "y": (360 - 80) / size[1], "w": 160.0 / size[0],
                       "h": 160.0 / size[1], "confidence": 0.9}]
        return out

    m = {"source_path": src, "speech": False,
         "clips": [{"name": "a", "spans": [{"in": a, "out": b} for a, b in spans]}]}
    report = reframe.plan(m, aspects=["9x16"], reader=Reader(), detect=detect)
    return reframe.apply(m, report)


@NEEDS
class TestPlanCrop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.realpath(cls.tmp.name)
        cls.src = os.path.join(cls.dir, "SW999_Test_plan.mp4")
        make_source(cls.src)
        cls.plan = real_plan(cls.src)
        for sp in cls.plan["clips"][0]["spans"]:
            assert sp["reframe"]["9x16"]["status"] == "pass", sp
        cls.style = os.path.join(cls.dir, "style.json")
        Path(cls.style).write_text(json.dumps({"font": FONT}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def chains(self, rows, plan=None, src=None, offset=0.0, size=(1280, 720)):
        import vertcut_cut as v
        rows = [dict(r, index=i) for i, r in enumerate(rows, 1)]
        path = os.path.join(self.dir, "plan-%d.json" % id(plan))
        Path(path).write_text(json.dumps(plan or self.plan))
        return v.plan_chain(path, src or self.src, size[0], size[1], rows, 25.0, offset)

    def expect(self, span):
        e = self.plan["clips"][0]["spans"][span]["reframe"]["9x16"]
        cw, ch = 1080 / e["scale"], 1920 / e["scale"]
        return "[0:v]crop=%d:%d:%d:%d,scale=1080:1920:flags=lanczos,setsar=1[base]" % (
            round(cw), round(ch), round(e["x"] - cw / 2), round(e["y"] - ch / 2))

    def test_each_row_takes_the_crop_of_the_span_that_holds_it(self):
        got = self.chains([{"id": "r1", "start": 1.0, "end": 2.0}, {"id": "r2", "start": 3.5, "end": 5.0}])
        self.assertEqual(got[1], self.expect(0))
        self.assertEqual(got[2], self.expect(1))
        self.assertNotEqual(got[1], got[2])      # the face moved, so the crops differ

    def test_the_offset_moves_the_row_into_its_span(self):
        got = self.chains([{"id": "r", "start": 0.0, "end": 1.0}], offset=3.5)
        self.assertEqual(got[1], self.expect(1))

    def refused(self, rows, plan=None, src=None, size=(1280, 720)):
        with self.assertRaises(SystemExit) as cm, mock.patch("sys.stderr") as err:
            self.chains(rows, plan, src, size=size)
        self.assertEqual(cm.exception.code, 1)
        return "".join(c.args[0] for c in err.write.call_args_list)

    def test_a_row_no_span_holds_is_refused(self):
        self.assertIn("no plan span holds it", self.refused([{"id": "r", "start": 0.0, "end": 1.0}]))
        # straddling two spans is not held by either
        self.assertIn("no plan span holds it", self.refused([{"id": "r", "start": 2.0, "end": 4.0}]))

    def test_a_plan_for_another_source_is_refused(self):
        other = os.path.join(self.dir, "SW999_Test_other.mp4")
        Path(other).write_bytes(b"x")
        self.assertIn("another source", self.refused([{"id": "r", "start": 1.0, "end": 2.0}], src=other))

    def test_a_plan_for_another_picture_size_is_refused(self):
        self.assertIn("planned on a 1280x720", self.refused([{"id": "r", "start": 1.0, "end": 2.0}], size=(1920, 1080)))

    def test_a_plan_without_9x16_is_refused(self):
        plan = copy.deepcopy(self.plan)
        plan["reframe_plan"]["aspects"] = ["1x1"]
        self.assertIn("no 9x16 crops", self.refused([{"id": "r", "start": 1.0, "end": 2.0}], plan))

    def test_a_crop_that_is_not_a_filled_static_crop_is_refused(self):
        row = [{"id": "r", "start": 1.0, "end": 2.0}]
        for what, change, word in (
                ("manual", lambda e: e.update(status="manual"), "manual"),
                ("bars", lambda e: e.update(bars="top and bottom"), "with bars"),
                ("moving", lambda e: e.update(x=[100, 200]), "moves"),
                ("keyframes", lambda e: e.update(keyframes=[{"t": 0, "x": 1}]), "moves"),
                ("no scale", lambda e: e.pop("scale"), "no x, y and scale"),
                ("squeezed", lambda e: e.update(stored=[960, 720], squeeze=1.333), "squeezed"),
                ("other frame", lambda e: e.update(out=[1080, 1080]), "frame")):
            plan = copy.deepcopy(self.plan)
            change(plan["clips"][0]["spans"][0]["reframe"]["9x16"])
            self.assertIn(word, self.refused(row, plan), what)

    def test_a_crop_that_leaves_the_picture_is_refused(self):
        plan = copy.deepcopy(self.plan)
        plan["clips"][0]["spans"][0]["reframe"]["9x16"]["x"] = 10.0
        self.assertIn("leaves the", self.refused([{"id": "r", "start": 1.0, "end": 2.0}], plan))

    def test_the_cli_refuses_x_and_frame_with_a_plan(self):
        tsv = os.path.join(self.dir, "c.tsv")
        Path(tsv).write_text("1\t1.0\t2.0\tplan row\n")
        plan = os.path.join(self.dir, "plan.json")
        Path(plan).write_text(json.dumps(self.plan))
        for extra in (["--x=0.3"], ["--frame", "fit"]):
            r = run_cut(self.src, tsv, "--plan", plan, "-o", os.path.join(self.dir, "o-refuse"), "--no-captions",
                        "--draft-names", "--dry-run", *extra)
            self.assertEqual(r.returncode, 1)
            self.assertIn("does not go with", r.stderr)

    def test_a_render_uses_the_plan_crop(self):
        tsv = os.path.join(self.dir, "c2.tsv")
        Path(tsv).write_text("1\t1.0\t2.0\tplan row\n")
        plan = os.path.join(self.dir, "plan2.json")
        Path(plan).write_text(json.dumps(self.plan))
        out = os.path.join(self.dir, "o-plan")
        run_cut(self.src, tsv, "--plan", plan, "-o", out, "--no-captions", "--draft-names", "--crf", "0",
                "--preset", "ultrafast", check=True)
        from PIL import Image, ImageChops, ImageStat
        from vertcut_cut import COLOR_TAG_ARGS, COLOR_TAG_FILTER

        def frame(mp4, name):
            png = os.path.join(self.dir, name + ".png")
            ffmpeg("-i", mp4, "-frames:v", "1", png)
            return Image.open(png).convert("RGB")

        def mean_diff(a, b):
            return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3

        got = frame(os.path.join(out, "1-plan-row.mp4"), "got")
        # the same crop made by hand, encoded the way vertcut encodes, so both go through one colour path
        for name, vf in (("want", self.expect(0).replace("[0:v]", "").replace("[base]", "")),
                         ("centre", "scale=-2:1920:flags=lanczos,crop=1080:1920,setsar=1")):
            ffmpeg("-ss", "1.0", "-i", self.src, "-vf", vf + "," + COLOR_TAG_FILTER, "-frames:v", "1", "-c:v", "libx264",
                   "-crf", "0", "-pix_fmt", "yuv420p", *COLOR_TAG_ARGS, os.path.join(self.dir, name + ".mp4"))
        want, centre = (frame(os.path.join(self.dir, n + ".mp4"), n) for n in ("want", "centre"))
        self.assertLess(mean_diff(got, want), 2.0)
        self.assertGreater(mean_diff(got, centre), 15.0)      # not the hand crop at --x 0, which centres the picture


@NEEDS
class TestBoundedCaptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.realpath(cls.tmp.name)
        cls.src = os.path.join(cls.dir, "SW999_Test_cues.mp4")
        make_source(cls.src, size="640x360", rate=30, secs=60)
        cls.style = os.path.join(cls.dir, "style.json")
        Path(cls.style).write_text(json.dumps({"font": FONT}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def words(self, name, n, step):
        path = os.path.join(self.dir, name)
        Path(path).write_text(json.dumps({"segments": [{"words": [
            {"word": " w%d" % i, "start": i * step, "end": i * step + 0.25} for i in range(n)]}]}))
        return path

    def cut(self, name, tsv_row, words, out, *extra, spy=None):
        tsv = os.path.join(self.dir, name + ".tsv")
        Path(tsv).write_text(tsv_row)
        argv = [self.src, tsv, "--words", words, "-o", out, "--draft-names", "--caption-style", self.style,
                "--max-words", "1", "--preset", "ultrafast", *extra]
        if spy is None:
            return run_cut(*argv, check=True)
        import vertcut_cut as v
        real = subprocess.run

        def wrap(cmd, *a, **k):
            if cmd and cmd[0] == "ffmpeg" and "-filter_complex" in cmd:
                spy.append(cmd)
            return real(cmd, *a, **k)
        with mock.patch.object(sys, "argv", ["vertcut cut"] + argv), mock.patch("subprocess.run", wrap):
            try:
                v.main()
            except SystemExit as e:
                self.assertFalse(e.code, "vertcut exited %r" % (e.code,))

    def graph(self, cmd):
        return cmd[cmd.index("-filter_complex") + 1]

    def test_a_200_cue_clip_has_a_bounded_graph_and_renders(self):
        words = self.words("w200.json", 200, 0.25)
        small, big = [], []
        self.cut("c3", "1\t0\t1\tthree\n", words, os.path.join(self.dir, "o3"), "--crf", "35", spy=small)
        self.cut("c200", "1\t0\t50\ttwo hundred\n", words, os.path.join(self.dir, "o200"), "--crf", "35", spy=big)
        self.assertEqual(len(small), 1)
        self.assertEqual(len(big), 1)
        inputs = big[0].count("-i")
        self.assertLessEqual(inputs, 1 + 4, big[0][:12])        # the source and at most four cue streams
        self.assertLessEqual(self.graph(big[0]).count("overlay"), 4)
        self.assertLess(len(self.graph(big[0])), 600)            # one overlay per cue was ~100 characters each
        self.assertLessEqual(len(self.graph(big[0])), len(self.graph(small[0])) + 3 * 120)
        out = os.path.join(self.dir, "o200", "1-two-hundred.mp4")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=nb_frames", "-select_streams", "v:0",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
        self.assertEqual(int(probe), 50 * 30)
        # captions are on screen late in the clip: the bar's pixels differ from the same clip without captions
        shot = os.path.join(self.dir, "late.png")
        ffmpeg("-ss", "40.1", "-i", out, "-frames:v", "1", shot)
        from PIL import Image
        bar = Image.open(shot).convert("L").crop((300, 1600, 780, 1680))
        self.assertLess(bar.getextrema()[0], 40)                 # the dark bar
        self.assertGreater(bar.getextrema()[1], 200)             # and its white text

    def legacy(self, words, start, end, out):
        """Today's build: one input and one overlay per cue, enable between start and end."""
        import vertcut_cut as v
        from PIL import ImageFont
        style = v.resolve_style(self.style, None, None, None)
        font = ImageFont.truetype(style["font"], 66)
        cues = v.group_cues(v.load_words(words), start, end, 1, 32, 0.6, 16)
        inputs = ["-ss", "%.3f" % start, "-to", "%.3f" % end, "-i", self.src]
        chain, _ = v.reframe_chain(640, 360, "crop", 0.0)
        filters, label = [chain], "[base]"
        for i, c in enumerate(cues):
            png = os.path.join(self.dir, "legacy%d.png" % i)
            v.render_cue(v.wrap_chars(c["text"], 16), font, png, style, v.DEFAULT_BAR_OPACITY)
            inputs += ["-loop", "1", "-i", png]
            filters.append("%s[%d:v]overlay=x=(W-w)/2:y=%d-h:enable='between(t,%.3f,%.3f)'[v%d]"
                           % (label, i + 1, v.CAPTION_BOTTOM, c["start"], c["end"], i))
            label = "[v%d]" % i
        filters.append("%s%s[vout]" % (label, v.COLOR_TAG_FILTER))
        ffmpeg(*inputs, "-filter_complex", ";".join(filters), "-map", "[vout]", "-t", "%.3f" % (end - start), "-an", "-c:v", "libx264",
               "-crf", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "30", *v.COLOR_TAG_ARGS, out)
        return len(cues)

    def test_a_three_cue_clip_matches_the_per_cue_build(self):
        # cues with a gap between each, no hook: luma identical, chroma within rounding. The second clip
        # starts between two source frames (30 fps), so its first frame is not at 0.
        words = self.words("w3.json", 10, 0.5137)    # no cue edge lands on a frame time
        for start, end in ((0.0, 5.0), (0.37, 5.37)):
            with self.subTest(start=start):
                tag = "s%d" % round(start * 100)
                new_dir = os.path.join(self.dir, "o-new" + tag)
                self.cut("cmp" + tag, "1\t%s\t%s\tcompare\n" % (start, end), words, new_dir, "--crf", "0", "--no-hook")
                old = os.path.join(self.dir, "old%s.mp4" % tag)
                self.assertGreaterEqual(self.legacy(words, start, end, old), 3)
                new = os.path.join(new_dir, "1-compare.mp4")
                stats = os.path.join(self.dir, "psnr.txt")
                r = subprocess.run([FFMPEG, "-hide_banner", "-i", old, "-i", new, "-lavfi", "psnr=stats_file=" + stats,
                                    "-f", "null", "-"], capture_output=True, text=True)
                line = [ln for ln in r.stderr.splitlines() if "PSNR y:" in ln][-1]
                self.assertIn("y:inf", line)                     # every luma sample of every frame, equal
                u = float(line.split("u:")[1].split()[0])
                self.assertGreater(u, 55.0, line)                # chroma: a few 2x2 blocks at rounded corners

    def test_touching_cues_of_either_parity_do_not_stack_or_leave_a_zero_hold(self):
        # cues that touch, of different bar widths (so different parity groups): each shows only on its own
        # frames, and no concat entry holds for zero seconds
        import vertcut_cut as v
        from PIL import ImageFont
        font = ImageFont.truetype(FONT, 66)
        cues = [{"start": 0.0, "end": 1.0, "text": "ab"}, {"start": 1.0, "end": 2.0, "text": "abc"},
                {"start": 2.0, "end": 3.0, "text": "abcd"}]
        with tempfile.TemporaryDirectory() as tmp:
            style = v.resolve_style(self.style, None, None, None)
            streams = v.cue_streams(cues, lambda c: [c["text"]], font, v.CAPTION_BOTTOM, tmp, style, 0.88, 30)
            shown = {}
            for path, _, _ in streams:
                rows = Path(path).read_text().split("\n")
                self.assertNotIn("duration 0.000000", rows)
                t, name = 0.0, ""
                for row in rows:
                    if row.startswith("file"):
                        name = row
                    elif row.startswith("duration"):
                        d = float(row.split()[1])
                        if "cue" in name:
                            shown[name.split("cue")[1][:3]] = (round(t, 4), round(t + d, 4))
                        t += d
            self.assertEqual(shown["000"][1], round(30 / 30 - 0.25 / 30, 4))     # ends a quarter of a frame before frame 30
            self.assertEqual(shown["001"][0], round(30 / 30 - 0.25 / 30, 4))         # where the next one begins
            self.assertEqual(shown["001"][1], round(60 / 30 - 0.25 / 30, 4))


@NEEDS
class TestGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.realpath(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_gate_names_the_source_file(self):
        from rpresolve import gatepacket, reviewlog
        a, b = (os.path.join(self.dir, n) for n in ("a.mp4", "b.mp4"))
        make_source(a, size="640x360", secs=6, tone=440)
        make_source(b, size="640x360", secs=6, tone=880)
        tsv = os.path.join(self.dir, "g.tsv")
        Path(tsv).write_text("1\t0.0\t2.0\tgate test\n")
        gate_dir = os.path.join(self.dir, "gate")
        clips, inputs = gatepacket.clips_from_cuts(a, tsv)
        gatepacket.build(gate_dir, "P-0001", clips, inputs)
        reviewlog.append(os.path.join(self.dir, "proj"), "P-0001", "gate", "desktop", choice="go", key=gate_dir)
        out = os.path.join(self.dir, "gate-out")
        os.makedirs(out)
        Path(out, "1-gate-test.mp4").write_bytes(b"x")           # a replacement needs the gate
        argv = ["-o", out, "--no-captions", "--draft-names", "--dry-run", "--reviewed", os.path.join(gate_dir, "gate.json"),
                "--review-log", os.path.join(self.dir, "proj", "review", "events.jsonl")]
        ok = run_cut(a, tsv, *argv)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        other = run_cut(b, tsv, *argv)
        self.assertEqual(other.returncode, 1)
        self.assertIn("not built from this source file", other.stderr)

    def test_a_review_srt_symlink_into_a_repository_is_refused(self):
        src = os.path.join(self.dir, "h.mp4")
        make_source(src, size="640x360", secs=3)
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        os.makedirs(os.path.join(repo, "sub"))
        out = os.path.join(self.dir, "link-out")
        os.makedirs(out)
        os.symlink(os.path.join(repo, "sub"), os.path.join(out, "review-srt"))
        tsv = os.path.join(self.dir, "h.tsv")
        Path(tsv).write_text("1\t0.0\t2.5\tA test\tBIG IDEA\n")
        r = run_cut(src, tsv, "-o", out, "--no-captions", "--draft-names", "--hook-source", "test", "--preset", "ultrafast")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(os.listdir(os.path.join(repo, "sub")), [])

    def test_a_drop_frame_timecode_is_a_clean_error(self):
        src = os.path.join(self.dir, "t.mp4")
        make_source(src, size="640x360", secs=3)
        tsv = os.path.join(self.dir, "t.tsv")
        Path(tsv).write_text("1\t00:00:01;12\t00:00:02;00\tdrop frame\n")
        r = run_cut(src, tsv, "-o", os.path.join(self.dir, "t-out"), "--no-captions", "--draft-names", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a timecode", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_5994_source_goes_out_at_30000_1001(self):
        import vertcut_cut as v
        for rate in (Fraction(60000, 1001), 59.94005994):
            self.assertEqual(v.out_rate(rate), Fraction(30000, 1001), rate)
        self.assertEqual(v.out_rate(60.0), Fraction(30))
        self.assertEqual(v.out_rate(25.0), Fraction(25))
        src = os.path.join(self.dir, "ntsc60.mp4")
        make_source(src, size="320x180", rate="60000/1001", secs=2)
        tsv = os.path.join(self.dir, "n.tsv")
        Path(tsv).write_text("1\t0\t1\tntsc\n")
        out = os.path.join(self.dir, "ntsc-out")
        run_cut(src, tsv, "-o", out, "--no-captions", "--draft-names", "--preset", "ultrafast", check=True)
        rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                               "-of", "csv=p=0", os.path.join(out, "1-ntsc.mp4")], capture_output=True, text=True).stdout.strip()
        self.assertEqual(rate, "30000/1001")



if __name__ == "__main__":
    unittest.main()
