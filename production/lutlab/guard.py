"""lutlab.guard: where output may go.

Decompositions of licensed LUTs, and the LUTs rebuilt from them, are not
for any git repository, and nothing goes to iCloud. Every directory lutlab
writes to passes check_out_dir first."""

import contextlib
import os
import sys
import tempfile
from pathlib import Path

from rpresolve.paths import in_any_git_tree  # stdlib-only; the one "not in a git tree" test

ICLOUD = [Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Library" / "Mobile Documents"]


@contextlib.contextmanager
def atomic_path(path):
    """Yield a fresh temp path in path's folder (same extension, so an image library picks the format);
    on a clean exit it is renamed onto path, on an error it is removed. A hard link at path keeps its
    old content: the rename replaces the name, not the inode."""
    path = os.path.abspath(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".part-" + os.path.basename(path) + "-", suffix=os.path.splitext(path)[1])
    os.close(fd)
    try:
        yield tmp
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)   # mkstemp makes 0600; open() would have made 0666 less the umask
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise


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
