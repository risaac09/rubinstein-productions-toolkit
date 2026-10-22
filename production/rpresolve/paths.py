"""
rpresolve.paths: where output that names client media may go. Stdlib only.

Reports, detect tables, manifests and endcheck results name clients, media
paths and transcripts, and this toolkit is a public repository. They are
refused inside this repository's working trees, and with any_git_tree=True
inside any git working tree at all (another public repo would leak them
just the same).
"""

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


def out_problem(path, any_git_tree=False):
    """Why `path` cannot take private output, or None: inside this repo (or
    any git tree with any_git_tree=True), a directory, or not writable."""
    from resolve_survey import inside_repo, output_path_problem  # lazy: keeps this module light
    if inside_repo(path):
        return (f"{path} is inside this repository's git working tree. The output "
                "names client media; write it outside the repo.")
    if any_git_tree and in_any_git_tree(path):
        return (f"{path} is inside a git working tree. The output names client media; "
                "write it outside any repository.")
    return output_path_problem(path)


def write_private(path, text, any_git_tree=False):
    """Write output that names private media. Raises OutputRefused."""
    problem = out_problem(path, any_git_tree)
    if problem:
        raise OutputRefused(problem)
    # Temp file and rename: a hard link to the target elsewhere keeps its old
    # content, so a link into a repository cannot carry the output there. The
    # file keeps the mode open() gave it (0666 less the umask), and a missing
    # folder still fails as it did.
    target = os.path.realpath(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".rp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise
    return path
