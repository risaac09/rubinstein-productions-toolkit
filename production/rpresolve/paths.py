"""
rpresolve.paths: where output that names client media may go. Stdlib only.

Reports, detect tables, manifests and endcheck results name clients, media
paths and transcripts, and this toolkit is a public repository. They are
refused inside any git working tree (another public repo would leak them
just the same); any_git_tree=False narrows that to this repository's trees.
"""

import contextlib
import os
import tempfile
from pathlib import Path


class OutputRefused(ValueError):
    """An output path that must not be written."""


def default_out_dir():
    """Where tools write when no path is given: $RPRESOLVE_MCP_OUT, else
    ~/Library/Caches/rpresolve/mcp. Created on demand."""
    d = os.environ.get("RPRESOLVE_MCP_OUT") or os.path.expanduser("~/Library/Caches/rpresolve/mcp")
    os.makedirs(d, exist_ok=True)
    return d


def in_any_git_tree(path):
    """True when `path` (or its nearest existing folder) sits inside any git
    working tree: a .git directory or a worktree's .git file above it."""
    here = Path(os.path.realpath(path))
    folder = here if here.is_dir() else here.parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    for d in (folder, *folder.parents):
        if (d / ".git").exists():
            return True
    return False


def out_problem(path, any_git_tree=True):
    """Why `path` cannot take private output, or None: inside any git tree
    (only this repo's with any_git_tree=False), a directory, or not writable."""
    from resolve_survey import inside_repo, output_path_problem  # lazy: keeps this module light
    if inside_repo(path):
        return (f"{path} is inside this repository's git working tree. The output "
                "names client media; write it outside the repo.")
    if any_git_tree and in_any_git_tree(path):
        return (f"{path} is inside a git working tree. The output names client media; "
                "write it outside any repository.")
    return output_path_problem(path)


@contextlib.contextmanager
def atomic_path(path, prefix=None, suffix=".part"):
    """Yield a fresh temp path in path's folder (default name ends in .part, so no scan for images or
    cubes lists a half-written file). On a clean exit it is renamed onto path, on an error it is removed.
    A hard link at path keeps its old content: the rename replaces the name, not the inode. No tree
    check here: callers that need one use write_private or their own guard."""
    path = os.path.realpath(path)   # a symlink at the name is written through to its target, as open() did
    if prefix is None:
        prefix = ".part-" + os.path.basename(path)[:80] + "-"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=prefix, suffix=suffix)
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


def write_private(path, text, any_git_tree=True):
    """Write output that names private media. Raises OutputRefused."""
    problem = out_problem(path, any_git_tree)
    if problem:
        raise OutputRefused(problem)
    # Temp file and rename: a hard link to the target elsewhere keeps its old
    # content, so a link into a repository cannot carry the output there. A
    # missing folder still fails as it did.
    with atomic_path(path, prefix=".rp-", suffix=".tmp") as tmp:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
    return path


def source_problem(outs, sources):
    """Why one of `outs` must not be written, or None: it is one of `sources`
    (os.path.samefile, so another name for the same file, such as one that
    differs only in case on a case-insensitive disk, counts too)."""
    for o in outs:
        for s in sources:
            if os.path.exists(o) and os.path.exists(s) and os.path.samefile(o, s):
                return f"{o} is the source {s}; a source is never written over"
    return None
