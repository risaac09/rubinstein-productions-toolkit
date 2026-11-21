"""
decon fetch: download one reference video with yt-dlp, probe it, write DIR/<id>.ref.json.
References are for study only: the folder must be outside every git tree, and nothing
fetched here is committed or published. Stdlib only; ffprobe runs through delivercheck.
"""

import glob
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import delivercheck as dc, paths

DURATION_TOLERANCE_S = 0.5
YTDLP_TIMEOUT_S = 600
FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"


def find_ytdlp(explicit=None):
    exe = explicit or shutil.which("yt-dlp")
    if not exe:
        raise dc.ToolMissing("yt-dlp is not on PATH: brew install yt-dlp (or pass --yt-dlp PATH)")
    return exe


def check_url(url):
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        raise ValueError(f"only http and https URLs are fetched, got {url!r}")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, out_dir, yt_dlp=None):
    """Download url into out_dir and write <id>.ref.json. Returns the record.
    Raises ValueError, dc.ToolMissing, dc.CheckError or OSError."""
    check_url(url)
    problem = paths.out_problem(os.path.join(out_dir, "x.ref.json"), any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    exe = find_ytdlp(yt_dlp)
    dc.check_tools(ffmpeg=False)
    os.makedirs(out_dir, exist_ok=True)
    started = datetime.now(timezone.utc).timestamp()
    cmd = [exe, "-o", os.path.join(out_dir, "%(id)s.%(ext)s"), "--write-info-json", "--no-playlist",
           "-f", FORMAT, "--merge-output-format", "mp4", "--", url]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace", timeout=YTDLP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise dc.CheckError(f"yt-dlp timed out after {YTDLP_TIMEOUT_S}s")
    if proc.returncode != 0:
        raise dc.CheckError(f"yt-dlp failed: {(proc.stderr or '').strip()[-300:]}")
    infos = [p for p in glob.glob(os.path.join(out_dir, "*.info.json")) if os.path.getmtime(p) >= started - 2]
    if not infos:
        raise dc.CheckError("yt-dlp wrote no info json")
    info_path = max(infos, key=os.path.getmtime)
    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    vid = str(info.get("id") or os.path.basename(info_path)[:-len(".info.json")])
    media = sorted(p for p in glob.glob(os.path.join(out_dir, glob.escape(vid) + ".*"))
                   if not p.endswith((".json", ".part")))
    if not media:
        raise dc.CheckError(f"yt-dlp left no media file for {vid}")
    media = media[0]
    probed = dc.probe(media)
    video = next((s for s in probed.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise dc.CheckError(f"{media} has no video stream")
    try:
        real = float(probed["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise dc.CheckError(f"ffprobe gave no duration for {media}")
    claimed = info.get("duration")
    if claimed is None or abs(float(claimed) - real) > DURATION_TOLERANCE_S:
        raise dc.CheckError(f"duration mismatch: info json says {claimed}, ffprobe says {real:.2f}")
    num, _, den = str(video.get("avg_frame_rate", "0/1")).partition("/")
    fps = info.get("fps") or (float(num) / float(den or 1) if float(den or 1) else None)
    record = {"url": url, "id": vid, "title": info.get("title"), "uploader": info.get("uploader"),
              "duration": float(claimed), "fps": fps,
              "width": info.get("width") or video.get("width"), "height": info.get("height") or video.get("height"),
              "file": os.path.basename(media), "sha256": _sha256(media),
              "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    ref_path = os.path.join(out_dir, vid + ".ref.json")
    if os.path.islink(ref_path):   # never write through a symlink onto some other file
        raise paths.OutputRefused(f"{ref_path} is a symlink; remove it or pick another --out-dir")
    with open(ref_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)
    return record
