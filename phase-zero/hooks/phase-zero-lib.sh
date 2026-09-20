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
#   pz_marker <session_id> <scope>
#                               the per-session marker path under $TMPDIR, or
#                               "" when the id is empty after sanitizing. The
#                               trigger hook writes it after a full map prints;
#                               the session brief removes it on start, resume,
#                               clear, and compaction.
#
#                               <scope> is the installation the marker belongs
#                               to, and callers pass the directory the hook
#                               itself lives in. Without it the marker was
#                               keyed on session_id alone, while every repo
#                               carries its own copy of these hooks and a
#                               login session shares one $TMPDIR. So the first
#                               trigger in a second repo read the marker the
#                               first repo wrote, printed the short form, and
#                               told the reader "the full map loaded earlier
#                               this session" while emitting none of that
#                               repo's map. Reproduced 2026-09-19 by running
#                               two installs against one TMPDIR and one
#                               session id. The trigger and the brief sit in
#                               the same directory, so they agree on the scope
#                               without being told what it is.

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
  local s scope
  s="$(printf '%s' "${1:-}" | tr -cd 'A-Za-z0-9_-')"
  [ -n "$s" ] || return 0
  # cksum, not the path: the path can hold anything, and the marker name has
  # to stay a single harmless filename. An absent scope still hashes to a
  # stable value, so an old caller keeps working.
  scope="$(printf '%s' "${2:-}" | cksum | tr -cd '0-9 ' | cut -d' ' -f1)"
  printf '%s' "${TMPDIR:-/tmp}/phase-zero-seen-$s-${scope:-0}"
  return 0
}
