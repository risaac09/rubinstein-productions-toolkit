"""
rpresolve.trimapply: the rows of a trim-review TSV that an editor accepted,
applied to a copy of the cut manifest. The editor gate: nothing here picks a
row. It refuses without an explicit list.

    python3 -m rpresolve.trimapply MANIFEST REVIEW.tsv --out NEW.json
        (--accept 3,7,12-15 | --accepted-file ROWS.txt)

Rows are numbered from 1 down the TSV (the line under the header is row 1).
--accepted-file holds one number or range per line; `#` starts a comment.
Only a manifest review is read (the TSV has clip and span columns), and only
rows of kind silence, filler or repeat whose suggestion is tighten or
cut-candidate and whose confidence is not low. Accepting anything else is
refused, not skipped.

An accepted row cuts [source_start, source_end) out of its span, leaving up
to two spans; a repeat row (both copies) cuts its first half only. A silence keeps PAD seconds of its pause each side, so the join
keeps breath. The new edges are not checked: end_words and source_text are
cleared where a cut changed them (not guessed), and audio_lead and audio_tail
stay only on the first and last piece. Run endcheck on the copy.

Writes NEW.json and NEW.json.applied.json (what was applied, from which
files, what was cleared). Never in place: --out is refused when it exists,
is the manifest or the TSV, or lies inside a git working tree. Exit 0 done,
2 refused. A version 1 manifest comes back version 1.
"""

import argparse
import copy
import csv
import json
import os
import re
import sys

from . import cutlist, paths, trimreview as tr

PAD = 0.12  # seconds of a cut silence left each side
APPLIED = ".applied.json"
CUTTABLE = (tr.SILENCE, tr.FILLER, tr.REPEAT)
SUGGESTIONS = (tr.TIGHTEN, tr.CUT)
EDGE_S = 0.04  # a row may lie this far outside its span (frame snapping)


class TrimApplyError(ValueError):
    """The rows cannot be applied as asked; nothing is written."""


def parse_accept(text):
    """[row numbers] from '3,7,12-15' or from a file's lines. Raises on a
    duplicate, a bad token or an empty list."""
    nums = []
    for tok in re.split(r"[,\s]+", text.strip()):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
        if not m:
            raise TrimApplyError(f"{tok!r} is not a row number or a range such as 12-15")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a:
            raise TrimApplyError(f"{tok!r} is not a range of rows counted from 1")
        nums += range(a, b + 1)
    if not nums:
        raise TrimApplyError("the accepted list is empty; name the rows to apply")
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        raise TrimApplyError("row(s) accepted twice: " + ", ".join(map(str, dup)))
    return sorted(nums)


def read_accepted_file(path):
    with open(path, encoding="utf-8") as f:
        return parse_accept(" ".join(line.split("#", 1)[0] for line in f))


def read_review(path):
    """[row dict] from a trim-review TSV of a manifest."""
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        if tuple(header or ()) not in (tr.COLUMNS, tr.V2_COLUMNS):
            raise TrimApplyError(f"{path} does not start with the trim-review header "
                                 f"({', '.join(tr.COLUMNS)})")
        rows = [dict(zip(header, r)) for r in reader if r]
    if rows and not (rows[0]["clip"] and rows[0]["span"]):
        raise TrimApplyError(f"{path} is a review of a whole source (no clip or span); "
                             "trim-apply reads a review of a cut manifest")
    return rows


def _pieces(span, cuts):
    """[(in, out)] of span left after cutting [(a, b)] (sorted, disjoint)."""
    out, at = [], span["in"]
    for a, b in cuts:
        if a - at > 1e-6:
            out.append((at, a))
        at = max(at, b)
    if span["out"] - at > 1e-6:
        out.append((at, span["out"]))
    return out


def apply(manifest, rows, accepted):
    """(new manifest, sidecar detail) from a validated manifest, review
    rows and accepted row numbers. Raises TrimApplyError before changing
    anything."""
    fps = manifest["fps"]
    byspan, picked = {}, []
    for n in accepted:
        if n > len(rows):
            raise TrimApplyError(f"row {n} does not exist; the review has {len(rows)} row(s)")
        r = rows[n - 1]
        why = (f"is kind {r['kind']}" if r["kind"] not in CUTTABLE else
               f"is suggested {r['suggestion']}" if r["suggestion"] not in SUGGESTIONS else
               "has low confidence" if r["confidence"] == "low" else None)
        if why:
            raise TrimApplyError(f"row {n} {why}; only silence, filler and repeat rows suggested "
                                 "tighten or cut-candidate, above low confidence, are applied")
        clip = next((c for c in manifest["clips"] if c["name"] == r["clip"]), None)
        if clip is None or not r["span"].isdigit() or not 1 <= int(r["span"]) <= len(clip["spans"]):
            raise TrimApplyError(f"row {n}: clip {r['clip']!r} span {r['span']!r} is not in the "
                                 "manifest; was this review made from another manifest?")
        span = clip["spans"][int(r["span"]) - 1]
        if r.get("source") and r["source"] != cutlist.source_for(manifest, span)["id"]:
            raise TrimApplyError(f"row {n}: names source {r['source']!r}, not the span's")
        a, b = float(r["source_start"]), float(r["source_end"])
        if a < span["in"] - EDGE_S or b > span["out"] + EDGE_S or b <= a:
            raise TrimApplyError(f"row {n}: {a}-{b} s is outside {r['clip']} span {r['span']} "
                                 f"({span['in']}-{span['out']}); was this review made from "
                                 "another manifest?")
        if r["kind"] == tr.SILENCE:
            a, b = a + PAD, b - PAD
            if b - a <= 1e-6:
                raise TrimApplyError(f"row {n}: a silence of {b - a + 2 * PAD:.2f} s is too short "
                                     f"to cut with {PAD} s left each side")
        if r["kind"] == tr.REPEAT:  # the row spans both copies; drop the first, keep the second
            b = a + (b - a) / 2
        a, b = max(a, span["in"]), min(b, span["out"])
        byspan.setdefault((r["clip"], int(r["span"])), []).append((a, b, n, r))
        picked.append(n)
    new, detail, cleared = copy.deepcopy(manifest), [], []
    for clip in new["clips"]:
        spans, before = [], cutlist.clip_frames(clip, fps)
        for i, span in enumerate(clip["spans"], 1):
            cuts = sorted(byspan.pop((clip["name"], i), []))
            if not cuts:
                spans.append(span)
                continue
            for (a1, b1, n1, _), (a2, b2, n2, _) in zip(cuts, cuts[1:]):
                if a2 < b1:
                    raise TrimApplyError(f"rows {n1} and {n2} overlap in {clip['name']} span {i}; "
                                         "accept one of them")
            pieces = _pieces(span, [(a, b) for a, b, _, _ in cuts])
            made = []
            for k, (pa, pb) in enumerate(pieces):
                pa, pb = round(pa, 3), round(pb, 3)
                if cutlist.frame(pb, fps) - cutlist.frame(pa, fps) < 1:
                    raise TrimApplyError(f"{clip['name']} span {i}: the cut leaves {pa}-{pb} s, "
                                         "under one frame; accept a smaller cut")
                p = copy.deepcopy(span)
                p["in"], p["out"] = pa, pb
                gone = [key for key in ("end_words", "source_text") if key in p
                        and (key == "source_text" or pb != span["out"])]
                gone += [key for key, keep in (("audio_lead", k == 0 and pa == span["in"]),
                                               ("audio_tail", k == len(pieces) - 1
                                                and pb == span["out"]))
                         if key in p and not keep]
                for key in gone:
                    del p[key]
                if gone:
                    cleared.append({"clip": clip["name"], "span": i, "piece": k + 1,
                                    "fields": gone})
                made.append(p)
            spans += made
            detail += [{"row": n, "kind": r["kind"], "clip": clip["name"], "span": i,
                        "removed": [round(a, 3), round(b, 3)],
                        "span_result": [[p["in"], p["out"]] for p in made]}
                       for a, b, n, r in cuts]
        if not spans:
            raise TrimApplyError(f"{clip['name']}: nothing would be left")
        clip["spans"] = spans
        clip["_removed"] = before - cutlist.clip_frames(clip, fps)
    removed = {c["name"]: c.pop("_removed") for c in new["clips"]}
    cutlist.validate_manifest(new)
    return new, {"rows": detail, "cleared_fields": cleared, "frames_removed_per_clip": removed}


def add_arguments(p):
    p.add_argument("manifest", help="the cut manifest the review was made from")
    p.add_argument("review", help="the trim-review TSV")
    p.add_argument("--out", required=True, help="the new manifest, outside any git working tree")
    p.add_argument("--accept", help="row numbers to apply: 3,7,12-15")
    p.add_argument("--accepted-file", help="a file of row numbers, one or more per line")


def run(args):
    try:
        if bool(args.accept) == bool(args.accepted_file):
            raise TrimApplyError("give exactly one of --accept or --accepted-file: the editor "
                                 "names the rows; nothing is applied by default")
        side = args.out + APPLIED
        for target in (args.out, side):
            problem = paths.out_problem(target, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
            for label, p in (("manifest", args.manifest), ("review", args.review)):
                if os.path.realpath(target) == os.path.realpath(p):
                    raise paths.OutputRefused(f"{target} is the {label}; trim-apply writes a copy")
            if os.path.exists(target):
                raise paths.OutputRefused(f"{target} exists; a manifest copy is never written "
                                          "over another file")
        accepted = (parse_accept(args.accept) if args.accept
                    else read_accepted_file(args.accepted_file))
        m = cutlist.load_manifest(args.manifest)
        new, detail = apply(m, read_review(args.review), accepted)
        record = {"manifest": os.path.abspath(args.manifest),
                  "manifest_sha256": cutlist.sha256_file(args.manifest),
                  "review": os.path.abspath(args.review),
                  "review_sha256": cutlist.sha256_file(args.review), "accepted": accepted,
                  "pad": PAD, "applied_by": "trimapply", **detail,
                  "note": "the new edges are unchecked: run endcheck on the copy"}
        paths.write_private(side, json.dumps(record, indent=2) + "\n", any_git_tree=True)
        try:
            paths.write_private(args.out, json.dumps(new, indent=2) + "\n", any_git_tree=True)
        except BaseException:
            os.remove(side)
            raise
    except (OSError, ValueError) as e:  # TrimApplyError, CutlistError, OutputRefused, bad JSON
        print(f"trim-apply: {e}", file=sys.stderr)
        return 2
    frames = sum(detail["frames_removed_per_clip"].values())
    print(f"trim-apply: {len(accepted)} row(s) applied, {frames} frame(s) removed -> {args.out}\n"
          "The new edges are not checked: run endcheck on the copy.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.trimapply",
                                description=__doc__.split("\n\n")[0])
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
