"""rpstills.cull: a ranked proposal per run, written as cull.json.

Score per frame, inside its run (so numbers compare only between frames of
the same person or group):
  quality    Vision capture quality of the main face, 0 to 1 (weight 0.5)
  sharp      face sharpness, rank-normalized within the run (weight 0.2)
  eyes       1 when every counted face has both eyes open, 0.5 when one is
             doubtful, 0 when any face has both eyes closed (weight 0.2)
  size       main face height as a share of the frame, capped (weight 0.1)
A run with a counted face is ranked this way (cluster.counted_faces says which
faces count: an audience behind a performer does not). PICKS per solo run,
GROUP_PICKS per pair or group run, one per burst at most.

A run with no counted face (class "none": a wide stage shot, a room, a
landscape) used to get no pick. It is ranked by the frame itself instead
(frames.py): sharpness ranked inside the run, exposure, and composition when
Vision found a person. Such a run proposes NONE_PICKS frames, one more for
each NONE_PER_FRAMES frames it holds up to NONE_MAX, one per burst, none
whose score is under NONE_FLOOR (a blank, flat or out-of-focus frame never
makes it), and none that is a near-duplicate (perceptual hash within
NEAR_DUPLICATE bits) of one already picked. A face run from frame grabs (rows whose time came from a sidecar, a file-name stamp or a sequence) skips a
frame that is all but identical (within NEAR_DUPLICATE_FACE bits) to a pick of
its own, which a static camera and a performer who sits still produce by the
dozen. Camera JPEGs keep every frame: a perceptual hash cannot see the eyes or
the expression that make two near-identical shots different pictures. An index written before the frame measures existed
has none to rank by and proposes nothing for such a run, as before; running
any later stage on it adds the measures.

Nothing is deleted: every frame keeps its score and the review sheet shows
them all. Each score and each proposal says which kind it is, "face" or
"frame".
"""

import json
import os
from bisect import bisect_left

from . import frames
from .cluster import counted_faces, hash_distance

PICKS = 3
GROUP_PICKS = 3
MIN_FACE = 0.04
EYE_OPEN = 0.22
EYE_CLOSED = 0.15
NONE_PICKS = 2
NONE_PER_FRAMES = 12
NONE_MAX = 4
NONE_FLOOR = 0.30
NEAR_DUPLICATE = 8
NEAR_DUPLICATE_FACE = 4


def _eyes(face):
    vals = [v for v in (face.get("eye_left"), face.get("eye_right")) if v is not None]
    if not vals:
        return 0.5
    if all(v >= EYE_OPEN for v in vals):
        return 1.0
    if all(v < EYE_CLOSED for v in vals):
        return 0.0
    return 0.5


def _counted_pairs(r):
    """[(face, sharpness)] for the faces that count in a row."""
    faces = r.get("faces") or []
    sharp = r.get("sharpness") or [None] * len(faces)
    keep = {id(f) for f in counted_faces(r, MIN_FACE)}
    return [(f, s) for f, s in zip(faces, sharp) if id(f) in keep]


def _score_faces(frames_, run, burst_of, scored):
    parts = []
    for r in frames_:
        faces = _counted_pairs(r)
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
        s["kind"] = "face"
        s["run"] = run["id"]
        s["burst"] = burst_of.get(r["id"])
        scored[r["id"]] = s


def _score_frames(frames_, run, burst_of, scored):
    sharps = sorted(r["frame"]["sharp"] for r in frames_
                    if r.get("frame") and r["frame"].get("sharp") is not None)
    for r in frames_:
        m = r.get("frame")
        if not m or m.get("sharp") is None:
            s = {"score": 0.0, "sharp": None, "sharp_rank": None, "exposure": None,
                 "composition": None, "people": len(r.get("people") or [])}
        else:
            rank = (bisect_left(sharps, m["sharp"]) / float(max(1, len(sharps) - 1))
                    if len(sharps) > 1 else 0.5)
            exposure = frames.exposure_score(m)
            composition = frames.composition_score(r.get("people"))
            blurry = frames.is_blurry(m)
            s = {"score": 0.0 if blurry else frames.frame_score(rank, exposure, composition),
                 "sharp": m["sharp"], "sharp_rank": round(rank, 3), "exposure": exposure,
                 "composition": composition, "people": len(r.get("people") or []),
                 "blurry": blurry}
        s["kind"] = "frame"
        s["run"] = run["id"]
        s["burst"] = burst_of.get(r["id"])
        scored[r["id"]] = s


def score_rows(rows, clusters):
    by_id = {r["id"]: r for r in rows}
    burst_of = {fid: i for i, b in enumerate(clusters["bursts"]) for fid in b}
    scored = {}
    for run in clusters["runs"]:
        frames_ = [by_id[f] for f in run["frames"] if f in by_id]
        if run["class"] == "none":
            _score_frames(frames_, run, burst_of, scored)
        else:
            _score_faces(frames_, run, burst_of, scored)
    return scored


def frame_pick_count(run):
    """How many frames a run with no counted face may propose."""
    return min(NONE_MAX, max(NONE_PICKS, len(run["frames"]) // NONE_PER_FRAMES))


def propose(rows, clusters, scored):
    by_id = {r["id"]: r for r in rows}
    proposal = []
    for run in clusters["runs"]:
        ranked = sorted(run["frames"], key=lambda f: -scored.get(f, {}).get("score", 0))
        if run["class"] == "none":
            want = frame_pick_count(run)
            picks, used_bursts = [], set()
            for f in ranked:
                s = scored.get(f, {})
                if s.get("score", 0) < NONE_FLOOR:
                    break
                if s.get("burst") in used_bursts:
                    continue
                phash = by_id.get(f, {}).get("phash")
                if any(hash_distance(phash, by_id.get(p, {}).get("phash")) <= NEAR_DUPLICATE for p in picks):
                    continue
                picks.append(f)
                used_bursts.add(s.get("burst"))
                if len(picks) >= want:
                    break
            proposal.append({"run": run["id"], "class": run["class"], "kind": "frame",
                             "picks": picks, "ranked": ranked})
            continue
        want = PICKS if run["class"] == "solo" else GROUP_PICKS
        picks, used_bursts = [], set()
        for f in ranked:
            s = scored.get(f, {})
            if s.get("score", 0) <= 0:
                break
            if s.get("burst") in used_bursts:
                continue
            phash = by_id.get(f, {}).get("phash")
            if (by_id.get(f, {}).get("time_source") in ("sidecar", "filename", "sequence") and
                    any(hash_distance(phash, by_id.get(p, {}).get("phash")) <= NEAR_DUPLICATE_FACE
                        for p in picks)):
                continue
            picks.append(f)
            used_bursts.add(s.get("burst"))
            if len(picks) >= want:
                break
        proposal.append({"run": run["id"], "class": run["class"], "kind": "face",
                         "picks": picks, "ranked": ranked})
    return proposal


def build(rows, clusters):
    scored = score_rows(rows, clusters)
    return {"scores": scored, "proposal": propose(rows, clusters, scored),
            "params": {"picks": PICKS, "group_picks": GROUP_PICKS, "min_face": MIN_FACE,
                       "eye_open": EYE_OPEN, "eye_closed": EYE_CLOSED, "none_picks": NONE_PICKS,
                       "none_per_frames": NONE_PER_FRAMES, "none_max": NONE_MAX,
                       "none_floor": NONE_FLOOR, "near_duplicate": NEAR_DUPLICATE,
                       "near_duplicate_face": NEAR_DUPLICATE_FACE, "blur_below": frames.BLUR_BELOW}}


def write(out_dir, cull):
    path = os.path.join(out_dir, "cull.json")
    with open(path, "w") as f:
        json.dump(cull, f, indent=1, sort_keys=True)
    return path


def summary(cull):
    picks = sum(len(p["picks"]) for p in cull["proposal"])
    runs = sum(1 for p in cull["proposal"] if p["picks"])
    text = f"proposed {picks} picks across {runs} runs"
    fr = [p for p in cull["proposal"] if p.get("kind") == "frame"]
    if fr:
        n = sum(len(p["picks"]) for p in fr)
        text += (f"; {n} of them chosen by the frame itself in {sum(1 for p in fr if p['picks'])} "
                 f"of {len(fr)} runs with no subject face")
    return text
