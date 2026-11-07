"""
rpresolve.proxies: ProRes LT proxies for BRAW, made after ingest and linked
afterwards. lutlab/braw/braw-proxy.sh makes the files, detached, because a card
takes hours over Wi-Fi; link_proxies attaches whatever has landed. They live on
the laptop, so a timeline keeps playing when the NAS drops.
"""

import os
import re
import subprocess

ROOT = os.path.expanduser("~/Movies/RPProxies")
BRAW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lutlab", "braw")
SCRIPT = os.path.join(BRAW_DIR, "braw-proxy.sh")
PREFER_PROXIES = "1"  # perfProxyMediaMode: 0 off, 1 when available, 2 when the source is missing


def proxy_dir(project_name):
    return os.path.join(ROOT, re.sub(r"[^\w.-]+", "-", project_name).strip("-"))


def braw_clips(paths):
    """The moving BRAW among paths: a still gains nothing from a proxy."""
    return [p for p in paths if p.lower().endswith(".braw")
            and os.path.basename(os.path.dirname(p)) != "Stills"]


def start(project_name, paths, popen=subprocess.Popen, exists=os.path.exists):
    """Start the proxies for paths in the background. Returns {dir, clips, log},
    or {error} when the decoder has not been built, or None with nothing to do."""
    clips = braw_clips(paths)
    if not clips:
        return None
    if not exists(os.path.join(BRAW_DIR, "braw2ppm")):
        return {"error": "braw2ppm is not built: run production/lutlab/braw/build.sh"}
    out = proxy_dir(project_name)
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "proxies.log")
    with open(log, "a") as f:
        popen([SCRIPT, out] + clips, stdout=f, stderr=subprocess.STDOUT,
              stdin=subprocess.DEVNULL, start_new_session=True)
    return {"dir": out, "clips": len(clips), "log": log}


def plan_links(clips, folder, exists=os.path.exists):
    """clips: [(clip, props)]. Each BRAW with <stem>.mov in folder and not yet on
    it is linked; the rest are reported. Returns [{name, proxy, action}]."""
    rows = []
    for clip, props in clips:
        path = props.get("File Path") or ""
        if not path or path not in braw_clips([path]):
            continue
        proxy = os.path.join(folder, os.path.splitext(os.path.basename(path))[0] + ".mov")
        if props.get("Proxy Media Path") == proxy:
            action = "linked already"
        elif exists(proxy):
            action = "link"
        else:
            action = "no proxy yet"
        rows.append({"name": props.get("Clip Name") or os.path.basename(path), "proxy": proxy,
                     "action": action, "clip": clip})
    return rows
