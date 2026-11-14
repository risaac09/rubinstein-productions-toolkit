"""Edit methods against one shoot: rank methods for it, fill a method's slots, swap one slot.

Methods are technique-only JSON files from the toolkit (production/edit-methods/). Output goes to
<edits_dir>/<shoot>-<method>/ (a setting; keep it outside every git repo): fills.json, plus manifest.json when the toolkit's
rpresolve.methods.to_manifest is importable."""
import json
import math
import os
import re
import subprocess
from collections import Counter

import numpy as np

from . import config, embed, gate as G, roots, search, shoot, trim as trim_mod

TOOLKIT = config.PRODUCTION
METHODS = config.get("methods_dir")
EDITS = config.get("edits_dir")
FLOOR = config.get("slot_floor")  # SigLIP cosine a slot's best match must clear
LOW_RES = re.compile(r"/low[ _-]?res/", re.I)
POOL = 40
# Slot scoring on the gate (set on one shoot; re-read on each new one)
USE_PENALTY = 0.7   # per earlier use of the same take, across the plan (and across variants)
MAX_COS = 0.80      # a pick may not look like any earlier pick
PREV_FLOOR, PREV_W = 0.55, 1.5   # penalty PREV_W * clip(cos to previous pick - PREV_FLOOR, 0, 1)
LEN_MARGIN = 0.4    # clip must be at least slot length + this
ROLE_W, TEXT_W, STATIC_W = 0.8, 0.3, 0.5
SIZE_REPEAT, CLUSTER_REPEAT = 0.4, 0.6   # per earlier pick of the same shot size / embedding cluster
THEME_W = 1.4
ROLE_MIN = 0.5      # a slot whose role names a probe takes shots half a deviation above the shoot's mean on it
FACE_MIN = 0.02     # face box area as a share of the frame: 0.025 and up were faces, 0.015 a passer-by behind a torso
# role words (in the slot's role or query) -> the gate's size and subject probes that fit it
ROLE_FIT = [(r"wide|establish|reveal|landscape|plate|closer|street|skyline|place", ("wide",), ROLE_W),
            (r"close|face|portrait", ("close",), ROLE_W),
            (r"figure|silhouette", ("figure",), ROLE_W),
            (r"person|subject", ("mid",), ROLE_W),
            (r"detail|hands|object|food|sign", ("detail",), ROLE_W),
            (r"burst|crowd|people|group", ("group",), 0.5),
            (r"move|movement|danc|run|jump|action|payoff|hero", ("action",), 0.5)]
THEMES = {"feast": "people sharing a meal or food", "hands": "close-up of hands making something",
          "land": "open land, fields and sky", "faces": "a portrait of a person's face",
          "move": "people dancing or moving their bodies", "dusk": "golden hour light at dusk",
          "gather": "a crowd or circle of people gathered together", "place": "buildings and architecture"}


def load_methods(path=None):
    path = path or METHODS
    out = []
    for f in sorted(os.listdir(path)) if os.path.isdir(path) else []:
        if f.endswith(".json"):
            with open(os.path.join(path, f)) as fh:
                m = json.load(fh)
            if m.get("edit_method") == 1 and m.get("slots"):
                out.append(m)
    return out


def get_method(mid, path=None):
    for m in load_methods(path):
        if m["id"] == mid:
            return m
    raise ValueError(f"no method {mid!r} in {path or METHODS}")


def slot_seconds(method, slot):
    if slot.get("seconds"):
        return float(slot["seconds"])
    bpm = method.get("bpm") or [120, 120]
    return float(slot.get("beats", 4)) * 60 / (sum(bpm) / 2)


def slug(prefix):
    return re.sub(r"[^a-z0-9]+", "-", prefix.rstrip("/").split("/")[-1].lower()).strip("-")


def out_dir(method_id, prefix):
    return os.path.join(EDITS, f"{slug(prefix)}-{method_id}")


def full_res(c, r):
    """A low-res handoff's full-resolution sibling (same length, its camera stem inside the handoff's name), else r."""
    if not LOW_RES.search(r["relpath"]):
        return r
    name = os.path.basename(r["relpath"]).lower()
    project = os.path.dirname(os.path.dirname(r["relpath"].rstrip("/"))) + "/"  # the handoff's parent folder only
    for cl in c.execute("SELECT id, relpath, fps, duration, camera, profile FROM clips WHERE abs(duration - ?) < 0.5 "
                        "AND substr(relpath, 1, ?) = ?", (r["duration"] or 0, len(project), project)):
        stem = os.path.splitext(os.path.basename(cl["relpath"]))[0].lower()
        if (not LOW_RES.search(cl["relpath"]) and len(stem) >= 4
                and re.search(rf"(^|[^a-z0-9]){re.escape(stem)}([^a-z0-9]|$)", name)):
            return {**r, "relpath": cl["relpath"], "fps": cl["fps"], "duration": cl["duration"], "clip_id": cl["id"],
                    "camera": cl["camera"], "profile": cl["profile"], "low_res": r["relpath"]}
    return r


def trim(r, seconds, handle=0.0):
    """seconds centred inside the keyframe window, kept inside the clip (handle: seconds to leave after the out point)."""
    dur = r["duration"] or r["t_out"]
    dur -= handle if dur - handle >= seconds else 0.0  # a take that cannot give both keeps the length; fill() marks it plate_short
    mid = (r["t_in"] + r["t_out"]) / 2
    a = max(0.0, mid - seconds / 2)
    b = min(dur, a + seconds)
    return round(max(0.0, b - seconds), 3), round(b, 3)


TAKE_COS = 0.9  # a renamed copy or stabilised render of a take: same length, near-same picture at the same times


def same_take(c, a, b):
    """True when clips a and b (ids) are copies of one take: equal length to 0.1 s and alike at matching keyframes."""
    if a == b:
        return True
    da, db = (c.execute("SELECT duration FROM clips WHERE id = ?", (x,)).fetchone()[0] for x in (a, b))
    if da is None or db is None or abs(da - db) >= 0.1:
        return False
    import numpy as np
    pairs = c.execute("SELECT v1.embedding, v2.embedding FROM shots s1 JOIN shots s2 ON abs(s1.t - s2.t) < 0.05 "
                      "JOIN vec_shots v1 ON v1.shot_id = s1.id JOIN vec_shots v2 ON v2.shot_id = s2.id "
                      "WHERE s1.clip_id = ? AND s2.clip_id = ? LIMIT 5", (a, b)).fetchall()
    return bool(pairs) and min(float(np.frombuffer(x, np.float32) @ np.frombuffer(y, np.float32)) for x, y in pairs) > TAKE_COS


def role_fit(g, slot):
    """(fit, z): role fit from the gate's shot-size and subject probes, and the probe z it came from (None when no row
    matches). The slot's role picks the row; a role no row names falls back to the words of its query."""
    role = (slot.get("role") or "").lower()
    probes = {**G.SIZES, **G.SUBJECTS}
    fit, z = np.zeros(g.n), None
    for text in (role, f"{role} {slot.get('query', '')}".lower()):
        row = next(((names, w) for rx, names, w in ROLE_FIT if re.search(rx, text)), None)
        if row:
            z = np.max([g.zt(probes[k]) for k in row[0]], axis=0)
            fit = row[1] * z
            break
    return fit - (STATIC_W * g.motion_z if slot.get("move") == "static" else 0), z


def role_mask(g, slot, role_z, mask):
    """mask narrowed to the slot's role, each rule only while a candidate is left: the probe floor, and for a face slot
    a detected face of FACE_MIN (the close-up probe alone passes a torso with a passer-by behind it)."""
    for keep in (role_z >= ROLE_MIN if role_z is not None else None,
                 g.face >= FACE_MIN if (slot.get("role") or "").lower() == "face" else None):
        if keep is not None and (mask & keep).any():
            mask = mask & keep
    return mask


def _gated(c, method, slot, g, seconds, used, limit, ctx):
    """Best gated shots for a slot: gate base + role fit + query text - prior uses - diversity penalties."""
    ctx = ctx or {}
    need = ctx.get("need", seconds) + LEN_MARGIN
    sim = g.V @ embed.text(slot["query"])
    fit, role_z = role_fit(g, slot)
    score = g.base + fit + TEXT_W * G.z(sim)
    uses = ctx.get("uses") or Counter()
    score = score - USE_PENALTY * np.array([uses[t] for t in g.take])
    if ctx.get("theme") is not None:
        score = score + THEME_W * np.clip(ctx["theme"], -1, 2.5)
    picks = list(ctx.get("picks") or [])
    mask = g.ok & (g.dur >= need)
    if ctx.get("handle"):  # the keyframe must precede the handle, or the trim (out <= duration - handle) drops it
        mask &= np.array([r["t"] for r in g.rows]) <= g.dur - ctx["handle"]
    blocked = set(ctx.get("used_takes") or ()) | {g.rel_take[u] for u in used if u in g.rel_take}
    if blocked:  # before the variety rule, so "a like one beats an empty slot" is judged on takes still free
        mask &= np.array([t not in blocked for t in g.take])
    if picks:
        S = g.V @ g.V[picks].T
        far = S.max(axis=1) < MAX_COS
        if (mask & far).any():  # a long method runs out of unlike shots; a like one beats an empty slot
            mask &= far
        score = score - PREV_W * np.clip(S[:, -1] - PREV_FLOOR, 0, 1)
        for arr, w in ((g.size, SIZE_REPEAT), (g.cluster, CLUSTER_REPEAT)):
            cnt = Counter(arr[picks].tolist())
            score = score - w * np.array([cnt[v] for v in arr.tolist()])
    # The role holds whatever the theme: a face slot takes a shot from the upper half of the shoot's face probe, when
    # one is left (a theme at THEME_W otherwise outvotes the role, and a "face" slot opens on plates of food).
    mask = role_mask(g, slot, role_z, mask)
    fps_min = method.get("fps", 30) / slot["speed"] * 0.95 if (slot.get("speed") or 1) < 1 else None
    for fmin in ([fps_min, None] if fps_min else [None]):
        m = mask & (g.fps >= fmin) if fmin else mask
        out, seen = [], set()
        for i in np.argsort(-np.where(m, score, -np.inf)):
            if not m[i] or len(out) >= limit:
                break
            if g.take[i] in seen:
                continue
            seen.add(g.take[i])
            r = {**g.rows[i], "idx": int(i), "sim": round(float(sim[i]), 4),
                 "score": round(float(score[i]), 3), "take": g.take[i], "state": g.state_of[i],
                 "kid": round(float(g.kid[i]), 4), "fps_short": fmin is None and fps_min is not None}
            out.append(full_res(c, r))
        if out:
            return out
    return out


def candidates(c, method, slot, prefix, used=(), limit=5, gate=None, ctx=None, seconds=None, handle=0.0):
    """Best in-shoot matches for a slot that fit its length, full-res where a sibling exists, unused takes only.
    A slow slot wants a take at fps/speed; when the shoot has none, the best take at any rate comes back with
    fps_short set (the slow-down then needs frame interpolation)."""
    seconds = seconds or slot_seconds(method, slot)
    if gate is not None:
        return _gated(c, method, slot, gate, seconds, used, limit, {**(ctx or {}), "handle": handle})
    fps_min = method.get("fps", 30) / slot["speed"] * 0.95 if (slot.get("speed") or 1) < 1 else None
    used_ids = [r[0] for r in c.execute("SELECT id FROM clips WHERE relpath IN (%s)" % ",".join("?" * len(used)),
                                        list(used))] if used else []
    out = []
    for fmin in ([fps_min, None] if fps_min else [None]):
        for r in search.find(c, slot["query"], limit=POOL, prefix=prefix, fps_min=fmin, max_per_clip=1,
                             exclude_clips=used):
            r = full_res(c, r)
            if r["relpath"] in used or r.get("low_res") in used or (r["duration"] or 0) < seconds:
                continue
            if handle and r["t"] > (r["duration"] or 0) - handle:  # the keyframe would fall in the handle: the trim cannot keep it
                continue
            if any(same_take(c, r["clip_id"], o["clip_id"]) for o in out) or \
                    any(same_take(c, r["clip_id"], u) for u in used_ids):
                continue
            out.append({**r, "fps_short": fmin is None and fps_min is not None})
            if len(out) >= limit:
                return out
        if out:
            return out
    return out


def _handle(method, k, seconds=None):
    """Seconds the slot before method slot k (0-based) must leave of its take after its out point, because slot k opens a
    window over it (the previous shot plays on under the window); 0.0 when slot k is none or past the end. seconds: slot k's
    laid length, when the plan has one."""
    if k >= len(method["slots"]):
        return 0.0
    s = method["slots"][k]
    return trim_mod.plate_need(s.get("effect"), seconds or slot_seconds(method, s), float(method.get("fps") or 24))


def _span(r, seconds, mode, handle=0.0):
    """(in, out, reason): from the motion curve, or centred on the keyframe window (mode 'centre', or when the curve
    cannot be read: footage offline, BRAW, a take too short for the margins)."""
    why = "centred on the keyframe window (--trim centre)"
    if mode == "motion":
        try:
            return trim_mod.span(r, seconds, handle)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            why = f"centred on the keyframe window ({type(e).__name__}: {e})"
    return (*trim(r, seconds, handle), why)


def fill(method, slot, n, r, span=None, seconds=None, handle=0.0):
    """handle: seconds the planner keeps of the take after the out point for the next slot's window (the plate plus
    trim.PLATE_MARGIN); a fill with less is marked plate_short. check and the draft farm refuse only below the plate itself."""
    seconds = seconds or slot_seconds(method, slot)
    a, b, why = span or _span(r, seconds, "centre", handle)
    short = {"plate_short": f"{r['duration'] - b:.2f}s after the out point; the planner keeps {handle:.2f}s for the next slot's "
                            f"window (the plate plus {trim_mod.PLATE_MARGIN:g}s margin), the draft farm refuses below the plate"} \
        if handle and r["duration"] and r["duration"] - b < handle - 1e-6 else {}  # no length in the index: nothing to compare
    return {**short, "slot": n, "role": slot["role"], "query": slot["query"], "seconds": seconds, "trim": why,
            "take": r.get("take"), "score": r.get("score"), "kid": r.get("kid"), "state": r.get("state"),
            "path": roots.volume_path(r["relpath"]), "relpath": r["relpath"], "in_s": a, "out_s": b,
            "shot_id": r["id"], "keyframe_s": r["t"], "thumb": r["thumb"], "sim": r["sim"], "fps": r["fps"],
            "low_res": r.get("low_res"), "fps_short": r.get("fps_short", False)}


def gated_takes(c, prefix):
    """Distinct takes that pass the shoot's gate; None when the shoot has no vectors yet (nothing to gate on)."""
    g = build_gate(c, prefix)
    if not g.n:
        return None
    return len({g.take[i] for i in range(g.n) if g.ok[i]})


def take_need(method, variants=1):
    """Distinct takes the method needs: every slot, or half of them when it may reuse; each further variant may share
    at most half its slots with the others, so it adds half as many again."""
    n = len(method["slots"]) / (2 if (method.get("rules") or {}).get("reuse") else 1)
    return n * (1 + (variants - 1) / 2)


def rank(c, prefix, methods=None, floor=FLOOR, top=3, variants=1):
    """top=None returns every method, ranked. A method's score is scaled by its take budget: the share of the distinct
    takes it needs for this many variants (take_need) that the shoot's gate can supply."""
    prefix = search.shoot_prefix(prefix)
    prof = shoot.profile(c, prefix)
    tags = {t: v["strength"] for t, v in prof["tags"].items()}
    takes = gated_takes(c, prefix)
    ranked = []
    for m in methods if methods is not None else load_methods():
        fits = m.get("fits") or []
        tag_score = sum(tags.get(t, 0) for t in fits) / len(fits) if fits else 0.0
        budget = min(1.0, takes / take_need(m, variants)) if takes is not None else 1.0
        best = []  # (slot, role, candidates by similarity)
        for i, s in enumerate(m["slots"], 1):
            cand = sorted(candidates(c, m, s, prefix, limit=3), key=lambda r: -(r["sim"] or 0))
            if cand:  # by similarity: a caption-only hit (sim None) can top the fused rank
                best.append((i, s["role"], cand))
        cover = sum(1 for _i, _role, cs in best if (cs[0]["sim"] or 0) >= floor) / len(m["slots"])
        strongest, seen = [], set()
        for i, role, cs in sorted(best, key=lambda x: -(x[2][0]["sim"] or 0)):
            r = next((x for x in cs if x["id"] not in seen), None)  # four different shots, not one shot four times
            if r and len(strongest) < 4:
                seen.add(r["id"]); strongest.append((i, role, r))
        ranked.append({"id": m["id"], "name": m["name"], "summary": m["summary"],
                       "score": round((0.6 * tag_score + 0.4 * cover) * budget, 3), "tag_score": round(tag_score, 3),
                       "slot_cover": round(cover, 3), "take_budget": round(budget, 3), "gated_takes": takes,
                       "thumbs": [{"slot": i, "role": role, "thumb": r["thumb"], "sim": r["sim"]} for i, role, r in strongest]})
    ranked.sort(key=lambda x: -x["score"])
    return {"profile": prof, "methods": ranked[:top]}


def _absheet(paths, out, title=None):
    cmd = [config.get("tools_python"), "-m", "rpstills", "absheet", *paths, "--out", out]
    subprocess.run(cmd + (["--title", title] if title else []), cwd=TOOLKIT, check=True)
    with open(out.rstrip("/") + ".key/key.json") as f:
        return json.load(f)


def method_sheet(ranked, out):
    """One tile per method (its 4 strongest slot thumbnails, 2x2), on one lettered sheet; returns letter -> method id."""
    from PIL import Image
    if len(ranked) < 2:
        raise ValueError(f"{len(ranked)} method(s) ranked; a sheet needs 2 or more")
    tiles = os.path.abspath(out.rstrip("/") + ".tiles")
    os.makedirs(tiles, exist_ok=True)
    paths = {}
    for m in ranked:
        tile = Image.new("RGB", (768, 432), (20, 20, 20))
        for k, t in enumerate(m["thumbs"][:4]):
            im = Image.open(t["thumb"]).convert("RGB")
            im.thumbnail((384, 216))
            tile.paste(im, ((k % 2) * 384, (k // 2) * 216))
        p = os.path.join(tiles, f"{m['id']}.jpg")
        tile.save(p, quality=90)
        paths[p] = m["id"]
    key = _absheet(list(paths), out, "edit methods")
    return {letter: paths[v["source"]] for letter, v in key["labels"].items()}


def _minors(fills, d):
    """minors.jpg: every pick at its stored size, most child-like first by the SigLIP probe. It ranks for a person to
    read; it never clears a shot."""
    picks = sorted((s for s in fills["slots"] if s.get("path") and s.get("kid") is not None), key=lambda s: -s["kid"])
    if picks:
        G.contact([(s["thumb"], f"{s['kid']:+.3f} s{s['slot']}") for s in picks], os.path.join(d, "minors.jpg"), cols=4)


def retimed(method, fills):
    """The method with each slot's seconds set to its fill's (beat-laid) length, so the manifest keeps the plan's cuts."""
    m = json.loads(json.dumps(method))
    for s, f in zip(m["slots"], fills["slots"]):
        s["seconds"] = f["seconds"]
    end = (m.get("rules") or {}).get("end") or ""
    black = float(end.split(":")[1]) if end.startswith("black:") else 0.0
    m["length_s"] = round(sum(s["seconds"] for s in m["slots"]) + black, 3)
    if "min_shot_s" in (m.get("rules") or {}):
        m["rules"]["min_shot_s"] = min(m["rules"]["min_shot_s"], min(s["seconds"] for s in m["slots"]))
    return m


def _write(method, fills, d):
    """Write the plan folder; the returned note says what was written and names any slot whose take is too short to play
    under the next slot's window plus margin (plate_short)."""
    note = plate_note(fills)
    return _write_files(method, fills, d) + (f"; {note}" if note else "")


def plate_note(fills):
    """A sentence naming the fills marked plate_short (less than the plate plus margin left for the next slot's window), or ''."""
    late = [s["slot"] for s in fills["slots"] if s.get("plate_short")]
    return (f"slot(s) {', '.join(map(str, late))} leave less than the window plate plus {trim_mod.PLATE_MARGIN:g}s margin of the "
            "take after the out point; the draft farm refuses a window only below the plate itself" if late else "")


def _write_files(method, fills, d):
    os.makedirs(d, exist_ok=True)
    if os.path.exists(os.path.join(d, "manifest.json")):  # an earlier plan's manifest would pass check beside these fills
        os.remove(os.path.join(d, "manifest.json"))
    with open(os.path.join(d, "fills.json"), "w") as f:
        json.dump(fills, f, indent=1)
    _minors(fills, d)
    method = retimed(method, fills)
    with open(os.path.join(d, "method.json"), "w") as f:
        json.dump(method, f, indent=1)
    empty = [s["slot"] for s in fills["slots"] if not s.get("path")]
    if empty:
        return f"fills.json only: slot(s) {', '.join(map(str, empty))} unfilled, so no manifest"
    try:
        from rpresolve import methods as rm
        # to_manifest takes one {source_path, in, out} per slot, in slot order
        word = (fills.get("word") or fills.get("theme") or slug(fills["shoot"]).split("-")[0]).upper()
        manifest = rm.to_manifest(method, [{"source_path": s["path"], "in": s["in_s"], "out": s["out_s"],
                                            **({"text": word} if ms.get("text") else {})}
                                           for s, ms in zip(fills["slots"], method["slots"])])
        if fills.get("music"):
            manifest["music"] = {"path": fills["music"], "in": fills.get("music_in_s", 0.0)}
    except (ImportError, AttributeError) as e:
        return f"fills.json only: rpresolve.methods.to_manifest is not available ({type(e).__name__}: {e})"
    except Exception as e:  # the toolkit refusing these fills must not lose them
        return f"fills.json only: to_manifest refused the fills ({type(e).__name__}: {e})"
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return "fills.json and manifest.json"


SAME_TAKE = re.compile(r"same[ -]take", re.I)


def exclude_path(prefix):
    return os.path.join(EDITS, f"{slug(prefix)}-exclude.json")


EXCLUDE_KEYS = ("shots", "takes", "why")  # the gate reads shots and takes; "why" and any key starting "ask" are notes for a person


def read_exclude(path):
    """An exclude file ({"shots": [ids], "takes": [take ids or relpaths]}) as a dict; {} when there is none.
    A key the gate does not read is an error: it would be ignored without a word and the excluded clips would come back."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            ex = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON ({e})")
    if not isinstance(ex, dict):
        raise ValueError(f"{path}: expected an object with the keys {', '.join(EXCLUDE_KEYS)}")
    bad = sorted(k for k in set(ex) - set(EXCLUDE_KEYS) if not k.startswith("ask"))
    if bad:
        raise ValueError(f"{path}: unknown key {', '.join(map(repr, bad))}; the keys are {', '.join(EXCLUDE_KEYS)} "
                         "(only shots and takes exclude anything)")
    for k in ("shots", "takes"):
        if not isinstance(ex.get(k, []), list):
            raise ValueError(f"{path}: {k!r} must be a list")
    return ex


def load_exclude(prefix):
    """<edits>/<shoot>-exclude.json as a dict, {} when there is none (read_exclude)."""
    return read_exclude(exclude_path(prefix))


def build_gate(c, prefix, state=None):
    """The shoot's gate, honouring the shoot's exclude list (load_exclude)."""
    return G.load(c, prefix, state, load_exclude(prefix))


def _fills_slots(d):
    """The slots of the folder's fills.json, or None when there is none or it cannot be read."""
    try:
        with open(os.path.join(d, "fills.json")) as f:
            slots = json.load(f)["slots"]
        return slots if isinstance(slots, list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _fills_only(d):
    """Why a folder is not a complete plan, when its fills.json has unfilled slots (whether or not a manifest.json sits
    beside it: the planner writes none then, so one here is from an earlier plan); else None."""
    empty = [str(s["slot"]) for s in _fills_slots(d) or [] if isinstance(s, dict) and not s.get("path")]
    return f"slot(s) {', '.join(empty)} unfilled in fills.json, so this is not a complete plan (a manifest.json here is stale)" if empty else None


def _frame(t, fps):
    """The frame a time falls on, rounding half up, as the toolkit's cutlist.frame does."""
    return int(math.floor(t * fps + 0.5))


def _names(path, takes):
    """The excluded takes that name this file: its stem, basename or a path suffix, ignoring case (the toolkit's
    cutlist.exclude_music_matches rule, so the two checks agree)."""
    full = path.replace("\\", "/").lower()
    base = full.rsplit("/", 1)[-1]
    bare = full.rsplit(".", 1)[0] if "." in base else full
    out = []
    for take in takes:
        t = str(take).replace("\\", "/").strip().lower()
        if t in (base.rsplit(".", 1)[0], base) or any(x == t or x.endswith("/" + t.lstrip("/")) for x in (full, bare)):
            out.append(take)
    return out


def _music_lines(d, m, takes):
    """Lines for the manifest's music file: no path, missing or unreadable on this machine, or named by an excluded take.
    Reads the path only, as the toolkit's exclude_music_matches does ("in" defaults to 0 there). Worded as its
    assemble_timeline refuses it ("not on this machine", "excluded take(s)")."""
    music = m.get("music")
    if music is None:
        return []
    path = music.get("path") if isinstance(music, dict) else None
    if not isinstance(path, str) or not path:
        return [f"{d}: music has no path; not checked"]
    out = []
    if not os.path.isfile(path):
        out.append(f"{d}: music {path}: not on this machine")
    elif not os.access(path, os.R_OK):
        out.append(f"{d}: music {path}: not readable")
    out += [f"{d}: music {path}: excluded take {t}" for t in _names(path, takes)]
    return out


def _exclusions(c, p, ex):
    """(excluded take ids upper-cased, {relpath: take key}, {relpath: [(t_in, t_out, shot id)]} of excluded shots, {relpath: duration})
    for the shots under prefix p; check and check_shotlist share it."""
    ex_takes = {str(t).upper() for t in ex.get("takes") or []}
    rows = [dict(r) for r in c.execute("SELECT s.id, s.clip_id, s.t, s.t_in, s.t_out, cl.relpath, cl.duration FROM shots s "
                                       "JOIN clips cl ON cl.id = s.clip_id WHERE substr(cl.relpath, 1, ?) = ?", (len(p), p))]
    keys = G.take_keys(c, {r["clip_id"]: (r["relpath"], r["duration"]) for r in rows})
    take = [keys[r["clip_id"]] for r in rows]
    take_of, windows = {r["relpath"]: t for r, t in zip(rows, take)}, {}
    for i in G.excluded(rows, take, ex):
        windows.setdefault(rows[i]["relpath"], []).append((rows[i]["t_in"], rows[i]["t_out"], rows[i]["id"]))
    return ex_takes, take_of, windows, {r["relpath"]: r["duration"] for r in rows if r["duration"]}


def _why(rel, a, b, ex_takes, take_of, windows):
    """Why the span a to b of rel is ruled out by the exclude list, or None."""
    tid = G.TAKE_ID.search(os.path.basename(rel))
    key = take_of.get(rel) or (tid.group(0).upper() if tid else None)
    hit = [w[2] for w in windows.get(rel, ()) if w[0] < b and a < w[1]]
    return (f"excluded take {rel if rel.upper() in ex_takes else key}" if rel.upper() in ex_takes or key in ex_takes
            else f"overlaps excluded shot(s) {', '.join(map(str, hit))}" if hit else None)


SHOT_DEFAULT_S = 5.0  # a shotlist entry with no "out" is judged over this many seconds from "in": a real shot runs past its in point


def check_shotlist(c, prefix, path):
    """(errors, warnings) for a hand-written shot list ({"shots": [{"name", "source": absolute path, "in": seconds, "out"?}]}).
    A shot with no "out" is judged over SHOT_DEFAULT_S seconds from "in". An error is a shot the shoot's exclude list rules out (as check judges a span); a warning is a source the index does not
    hold, or a gate flag (gate.shot_flags: soft, dark, blown, smear) on the nearest indexed thumbnail of its clip. Flag
    percentiles are over the shoot's indexed shots. Raises ValueError when the shoot has no exclude list or the file is malformed."""
    p = search.shoot_prefix(prefix)
    if not os.path.exists(exclude_path(p)):
        raise ValueError(f"no exclude list at {exclude_path(p)}; nothing to check against")
    try:
        with open(path) as f:
            shots = json.load(f)["shots"]
        assert isinstance(shots, list) and all(isinstance(s, dict) and isinstance(s["source"], str) and float(s["in"]) >= 0
                                                      and (s.get("out") is None or float(s["out"]) > float(s["in"])) for s in shots)
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as e:
        raise ValueError(f"{path}: expected {{\"shots\": [{{\"name\", \"source\", \"in\"}}]}} ({type(e).__name__})")
    ex_takes, take_of, windows, _ = _exclusions(c, p, load_exclude(p))
    rows = [dict(r) for r in c.execute("SELECT s.t, s.thumb, s.motion, s.luma, cl.relpath FROM shots s "
                                       "JOIN clips cl ON cl.id = s.clip_id WHERE substr(cl.relpath, 1, ?) = ?", (len(p), p))]
    flags = dict(zip(map(id, rows), G.shot_flags(rows)))
    errs, warns = [], []
    for k, s in enumerate(shots, 1):
        name, a = s.get("name") or f"shot {k}", float(s["in"])
        b = float(s["out"]) if s.get("out") is not None else a + SHOT_DEFAULT_S
        path_ = s["source"]
        rel = path_[path_.find("/" + p) + 1:] if "/" + p in path_ else path_
        if why := _why(rel, a, b, ex_takes, take_of, windows):
            errs.append(f"{name}: {rel} {a:g}s: {why}")
        near = min((r for r in rows if r["relpath"] == rel), key=lambda r: abs(r["t"] - a), default=None)
        if near is None:
            warns.append(f"{name}: {rel} is not in the index; no gate flags")
        elif flags[id(near)]:
            warns.append(f"{name}: {rel} {a:g}s: {', '.join(flags[id(near)])} (thumbnail at {near['t']:g}s)")
    return errs, warns


def check(c, prefix, plan_dirs):
    """Lines naming each span, in the manifest.json of each plan folder, that uses footage the shoot's exclude list rules
    out: a source that is an excluded take (take id or relpath), or a span that overlaps an excluded shot's keyframe window.
    Reads only the manifest (sources [{id, path}], clips [{spans [{source, in, out}]}], music {path}), so a hand-built
    plan with no fills.json is checked like any other. The music file is checked too: it must exist and be readable, and no
    excluded take may name it (lines start "<dir>: music"). A source the index does not hold is matched by the take id in its file name.
    A manifest that is missing, unreadable, malformed (a span with no in/out, a source id not in sources, not an object) or
    empty is a line too (a plan that cannot be checked is not clean); a folder with fills.json and no manifest.json, because the
    planner left slots unfilled, says which slots. A span that opens a window (the manifest's effects, scale-window or
    crop-window) over a previous take that ends inside the plate (trim.PLATE_FRAMES) is a line too, judged as the draft
    farm judges it, when the index holds the previous take's length. Raises
    ValueError when the shoot has no exclude list, so a mistyped shoot cannot pass."""
    p = search.shoot_prefix(prefix)
    if not os.path.exists(exclude_path(p)):
        raise ValueError(f"no exclude list at {exclude_path(p)}; nothing to check against")
    ex = load_exclude(p)
    ex_takes, take_of, windows, dur_of = _exclusions(c, p, ex)
    out = []
    for d in plan_dirs:
        if why := _fills_only(d):
            out.append(f"{d}: {why}; not checked")
            continue
        try:
            with open(os.path.join(d, "manifest.json")) as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            out.append(f"{d}: cannot read manifest.json ({type(e).__name__}); not checked")
            continue
        try:
            lines, n = [], 0
            paths = {s["id"]: s["path"] for s in m.get("sources", [])}
            def rel_of(sp):  # the mount point differs (a volume path, a direct mount): cut at the shoot prefix
                path = paths[sp["source"]]
                return path[path.find("/" + p) + 1:] if "/" + p in path else path
            wins = {e["span_index"]: e["effect"] for e in m.get("effects") or [] if e.get("effect") in trim_mod.PLATE_FRAMES}
            fps = float(m.get("fps") or 24)
            for cl in m.get("clips", []):
                spans = cl.get("spans", [])
                for j, sp in enumerate(spans):
                    n += 1
                    a, b = float(sp["in"]), float(sp["out"])
                    rel = rel_of(sp)
                    if j and j in wins and cl is m["clips"][0] and rel_of(spans[j - 1]) in dur_of:  # a source the index does not hold is the farm's to judge
                        prev = spans[j - 1]
                        need = trim_mod.plate_need(wins[j], (_frame(b, fps) - _frame(a, fps)) / fps, fps) - trim_mod.PLATE_MARGIN
                        have = dur_of[rel_of(prev)] - _frame(float(prev["out"]), fps) / fps  # the farm starts the plate on the out frame
                        if have < need - 0.5 / fps:  # the draft farm's own test (drafts._check_plate)
                            lines.append(f"{d}: span {n} {wins[j]}: the previous shot has {max(have, 0):.2f}s after its out point "
                                         f"({prev['out']:g}s) and the window needs {need:.2f}s of it playing on")
                    why = _why(rel, a, b, ex_takes, take_of, windows)
                    if why:
                        lines.append(f"{d}: span {n} {rel} {a:g}-{b:g}s: {why}")
        except (KeyError, TypeError, AttributeError, ValueError) as e:  # a hand-built manifest: say which plan, keep going
            out.append(f"{d}: malformed manifest ({type(e).__name__} {e}); not checked")
            continue
        slots = _fills_slots(d)
        if slots is not None and len(slots) != n:  # fills.json is the planner's latest word: a manifest of another size is stale
            out.append(f"{d}: manifest.json holds {n} spans but fills.json has {len(slots)} slots (a stale manifest); not checked")
            continue
        out += lines + _music_lines(d, m, ex.get("takes") or [])
        if not n:
            out.append(f"{d}: manifest.json holds no spans; not checked")
    return out


def _gate_arg(c, prefix, gate, state=None):
    """None builds the gate, False means the ungated search-only path, a Gate is used as is."""
    return None if gate is False else (build_gate(c, prefix, state) if gate is None else gate)


SNAP_WINDOW_S = 0.16  # a planned cut moves to the strongest played note within this many seconds
SNAP_MIN_STRENGTH = 1.5  # ... when that note's onset strength (z-score) is at least this; otherwise the cut stays


def snap_to_onset(t, notes):
    """(time, moved): t moved onto the strongest onset of a notes file within SNAP_WINDOW_S whose strength is at least
    SNAP_MIN_STRENGTH, else t unchanged."""
    near = [(w, o) for o, w in zip(notes["onsets"], notes["strength"]) if abs(o - t) <= SNAP_WINDOW_S and w >= SNAP_MIN_STRENGTH]
    return (max(near)[1], True) if near else (t, False)


def check_notes(notes):
    """The notes file ({period, onsets, strength}) when it can drive a plan; ValueError otherwise."""
    try:
        ok = float(notes["period"]) > 0 and len(notes["onsets"]) == len(notes["strength"])
    except (KeyError, TypeError, ValueError):
        ok = False
    if not ok:
        raise ValueError("notes: expected {period > 0, onsets: [...], strength: [...]} with one strength per onset")
    return notes


def fill_slots(c, method, prefix, music=None, others=(), gate=False, trim_mode="motion", beats=None, theme=None, uses=None,
               beat_start=0, notes=None):
    """Fill every slot. gate: a Gate (only gated shots are candidates) or False (search only). others: (take set, slot
    count) of earlier variants; a take from one is used only while this plan shares at most half of either plan's slots
    with it. beats: the track's beat times in seconds; each slot is counted in beats of the method's own tempo (to the half
    beat) and laid on the track from beat index beat_start, so every cut is on a beat or a half beat. theme: a z array over
    the gate's shots that biases the fills. uses: take use counts, shared across variants. notes: a notes file (period,
    onsets, strength) instead of beats: the grid is the notes' period from beat_start, and each cut the grid would place
    moves by snap_to_onset; the slot lengths follow from the moved cuts. Not both."""
    if beats and notes:
        raise ValueError("beats and notes both given; a plan takes one of them")
    g = gate or None
    used, slots, picks, used_takes, last, own = set(), [], [], set(), None, []
    uses = Counter() if uses is None else uses
    reuse = (method.get("rules") or {}).get("reuse", False)
    cap = len(method["slots"]) // 2
    bpm, fps, bi = sum(method.get("bpm") or [120, 120]) / 2, float(method.get("fps") or 24), 0.0
    moved = 0
    if notes:  # a uniform grid is two points: beat_time runs on at that period
        period = float(check_notes(notes)["period"])
        beats = [beat_start * period, (beat_start + 1) * period]
    elif beats:
        beats = beats[beat_start:]
    if beats:
        beats = trim_mod.fit_grid(beats, bpm)
    start_s = cursor = beats[0] if beats else 0.0
    nxt_same = lambda k: k < len(method["slots"]) and SAME_TAKE.search(f"{method['slots'][k]['role']} {method['slots'][k]['query']}")
    for n, s in enumerate(method["slots"], 1):
        seconds = slot_seconds(method, s)
        handle = _handle(method, n)  # a window on the next slot plays over this take past its out point (trim.plate_need)
        if beats:  # the cut goes on the beat, then on the method's frame grid so frames do not drift
            bi += trim_mod.beat_units(seconds, bpm)
            at = trim_mod.beat_time(beats, bi)
            if notes:
                to, hit = snap_to_onset(at, notes)
                if hit and to - cursor >= 1 / fps:  # a note the previous cut already took (or passed) leaves this cut on the grid
                    at, moved = to, moved + 1
            end = start_s + round((at - start_s) * fps) / fps
            seconds = round(end - cursor, 6)
        if last and SAME_TAKE.search(f"{s['role']} {s['query']}"):
            cont = trim_mod.continue_span(last[1]["out_s"], seconds, last[0]["duration"] or 0, handle)
            if cont:
                f = fill(method, s, n, last[0], cont, seconds, handle)
                slots.append(f); cursor += seconds
                last = (last[0], f)
                continue
        need = seconds + max(slot_seconds(method, method["slots"][n]) if nxt_same(n) else 0, handle)
        ctx = {"uses": uses, "picks": picks, "used_takes": set() if reuse else used_takes, "need": need, "theme": theme}
        cand = candidates(c, method, s, prefix, used=() if reuse else used, limit=20 if others else 1, gate=g, ctx=ctx,
                          seconds=need if g is None else seconds, handle=handle)
        mine = {x["relpath"] for x in slots if x.get("path")}
        ok = [r for r in cand if all(r["relpath"] not in o or len((mine | {r["relpath"]}) & o) <= min(cap, k // 2)
                                     for o, k in others)]
        if not ok and reuse:  # a reuse method returns to a take of its own (the same role first): no new overlap
            ok = [r for r, f in sorted(own, key=lambda p: p[1]["role"] != s["role"])
                  if (r["duration"] or 0) >= seconds + handle + LEN_MARGIN and r["t"] <= r["duration"] - handle][:1]
        if not ok:
            slots.append({"slot": n, "role": s["role"], "query": s["query"], "seconds": seconds,
                          "path": None, "unfilled": "no unused take long enough in this shoot"})
            cursor += seconds
            continue
        r = ok[0]
        f = fill(method, s, n, r, _span(r, seconds, trim_mode, handle), seconds, handle)
        f["cut_s"] = round(cursor + seconds, 3) if beats else None
        used |= {f["relpath"], f["low_res"]} - {None}
        if g is not None:
            uses[r["take"]] += 1; picks.append(r["idx"]); used_takes.add(r["take"])
        slots.append(f); own.append((r, f)); cursor += seconds
        last = (r, f)
    out = {"fills": 1, "method": method["id"], "shoot": prefix, "music": music, "slots": slots}
    if beats:
        out["music_in_s"] = round(start_s, 3)
        out["route"] = "notes" if notes else "grid"
    if notes:
        out["cuts_on_onset"] = moved
    if g is not None:
        out["state"] = g.state
    return out


def plan(c, method, prefix, music=None, gate=None, trim_mode="motion", beats=None, state=None, beat_start=0, notes=None):
    prefix = search.shoot_prefix(prefix)
    fills = fill_slots(c, method, prefix, music, gate=_gate_arg(c, prefix, gate, state), trim_mode=trim_mode, beats=beats,
                       beat_start=beat_start, notes=notes)
    d = out_dir(method["id"], prefix)
    return fills, d, _write(method, fills, d)


TASTE = config.get("taste_profile")  # "" = no profile
EVENTS = config.get("review_log")  # "" = no log
LOOKS = os.path.join(TOOLKIT, "lutlab-looks")


def _norm(x):
    return re.sub(r"[^a-z0-9]+", "", str(x).lower())


def _take_key(relpath):
    """A take named the same whether it is the full-res file or its low-res handoff."""
    return _norm(os.path.splitext(os.path.basename(str(relpath).split("@")[0]))[0])


def taste(looks):
    """(take keys picked in the review log, looks that won: chosen in the log, or before "over" in a profile line).
    The profile orders; it never chooses."""
    won = []
    for line in (open(TASTE).read().lower().splitlines() if os.path.exists(TASTE) else []):
        if " over " in line:
            won.append(line.split(" over ")[0])
    picked = set()
    for line in open(EVENTS) if os.path.exists(EVENTS) else []:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        opts, ch = e.get("options"), e.get("choice")
        val = e.get("decoded") or (opts.get(ch) if isinstance(opts, dict) else ch)
        if isinstance(val, str) and "/" in val:
            picked.add(_take_key(val))
        elif val:
            won.append(str(val).lower())
    text = _norm(" ".join(won))
    return picked, {lk for lk in looks if len(_norm(lk)) >= 3 and _norm(lk) in text}


def paced(method, pace):
    """The method with every slot length scaled by pace, never under rules.min_shot_s."""
    if pace == 1.0:
        return method
    floor = (method.get("rules") or {}).get("min_shot_s") or 0
    slots = [{**s, "seconds": round(max(floor, slot_seconds(method, s) * pace), 3)} for s in method["slots"]]
    end = re.match(r"black:([0-9.]+)$", str((method.get("rules") or {}).get("end") or ""))
    return {**method, "slots": slots, "length_s": round(sum(x["seconds"] for x in slots) + (float(end[1]) if end else 0), 3)}


def pace_for(method, k, period=None):
    """Variant k's pacing: as written, tighter, looser. Beat-snapped methods move inside their bpm range. period: the
    notes file's period; the middle of that range is then the track's tempo (halved or doubled toward the method's, kept
    inside the range). Without it the flag promises no grid and the range's own middle is used."""
    bpm = method.get("bpm")
    if (method.get("rules") or {}).get("snap_to_beat") and bpm and bpm[1] > bpm[0]:
        mid = sum(bpm) / 2
        if period:
            mid = min(bpm[1], max(bpm[0], min((0.5, 1, 2), key=lambda m: abs(math.log(mid / (60 / period * m)))) * 60 / period))
        return [1.0, round(mid / bpm[1], 3), round(mid / bpm[0], 3)][k % 3]
    return [1.0, 0.85, 1.2][k % 3]


def variants(c, prefix, n=6, music=None, methods=None, gate=None, trim_mode="motion", beats=None, state=None, beat_start=0,
             notes=None):
    """n complete plans that differ in method, fills, look and pacing; no two share more than half their takes.
    Each goes to <edits_dir>/<shoot>-variants/vK-<method>/ with fills.json, method.json and the manifest;
    variants.json there ranks them by method fit plus the taste profile and the review log."""
    prefix = search.shoot_prefix(prefix)
    methods = methods if methods is not None else load_methods()
    if not methods:
        raise ValueError(f"no methods in {METHODS}")
    g = _gate_arg(c, prefix, gate, state)
    uses = Counter()
    ranked = rank(c, prefix, methods, top=None)["methods"]
    by_id = {m["id"]: m for m in methods}
    looks = sorted(os.path.splitext(f)[0] for f in os.listdir(LOOKS)) if os.path.isdir(LOOKS) else []
    picked, named = taste(looks)
    pool = sorted(looks, key=lambda lk: (lk not in named, lk))
    root = os.path.join(EDITS, f"{slug(prefix)}-variants")
    out, others, used_looks = [], [], []
    for k in range(n):
        r = ranked[k % len(ranked)]
        pace = pace_for(by_id[r["id"]], k, float(check_notes(notes)["period"]) if notes else None)
        m = paced(by_id[r["id"]], pace)
        mine = [lk for lk in [m.get("look")] + pool if lk and (lk, m["id"]) not in used_looks]
        look = next((lk for lk in mine if lk not in {x for x, _ in used_looks}), mine[0] if mine else None)
        used_looks.append((look, m["id"]))
        m = {**m, "look": look}
        fills = {**fill_slots(c, m, prefix, music, others, gate=g, trim_mode=trim_mode, beats=beats, uses=uses,
                              beat_start=beat_start, notes=notes),
                 "pace": pace, "look": look}
        takes = {x["relpath"] for x in fills["slots"] if x.get("path")}
        others.append((takes, len(m["slots"])))
        # a handoff's name carries its camera file's stem: the id P1900001 matches v1_P1900001_dish
        liked = sum(1 for t in takes if any(len(tk := _take_key(t)) >= 4 and tk in p for p in picked)) / max(len(takes), 1)
        score = r["score"] + 0.1 * liked + (0.05 if look in named else 0)
        reasons = [f"method fit {r['score']}"] + ([f"{liked:.0%} of takes picked before in review"] if liked else []) \
            + ([f"look {look} won before (taste profile or review log)"] if look in named else [])
        d = os.path.join(root, f"v{k + 1}-{m['id']}")
        with open(os.path.join(_mk(d), "method.json"), "w") as f:
            json.dump(m, f, indent=1)
        out.append({"variant": k + 1, "dir": d, "method": m["id"], "look": look, "pace": pace,
                    "score": round(score, 3), "reasons": reasons, "takes": len(takes),
                    "unfilled": [x["slot"] for x in fills["slots"] if not x.get("path")], "plate_note": plate_note(fills),
                    "wrote": _write(m, fills, d)})
    out.sort(key=lambda v: -v["score"])
    with open(os.path.join(root, "variants.json"), "w") as f:
        json.dump(out, f, indent=1)
    return out, root


def variants_within(c, method, prefix, n=4, music=None, gate=None, trim_mode="motion", beats=None, state=None, beat_start=0,
                    avoid=(), notes=None):
    """n complete plans of ONE method, look and pace; only the fills differ. Plan 1 takes the best fills; each later plan
    leans on the next theme the shoot supports (THEMES, at least slots + 1 gated takes with theme z > 0.8), and when themes
    run out, on the take-use penalty alone. No two plans share more than half their takes. avoid: variant folders of
    other methods on this shoot; each take any of their plans used counts as one earlier use, so the shoot's videos do not
    all open on the same shots. Written to <edits>/<shoot>-<method>-variants/vK-<theme or top>/ with variants.json
    (ranked by mean slot score)."""
    prefix = search.shoot_prefix(prefix)
    g = _gate_arg(c, prefix, gate, state)
    if g is None:
        raise ValueError("within-method variants need the gate")
    need = len(method["slots"]) + 1
    have = len({g.take[i] for i in np.flatnonzero(g.ok)})
    budget = {"gated_takes": have, "need": take_need(method, n), "short": have < take_need(method, n)}
    support = {name: len({g.take[i] for i in np.flatnonzero(g.ok & (g.zt(text) > 0.8))}) for name, text in THEMES.items()}
    names = [k for k in sorted(support, key=lambda k: (-support[k], k)) if support[k] >= need]
    root = os.path.join(EDITS, f"{slug(prefix)}-{method['id']}-variants")
    uses, others, out = Counter(), [], []
    for folder in avoid:
        spent = set()
        for name in sorted(os.listdir(folder)):
            if os.path.isfile(os.path.join(folder, name, "fills.json")):
                with open(os.path.join(folder, name, "fills.json")) as f:
                    spent |= {x["take"] for x in json.load(f)["slots"] if x.get("take")}
        uses.update(spent)
    for k in range(n):
        theme = names[k - 1] if 0 < k <= len(names) else None
        fills = fill_slots(c, method, prefix, music, others, gate=g, trim_mode=trim_mode, beats=beats,
                           theme=g.zt(THEMES[theme]) if theme else None, uses=uses, beat_start=beat_start,
                           notes=notes)
        fills["theme"] = theme
        takes = {x["relpath"] for x in fills["slots"] if x.get("path")}
        others.append((takes, len(method["slots"])))
        d = os.path.join(root, f"v{k + 1}-{theme or 'top'}")
        with open(os.path.join(_mk(d), "method.json"), "w") as f:
            json.dump(method, f, indent=1)
        sc = [x["score"] for x in fills["slots"] if x.get("score") is not None]
        out.append({"variant": k + 1, "dir": d, "method": method["id"], "theme": theme, "takes": len(takes),
                    "score": round(float(np.mean(sc)), 3) if sc else 0.0,
                    "unfilled": [x["slot"] for x in fills["slots"] if not x.get("path")], "plate_note": plate_note(fills),
                    "wrote": _write(method, fills, d)})
    out.sort(key=lambda v: -v["score"])
    with open(os.path.join(root, "variants.json"), "w") as f:
        json.dump({"variants": out, "budget": budget}, f, indent=1)
    return out, root, budget


def _mk(d):
    os.makedirs(d, exist_ok=True)
    return d


SHEET_MAX = 10  # per-shot picks suit a short method; a long one is judged as whole drafts (plan --variants)


def variant_dir(method_id, prefix, name):
    """The folder of variant `name` ("v2", or a whole folder name) under <edits>/<shoot>-<method>-variants/, where
    `plan METHOD PREFIX --variants N` writes vK-<theme or top>/. "vK" is the one directory named vK or vK-<word> (no dot, so
    "v1-top.bak-old" and files are ignored); "v1" never takes in "v10-x"."""
    root = os.path.join(EDITS, f"{slug(prefix)}-{method_id}-variants")
    have = sorted(os.listdir(root)) if os.path.isdir(root) else []
    have = [n for n in have if os.path.isdir(os.path.join(root, n))]
    hits = [n for n in have if n == name or re.fullmatch(re.escape(name) + r"-[^.]+", n)]
    if len(hits) != 1:
        raise ValueError(f"{'no' if not hits else 'several'} variant {name!r} in {root}"
                         + (f" ({', '.join(have)})" if have else ""))
    return os.path.join(root, hits[0])


def _plan_fills(method, prefix, plan_dir=None):
    """(plan folder, its fills.json) for the method on the shoot. plan_dir: a variants folder (variant_dir) or any plan
    folder, used as the default <edits>/<shoot>-<method>/ is; its fills must be this method's."""
    if plan_dir == "":
        raise ValueError("empty plan folder")
    d = os.path.abspath(os.path.expanduser(plan_dir)) if plan_dir is not None else out_dir(method["id"], prefix)
    try:
        with open(os.path.join(d, "fills.json")) as f:
            fills = json.load(f)
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError(f"no plan at {d}" + ("" if plan_dir is not None else f"; run plan {method['id']} {prefix} first"))
    if fills.get("method", method["id"]) != method["id"]:
        raise ValueError(f"{d} holds a plan of {fills['method']}, not {method['id']}")
    return d, fills


def _short(method):
    if len(method["slots"]) > SHEET_MAX:
        raise ValueError(f"{method['id']} has {len(method['slots'])} slots; per-slot sheets are for {SHEET_MAX} or fewer "
                         "(judge a longer method as whole drafts: plan --variants)")


def _gate_once(c, prefix, gate, state):
    """_gate_arg resolved once for a run of calls: a Gate, or False for ungated (None would mean build it again)."""
    g = _gate_arg(c, prefix, gate, state)
    return False if g is None else g


def _slot_cands(c, method, slot_n, prefix, fills, g):
    """The slot's top 5 unused takes, drawn against the other slots as fills holds them. g: a Gate or False."""
    if not 1 <= slot_n <= len(method["slots"]):
        raise ValueError(f"slot {slot_n}: the method has slots 1 to {len(method['slots'])}")
    reuse = (method.get("rules") or {}).get("reuse", False)
    used = set() if reuse else {x for o in fills["slots"] if o["slot"] != slot_n for x in (o.get("relpath"), o.get("low_res")) if x}
    # the length the plan laid for the slot (beat-laid under --beats), so a take must fit that
    seconds = fills["slots"][slot_n - 1].get("seconds") or slot_seconds(method, method["slots"][slot_n - 1])
    return candidates(c, method, method["slots"][slot_n - 1], prefix, used=used, limit=5, gate=g or None,
                      seconds=seconds + _handle(method, slot_n), handle=_handle(method, slot_n))


def _fill_pick(method, slot_n, letter, cand, g, trim_mode, sheet, old):
    """The slot's new fill: the take `letter` names on its sealed sheet, cut to the length and music cut of `old`, the fill
    it replaces. Refuses a letter not on the sheet and a take that is not among the current candidates, saying so when the
    shoot's exclude list is why. Never prints the key."""
    try:
        with open(sheet + ".key/key.json") as f:
            labels = json.load(f)["labels"]
    except FileNotFoundError:
        raise ValueError(f"slot {slot_n}: no sheet at {sheet}; make one first")
    letter = str(letter).upper()
    if letter not in labels:
        raise ValueError(f"slot {slot_n}: no letter {letter} on that sheet ({', '.join(sorted(labels))})")
    src = labels[letter]["source"]
    pick = next((r for r in cand if os.path.abspath(r["thumb"]) == src), None)
    if pick is None:
        if g and any(g.reason[i] == "excluded" and os.path.abspath(g.rows[i]["thumb"]) == src for i in range(g.n)):
            raise ValueError(f"slot {slot_n} {letter}: that take is on the shoot's exclude list")
        raise ValueError(f"slot {slot_n} {letter}: that take is no longer a free candidate; make a new sheet")
    s = method["slots"][slot_n - 1]
    seconds = old.get("seconds") or slot_seconds(method, s)
    handle = _handle(method, slot_n)
    new = fill(method, s, slot_n, pick, _span(pick, seconds, trim_mode, handle), seconds, handle)
    new["cut_s"] = old.get("cut_s")  # the slot keeps its place on the beat
    return pick, new


def swap(c, method, prefix, slot_n, letter=None, gate=None, trim_mode="motion", state=None, plan_dir=None):
    """Without a letter: a lettered sheet of the slot's top 5 unused takes. With one: put that take in the slot. gate: None
    builds the shoot's gate, so the exclude list applies (as plan does); False searches ungated; a Gate is used as is.
    state: the colour state to draw from; when none is given, the one the plan was made with (fills.json), so a plan made
    with an explicit state is swapped from that state. plan_dir: the plan folder to act on, a variants folder for one
    (variant_dir); the default is <edits>/<shoot>-<method>/."""
    prefix = search.shoot_prefix(prefix)
    d, fills = _plan_fills(method, prefix, plan_dir)
    g = _gate_once(c, prefix, gate, state or fills.get("state"))
    cand = _slot_cands(c, method, slot_n, prefix, fills, g)
    sheet = os.path.join(d, f"swap-{slot_n}")
    if letter is None:
        if len(cand) < 2:
            raise ValueError(f"slot {slot_n}: only {len(cand)} unused take(s) fit; nothing to choose between")
        key = _absheet([r["thumb"] for r in cand], sheet, f"slot {slot_n} {method['slots'][slot_n - 1]['role']}")
        return {"sheet": os.path.join(sheet, "sheet.jpg"), "letters": sorted(key["labels"])}
    fills["slots"][slot_n - 1] = _fill_pick(method, slot_n, letter, cand, g, trim_mode, sheet, fills["slots"][slot_n - 1])[1]
    return {"fills": fills, "dir": d, "wrote": _write(method, fills, d)}


def slots_sheet(c, method, prefix, gate=None, state=None, plan_dir=None):
    """swap's lettered sheet for every slot of a plan of SHEET_MAX slots or fewer, into the plan folder (swap-1/ to swap-N/,
    each key sealed beside it). A slot with fewer than two unused takes gets no sheet and keeps its take. Returns
    {"dir", "sheets": {slot: swap's result (sheet path, letters)}, "skipped": {slot: why}}."""
    _short(method)
    prefix = search.shoot_prefix(prefix)
    d, fills = _plan_fills(method, prefix, plan_dir)
    g = _gate_once(c, prefix, gate, state or fills.get("state"))
    sheets, skipped = {}, {}
    for n in range(1, len(method["slots"]) + 1):
        try:
            sheets[n] = swap(c, method, prefix, n, gate=g, state=state, plan_dir=d)
        except ValueError as e:
            skipped[n] = str(e)
    return {"dir": d, "sheets": sheets, "skipped": skipped}


def compose(c, method, prefix, picks, gate=None, trim_mode="motion", state=None, plan_dir=None):
    """Put one take per named slot, picks {slot: letter} read through each slot's sealed sheet (slots_sheet), and write the
    plan once. Each pick is judged as swap judges it, against the plan the sheets were made from: the gate and the exclude
    list as they stand now, a take still free, no take picked twice (unless the method reuses). Any refusal writes nothing.
    A slot not named keeps its take. plan_dir as in swap."""
    _short(method)
    prefix = search.shoot_prefix(prefix)
    try:
        by_slot = {int(k): v for k, v in picks.items()}
    except (ValueError, TypeError, AttributeError):
        by_slot = {}
    if not by_slot or len(by_slot) != len(picks):
        raise ValueError('picks: expected {"1": "B", "2": "A", ...}, one letter per slot number')
    d, fills = _plan_fills(method, prefix, plan_dir)
    g = _gate_once(c, prefix, gate, state or fills.get("state"))
    new, taken = json.loads(json.dumps(fills)), {}
    reuse = (method.get("rules") or {}).get("reuse", False)
    for n, letter in sorted(by_slot.items()):
        cand = _slot_cands(c, method, n, prefix, fills, g)
        pick, new["slots"][n - 1] = _fill_pick(method, n, letter, cand, g, trim_mode, os.path.join(d, f"swap-{n}"),
                                               fills["slots"][n - 1])
        key = pick.get("take") or pick["relpath"]
        if key in taken and not reuse:
            raise ValueError(f"slots {taken[key]} and {n} picked the same take")
        taken[key] = n
    return {"fills": new, "dir": d, "wrote": _write(method, new, d)}
