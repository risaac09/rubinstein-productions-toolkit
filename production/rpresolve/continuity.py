"""
rpresolve.continuity: the G8 continuity report and Shot Match prep. Offline:
it reads `measure` JSON files and never touches Resolve or the footage.

Input: measure reports grouped by scene. Per scene it names

    - one reference clip, the one to run Shot Match against, by exposure
      (luma p50 near mid grey), skin (a read, close to the skin line) and
      clipping (little at either end), with the reasons in numbers
    - flagged pairs: two clips whose white point differs (the near-neutral
      cast when a report carries `neutral_cast`, else skin Lab, and the
      basis is named; measure does not write a cast yet, so skin is the usual
  basis and also moves with the person) or whose key light comes from opposite sides. Flags
      are hints for the paper edit and the grade; nothing here blocks.

Second run (`--before` the first report, new measure files of the same
clips after a person ran Shot Match by hand in Resolve): per clip, skin
CIEDE2000 to the reference before and after, matched by file path, then by
file stem.

Run: python3 -m rpresolve.continuity --scene S01 a.json b.json [--out report.json]
"""

import argparse
import itertools
import json
import os
import sys

import numpy as np

from . import color
from .paths import OutputRefused, write_private

MID_IRE = 45.0          # skin-friendly mid grey target for luma p50
CLIP_FULL_PCT = 5.0     # clipping share (high + low) that scores zero
SKIN_LINE_SPAN_DEG = 30.0
WEIGHTS = {"exposure": 0.35, "skin": 0.40, "clipping": 0.25}
CAST_FLAG = 2.0         # Lab a*, b* distance between near-neutral casts
SKIN_FLAG = 5.0         # skin CIEDE2000 between two clips of a scene


def _skin(rep):
    s = rep.get("skin")
    return s if isinstance(s, dict) and s.get("lab") else None


def score_clip(rep):
    """(total, parts, reasons) for one measure report."""
    p50 = rep["luma"]["p50"]
    exp = max(0.0, 1 - abs(p50 - MID_IRE) / MID_IRE)
    clip = rep["clipping"]["high_pct"] + rep["clipping"]["low_pct"]
    clp = max(0.0, 1 - clip / CLIP_FULL_PCT)
    sk = _skin(rep)
    skn = max(0.0, 1 - abs(sk["hue_off_skin_line_deg"]) / SKIN_LINE_SPAN_DEG) if sk else 0.0
    parts = {"exposure": exp, "skin": skn, "clipping": clp}
    reasons = [f"exposure: luma p50 {p50} IRE ({abs(p50 - MID_IRE):.1f} from {MID_IRE:g})",
               (f"skin: read in {sk['frames_with_skin']} frame(s), {sk['hue_off_skin_line_deg']:+} deg off the skin line"
                if sk else "skin: no read"),
               f"clipping: {rep['clipping']['high_pct']}% high, {rep['clipping']['low_pct']}% low"]
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    return round(total, 4), {k: round(v, 3) for k, v in parts.items()}, reasons


def _lit_side(rep):
    li = rep.get("lighting")
    return li["lit_side"] if isinstance(li, dict) else None


def _white_point(rep):
    """((a, b) or Lab triple, basis) for a report, or (None, None)."""
    nc = (rep.get("neutral_cast") or None)
    if isinstance(nc, dict):
        return (nc["a"], nc["b"]), "neutral cast"
    sk = _skin(rep)
    return (sk["lab"], "skin") if sk else (None, None)


def flag_pairs(reps):
    """Flagged pairs among a scene's reports (name -> report)."""
    flags = []
    for a, b in itertools.combinations(sorted(reps), 2):
        reasons = []
        wa, basis_a = _white_point(reps[a])
        wb, basis_b = _white_point(reps[b])
        if wa is not None and wb is not None and basis_a == basis_b:
            if basis_a == "skin":
                d = float(color.delta_e_2000(np.array(wa), np.array(wb)))
                if d > SKIN_FLAG:
                    reasons.append(f"white point: skin dE2000 {d:.2f} (over {SKIN_FLAG:g}, basis skin)")
            else:
                d = float(np.hypot(wa[0] - wb[0], wa[1] - wb[1]))
                if d > CAST_FLAG:
                    reasons.append(f"white point: neutral cast apart by {d:.2f} Lab (over {CAST_FLAG:g})")
        la, lb = _lit_side(reps[a]), _lit_side(reps[b])
        if la and lb and la != lb:
            reasons.append(f"light side: {a} lit {la}, {b} lit {lb}")
        if reasons:
            flags.append({"a": a, "b": b, "reasons": reasons})
    return flags


def scene_report(reps):
    """One scene: reps maps a clip name to its measure report."""
    scored = {n: score_clip(r) for n, r in reps.items()}
    ranked = sorted(scored, key=lambda n: (-scored[n][0], n))
    ref = ranked[0]
    out = {"reference": ref, "score": scored[ref][0], "reasons": scored[ref][2],
           "scores": {n: {"total": scored[n][0], **scored[n][1]} for n in ranked}}
    if len(ranked) > 1:
        out["runner_up"] = ranked[1]
        out["margin"] = round(scored[ref][0] - scored[ranked[1]][0], 4)
    else:
        out["reasons"].append("only clip in the scene")
    out["flagged_pairs"] = flag_pairs(reps)
    out["clips"] = {n: {"file": r["file"],
                        "skin_lab": (_skin(r) or {}).get("lab"),
                        "luma_p50": r["luma"]["p50"]} for n, r in reps.items()}
    return out


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def build(scenes):
    """scenes: {scene: [measure report, ...]} -> the report dict. Clips are
    named by file stem; a stem twice in one scene is refused."""
    out = {"scenes": {}}
    for scene, reports in scenes.items():
        reps = {}
        for r in reports:
            name = _stem(r["file"])
            if name in reps:
                raise ValueError(f"scene {scene}: two reports share the clip name {name}")
            reps[name] = r
        out["scenes"][scene] = scene_report(reps)
    return out


def _find(clips, file):
    for n, c in clips.items():
        if c["file"] == file:
            return n
    stems = [n for n, c in clips.items() if _stem(c["file"]) == _stem(file)]
    return stems[0] if len(stems) == 1 else None


def compare(before, after):
    """Skin dE2000 to the reference clip, before (a first report) and after
    (a report built from the re-measured files)."""
    out = {}
    for scene, b in before["scenes"].items():
        a = (after["scenes"] or {}).get(scene)
        if a is None:
            out[scene] = {"note": "scene missing from the new files"}
            continue
        ref_b = b["clips"][b["reference"]]
        ref_n = _find(a["clips"], ref_b["file"])
        rows = {}
        for name, c in b["clips"].items():
            if name == b["reference"]:
                continue
            n = _find(a["clips"], c["file"])
            row = {"before_de2000": None, "after_de2000": None, "change": None}
            if c["skin_lab"] and ref_b["skin_lab"]:
                row["before_de2000"] = round(float(color.delta_e_2000(
                    np.array(c["skin_lab"]), np.array(ref_b["skin_lab"]))), 3)
            if n is None or ref_n is None:
                row["note"] = "clip or reference not found in the new files"
            elif a["clips"][n]["skin_lab"] and a["clips"][ref_n]["skin_lab"]:
                row["after_de2000"] = round(float(color.delta_e_2000(
                    np.array(a["clips"][n]["skin_lab"]), np.array(a["clips"][ref_n]["skin_lab"]))), 3)
            if row["before_de2000"] is not None and row["after_de2000"] is not None:
                row["change"] = round(row["after_de2000"] - row["before_de2000"], 3)
            rows[name] = row
        out[scene] = {"reference": b["reference"], "clips": rows}
    return out


def format_summary(report):
    lines = []
    for scene, s in report["scenes"].items():
        lines.append(f"scene {scene}: reference {s['reference']} (score {s['score']})"
                     + (" [this run's own pick; Shot Match below compares against the first run's]"
                        if report.get("shot_match") else ""))
        lines += [f"  {r}" for r in s["reasons"]]
        for f in s["flagged_pairs"]:
            lines.append(f"  flag {f['a']} / {f['b']}: " + "; ".join(f["reasons"]))
        if not s["flagged_pairs"]:
            lines.append("  no flagged pairs")
    for scene, c in (report.get("shot_match") or {}).items():
        lines.append(f"shot match, scene {scene}:")
        for name, r in c.get("clips", {}).items():
            lines.append(f"  {name}: skin dE2000 to {c['reference']} {r['before_de2000']} -> {r['after_de2000']}"
                         + (f" ({r['change']:+})" if r["change"] is not None else "")
                         + (f" [{r['note']}]" if r.get("note") else ""))
        if "note" in c:
            lines.append(f"  {c['note']}")
    return "\n".join(lines) + "\n"


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    p = argparse.ArgumentParser(prog="rpresolve.continuity", description=__doc__.split("\n\n")[0])
    p.add_argument("--scene", nargs="+", action="append", metavar=("NAME", "FILE"), required=True,
                   help="a scene name then its measure JSON files; repeat for more scenes")
    p.add_argument("--before", help="a first continuity report (JSON); the scene files are then "
                                    "the re-measure after Shot Match")
    p.add_argument("--out", help="write the report JSON here (outside any git tree)")
    args = p.parse_args(argv)
    try:
        scenes = {}
        for name, *files in args.scene:
            if not files:
                p.error(f"scene {name} has no files")
            if name in scenes:
                p.error(f"scene {name} given twice; list its files under one --scene")
            scenes[name] = [_load(f) for f in files]
        report = build(scenes)
        if args.before:
            report["shot_match"] = compare(_load(args.before), report)
        if args.out:
            write_private(args.out, json.dumps(report, indent=2) + "\n", any_git_tree=True)
    except KeyError as e:
        print(f"continuity: input is not a measure report (missing {e})", file=sys.stderr)
        return 2
    except (OSError, ValueError, OutputRefused) as e:
        print(f"continuity: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(format_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
