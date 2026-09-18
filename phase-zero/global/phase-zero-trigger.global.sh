#!/usr/bin/env bash
# phase-zero-trigger.global — user-level UserPromptSubmit hook.
#
# Installed at ~/.claude/hooks/ by install-global.sh so the trigger phrases load
# global awareness in every session on this machine, including outside any repo.
#
# Triggers (case-insensitive):
#   "activate all agents" | "engage global awareness" | "refresh global awareness"
#   | "delegate to your orchestrator" | "engage the orchestrator" | "engage your orchestrator"
#
# Two behaviors set it apart from the per-repo hook:
#   1. Inside a repo that already ships the phase-zero kit, it defers to that
#      project hook, so phase zero never prints twice.
#   2. It resolves the source from a known stack-data clone (rich renderer with
#      live state) rather than the current repo. Point it with STACK_DATA_DIR,
#      or it tries the common clone locations, then falls back to the portable
#      core at ~/.claude/phase-zero.md.
#
# Repeat triggers (2026-09-17), same rule as the per-repo hook: the first
# trigger in a session prints the full map, later ones the short form, and
# "refresh global awareness" always prints in full. The marker lives under
# $TMPDIR keyed by session_id, is written only when a map actually printed,
# and the global SessionStart brief removes it. A non-trigger prompt leaves
# through the case gate below with no subprocess spawned.

set -euo pipefail

# Defer to the project-level hook when the current repo ships one. At $HOME the
# candidate path is this hook's own install location, not a repo kit, so a
# home-directory session must not defer to itself (same guard as the session
# brief).
if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ "$CLAUDE_PROJECT_DIR" != "$HOME" ] \
   && [ -f "$CLAUDE_PROJECT_DIR/.claude/hooks/phase-zero-trigger.sh" ]; then
  exit 0
fi

input=$(cat)

shopt -s nocasematch
case "$input" in
  *"activate all agents"*|*"engage global awareness"*|*"refresh global awareness"*|*"delegate to your orchestrator"*|*"engage the orchestrator"*|*"engage your orchestrator"*|*"log learnings"*|*"retro this chat"*|*"session retrospective"*) ;;
  *) exit 0 ;;
esac
shopt -u nocasematch

# get_field <name>: one string field of the event JSON, or "". Never fails;
# with no JSON parser at all it prints nothing (fail closed).
get_field() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r --arg k "$1" '.[$k] // "" | if type == "string" then . else "" end' 2>/dev/null || true
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$input" | python3 -c 'import sys, json
try:
    v = json.load(sys.stdin).get(sys.argv[1], "")
    print(v if isinstance(v, str) else "")
except Exception:
    print("")' "$1" 2>/dev/null || true
  else
    printf ''
  fi
}

section() {
  awk -v h="## $2" '$0 == h { p = 1 } p && $0 != h && /^## / { exit } p { print }' "$1"
}

# Prints the richest map available. Returns 0 only when a map printed.
emit_full() {
  local cand
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] || continue
    if [ -x "$cand/scripts/phase-zero" ]; then bash "$cand/scripts/phase-zero" 2>/dev/null && return 0; fi
    if [ -f "$cand/PHASE-ZERO.md" ]; then cat "$cand/PHASE-ZERO.md" && return 0; fi
  done
  if [ -f "$HOME/.claude/phase-zero.md" ]; then cat "$HOME/.claude/phase-zero.md" && return 0; fi
  echo "(portable core missing; run the kit's global installer: rubinstein-productions-toolkit/phase-zero/global/install-global.sh)"
  return 1
}

emit_short() {
  local cand src=""
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] || continue
    if [ -x "$cand/scripts/phase-zero" ]; then bash "$cand/scripts/phase-zero" --short 2>/dev/null && return 0; fi
    if [ -f "$cand/PHASE-ZERO.md" ]; then src="$cand/PHASE-ZERO.md"; break; fi
  done
  [ -z "$src" ] && [ -f "$HOME/.claude/phase-zero.md" ] && src="$HOME/.claude/phase-zero.md"
  echo '[phase zero: short form. The full map loaded earlier this session; say "refresh global awareness" to reload it.]'
  echo
  [ -n "$src" ] || return 0
  section "$src" "Gear and blast radius"
  section "$src" "Delegation protocol"
  section "$src" "The merge boundary"
  return 0
}

emit_retro() {
  local cand
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] || continue
    if [ -f "$cand/.claude/retrospective.md" ]; then cat "$cand/.claude/retrospective.md" && return 0; fi
    if [ -f "$cand/context/session-retrospective.md" ]; then cat "$cand/context/session-retrospective.md" && return 0; fi
  done
  [ -f "$HOME/.claude/retrospective.md" ] && { cat "$HOME/.claude/retrospective.md"; return 0; }
  return 0
}

prompt=$(get_field prompt | tr '[:upper:]' '[:lower:]')

mode=""
case "$prompt" in
  *"refresh global awareness"*) mode=full ;;
  *"activate all agents"*|*"engage global awareness"*|*"delegate to your orchestrator"*|*"engage the orchestrator"*|*"engage your orchestrator"*) mode=pending ;;
esac

if [ -n "$mode" ]; then
  session=$(get_field session_id | tr -cd 'A-Za-z0-9_-')
  marker=""
  [ -n "$session" ] && marker="${TMPDIR:-/tmp}/phase-zero-seen-$session"
  if [ "$mode" = pending ]; then
    if [ -n "$marker" ] && [ -f "$marker" ]; then mode=short; else mode=full; fi
  fi
  echo "[phase zero engaged — global awareness]"
  echo
  if [ "$mode" = full ]; then
    if emit_full && [ -n "$marker" ]; then { : > "$marker"; } 2>/dev/null || true; fi
  else
    emit_short
  fi
  exit 0
fi

case "$prompt" in
  *"log learnings"*|*"retro this chat"*|*"session retrospective"*)
    echo "[retrospective — reflect and log]"
    echo
    emit_retro
    ;;
esac

exit 0
