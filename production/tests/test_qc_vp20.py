"""
Tests for the opt-in qc rows of VP-20: sync, colour and stabiliser, on media
made here from lavfi sources and seeded noise (a few seconds each). Every
threshold is a working default; these tests pin the rows, not the numbers'
standing. Skipped where ffmpeg ("ffmpeg missing") or numpy is missing.

Run: /usr/bin/python3 -m unittest production.tests.test_qc_vp20 -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, qc  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
RATE = 48000


def _have():
    if np is None or not all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)):
        return False
    enc = subprocess.run([dc.FFMPEG, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True).stdout
    return "libx264" in enc


def _ff(*args):
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)


def _wav(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def _row(r, name):
    return next(c for c in r["checks"] if c["check"] == name)


def _video(out, vf_graph, audio=None):
    """vf_graph: a lavfi graph ending in [v]; audio: a wav to carry, or none."""
    cmd = ["-filter_complex", vf_graph]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-map", "[v]"] + (["-map", "0:a"] if audio else []) + \
        ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-t", "12", out]
    _ff(*cmd)


@unittest.skipUnless(_have(), "ffmpeg or numpy missing")
class Vp20Rows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls._tmp.name
        rng = np.random.default_rng(7)
        noise = (rng.standard_normal(RATE * 12) * 0.2).astype(np.float32)
        cls.ref = os.path.join(t, "ref.wav")
        _wav(cls.ref, noise)
        late = np.concatenate([np.zeros(int(0.2 * RATE), np.float32), noise])[:RATE * 12]
        cls.late_wav = os.path.join(t, "late.wav")
        _wav(cls.late_wav, late)
        cls.other = os.path.join(t, "other.wav")
        _wav(cls.other, (np.random.default_rng(99).standard_normal(RATE * 12) * 0.2).astype(np.float32))
        pic = "color=c=gray:s=108x192:r=25:d=12[v]"
        cls.inplace = os.path.join(t, "inplace.mov")
        cls.delayed = os.path.join(t, "delayed.mov")
        cls.unrelated = os.path.join(t, "unrelated.mov")
        _video(cls.inplace, pic, cls.ref)
        _video(cls.delayed, pic, cls.late_wav)
        _video(cls.unrelated, pic, cls.other)
        cls.gray = os.path.join(t, "gray.mp4")
        cls.black = os.path.join(t, "black.mp4")
        cls.hot = os.path.join(t, "hot.mp4")
        _video(cls.gray, "color=c=gray:s=108x192:r=25:d=3[v]")
        _video(cls.black, "color=c=black:s=108x192:r=25:d=3[v]")
        _video(cls.hot, "color=c=gray:s=108x192:r=25:d=3,format=yuv420p,lutyuv=y=250[v]")
        src = "testsrc2=s=540x960:r=25:d=4"
        cls.steady = os.path.join(t, "steady.mp4")
        cls.shaky = os.path.join(t, "shaky.mp4")
        _video(cls.steady, src + ",crop=432:768:50:50[v]")
        _video(cls.shaky, src + ",crop=432:768:x='50+20*sin(n*2.3)':y='50+20*sin(n*3.7+1)'[v]")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # sync
    def test_sync_skips_without_a_reference(self):
        row = _row(qc.run(self.inplace), "sync")
        self.assertEqual(row["status"], qc.SKIP)

    def test_sync_passes_a_file_sitting_on_its_reference(self):
        row = _row(qc.run(self.inplace, sync_ref=self.ref), "sync")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_sync_fails_a_file_200_ms_late(self):
        row = _row(qc.run(self.delayed, sync_ref=self.ref), "sync")
        self.assertEqual(row["status"], qc.FAIL, row)
        self.assertIn("ms", row["found"])

    def test_sync_warns_when_the_sound_is_unrelated(self):
        row = _row(qc.run(self.unrelated, sync_ref=self.ref), "sync")
        self.assertEqual((row["status"], row["found"]), (qc.WARN, "no match"))

    def test_sync_tolerance_is_a_threshold(self):
        th = qc.parse_overrides(["sync_tol_ms=500"])
        self.assertEqual(_row(qc.run(self.delayed, sync_ref=self.ref, thresholds=th), "sync")["status"],
                         qc.PASS)

    def test_sync_with_a_reference_that_has_no_sound_warns_and_keeps_the_table(self):
        r = qc.run(self.inplace, sync_ref=self.gray)
        self.assertEqual(_row(r, "sync")["status"], qc.WARN)
        self.assertTrue(_row(r, "sha256"))

    # colour
    def test_colour_skips_unless_asked(self):
        self.assertEqual(_row(qc.run(self.gray), "colour")["status"], qc.SKIP)

    def test_colour_passes_mid_grey(self):
        row = _row(qc.run(self.gray, colour=True), "colour")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_colour_warns_on_crushed_black(self):
        row = _row(qc.run(self.black, colour=True), "colour")
        self.assertEqual(row["status"], qc.WARN, row)

    def test_colour_fails_luma_over_235(self):
        row = _row(qc.run(self.hot, colour=True), "colour")
        self.assertEqual(row["status"], qc.FAIL, row)

    # stabiliser
    def test_stabiliser_skips_unless_asked(self):
        self.assertEqual(_row(qc.run(self.steady), "stabiliser")["status"], qc.SKIP)

    def test_stabiliser_passes_a_locked_off_shot(self):
        row = _row(qc.run(self.steady, stabiliser=True), "stabiliser")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_stabiliser_warns_on_residual_shake_and_never_fails(self):
        r = qc.run(self.shaky, stabiliser=True)
        self.assertEqual(_row(r, "stabiliser")["status"], qc.WARN, _row(r, "stabiliser"))
        self.assertEqual(r["status"], "pass")

    # the command
    # privacy (always on) and the MCP flags
    def tagged(self, name, *tags):
        out = os.path.join(self._tmp.name, name)
        meta = [x for t in tags for x in ("-metadata", t)]
        _ff("-i", self.gray, "-c", "copy", *meta, out)
        return out

    def test_privacy_passes_a_clean_file_and_names_no_value(self):
        r = _row(qc.run(self.gray), "privacy")
        self.assertEqual(r["status"], qc.PASS, r)

    def test_privacy_warns_on_each_planted_tag_and_prints_keys_only(self):
        for tag in ("location=+41.0000-071.0000/", "serial_number=SN123456", "artist=Some Person",
                    "comment=exported from /Users/someone/clip.mov", "title=write me at a@example.org"):
            r = _row(qc.run(self.tagged("t.mkv", tag)), "privacy")
            self.assertEqual(r["status"], qc.WARN, (tag, r))
            self.assertNotIn(tag.split("=", 1)[1], r["found"] + str(r.get("note")))
            self.assertIn(tag.split("=")[0], r["found"].lower())

    def test_privacy_ignores_a_plain_title_and_never_fails(self):
        r = qc.run(self.tagged("ok.mkv", "title=Example clip"))
        self.assertEqual(_row(r, "privacy")["status"], qc.PASS)
        planted = qc.run(self.tagged("p.mkv", "location=+41.0000-071.0000/"))
        self.assertEqual({c["status"] for c in planted["checks"] if c["check"] == "privacy"}, {qc.WARN})
        self.assertEqual(r["status"], planted["status"])  # a WARN never changes the outcome

    def test_mcp_qc_exposes_sync_colour_and_stabiliser(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("qc")
        props = tool.input_schema["properties"]
        for k in ("sync_ref", "colour", "stabiliser"):
            self.assertIn(k, props)
        args = {"file": self.gray, "colour": True, "stabiliser": True, "sync_ref": self.ref}
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        r = tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())
        rows = {c["check"]: c for c in r["checks"]}
        self.assertNotIn("not run", rows["colour"]["found"])
        self.assertNotIn("not run", rows["stabiliser"]["found"])
        self.assertNotEqual(rows["sync"]["found"], "no reference")
        plain = tool.handler(schema.with_defaults(tool.input_schema, {"file": self.gray}), ToolContext())
        self.assertEqual({c["check"]: c for c in plain["checks"]}["colour"]["found"], "not run")
        with self.assertRaises(Exception):
            tool.handler(schema.with_defaults(tool.input_schema, {"file": self.gray, "sync_ref": "rel.wav"}),
                         ToolContext())

    def test_cli_flags_add_the_rows(self):
        p = subprocess.run([sys.executable, str(WORKFLOW), "qc", self.gray, "--colour", "--stabiliser",
                            "--sync-ref", self.ref], capture_output=True, text=True,
                           env=dict(os.environ, RPRESOLVE_FFMPEG=dc.FFMPEG, RPRESOLVE_FFPROBE=dc.FFPROBE))
        self.assertEqual(p.stderr, "")  # exit 1 is a FAIL row (no audio here), not an error
        self.assertIn("colour", p.stdout)
        self.assertIn("stabiliser", p.stdout)
        self.assertIn("sync", p.stdout)


if __name__ == "__main__":
    unittest.main()
