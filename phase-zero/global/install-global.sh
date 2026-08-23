#!/usr/bin/env bash
# install-global.sh — install the phase-zero hooks at the user level (~/.claude).
#
# Run once per machine. After this, the trigger phrases load global awareness in
# every Claude Code session on the machine, including outside any repo, and the
# 05_RELATIONS privacy guard is live on Read/Grep/Glob. Existing
# ~/.claude/settings.json is preserved (hooks are merged in with jq). Idempotent.
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
cp "$SRC/relations-guard.global.sh" "$DEST/hooks/relations-guard.sh"
chmod +x "$DEST/hooks/relations-guard.sh"

settings="$DEST/settings.json"
hook_cmd='bash "$HOME/.claude/hooks/phase-zero-trigger.sh"'
session_cmd='bash "$HOME/.claude/hooks/session-brief.sh"'
relations_cmd='bash "$HOME/.claude/hooks/relations-guard.sh"'

if [ -f "$settings" ] && command -v jq >/dev/null 2>&1; then
  entry=$(jq -n --arg cmd "$hook_cmd" '{hooks:[{type:"command",command:$cmd}]}')
  session_entry=$(jq -n --arg cmd "$session_cmd" '{hooks:[{type:"command",command:$cmd}]}')
  relations_entry=$(jq -n --arg cmd "$relations_cmd" '{matcher:"Read|Grep|Glob",hooks:[{type:"command",command:$cmd}]}')
  jq --argjson entry "$entry" --argjson session_entry "$session_entry" --argjson relations_entry "$relations_entry" '
    .hooks //= {} |
    .hooks.UserPromptSubmit //= [] |
    (if any(.hooks.UserPromptSubmit[]?; (.hooks[]?.command // "") | test("phase-zero-trigger"))
     then . else .hooks.UserPromptSubmit += [$entry] end) |
    .hooks.SessionStart //= [] |
    (if any(.hooks.SessionStart[]?; (.hooks[]?.command // "") | test("session-brief"))
     then . else .hooks.SessionStart += [$session_entry] end) |
    .hooks.PreToolUse //= [] |
    (if any(.hooks.PreToolUse[]?; (.hooks[]?.command // "") | test("relations-guard"))
     then . else .hooks.PreToolUse += [$relations_entry] end)
  ' "$settings" > "$settings.tmp" && mv "$settings.tmp" "$settings"
elif [ -f "$settings" ]; then
  # jq is missing and a settings.json exists: never clobber it. Fail loudly,
  # the same guard install.sh applies to a repo settings.json.
  echo "ERROR: $settings exists but jq is not installed; cannot merge." >&2
  echo "Install jq, or add these hooks to it by hand:" >&2
  echo "  UserPromptSubmit: $hook_cmd" >&2
  echo "  SessionStart:     $session_cmd" >&2
  echo "  PreToolUse (Read|Grep|Glob): $relations_cmd" >&2
  exit 1
elif command -v jq >/dev/null 2>&1; then
  jq -n --arg hook_cmd "$hook_cmd" --arg session_cmd "$session_cmd" --arg relations_cmd "$relations_cmd" '
    {
      hooks: {
        UserPromptSubmit: [ { hooks: [ { type: "command", command: $hook_cmd } ] } ],
        SessionStart:     [ { hooks: [ { type: "command", command: $session_cmd } ] } ],
        PreToolUse:       [ { matcher: "Read|Grep|Glob", hooks: [ { type: "command", command: $relations_cmd } ] } ]
      }
    }
  ' > "$settings"
else
  # No settings.json and no jq: nothing to merge into, write nothing rather
  # than hand-quote JSON (a hand-quoted heredoc broke on $hook_cmd's own
  # embedded double quotes once already; jq is the only safe path here).
  echo "ERROR: no $settings and jq is not installed; cannot write it safely." >&2
  echo "Install jq, or create $settings by hand with these hooks:" >&2
  echo "  UserPromptSubmit: $hook_cmd" >&2
  echo "  SessionStart:     $session_cmd" >&2
  echo "  PreToolUse (Read|Grep|Glob): $relations_cmd" >&2
  exit 1
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

  # Machine-local overlay, merged last so it wins. This is where the specifics
  # go: which repos are actually private, internal hostnames and services,
  # local paths. They are deliberately absent from the kit file, because this
  # repo is public and that inventory is a map of what is worth taking. Same
  # section-wholesale semantics as the kit merge above.
  OVERLAY="${AUTO_MODE_OVERLAY:-$HOME/.claude/auto-mode.local.json}"
  if [ -f "$OVERLAY" ]; then
    # Fail loudly. A malformed overlay that silently did not merge would leave
    # the generic kit environment in place while the operator believed their
    # machine-specific rules were loaded, which is the one outcome this overlay
    # exists to prevent.
    if jq --slurpfile local "$OVERLAY" '
      .autoMode //= {} |
      reduce ("environment","allow","soft_deny","hard_deny") as $k (.;
        ($local[0].autoMode[$k] // []) as $locallist |
        if ($locallist | length) > 0 then .autoMode[$k] = $locallist else . end)
    ' "$settings" > "$settings.tmp" && mv "$settings.tmp" "$settings"; then
      echo "auto-mode: merged machine-local overlay from $OVERLAY"
    else
      rm -f "$settings.tmp"
      echo "ERROR: overlay at $OVERLAY did not merge (invalid JSON?)." >&2
      echo "  autoMode is left on the generic kit environment. Fix and re-run." >&2
      exit 1
    fi
  else
    echo "auto-mode: no machine-local overlay at $OVERLAY (using the generic kit environment)"
  fi
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
