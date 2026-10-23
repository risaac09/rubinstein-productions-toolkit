"""
tilt-fixture: a short clip with markers, and the Pan, Tilt and Zoom to type
into Resolve, for the render that settles Tilt's sign. A person renders the
still, so no MCP tool is planned.
"""

import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "tilt-fixture"
HELP = ("Offline: write the clip and numbers for the render that settles Tilt's sign (needs "
        "ffmpeg); prints the hand steps")


TILT_STEPS = """\
Hand steps (only a person presses Render). In a scratch Resolve project:
  1. Make a timeline of {size} with input scaling set to {scaling_ui}.
  2. Import {video}; put it on the timeline.
  3. In the Inspector, set Zoom X and Zoom Y to {zoom}, Pan to {pan} and Tilt to {tilt}.
  4. Deliver ONE still of any frame (PNG or JPEG, {size} or any size of that shape).
  5. Run: python3 resolve_workflow.py tilt-check <the still> --fixture {folder}
White marker: the crop centre, so it must land at the middle of the frame. Red marker: the middle of
the source. Yellow marker: above the white one, so you can see which way is up.
Pan and Tilt were confirmed on 2026-10-15; a run now re-checks a new Resolve version."""


TILT_SCALING_UI = {"scaleToFit": "Scale entire image to fit (the default)",
                   "scaleToCrop": "Scale full frame with crop"}


def add_arguments(p):
    p.add_argument("--out", required=True,
                   help="A new folder outside any repository for tilt-fixture.mp4 and tilt-expect.json")
    p.add_argument("--variant", choices=("9x16", "master", "1x1", "9x16-6k", "master-fit",
                                         "16x9-crop"), default="9x16",
                   help="9x16: UHD into 1080x1920 at scale-to-fit (default). master: 6048x4032 "
                        "into 5952x2646 at scale-full-frame-with-crop, a festival project's setup. The "
                        "rest vary the shapes and scaling (reframe.TILT_VARIANTS)")


def run(args):
    """Write the Tilt fixture clip and tilt-expect.json into --out (outside
    any repository) and print the hand steps. Needs ffmpeg."""
    from rpresolve import reframe
    folder = os.path.abspath(args.out)
    for name in (reframe.TILT_VIDEO, reframe.TILT_EXPECT_FILE):
        problem = rppaths.out_problem(os.path.join(folder, name))
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
    try:
        expect = reframe.tilt_fixture(folder, variant=getattr(args, "variant", "9x16"))
    except (reframe.ReframeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    p = expect["props"]
    print(TILT_STEPS.format(video=os.path.join(folder, reframe.TILT_VIDEO), zoom=p["ZoomX"],
                            pan=p["Pan"], tilt=p["Tilt"], folder=folder,
                            size="x".join(str(v) for v in expect["out"]),
                            scaling_ui=TILT_SCALING_UI[expect["input_scaling"]]))
    return 0


if __name__ == "__main__":
    cli.script(__name__)
