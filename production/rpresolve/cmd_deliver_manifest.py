"""
deliver-manifest: write manifest.md and checksums.txt for a delivery folder
from ffprobe and the files' bytes, after the last check; --verify re-checks
checksums.txt; it refuses to overwrite without --overwrite.
"""

import sys

from rpresolve import cli

NAME = "deliver-manifest"
HELP = "Offline: write manifest.md and checksums.txt for a delivery folder, or --verify"


def add_arguments(p):
    p.add_argument("folder", help="Delivery root: <folder>/<destination key>/<files>")
    p.add_argument("--config", dest="deliver_config",
                   help="A config overlay kept outside this repo (may also go before "
                        "the command)")
    p.add_argument("--verify", action="store_true",
                   help="Re-check checksums.txt against the files; writes nothing")
    p.add_argument("--overwrite", action="store_true",
                   help="Replace an existing manifest.md and checksums.txt")
    p.add_argument("--project", help="Project slug, as used in the file names")
    p.add_argument("--approval", help="Approval reference id")
    p.add_argument("--prepared-by", help="Name and role")
    p.add_argument("--review-copy", help="File name of the approved review copy")
    p.add_argument("--review-sha256", help="SHA-256 of the review copy, lowercase hex")
    p.add_argument("--delivered", help="Delivery date (default: today)")
    p.add_argument("--machine-steps", default="none stated")
    p.add_argument("--credits", default="none requested")
    p.add_argument("--platform-notes", default="none looked up")
    p.add_argument("--licence", action="append", default=[], metavar="ASSET|LICENCE|ATTRIBUTION",
                   help="A licence row; repeat for more (default: none)")
    p.add_argument("--no-loudness", action="store_true",
                   help="Record loudness as n/a (skips reading each file's audio)")


def run(args):
    """Write manifest.md and checksums.txt at a delivery root, or with
    --verify re-check checksums.txt. Exit 0 written or all OK, 1 refused or
    a mismatch, 2 ffprobe or ffmpeg missing."""
    import time
    from rpresolve import deliver as rpdeliver, delivercheck as dc, manifest as mf
    try:
        if args.verify:
            results = mf.verify(args.folder)
            for status, rel in results:
                print(f"{rel}: {status}")
            return 0 if all(s == "OK" for s, _ in results) else 1
        missing = [f for f in ("project", "approval", "prepared_by", "review_copy",
                               "review_sha256") if not getattr(args, f)]
        if missing:
            raise mf.ManifestError("required to write a manifest: --" +
                                   ", --".join(m.replace("_", "-") for m in missing))
        dc.check_tools(ffmpeg=not args.no_loudness)
        text, sums = mf.build(
            args.folder, cli.deliver_config(args), args.project, args.approval, args.prepared_by,
            args.review_copy, args.review_sha256, args.delivered or time.strftime("%Y-%m-%d"),
            machine_steps=args.machine_steps, credits=args.credits,
            platform_notes=args.platform_notes, licences=args.licence,
            loudness=not args.no_loudness)
        paths = mf.write(args.folder, text, sums, overwrite=args.overwrite)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (mf.ManifestError, dc.CheckError, rpdeliver.DeliverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("Wrote " + ", ".join(paths))
    return 0


if __name__ == "__main__":
    cli.script(__name__)
