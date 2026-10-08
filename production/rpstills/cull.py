"""rpstills.cull: a ranked proposal per run, written as cull.json.

Score per frame, inside its run (so numbers compare only between frames of
the same person or group):
  quality    Vision capture quality of the main face, 0 to 1 (weight 0.5)
  sharp      face sharpness, rank-normalized within the run (weight 0.2)
  eyes       1 when every counted face has both eyes open, 0.5 when one is
             doubtful, 0 when any face has both eyes closed (weight 0.2)
  size       main face height as a share of the frame, capped (weight 0.1)
A frame with no counted face scores 0. One pick per burst at most; PICKS
per solo run, GROUP_PICKS per pair or group run. Nothing is deleted:
every frame keeps its score and the review sheet shows them all.
"""

import json
import os

from .cluster import frame_class

PICKS = 3
GROUP_PICKS = 3
MIN_FACE = 0.04
EYE_OPEN = 0.22
EYE_CLOSED = 0.15


def _eyes(face):
    vals = [v for v in (face.get("eye_left"), face.get("eye_right")) if v is not None]
    if not vals:
        return 0.5
    if all(v >= EYE_OPEN for v in vals):
        return 1.0
    if all(v < EYE_CLOSED for v in vals):
        return 0.0
    return 0.5


def score_rows(rows, clusters):
    by_id = {r["id"]: r for r in rows}
    burst_of = {fid: i for i, b in enumerate(clusters["bursts"]) for fid in b}
    scored = {}
    for run in clusters["runs"]:
        frames = [by_id[f] for f in run["frames"] if f in by_id]
        parts = []
        for r in frames:
            faces = [(f, s) for f, s in zip(r.get("faces") or [], r.get("sharpness") or [None] * len(r.get("faces") or []))
                     if f.get("h", 0) >= MIN_FACE]
            if not faces:
                parts.append((r, None, None, 0.0, 0.0))
                continue
            main, main_sharp = max(faces, key=lambda fs: fs[0]["h"])
            quality = main.get("quality")
            eyes = min(_eyes(f) for f, _ in faces)
            size = min(main["h"] / 0.5, 1.0)
            parts.append((r, quality, main_sharp, eyes, size))
        sharps = sorted(p[2] for p in parts if p[2] is not None)
        for r, quality, sharp, eyes, size in parts:
            if quality is None and sharp is None:
                s = {"score": 0.0, "quality": None, "sharp": None, "eyes": eyes, "size": size}
            else:
                sharp_rank = (sharps.index(sharp) / max(1, len(sharps) - 1)) if sharp is not None and len(sharps) > 1 else 0.5
                q = quality if quality is not None else 0.5
                s = {"score": round(0.5 * q + 0.2 * sharp_rank + 0.2 * eyes + 0.1 * size, 4),
                     "quality": quality, "sharp": sharp, "eyes": eyes, "size": round(size, 3)}
            s["run"] = run["id"]
            s["burst"] = burst_of.get(r["id"])
            scored[r["id"]] = s
    return scored


def propose(rows, clusters, scored):
    proposal = []
    for run in clusters["runs"]:
        if run["class"] == "none":
            proposal.append({"run": run["id"], "class": run["class"], "picks": [], "ranked": run["frames"]})
            continue
        ranked = sorted(run["frames"], key=lambda f: -scored.get(f, {}).get("score", 0))
        want = PICKS if run["class"] == "solo" else GROUP_PICKS
        picks, used_bursts = [], set()
        for f in ranked:
            s = scored.get(f, {})
            if s.get("score", 0) <= 0:
                break
            if s.get("burst") in used_bursts:
                continue
            picks.append(f)
            used_bursts.add(s.get("burst"))
            if len(picks) >= want:
                break
        proposal.append({"run": run["id"], "class": run["class"], "picks": picks, "ranked": ranked})
    return proposal


def build(rows, clusters):
    scored = score_rows(rows, clusters)
    return {"scores": scored, "proposal": propose(rows, clusters, scored),
            "params": {"picks": PICKS, "group_picks": GROUP_PICKS, "min_face": MIN_FACE,
                       "eye_open": EYE_OPEN, "eye_closed": EYE_CLOSED}}


def write(out_dir, cull):
    path = os.path.join(out_dir, "cull.json")
    with open(path, "w") as f:
        json.dump(cull, f, indent=1, sort_keys=True)
    return path


def summary(cull):
    picks = sum(len(p["picks"]) for p in cull["proposal"])
    runs = sum(1 for p in cull["proposal"] if p["picks"])
    return f"proposed {picks} picks across {runs} runs"
