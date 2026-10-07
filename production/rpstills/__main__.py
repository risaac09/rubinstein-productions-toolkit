"""python3 -m rpstills <command> ...

  index   <shoot folder> <out dir>           read originals, write proxies and index.jsonl
  cluster <out dir>                          segments, bursts, runs -> clusters.json
  cull    <out dir>                          scores and proposal -> cull.json
  review  <out dir> --session NAME [--title] -> review.html
  crops   <out dir> [--config PATH]          crop plan per profile -> crops.json, crops-preview.jpg
  run     <shoot folder> <out dir> --session NAME   all four in order
"""

import argparse
import os
import sys

from . import cluster, crops, cull, index, review


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rpstills")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index"); p.add_argument("shoot"); p.add_argument("out"); p.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("cluster"); p.add_argument("out")
    p = sub.add_parser("cull"); p.add_argument("out")
    p = sub.add_parser("review"); p.add_argument("out"); p.add_argument("--session", required=True); p.add_argument("--title")
    p = sub.add_parser("crops"); p.add_argument("out"); p.add_argument("--config")
    p = sub.add_parser("run"); p.add_argument("shoot"); p.add_argument("out"); p.add_argument("--session", required=True)
    p.add_argument("--title"); p.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    if a.cmd in ("index", "run"):
        rows = index.build(os.path.abspath(a.shoot), out, workers=a.workers)
    else:
        rows = index.load(out)
    if a.cmd == "index":
        return 0
    rows = index.augment_face_hash(out, rows)
    cl = cluster.build(rows)
    if a.cmd in ("cluster", "run"):
        print(cluster.write(out, cl)); print(cluster.summary(cl))
        if a.cmd == "cluster":
            return 0
    cu = cull.build(rows, cl)
    if a.cmd in ("cull", "run"):
        print(cull.write(out, cu)); print(cull.summary(cu))
        if a.cmd == "cull":
            return 0
    if a.cmd == "crops":
        cfg = crops.load_config(a.config or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stills-config.json"))
        ids, src = crops.selection(out, cu)
        plan = crops.build(rows, cl, ids, cfg)
        print(crops.write(out, plan, src, cfg)); print("selection:", src); print(crops.summary(plan))
        print(crops.preview(out, rows, plan, cfg))
        return 0
    source = rows[0]["jpeg"].rsplit("/", 1)[0] if rows else a.shoot if hasattr(a, "shoot") else out
    print(review.build(out, a.session, source, rows, cl, cu, title=a.title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
