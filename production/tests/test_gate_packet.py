"""
gate-packet on three lavfi clips (no client media). Skipped without ffmpeg.

Run: /usr/bin/python3 -m unittest -v production.tests.test_gate_packet
"""

import contextlib
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cli, delivercheck, gatepacket, reviewlog  # noqa: E402

HAVE_FF = all(os.access(p, os.X_OK) for p in (delivercheck.FFMPEG, delivercheck.FFPROBE))
FPS = 25


def make_clip(path, seconds, size="320x180"):
    subprocess.run([delivercheck.FFMPEG, "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={FPS}:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
                   check=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jpeg_size(path):
    b = Path(path).read_bytes()
    i = 2
    while i < len(b):
        marker = b[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    raise AssertionError("no SOF marker")


def wav_info(path):
    out = subprocess.run([delivercheck.FFPROBE, "-v", "error", "-show_entries", "stream=sample_rate,duration",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["sample_rate"]), float(s["duration"])


@unittest.skipUnless(HAVE_FF, "ffmpeg or ffprobe missing")
class GatePacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls._tmp.name)
        cls.clips = []
        for i, secs in enumerate((30, 45, 28)):
            p = os.path.join(cls.root, f"SW999_Test_{i + 1}.mp4")
            make_clip(p, secs)
            cls.clips.append(p)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def build(self, name="out", **kw):
        out = os.path.join(self.root, name)
        clips, inputs = gatepacket.clips_from_copies(self.clips)
        return out, gatepacket.build(out, "P-0001", clips, inputs, **kw)

    def test_packet(self):
        out, gate = self.build("packet", sendable={"SW999_Test_2": 30.0})
        self.assertEqual(gate["clip_count"], 3)
        self.assertAlmostEqual(gate["total_runtime_s"], 103, delta=1)
        self.assertEqual(json.loads(Path(out, "gate.json").read_text()), gate)
        self.assertEqual(gate["packet_sha256"], sha(os.path.join(out, "packet.json")))
        for c in gate["clips"]:
            sheet = os.path.join(out, c["sheet"]["file"])
            w, h = jpeg_size(sheet)
            self.assertLessEqual(max(w, h), 1920)
            self.assertLess(os.path.getsize(sheet), 1 << 20)
            self.assertEqual(c["sheet"]["sha256"], sha(sheet))
            self.assertEqual(len(c["sheet"]["cells"]), 24)
            self.assertEqual(c["sheet"]["cells"][0]["t_s"], 0)
            self.assertEqual(len(c["excerpts"]), 2)
            for e in c["excerpts"]:
                f = os.path.join(out, e["file"])
                self.assertEqual(e["sha256"], sha(f))
                rate, dur = wav_info(f)
                self.assertEqual(rate, 48000)
                self.assertAlmostEqual(dur, 20.0, delta=1 / FPS)
            self.assertEqual(c["excerpts"][0]["start_s"], 0)
        two = gate["clips"][1]["excerpts"][1]
        self.assertAlmostEqual(two["start_s"], 20.0, delta=0.01)  # 30 s centred, in a 45 s clip
        mid = gate["clips"][0]["excerpts"][1]
        self.assertAlmostEqual(mid["start_s"], 5.0, delta=0.01)  # the middle of 30 s
        self.assertEqual([i["sha256"] for i in gate["inputs"]], [sha(p) for p in self.clips])

    def test_pending_line_validates_through_review_log(self):
        out, gate = self.build("pending")
        pend = gate["pending"]
        self.assertEqual((pend["step"], pend["choice"]), ("gate", None))
        proj = os.path.join(self.root, "proj")
        line = reviewlog.append(proj, pend["project"], pend["step"], "desktop", choice="go", key=out)
        self.assertEqual(line["stimulus"], pend["stimulus"])
        self.assertEqual(line["options"], pend["options"])
        self.assertEqual(line["choice"], "go")
        with self.assertRaises(reviewlog.Refused):  # the pending form itself is not an answer
            reviewlog.append(proj, pend["project"], pend["step"], "desktop", key=out)

    def test_cuts_mode(self):
        tsv = os.path.join(self.root, "cuts.tsv")
        Path(tsv).write_text("# id\tin\tout\ttitle\n7\t00:00:02\t00:00:27\tfirst\n8\t5.5\t35\tsecond\n")
        clips, inputs = gatepacket.clips_from_cuts(self.clips[1], tsv)
        gate = gatepacket.build(os.path.join(self.root, "cuts"), "P-0002", clips, inputs)
        self.assertAlmostEqual(gate["total_runtime_s"], 25 + 29.5, delta=0.01)
        self.assertEqual([i["sha256"] for i in gate["inputs"]], [sha(self.clips[1]), sha(tsv)])
        # the second clip's line excerpt sits inside its span: cell times are clip-relative
        self.assertLessEqual(gate["clips"][1]["sheet"]["cells"][-1]["t_s"], 29.5)
        for row in ("9\t10\t99\tpast the end\n", "9\t30\t10\tinverted\n"):
            Path(tsv).write_text(row)
            with self.assertRaises(gatepacket.GateError):
                gatepacket.clips_from_cuts(self.clips[1], tsv)

    def test_refusals(self):
        clips, inputs = gatepacket.clips_from_copies(self.clips)
        out, _ = self.build("again")
        with self.assertRaises(gatepacket.GateError):  # overwrite
            gatepacket.build(out, "P-0001", clips, inputs)
        repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaises(gatepacket.GateError):  # git tree
            gatepacket.build(os.path.join(repo, "out"), "P-0001", clips, inputs)
        self.assertFalse(os.path.exists(os.path.join(repo, "out")))
        silent = os.path.join(self.root, "silent.mp4")
        subprocess.run([delivercheck.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=25:duration=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", silent], check=True)
        sc, si = gatepacket.clips_from_copies([silent])
        with self.assertRaises(gatepacket.GateError):  # ffmpeg fails midway: nothing is left behind
            gatepacket.build(os.path.join(self.root, "half"), "P-0001", sc, si)
        self.assertFalse(os.path.exists(os.path.join(self.root, "half")))
        with self.assertRaises(gatepacket.GateError):  # a name, not an id
            gatepacket.build(os.path.join(self.root, "named"), "Some Client", clips, inputs)
        with self.assertRaises(gatepacket.GateError):  # a sendable time outside the clip
            gatepacket.build(os.path.join(self.root, "late"), "P-0001", clips, inputs,
                             sendable={"SW999_Test_1": 99})

    def test_command(self):
        out = os.path.join(self.root, "cmd")
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cli.main(["gate-packet", "--out", out, "--project", "P-0003", "--clip", *self.clips,
                           "--sendable", "SW999_Test_3=10"])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("total runtime 103", buf.getvalue())
        self.assertNotIn("render", buf.getvalue().lower())
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["gate-packet", "--out", out, "--project", "P-0003",
                                       "--clip", *self.clips]), 2)  # now exists
            self.assertEqual(cli.main(["gate-packet", "--out", out + "2", "--project", "P-0003"]), 2)


if __name__ == "__main__":
    unittest.main()
