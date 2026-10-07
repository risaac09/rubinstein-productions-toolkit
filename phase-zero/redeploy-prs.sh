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
# Usage: KIT=<kit dir> phase-zero/redeploy-prs.sh [--public] [--dry-run]
#   KIT defaults to the phase-zero/ directory beside this script. The roster
#   is `install.sh --list` from that KIT. --dry-run prints each consumer's
#   clone, branch, dirty count, and visibility.
#   --public deploys the public-kit instead (install.sh --public), roster
#   from `install.sh --public --list`, which reads the stack-data registry
#   and exits 2 without it. Branch chore/public-kit-<date>. The kit lands
#   under .claude/public-kit/, which a global gitignore of .claude/ hides, so
#   it is staged with git add -f; an unstaged kit is the drift install.sh
#   --check now reports as DRIFT uncommitted.
#   PZ_COAUTHOR="Claude <model> <noreply@anthropic.com>" adds a Co-Authored-By
#   trailer naming the model running the redeploy. Unset, the commit credits
#   no model: the script writes no content of its own.
# Visibility is read live from GitHub, not kept in a list here, because a
# hand list drifted (2026-09-29: isaacrubinstein.com public, listed internal).
# It is a label only. Who merges which PR follows stack-data DECISIONS, "The
# merge boundary", which gates three-type-evaluation although it is private.
# Needs: gh (authenticated), git, and clones of the consumers under $HOME.
set -uo pipefail
DRY=0; MODE=phase-zero
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --public) MODE=public ;;
    *) echo "usage: redeploy-prs.sh [--public] [--dry-run]" >&2; exit 2 ;;
  esac
done
KIT="${KIT:-$(cd "$(dirname "$0")" && pwd)}"
KITREPO="$(cd "$KIT/.." && pwd)"
# Per-mode settings. PUBFLAG is empty for phase-zero, and ${PUBFLAG:+...}
# then adds no argument to the install.sh calls below (an empty array would
# trip set -u on the macOS system bash 3.2).
if [ "$MODE" = public ]; then
  PUBFLAG="--public"; KITDIR=".claude/public-kit"; BRPREFIX="chore/public-kit"
  KITNAME="public-kit"; KITSRC="public-kit"
else
  PUBFLAG=""; KITDIR=".claude"; BRPREFIX="chore/phase-zero-kit"
  KITNAME="phase-zero kit"; KITSRC="phase-zero"
fi
# The roster comes from install.sh, the list of record, never a copy here: a
# hand copy left out stack-data and the 2026-09-29 redeploy skipped it. The
# toolkit itself is dropped, because its own .claude/ ships in the same PR as
# the kit source change. Named literally: KITREPO is often a worktree, so its
# basename is the worktree's name, not the repo's.
roster="$(bash "$KIT/install.sh" ${PUBFLAG:+"$PUBFLAG"} --list)" || { echo "FAIL: install.sh ${PUBFLAG:+$PUBFLAG }--list failed; roster unavailable" >&2; exit 1; }
CONSUMERS="$(printf '%s\n' "$roster" | grep -vx 'rubinstein-productions-toolkit')"
if [ -z "$CONSUMERS" ]; then echo "FAIL: install.sh ${PUBFLAG:+$PUBFLAG }--list returned no consumers" >&2; exit 1; fi
trailer=""
if [ -n "${PZ_COAUTHOR:-}" ]; then trailer="

Co-Authored-By: $PZ_COAUTHOR"; fi
drop_wt() { git -C "$clone" worktree remove --force "$wt"; rmdir "$wtparent" 2>/dev/null || true; }
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
  # The worktree's basename is the repo's name: install.sh picks the public
  # kit's LICENSE template by basename, and a mktemp name matched nothing.
  wtparent="$(mktemp -d "/tmp/kit-$name.XXXX")"; wt="$wtparent/$name"
  br="$BRPREFIX-$(date +%Y-%m-%d)"
  if git -C "$clone" show-ref --verify -q "refs/heads/$br"; then git -C "$clone" branch -D "$br" >/dev/null; fi
  git -C "$clone" worktree add -q -b "$br" "$wt" origin/main || { echo "  FAIL worktree $name"; rmdir "$wtparent"; continue; }
  bash "$KIT/install.sh" ${PUBFLAG:+"$PUBFLAG"} "$wt" >/dev/null || { echo "  FAIL install $name"; drop_wt; continue; }
  # The committed-state compare is skipped here: this worktree is checked
  # before its commit, so origin/HEAD cannot carry the new copies yet.
  if ! KIT_COMMITTED_CHECK=skip bash "$KIT/install.sh" ${PUBFLAG:+"$PUBFLAG"} --check "$wt" >/dev/null; then echo "  FAIL check $name"; drop_wt; continue; fi
  git -C "$wt" add -f "$KITDIR" >/dev/null
  if git -C "$wt" diff --cached --quiet; then
    echo "  current: nothing to deploy"; drop_wt; git -C "$clone" branch -D "$br" >/dev/null 2>&1; continue
  fi
  git -C "$wt" -c commit.gpgsign=false commit -q -m "chore($KITSRC): redeploy the $KITNAME

Deployed from rubinstein-productions-toolkit/$KITSRC at $(git -C "$KITREPO" rev-parse --short HEAD) (branch $(git -C "$KITREPO" rev-parse --abbrev-ref HEAD)). Never edit these copies in place.$trailer" || { echo "  FAIL commit $name"; drop_wt; continue; }
  pushed=0
  if git -C "$wt" push -q -u origin "$br"; then
      pushed=1
      gh pr create -R "$slug" --base main --head "$br" --title "$KITNAME redeploy ($(date +%Y-%m-%d))" --body "Kit redeploy from rubinstein-productions-toolkit/$KITSRC at $(git -C "$KITREPO" rev-parse --short HEAD). Merge the toolkit change that carries the kit source first; if it changes, re-run phase-zero/redeploy-prs.sh ${PUBFLAG:+$PUBFLAG }and this PR regenerates. These are deployed copies; never edit them in place.

🤖 Generated with [Claude Code](https://claude.com/claude-code)" 2>&1 | tail -1
  else echo "  FAIL push branch $name"; fi
  drop_wt
  # The pushed branch lives on the remote; drop the local ref so dated
  # branches don't pile up in the operator's clone. A failed push keeps it,
  # since the commit exists nowhere else.
  if [ "$pushed" -eq 1 ]; then git -C "$clone" branch -D "$br" >/dev/null 2>&1; fi
done
