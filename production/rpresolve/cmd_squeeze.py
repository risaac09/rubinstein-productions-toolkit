"""
squeeze: record and read the anamorphic squeeze of a card's clips.

A camera records an anamorphic picture squeezed and usually writes nothing that says so, so a
person answers once per card and the answer is kept beside the card copy
(`<CAM>-<id6>.squeeze.json`, see rpresolve/squeeze.py). shoot-table shows it, and the draft
farm desqueezes by it and refuses a card with no answer.

    squeeze show PATH...                       one line per clip: factor, where it came from, lens
    squeeze set FOLDER --factor 1              the whole card is spherical
    squeeze set FOLDER --factor 1.5            the whole card is 1.5x anamorphic
    squeeze set FOLDER --factor 1.5 --manual-lens
                                               clips with no lens data are 1.5x, the rest spherical
    squeeze set FOLDER --factor 1.5 --clip P1040114.MOV --clip P1040115.MOV

A clip with no lens data was shot on a lens with no contacts, which is how most anamorphic
lenses mount; it is a hint, never proof, so `show` marks it and a person decides. Exit status
of show: 0 when every clip is answered, 2 when a clip of a copied card is not.
"""

import os
import sys

from rpresolve import cli
from rpresolve import detect as rpdetect
from rpresolve import shoot_table as rpshoot
from rpresolve import squeeze as rpsqueeze

NAME = "squeeze"
HELP = "Offline: record or read the anamorphic squeeze of a card's clips"


def add_arguments(p):
    p.add_argument("action", choices=("show", "set"))
    p.add_argument("paths", nargs="+", help="show: media files or folders; set: one folder (the card copy)")
    p.add_argument("--factor", type=float, help="set: the squeeze factor; 1 is spherical")
    p.add_argument("--clip", action="append", metavar="NAME",
                   help="set: only this clip, by file name (repeatable)")
    p.add_argument("--manual-lens", action="store_true",
                   help="set: the factor goes to clips with no lens data, 1 to the rest")


lens = rpshoot.lens


def rows(files, exif):
    """[{file, factor, source, lens}] for media files. source: record, unanswered (a
    card clip nobody has answered for) or none (not on a card, no record)."""
    out = []
    for f in files:
        fac = rpsqueeze.factor(f)
        source = "record" if fac is not None else ("unanswered" if rpsqueeze.unanswered(f) else "none")
        out.append({"file": f, "factor": fac, "source": source, "lens": lens(exif.get(f))})
    return out


def _exif_for(files):
    return rpdetect.run_exiftool(files) if files else {}


def show(paths):
    files, missing = rpdetect.collect_files(paths)
    for m in missing:
        print(f"  [skip] Not found: {m}", file=sys.stderr)
    if not files:
        print("ERROR: no video files found", file=sys.stderr)
        return 1
    table = rows(files, _exif_for(files))
    for r in table:
        fac = "?" if r["factor"] is None else f"{r['factor']:g}"
        print(f"{fac:>5}  {r['source']:<10}  {r['lens'] or '(no lens data)':<34}  {r['file']}")
    open_ = [r for r in table if r["source"] == "unanswered"]
    hint = sum(1 for r in open_ if not r["lens"])
    print(f"squeeze: {len(table)} clip(s), {len(open_)} unanswered"
          + (f" ({hint} with no lens data: likely a manual lens)" if hint else ""), file=sys.stderr)
    return 2 if open_ else 0


def set_(folder, factor, clips, manual_lens):
    if factor is None:
        print("ERROR: set wants --factor (1 is spherical)", file=sys.stderr)
        return 1
    factor = int(factor) if float(factor).is_integer() else factor
    files, _ = rpdetect.collect_files([folder]) if (clips or manual_lens) else ([], [])
    here = {os.path.basename(f) for f in files}
    named = {os.path.basename(c): factor for c in clips or []}
    strangers = sorted(set(named) - here)
    if strangers:  # a mistyped name would be recorded and the real clip left open
        print(f"ERROR: no such clip in {folder}: {', '.join(strangers)}", file=sys.stderr)
        return 1
    default = None if (named or manual_lens) else factor
    if manual_lens:
        exif = _exif_for(files)
        unread = sorted(os.path.basename(f) for f in files if f not in exif)
        if unread:  # an unread clip has no lens data either: do not answer for it
            print(f"  [skip] metadata not read, left open: {', '.join(unread)}", file=sys.stderr)
        named.update({os.path.basename(f): factor for f in files if f in exif and not lens(exif[f])})
        if not named:
            print("ERROR: every clip read in the folder names its lens; nothing to mark", file=sys.stderr)
            return 1
        own = os.path.abspath(folder).rstrip(os.sep) + rpsqueeze.SUFFIX
        if not (os.path.isfile(own) and rpsqueeze.load(own)["default"] is not None):
            default = 1  # the rest name their lens: spherical, unless this card's record says otherwise
    record = rpsqueeze.write(folder, default=default, clips=named)
    doc = rpsqueeze.load(record)
    print(f"Wrote {record}: default {doc['default']}, {len(doc['clips'])} clip(s) named")
    return 0


def run(args):
    """Never connects to Resolve. Exit status: 0 done, 2 when show finds an unanswered
    card clip, 1 on a usage error or a missing tool."""
    try:
        if args.action == "show":
            return show(args.paths)
        if len(args.paths) != 1:
            print("ERROR: set takes one folder", file=sys.stderr)
            return 1
        return set_(args.paths[0], args.factor, args.clip, args.manual_lens)
    except (rpsqueeze.SqueezeError, rpdetect.ToolMissing) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    cli.script(__name__)
