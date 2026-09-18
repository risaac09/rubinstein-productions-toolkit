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
#   KIT defaults to the phase-zero/ directory beside this script. --dry-run
#   prints each consumer's clone, branch, dirty count, and visibility.
# Needs: gh (authenticated), git, and clones of the consumers under $HOME.
set -uo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
KIT="${KIT:-$(cd "$(dirname "$0")" && pwd)}"
KITREPO="$(cd "$KIT/.." && pwd)"
CONSUMERS="second-brain-mirror rp-shared rubinsteinproductions rp-intranet alchemy material-and-meaning-institute scripts gene-keys-data three-type-evaluation statehouse-dashboard isaacrubinstein.com three-bits"
PUBLIC="alchemy statehouse-dashboard gene-keys-data rubinsteinproductions three-type-evaluation"
is_public() { case " $PUBLIC " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
for name in $CONSUMERS; do
  clone="$HOME/$name"
  if [ ! -d "$clone/.git" ] && [ ! -f "$clone/.git" ]; then echo "SKIP $name: no clone at $clone"; continue; fi
  url="$(git -C "$clone" remote get-url origin 2>/dev/null || echo '?')"
  branch="$(git -C "$clone" symbolic-ref --short -q HEAD 2>/dev/null || echo detached)"
  dirty="$(git -C "$clone" status --porcelain -uno 2>/dev/null | wc -l | tr -d ' ')"
  kind=internal; is_public "$name" && kind=public
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

Deployed from rubinstein-productions-toolkit/phase-zero at $(git -C "$KITREPO" rev-parse --short HEAD) (branch $(git -C "$KITREPO" rev-parse --abbrev-ref HEAD)). Never edit these copies in place.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" || { echo "  FAIL commit $name"; git -C "$clone" worktree remove --force "$wt"; continue; }
  if git -C "$wt" push -q -u origin "$br"; then
      gh pr create -R "$(printf '%s' "$url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')" --base main --head "$br" --title "phase-zero kit redeploy ($(date +%Y-%m-%d))" --body "Kit redeploy from rubinstein-productions-toolkit/phase-zero at $(git -C "$KITREPO" rev-parse --short HEAD). Merge the toolkit change that carries the kit source first; if it changes, re-run phase-zero/redeploy-prs.sh and this PR regenerates. Deployed copies, never edited in place: repeat triggers print the short form, the gear ladder rides in the portable core, and the four hooks share one sourced lib. Deployed copies, never edited in place.

🤖 Generated with [Claude Code](https://claude.com/claude-code)" 2>&1 | tail -1
  else echo "  FAIL push branch $name"; fi
  git -C "$clone" worktree remove --force "$wt"
done
