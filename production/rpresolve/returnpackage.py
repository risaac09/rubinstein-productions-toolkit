"""
rpresolve.returnpackage: assemble and verify the return package of
production/return-package.md. Copies, never moves; camera files keep their
names; a final ships only on a deliver_check PASS. Offline, stdlib only.

    <out>/<YYYY-MM>_<engagement-id>/
      01_originals/<card folders>  + SHA256SUMS (paths as `find .` writes them)
      02_finals/<destination key>/   03_captions/   04_project/<x>.drp
      05_session/                    manifest.md (a row and checksum per file)
"""

import os
import re
import shutil
import time

from . import deliver, paths
from .manifest import sha256

SUMS = "SHA256SUMS"
MANIFEST = "manifest.md"
SHA_RE = re.compile(r"[0-9a-f]{64}")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
VIDEO_EXTS = {".mp4", ".mov"}
IGNORED = {".ds_store"}


class PackageError(RuntimeError):
    """The package cannot be built or checked."""


def files_under(root):
    """Sorted relative paths (forward slashes) of the files below root, .DS_Store left out."""
    out = []
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for n in dirs + names:
            if os.path.islink(os.path.join(d, n)):
                raise PackageError(f"{os.path.join(d, n)} is a symlink; copy the real files, not links")
        out += [os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")
                for n in names if n.lower() not in IGNORED]
    return sorted(out)


def check_final(config, path, sub):
    """Run deliver_check on one final against its destination; the result dict.
    Raises delivercheck.ToolMissing or CheckError. The one seam tests replace."""
    from . import delivercheck as dc
    subs = deliver.subfolders(config)
    return dc.check(path, deliver.destination(config, subs[sub.casefold()]), folders=subs)


def gate(config, finals, check=None, ignore=()):
    """Blockers for the finals folder <finals>/<destination key>/<files>: a list
    of 'path: reason' strings, empty when every video final has a deliver_check
    PASS and every .srt sits beside a video of the same stem."""
    from . import delivercheck as dc
    check = check or check_final
    subs = deliver.subfolders(config)
    blockers, seen = [], 0
    for rel in files_under(finals):
        sub, _, name = rel.rpartition("/")
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        if not sub and name in ignore:
            continue  # deliver-manifest passes its own manifest.md and checksums.txt
        if not sub or "/" in sub or sub.casefold() not in subs:
            blockers.append(f"{rel}: not in a configured destination's folder")
        elif ext in VIDEO_EXTS:
            seen += 1
            try:
                status = check(config, os.path.join(finals, rel), sub)["status"]
            except (dc.CheckError, deliver.DeliverError) as e:
                blockers.append(f"{rel}: no deliver_check result ({e})")
                continue
            if status != "pass":
                blockers.append(f"{rel}: deliver_check {status.upper()}")
        elif ext == ".srt":
            if not any(os.path.exists(os.path.join(finals, sub, stem + e)) for e in VIDEO_EXTS):
                blockers.append(f"{rel}: caption with no video beside it")
        else:
            blockers.append(f"{rel}: not a final (mp4, mov or srt)")
    if not seen and not blockers:
        blockers.append(f"{finals}: no finals found")
    return blockers


def _copy_tree(src, dst):
    for rel in files_under(src):
        os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
        shutil.copy2(os.path.join(src, rel), os.path.join(dst, rel))
        if sha256(os.path.join(src, rel)) != sha256(os.path.join(dst, rel)):
            raise PackageError(f"{rel}: the copy does not match the source")


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or "none"


def build(out, engagement, month, approval, prepared_by, originals, finals, config,
          project_file=None, session=None, licences=(), check=None):
    """Gate the finals, then assemble the package under out. Returns its path.
    Raises PackageError with every blocker listed."""
    if not ID_RE.fullmatch(engagement):
        raise PackageError("engagement id must be letters, digits, '-' or '_' (never a client name)")
    if not MONTH_RE.fullmatch(month):
        raise PackageError("month must be YYYY-MM")
    if paths.in_any_git_tree(out):
        raise PackageError(f"{out} is inside a git working tree; write the package outside any repository")
    if not originals:
        raise PackageError("no originals given")
    for src in list(originals) + [finals] + ([session] if session else []):
        if not os.path.isdir(src):
            raise PackageError(f"{src} is not a folder")
    if project_file and (not os.path.isfile(project_file) or not project_file.lower().endswith(".drp")):
        raise PackageError(f"{project_file} is not a .drp file")
    cards = [os.path.basename(os.path.normpath(o)) for o in originals]
    if len(set(cards)) != len(cards):
        raise PackageError("two original folders share a name; the folder name is the card name")
    here = os.path.realpath(out)
    for src in list(originals) + [finals] + ([session] if session else []):
        there = os.path.realpath(src)
        if here == there or here.startswith(there + os.sep) or there.startswith(here + os.sep):
            raise PackageError(f"{out} and {src} contain one another; build the package elsewhere")
    final_dir = os.path.join(os.path.abspath(out), f"{month}_{engagement}")
    work = final_dir + ".partial"
    if os.path.exists(final_dir) or os.path.exists(work):
        raise PackageError(f"{final_dir} already exists; the package is not overwritten")
    names = [os.path.basename(r) for r in files_under(finals) if r.lower().endswith(".srt")]
    if len(set(names)) != len(names):
        raise PackageError("two captions share a file name; 03_captions holds them side by side")
    for rel in [r for o in originals for r in files_under(o)] + files_under(finals):
        if "|" in rel:
            raise PackageError(f"{rel}: a '|' in a file name cannot be listed in the manifest")
    blockers = gate(config, finals, check)
    if blockers:
        raise PackageError("blocked: not every final has a deliver_check PASS\n  " + "\n  ".join(blockers))
    try:
        for o, card in zip(originals, cards):
            _copy_tree(o, os.path.join(work, "01_originals", card))
        orig = os.path.join(work, "01_originals")
        listed = files_under(orig)
        with open(os.path.join(orig, SUMS), "w", encoding="utf-8") as f:
            f.write("".join(f"{sha256(os.path.join(orig, r))}  ./{r}\n" for r in listed))
        _copy_tree(finals, os.path.join(work, "02_finals"))
        for rel in files_under(finals):
            if rel.lower().endswith(".srt"):
                dst = os.path.join(work, "03_captions", os.path.basename(rel))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(finals, rel), dst)
        if project_file:
            os.makedirs(os.path.join(work, "04_project"))
            shutil.copy2(project_file, os.path.join(work, "04_project", os.path.basename(project_file)))
        if session:
            _copy_tree(session, os.path.join(work, "05_session"))
        _write_manifest(work, engagement, approval, prepared_by, licences, len(listed))
        os.rename(work, final_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return final_dir


def _write_manifest(work, engagement, approval, prepared_by, licences, n_originals):
    rows = []
    for folder in ("02_finals", "03_captions", "04_project", "05_session"):
        base = os.path.join(work, folder)
        for rel in files_under(base) if os.path.isdir(base) else []:
            dest = rel.split("/")[0] if folder == "02_finals" else "n/a"
            ext = os.path.splitext(rel)[1][1:].lower() or "n/a"
            rows.append(f"| {_cell(folder + '/' + rel)} | {ext} | {sha256(os.path.join(base, rel))} "
                        f"| {_cell(dest)} | {_cell(approval)} |")
    lic = ["| Asset | Licence | Attribution required |", "|---|---|---|"]
    for item in licences:
        parts = [p.strip() for p in item.split("|")]
        if len(parts) != 3:
            raise PackageError(f"--licence wants 'asset|licence|attribution', got {item!r}")
        lic.append("| " + " | ".join(_cell(p) for p in parts) + " |")
    text = "\n".join([
        f"# Return package manifest: {_cell(engagement)}", "",
        "| | |", "|---|---|",
        f"| Engagement | {_cell(engagement)} |", f"| Packaged | {time.strftime('%Y-%m-%d')} |",
        f"| Prepared by | {_cell(prepared_by)} |", f"| Approval reference | {_cell(approval)} |", "",
        "## Originals", "",
        f"{n_originals} files, every card as shot. Checksums: `01_originals/{SUMS}`.", "",
        "## Files", "",
        "| File | Format | Checksum | Destination | Approval reference |",
        "|---|---|---|---|---|", *rows, "",
        "## Licences used", "", *(lic if len(lic) > 2 else ["none"]), ""])
    with open(os.path.join(work, MANIFEST), "w", encoding="utf-8") as f:
        f.write(text)


def _expected(pkg):
    """{package-relative path: sha} from SHA256SUMS and the manifest rows."""
    exp = {}
    sums = os.path.join(pkg, "01_originals", SUMS)
    if not os.path.isfile(sums):
        raise PackageError(f"no 01_originals/{SUMS} in {pkg}")
    with open(sums, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            h, sep, rel = line.partition("  ")
            rel = rel[2:] if rel.startswith("./") else rel
            if not sep or not SHA_RE.fullmatch(h) or ".." in rel.split("/") or rel.startswith("/"):
                raise PackageError(f"{SUMS} line {n} is not '<sha256>  <relative path>'")
            exp["01_originals/" + rel] = h
    m = os.path.join(pkg, MANIFEST)
    if not os.path.isfile(m):
        raise PackageError(f"no {MANIFEST} in {pkg}")
    with open(m, encoding="utf-8") as f:
        for line in f:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and SHA_RE.fullmatch(cells[2]) and ".." not in cells[0].split("/") \
                    and not cells[0].startswith("/"):
                exp[cells[0]] = cells[2]
    return exp


def verify(pkg):
    """[(status, path)]: OK, FAILED, MISSING, or NOT LISTED for a package file
    that neither SHA256SUMS nor the manifest covers. Raises PackageError."""
    pkg = os.path.abspath(pkg)
    exp = _expected(pkg)
    out = [("MISSING" if not os.path.isfile(os.path.join(pkg, rel))
            else "OK" if sha256(os.path.join(pkg, rel)) == h else "FAILED", rel)
           for rel, h in sorted(exp.items())]
    skip = {MANIFEST, "01_originals/" + SUMS}
    out += [("NOT LISTED", r) for r in files_under(pkg) if r not in exp and r not in skip]
    return out
