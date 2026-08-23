#!/usr/bin/env bash
# permissions-audit.sh — report-only drift/ratchet check on the allow list.
#
# settings.local.json allow entries evaluate before the auto-mode classifier,
# so an allow entry is a permanent hole through exactly the commands the
# classifier's soft/hard-deny rules name. No surface reviewed this file
# before. This script never edits it; pruning stays Isaac's one-batch human
# act. Actor-layer adversarial review F01, 2026-08-16.
#
# Check A: any live entry not in permissions-baseline.json (drift since the
#          baseline was captured).
# Check B: any live entry (baseline or new) matching a fixed set of
#          classifier-defeating patterns, regardless of drift status.
# Exit 1 if either check has hits, 0 if clean.
set -uo pipefail

SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.local.json}"
# Machine-local, never committed here. The baseline is a verbatim snapshot of
# one machine's allow list, which is a list of exactly the commands that run
# with no approval prompt on that machine. That is the last thing to publish
# from a public repo, so it lives beside the settings it mirrors. Seed it with:
#   jq '{allow: (.permissions.allow // [])}' ~/.claude/settings.local.json \
#     > ~/.claude/permissions-baseline.json
BASELINE="${CLAUDE_PERMISSIONS_BASELINE:-$HOME/.claude/permissions-baseline.json}"

[ -f "$SETTINGS" ] || { echo "permissions-audit: no $SETTINGS, nothing to check"; exit 0; }
[ -f "$BASELINE" ] || {
  echo "permissions-audit: no baseline at $BASELINE" >&2
  echo "  seed it: jq '{allow: (.permissions.allow // [])}' $SETTINGS > $BASELINE" >&2
  exit 1
}

rc=0
count=$(jq '.permissions.allow // [] | length' "$SETTINGS")

# Fetch by index (not a newline/NUL-delimited stream) so multi-line entries
# (Bash commands with backslash continuations) stay one atomic unit each.
i=0
while [ "$i" -lt "$count" ]; do
  entry=$(jq -r --argjson i "$i" '.permissions.allow[$i]' "$SETTINGS")
  i=$((i + 1))
  [ -n "$entry" ] || continue
  if ! jq -e --arg e "$entry" '.allow | index($e) != null' "$BASELINE" >/dev/null; then
    echo "NEW: $entry"; rc=1
  fi
  case "$entry" in
    Bash\(cat:*\)|Bash\(find:*\)|Bash\(head:*\)|Bash\(tail:*\)|Bash\(python3:*\)|Bash\(ls:*\)|\
    Bash\(security\ *|Bash\(osascript:*\)|Bash\(sudo\ *|Bash\(git\ config\ *|Bash\(crontab:*\)|Bash\(launchctl*)
      echo "FLAG: $entry"; rc=1 ;;
  esac
done

[ "$rc" -eq 0 ] && echo "permissions-audit: clean"
exit $rc
