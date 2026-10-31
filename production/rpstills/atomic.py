"""rpstills.atomic: write a file by temp name and rename, so a hard link at the output name is not written through."""

import contextlib
import os
import tempfile


@contextlib.contextmanager
def atomic_path(path):
    """Yield a fresh temp path in path's folder (same extension, so an image library picks the format);
    on a clean exit it is renamed onto path, on an error it is removed. An existing file or hard link at
    path keeps its old content: the rename replaces the name, not the inode."""
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
