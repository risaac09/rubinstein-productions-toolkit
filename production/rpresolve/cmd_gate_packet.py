"""
gate-packet: the packet for the 30-minute review gate. For each clip, a full-frame
contact sheet (6 x 4 cells, 1920 px or less, under 1 MiB, cell times listed) and two
20 s 48 kHz listening excerpts (from the start, and around the sendable line or the
middle), plus gate.json with the clip count, the total runtime, each input's sha256
and the pending review-log line. Input is review copies (--clip), or a source with a
vertcut cuts TSV (--source and --cuts) so the sheets show the planned spans before
any render. The output folder must be new or empty and outside any git tree. States
total runtime only, never render time. Answer it with review-log --step gate
--choice go --key OUT. Exit 2 on a refusal. Touches neither Resolve nor the media.
The file layout and gate.json's shape are in rpresolve/gatepacket.py.
"""

import sys

from rpresolve import cli

NAME = "gate-packet"
HELP = "Contact sheet and listening excerpts per clip, and a pending gate line"


def add_arguments(p):
    p.add_argument("--out", required=True, help="new or empty folder for the packet, outside any git tree")
    p.add_argument("--project", required=True, help="project id like P-0001, never a name")
    p.add_argument("--clip", nargs="+", help="review copies, one clip each")
    p.add_argument("--source", help="the source file, with --cuts")
    p.add_argument("--cuts", help="vertcut cuts TSV (id, in, out, title), with --source")
    p.add_argument("--offset", type=float, default=0.0, help="seconds added to every --cuts row")
    p.add_argument("--sendable", action="append", default=[], metavar="NAME=SECONDS",
                   help="the sendable line's time in clip NAME, seconds from its start; repeatable")


def run(args):
    from rpresolve import gatepacket as gp
    try:
        if bool(args.clip) == bool(args.source or args.cuts) or bool(args.source) != bool(args.cuts):
            raise gp.GateError("give --clip FILE..., or --source and --cuts together")
        sendable = {}
        for item in args.sendable:
            k, _, v = item.rpartition("=")
            try:
                sendable[k] = float(v)
            except ValueError:
                raise gp.GateError(f"--sendable {item!r} must look like NAME=SECONDS")
        clips, inputs = (gp.clips_from_copies(args.clip) if args.clip
                         else gp.clips_from_cuts(args.source, args.cuts, args.offset))
        gate = gp.build(args.out, args.project, clips, inputs, sendable)
    except (gp.GateError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"gate-packet: {gate['clip_count']} clips, total runtime {gate['total_runtime_s']:.1f} s, in {args.out}")
    for c in gate["clips"]:
        print(f"  {c['name']}: " + ", ".join(f"cell {x['cell']} {x['t_s']:.1f}s" for x in c["sheet"]["cells"]))
    return 0


if __name__ == "__main__":
    cli.script(__name__)
