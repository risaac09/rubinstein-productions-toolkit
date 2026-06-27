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
#      live signal counts) rather than the current repo. Point it with
#      STACK_DATA_DIR, or it tries the common clone locations, then falls back
#      to the portable core at ~/.claude/phase-zero.md.

set -euo pipefail

# Defer to the project-level hook when the current repo ships one.
if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$CLAUDE_PROJECT_DIR/.claude/hooks/phase-zero-trigger.sh" ]; then
  exit 0
fi

input=$(cat)

get_prompt() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -r '.prompt // ""' 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$1" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("prompt",""))
except Exception: print("")' 2>/dev/null
  else
    printf '%s' "$1"
  fi
}

emit() {
  local cand
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] || continue
    if [ -x "$cand/scripts/phase-zero" ]; then bash "$cand/scripts/phase-zero" 2>/dev/null && return 0; fi
    if [ -f "$cand/PHASE-ZERO.md" ]; then cat "$cand/PHASE-ZERO.md" && return 0; fi
  done
  [ -f "$HOME/.claude/phase-zero.md" ] && { cat "$HOME/.claude/phase-zero.md"; return 0; }
  return 0
}

prompt=$(get_prompt "$input" | tr '[:upper:]' '[:lower:]')

case "$prompt" in
  *"activate all agents"*|*"engage global awareness"*|*"refresh global awareness"*|*"delegate to your orchestrator"*|*"engage the orchestrator"*|*"engage your orchestrator"*)
    echo "[phase zero engaged — global awareness]"
    echo
    emit
    ;;
esac

exit 0
