#!/usr/bin/env bash
# install.sh — install the phase-zero kit into a target repo's .claude/.
#
# Drops the portable phase-zero core, the model-routing check, the operating
# brief for the standing model, both hooks (UserPromptSubmit trigger,
# SessionStart brief), and the hook registrations into <target-repo>/.claude/.
# If the target already has a settings.json, the hooks are merged in (jq)
# rather than overwriting existing config. Idempotent: re-running refreshes
# the kit.
#
# Usage:
#   ./install.sh <target-repo-dir>          install into one repo
#   ./install.sh --all <parent-dir>         install into every git repo one level down
#   ./install.sh --check <target-repo-dir>  verify one deployed kit, no writes
#   ./install.sh --check --all <parent-dir> verify every deployed kit, no writes
#
# --check byte-compares the six kit files against source and confirms both
# hook registrations exist in settings.json. It writes nothing and exits 1
# on any drift. This is the kit-drift tripwire from the 2026-07-17 audit:
# a stale deployed copy (third-information-lab missed the merge-boundary
# section for a day) is invisible until something reads it.
#
# This is the rp-shared-style distribution path: the kit is versioned here in
# the toolkit, and synced out, so every repo runs the same infrastructure.

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

KIT_FILES="phase-zero.md retrospective.md model-routing.md opus-4-8-brief.md hooks/phase-zero-trigger.sh hooks/session-brief.sh"

check_one() {
  local target="$1" drift=0
  [ -d "$target" ] || { echo "skip (not a dir): $target"; return 0; }
  if [ ! -d "$target/.claude" ]; then
    echo "no kit: $target"
    return 0
  fi
  for f in $KIT_FILES; do
    if [ ! -f "$target/.claude/$f" ]; then
      echo "DRIFT missing $f: $target"
      drift=1
    elif ! cmp -s "$SRC/$f" "$target/.claude/$f"; then
      echo "DRIFT stale $f: $target"
      drift=1
    fi
  done
  local settings="$target/.claude/settings.json"
  if [ ! -f "$settings" ]; then
    echo "DRIFT missing settings.json: $target"
    drift=1
  else
    grep -q "phase-zero-trigger" "$settings" || { echo "DRIFT hook unregistered (phase-zero-trigger): $target"; drift=1; }
    grep -q "session-brief" "$settings" || { echo "DRIFT hook unregistered (session-brief): $target"; drift=1; }
  fi
  [ "$drift" -eq 0 ] && echo "kit current: $target"
  return $drift
}

install_one() {
  local target="$1"
  [ -d "$target" ] || { echo "skip (not a dir): $target"; return 0; }
  mkdir -p "$target/.claude/hooks"
  cp "$SRC/phase-zero.md" "$target/.claude/phase-zero.md"
  cp "$SRC/retrospective.md" "$target/.claude/retrospective.md"
  cp "$SRC/model-routing.md" "$target/.claude/model-routing.md"
  cp "$SRC/opus-4-8-brief.md" "$target/.claude/opus-4-8-brief.md"
  cp "$SRC/hooks/phase-zero-trigger.sh" "$target/.claude/hooks/phase-zero-trigger.sh"
  cp "$SRC/hooks/session-brief.sh" "$target/.claude/hooks/session-brief.sh"
  chmod +x "$target/.claude/hooks/phase-zero-trigger.sh" "$target/.claude/hooks/session-brief.sh"

  local pz_cmd='bash "$CLAUDE_PROJECT_DIR/.claude/hooks/phase-zero-trigger.sh"'
  local sb_cmd='bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session-brief.sh"'
  local settings="$target/.claude/settings.json"
  if [ -f "$settings" ] && command -v jq >/dev/null 2>&1; then
    # Merge: keep existing config, add each kit hook if absent.
    jq --arg pz "$pz_cmd" --arg sb "$sb_cmd" '
      .hooks //= {} |
      .hooks.UserPromptSubmit //= [] |
      (if any(.hooks.UserPromptSubmit[]?; (.hooks[]?.command // "") | test("phase-zero-trigger"))
       then . else .hooks.UserPromptSubmit += [{hooks:[{type:"command",command:$pz}]}] end) |
      .hooks.SessionStart //= [] |
      (if any(.hooks.SessionStart[]?; (.hooks[]?.command // "") | test("session-brief"))
       then . else .hooks.SessionStart += [{hooks:[{type:"command",command:$sb}]}] end)
    ' "$settings" > "$settings.tmp" && mv "$settings.tmp" "$settings"
  elif [ -f "$settings" ]; then
    # jq is missing and a settings.json exists: never clobber it. Fail loudly.
    echo "ERROR: $settings exists but jq is not installed; cannot merge." >&2
    echo "Install jq, or add these hooks to it by hand:" >&2
    echo "  UserPromptSubmit: $pz_cmd" >&2
    echo "  SessionStart:     $sb_cmd" >&2
    return 1
  else
    cp "$SRC/settings.json" "$settings"
  fi
  echo "phase-zero installed -> $target/.claude"
}

if [ "${1:-}" = "--check" ]; then
  shift
  RC=0
  if [ "${1:-}" = "--all" ]; then
    parent="${2:?usage: install.sh --check --all <parent-dir>}"
    for d in "$parent"/*/; do
      if [ -d "$d/.git" ]; then
        check_one "${d%/}" || RC=1
      fi
    done
  else
    check_one "${1:?usage: install.sh --check <target-repo-dir>}" || RC=1
  fi
  exit $RC
elif [ "${1:-}" = "--all" ]; then
  parent="${2:?usage: install.sh --all <parent-dir>}"
  for d in "$parent"/*/; do
    [ -d "$d/.git" ] && install_one "${d%/}"
  done
else
  install_one "${1:?usage: install.sh <target-repo-dir>   (or --all <parent-dir>)}"
fi
