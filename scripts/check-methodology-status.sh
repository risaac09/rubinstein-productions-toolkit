#!/usr/bin/env bash
# check-methodology-status.sh: every methodology document says how far to trust it.
#
# Each methodology/*.md must carry a line `**Status:** <word>` in its first 25
# lines, where <word> is one of:
#   operational   in use as written
#   draft         working text; parts are open or unsettled
#   aspirational  describes a plan or forecast, not current practice
#   superseded    replaced; kept for history, with a pointer to the successor
# A stranger reading a forecast as a fact is the most expensive misreading this
# repo can cause, so a document with no status fails CI.
#
# For the six files scripts/sync-methodology.sh mirrors from the vault, the
# status sits on the first line, because the sync preserves only that line.
set -u
cd "$(dirname "$0")/.." || exit
bad=0
for f in methodology/*.md; do
  if ! head -25 "$f" | grep -Eq '\*\*Status:\*\* (operational|draft|aspirational|superseded)\b'; then
    echo "no status header: $f" >&2
    bad=1
  fi
done
[ "$bad" -eq 0 ] && echo "check-methodology-status: CLEAN"
exit "$bad"
