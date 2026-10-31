#!/usr/bin/env bash
# workspace-check.global — user-level SessionStart warning about where sessions start.
#
# The desktop app picks a session's folder before any hook runs, and a
# SessionStart hook cannot refuse a session, so this only warns: the lines it
# prints land in the session's context for the model to pass on. Silent when
# everything is in order. Always exits 0.
#
# Checks:
#   - the session folder is in an iCloud-synced place (Desktop, Documents,
#     Library/Mobile Documents), or is a worktree inside a worktree
#   - each local environment in the app's preset file points at a folder that
#     exists, outside iCloud, and not inside a .claude/worktrees/ folder
#   --repos also lists the git repos directly under $HOME with no environment
#   (left out of the SessionStart run: most homes hold many such repos).
#
# Limit: a session started in a worktree folder with Worktree off looks the
# same as an app-made worktree session, so only the nested case is caught.
#
# CCD_PRESETS overrides the preset file path (tests, other installs).

set -uo pipefail

input=""
[ -t 0 ] || input="$(cat 2>/dev/null || true)"
lib="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/phase-zero-lib.sh"
cwd=""
# shellcheck source=/dev/null
if [ -n "$input" ] && [ -f "$lib" ] && . "$lib" 2>/dev/null && command -v pz_field >/dev/null 2>&1; then
  cwd="$(pz_field "$input" cwd)"
fi
[ -n "$cwd" ] || cwd="$PWD"

icloud_re="^$HOME/(Desktop|Documents|Library/Mobile Documents)(/|$)"
nested_re="/\.claude/worktrees/[^/]+/.*\.claude/worktrees/"
warn=()

if printf '%s' "$cwd" | grep -Eq "$icloud_re"; then
  warn+=("session folder is iCloud-synced: $cwd")
fi
if printf '%s' "$cwd" | grep -Eq "$nested_re"; then
  warn+=("session is a worktree inside a worktree (started from a worktree folder): $cwd")
fi

presets="${CCD_PRESETS:-$HOME/Library/Application Support/Claude/ccd-session-presets.json}"
env_dirs=""
if [ -f "$presets" ] && command -v jq >/dev/null 2>&1; then
  env_dirs="$(jq -r '.byAccount[]?.presets[]? | select(.target.kind == "local") | "\(.name)\t\(.target.cwd)"' "$presets" 2>/dev/null || true)"
  while IFS=$'\t' read -r name dir; do
    [ -n "$dir" ] || continue
    if printf '%s' "$dir" | grep -Eq "$icloud_re"; then
      warn+=("environment '$name' points into iCloud: $dir")
    elif printf '%s' "$dir" | grep -q '/\.claude/worktrees/'; then
      warn+=("environment '$name' points at a worktree, not a repo root: $dir")
    elif [ ! -d "$dir" ]; then
      warn+=("environment '$name' points at a missing folder: $dir")
    fi
  done <<< "$env_dirs"
fi

if [ "${1:-}" = "--repos" ]; then
  missing=""
  for g in "$HOME"/*/.git; do
    [ -e "$g" ] || continue
    repo="${g%/.git}"
    printf '%s\n' "$env_dirs" | cut -f2 | grep -Fxq "$repo" || missing="$missing ${repo##*/}"
  done
  [ -z "$missing" ] || warn+=("repos with no environment:$missing")
fi

if [ "${#warn[@]}" -gt 0 ]; then
  echo "[workspace check] Tell the user, briefly, before other work:"
  printf '  - %s\n' "${warn[@]}"
  echo "  Fix: start sessions from a repo root with Worktree on; edit environments in the app's environment picker."
fi
exit 0
