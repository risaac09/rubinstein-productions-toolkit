"""Profile one shoot (a relpath prefix) into context tags, each with a 0-1 strength and its evidence.

Captions are sparse until the overnight pass, so SigLIP text probes carry most of the weight. Each shot scores
each tag by its best prompt's cosine, centred on that shot's mean over all tags (busy frames score high on
everything; centring keeps what stands out). A tag's probe strength ramps its shoot mean from -0.01 to +0.03,
set on four indexed shoots. Faces, motion, luma, fps, captions and folder names add direct evidence."""
import datetime
import json
import re

import numpy as np

TAGS = ("travel", "event", "outdoor", "people", "food", "night", "motion", "portrait", "nature")
PROBES = {
    "travel": ["a travel photo of a scenic destination", "sightseeing on a trip", "a road trip view from a car window"],
    "event": ["a crowd at a festival", "people performing on a stage", "an audience at a concert or event"],
    "outdoor": ["an outdoor scene in daylight", "people outside under the open sky"],
    "people": ["a group of people", "a person talking to someone"],
    "food": ["people eating food", "a plate of food on a table", "cooking food in a kitchen"],
    "night": ["a night scene", "city lights at night", "a dark scene lit by lamps"],
    "motion": ["fast action and movement", "a person running", "a moving vehicle"],
    "portrait": ["a close-up portrait of a face", "a person looking at the camera"],
    "nature": ["a nature landscape", "a forest", "mountains and a lake", "waves on the ocean shore"],
}
CAPTION_WORDS = {
    "travel": {"travel", "trip", "tourism", "sightseeing", "adventure", "exploration", "road"},
    "event": {"festival", "concert", "event", "crowd", "stage", "performance", "audience", "celebration", "party"},
    "outdoor": {"outdoors", "outdoor", "outside", "street", "park", "beach", "field"},
    "people": {"people", "person", "man", "woman", "group", "crowd", "child", "children"},
    "food": {"food", "eating", "meal", "cooking", "kitchen", "dinner", "restaurant", "plate"},
    "night": {"night", "evening", "dusk", "dark", "nighttime"},
    "motion": {"running", "action", "moving", "dancing", "riding", "driving", "sports"},
    "portrait": {"portrait", "close-up", "closeup", "face"},
    "nature": {"nature", "landscape", "forest", "mountain", "mountains", "lake", "ocean", "river", "trees", "glacier"},
}
FOLDER_WORDS = {"travel": r"trip|travel|vacation|tour", "event": r"fest|festival|wedding|concert|event|gala|party",
                "nature": r"trail|hike|forest|mountain", "food": r"food|kitchen|restaurant"}
MOVING, PORTRAIT_FACE, DARK = 0.02, 0.03, 0.18
PROBE_LO, PROBE_HI = -0.01, 0.03
VERSION = 2  # bump when the scoring changes, so cached profiles are recomputed
_probe = {}

TABLE = "CREATE TABLE IF NOT EXISTS shoot_profiles(prefix TEXT PRIMARY KEY, sig TEXT NOT NULL, data TEXT NOT NULL, at TEXT)"


def probe_matrix():
    """(prompts x DIM text embeddings, the tag index of each row)."""
    if "m" not in _probe:
        from . import embed
        rows = [(i, embed.text(p)) for i, t in enumerate(TAGS) for p in PROBES[t]]
        _probe["m"] = (np.stack([v for _i, v in rows]).astype(np.float32), np.array([i for i, _v in rows]))
    return _probe["m"]


def probe_scores(S):
    """Per-tag mean over shots of the best prompt's cosine, centred per shot across tags."""
    E, tag_of = probe_matrix()
    cos = S @ E.T
    mx = np.stack([cos[:, tag_of == i].max(axis=1) for i in range(len(TAGS))], axis=1)
    return (mx - mx.mean(axis=1, keepdims=True)).mean(axis=0)


def _ramp(x, lo, hi):
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def _caption_words(data):
    words = set()
    for k in ("subject", "setting", "action", "shot_size", "time_of_day", "light"):
        words |= set(re.findall(r"[a-z][a-z-]+", str(data.get(k) or "").lower()))
    return words | {str(t).lower() for t in data.get("tags") or []}


def profile(c, prefix, refresh=False):
    from . import search
    prefix = search.shoot_prefix(prefix)
    rows = c.execute("SELECT s.id, s.motion, s.faces, s.face_max, s.luma, cl.relpath, cl.fps, cap.data AS caption "
                     "FROM shots s JOIN clips cl ON cl.id = s.clip_id LEFT JOIN captions cap ON cap.shot_id = s.id "
                     "WHERE substr(cl.relpath, 1, ?) = ?", (len(prefix), prefix)).fetchall()
    ids, S = search.prefix_vectors(c, prefix)
    caps = [json.loads(r["caption"]) for r in rows if r["caption"]]
    sig = f"{VERSION}:{len(rows)}:{len(ids)}:{len(caps)}"
    c.execute(TABLE)
    hit = c.execute("SELECT sig, data FROM shoot_profiles WHERE prefix = ?", (prefix,)).fetchone()
    if hit and hit["sig"] == sig and not refresh:
        return json.loads(hit["data"])

    n = len(rows)
    clips = sorted({r["relpath"] for r in rows})
    fps_mix = {}
    for r in {r["relpath"]: r for r in rows}.values():
        k = str(round(r["fps"])) if r["fps"] else "?"
        fps_mix[k] = fps_mix.get(k, 0) + 1
    share = lambda f: (sum(1 for r in rows if f(r)) / n) if n else 0.0
    moving = share(lambda r: (r["motion"] or 0) >= MOVING)
    faces = share(lambda r: (r["faces"] or 0) > 0)
    close = share(lambda r: (r["face_max"] or 0) >= PORTRAIT_FACE)
    dark = share(lambda r: r["luma"] is not None and r["luma"] < DARK)
    fast = (sum(v for k, v in fps_mix.items() if k != "?" and int(k) >= 50) / len(clips)) if clips else 0.0

    sigs = {t: [] for t in TAGS}  # tag -> [(strength, evidence)]
    if len(ids):
        for i, x in enumerate(probe_scores(S)):
            sigs[TAGS[i]].append((_ramp(x, PROBE_LO, PROBE_HI), f"SigLIP probe {x:+.3f} against the shot's other tags"))
    sigs["people"].append((_ramp(faces, 0.1, 0.6), f"faces in {faces:.0%} of shots"))
    sigs["portrait"].append((_ramp(close, 0.05, 0.4), f"a large face in {close:.0%} of shots"))
    sigs["motion"].append((_ramp(moving, 0.2, 0.8), f"{moving:.0%} of shots moving"))
    sigs["motion"].append((_ramp(fast, 0.1, 0.6), f"{fast:.0%} of clips at 50 fps or more"))
    sigs["night"].append((_ramp(dark, 0.15, 0.6), f"{dark:.0%} of shots dark (luma < {DARK})"))
    if len(caps) >= 5:
        words = [_caption_words(d) for d in caps]
        for t in TAGS:
            s = sum(1 for w in words if w & CAPTION_WORDS[t]) / len(words)
            sigs[t].append((_ramp(s, 0.1, 0.6), f"captions: {s:.0%} of {len(caps)} mention it"))
    for t, rx in FOLDER_WORDS.items():
        m = re.search(rx, prefix, re.I)
        if m:
            sigs[t].append((0.6, f"folder name says '{m.group(0)}'"))

    tags = {}
    for t in TAGS:
        best = max(sigs[t], key=lambda x: x[0]) if sigs[t] else (0.0, "no evidence")
        tags[t] = {"strength": round(best[0], 2), "evidence": [e for _s, e in sorted(sigs[t], key=lambda x: -x[0])]}
    out = {"prefix": prefix, "clips": len(clips), "shots": n, "vectors": len(ids), "captions": len(caps),
           "fps_mix": dict(sorted(fps_mix.items())), "moving_share": round(moving, 3), "faces_share": round(faces, 3),
           "dark_share": round(dark, 3), "tags": dict(sorted(tags.items(), key=lambda kv: -kv[1]["strength"]))}
    c.execute("INSERT OR REPLACE INTO shoot_profiles(prefix, sig, data, at) VALUES (?,?,?,?)",
              (prefix, sig, json.dumps(out), datetime.datetime.now().isoformat(timespec="seconds")))
    c.commit()
    return out
