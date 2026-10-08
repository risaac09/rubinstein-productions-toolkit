"""rpstills.cluster: segments, bursts and runs from an index.

- segment: frames separated by a gap longer than SEGMENT_GAP_S belong to
  different segments (a headshot session and the event after it).
- burst: consecutive frames within BURST_GAP_S whose phash distance is at
  most BURST_HASH; one pick per burst at most.
- run: consecutive frames of the same class (solo, pair, group, none) with
  no gap over RUN_GAP_S. Inside solo frames a run also ends when the next
  person steps in: a pause of PERSON_GAP_S or more together with a face-crop
  hash distance of PERSON_HASH or more, or either alone past HARD_GAP_S or
  HARD_HASH. A false split costs one shared name on the sheet; a missed
  split hides a person, so the rule leans toward splitting. The sheet asks
  Isaac for the name, the pipeline never guesses one.

Writes clusters.json: {"segments": [{"id", "start", "end", "frames"}],
"bursts": [[ids]], "runs": [{"id", "segment", "class", "frames", "start",
"end"}]}. Frame order is the index order (file name order).
"""

import datetime as _dt
import json
import os

SEGMENT_GAP_S = 20 * 60
BURST_GAP_S = 2.0
BURST_HASH = 8
RUN_GAP_S = 45
PERSON_GAP_S = 10
PERSON_HASH = 16
HARD_GAP_S = 30
HARD_HASH = 30


def _t(row):
    if not row.get("time"):
        return None
    t = _dt.datetime.fromisoformat(row["time"])
    sub = row.get("subsec")
    if sub and sub.isdigit():
        t += _dt.timedelta(seconds=int(sub) / (10 ** len(sub)))
    return t


def _hash_distance(a, b):
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def frame_class(row, min_face=0.04):
    """solo, pair, group or none, counting faces whose height is at least
    min_face of the upright image (a bystander in the background does not
    make a group)."""
    n = sum(1 for f in row.get("faces") or [] if f.get("h", 0) >= min_face)
    return "none" if n == 0 else "solo" if n == 1 else "pair" if n == 2 else "group"


def build(rows):
    rows = [r for r in rows if not r.get("error")]
    segments, bursts, runs = [], [], []
    prev, prev_t = None, None
    for r in rows:
        t = _t(r)
        # a frame without a time stays with its neighbours (gap 0)
        gap = None if prev is None else ((t - prev_t).total_seconds() if (t and prev_t) else 0.0)
        cls = frame_class(r)
        if prev is None or gap > SEGMENT_GAP_S:
            segments.append({"id": f"seg{len(segments) + 1:02d}", "start": r.get("time"), "end": r.get("time"), "frames": []})
        segments[-1]["frames"].append(r["id"])
        segments[-1]["end"] = r.get("time")
        if prev is not None and gap <= BURST_GAP_S and _hash_distance(prev.get("phash"), r.get("phash")) <= BURST_HASH:
            bursts[-1].append(r["id"])
        else:
            bursts.append([r["id"]])
        person_change = False
        if prev is not None and cls == "solo" and runs and runs[-1]["class"] == "solo":
            fd = _hash_distance(prev.get("face_hash"), r.get("face_hash")) if (prev.get("face_hash") and r.get("face_hash")) else 0
            person_change = (gap >= PERSON_GAP_S and fd >= PERSON_HASH) or gap >= HARD_GAP_S or fd >= HARD_HASH
        if (prev is None or gap > RUN_GAP_S or person_change or runs[-1]["class"] != cls
                or runs[-1]["segment"] != segments[-1]["id"]):
            runs.append({"id": f"run{len(runs) + 1:03d}", "segment": segments[-1]["id"], "class": cls,
                         "frames": [], "start": r.get("time"), "end": r.get("time")})
        runs[-1]["frames"].append(r["id"])
        runs[-1]["end"] = r.get("time")
        prev, prev_t = r, t or prev_t
    return {"segments": segments, "bursts": bursts, "runs": runs,
            "params": {"segment_gap_s": SEGMENT_GAP_S, "burst_gap_s": BURST_GAP_S,
                       "burst_hash": BURST_HASH, "run_gap_s": RUN_GAP_S, "person_gap_s": PERSON_GAP_S,
                       "person_hash": PERSON_HASH, "hard_gap_s": HARD_GAP_S, "hard_hash": HARD_HASH}}


def write(out_dir, clusters):
    path = os.path.join(out_dir, "clusters.json")
    with open(path, "w") as f:
        json.dump(clusters, f, indent=1, sort_keys=True)
    return path


def summary(clusters):
    runs = clusters["runs"]
    by = {}
    for r in runs:
        by.setdefault(r["class"], []).append(len(r["frames"]))
    lines = [f"segments {len(clusters['segments'])}: " + ", ".join(
        f"{s['id']} {s['start']} to {s['end']} ({len(s['frames'])} frames)" for s in clusters["segments"])]
    lines.append(f"bursts {len(clusters['bursts'])}, longest {max(len(b) for b in clusters['bursts'])}")
    for cls, sizes in sorted(by.items()):
        lines.append(f"runs {cls}: {len(sizes)} (frames {sum(sizes)}, largest {max(sizes)})")
    return "\n".join(lines)
