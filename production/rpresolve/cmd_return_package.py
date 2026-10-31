"""
return-package: assemble the return package of production/return-package.md
from card folders, the finals, a .drp and the session files (copied, never
moved), write 01_originals/SHA256SUMS and manifest.md, and refuse unless
every final has a deliver_check PASS; --verify re-checks a built package.
"""

import sys

from rpresolve import cli

NAME = "return-package"
HELP = "Offline: assemble the return package behind a deliver_check PASS gate, or --verify"


def add_arguments(p):
    p.add_argument("--config", dest="deliver_config",
                   help="A config overlay kept outside this repo (may also go before the command)")
    p.add_argument("--verify", metavar="PACKAGE",
                   help="Re-check a built package's checksums; writes nothing")
    p.add_argument("--out", help="Folder to build the package in (outside any git tree)")
    p.add_argument("--engagement", help="Engagement id, never a client name")
    p.add_argument("--month", help="Month of return, YYYY-MM (default: this month)")
    p.add_argument("--approval", help="Approval reference id")
    p.add_argument("--prepared-by", help="Name and role")
    p.add_argument("--originals", nargs="+", metavar="CARD", help="Card folders, copied under their names")
    p.add_argument("--finals", help="Delivery root: <folder>/<destination key>/<files>")
    p.add_argument("--project-file", help="The Resolve project as a .drp (media not consolidated)")
    p.add_argument("--session", help="Folder of session recordings and transcripts")
    p.add_argument("--licence", action="append", default=[], metavar="ASSET|LICENCE|ATTRIBUTION")


def run(args):
    """Exit 0 built or all OK, 1 blocked, refused or a mismatch, 2 ffprobe or ffmpeg missing."""
    import time
    from rpresolve import deliver, delivercheck as dc, returnpackage as rp
    try:
        if args.verify:
            results = rp.verify(args.verify)
            for status, rel in results:
                print(f"{rel}: {status}")
            return 0 if all(s == "OK" for s, _ in results) else 1
        missing = [f for f in ("out", "engagement", "approval", "prepared_by", "originals", "finals")
                   if not getattr(args, f)]
        if missing:
            raise rp.PackageError("required to build: --" + ", --".join(m.replace("_", "-") for m in missing))
        dc.check_tools()
        path = rp.build(args.out, args.engagement, args.month or time.strftime("%Y-%m"), args.approval,
                        args.prepared_by, args.originals, args.finals, cli.deliver_config(args),
                        project_file=args.project_file, session=args.session, licences=args.licence)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (rp.PackageError, dc.CheckError, deliver.DeliverError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    cli.script(__name__)
