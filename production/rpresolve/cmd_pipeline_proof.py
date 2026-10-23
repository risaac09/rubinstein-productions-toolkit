"""
pipeline-proof: compare a test render with a reference render, for the G1
proof: per-frame luma and chroma shift, legal or full range, PSNR, SSIM, VMAF
where ffmpeg has libvmaf; PASS or FAIL per row.
"""

import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "pipeline-proof"
HELP = "Offline: compare a test render with a reference; PASS or FAIL per row"
DESCRIPTION = ("Compare a test render with a reference (another render, or Resolve's own decode "
               "exported as a file). Rows: frames, range, gamma or level shift, luma and chroma "
               "shift, PSNR, SSIM, VMAF (\"not available\" when ffmpeg has no libvmaf). Exit 0 on "
               "PASS, 1 on FAIL.")


_PROOF_TRAP = ("Resolve decodes log H.264 about 25 code values darker than ffmpeg. Never compare a "
               "Resolve render against an ffmpeg decode of a log source; the level-shift row would "
               "blame the pipeline for the decode. Compare render against render, or against "
               "Resolve's own decode exported as a file.")


EPILOG = "KNOWN TRAP: " + _PROOF_TRAP


def add_arguments(p):
    p.add_argument("test", help="The render under test")
    p.add_argument("reference", help="The reference render, or Resolve's decode exported as a file")
    p.add_argument("--frames", type=int, default=300, help="Frames to compare from the start (default 300)")
    p.add_argument("--psnr", type=float, help="Minimum PSNR in dB (default 45, for a flat clip)")
    p.add_argument("--ssim", type=float, help="Minimum SSIM (default 0.99)")
    p.add_argument("--vmaf", type=float, help="Minimum VMAF (default 95)")
    p.add_argument("--luma-shift", type=float, help="Largest mean luma shift in 8-bit codes (default 1.5)")
    p.add_argument("--chroma-shift", type=float, help="Largest mean chroma shift in 8-bit codes (default 1.5)")
    p.add_argument("--json", help="Also write the full report, per-frame shifts included, here")


def run(args):
    """Compare a test render with a reference (see rpresolve/proof.py). Exit
    0 on PASS, 1 on FAIL or an unreadable file."""
    try:
        from rpresolve import proof as rpproof
    except ImportError as e:
        print(f"ERROR: pipeline-proof needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    problem = rppaths.out_problem(args.json) if args.json else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        report = rpproof.compare(args.test, args.reference, frames=args.frames, thresholds={
            "psnr": args.psnr, "ssim": args.ssim, "vmaf": args.vmaf,
            "luma_shift": args.luma_shift, "chroma_shift": args.chroma_shift})
    except rpproof.MeasureError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(rpproof.format_report(report))
    if args.json:
        rpproof.write_json(report, args.json)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    cli.script(__name__)
