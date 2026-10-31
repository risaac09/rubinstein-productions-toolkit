rpresolve.paths now refuses any git working tree by default (any_git_tree=True), and lutlab.guard uses the same test, so a new caller no longer has to remember the flag.
