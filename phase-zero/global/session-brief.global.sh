#!/usr/bin/env bash
# session-brief.global — user-level SessionStart fallback.
#
# Home-directory Claude sessions account for most measured CLI work. Repository
# sessions receive the canonical routing brief from their deployed phase-zero
# kit. This hook covers sessions opened from ~/ or another directory without
# that kit and defers when a project-level SessionStart hook exists.

set -euo pipefail

cat >/dev/null 2>&1 || true

project="${CLAUDE_PROJECT_DIR:-}"
if [ -n "$project" ] && [ "$project" != "$HOME" ] && [ -f "$project/.claude/hooks/session-brief.sh" ]; then
  exit 0
fi

routing="$HOME/.claude/model-routing.md"
[ -f "$routing" ] || exit 0

echo "[session brief: global model routing and standing context]"
echo
cat "$routing"
echo

if [ -f "$HOME/.claude/operating-brief.md" ]; then
  echo "Operating brief: ~/.claude/operating-brief.md. Read it before work that spans more than one repo or more than one session."
fi

for stack in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
  [ -n "$stack" ] || continue
  if [ -f "$stack/docs/DECISIONS.md" ]; then
    echo "Decisions of record: $stack/docs/DECISIONS.md. Cite a settled call instead of re-deriving it."
    [ -f "$stack/docs/FAILURE-MODES.md" ] && echo "Failure catalog: $stack/docs/FAILURE-MODES.md."
    break
  fi
done

exit 0
