#!/usr/bin/env bash
# phase-zero-trigger — UserPromptSubmit hook (portable across every repo).
#
# Reads the prompt-submit event JSON on stdin. If the prompt contains a
# phase-zero trigger phrase, it prints global awareness on stdout, which the
# harness adds to the model's context before the turn runs. Any other prompt
# passes through untouched (no output, exit 0) and spawns no process: a
# case-insensitive substring gate on the raw event runs first, and only an
# event that carries a phrase somewhere is parsed. Unparseable input also
# passes through with exit 0: a context hook must never block or alarm.
#
# Triggers (case-insensitive; this case pattern is the canonical set):
#   "activate all agents" | "engage global awareness" | "refresh global awareness"
#   | "delegate to your orchestrator" | "engage the orchestrator" | "engage your orchestrator"
# Retro triggers (the bookend): "log learnings" | "retro this chat" | "session retrospective"
#
# Repeat triggers (2026-09-17). The first trigger in a session prints the full
# map. A later trigger in the same session prints the short form, the live
# sections plus the gear ladder, the delegation protocol, and the merge
# boundary, because the map is already in context: 33 of the 85 sessions that
# ever typed "activate all agents" typed it twice or more, and each repeat
# re-sent the whole map. "refresh global awareness" always prints in full; it
# is the reload phrase, and the one to use after a context compaction. The
# per-session marker lives under $TMPDIR, keyed by the event's session_id, and
# is written only when a full map actually printed; the SessionStart hook
# removes it, so a started, resumed, cleared, or compacted session begins full
# again. No session_id in the event means full every time.
#
# Source of truth: stack-data/PHASE-ZERO.md. This kit is versioned in
# rubinstein-productions-toolkit/phase-zero/ and installed into each repo's
# .claude/ so every clone session shares the same infrastructure. It resolves
# the richest awareness available in the current repo, in order:
#   1. scripts/phase-zero   (stack-data: full hierarchy + live state)
#   2. PHASE-ZERO.md        (stack-data: full hierarchy)
#   3. .claude/phase-zero.md (the portable core carried into every repo)

set -euo pipefail

input=$(cat)


# The cheap gate. Almost every prompt is not a trigger; those leave here with
# zero subprocesses. nocasematch makes the case patterns case-insensitive in
# bash 3.2 and up.
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
# trigger phrase silently stops working for the rest of the session: the hook
# exits 0, prints nothing, and looks exactly like a prompt that was not a
# trigger. Say so once instead. The gate above means this only ever fires on
# a prompt that really did carry a phrase.
if ! command -v jq >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
  echo "[phase zero: neither jq nor python3 is on PATH, so this hook cannot read the prompt."
  echo "Global awareness is NOT loading in this session. Install either one.]"
  exit 0
fi

prompt=$(pz_field "$input" prompt | tr '[:upper:]' '[:lower:]')
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)}"

# Prints the richest map available. Returns 0 only when a map printed; the
# installer hint is not a map, and a session that saw only the hint must get
# the full attempt again next time, not the short form.
# Each tier's output is captured before any of it is printed. Printing as we
# go meant a renderer that died halfway left its partial map in the context
# window AND fell through to the next tier, so the reader got a truncated map
# followed by a whole second one, and the session was still marked as having
# seen a clean full map.
emit_full() {
  local out
  if [ -x "$root/scripts/phase-zero" ]; then
    if out="$(bash "$root/scripts/phase-zero" 2>/dev/null)" && [ -n "$out" ]; then
      printf '%s\n' "$out"; return 0
    fi
  fi
  if [ -f "$root/PHASE-ZERO.md" ]; then
    cat "$root/PHASE-ZERO.md" && return 0
  fi
  if [ -f "$root/.claude/phase-zero.md" ]; then
    cat "$root/.claude/phase-zero.md" && return 0
  fi
  echo "(portable core missing in this repo; run the kit installer: rubinstein-productions-toolkit/phase-zero/install.sh)"
  return 1
}

emit_short() {
  if [ -x "$root/scripts/phase-zero" ]; then
    bash "$root/scripts/phase-zero" --short 2>/dev/null && return 0
  fi
  echo '[phase zero: short form. The full map loaded earlier this session; say "refresh global awareness" to reload it.]'
  echo
  local src=""
  [ -f "$root/PHASE-ZERO.md" ] && src="$root/PHASE-ZERO.md"
  [ -z "$src" ] && [ -f "$root/.claude/phase-zero.md" ] && src="$root/.claude/phase-zero.md"
  [ -n "$src" ] || return 0
  pz_section "$src" "Gear and blast radius"
  pz_section "$src" "Delegation protocol"
  pz_section "$src" "The merge boundary"
  return 0
}

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
