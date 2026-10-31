"""lutlab.guard: where output may go.

Decompositions of licensed LUTs, and the LUTs rebuilt from them, are not
for any git repository, and nothing goes to iCloud. Every directory lutlab
writes to passes check_out_dir first."""

import os
import sys
from pathlib import Path

# stdlib-only: the one "not in a repository" test and the one write-then-rename helper (re-exported)
from rpresolve.paths import atomic_path, in_any_git_tree  # noqa: F401

ICLOUD = [Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Library" / "Mobile Documents"]


def check_out_dir(path):
    """Create path if needed and return it, or raise if it is inside a git work tree or iCloud."""
    p = Path(path).expanduser().resolve()
    for bad in ICLOUD:
        pc, bc = str(p), str(bad.resolve())
        if sys.platform == "darwin":  # APFS is case-insensitive by default, so match any spelling
            pc, bc = pc.casefold(), bc.casefold()
        if pc == bc or pc.startswith(bc + os.sep):
            raise PermissionError(f"{p}: refusing to write under {bad} (iCloud-synced)")
    if in_any_git_tree(p):
        raise PermissionError(f"{p}: refusing to write inside a git work tree; LUT values and "
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
