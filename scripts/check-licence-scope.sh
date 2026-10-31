#!/usr/bin/env bash
# check-licence-scope.sh [ROOT]: every tracked top-level directory must be
# classified as MIT (code), CC BY-SA (content) or exempt. The MIT names are
# checked against the Scope paragraph of LICENSE; everything else is CC BY-SA. An unclassified directory fails.
set -eu
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
MIT=" scripts production tools phase-zero public-kit "
CC=" docs methodology templates prompts "
EXEMPT=" changelog.d .github .claude .agents "
cd "$ROOT"
bad=0
files=$(git ls-files) || { echo "git ls-files failed in $ROOT" >&2; exit 2; }
[ -n "$files" ] || { echo "no tracked files in $ROOT" >&2; exit 2; }
for d in $(printf '%s\n' "$files" | grep / | cut -d/ -f1 | sort -u); do
  case "$MIT$CC$EXEMPT" in *" $d "*) ;; *) echo "unclassified top-level directory: $d" >&2; bad=1;; esac
done
# The MIT list must still match the stated scope.
for d in $MIT; do grep -q "\`$d/\`" LICENSE || { echo "LICENSE scope no longer names $d/" >&2; bad=1; }; done
[ "$bad" -eq 0 ] || exit 1
echo "licence scope clean"
