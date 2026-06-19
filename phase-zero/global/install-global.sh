#!/usr/bin/env bash
# install-global.sh — install the phase-zero hook at the user level (~/.claude).
#
# Run once per machine. After this, the trigger phrases load global awareness in
# every Claude Code session on the machine, including outside any repo. Existing
# ~/.claude/settings.json is preserved (the hook is merged in with jq). Idempotent.
#
# Usage:
#   ./install-global.sh
#   STACK_DATA_DIR=/path/to/stack-data ./install-global.sh   # pin the live source
#
# The global hook defers to a repo's own phase-zero hook when you are inside one,
# so phase zero never prints twice.

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"      # .../phase-zero/global
KITROOT="$(cd "$SRC/.." && pwd)"          # .../phase-zero
DEST="${CLAUDE_HOME:-$HOME/.claude}"

mkdir -p "$DEST/hooks"
cp "$SRC/phase-zero-trigger.global.sh" "$DEST/hooks/phase-zero-trigger.sh"
chmod +x "$DEST/hooks/phase-zero-trigger.sh"
cp "$KITROOT/phase-zero.md" "$DEST/phase-zero.md"   # guaranteed fallback core

settings="$DEST/settings.json"
hook_cmd='bash "$HOME/.claude/hooks/phase-zero-trigger.sh"'

if [ -f "$settings" ] && command -v jq >/dev/null 2>&1; then
  entry=$(jq -n --arg cmd "$hook_cmd" '{hooks:[{type:"command",command:$cmd}]}')
  jq --argjson entry "$entry" '
    .hooks //= {} |
    .hooks.UserPromptSubmit //= [] |
    if any(.hooks.UserPromptSubmit[]?; (.hooks[]?.command // "") | test("phase-zero-trigger"))
    then . else .hooks.UserPromptSubmit += [$entry] end
  ' "$settings" > "$settings.tmp" && mv "$settings.tmp" "$settings"
else
  cat > "$settings" <<'JSON'
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/phase-zero-trigger.sh\"" } ] }
    ]
  }
}
JSON
fi

echo "phase-zero global hook installed -> $DEST"
if [ -z "${STACK_DATA_DIR:-}" ] && [ ! -d "$HOME/stack-data" ]; then
  echo "note: no stack-data clone found at ~/stack-data. The hook will use the"
  echo "portable core at $DEST/phase-zero.md. For the live renderer, set"
  echo "STACK_DATA_DIR to your stack-data clone in your shell profile."
fi
