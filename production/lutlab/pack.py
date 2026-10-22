"""lutlab.pack: one zip of LUTs to carry to Resolve on an iPad (or any Resolve). numpy only unless looks are baked.

  ~/.venvs/rp-tools/bin/python -m lutlab pack --camera s5ii,gh7,rcm [--base PARAMS.json [--looks DIR|FILE ...]]
        [--size 33|65] [--date YYYYMMDD] [--out DIR] [--overwrite]
        -> DIR/rp-luts-<YYYYMMDD>.zip

Inside, under one folder rp-luts-<YYYYMMDD>/:
  cst/      per camera log: <log>-to-di-N.cube and <log>-to-r709-N.cube (S5II and GH7 share the V-Log pair);
            for rcm: di-to-r709-N.cube
  looks/    "<look> [<Camera>].cube" for every delta on --base for every camera (the `looks` command; it needs
            scipy). Without --base the folder is left out and README.txt says so. --looks defaults to the
            shipped deltas in production/lutlab-looks.
  check/cast-magenta.cube   not a look: it turns grey magenta, to prove a LUT reached the node
  README.txt  where the files go on an iPad, which need a DaVinci Intermediate timeline, what is unverified
  SHA256SUMS  `shasum -a 256 -c SHA256SUMS` from inside the folder
The zip is reproducible (fixed member order, time stamps from --date). Refuses a git work tree or iCloud (guard)
and an existing zip unless --overwrite."""

import datetime
import hashlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from . import cst, cube, guard, spaces

LOOKS_DIR = Path(__file__).resolve().parent.parent / "lutlab-looks"
CAST_MAGENTA = (0.05, -0.05, 0.05)  # code-value offset: grey 0.5 becomes 0.55 / 0.45 / 0.55
CAST_SIZE = 33  # the one size every Resolve and camera takes

README = """RP LUT pack {date}
=====================

Cameras: {cameras}. Lattice: {size} points. Built by lutlab pack (rubinstein-productions-toolkit).
Check the files first: inside this folder run  shasum -a 256 -c SHA256SUMS

Getting them into Resolve on an iPad
1. Unzip in the Files app (tap the zip).
2. Copy the .cube files you want into Files > On My iPad > DaVinci Resolve > LUT.
3. In Resolve: Settings > Color Management > Update Lists.
4. Color page > LUTs: the files are listed there; drag one onto a node.
5. Prove the route first with check/cast-magenta.cube on any clip: grey must turn magenta. If nothing
   changes, the LUT is not on the node.
On a Mac the same files go in /Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT, then
Update Lists.

What each file expects
{files}
Which files need a DaVinci Intermediate timeline (or a DI node before them)
- Every looks/*[RCM].cube reads and writes DaVinci Intermediate / DaVinci Wide Gamut. In a Resolve Color
  Managed project set Timeline to DaVinci WG/Intermediate, Output to Rec.709 Gamma 2.4, tone and gamut
  mapping None.
- cst/di-to-r709-*.cube reads DaVinci Intermediate and writes Rec.709 Gamma 2.4. It is only the last node
  in a project that is not colour managed (DaVinci YRGB): <log>-to-di first, then the [RCM] look, then
  di-to-r709 last. Never use it in a Resolve Color Managed project with a Rec.709 output: Resolve converts
  its Rec.709 result a second time and mid grey shows near white.
- Do not put any cst/ file in a Resolve Color Managed project: Resolve already converts the camera and
  the output.
- Camera looks ([S5II], [GH7], [Pyxis]) read the camera's own log and give Rec.709 Gamma 2.4: a project
  that is not colour managed, one node per clip.

Technical transforms, not looks
The cst files have no tone mapping and no gamut mapping (Resolve's Color Space Transform with both None).
<log>-to-r709 clips everything above display white, about 2.5 stops over 18% grey: use it to judge
exposure and colour, not as a grade.

Unverified (record what you find)
- The iPad import route above comes from a third-party tutorial, not Blackmagic; not yet tried on a device.
- Blackmagic lists "LUT in a node" under Studio for the iPad; whether the free app loads these is untested.
- Whether Resolve for iPad interpolates tetrahedrally or trilinearly, and whether it lists 65-point files.
- Whether Resolve for iPad offers V-Log / V-Gamut as an input colour space under colour management.
{extra}"""


def cst_pairs(cameras):
    """(from, to) for each camera, in order, without repeats: rcm takes DI out to Rec.709."""
    pairs = []
    for c in cameras:
        space = spaces.target(c)[0]
        for pr in ([(space, "r709")] if c.lower() == "rcm" else [(space, "di"), (space, "r709")]):
            if pr not in pairs:
                pairs.append(pr)
    return pairs


def cast_table(size=CAST_SIZE):
    return np.clip(cube.lattice(size) + np.array(CAST_MAGENTA), 0.0, 1.0)


def describe(src, dst):
    cams = [label for space, label in spaces.CAMERAS.values() if space == src]
    who = f" ({', '.join(cams)})" if cams else ""
    what = {"di": "DaVinci Intermediate / DaVinci Wide Gamut", "r709": "Rec.709 Gamma 2.4"}[dst]
    return f"{spaces.SPACES[src][3]}{who} in, {what} out"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build(cameras, out, size=33, date=None, base=None, deltas=None, overwrite=False):
    """Write <out>/rp-luts-<date>.zip and return its path."""
    date = date or datetime.date.today().strftime("%Y%m%d")
    day = datetime.datetime.strptime(date, "%Y%m%d")  # ValueError on a bad date
    # strptime takes 1-digit months and days; zip time stamps run 1980 to 2107
    if day.strftime("%Y%m%d") != date or not 1980 <= day.year <= 2107:
        raise ValueError(f"date {date!r}: give YYYYMMDD, 1980 to 2107")
    cameras = list(dict.fromkeys(c.strip().lower() for c in cameras if c.strip()))  # "s5ii,S5II" is one camera
    if not cameras:
        raise ValueError("give at least one camera")
    for c in cameras:
        spaces.target(c)  # ValueError on an unknown camera
    if size not in cst.SIZES:
        raise ValueError(f"size must be one of {cst.SIZES}")
    out = guard.check_out_dir(out)
    name = f"rp-luts-{date}"
    zpath = out / f"{name}.zip"
    if zpath.exists() and not overwrite:
        raise FileExistsError(zpath)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / name
        for d in ("cst", "check"):
            (root / d).mkdir(parents=True)
        lines = []
        for src, dst in cst_pairs(cameras):
            f = cst.write(src, dst, size, root / "cst")
            lines.append(f"- cst/{Path(f).name}: {describe(src, dst)}.")
        if base:
            from . import pipeline  # scipy and Pillow come in only here
            (root / "looks").mkdir()
            deltas = deltas or [str(LOOKS_DIR)]
            files = []
            for d in deltas:
                files += sorted(str(x) for x in Path(d).glob("*.json")) if Path(d).is_dir() else [d]
            pipeline.looks(base, files, cameras, root / "looks", size)
            lines.append(f"- looks/: {len(files)} looks for {', '.join(cameras)} on {Path(base).parent.name}; "
                         "[RCM] files read and write DaVinci Intermediate, the others read the camera's log "
                         "and write Rec.709 Gamma 2.4.")
            extra = ""
        else:
            extra = "\nNo looks in this pack: it was built without a base (pack --base PARAMS.json adds them).\n"
        cube.write_cube(str(root / "check" / "cast-magenta.cube"), cast_table(), "RP check: cast magenta")
        lines.append("- check/cast-magenta.cube: any input; adds magenta (red and blue up, green down) so you can "
                     "see the LUT is applied. Remove it after the check.")
        (root / "README.txt").write_text(README.format(
            date=date, cameras=", ".join(cameras), size=size, files="\n".join(lines) + "\n", extra=extra))
        members = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        (root / "SHA256SUMS").write_text("".join(f"{sha256(root / m)}  {m}\n" for m in members))
        stamp = (day.year, day.month, day.day, 0, 0, 0)
        part = Path(str(zpath) + ".part")
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
                for m in sorted(members + ["SHA256SUMS"]):
                    info = zipfile.ZipInfo(f"{name}/{m}", date_time=stamp)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    z.writestr(info, (root / m).read_bytes())
            os.replace(part, zpath)
        finally:
            part.unlink(missing_ok=True)  # a failed write leaves nothing behind
    return zpath


def summary(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    count = lambda d: sum(1 for n in names if f"/{d}/" in n and n.endswith(".cube"))
    return (f"{path}  {os.path.getsize(path) / 1e6:.1f} MB: {count('cst')} cst, {count('looks')} looks, "
            f"{count('check')} check cube(s), README.txt, SHA256SUMS")


def add_arguments(p):
    p.add_argument("--camera", required=True, help="comma-separated: " + ", ".join(list(spaces.CAMERAS) + ["rcm"]))
    p.add_argument("--base", help="a decomposed base params.json; bakes looks/ (needs scipy)")
    p.add_argument("--looks", nargs="+", help="look delta files or folders (default: the shipped deltas); needs --base")
    p.add_argument("--size", type=int, default=33, choices=cst.SIZES)
    p.add_argument("--date", help="YYYYMMDD for the name and time stamps (default: today, local)")
    p.add_argument("--out")
    p.add_argument("--overwrite", action="store_true")


def run(a):
    if a.looks and not a.base:
        raise SystemExit("lutlab pack: --looks needs --base PARAMS.json (the look deltas are layered on a base)")
    if not a.base:
        print("looks/ skipped: no --base given", file=sys.stderr)
    try:
        path = build(a.camera.split(","), guard.out_dir(a.out, "pack"), a.size, a.date, a.base, a.looks, a.overwrite)
    except ValueError as e:
        raise SystemExit(f"lutlab pack: {e}")
    print(summary(path))
    return 0
