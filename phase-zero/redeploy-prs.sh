#!/usr/bin/env bash
# redeploy-prs.sh — deploy the phase-zero kit to every consumer as a pull
# request, one per repo, from temp worktrees, so no session's checkout is
# touched and nothing lands on a main without a click.
#
# install.sh --all writes into the consumers' working trees and commits
# nothing; that fits a machine where the operator commits by hand. This is the
# other path, written 2026-09-17 when the auto-mode classifier refused an agent
# merge as "merge without review": every consumer gets a branch
# (chore/phase-zero-kit-<date>) and a PR carrying the kit copies, public and
# private alike, and the operator merges them after the toolkit PR that holds
# the kit source. Re-run after the kit changes; a consumer already current
# prints "nothing to deploy".
#
# Usage: KIT=<kit dir> phase-zero/redeploy-prs.sh [--dry-run]
#   KIT defaults to the phase-zero/ directory beside this script. The roster
#   is `install.sh --list` from that KIT. --dry-run prints each consumer's
#   clone, branch, dirty count, and visibility.
#   PZ_COAUTHOR="Claude <model> <noreply@anthropic.com>" adds a Co-Authored-By
#   trailer naming the model running the redeploy. Unset, the commit credits
#   no model: the script writes no content of its own.
# Visibility is read live from GitHub, not kept in a list here, because a
# hand list drifted (2026-09-29: isaacrubinstein.com public, listed internal).
# It is a label only. Who merges which PR follows stack-data DECISIONS, "The
# merge boundary", which gates three-type-evaluation although it is private.
# Needs: gh (authenticated), git, and clones of the consumers under $HOME.
set -uo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
KIT="${KIT:-$(cd "$(dirname "$0")" && pwd)}"
KITREPO="$(cd "$KIT/.." && pwd)"
# The roster comes from install.sh, the list of record, never a copy here: a
# hand copy left out stack-data and the 2026-09-29 redeploy skipped it. The
# toolkit itself is dropped, because its own .claude/ ships in the same PR as
# the kit source change. Named literally: KITREPO is often a worktree, so its
# basename is the worktree's name, not the repo's.
CONSUMERS="$(bash "$KIT/install.sh" --list | grep -vx 'rubinstein-productions-toolkit')"
if [ -z "$CONSUMERS" ]; then echo "FAIL: install.sh --list returned no consumers" >&2; exit 1; fi
trailer=""
if [ -n "${PZ_COAUTHOR:-}" ]; then trailer="

Co-Authored-By: $PZ_COAUTHOR"; fi
for name in $CONSUMERS; do
  clone="$HOME/$name"
  if [ ! -d "$clone/.git" ] && [ ! -f "$clone/.git" ]; then echo "SKIP $name: no clone at $clone"; continue; fi
  url="$(git -C "$clone" remote get-url origin 2>/dev/null || echo '?')"
  slug="$(printf '%s' "$url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
  branch="$(git -C "$clone" symbolic-ref --short -q HEAD 2>/dev/null || echo detached)"
  dirty="$(git -C "$clone" status --porcelain -uno 2>/dev/null | wc -l | tr -d ' ')"
  kind="$(gh repo view "$slug" --json visibility -q '.visibility|ascii_downcase' 2>/dev/null)" || kind=""
  [ -n "$kind" ] || kind="?"
  printf '%-32s %-8s branch=%-40s dirty=%s remote=%s\n' "$name" "$kind" "$branch" "$dirty" "$url"
  [ "$DRY" -eq 1 ] && continue
  git -C "$clone" fetch -q origin main || { echo "  FAIL fetch $name"; continue; }
  wt="$(mktemp -d "/tmp/kit-$name.XXXX")"; rmdir "$wt"
  br="chore/phase-zero-kit-$(date +%Y-%m-%d)"
  if git -C "$clone" show-ref --verify -q "refs/heads/$br"; then git -C "$clone" branch -D "$br" >/dev/null; fi
  git -C "$clone" worktree add -q -b "$br" "$wt" origin/main || { echo "  FAIL worktree $name"; continue; }
  bash "$KIT/install.sh" "$wt" >/dev/null || { echo "  FAIL install $name"; git -C "$clone" worktree remove --force "$wt"; continue; }
  if ! bash "$KIT/install.sh" --check "$wt" >/dev/null; then echo "  FAIL check $name"; git -C "$clone" worktree remove --force "$wt"; continue; fi
  git -C "$wt" add -f .claude >/dev/null
  if git -C "$wt" diff --cached --quiet; then
    echo "  current: nothing to deploy"; git -C "$clone" worktree remove --force "$wt"; git -C "$clone" branch -D "$br" >/dev/null 2>&1; continue
  fi
  git -C "$wt" -c commit.gpgsign=false commit -q -m "chore(phase-zero): redeploy the kit

Deployed from rubinstein-productions-toolkit/phase-zero at $(git -C "$KITREPO" rev-parse --short HEAD) (branch $(git -C "$KITREPO" rev-parse --abbrev-ref HEAD)). Never edit these copies in place.$trailer" || { echo "  FAIL commit $name"; git -C "$clone" worktree remove --force "$wt"; continue; }
  pushed=0
  if git -C "$wt" push -q -u origin "$br"; then
      pushed=1
      gh pr create -R "$slug" --base main --head "$br" --title "phase-zero kit redeploy ($(date +%Y-%m-%d))" --body "Kit redeploy from rubinstein-productions-toolkit/phase-zero at $(git -C "$KITREPO" rev-parse --short HEAD). Merge the toolkit change that carries the kit source first; if it changes, re-run phase-zero/redeploy-prs.sh and this PR regenerates. These are deployed copies; never edit them in place.

🤖 Generated with [Claude Code](https://claude.com/claude-code)" 2>&1 | tail -1
  else echo "  FAIL push branch $name"; fi
  git -C "$clone" worktree remove --force "$wt"
  # The pushed branch lives on the remote; drop the local ref so dated
  # branches don't pile up in the operator's clone. A failed push keeps it,
  # since the commit exists nowhere else.
  if [ "$pushed" -eq 1 ]; then git -C "$clone" branch -D "$br" >/dev/null 2>&1; fi
done
