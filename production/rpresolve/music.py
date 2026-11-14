"""
rpresolve.music: a library index for a folder of music. The files are never
moved, renamed or changed. One library.json beside the folders holds a row per
track keyed by the file's sha256: tags, duration, tempo, whether the pulse is
steady, loudness, onset density, and the fields a person or a tagger fills
(genre, feel) with where each came from. Playlists (.m3u, relative paths) are
written to playlists/ beside it. Measured fields are redone when the file
changes; genre, feel and anything marked "isaac" survive a re-run.

Tempo is rpresolve.beats' tracker (its prior settles half and double time);
rpresolve.notes.measure over TEMPO_RANGE (wider than notes' default fold) is
kept as bpm_notes, and onset counts come from it. A steady pulse is rpresolve.beats' tracked beats, with STEADY_SHARE of
the gaps within STEADY_TOL of the median gap. Loudness is ffmpeg ebur128.
Everything is measured, nothing is heard: genre and feel from a model are
drafts until a person checks them.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

from . import cutlist, methods, notes

EXTS = (".mp3", ".m4a", ".wav", ".flac", ".aif", ".aiff", ".ogg")
TEMPO_RANGE = (60.0, 180.0)
STEADY_TOL, STEADY_SHARE, MIN_BEATS = 0.05, 0.8, 8
BANDS = ((0, 90), (90, 110), (110, 125), (125, 140), (140, 1000))
LIBRARY, PLAYLISTS = "library.json", "playlists"
# fields measured from the file; every other field of a row is kept on a re-run
MEASURED = ("path", "size", "mtime", "artist", "title", "tag_genre", "duration_s", "bpm",
            "steady", "steady_share", "bpm_notes", "lufs", "onsets_per_s", "dupes", "error")


class MusicError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tags(path):
    """(artist, title, genre, duration) from ffprobe; missing tags are None."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags",
                          "-of", "json", "file:" + path], capture_output=True, text=True)
    fmt = (json.loads(out.stdout or "{}").get("format") or {}) if out.returncode == 0 else {}
    t = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    dur = float(fmt["duration"]) if fmt.get("duration") else None
    stem = os.path.splitext(os.path.basename(path))[0]
    return t.get("artist"), t.get("title") or stem, t.get("genre"), dur


def loudness(path):
    """Integrated loudness in LUFS (ffmpeg ebur128), or None."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", "file:" + path, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None


def steadiness(beats_s):
    """(share of beat gaps within STEADY_TOL of the median gap, steady?)."""
    gaps = [b - a for a, b in zip(beats_s, beats_s[1:])]
    if len(beats_s) < MIN_BEATS or not gaps:
        return 0.0, False
    med = sorted(gaps)[len(gaps) // 2]
    share = sum(bool(abs(g - med) <= STEADY_TOL * med) for g in gaps) / len(gaps)
    return round(float(share), 3), bool(share >= STEADY_SHARE)


def measure(path):
    """The measured fields of one file. Never raises: a failure is the row's error."""
    row = {"path": path}
    try:
        import numpy as np
        from . import beats
        artist, title, genre, dur = tags(path)
        rep = notes.measure(path, *TEMPO_RANGE)
        raw = cutlist.decode_pcm(path, 0.0, None, None, timeout=300)
        y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
        tempo, bt, _ = beats.beat_map(y)
        share, steady = steadiness(list(bt))
        d = dur or len(y) / beats.SR
        row.update(artist=artist, title=title, tag_genre=genre, duration_s=round(d, 1),
                   bpm=round(float(tempo), 2), bpm_notes=rep["bpm"], steady=steady, steady_share=share, lufs=loudness(path),
                   onsets_per_s=round(len(rep["onsets"]) / d, 2) if d else None)
    except Exception as e:  # one bad file must not stop the run
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def _work(args):
    root, rel, old = args
    p = os.path.join(root, rel)
    try:
        st = os.stat(p)
        if old and old.get("size") == st.st_size and old.get("mtime") == int(st.st_mtime) and not old.get("error"):
            return old["sha"], dict({k: v for k, v in old.items() if k != "sha"}, path=rel)
        row = measure(p)
        row.update(path=rel, size=st.st_size, mtime=int(st.st_mtime))
        return sha256(p), row
    except OSError as e:  # a vanished or unreadable file is that row's error, not the run's
        return "unread:" + rel, {"path": rel, "error": f"{type(e).__name__}: {e}"}


def scan(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x != PLAYLISTS and not x.startswith((".", "#", "_")))
        out += [os.path.relpath(os.path.join(d, f), root) for f in sorted(files)
                if f.lower().endswith(EXTS) and not f.startswith(".")]
    return out


def load_library(root):
    p = os.path.join(root, LIBRARY)
    if not os.path.isfile(p):
        return {"tracks": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_library(root, lib):
    p = os.path.join(root, LIBRARY)
    tmp = p + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)


def index(root, workers=6, log=lambda s: None):
    """Measure every audio file under root, merge into library.json, write playlists."""
    if not os.path.isdir(root):
        raise MusicError(f"{root} is not a folder")
    lib = load_library(root)
    by_path = {t.get("path"): dict(t, sha=h) for h, t in lib["tracks"].items()}
    files = scan(root)
    jobs = [(root, r, by_path.get(r)) for r in files]
    from multiprocessing import Pool
    tracks = {}
    with Pool(workers) as pool:
        for n, (h, row) in enumerate(pool.imap(_work, jobs, chunksize=1), 1):
            if h in tracks:  # the same bytes under another path: one row, every path kept
                tracks[h].setdefault("dupes", []).append(row["path"])
                continue
            prev = lib["tracks"].get(h) or {}
            tracks[h] = dict({k: v for k, v in prev.items() if k not in MEASURED}, **row)
            if n % 25 == 0 or n == len(jobs):
                log(f"music: {n}/{len(jobs)}")
    lib = {"tracks": tracks, "playlists": lib.get("playlists", [])}
    write_playlists(root, lib)
    save_library(root, lib)
    return lib


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def playlists(lib):
    """{name: [relative paths]} by tempo band, feel and genre; a track in many lists is fine."""
    out = {}
    for t in lib["tracks"].values():
        if t.get("error") or not t.get("path"):
            continue
        mine = [t["path"]] + t.get("dupes", [])
        if t.get("bpm"):
            for lo, hi in BANDS:
                if lo <= t["bpm"] < hi:
                    out.setdefault(f"tempo-{lo}-{hi if hi < 1000 else 'up'}", []).extend(mine)
        for key in ("feel", "genre"):
            for v in (t.get(key) if isinstance(t.get(key), list) else [t.get(key)] if t.get(key) else []):
                out.setdefault(f"{key}-{_slug(v)}", []).extend(mine)
    return {k: sorted(v) for k, v in out.items()}


def write_playlists(root, lib):
    """Write the .m3u files in playlists/ and record their names in lib["playlists"]. Only a list
    this tool wrote earlier (named there) is removed when it goes stale; a person's file stays."""
    d = os.path.join(root, PLAYLISTS)
    os.makedirs(d, exist_ok=True)
    lists = playlists(lib)
    for name in lib.get("playlists", []):
        if name not in lists and os.path.isfile(os.path.join(d, name + ".m3u")):
            os.remove(os.path.join(d, name + ".m3u"))
    lib["playlists"] = sorted(lists)
    for name, paths_ in lists.items():
        with open(os.path.join(d, name + ".m3u"), "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n" + "".join(f"../{p}\n" for p in paths_))
    return sorted(lists)


def tempo_gap(bpm, rng):
    """How far bpm is from the range [lo, hi], also trying half and double time: 0 is inside."""
    lo, hi = rng
    return min(0.0 if lo <= bpm * k <= hi else min(abs(bpm * k - lo), abs(bpm * k - hi)) for k in (1, 2, 0.5))


def suggest(lib, method=None, bpm=None, feel=None, n=10, steady=True):
    """Tracks (feel: a list, any of them) for a method's tempo range (or a bpm range) and feel, nearest first.
    Suggestions only; a person's choice wins."""
    if method:
        m = methods.load(method)
        rng = m.get("bpm")
    else:
        rng = bpm
    out = []
    for h, t in lib["tracks"].items():
        if t.get("error") or not t.get("bpm"):
            continue
        if steady and not t.get("steady"):
            continue
        if feel and not set(feel) & set(t.get("feel") if isinstance(t.get("feel"), list) else [t.get("feel")]):
            continue
        gap = tempo_gap(t["bpm"], rng) if rng else 0.0
        out.append((gap, h, t))
    out.sort(key=lambda x: (x[0], x[2].get("path", "")))
    return [dict(t, sha=h, tempo_gap=round(g, 1)) for g, h, t in out[:n]]


def suits(lib):
    """Fill `suits` (edit method ids whose tempo range holds the track, half or double time too)
    on rows a person has not set."""
    ms = [m for m in methods.list_methods() if m.get("bpm")]
    for t in lib["tracks"].values():
        if t.get("suits_source") == "isaac":
            continue
        t["suits"] = [m["id"] for m in ms if t.get("bpm") and tempo_gap(t["bpm"], m["bpm"]) == 0]
        t["suits_source"] = "tempo"


def set_fields(lib, key, genre=None, feel=None, source="isaac"):
    """Set genre/feel on the track whose sha256 starts with key (or whose path is key)."""
    hit = [h for h, t in lib["tracks"].items() if h.startswith(key) or t.get("path") == key]
    if len(hit) != 1:
        raise MusicError(f"{key}: {len(hit)} tracks match, need exactly one")
    t = lib["tracks"][hit[0]]
    for k, v in (("genre", genre), ("feel", feel)):
        if v is not None:
            t[k] = v
            t[k + "_source"] = source
    return t


def import_tags(lib, rows, source="model"):
    """rows: {sha or path: {"genre": ..., "feel": [...]}} from a tagger. A field a person
    set (source isaac) is never overwritten. Returns how many tracks changed."""
    n = 0
    for key, v in rows.items():
        hit = [h for h, t in lib["tracks"].items() if h == key or t.get("path") == key]
        if len(hit) != 1:
            continue
        t = lib["tracks"][hit[0]]
        for k in ("genre", "feel"):
            if k in v and t.get(k + "_source") != "isaac":
                t[k], t[k + "_source"] = v[k], source
                n += 1
    return n


def counts(lib):
    ts = [t for t in lib["tracks"].values()]
    ok = [t for t in ts if not t.get("error")]
    return {"tracks": len(ts), "errors": len(ts) - len(ok),
            "with_tempo": sum(bool(t.get("bpm")) for t in ok),
            "with_steady_pulse": sum(bool(t.get("steady")) for t in ok),
            "with_genre": sum(bool(t.get("genre")) for t in ok),
            "with_feel": sum(bool(t.get("feel")) for t in ok)}
