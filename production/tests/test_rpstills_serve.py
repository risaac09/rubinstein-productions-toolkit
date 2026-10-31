"""
Tests for rpstills.serve, the read-only review file server: byte ranges (206,
Content-Range, 416), HEAD, MIME types, If-Range, keep-alive framing, and
refusal of anything outside the served folder; and that `rpstills serve`
starts without OpenCV. The server runs on an
ephemeral loopback port in a thread over a temporary folder; stdlib only,
no real media, no network beyond 127.0.0.1.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_serve
"""

import argparse
import http.client
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpstills import serve  # noqa: E402

DATA = (bytes(range(256)) * 4)[:1000]   # varied bytes, so an off-by-one slice shows up


class ParseRangeTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(serve.parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(serve.parse_range("bytes=900-", 1000), (900, 999))
        self.assertEqual(serve.parse_range("bytes=-10", 1000), (990, 999))
        self.assertEqual(serve.parse_range("bytes=-5000", 1000), (0, 999))
        self.assertEqual(serve.parse_range("bytes=990-5000", 1000), (990, 999))
        self.assertEqual(serve.parse_range("BYTES=0-0", 1000), (0, 0))

    def test_ignored_headers_serve_the_whole_file(self):
        for h in (None, "", "bytes=-", "bytes=0-1,5-9", "items=0-9", "bytes=9-3", "bytes=a-b", "bytes=+1-2"):
            self.assertIsNone(serve.parse_range(h, 1000), h)

    def test_unsatisfiable(self):
        for h, size in (("bytes=1000-", 1000), ("bytes=5000-6000", 1000), ("bytes=-0", 1000), ("bytes=-5", 0),
                        ("bytes=0-", 0)):
            with self.assertRaises(serve.RangeNotSatisfiable, msg=h):
                serve.parse_range(h, size)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rpstills-serve-")
        self.root = os.path.join(self.tmp, "review")
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "clip.mp4"), "wb") as f:
            f.write(DATA)
        for name in ("a.mov", "A.MOV", "a.wav", "a.m4a", "a.jpg", "a.png", "page.html", "a.json", "sub/b.jpg"):
            with open(os.path.join(self.root, name), "wb") as f:
                f.write(b"x" * 10)
        with open(os.path.join(self.tmp, "secret.txt"), "w") as f:
            f.write("SECRET-OUTSIDE-ROOT")
        os.symlink(os.path.join(self.tmp, "secret.txt"), os.path.join(self.root, "escape.txt"))
        os.symlink(self.tmp, os.path.join(self.root, "escape-dir"))
        os.makedirs(os.path.join(self.root, "idx"))
        os.symlink(os.path.join(self.tmp, "secret.txt"), os.path.join(self.root, "idx", "index.html"))
        os.makedirs(os.path.join(self.root, "page"))
        with open(os.path.join(self.root, "page", "index.html"), "wb") as f:
            f.write(DATA)
        quiet =mock.patch.object(serve.Handler, "log_message", lambda *a, **k: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = serve.make_server(self.root, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        shutil.rmtree(self.tmp)

    def conn(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(c.close)
        return c

    def get(self, path, method="GET", headers=None, conn=None):
        c = conn or self.conn()
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        return r, r.read()

    def test_binds_to_the_given_address(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        ap = argparse.ArgumentParser()
        serve.add_arguments(ap)
        a = ap.parse_args(["somewhere"])
        self.assertEqual((a.bind, a.port), ("127.0.0.1", serve.DEFAULT_PORT))

    def test_range_first_100_bytes(self):
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=0-99"})
        self.assertEqual(r.status, 206)
        self.assertEqual(body, DATA[:100])
        self.assertEqual(r.getheader("Content-Length"), "100")
        self.assertEqual(r.getheader("Content-Range"), "bytes 0-99/1000")
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(r.getheader("Content-Type"), "video/mp4")

    def test_open_ended_and_suffix_ranges(self):
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=900-"})
        self.assertEqual((r.status, body, r.getheader("Content-Range")), (206, DATA[900:], "bytes 900-999/1000"))
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=-10"})
        self.assertEqual((r.status, body), (206, DATA[-10:]))
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=990-5000"})
        self.assertEqual((r.status, body, r.getheader("Content-Range")), (206, DATA[990:], "bytes 990-999/1000"))

    def test_range_past_the_end_is_416(self):
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=1000-"})
        self.assertEqual(r.status, 416)
        self.assertEqual(r.getheader("Content-Range"), "bytes */1000")
        self.assertEqual(body, b"")

    def test_multi_range_and_no_range_send_the_whole_file(self):
        for headers in ({}, {"Range": "bytes=0-9,20-29"}):
            r, body = self.get("/clip.mp4", headers=headers)
            self.assertEqual((r.status, body), (200, DATA))
            self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
            self.assertEqual(r.getheader("Cache-Control"), "no-cache")

    def test_head(self):
        r, body = self.get("/clip.mp4", method="HEAD")
        self.assertEqual(r.status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(r.getheader("Content-Length"), "1000")
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(r.getheader("Content-Type"), "video/mp4")
        self.assertTrue(r.getheader("ETag"))
        r, body = self.get("/clip.mp4", method="HEAD", headers={"Range": "bytes=0-99"})
        self.assertEqual((r.status, body, r.getheader("Content-Length")), (206, b"", "100"))

    def test_if_range(self):
        r, _ = self.get("/clip.mp4", method="HEAD")
        etag, modified = r.getheader("ETag"), r.getheader("Last-Modified")
        for validator in (etag, modified):
            r, body = self.get("/clip.mp4", headers={"Range": "bytes=0-9", "If-Range": validator})
            self.assertEqual((r.status, body), (206, DATA[:10]))
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=0-9", "If-Range": '"stale"'})
        self.assertEqual((r.status, body), (200, DATA))

    def test_keep_alive_framing_across_requests(self):
        c = self.conn()
        r1, b1 = self.get("/clip.mp4", headers={"Range": "bytes=0-99"}, conn=c)
        r2, b2 = self.get("/clip.mp4", headers={"Range": "bytes=100-199"}, conn=c)
        r3, b3 = self.get("/clip.mp4", conn=c)
        self.assertEqual((r1.status, r2.status, r3.status), (206, 206, 200))
        self.assertEqual((b1, b2, b3), (DATA[:100], DATA[100:200], DATA))

    def test_mime_types(self):
        want = {"/a.mov": "video/quicktime", "/A.MOV": "video/quicktime", "/a.wav": "audio/wav",
                "/a.m4a": "audio/mp4", "/a.jpg": "image/jpeg", "/a.png": "image/png",
                "/page.html": "text/html; charset=utf-8", "/a.json": "application/json",
                "/sub/b.jpg": "image/jpeg"}
        for path, ctype in want.items():
            r, _ = self.get(path, method="HEAD")
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, ctype), path)

    def test_path_traversal_is_refused(self):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/sub/../../secret.txt", "/sub/%2E%2E/%2e%2e/secret.txt",
                     "/..%2fsecret.txt", "/escape.txt", "/escape-dir/secret.txt", "/sub/..\\..\\secret.txt",
                     "/idx/", "/idx/index.html", "/escape-dir/"):
            for method in ("GET", "HEAD"):
                r, body = self.get(path, method=method)
                self.assertEqual(r.status, 403, (method, path))
                self.assertNotIn(b"SECRET", body)

    def test_the_blind_key_is_never_served(self):
        for rel in ("key.json", "sub/key.json", "KEY.JSON"):
            with open(os.path.join(self.root, rel), "w") as f:
                f.write("{}")
            for method in ("GET", "HEAD"):
                self.assertEqual(self.get("/" + rel, method=method)[0].status, 404, (method, rel))

    def test_a_foreign_host_header_is_refused(self):
        self.assertEqual(self.get("/clip.mp4", headers={"Host": "evil.example:80"})[0].status, 421)
        self.assertEqual(self.get("/clip.mp4", headers={"Host": "localhost:%d" % self.port})[0].status, 200)
        self.assertEqual(self.get("/clip.mp4", headers={"Host": "127.0.0.1:%d" % self.port})[0].status, 200)
        self.assertEqual(self.get("/clip.mp4", headers={"Host": "192.168.1.5"})[0].status, 421)

    def test_a_huge_range_header_is_ignored_not_a_crash(self):
        r, body = self.get("/clip.mp4", headers={"Range": "bytes=0-" + "9" * 6000})
        self.assertEqual((r.status, len(body)), (200, len(DATA)))

    def test_inside_paths_work_and_any_dotdot_is_refused(self):
        r, body = self.get("/sub/../clip.mp4", headers={"Range": "bytes=0-3"})
        self.assertEqual(r.status, 403)   # browsers resolve .. before sending, so only a crafted request has one
        r, body = self.get("/sub/b.jpg")
        self.assertEqual((r.status, body), (200, b"x" * 10))
        r, body = self.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"clip.mp4", body)
        r, _ = self.get("/missing.mp4")
        self.assertEqual(r.status, 404)

    def test_folder_index_is_served_like_a_file(self):
        r, body = self.get("/page/", headers={"Range": "bytes=0-3"})
        self.assertEqual((r.status, body, r.getheader("Content-Range")), (206, DATA[:4], "bytes 0-3/1000"))
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        self.assertEqual(r.getheader("Content-Type"), "text/html; charset=utf-8")
        r, _ = self.get("/page")
        self.assertEqual((r.status, r.getheader("Location")), (301, "/page/"))

    def test_unencodable_paths_get_403_and_the_server_stays_up(self):
        for path in ("/clip.mp4%00", "/%00", "/a%00/", "/%ED%A0%80", "/%ED%A0%80/"):
            for method in ("GET", "HEAD"):
                r, _ = self.get(path, method=method)
                self.assertEqual(r.status, 403, (method, path))
        r, body = self.get("/clip.mp4")
        self.assertEqual((r.status, body), (200, DATA))

    def test_no_post_or_put(self):
        for method in ("POST", "PUT", "DELETE"):
            r, _ = self.get("/clip.mp4", method=method)
            self.assertEqual(r.status, 501, method)
        with open(os.path.join(self.root, "clip.mp4"), "rb") as f:
            self.assertEqual(f.read(), DATA)

    def test_refuses_a_missing_root(self):
        with self.assertRaises(NotADirectoryError):
            serve.make_server(os.path.join(self.tmp, "nope"), "127.0.0.1", 0)
        ap = argparse.ArgumentParser()
        serve.add_arguments(ap)
        with mock.patch("sys.stderr"):
            self.assertEqual(serve.run(ap.parse_args([os.path.join(self.tmp, "nope"), "--port", "0"])), 1)

    def test_port_out_of_range_is_a_one_line_error(self):
        ap = argparse.ArgumentParser()
        serve.add_arguments(ap)
        for port in ("70000", "-1"):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(serve.run(ap.parse_args([self.root, "--port", port])), 1)
            self.assertTrue(err.getvalue().startswith("serve: "), err.getvalue())


class CliWithoutOpenCVTest(unittest.TestCase):
    """The shoot commands and serve load no module that needs OpenCV; only develop, finish and audit do."""

    def test_commands_parse_with_cv2_blocked(self):
        code = ("import sys; sys.modules['cv2'] = None\n"
                "from rpstills.__main__ import main\n"
                "for cmd in (['serve', '--help'], ['cull', '--help'], ['--help']):\n"
                "    try:\n"
                "        main(cmd)\n"
                "    except SystemExit as e:\n"
                "        assert e.code == 0, (cmd, e.code)\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parent.parent),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--bind", r.stdout)
        self.assertIn("serve", r.stdout.rsplit("usage: rpstills", 1)[-1])


if __name__ == "__main__":
    unittest.main()
