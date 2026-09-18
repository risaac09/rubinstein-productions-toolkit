#!/usr/bin/env bash
# phase-zero-lib.sh — the three helpers the four phase-zero hooks share.
#
# Sourced, never executed, by hooks/phase-zero-trigger.sh, hooks/session-brief.sh
# and their global twins under global/. Deployed beside the hooks by install.sh
# (repo kit) and install-global.sh (~/.claude/hooks). Before 2026-09-17 each
# hook carried its own copy of the JSON-field ladder and the marker path, and
# they had already drifted by one word; one file, four callers.
#
#   pz_field <json> <name>      one string field of an event JSON, or "".
#                               Never fails: a parse error or a missing field is
#                               "", and with no JSON parser at all it prints
#                               nothing, because matching phrases against the raw
#                               event can false-positive on non-prompt fields.
#   pz_section <file> <heading> that "## heading" section, through the line
#                               before the next "## ", so the short form prints
#                               the protocol from the one source.
#   pz_marker <session_id>      the per-session marker path under $TMPDIR, or ""
#                               when the id is empty after sanitizing. The
#                               trigger hook writes it after a full map prints;
#                               the session brief removes it on start, resume,
#                               clear, and compaction.

pz_field() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -r --arg k "$2" '.[$k] // "" | if type == "string" then . else "" end' 2>/dev/null || true
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$1" | python3 -c 'import sys, json
try:
    v = json.load(sys.stdin).get(sys.argv[1], "")
    print(v if isinstance(v, str) else "")
except Exception:
    print("")' "$2" 2>/dev/null || true
  else
    printf ''
  fi
}

pz_section() {
  awk -v h="## $2" '$0 == h { p = 1 } p && $0 != h && /^## / { exit } p { print }' "$1"
}

pz_marker() {
  local s
  s="$(printf '%s' "${1:-}" | tr -cd 'A-Za-z0-9_-')"
  [ -n "$s" ] && printf '%s' "${TMPDIR:-/tmp}/phase-zero-seen-$s"
  return 0
}
