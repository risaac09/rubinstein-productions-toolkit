"""
rpresolve.refs: the one refusal for a private reference library. A library
holds the author's own finished work as grade targets (see
production/reference-library.md); its root carries a marker file, and nothing
the toolkit delivers or writes as a packet may land inside it. Stdlib only.
"""

import os

# Not REFERENCE-LIBRARY.md: macOS folds case, and the layout doc is reference-library.md.
MARKER = "REFERENCE-LIBRARY-ROOT.md"


def is_reference(path):
    """True when any folder above `path` (or `path` itself, when a folder)
    holds the marker. `path` need not exist; links are resolved first."""
    here = os.path.realpath(os.path.expanduser(str(path)))
    folder = here if os.path.isdir(here) else os.path.dirname(here)
    while True:
        if os.path.isfile(os.path.join(folder, MARKER)):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def reference_problem(path):
    """Why `path` may not take output, or None."""
    if is_reference(path):
        return (f"{path} is inside a reference library (a folder above it holds {MARKER}). "
                "A library holds targets for grading, never deliverables or packets; write elsewhere.")
    return None
