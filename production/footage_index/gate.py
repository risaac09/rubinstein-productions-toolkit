"""Per-shoot quality gate, computed on demand for the shots under a prefix (no schema change, nothing written to
footage.db). Search ranks by similarity to a query; that returned soft, smeared, black, blank and sideways frames
and the same take twice. The gate decides which shots may be candidates at all, and every rejection has a reason."""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

from . import embed, search, store

# Every threshold below was measured on one shoot and must be re-read on each new shoot
# (`footage gate PREFIX --sheet DIR`, then look at the rejects).
SHARP_PCT = 35      # reject below this percentile of log sharpness within the shoot
LUMA_LO, LUMA_HI = 0.10, 0.90  # stored mean luma outside this band is black or blown out
MOTION_PCT = 92     # reject above this percentile of the shoot's stored motion cue (smear)
KEEPER_MIN = -0.3   # keeper z (good photo minus worst bad-photo probe) must be above this
BLACK_PX, BLACK_MAX = 0.04, 0.55   # a frame with more than BLACK_MAX of its pixels under BLACK_PX (a doorway, a lens cap)
SIDE_MAX = 0.8      # sideways z (rotated-frame probe minus good probe) must be below this
SHARP_W = 0.6       # base score = keeper z + SHARP_W * sharpness z
CLUSTER_COS = 0.90  # greedy embedding clusters, a stand-in for "the same place or person"
COPY_DT = 0.05      # keyframes of two copies of one take sit at the same second

GOOD = "a sharp, in-focus, well-composed photograph"
BAD = ["a blurry, out-of-focus photo", "a photo ruined by motion blur and smear", "a black or blank empty frame",
       "an accidental photo of the ground or sky with nothing framed"]
SIDE = "a photo turned sideways, rotated ninety degrees"
KID, ADULT = "a photo of a young child or kids", "a photo of adults"
SIZES = {"wide": "a wide landscape shot of a place", "mid": "a medium shot of a person",
         "close": "a close-up portrait of a face", "detail": "a close-up detail of hands, food or an object"}
SUBJECTS = {"group": "a group of people together at a gathering", "action": "a person dancing or moving energetically",
            "figure": "one person seen small in a wide view, or as a silhouette"}

PROBE_CACHE = os.path.join(os.path.dirname(store.DB), "probe-cache.json")  # text vectors only, so a run needs no model load
# the camera's own take id in a file name: a letter and seven digits, a roll_date_clip form, or a phone's IMG_nnnn
TAKE_ID = re.compile(r"P\d{7}|A\d{3}_\d{8}_C\d{3}|(?:IMG|MVI|DSC)_\d{4}", re.I)
LOW_RES = re.compile(r"/low[ _-]?res/", re.I)
_pv, _sharp = {}, {}


def probe(text):
    """L2-normalised SigLIP text vector, memoised in memory and in PROBE_CACHE."""
    if text in _pv:
        return _pv[text]
    key, disk = f"{embed.MODEL}|{text}", {}
    if PROBE_CACHE:
        try:
            with open(PROBE_CACHE) as f:
                disk = json.load(f)
        except (OSError, ValueError):
            pass
    if key in disk:
        v = np.asarray(disk[key], np.float32)
    else:
        v = np.asarray(embed.text(text), np.float32)
        if PROBE_CACHE:
            disk[key] = [round(float(x), 6) for x in v]
            try:
                with open(PROBE_CACHE, "w") as f:
                    json.dump(disk, f)
            except OSError:
                pass
    _pv[text] = v
    return v


def z(x):
    x = np.asarray(x, np.float64)
    return (x - x.mean()) / (x.std() or 1.0)


def thumb_stats(path):
    """(sharpness, black share) of a thumbnail: variance of the Laplacian over (mean + 0.05) of the grey picture in 0..1,
    and the share of its pixels under BLACK_PX. (None, None) when unreadable."""
    try:
        g = np.asarray(Image.open(path).convert("L"), np.float32) / 255.0
    except (OSError, ValueError):
        return None, None
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var() / (g.mean() + 0.05)), float((g < BLACK_PX).mean())


def sharpness(path):
    return thumb_stats(path)[0]


def _sharp_cached(path):
    if path not in _sharp:
        _sharp[path] = thumb_stats(path)
    return _sharp[path]


def state_key(row):
    """Colour state of a clip. The index marks every render (Resolve export, ffmpeg transcode, handoff copy) as profile
    'derived' and a proxy as 'proxy'; anything else is a camera original keyed by camera, profile and colour space."""
    p = row["profile"] or ""
    if p == "derived":
        return "graded"
    if p == "proxy":
        return "proxy"
    return f"original:{row['camera'] or '?'}/{p or '?'}/{row['color_space'] or '?'}"


def take_keys(c, clips):
    """{clip id: take key}: the camera's id from the file name, else a group of clips that edit.same_take calls one take."""
    from .edit import same_take
    key, loose = {}, {}
    for cid, (rel, dur) in clips.items():
        m = TAKE_ID.search(os.path.basename(rel))
        if m:
            key[cid] = m.group(0).upper()
        else:
            loose.setdefault(int((dur or 0) * 10), []).append(cid)  # shortcut: a pair straddling a 0.1 s bucket edge is missed
    for ids in loose.values():
        root = {i: i for i in ids}
        for a in ids:
            for b in ids:
                if a < b and root[b] == b and same_take(c, a, b):
                    root[b] = root[a]
        for i in ids:
            key[i] = f"clip:{root[i]}"
    return key


def excluded(rows, take, ex):
    """Indices of the shot rows an exclude list ({"shots": [shot ids], "takes": [take ids or relpaths]}) rules out.
    rows: dicts with id, relpath and t; take: each row's take key. The gate and edit.check share this one rule."""
    ex_shots, ex_takes = set(ex.get("shots") or []), {str(t).upper() for t in ex.get("takes") or []}
    # An excluded shot takes its copies with it: another render of the take at the same second shows the same person.
    ex_at = [(take[i], r["t"]) for i, r in enumerate(rows) if r["id"] in ex_shots]
    return [i for i, r in enumerate(rows)
            if take[i] in ex_takes or r["relpath"].upper() in ex_takes
            or any(t == take[i] and abs(r["t"] - at) < COPY_DT for t, at in ex_at)]


class Gate:
    """Everything the gate knows about one shoot, as arrays aligned with V (one row per embedded shot)."""

    def __init__(self, c, prefix, state=None, exclude=None):
        self.prefix = p = search.shoot_prefix(prefix)
        ids, self.V = search.prefix_vectors(c, p)
        pos = {s: i for i, s in enumerate(ids)}
        self.rows = [None] * len(ids)
        for r in c.execute("SELECT s.id, s.clip_id, s.t, s.t_in, s.t_out, s.thumb, s.motion, s.faces, s.face_max, s.luma, "
                           "cl.relpath, cl.camera, cl.profile, cl.color_space, cl.fps, cl.duration, cl.width, cl.height, cl.mtime "
                           "FROM shots s JOIN clips cl ON cl.id = s.clip_id WHERE substr(cl.relpath, 1, ?) = ?",
                           (len(p), p)):
            if r["id"] in pos:
                self.rows[pos[r["id"]]] = dict(r)
        n = len(self.rows)
        self.n = n
        self.reason, self.zcache = [None] * n, {}
        if not n:  # nothing embedded yet: every array empty, so a consumer sees no shots instead of a missing attribute
            self.ok = self.pop = np.zeros(0, bool)
            self.base = self.face = self.dur = self.fps = self.kid = self.sharp_z = self.motion_z = np.zeros(0)
            self.size = self.cluster = np.zeros(0, int)
            self.take, self.state_of, self.rel_take, self.states, self.state = [], [], {}, {}, state
            return
        rows = self.rows
        clips = {r["clip_id"]: (r["relpath"], r["duration"]) for r in rows}
        tk = take_keys(c, clips)
        self.take = [tk[r["clip_id"]] for r in rows]
        self.state_of = [state_key(r) for r in rows]
        self.dur = np.array([r["duration"] or 0.0 for r in rows])
        self.fps = np.array([r["fps"] or 0.0 for r in rows])
        self.face = np.array([r["face_max"] or 0.0 for r in rows])
        self.rel_take = {r["relpath"]: t for r, t in zip(rows, self.take)}

        # Structure first: exclusions, one copy per take, one colour state. The statistics below are then taken over
        # that population only, so low-res copies and other states cannot move a percentile or a z-score.
        for i in excluded(rows, self.take, exclude or {}):
            self.reason[i] = "excluded"
        kept = {}  # copies of one take sit at the same seconds: keep the full-size, widest, newest one
        for i in sorted((i for i in range(n) if self.reason[i] is None),
                        key=lambda i: (bool(LOW_RES.search(rows[i]["relpath"])), -(rows[i]["width"] or 0),
                                       -(rows[i]["mtime"] or 0), rows[i]["clip_id"], rows[i]["id"])):
            ts = kept.setdefault((self.state_of[i], self.take[i]), [])
            if any(abs(rows[i]["t"] - t) < COPY_DT for t in ts):
                self.reason[i] = "copy"
            else:
                ts.append(rows[i]["t"])
        counts = {}
        for i in range(n):
            c3 = counts.setdefault(self.state_of[i], [0, 0, 0])
            c3[0] += 1
            c3[1] += self.reason[i] is None
        if state is not None and state not in counts:
            raise ValueError(f"no colour state {state!r} under {p}; found: {', '.join(sorted(counts))}")
        self.state = state or max(counts, key=lambda k: (counts[k][1], k))
        for i in range(n):
            if self.reason[i] is None and self.state_of[i] != self.state:
                self.reason[i] = "other_state"
        self.pop = pop = np.array([r is None for r in self.reason])

        with ThreadPoolExecutor(8) as pool:
            sh, black = zip(*pool.map(_sharp_cached, [r["thumb"] for r in rows])) if rows else ((), ())
        ls = np.log(np.maximum(np.array([np.nan if s is None else s for s in sh], np.float64), 1e-6))
        fin = np.isfinite(ls)  # an unreadable thumbnail is neither passed nor failed on sharpness
        ls = np.where(fin, ls, ls[fin & pop].mean() if (fin & pop).any() else 0.0)
        self.sharp_z = self.zp(ls)
        sharp_cut = np.percentile(ls[fin & pop], SHARP_PCT) if (fin & pop).any() else -np.inf
        mot = np.array([np.nan if r["motion"] is None else r["motion"] for r in rows], np.float64)
        mfin = np.isfinite(mot) & pop
        mot_cut = np.percentile(mot[mfin], MOTION_PCT) if mfin.any() else np.inf
        self.motion_z = self.zp(np.nan_to_num(mot, nan=float(mot[mfin].mean()) if mfin.any() else 0.0))
        luma = np.array([np.nan if r["luma"] is None else r["luma"] for r in rows], np.float64)

        T = lambda text: self.V @ probe(text)
        good = T(GOOD)
        self.keeper = self.zp(good - np.max([T(b) for b in BAD], axis=0))
        self.side = self.zp(T(SIDE) - good)
        self.kid = T(KID) - T(ADULT)  # ranks shots for a human look; never clears one
        self.base = self.keeper + SHARP_W * self.sharp_z
        self.size = np.argmax(np.stack([self.zt(t) for t in SIZES.values()]), axis=0)
        self.cluster = self._clusters()

        for i in np.flatnonzero(pop):
            if luma[i] < LUMA_LO:
                self.reason[i] = "dark"
            elif luma[i] > LUMA_HI:
                self.reason[i] = "blown"
            elif (black[i] or 0) > BLACK_MAX:
                self.reason[i] = "mostly_black"
            elif ls[i] < sharp_cut and fin[i]:
                self.reason[i] = "soft"
            elif mot[i] > mot_cut:
                self.reason[i] = "motion"
            elif self.keeper[i] <= KEEPER_MIN:
                self.reason[i] = "keeper"
            elif self.side[i] >= SIDE_MAX:
                self.reason[i] = "sideways"
        for i in np.flatnonzero(pop):
            counts[self.state][2] += self.reason[i] is None
        self.states = {k: {"shots": a_, "unique": b_, "gated": c_ if k == self.state else None}
                       for k, (a_, b_, c_) in sorted(counts.items())}
        self.ok = np.array([r is None for r in self.reason])

    def zp(self, x):
        """z-score against the gate's population (one colour state, one copy per take), applied to every row."""
        x = np.asarray(x, np.float64)
        ref = x[self.pop] if self.pop.any() else x
        return (x - ref.mean()) / (ref.std() or 1.0)

    def zt(self, text):
        """z-scored similarity of every shot in the shoot to a text probe."""
        if text not in self.zcache:
            self.zcache[text] = self.zp(self.V @ probe(text))
        return self.zcache[text]

    def _clusters(self):
        leaders, out = [], np.zeros(self.n, int)
        for i in range(self.n):
            if leaders:
                s = self.V[leaders] @ self.V[i]
                j = int(s.argmax())
                if s[j] >= CLUSTER_COS:
                    out[i] = j
                    continue
            out[i] = len(leaders)
            leaders.append(i)
        return out

    def summary(self):
        rej = {}
        for r in self.reason:
            if r:
                rej[r] = rej.get(r, 0) + 1
        return {"prefix": self.prefix, "shots": self.n, "unique": int(self.pop.sum()), "passes": int(self.ok.sum()), "state": self.state,
                "rejects": dict(sorted(rej.items(), key=lambda kv: -kv[1])), "states": self.states}

    def sheet(self, out, per=8, seed=0):
        """One contact sheet: a row of passes, then a row of rejects for each reason (a seeded sample), to read by eye."""
        rng = np.random.default_rng(seed)
        groups = [("pass", [i for i in range(self.n) if self.ok[i]])]
        for why in self.summary()["rejects"]:
            groups.append((why, [i for i in range(self.n) if self.reason[i] == why]))
        items = []
        for why, idx in groups:
            pick = sorted(rng.choice(idx, min(per, len(idx)), replace=False)) if idx else []
            items += [(self.rows[i]["thumb"], f"{why} {self.rows[i]['id']}") for i in pick]
            items += [(None, "")] * (per - len(pick))
        return contact(items, out, cols=per, width=192)


def shot_flags(rows):
    """[flags] per row (dicts with thumb, motion, luma): dark, blown, soft (log sharpness under the SHARP_PCT-th percentile of
    these rows) and smear (motion over the MOTION_PCT-th), by the gate's own thresholds but with no embeddings and no state
    or copy rules, so it runs on any set of shots. Percentiles are over the rows given."""
    with ThreadPoolExecutor(8) as pool:
        sh = list(pool.map(lambda r: _sharp_cached(r["thumb"])[0], rows))
    ls = np.log(np.maximum(np.array([np.nan if s is None else s for s in sh], np.float64), 1e-6))
    mot = np.array([np.nan if r["motion"] is None else r["motion"] for r in rows], np.float64)
    cut = lambda a, q: np.percentile(a[np.isfinite(a)], q) if np.isfinite(a).any() else None
    sc, mc = cut(ls, SHARP_PCT), cut(mot, MOTION_PCT)
    out = []
    for i, r in enumerate(rows):
        f, lu = [], r["luma"]
        if lu is not None and lu < LUMA_LO: f.append("dark")
        if lu is not None and lu > LUMA_HI: f.append("blown")
        if sc is not None and np.isfinite(ls[i]) and ls[i] < sc: f.append("soft")
        if mc is not None and np.isfinite(mot[i]) and mot[i] > mc: f.append("smear")
        out.append(f)
    return out


def load(c, prefix, state=None, exclude=None):
    return Gate(c, prefix, state, exclude)


def contact(items, out, cols=4, width=None):
    """Tile (image path, label) pairs into one JPEG; width None keeps each image at its stored size."""
    ims = []
    for path, label in items:
        try:
            im = Image.open(path).convert("RGB") if path else Image.new("RGB", (width or 384, int((width or 384) * 9 / 16)))
        except (OSError, ValueError, TypeError):
            im = Image.new("RGB", (width or 384, int((width or 384) * 9 / 16)), (40, 40, 40))
        if width:  # fit a 16:9 cell, so one portrait thumbnail cannot stretch every row
            k = min(width / im.width, (width * 9 / 16) / im.height)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
        if label:
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 8 * len(label) + 4, 13], fill=(0, 0, 0))
            d.text((2, 1), label, fill=(255, 255, 255))
        ims.append(im)
    cw, ch = max(i.width for i in ims), max(i.height for i in ims)
    rows = -(-len(ims) // cols)
    sheet = Image.new("RGB", (cw * cols, ch * rows))
    for k, im in enumerate(ims):
        sheet.paste(im, ((k % cols) * cw, (k // cols) * ch))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sheet.save(out, quality=85)
    return out
