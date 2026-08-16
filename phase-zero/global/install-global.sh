#!/usr/bin/env bash
# install-global.sh — install the phase-zero hooks at the user level (~/.claude).
#
# Run once per machine. After this, the trigger phrases load global awareness in
# every Claude Code session on the machine, including outside any repo. Existing
# ~/.claude/settings.json is preserved (the hook is merged in with jq). Idempotent.
#
# Usage:
#   ./install-global.sh
#   STACK_DATA_DIR=/path/to/stack-data ./install-global.sh   # pin the live source
#
# The global hooks defer to a repo's own phase-zero kit when you are inside one,
# so phase zero and the routing brief never print twice.

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"      # .../phase-zero/global
KITROOT="$(cd "$SRC/.." && pwd)"          # .../phase-zero
DEST="${CLAUDE_HOME:-$HOME/.claude}"

mkdir -p "$DEST/hooks"
cp "$SRC/phase-zero-trigger.global.sh" "$DEST/hooks/phase-zero-trigger.sh"
chmod +x "$DEST/hooks/phase-zero-trigger.sh"
cp "$KITROOT/phase-zero.md" "$DEST/phase-zero.md"   # guaranteed fallback core
cp "$KITROOT/retrospective.md" "$DEST/retrospective.md"   # retrospective prompt fallback
cp "$KITROOT/model-routing.md" "$DEST/model-routing.md"
cp "$KITROOT/operating-brief.md" "$DEST/operating-brief.md"
cp "$SRC/session-brief.global.sh" "$DEST/hooks/session-brief.sh"
chmod +x "$DEST/hooks/session-brief.sh"

settings="$DEST/settings.json"
hook_cmd='bash "$HOME/.claude/hooks/phase-zero-trigger.sh"'
session_cmd='bash "$HOME/.claude/hooks/session-brief.sh"'

if [ -f "$settings" ] && command -v jq >/dev/null 2>&1; then
  entry=$(jq -n --arg cmd "$hook_cmd" '{hooks:[{type:"command",command:$cmd}]}')
  session_entry=$(jq -n --arg cmd "$session_cmd" '{hooks:[{type:"command",command:$cmd}]}')
  jq --argjson entry "$entry" --argjson session_entry "$session_entry" '
    .hooks //= {} |
    .hooks.UserPromptSubmit //= [] |
    (if any(.hooks.UserPromptSubmit[]?; (.hooks[]?.command // "") | test("phase-zero-trigger"))
     then . else .hooks.UserPromptSubmit += [$entry] end) |
    .hooks.SessionStart //= [] |
    (if any(.hooks.SessionStart[]?; (.hooks[]?.command // "") | test("session-brief"))
     then . else .hooks.SessionStart += [$session_entry] end)
  ' "$settings" > "$settings.tmp" && mv "$settings.tmp" "$settings"
elif [ -f "$settings" ]; then
  # jq is missing and a settings.json exists: never clobber it. Fail loudly,
  # the same guard install.sh applies to a repo settings.json.
  echo "ERROR: $settings exists but jq is not installed; cannot merge." >&2
  echo "Install jq, or add these hooks to it by hand:" >&2
  echo "  UserPromptSubmit: $hook_cmd" >&2
  echo "  SessionStart:     $session_cmd" >&2
  exit 1
else
  cat > "$settings" <<'JSON'
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/phase-zero-trigger.sh\"" } ] }
    ],
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/session-brief.sh\"" } ] }
    ]
  }
}
JSON
fi

# Merge the auto-mode classifier config from auto-mode.json. The classifier
# reads autoMode only from user-level settings (never project .claude/), so
# this is its one deploy path. Same convention as every other kit-deployed
# file (see repo CLAUDE.md): kit source is truth, a redeploy overwrites each
# section the kit provides wholesale, so an edited or removed kit entry never
# lingers as a stale duplicate. Do not hand-edit the deployed autoMode block;
# edit global/auto-mode.json and re-run this script. Sections the kit file
# omits (allow) are left untouched, keeping the built-in defaults.
if command -v jq >/dev/null 2>&1; then
  jq --slurpfile kit "$SRC/auto-mode.json" '
    .autoMode //= {} |
    reduce ("environment","allow","soft_deny","hard_deny") as $k (.;
      ($kit[0].autoMode[$k] // []) as $kitlist |
      if ($kitlist | length) > 0 then .autoMode[$k] = $kitlist else . end)
  ' "$settings" > "$settings.tmp" && mv "$settings.tmp" "$settings"
else
  echo "ERROR: jq is not installed; autoMode block not merged into $settings" >&2
  echo "Install jq and re-run, or merge global/auto-mode.json by hand." >&2
  exit 1
fi

echo "phase-zero global hook installed -> $DEST"
if [ -z "${STACK_DATA_DIR:-}" ] && [ ! -d "$HOME/stack-data" ]; then
  echo "note: no stack-data clone found at ~/stack-data. The hook will use the"
  echo "portable core at $DEST/phase-zero.md. For the live renderer, set"
  echo "STACK_DATA_DIR to your stack-data clone in your shell profile."
fi
