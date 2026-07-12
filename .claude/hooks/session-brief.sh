#!/usr/bin/env bash
# session-brief — SessionStart hook (portable across every repo).
#
# Fires when a session starts, resumes, or clears. Whatever it prints on
# stdout is added to the model's context before the first turn, so this is
# the injection point for the standing context every session should carry:
#
#   1. The model routing check (.claude/model-routing.md), so routing stops
#      living as hand-copied CLAUDE.md blocks that drift apart.
#   2. A pointer to the operating brief for the standing model
#      (.claude/opus-4-8-brief.md).
#   3. Pointers to the decisions of record and the failure catalog in
#      stack-data, when a clone is reachable (this repo, or a sibling).
#
# Silent when the kit payload is absent, so a repo without the kit loses
# nothing. Exit 0 always; a context hook must never block a session.
#
# Source of truth: rubinstein-productions-toolkit/phase-zero/. Deployed
# copies are overwritten on every install; edit the source and redeploy.

set -euo pipefail

# Consume the event JSON; its fields are not needed. Injection on resume and
# clear is deliberate: the routing block should survive context resets.
cat >/dev/null 2>&1 || true

root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

routing="$root/.claude/model-routing.md"
[ -f "$routing" ] || exit 0

echo "[session brief: model routing and standing context]"
echo
cat "$routing"
echo

if [ -f "$root/.claude/opus-4-8-brief.md" ]; then
  echo "Operating brief for the standing model: .claude/opus-4-8-brief.md."
  echo "Read it before the first substantive task of this session."
fi

# Standing records: local when this is stack-data, sibling clone otherwise.
# Paths print repo-relative so the injected context stays readable.
records=""
if [ -f "$root/docs/DECISIONS.md" ]; then
  records="docs"
elif [ -f "$root/../stack-data/docs/DECISIONS.md" ]; then
  records="../stack-data/docs"
fi
if [ -n "$records" ]; then
  echo "Decisions of record: $records/DECISIONS.md. Cite a settled call instead of re-deriving it."
  if [ -f "$root/$records/FAILURE-MODES.md" ]; then
    echo "Failure catalog: $records/FAILURE-MODES.md."
  fi
fi

exit 0
