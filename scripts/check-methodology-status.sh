#!/usr/bin/env bash
# check-methodology-status.sh: every methodology, docs and production document
# says how far to trust it.
#
# Covered: methodology/*.md, docs/**/*.md (any depth) and production/*.md (top
# level only; not production/skills/ or the tool subfolders). Each must carry a
# line `**Status:** <word>` in its first 25 lines, where <word> is one of:
#   operational   in use as written
#   draft         working text; parts are open or unsettled
#   aspirational  describes a plan or forecast, not current practice
#   superseded    replaced; kept for history, with a pointer to the successor
# A stranger reading a forecast as a fact is the most expensive misreading this
# repo can cause, so a document with no status fails CI. A line may continue
# after the word (a date, a note); a quote prefix `> ` is fine.
#
# For the six files scripts/sync-methodology.sh mirrors from the vault, the
# status sits on the first line, because the sync preserves only that line.
#
# Optional first argument: the root to check (default: the repo). The test uses it.
set -u
cd "${1:-$(dirname "$0")/..}" || exit
bad=0
for d in methodology docs production; do
  [ -d "$d" ] || { echo "missing directory: $d" >&2; bad=1; }
done
# A folder find cannot read is an error, never a clean pass.
files=$(find methodology production -maxdepth 1 -name '*.md' && find docs -name '*.md') \
  || { echo "cannot list every covered folder" >&2; bad=1; }
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if ! head -25 "$f" | grep -Eq '\*\*Status:\*\* (operational|draft|aspirational|superseded)([^A-Za-z0-9_/-]|$)'; then
    echo "no status header: $f" >&2
    bad=1
  fi
done < <(printf '%s\n' "$files" | sort)
[ "$bad" -eq 0 ] && echo "check-methodology-status: CLEAN"
exit "$bad"
