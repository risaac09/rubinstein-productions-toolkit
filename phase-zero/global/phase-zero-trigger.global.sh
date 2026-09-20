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

# The shared helpers (pz_field, pz_section, pz_marker) live beside this hook
# and load only past the gate, so a non-trigger prompt still spawns nothing.
# A kit missing them, or a lib caught mid-copy, is half installed: fail
# closed, print nothing, exit 0, never block the prompt.
pz_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
pz_lib="$pz_dir/phase-zero-lib.sh"
[ -f "$pz_lib" ] || exit 0
bash -n "$pz_lib" 2>/dev/null || exit 0
. "$pz_lib" || exit 0
command -v pz_field >/dev/null 2>&1 || exit 0

# Past the gate with no JSON parser, the prompt reads as empty and every
# trigger phrase silently stops working for the rest of the session. Say so
# once instead of going dark.
if ! command -v jq >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  echo "[phase zero: neither jq nor python3 is on PATH, so this hook cannot read the prompt."
  echo "Global awareness is NOT loading in this session. Install either one.]"
  exit 0
fi

# Prints the richest map available. Returns 0 only when a map printed.
# Each tier is captured before it is printed: a renderer that died halfway
# used to leave its partial map in the context window and then fall through
# to the next tier, so the reader got a truncated map followed by a whole
# second one, and the session was still marked as having seen a clean map.
# pz_current <dir>: true when that checkout already contains origin/main, so
# its copy of the renderer is not behind. The renderer itself now reads the
# map from origin/main, but the renderer is a file in a working tree, and a
# tree parked on a feature branch hands us the version of the script that
# predates that fix. Preferring a current checkout is what makes the fix
# reach a machine whose main clone is usually mid-branch, which is the normal
# state of this one.
pz_current() {
  git -C "$1" rev-parse --verify --quiet origin/main >/dev/null 2>&1 || return 1
  [ "$(git -C "$1" rev-list --count HEAD..origin/main 2>/dev/null || echo 1)" = "0" ]
}

emit_full() {
  local cand out
  # Tier 1: a checkout that already contains origin/main. Its renderer is
  # current, so this is the only tier that gives the canonical map AND the
  # live state together.
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] && [ -d "$cand" ] || continue
    pz_current "$cand" || continue
    if [ -x "$cand/scripts/phase-zero" ]; then
      if out="$(bash "$cand/scripts/phase-zero" 2>/dev/null)" && [ -n "$out" ]; then
        printf '%s\n' "$out"; return 0
      fi
    fi
  done
  # Tier 2: no current checkout, so read the map out of origin/main directly
  # rather than running a stale renderer. The renderer now pins the map to
  # origin/main, but the renderer is itself a file in a working tree, and a
  # tree parked on a feature branch hands back the version of the script from
  # before that fix, which is exactly the 2026-09-19 bug. A correct map with
  # no live state beats a stale map with live state: the stale map is what
  # actually misled a session, twice in one day.
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] && [ -d "$cand" ] || continue
    if out="$(git -C "$cand" show origin/main:PHASE-ZERO.md 2>/dev/null)" && [ -n "$out" ]; then
      printf '%s\n' "$out"
      echo
      echo "[phase zero: read from origin/main because no stack-data checkout is"
      echo "up to date, so the live sections (queues, estate, scheduled work) are"
      echo "not in this render. Bring a checkout to main to get them back.]"
      return 0
    fi
  done
  # Tier 3: no git answer at all. A stale renderer or a static file, in that
  # order, is still better than no map.
  for cand in "${STACK_DATA_DIR:-}" "$HOME/stack-data" "$HOME/code/stack-data" "$HOME/src/stack-data"; do
    [ -n "$cand" ] && [ -d "$cand" ] || continue
    if [ -x "$cand/scripts/phase-zero" ]; then
      if out="$(bash "$cand/scripts/phase-zero" 2>/dev/null)" && [ -n "$out" ]; then
        printf '%s\n' "$out"; return 0
      fi
    fi
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
  pz_section "$src" "Gear and blast radius"
  pz_section "$src" "Delegation protocol"
  pz_section "$src" "The merge boundary"
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

prompt=$(pz_field "$input" prompt | tr '[:upper:]' '[:lower:]')

mode=""
case "$prompt" in
  *"refresh global awareness"*) mode=full ;;
  *"activate all agents"*|*"engage global awareness"*|*"delegate to your orchestrator"*|*"engage the orchestrator"*|*"engage your orchestrator"*) mode=pending ;;
esac

if [ -n "$mode" ]; then
  marker="$(pz_marker "$(pz_field "$input" session_id)" "$pz_dir")"
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
