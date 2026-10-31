"""rpstills.serve: a read-only file server for review pages, proxies and audio excerpts.

  python -m rpstills serve <folder> [--bind 127.0.0.1] [--port 8780]
  python -m rpstills.serve <folder> ...            (the same, on any Python with no packages installed)

Python's own `http.server` answers a Range request with 200 and the whole
file. Safari on an iPad (and any WebKit player) needs 206 Partial Content to
play or seek a video, so a review page served that way plays in a desktop
browser and fails on the iPad. This server is the stdlib handler with byte
ranges added and nothing else:

- GET and HEAD only. Any other method gets 501; nothing is ever written.
- One root folder. A path that climbs out of it (`..`, encoded or not) or a
  symlink that resolves outside it gets 403.
- One byte range per request (`bytes=a-b`, `bytes=a-`, `bytes=-n`) -> 206
  with Content-Range. A multi-range or malformed header is ignored and the
  whole file is sent (RFC 9110 allows that); a range past the end gets 416.
  If-Range that does not match the file's ETag or Last-Modified sends the
  whole file, so a proxy re-rendered in place is never spliced with the old one.
- A Host header that is not the bound address or localhost gets 421 (DNS
  rebinding); any IP literal passes when bound to 0.0.0.0. A file named
  key.json is never served: the blind A/B key must not reach the reviewer.
- Fixed MIME types for the review formats (table below), so the answer does
  not depend on the machine's mime.types.
- Cache-Control: no-cache on every file, so a browser always asks again and a
  reviewer never judges a stale render that kept its name.
- Binds to 127.0.0.1 unless told otherwise. There is no login: bind to a
  private network address only, and serve only a folder of derivatives.

Stdlib only; runs on Python 3.9 and later as well as the rp-tools venv.
"""

import argparse
import functools
import ipaddress
import os
import re
import sys
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8780
COPY_CHUNK = 256 * 1024     # bytes per write while sending a file or a range
MAX_RANGE_HEADER = 64
HIDDEN_NAMES = ("key.json",)    # never served, wherever it sits under the root
IDLE_TIMEOUT_S = 60         # an idle keep-alive connection is closed after this

MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
}

_RANGE = re.compile(r"bytes=(\d*)-(\d*)", re.ASCII | re.IGNORECASE)


class RangeNotSatisfiable(ValueError):
    pass


def parse_range(header, size):
    """The single byte range a Range header asks for, as (start, end) inclusive.

    None means serve the whole file: no header, or one this server does not
    honour (another unit, several ranges, bad syntax, last before first).
    Raises RangeNotSatisfiable when the range starts at or past the end."""
    if len(header or "") > MAX_RANGE_HEADER:   # int() on thousands of digits raises; no real range is this long
        return None
    m = _RANGE.fullmatch((header or "").strip())
    if not m or m.groups() == ("", ""):
        return None
    first, last = m.groups()
    if first == "":                       # suffix range: the last n bytes
        n = int(last)
        if n == 0 or size == 0:
            raise RangeNotSatisfiable(header)
        return max(0, size - n), size - 1
    start = int(first)
    if last != "" and int(last) < start:
        return None
    if start >= size:
        raise RangeNotSatisfiable(header)
    return start, size - 1 if last == "" else min(int(last), size - 1)


class Handler(SimpleHTTPRequestHandler):
    server_version = "rpstills-serve"
    protocol_version = "HTTP/1.1"      # keep-alive; every response carries Content-Length
    timeout = IDLE_TIMEOUT_S
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map, **MIME)
    _remaining = None                  # bytes left to send for the current file response

    def _inside_root(self, path):
        try:
            bad = b"\x00" in os.fsencode(path)
        except ValueError:
            bad = True                 # a lone surrogate (%ED%A0%80) cannot be encoded
        if bad:                        # it, or an encoded NUL, names no file, and os calls raise on it
            return False
        raw = urllib.parse.unquote(self.path.split("?", 1)[0].split("#", 1)[0])
        if ".." in raw.replace("\\", "/").split("/"):
            return False
        root = os.path.realpath(self.directory)
        real = os.path.realpath(path)
        return real == root or real.startswith(root.rstrip(os.sep) + os.sep)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        bound = self.server.server_address[0]
        if not host or host in ("localhost", bound):
            return True
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return bound == "0.0.0.0"

    def send_head(self):
        self._remaining = None
        if not self._host_ok():
            self.send_error(HTTPStatus.MISDIRECTED_REQUEST, "Unexpected Host")
            return None
        path = self.translate_path(self.path)
        if os.path.basename(path) in HIDDEN_NAMES:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        if path.endswith("/") and os.path.isdir(path):
            # a folder URL's index page goes through the same root check, ranges and no-cache as any file
            path = next((p for p in (path + "index.html", path + "index.htm") if os.path.exists(p)), path)
        if not self._inside_root(path):
            self.send_error(HTTPStatus.FORBIDDEN, "Outside the served folder")
            return None
        if not os.path.isfile(path):   # folders (index.html, listing, redirect) and 404s
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        try:
            st = os.fstat(f.fileno())
            size = st.st_size
            etag = '"%x-%x"' % (st.st_mtime_ns, size)
            modified = self.date_time_string(st.st_mtime)
            rng = None
            if "Range" in self.headers and self.headers.get("If-Range", etag).strip() in (etag, modified):
                try:
                    rng = parse_range(self.headers["Range"], size)
                except RangeNotSatisfiable:
                    f.close()
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
            if rng is None:
                self.send_response(HTTPStatus.OK)
                length = size
            else:
                start, end = rng
                f.seek(start)
                length = end - start + 1
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", modified)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self._remaining = length
            return f
        except BaseException:
            f.close()
            raise

    def copyfile(self, source, outputfile):
        left = self._remaining
        if left is None:               # a folder listing from the stdlib handler
            return super().copyfile(source, outputfile)
        try:
            while left > 0:
                chunk = source.read(min(COPY_CHUNK, left))
                if not chunk:
                    break
                outputfile.write(chunk)
                left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            left = -1                  # players drop a range mid-send on every seek
        if left:
            # short or aborted: the promised Content-Length was not met, so this connection is done
            self.close_connection = True


def make_server(root, bind=DEFAULT_BIND, port=DEFAULT_PORT):
    """A threading server for `root`, bound and listening; port 0 picks a free one."""
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError("not a folder: %s" % root)
    return ThreadingHTTPServer((bind, port), functools.partial(Handler, directory=root))


def summary(server, root):
    host, port = server.server_address[:2]
    return "serving %s at http://%s:%d/ (GET and HEAD, byte ranges; Ctrl-C stops)" % (root, host, port)


def add_arguments(p):
    p.add_argument("root", help="the one folder to serve; nothing outside it is reachable")
    p.add_argument("--bind", default=DEFAULT_BIND,
                   help="IPv4 address to listen on (default %(default)s, this machine only)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default %(default)s; 0 picks a free one)")


def run(args):
    try:
        server = make_server(args.root, args.bind, args.port)
    except (OSError, ValueError, OverflowError) as e:   # bind() raises OverflowError for a port past 65535
        print("serve: %s" % e, file=sys.stderr)
        return 1
    with server:
        host = server.server_address[0]
        print(summary(server, os.path.realpath(args.root)), flush=True)
        if not ipaddress.ip_address(host).is_loopback:
            print("serve: reachable from other machines at %s, with no login; keep client originals out of this folder"
                  % host, file=sys.stderr, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m rpstills.serve",
                                 description="Serve one folder read-only over HTTP with byte ranges.")
    add_arguments(ap)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
