#!/usr/bin/env bash
# session-brief — SessionStart hook (portable across every repo).
#
# Fires when a session starts, resumes, or clears. Whatever it prints on
# stdout is added to the model's context before the first turn, so this is
# the injection point for the standing context every session should carry:
#
#   1. The model routing check (.claude/model-routing.md), so routing stops
#      living as hand-copied CLAUDE.md blocks that drift apart.
#   2. A pointer to the operating brief (.claude/operating-brief.md), named
#      without a model version so a handoff edits the file, not every repo.
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

# Shallow-clone tripwire. Printed before the routing block and before the
# kit-payload check, because it stays true whether or not the payload is here.
#
# Cloud sandboxes clone shallow, and a graft boundary above the fork point
# makes git lie QUIETLY: merge-base returns a plausible wrong SHA, divergence
# counts invent commits on both sides, and `git diff base..head` shows phantom
# reversions. Nothing errors, nothing warns, so the wrong answer reads as the
# real state of the branch. Three mergeable PRs (stack-data #101, #76, #90)
# were closed and hand-replayed on 2026-07-27 on exactly that false verdict,
# and the prose rule in CLAUDE.md did not stop a recurrence on 2026-08-14.
#
# Local operation, no network, so this costs nothing. Remediation stays manual
# on purpose: an LFS-backed mirror can carry a very expensive history, and
# unshallowing it at session start would be the wrong default.
shallow=$(git -C "$root" rev-parse --is-shallow-repository 2>/dev/null || echo false)
if [ "$shallow" = "true" ]; then
  gitdir=$(git -C "$root" rev-parse --absolute-git-dir 2>/dev/null || echo "")
  grafts=""
  if [ -n "$gitdir" ] && [ -f "$gitdir/shallow" ]; then
    grafts=$(wc -l < "$gitdir/shallow" 2>/dev/null | tr -d ' ')
  fi
  echo "[SHALLOW CLONE${grafts:+, $grafts graft points}] git ancestry here is UNRELIABLE."
  echo "merge-base, divergence counts, and 'git diff base..head' return wrong"
  echo "answers SILENTLY: no error, no warning, just a plausible wrong result."
  echo "Before acting on any merge, divergence, or 'unrelated histories' verdict:"
  echo "  git fetch --unshallow origin   # then re-check git merge-base"
  echo "Treat 'unrelated histories' as a clone property until proven otherwise."
  echo
fi

routing="$root/.claude/model-routing.md"
[ -f "$routing" ] || exit 0

echo "[session brief: model routing and standing context]"
echo
cat "$routing"
echo

if [ -f "$root/.claude/operating-brief.md" ]; then
  echo "Operating brief: .claude/operating-brief.md. Four slips the record"
  echo "warrants, each with a tripwire. Read it before work that spans more"
  echo "than one repo or more than one session; skip it for a single-file fix."
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
