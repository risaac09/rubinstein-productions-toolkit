"""lutlab.guard: where output may go.

Decompositions of licensed LUTs, and the LUTs rebuilt from them, are not
for any git repository, and nothing goes to iCloud. Every directory lutlab
writes to passes check_out_dir first."""

import os
from pathlib import Path

ICLOUD = [Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Library" / "Mobile Documents"]


def git_root(path):
    p = Path(path).resolve()
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def check_out_dir(path):
    """Create path if needed and return it, or raise if it is inside a git work tree or iCloud."""
    p = Path(path).expanduser().resolve()
    for bad in ICLOUD:
        pc, bc = str(p).casefold(), str(bad.resolve()).casefold()  # APFS is case-insensitive, so match any spelling
        if pc == bc or pc.startswith(bc + os.sep):
            raise PermissionError(f"{p}: refusing to write under {bad} (iCloud-synced)")
    root = git_root(p)
    if root:
        raise PermissionError(f"{p}: refusing to write inside the git work tree {root}; LUT values and "
                              "decompositions stay out of repositories")
    p.mkdir(parents=True, exist_ok=True)
    return p


def out_dir(arg, name=None):
    """--out, else $LUTLAB_OUT/<name>."""
    if arg:
        return check_out_dir(arg)
    base = os.environ.get("LUTLAB_OUT")
    if not base:
        raise SystemExit("lutlab: give --out DIR or set LUTLAB_OUT")
    return check_out_dir(Path(base) / name if name else base)
