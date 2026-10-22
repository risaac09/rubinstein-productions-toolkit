"""
Tests for tools/ipad-probe: probe.sh and the four recipes (verify-copy,
proxy, loudness, sheet), run for real under sh, and under dash where it is
installed (dash is a-Shell's shell), on media made here with ffmpeg lavfi.
A fake ffmpeg on the command line proves the probe's "absent" rows come
from ffmpeg's own lists. stdlib only; ffmpeg and ffprobe from
RPRESOLVE_FFMPEG / RPRESOLVE_FFPROBE, else PATH, else Homebrew.

Run: /usr/bin/python3 -m unittest production.tests.test_ipad_probe -v
"""

import hashlib
import importlib.util
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "tools" / "ipad-probe"
PROBE = KIT / "probe.sh"
RECIPES = KIT / "recipes"
SCRIPTS = [PROBE] + sorted(RECIPES.glob("*.sh"))

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("RPRESOLVE_FFPROBE") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
HAVE_FF = all(os.access(t, os.X_OK) for t in (FFMPEG, FFPROBE))
needs_ff = unittest.skipUnless(HAVE_FF, "ffmpeg missing")

SH = "/bin/sh"
DASH = shutil.which("dash")
SHELLS = [SH] + ([DASH] if DASH else [])
RECIPE_SHELL = DASH or SH   # a-Shell runs dash, so the recipes are run under it when it is here

# The rows the probe must print, from the build spec.
FILTERS = ("lut3d lut1d haldclut zscale colorspace tonemap ebur128 loudnorm afftdn adeclick "
           "sidechaincompress acrossfade silencedetect scdet signalstats waveform vectorscope "
           "subtitles drawtext").split()
ENCODERS = ("h264_videotoolbox hevc_videotoolbox libx264 libx265 libkvazaar libsvtav1 "
            "prores_ks aac").split()
ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def run(shell, script, *args, env=None, cwd=None):
    e = dict(os.environ)
    e.update({"FFMPEG": FFMPEG, "FFPROBE": FFPROBE, "PYTHON": sys.executable})
    e.update(env or {})
    e = {k: v for k, v in e.items() if v is not None}   # None in env removes the variable
    return subprocess.run([shell, str(script)] + [str(a) for a in args], env=e, cwd=cwd,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, timeout=300)


def table(stdout):
    """{(kind, name): (result, detail)} from the probe's Markdown table."""
    rows = {}
    for line in stdout.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| ") and len(cells) == 4 and cells[0] not in ("kind", "---"):
            rows[(cells[0], cells[1])] = (cells[2], cells[3])
    return rows


def ff(*args):
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y"] + [str(a) for a in args],
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return p


def probe_streams(path, entries="stream=codec_type,codec_name,width,height,sample_rate,pix_fmt"):
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", entries, "-of", "compact=p=0:nk=0",
                        str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, check=True)
    out = []
    for line in p.stdout.splitlines():
        out.append(dict(kv.split("=", 1) for kv in line.split("|") if "=" in kv))
    return out


def ffmpeg_names(flag):
    p = subprocess.run([FFMPEG, "-hide_banner", flag], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    return {ln.split()[1] for ln in p.stdout.splitlines() if len(ln.split()) >= 2}


def integrated_lufs(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-i", str(path), "-map", "0:a:0",
                        "-af", "ebur128", "-f", "null", "-"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return float(re.findall(r"^\s*I:\s*(-?[\d.]+) LUFS", p.stderr, re.M)[-1])


def stub(folder, name, body):
    path = Path(folder) / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


class TestScripts(unittest.TestCase):
    def test_posix_sh_shebang_and_syntax(self):
        self.assertGreaterEqual(len(SCRIPTS), 5)
        for s in SCRIPTS:
            self.assertEqual(s.read_text().splitlines()[0], "#!/bin/sh", s.name)
            for shell in SHELLS:
                p = subprocess.run([shell, "-n", str(s)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True)
                self.assertEqual(p.returncode, 0, f"{shell} -n {s.name}: {p.stderr}")

    def test_self_test(self):
        for shell in SHELLS:
            p = run(shell, PROBE, "--self-test", env={"FFMPEG": "/nonexistent/ffmpeg"})
            self.assertEqual(p.returncode, 0, f"{shell}: {p.stderr}")
            self.assertRegex(p.stdout, r"self-test: ok \(\d+ checks\)")

    def test_usage(self):
        for args in (["a", "b"], ["--bogus"], ["/nonexistent/clip.mov"]):
            p = run(SH, PROBE, *args)
            self.assertEqual(p.returncode, 2, args)


class TestProbeFake(unittest.TestCase):
    """A fake ffmpeg that lists two filters and one encoder and can run
    nothing: every other row must say absent, and no smoke run may pass."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.ffmpeg = stub(self.d, "ffmpeg", """case " $* " in
  *" -version "*) echo "ffmpeg version n5.1.2-fake Copyright (c) the FFmpeg developers"
                  echo "configuration: --disable-gpl --enable-videotoolbox --enable-libzimg"; exit 0 ;;
  *" -filters "*) printf '%s\\n' 'Filters:' '  T.. = Timeline support' '  ------' \\
                    ' TSC lut3d   V->V  Adjust colors using a 3D LUT.' \\
                    ' ... loudnorm  A->A  EBU R128 loudness normalization'; exit 0 ;;
  *" -encoders "*) printf '%s\\n' 'Encoders:' ' ------' ' A....D aac   AAC (Advanced Audio Coding)'; exit 0 ;;
esac
echo "fake ffmpeg: cannot run this" >&2
exit 1
""")
        self.ffprobe = stub(self.d, "ffprobe", "exit 1\n")

    def probe(self, shell):
        p = run(shell, PROBE, env={"FFMPEG": str(self.ffmpeg), "FFPROBE": str(self.ffprobe),
                                   "PYTHON": "/nonexistent/python3", "TMPDIR": self.d})
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout, table(p.stdout)

    def test_absent_rows_follow_ffmpeg_lists(self):
        for shell in SHELLS:
            out, rows = self.probe(shell)
            for f in FILTERS:
                want = "present" if f in ("lut3d", "loudnorm") else "absent"
                self.assertEqual(rows[("filter", f)][0], want, f"{shell} {f}")
            for e in ENCODERS:
                self.assertEqual(rows[("encoder", e)][0], "present" if e == "aac" else "absent", e)
            self.assertEqual(rows[("version", "ffmpeg")][0], "n5.1.2-fake")
            self.assertEqual(rows[("version", "ffprobe")][0], "not found")
            self.assertEqual(rows[("version", "python3")][0], "not found")
            self.assertEqual(rows[("build", "gpl")][0], "not enabled")
            self.assertEqual(rows[("build", "videotoolbox")][0], "enabled")
            self.assertEqual(rows[("build", "libzimg")][0], "enabled")
            self.assertEqual(rows[("build", "hwaccel videotoolbox")][0], "not listed")
            self.assertEqual(rows[("smoke", "lavfi")][0], "fail")
            for s in ("lut3d", "loudnorm", "ebur128", "scdet", "ffprobe", "h264_videotoolbox"):
                self.assertEqual(rows[("smoke", s)][0], "skipped", s)
            self.assertIn("summary: filters 2/19 present, encoders 1/8 present, smoke 0/1 pass", out)
            self.assertEqual(sorted(os.listdir(self.d)), ["ffmpeg", "ffprobe"], "scratch folder left behind")

    def test_stub_found_on_path(self):
        """With FFMPEG and FFPROBE unset the probe calls plain `ffmpeg`, so the
        stub first on PATH is what it reads."""
        p = run(DASH or SH, PROBE, env={"FFMPEG": None, "FFPROBE": None, "TMPDIR": self.d,
                                        "PATH": self.d + os.pathsep + os.environ.get("PATH", "")})
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = table(p.stdout)
        self.assertEqual(rows[("version", "ffmpeg")], ("n5.1.2-fake", str(self.ffmpeg)))
        self.assertEqual(rows[("filter", "zscale")][0], "absent")
        self.assertEqual(rows[("encoder", "libx264")][0], "absent")
        self.assertEqual(rows[("filter", "lut3d")][0], "present")

    def test_no_ffmpeg_at_all(self):
        p = run(SH, PROBE, env={"FFMPEG": "/nonexistent/ffmpeg", "FFPROBE": "/nonexistent/ffprobe",
                                "TMPDIR": self.d})
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = table(p.stdout)
        self.assertEqual(rows[("version", "ffmpeg")][0], "not found")
        self.assertTrue(all(rows[("filter", f)][0] == "absent" for f in FILTERS))
        self.assertNotIn("ffmpeg configuration:", p.stdout)


@needs_ff
class TestProbeReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.out = {}
        for shell in SHELLS:
            p = run(shell, PROBE, env={"TMPDIR": cls.d})
            assert p.returncode == 0, p.stderr
            cls.out[shell] = p.stdout
        cls.rows = table(cls.out[SH])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def test_rows_match_ffmpeg_lists(self):
        filters, encoders = ffmpeg_names("-filters"), ffmpeg_names("-encoders")
        for f in FILTERS:
            self.assertEqual(self.rows[("filter", f)][0], "present" if f in filters else "absent", f)
        for e in ENCODERS:
            self.assertEqual(self.rows[("encoder", e)][0], "present" if e in encoders else "absent", e)

    def test_hwaccel_row(self):
        p = subprocess.run([FFMPEG, "-hide_banner", "-hwaccels"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True)
        listed = "videotoolbox" in p.stdout.split()
        self.assertEqual(self.rows[("build", "hwaccel videotoolbox")][0], "listed" if listed else "not listed")

    def test_smoke_runs_pass(self):
        for s in ("lavfi", "lut3d", "loudnorm", "ebur128", "scdet", "ffprobe"):
            self.assertEqual(self.rows[("smoke", s)][0], "pass", f"{s}: {self.rows[('smoke', s)][1]}")
        self.assertIn("50 frames", self.rows[("smoke", "lut3d")][1])
        self.assertIn("one cut at 1", self.rows[("smoke", "scdet")][1])
        vt = "h264_videotoolbox" in ffmpeg_names("-encoders")
        self.assertEqual(self.rows[("smoke", "h264_videotoolbox")][0], "pass" if vt else "skipped")

    def test_python_rows(self):
        self.assertEqual(self.rows[("version", "python3")][0], sys.version.split()[0])
        for m in ("numpy", "scipy", "PIL", "cv2"):
            want = "present" if importlib.util.find_spec(m) else "absent"
            self.assertEqual(self.rows[("python", m)][0], want, m)
        result, detail = self.rows[("python", "subprocess ffprobe")]
        self.assertEqual(result, "pass", detail)
        self.assertIn("isfile=True", detail)

    def test_tool_rows(self):
        for t in ("awk", "sed", "grep", "sort", "tail", "cut", "tr", "wc", "mkdir -p, cp -p", "find",
                  "python3 hashlib"):
            self.assertEqual(self.rows[("tool", t)][0], "works", t)
        self.assertIn("works", (self.rows[("tool", "sha256sum")][0], self.rows[("tool", "shasum -a 256")][0]))

    def test_summary_configuration_and_cleanup(self):
        out = self.out[SH]
        self.assertRegex(out, r"summary: filters \d+/19 present, encoders \d+/8 present, smoke \d+/\d+ pass, "
                              r"python modules \d/4 present")
        self.assertIn("ffmpeg configuration:", out)
        self.assertEqual(os.listdir(self.d), [], "scratch folder left behind")

    def test_dash_and_sh_agree(self):
        if not DASH:
            self.skipTest("dash not installed")
        strip = {k: v[0] for k, v in table(self.out[DASH]).items()}
        self.assertEqual(strip, {k: v[0] for k, v in self.rows.items()})

    def test_clip_rows(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "-clip.mp4"   # a leading dash must not read as an option
            ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2", "-f", "lavfi", "-i", "sine=r=48000:d=2",
               "-c:v", "mpeg4", "-c:a", "aac", "-shortest", "file:" + str(clip))
            p = run(SH, PROBE, clip.name, env={"TMPDIR": d}, cwd=d)
            self.assertEqual(p.returncode, 0, p.stderr)
            rows = table(p.stdout)
            self.assertEqual(rows[("clip", "stream")][0], "read")
            self.assertIn("codec_name=mpeg4", rows[("clip", "stream")][1])
            self.assertEqual(rows[("clip", "audio streams")][0], "1")
            # ffmpeg prints speed with %4.3g, so a decode past 1000x reads 1.03e+03x
            self.assertRegex(rows[("clip", "decode 5 s, software")][0], r"^[\d.]+(e\+\d+)?x$")
            self.assertIn(("clip", "decode 5 s, videotoolbox"), rows)


class TestVerifyCopy(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        rng = random.Random(7)
        self.card = self.d / "card"
        self.files = {"DCIM/100 X/A 1.MOV": bytes(rng.getrandbits(8) for _ in range(300_000)),
                      "DCIM/100 X/A 2.MOV": b"",
                      "-odd name.txt": b"leading dash\n",
                      "PRIVATE/M4ROOT/CLIP.XML": b"<x/>\n"}
        for rel, data in self.files.items():
            (self.card / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.card / rel).write_bytes(data)
        for junk in (".DS_Store", "DCIM/._A 1.MOV", ".Spotlight-V100/store", ".Trashes/501/x", ".fseventsd/f"):
            (self.card / junk).parent.mkdir(parents=True, exist_ok=True)
            (self.card / junk).write_bytes(b"junk")
        self.env = {"TMPDIR": str(self.d)}
        self.before = self.snapshot(self.card)

    def snapshot(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def vc(self, *dests, shell=RECIPE_SHELL, env=None):
        e = dict(self.env)
        e.update(env or {})
        return run(shell, RECIPES / "verify-copy.sh", self.card, *dests, env=e)

    def check_copy(self, dest):
        self.assertEqual(self.snapshot(dest), self.files)
        lines = (dest.parent / (dest.name + ".SHA256SUMS")).read_text().splitlines()
        want = [f"{hashlib.sha256(self.files[rel]).hexdigest()}  {rel}" for rel in sorted(self.files)]  # C-locale path order
        self.assertEqual(lines, want)

    def test_two_copies_verify(self):
        for shell in SHELLS:
            c1, c2 = self.d / f"c1-{Path(shell).name}", self.d / f"c2-{Path(shell).name}"
            c2.mkdir()   # an empty folder is accepted
            p = self.vc(c1, str(c2) + "/", shell=shell)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.check_copy(c1)
            self.check_copy(c2)
            self.assertIn("verified: " + str(c1), p.stdout)
            self.assertIn("result: 2 verified copies", p.stdout)
        self.assertEqual(self.snapshot(self.card), self.before, "the source changed")

    def test_one_copy_asks_for_a_second(self):
        p = self.vc(self.d / "only")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Make a second, on another device, before formatting the card.", p.stdout)

    def test_corrupt_copy_fails(self):
        cp = stub(self.d, "cp-corrupt", 'cp "$@" || exit 1\nfor last; do :; done\nprintf x >> "$last"\n')
        p = self.vc(self.d / "bad", env={"CP": str(cp)})
        self.assertEqual(p.returncode, 1)
        self.assertIn("MISMATCH", p.stderr)
        self.assertIn("FAILED: ", p.stdout)
        self.assertIn("0 of 1 copies verified", p.stdout)
        self.assertEqual(self.snapshot(self.card), self.before)

    def test_python_hash_when_no_sha_tools(self):
        """a-Shell may have neither sha256sum nor shasum: a PATH holding only
        the commands the recipe needs, without those two, must still verify."""
        bin_ = self.d / "bin"
        bin_.mkdir()
        for tool in ("awk", "cp", "cut", "find", "ls", "mkdir", "rm", "sort", "tr", "wc"):
            os.symlink(shutil.which(tool), bin_ / tool)
        p = self.vc(self.d / "py", env={"PATH": str(bin_)})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("(python)", p.stdout)
        self.check_copy(self.d / "py")

    def test_refusals(self):
        full = self.d / "full"
        full.mkdir()
        (full / "x").write_text("x")
        (self.d / "has.SHA256SUMS").write_text("")
        cases = [[], [self.d / "missing-parent" / "c"], [full], [self.card / "inside"], [self.card],
                 [self.d / "twice", str(self.d / "twice") + "/"], [self.d / "has"]]
        for dests in cases:
            p = self.vc(*dests) if dests else run(RECIPE_SHELL, RECIPES / "verify-copy.sh", self.card)
            self.assertEqual(p.returncode, 2, f"{dests}: {p.stdout} {p.stderr}")
        self.assertFalse((self.d / "missing-parent").exists())
        self.assertFalse((self.d / "twice").exists())
        self.assertEqual(self.snapshot(self.card), self.before)
        p = run(RECIPE_SHELL, RECIPES / "verify-copy.sh", self.d / "no-card", self.d / "c", env=self.env)
        self.assertEqual(p.returncode, 2)

    def vc_at(self, cwd, *args, env=None):
        e = dict(self.env)
        e.update(env or {})
        return run(RECIPE_SHELL, RECIPES / "verify-copy.sh", *args, env=e, cwd=cwd)

    def test_other_spellings_are_refused(self):
        """Folders are compared by physical path: no spelling puts a DEST
        inside the card or counts one folder twice."""
        os.symlink("card/DCIM", self.d / "dcimlink")
        d, card = str(self.d), str(self.card)
        cases = [(d, ["card", "./card/inside"]), (d, ["./card", "card/inside"]),
                 (d, [card, "card/inside"]), (d, ["card", card + "/inside"]),
                 (card, [".", "backup"]), (card, [card, "backup"]), (d, ["card", "dcimlink/x"]),
                 (d, ["card", "copy1", "./copy1"]), (d, ["card", "copy1", d + "/copy1"])]
        for cwd, args in cases:
            p = self.vc_at(cwd, *args)
            self.assertEqual(p.returncode, 2, f"{cwd} {args}: {p.stdout} {p.stderr}")
        self.assertEqual(self.snapshot(self.card), self.before, "something was written into the card")
        self.assertFalse((self.d / "copy1").exists())

    def test_alias_found_at_copy_time_is_not_counted(self):
        """A DEST that only becomes the same folder once an earlier DEST exists
        (here a dangling link to it) is skipped, not counted as a second copy."""
        (self.d / "ssd").mkdir()
        os.symlink("day1", self.d / "ssd" / "link")
        p = self.vc(self.d / "ssd" / "day1", self.d / "ssd" / "link")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("is the same folder as an earlier DEST; not counted", p.stdout)
        self.assertIn("result: 1 verified copy.", p.stdout)
        self.check_copy(self.d / "ssd" / "day1")

    def test_failed_file_counted_once(self):
        cp = stub(self.d, "cp-fail-xml", 'case "$2" in *CLIP.XML) exit 1 ;; esac\nexec cp "$@"\n')
        p = self.vc(self.d / "one", env={"CP": str(cp)})
        self.assertEqual(p.returncode, 1)
        self.assertIn("copy failed: PRIVATE/M4ROOT/CLIP.XML", p.stderr)
        self.assertIn(f"(1 of {len(self.files)} files missing or different)", p.stdout)

    def test_symlinked_source_and_leading_dashes(self):
        os.symlink("card", self.d / "cardlink")
        p = self.vc_at(self.d, "cardlink/", "viaLink")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.check_copy(self.d / "viaLink")
        shutil.copytree(self.card, self.d / "-card")
        p = self.vc_at(self.d, "-card", "-out")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.check_copy(self.d / "-out")

    def test_git_tree_refused(self):
        (self.d / "repo" / ".git").mkdir(parents=True)
        (self.d / "repo" / "sub").mkdir()
        p = self.vc(self.d / "repo" / "sub" / "day1")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("git work tree", p.stderr)
        self.assertEqual(os.listdir(self.d / "repo" / "sub"), [])


@needs_ff
class TestProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.land = cls.d / "land.mov"
        ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-f", "lavfi", "-i", "sine=r=48000:d=2",
           "-c:v", "mpeg4", "-c:a", "aac", "-shortest", cls.land)
        cls.port = cls.d / "port.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=25:d=1", "-c:v", "mpeg4", cls.port)
        cls.small = cls.d / "small.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=1", "-c:v", "mpeg4", cls.small)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def proxy(self, src, name, *extra, env=None):
        out = self.d / name
        p = run(RECIPE_SHELL, RECIPES / "proxy.sh", src, out, *extra, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return out, p

    def video(self, path):
        return [s for s in probe_streams(path) if s["codec_type"] == "video"][0]

    def test_landscape_with_audio(self):
        out, p = self.proxy(self.land, "land-proxy.mp4")
        v = self.video(out)
        self.assertEqual((v["codec_name"], v["width"], v["height"], v["pix_fmt"]), ("h264", "1280", "720", "yuv420p"))
        self.assertIn("aac", [s["codec_name"] for s in probe_streams(out) if s["codec_type"] == "audio"])
        self.assertRegex(p.stdout, r"\((h264_videotoolbox|libx264), -b:v 6M")

    def test_portrait_and_long_edge(self):
        out, _ = self.proxy(self.port, "port-proxy.mp4", "3M", "960")
        v = self.video(out)
        self.assertEqual((v["width"], v["height"]), ("540", "960"))

    def test_small_is_not_upscaled_and_has_no_audio(self):
        out, _ = self.proxy(self.small, "small-proxy.mp4")
        streams = probe_streams(out)
        self.assertEqual([(s["width"], s["height"]) for s in streams], [("320", "240")])

    def test_mpeg4_when_h264_encoders_are_absent(self):
        """Also the software-decode path: this ffmpeg lists no hwaccels."""
        wrap = stub(self.d, "ff-no-h264", f"""case " $* " in
  *" -encoders "*) "{FFMPEG}" "$@" | grep -v -E ' (h264_videotoolbox|libx264) '; exit 0 ;;
  *" -hwaccels "*) echo "Hardware acceleration methods:"; exit 0 ;;
esac
exec "{FFMPEG}" "$@"
""")
        out, p = self.proxy(self.land, "mpeg4-proxy.mp4", env={"FFMPEG": str(wrap)})
        self.assertEqual(self.video(out)["codec_name"], "mpeg4")
        self.assertEqual((self.video(out)["width"], self.video(out)["height"]), ("1280", "720"))
        self.assertNotIn("trying the next encoder", p.stderr)
        self.assertNotIn("decoded with videotoolbox", p.stdout)

    def test_listed_encoder_that_fails_falls_through(self):
        wrap = stub(self.d, "ff-broken-h264", f"""for a; do
  case "$a" in h264_videotoolbox|libx264) echo "refused $a" >&2; exit 1 ;; esac
done
exec "{FFMPEG}" "$@"
""")
        out, p = self.proxy(self.land, "fallback-proxy.mp4", env={"FFMPEG": str(wrap)})
        self.assertEqual(self.video(out)["codec_name"], "mpeg4")
        self.assertIn("trying the next encoder", p.stderr)

    def test_refuses_existing_output_and_bad_args(self):
        out = self.d / "exists.mp4"
        out.write_bytes(b"keep")
        for args in ([self.land, out], [self.land, self.d / "x.mp4", "6M", "wide"],
                     [self.land, self.d / "y.mp4", "fast"], [self.d / "none.mov", self.d / "z.mp4"], [self.land]):
            p = run(RECIPE_SHELL, RECIPES / "proxy.sh", *args)
            self.assertEqual(p.returncode, 2, args)
        self.assertEqual(out.read_bytes(), b"keep")


@needs_ff
class TestLoudness(unittest.TestCase):
    # A tone with a slow swell (LRA about 2 LU), at 44.1 kHz so the 48 kHz output is a real resample.
    SWELL = "aevalsrc=0.03*sin(2*PI*440*t)*(1+0.6*sin(2*PI*0.25*t)):s=44100:d=8"
    # Quiet, with a 2 ms full-scale click: one gain would push true peak far past TP.
    CLICK = "aevalsrc=0.01*sin(2*PI*440*t)*(1+0.6*sin(2*PI*0.25*t))+0.9*between(t\\,4\\,4.002):s=48000:d=8"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)

    def make(self, name, graph, *extra):
        path = self.d / name
        ff("-f", "lavfi", "-i", graph, *extra, path)
        return path

    def loud(self, src, name, *targets):
        out = self.d / name
        return out, run(RECIPE_SHELL, RECIPES / "loudness.sh", src, out, *targets)

    def test_linear_to_48k_wav(self):
        out, p = self.loud(self.make("swell.wav", self.SWELL, "-c:a", "pcm_s16le"), "swell-16.wav")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("normalization_type: linear", p.stdout)
        self.assertRegex(p.stdout, r"measured: I -[\d.]+ LUFS, TP -[\d.]+ dBTP")
        a = probe_streams(out)[0]
        self.assertEqual((a["codec_name"], a["sample_rate"]), ("pcm_s24le", "48000"))
        self.assertAlmostEqual(integrated_lufs(out), -16.0, delta=1.0)

    def test_dynamic_is_reported_with_why(self):
        out, p = self.loud(self.make("click.wav", self.CLICK, "-c:a", "pcm_s16le"), "click-16.wav")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("normalization_type: dynamic", p.stdout)
        self.assertRegex(p.stdout, r"why: one gain of \+[\d.]+ dB puts true peak at [\d.]+ dBTP \(TP -1.5\)")
        tp = float(re.search(r"result: I -?[\d.]+ LUFS, TP (-?[\d.]+) dBTP", p.stdout).group(1))
        self.assertLessEqual(tp, -1.0)

    def test_custom_target_and_video_copied(self):
        src = self.d / "clip.mov"
        ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=8", "-f", "lavfi", "-i", self.SWELL,
           "-c:v", "mpeg4", "-c:a", "aac", "-shortest", src)
        out, p = self.loud(src, "clip-23.mov", "-23", "-2", "15")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertAlmostEqual(integrated_lufs(out), -23.0, delta=1.0)
        md5 = [ff("-i", f, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-").stdout for f in (src, out)]
        self.assertEqual(md5[0], md5[1], "video stream changed")
        self.assertEqual(sorted(s["codec_type"] for s in probe_streams(out)), ["audio", "video"])

    def test_silence_and_refusals(self):
        out, p = self.loud(self.make("silent.wav", "anullsrc=r=48000:cl=mono:d=3", "-c:a", "pcm_s16le"),
                           "silent-16.wav")
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing measurable", p.stderr)
        self.assertFalse(out.exists())
        src = self.make("swell2.wav", self.SWELL, "-c:a", "pcm_s16le")
        _, p = self.loud(src, "x.wav", "loud")
        self.assertEqual(p.returncode, 2)
        (self.d / "taken.wav").write_bytes(b"keep")
        _, p = self.loud(src, "taken.wav")
        self.assertEqual(p.returncode, 2)
        self.assertEqual((self.d / "taken.wav").read_bytes(), b"keep")

    def test_steady_tone_why_names_lra_zero(self):
        """loudnorm refuses one gain when the measured LRA is 0, before any
        TP or LRA test: the why line must say so, not quote numbers that pass."""
        src = self.make("tone.wav", "sine=f=1000:r=48000:d=8,volume=-20dB", "-c:a", "pcm_s16le")
        _, p = self.loud(src, "tone-16.wav")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("normalization_type: dynamic", p.stdout)
        self.assertIn("why: loudnorm uses one gain only when the measured LRA is above 0 LU", p.stdout)

    def test_wide_range_stays_linear_unless_lra_given(self):
        """Like delivercheck, the target LRA is lifted to the measured LRA + 1,
        so 5 s loud / 5 s at -22 dB keeps one gain; an LRA given is kept."""
        src = self.make("wide.wav", "aevalsrc=0.1*sin(2*PI*440*t)*if(lt(mod(t\\,10)\\,5)\\,1\\,0.0794):s=48000:d=40",
                        "-c:a", "pcm_s16le")
        out, p = self.loud(src, "wide-16.wav")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout, r"target LRA [\d.]+ LU: the measured LRA \+ 1")
        self.assertIn("normalization_type: linear", p.stdout)
        self.assertAlmostEqual(integrated_lufs(out), -16.0, delta=1.0)
        _, p = self.loud(src, "wide-lra11.wav", "-16", "-1.5", "11")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("normalization_type: dynamic", p.stdout)
        self.assertNotIn("target LRA", p.stdout)

    def test_failed_write_leaves_no_partial_output(self):
        """AAC cannot go in an .mp3: pass 2 fails after ffmpeg opened OUT, and
        the partial file must not block the next run."""
        src = self.make("swell3.wav", self.SWELL, "-c:a", "pcm_s16le")
        for _ in range(2):
            out, p = self.loud(src, "fail.mp3")
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            self.assertFalse(out.exists())

    def test_note_when_lossy_result_is_over_tp(self):
        """A square wave through AAC gains about 1.5 dB of true peak; PCM does not."""
        src = self.make("square.wav", "aevalsrc=0.25*(gt(sin(2*PI*997*t)\\,0)*2-1)*if(lt(mod(t\\,4)\\,2)\\,1\\,0.5)"
                        ":s=48000:d=8", "-c:a", "pcm_s16le")
        _, p = self.loud(src, "square.m4a", "-8", "-3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("normalization_type: linear", p.stdout)
        self.assertRegex(p.stdout, r"note: true peak -?[\d.]+ dBTP is over TP -3")
        _, p = self.loud(src, "square-out.wav", "-8", "-3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("note:", p.stdout)


@needs_ff
class TestSheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.land = cls.d / "land.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=24", "-c:v", "mpeg4", cls.land)
        cls.port = cls.d / "port.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=360x640:r=10:d=6", "-c:v", "mpeg4", cls.port)
        cls.long = cls.d / "long.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=5:d=60", "-c:v", "mpeg4", "-g", "5", cls.long)
        cls.sparse = cls.d / "sparse.mp4"   # a keyframe every 4 s: wider than a 2.5 s slot
        ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=60", "-c:v", "mpeg4", "-g", "40", cls.sparse)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def sheet(self, src, name, *grid, env=None):
        out = self.d / name
        p = run(RECIPE_SHELL, RECIPES / "sheet.sh", src, out, *grid, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        s = probe_streams(out)[0]
        return (int(s["width"]), int(s["height"])), p.stdout

    def test_landscape_default_grid(self):
        size, out = self.sheet(self.land, "land.jpg")
        self.assertEqual(size, (1920, 720))
        cells = [ln for ln in out.splitlines() if ln.startswith("cell ")]
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[0], "cell 1 (row 1, col 1) about 0:00:00.0")
        self.assertTrue(cells[-1].startswith("cell 24 (row 4, col 6) about 0:00:23"))
        self.assertNotIn("keyframes only", out)

    def test_every_cell_filled_at_2997(self):
        """Picking "at least an interval after the last pick" drifts by up to a
        frame per cell and left the last cell black on a 29.97 clip; one pick
        per time slot does not."""
        src = self.d / "ntsc.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30000/1001:d=7.5075", "-c:v", "mpeg4", src)
        size, _ = self.sheet(src, "ntsc.jpg")
        self.assertEqual(size, (1920, 720))
        last = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-i", str(self.d / "ntsc.jpg"),
                               "-vf", "crop=320:180:1600:540,format=gray", "-f", "rawvideo", "-"],
                              stdout=subprocess.PIPE, check=True).stdout
        self.assertGreater(sum(last) / len(last), 30, "the last cell is black")

    def test_software_decode(self):
        wrap = stub(self.d, "ff-no-hw", f"""case " $* " in
  *" -hwaccels "*) echo "Hardware acceleration methods:"; exit 0 ;;
esac
exec "{FFMPEG}" "$@"
""")
        size, _ = self.sheet(self.land, "land-sw.jpg", env={"FFMPEG": str(wrap)})
        self.assertEqual(size, (1920, 720))

    def test_portrait_custom_grid(self):
        size, _ = self.sheet(self.port, "port.jpg", "3", "2")
        self.assertEqual(size, (540, 640))

    def test_long_clip_reads_keyframes(self):
        size, out = self.sheet(self.long, "long.jpg")
        self.assertEqual(size, (1920, 720))
        self.assertIn("keyframes only", out)

    def test_skipped_slots_print_the_picked_times(self):
        """A slot with no keyframe moves later frames up a cell: the printed
        times are the picked frames' own, and the cells left over say empty."""
        keys = subprocess.run([FFPROBE, "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
                               "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(self.sparse)],
                              stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout.split()
        self.assertEqual([round(float(k)) for k in keys], list(range(0, 60, 4)), "fixture keyframes")
        _, out = self.sheet(self.sparse, "sparse.jpg")
        cells = [ln for ln in out.splitlines() if ln.startswith("cell ")]
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[1], "cell 2 (row 1, col 2) about 0:00:04.0")
        self.assertEqual(cells[14], "cell 15 (row 3, col 3) about 0:00:56.0")
        self.assertTrue(all("empty (black)" in c for c in cells[15:]), cells[15:])

    def test_minute_carries(self):
        """Times round once to tenths, so a cell at a minute reads 0:01:00.0, never 0:00:60.0."""
        src = self.d / "two-min.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=2:d=120", "-c:v", "mpeg4", "-g", "2", src)
        _, out = self.sheet(src, "two-min.jpg")
        self.assertIn("cell 13 (row 3, col 1) about 0:01:00.0", out)
        for sec in re.findall(r"about \d+:\d\d:(\d\d)\.\d$", out, re.M):
            self.assertLess(int(sec), 60)

    def test_percent_in_out_is_a_name(self):
        """ffmpeg's image2 muxer would read %d as a frame pattern and write over "day 1.jpg"."""
        keep = self.d / "day 1.jpg"
        keep.write_bytes(b"keep")
        self.sheet(self.port, "day %d.jpg", "2", "1")
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_refusals(self):
        taken = self.d / "taken.jpg"
        taken.write_bytes(b"keep")
        for args in ([self.land, taken], [self.land, self.d / "z.jpg", "0"], [self.land, self.d / "z.jpg", "6", "x"],
                     [self.d / "none.mp4", self.d / "z.jpg"]):
            p = run(RECIPE_SHELL, RECIPES / "sheet.sh", *args)
            self.assertEqual(p.returncode, 2, args)
        self.assertEqual(taken.read_bytes(), b"keep")


@needs_ff
class TestGitTree(unittest.TestCase):
    """Outputs never go inside a git work tree, however OUT is spelled."""

    def test_recipes_refuse_a_git_tree(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "repo" / ".git").mkdir(parents=True)
            (d / "repo" / "sub").mkdir()
            clip = d / "clip.mov"
            ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=2", "-f", "lavfi", "-i", "sine=r=48000:d=2",
               "-c:v", "mpeg4", "-c:a", "aac", "-shortest", clip)
            for script, name in (("proxy.sh", "p.mp4"), ("loudness.sh", "l.wav"), ("sheet.sh", "s.jpg")):
                for out, cwd in ((d / "repo" / "sub" / name, None), (Path("sub") / name, d / "repo"),
                                 (Path("..") / "sub" / name, d / "repo" / "sub")):
                    p = run(RECIPE_SHELL, RECIPES / script, clip, out, cwd=cwd)
                    self.assertEqual(p.returncode, 2, f"{script} {out}: {p.stdout} {p.stderr}")
                    self.assertIn("git work tree", p.stderr)
            self.assertEqual(os.listdir(d / "repo" / "sub"), [])
            # A path that only passes through the tree by its spelling is fine.
            p = run(RECIPE_SHELL, RECIPES / "sheet.sh", clip, Path("..") / "out.jpg", cwd=d / "repo")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue((d / "out.jpg").exists())


if __name__ == "__main__":
    unittest.main()
