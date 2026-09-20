#!/usr/bin/env bash
# session-brief.global — user-level SessionStart fallback.
#
# Home-directory Claude sessions account for most measured CLI work. Repository
# sessions receive the canonical routing brief from their deployed phase-zero
# kit. This hook covers sessions opened from ~/ or another directory without
# that kit and defers when a project-level SessionStart hook exists.

set -euo pipefail

# Read the event JSON for one field, session_id. The phase-zero trigger hook
# keeps a per-session marker under $TMPDIR so a repeat trigger prints the
# short form; a start, resume, clear, or compaction removes it here, so the
# next trigger prints the full map again. Injection on resume and clear is
# deliberate: the routing block should survive context resets.
input="$(cat 2>/dev/null || true)"
# The shared helpers live beside this hook; without them the brief still
# prints, it just cannot clear the marker.
pz_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
pz_lib="$pz_dir/phase-zero-lib.sh"
if [ -f "$pz_lib" ] && bash -n "$pz_lib" 2>/dev/null; then
  . "$pz_lib" || true
  if command -v pz_marker >/dev/null 2>&1 && command -v pz_field >/dev/null 2>&1; then
    # Same scope the global trigger uses: both hooks sit in ~/.claude/hooks,
    # so they agree without being told, and the global marker no longer
    # collides with a per-repo install in the same login session.
    pz_seen="$(pz_marker "$(pz_field "$input" session_id)" "$pz_dir")"
    if [ -n "$pz_seen" ]; then rm -f "$pz_seen" 2>/dev/null || true; fi
  fi
fi

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
