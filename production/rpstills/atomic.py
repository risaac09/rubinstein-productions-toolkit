"""rpstills.atomic: write a file by temp name and rename, so a hard link at the output name is not written through.
The one implementation is rpresolve.paths.atomic_path."""


def atomic_path(path):
    from rpresolve.paths import atomic_path as one  # lazy: rpresolve is stdlib-only, imported only to write
    return one(path)
