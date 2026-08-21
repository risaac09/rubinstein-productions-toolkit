#!/usr/bin/env bash
# install.sh — install the phase-zero kit into a target repo's .claude/.
#
# Drops the portable phase-zero core, the model-routing check, the operating
# brief, both hooks (UserPromptSubmit trigger,
# SessionStart brief), and the hook registrations into <target-repo>/.claude/.
# If the target already has a settings.json, the hooks and the high-blast
# permission clamps (the deny/ask lists in the kit settings.json, mirroring
# ~/.claude/settings.json) are merged in (jq) rather than overwriting
# existing config. Idempotent: re-running refreshes the kit.
#
# Usage:
#   ./install.sh <target-repo-dir>          install into one repo
#   ./install.sh --all <parent-dir>         install into every listed consumer
#   ./install.sh --check <target-repo-dir>  verify one deployed kit, no writes
#   ./install.sh --check --all <parent-dir> verify every listed consumer, no writes
#
# --check byte-compares the six kit files against source and confirms both
# hook registrations plus every kit permission clamp exist in settings.json.
# settings.json itself is never byte-compared: merged consumer copies carry
# repo-local config and legitimately differ from source. It writes nothing
# and exits 1 on any drift. This is the kit-drift tripwire from the 2026-07-17 audit:
# a stale deployed copy (third-information-lab missed the merge-boundary
# section for a day) is invisible until something reads it.
#
# This is the rp-shared-style distribution path: the kit is versioned here in
# the toolkit, and synced out, so every repo runs the same infrastructure.
#
# A second, independent kit shares this script: public-kit/, for public-repo
# hygiene (license templates, README shape, CONTRIBUTING/SECURITY templates,
# public voice rules) rather than AI-agent session infrastructure. Prefix any
# of the above with --public to act on it instead:
#
#   ./install.sh --public <target-repo-dir>
#   ./install.sh --public --all <parent-dir>
#   ./install.sh --public --check <target-repo-dir>
#   ./install.sh --public --check --all <parent-dir>
#
# The two kits have separate allowlists (CONSUMERS vs PUBLIC_CONSUMERS),
# separate source directories, and separate target directories
# (.claude/*.md + .claude/hooks/ vs .claude/public-kit/) — never overlapping,
# and never implying anything about the other. A repo can be on neither list,
# either, or both.

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

KIT_FILES="phase-zero.md retrospective.md model-routing.md operating-brief.md hooks/phase-zero-trigger.sh hooks/session-brief.sh"

# Kit files that used to ship and no longer do. install_one removes them, since
# it otherwise only copies and a renamed file would leave its predecessor behind
# in every consuming repo. Add a line here whenever a kit file is renamed or
# dropped; drop the line once every clone has run an install past the change.
RETIRED_FILES="opus-4-8-brief.md"

# The current consumer roster. --all touches only these basenames; a direct
# install.sh <repo> still works for an intentional one-off. The local path
# `scripts` is the home-scripts repository. three-bits added 2026-08-21: its
# own CLAUDE.md claimed the kit was deployed there, but it carried none of
# the kit files and wasn't on this list either, so --check --all couldn't
# even see the gap. Added here so future drift is caught; a parallel session
# is deploying the actual kit files to three-bits directly.
CONSUMERS="stack-data second-brain-mirror rp-shared rubinsteinproductions rp-intranet alchemy material-and-meaning-institute scripts gene-keys-data three-type-evaluation statehouse-dashboard isaacrubinstein.com three-bits rubinstein-productions-toolkit"

# `case` rather than a loop with `&& return`: a failing test as the last command
# of a loop body would abort under `set -e` if this were ever called outside a
# conditional. This form returns cleanly from any context.
is_consumer() {
  case " $CONSUMERS " in
    *" $(basename "$1") "*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- public-kit: a second, independent kit (see the file header). Its own
# source dir, allowlist, target dir, and install/check functions; nothing
# below is read by the phase-zero path above, and vice versa.
PUBLIC_SRC="$(cd "$SRC/../public-kit" && pwd)"
PUBLIC_KIT_FILES="VOICE-RULES.md README-SHAPE.md CONTRIBUTING.md.template SECURITY.md.template"

# Repos that are actually public. Independent of CONSUMERS above -- being on
# one list implies nothing about the other. three-type-evaluation is here for
# its public paper side only; the rest of that repo stays private, untouched
# either way since this kit only ever writes under .claude/public-kit/.
PUBLIC_CONSUMERS="alchemy statehouse-dashboard gene-keys-data rubinsteinproductions risaac09 three-type-evaluation rubinstein-productions-toolkit"

is_public_consumer() {
  case " $PUBLIC_CONSUMERS " in
    *" $(basename "$1") "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Per-repo-type default for which LICENSE template a public-kit install
# picks: methodology-shaped repos (protocols, frameworks, practice-writing)
# get CC BY-SA 4.0, everything else defaults to MIT (code-shaped). A `case`,
# not a config file, so a new consumer's type is one line to add.
public_consumer_license_type() {
  case "$(basename "$1")" in
    three-type-evaluation) echo "methodology" ;;
    *) echo "code" ;;
  esac
}

public_license_template() {
  case "$(public_consumer_license_type "$1")" in
    methodology) echo "$PUBLIC_SRC/LICENSE-CC-BY-SA-4.0.template" ;;
    *) echo "$PUBLIC_SRC/LICENSE-MIT.template" ;;
  esac
}

check_one() {
  local target="$1" drift=0
  [ -d "$target" ] || { echo "skip (not a dir): $target"; return 0; }
  if [ ! -d "$target/.claude" ]; then
    echo "DRIFT missing kit directory: $target"
    return 1
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
  for f in $RETIRED_FILES; do
    if [ -f "$target/.claude/$f" ]; then
      echo "DRIFT retired file still present ($f): $target"
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
    # Clamp tripwire: every deny/ask rule in the kit settings.json must appear
    # in the consumer settings.json (a rule promoted from ask to deny locally
    # still matches; that is a stricter posture, not drift). Needs jq to read
    # the kit list; without jq the clamp check is skipped, like the merge.
    if command -v jq >/dev/null 2>&1; then
      for rule in $(jq -r '((.permissions.deny // []) + (.permissions.ask // []))[]' "$SRC/settings.json"); do
        grep -qF "\"$rule\"" "$settings" || { echo "DRIFT clamp unregistered ($rule): $target"; drift=1; }
      done
    fi
  fi
  [ "$drift" -eq 0 ] && echo "kit current: $target"
  return $drift
}

install_one() {
  local target="$1"
  [ -d "$target" ] || { echo "skip (not a dir): $target"; return 0; }
  mkdir -p "$target/.claude/hooks"
  # Retired files go before the copies, not after, so a future retired name that
  # collides with a current kit name cannot delete what was just installed.
  for f in $RETIRED_FILES; do
    rm -f "$target/.claude/$f"
  done
  cp "$SRC/phase-zero.md" "$target/.claude/phase-zero.md"
  cp "$SRC/retrospective.md" "$target/.claude/retrospective.md"
  cp "$SRC/model-routing.md" "$target/.claude/model-routing.md"
  cp "$SRC/operating-brief.md" "$target/.claude/operating-brief.md"
  cp "$SRC/hooks/phase-zero-trigger.sh" "$target/.claude/hooks/phase-zero-trigger.sh"
  cp "$SRC/hooks/session-brief.sh" "$target/.claude/hooks/session-brief.sh"
  chmod +x "$target/.claude/hooks/phase-zero-trigger.sh" "$target/.claude/hooks/session-brief.sh"

  local pz_cmd='bash "$CLAUDE_PROJECT_DIR/.claude/hooks/phase-zero-trigger.sh"'
  local sb_cmd='bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session-brief.sh"'
  local settings="$target/.claude/settings.json"
  if [ -f "$settings" ] && command -v jq >/dev/null 2>&1; then
    # Merge: keep existing config, add each kit hook if absent, and union the
    # kit permission clamps into the target. Existing entries keep their order;
    # kit rules are appended only when missing. An ask rule the target already
    # denies is not re-added to ask (deny wins, the stricter posture stands).
    # Nothing the repo already carries is dropped.
    jq --arg pz "$pz_cmd" --arg sb "$sb_cmd" --slurpfile kit "$SRC/settings.json" '
      .hooks //= {} |
      .hooks.UserPromptSubmit //= [] |
      (if any(.hooks.UserPromptSubmit[]?; (.hooks[]?.command // "") | test("phase-zero-trigger"))
       then . else .hooks.UserPromptSubmit += [{hooks:[{type:"command",command:$pz}]}] end) |
      .hooks.SessionStart //= [] |
      (if any(.hooks.SessionStart[]?; (.hooks[]?.command // "") | test("session-brief"))
       then . else .hooks.SessionStart += [{hooks:[{type:"command",command:$sb}]}] end) |
      .permissions //= {} |
      .permissions.deny = ((.permissions.deny // []) + (($kit[0].permissions.deny // []) - (.permissions.deny // []))) |
      .permissions.ask = ((.permissions.ask // []) + (($kit[0].permissions.ask // []) - (.permissions.ask // []) - (.permissions.deny // [])))
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

check_one_public() {
  local target="$1" drift=0
  [ -d "$target" ] || { echo "skip (not a dir): $target"; return 0; }
  if [ ! -d "$target/.claude/public-kit" ]; then
    echo "DRIFT missing public-kit directory: $target"
    return 1
  fi
  for f in $PUBLIC_KIT_FILES; do
    if [ ! -f "$target/.claude/public-kit/$f" ]; then
      echo "DRIFT missing $f: $target"
      drift=1
    elif ! cmp -s "$PUBLIC_SRC/$f" "$target/.claude/public-kit/$f"; then
      echo "DRIFT stale $f: $target"
      drift=1
    fi
  done
  local license_src license_dst
  license_src="$(public_license_template "$target")"
  license_dst="$target/.claude/public-kit/LICENSE.recommended"
  if [ ! -f "$license_dst" ]; then
    echo "DRIFT missing LICENSE.recommended: $target"
    drift=1
  elif ! cmp -s "$license_src" "$license_dst"; then
    echo "DRIFT stale LICENSE.recommended: $target"
    drift=1
  fi
  [ "$drift" -eq 0 ] && echo "public-kit current: $target"
  return $drift
}

install_one_public() {
  local target="$1"
  [ -d "$target" ] || { echo "skip (not a dir): $target"; return 0; }
  mkdir -p "$target/.claude/public-kit"
  for f in $PUBLIC_KIT_FILES; do
    cp "$PUBLIC_SRC/$f" "$target/.claude/public-kit/$f"
  done
  cp "$(public_license_template "$target")" "$target/.claude/public-kit/LICENSE.recommended"
  echo "public-kit installed -> $target/.claude/public-kit"
}

if [ "${1:-}" = "--public" ]; then
  shift
  if [ "${1:-}" = "--check" ]; then
    shift
    RC=0
    if [ "${1:-}" = "--all" ]; then
      parent="${2:?usage: install.sh --public --check --all <parent-dir>}"
      for d in "$parent"/*/; do
        if [ -d "$d/.git" ] && is_public_consumer "${d%/}"; then
          check_one_public "${d%/}" || RC=1
        fi
      done
    else
      check_one_public "${1:?usage: install.sh --public --check <target-repo-dir>}" || RC=1
    fi
    exit $RC
  elif [ "${1:-}" = "--all" ]; then
    parent="${2:?usage: install.sh --public --all <parent-dir>}"
    for d in "$parent"/*/; do
      if [ -d "$d/.git" ]; then
        if is_public_consumer "${d%/}"; then
          install_one_public "${d%/}"
        else
          echo "skip (not a public consumer): ${d%/}"
        fi
      fi
    done
  else
    install_one_public "${1:?usage: install.sh --public <target-repo-dir>   (or --public --all <parent-dir>)}"
  fi
  exit 0
elif [ "${1:-}" = "--check" ]; then
  shift
  RC=0
  if [ "${1:-}" = "--all" ]; then
    parent="${2:?usage: install.sh --check --all <parent-dir>}"
    for d in "$parent"/*/; do
      if [ -d "$d/.git" ] && is_consumer "${d%/}"; then
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
    if [ -d "$d/.git" ]; then
      if is_consumer "${d%/}"; then
        install_one "${d%/}"
      else
        echo "skip (not a consumer): ${d%/}"
      fi
    fi
  done
else
  install_one "${1:?usage: install.sh <target-repo-dir>   (or --all <parent-dir>)}"
fi
