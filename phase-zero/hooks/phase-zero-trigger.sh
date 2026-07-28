#!/usr/bin/env bash
# phase-zero-trigger — UserPromptSubmit hook (portable across every repo).
#
# Reads the prompt-submit event JSON on stdin. If the prompt contains a
# phase-zero trigger phrase, it prints global awareness on stdout, which the
# harness adds to the model's context before the turn runs. Any other prompt
# passes through untouched (no output, exit 0).
#
# Triggers (case-insensitive; this case pattern is the canonical set):
#   "activate all agents" | "engage global awareness" | "refresh global awareness"
#   | "delegate to your orchestrator" | "engage the orchestrator" | "engage your orchestrator"
# Retro triggers (the bookend): "log learnings" | "retro this chat" | "session retrospective"
#
# Source of truth: stack-data/PHASE-ZERO.md. This kit is versioned in
# rubinstein-productions-toolkit/phase-zero/ and installed into each repo's
# .claude/ so every clone session shares the same infrastructure. It resolves
# the richest awareness available in the current repo, in order:
#   1. scripts/phase-zero   (stack-data: full hierarchy + live signal counts)
#   2. PHASE-ZERO.md        (stack-data: full hierarchy)
#   3. .claude/phase-zero.md (the portable core carried into every repo)

set -euo pipefail

input=$(cat)

get_prompt() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -r '.prompt // ""' 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$1" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("prompt",""))
except Exception: print("")' 2>/dev/null
  else
    # No JSON parser available: fail closed. Matching phrases against the raw
    # event JSON can false-positive on non-prompt fields, so print nothing and
    # let the operator retype the phrase on a machine with jq or python3.
    printf ''
  fi
}

prompt=$(get_prompt "$input" | tr '[:upper:]' '[:lower:]')
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

case "$prompt" in
  *"activate all agents"*|*"engage global awareness"*|*"refresh global awareness"*|*"delegate to your orchestrator"*|*"engage the orchestrator"*|*"engage your orchestrator"*)
    echo "[phase zero engaged — global awareness]"
    echo
    if [ -x "$root/scripts/phase-zero" ]; then
      bash "$root/scripts/phase-zero" 2>/dev/null && exit 0
    fi
    if [ -f "$root/PHASE-ZERO.md" ]; then
      cat "$root/PHASE-ZERO.md" && exit 0
    fi
    if [ -f "$root/.claude/phase-zero.md" ]; then
      cat "$root/.claude/phase-zero.md" && exit 0
    fi
    echo "(portable core missing in this repo; run the kit installer: rubinstein-productions-toolkit/phase-zero/install.sh)"
    ;;
esac

# Retrospective: the bookend to phase zero. Loads the reflect-and-log prompt.
case "$prompt" in
  *"log learnings"*|*"retro this chat"*|*"session retrospective"*)
    echo "[retrospective — reflect and log]"
    echo
    if [ -f "$root/.claude/retrospective.md" ]; then
      cat "$root/.claude/retrospective.md" && exit 0
    fi
    if [ -f "$root/context/session-retrospective.md" ]; then
      cat "$root/context/session-retrospective.md" && exit 0
    fi
    ;;
esac

exit 0
