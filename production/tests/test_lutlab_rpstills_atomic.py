"""
Offline tests that lutlab and rpstills write each output by temp name and rename: a hard link sitting at an
output name (a tracked file elsewhere) keeps its content instead of being written through. Also that
`rpstills look` refuses a git tree, and that serve will not hand out a hard link to the A/B key under
another name. Synthetic data only.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_lutlab_rpstills_atomic
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lutlab import cube, guard, model, pack, pipeline  # noqa: E402
from rpstills import __main__ as cli, index, look, serve  # noqa: E402
from rpstills.atomic import atomic_path  # noqa: E402

TRACKED = b"TRACKED CONTENT"


class Linked(unittest.TestCase):
    """A scratch folder with a 'tracked' folder beside the output folder, and a way to link into it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="atomic-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tracked = self.tmp / "tracked"
        self.tracked.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.n = 0

    def link(self, name, data=TRACKED):
        """Replace out/name with a hard link to a fresh tracked file."""
        self.n += 1
        t = self.tracked / f"t{self.n}"
        t.write_bytes(data)
        target = self.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        os.link(t, target)
        return t


class AtomicPathTest(unittest.TestCase):
    def test_both_helpers_leave_a_link_alone_and_clean_up_on_error(self):
        for ctx in (atomic_path, guard.atomic_path):
            with tempfile.TemporaryDirectory() as d:
                a, b = Path(d, "a.txt"), Path(d, "b.txt")
                a.write_bytes(TRACKED)
                os.link(a, b)
                with ctx(b) as tmp:
                    Path(tmp).write_text("new")
                self.assertEqual(a.read_bytes(), TRACKED)
                self.assertEqual(b.read_text(), "new")
                self.assertEqual(oct(b.stat().st_mode & 0o777), oct(0o666 & ~self._umask()))
                with self.assertRaises(RuntimeError):
                    with ctx(b) as tmp:
                        raise RuntimeError
                self.assertEqual(sorted(os.listdir(d)), ["a.txt", "b.txt"])

    def test_a_symlink_at_the_name_is_written_through_to_its_target(self):
        with tempfile.TemporaryDirectory() as d:
            real, link = Path(d, "real.txt"), Path(d, "link.txt")
            real.write_text("old")
            link.symlink_to(real)
            with atomic_path(link) as tmp:
                Path(tmp).write_text("new")
            self.assertTrue(link.is_symlink())
            self.assertEqual(real.read_text(), "new")
            self.assertFalse([f for f in os.listdir(d) if f.startswith(".part")])

    @staticmethod
    def _umask():
        m = os.umask(0)
        os.umask(m)
        return m


class LutlabWritersTest(Linked):
    def test_cube_and_hald_writers(self):
        t = cube.lattice(3)
        links = [self.link("a.cube"), self.link("h.png"), self.link("a.cube.part"), self.link("h.png.part")]
        cube.write_cube(str(self.out / "a.cube"), t, overwrite=True)
        cube.write_hald(str(self.out / "h.png"), lambda x: x, level=2, overwrite=True)
        self.assertEqual([p.read_bytes() for p in links], [TRACKED] * 4)   # the old fixed .part name wrote through
        self.assertEqual(cube.read_cube(str(self.out / "a.cube"))["table"].shape, (3, 3, 3, 3))
        self.assertTrue((self.out / "h.png").read_bytes().startswith(b"\x89PNG"))

    def test_decompose_outputs(self):
        p = model.blank("r709", "r709")
        p["tone"] = {"black": 0.02, "white": 0.97, "mid": 0.42, "contrast": 0.16, "toe_start": -2.0,
                     "toe_strength": 1.5, "shoulder_start": 1.5, "shoulder_strength": 3.0, "mode": "per_channel"}
        src = cube.write_cube(str(self.tmp / "src.cube"), np.clip(model.evaluate(p, cube.lattice(17)), 0, 1))
        pipeline.decompose(src, "r709", "r709", self.out, "synthetic")
        names = sorted(f.name for f in self.out.iterdir())
        self.assertIn("params.json", names)
        links = [self.link(n) for n in names]
        pipeline.decompose(src, "r709", "r709", self.out, "synthetic", overwrite=True)
        self.assertEqual([t.read_bytes() for t in links], [TRACKED] * len(names))
        self.assertEqual(json.loads((self.out / "params.json").read_text())["in"], "r709")
        self.assertTrue((self.out / "tone.png").read_bytes().startswith(b"\x89PNG"))

    def test_pack_zip(self):
        zname = "rp-luts-20260101.zip"
        pack.build(["s5ii"], self.out, size=33, date="20260101")
        link = self.link(zname)
        pack.build(["s5ii"], self.out, size=33, date="20260101", overwrite=True)
        self.assertEqual(link.read_bytes(), TRACKED)
        self.assertTrue((self.out / zname).read_bytes().startswith(b"PK"))
        self.assertEqual([f.name for f in self.out.iterdir()], [zname])


class RpstillsWritersTest(Linked):
    def test_look_cube(self):
        link = self.link("l.cube")
        stale = self.out / "l.cube.part"
        stale.write_text("stale")
        os.remove(self.out / "l.cube")
        look.write_cube(str(self.out / "l.cube"), {}, size=3)
        self.assertEqual(link.read_bytes(), TRACKED)
        self.assertEqual(stale.read_text(), "stale")
        self.assertIn("LUT_3D_SIZE 3", (self.out / "l.cube").read_text())

    def test_index_proxy_and_jsonl(self):
        shoot = self.tmp / "shoot"
        shoot.mkdir()
        Image.fromarray(np.full((60, 80, 3), 90, np.uint8)).save(shoot / "F1.jpg", "JPEG")
        proxies = self.out / "proxies"
        old = io.BytesIO()
        Image.fromarray(np.full((10, 10, 3), 200, np.uint8)).save(old, "JPEG")   # a real image of another source
        links = [self.link("proxies/F1.jpg", old.getvalue()), self.link("proxies/F1.jpg.part")]
        row = index.read_one(str(shoot / "F1.jpg"), str(proxies))
        self.assertIsNone(row["error"])
        self.assertEqual(links[0].read_bytes(), old.getvalue())
        self.assertEqual(links[1].read_bytes(), TRACKED)
        with Image.open(proxies / "F1.jpg") as im:
            self.assertEqual(im.size, (80, 60))
        rows = [{"id": "F1", "proxy": "proxies/F1.jpg", "faces": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}]}]
        links += [self.link("index.jsonl"), self.link("index.jsonl.part")]
        index.augment_face_hash(str(self.out), rows, log=lambda *a: None)
        links += [self.link("index.jsonl"), self.link("index.jsonl.part")]
        rows2 = [{"id": "F1", "proxy": "proxies/F1.jpg", "faces": []}]
        index.augment_frame_metrics(str(self.out), rows2, log=lambda *a: None)
        self.assertEqual([t.read_bytes() for t in links[1:]], [TRACKED] * 5)
        self.assertEqual(json.loads((self.out / "index.jsonl").read_text().splitlines()[0])["id"], "F1")

    def test_tiles(self):
        shoot = self.tmp / "shoot"
        shoot.mkdir()
        Image.fromarray(np.full((300, 400, 3), 90, np.uint8)).save(shoot / "F1.jpg", "JPEG")
        row = {"id": "F1", "jpeg": str(shoot / "F1.jpg")}
        boxes = index.tile_boxes(400, 300)
        links = [self.link(f"tiles/F1_t{k}.jpg") for k in range(len(boxes))]
        with mock.patch("rpstills.vision.faces", return_value={}):
            index.detect_tiles([row], str(self.out), workers=1, log=lambda *a: None)
        self.assertGreater(len(links), 0)
        self.assertEqual([t.read_bytes() for t in links], [TRACKED] * len(links))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_look_cli_refuses_a_git_tree_but_reads_a_tracked_params_file(self):
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        params = repo / "look.json"
        params.write_text(json.dumps({"params": {}, "name": "t"}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["look", str(params), str(repo / "x.cube")]), 1)
        self.assertIn("git working tree", err.getvalue())
        self.assertFalse((repo / "x.cube").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["look", str(params), str(self.out / "x.cube")]), 0)
        self.assertTrue((self.out / "x.cube").exists())


class ServeKeyLinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serve-key-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "review"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "ok.txt").write_text("fine")
        os.link(self.root / "ok.txt", self.root / "sub" / "also-ok.txt")   # a hard link that is not the key
        keydir = self.tmp / "review.key"
        keydir.mkdir()
        (keydir / "key.json").write_text('{"A": "secret"}')
        os.link(keydir / "key.json", self.root / "sub" / "notes.txt")      # the sibling key under another name
        (self.root / "inner").mkdir()
        (self.root / "inner" / "key.json").write_text('{"B": "secret"}')
        os.link(self.root / "inner" / "key.json", self.root / "renamed.json")  # a key inside the folder
        quiet = mock.patch.object(serve.Handler, "log_message", lambda *a, **k: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = serve.make_server(str(self.root), "127.0.0.1", 0)
        t = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        t.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            t.join(5)
        self.addCleanup(stop)

    def get(self, path):
        c = HTTPConnection("127.0.0.1", self.server.server_address[1])
        try:
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_a_hard_linked_key_is_not_served_under_any_name(self):
        for path in ("/sub/notes.txt", "/renamed.json", "/inner/key.json"):
            status, body = self.get(path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"secret", body)

    def test_other_files_are_still_served(self):
        self.assertEqual(self.get("/ok.txt"), (200, b"fine"))
        self.assertEqual(self.get("/sub/also-ok.txt"), (200, b"fine"))


if __name__ == "__main__":
    unittest.main()
