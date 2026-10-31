"""rpstills.atomic: write a file by temp name and rename, so a hard link at the output name is not written through."""

import contextlib
import os
import tempfile


@contextlib.contextmanager
def atomic_path(path):
    """Yield a fresh temp path in path's folder. It ends in .part, so no scan for images or cubes lists a
    half-written file (give an image library its format by name). On a clean exit it is renamed onto path, on an error it is removed. An existing file or hard link at
    path keeps its old content: the rename replaces the name, not the inode."""
    path = os.path.realpath(path)   # a symlink at the name is written through to its target, as open() did
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".part-" + os.path.basename(path)[:80] + "-", suffix=".part")
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
