#!/usr/bin/env bash
# test-methodology-status.sh: exercise scripts/check-methodology-status.sh on
# fixture trees. Covers each root it checks and the three outcomes: no status
# fails, a valid word passes, an invalid word fails. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$SRC/scripts/check-methodology-status.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

mk() { # root: a tree with one passing doc in each covered place
  mkdir -p "$1/methodology" "$1/docs/sub/deep" "$1/production/skills" "$1/production/tool"
  for f in methodology/a.md docs/a.md docs/sub/deep/b.md production/a.md; do
    printf '# T\n\n> **Status:** draft, 2026-10-31. Note.\n' > "$1/$f"
  done
}
expect() { # name root want-exit [stderr-fragment]
  out=$("$CHECK" "$2" 2>&1); rc=$?
  if [ "$rc" -eq "$3" ] && { [ -z "${4:-}" ] || printf '%s' "$out" | grep -qF "$4"; }; then ok "$1"; else bad "$1 (rc=$rc: $out)"; fi
}

R="$TMP/clean"; mk "$R"
expect "valid statuses pass" "$R" 0 CLEAN

for w in operational draft aspirational superseded; do
  printf '# T\n\n**Status:** %s.\n' "$w" > "$R/docs/a.md"
  expect "word $w passes" "$R" 0
done

for spot in docs/new.md docs/sub/deep/new.md production/new.md methodology/new.md; do
  R="$TMP/miss"; rm -rf "$R"; mk "$R"; printf '# T\n\nNo status here.\n' > "$R/$spot"
  expect "no status fails: $spot" "$R" 1 "no status header: $spot"
done

R="$TMP/badword"; mk "$R"
printf '# T\n\n> **Status:** accepted 2026-10-16.\n' > "$R/production/a.md"
expect "invalid word fails" "$R" 1 "production/a.md"

for w in draft-ish operational/draft draft_old; do
  R="$TMP/prefix"; mk "$R"; printf '# T\n\n**Status:** %s\n' "$w" > "$R/docs/a.md"
  expect "word $w fails" "$R" 1 "docs/a.md"
done

R="$TMP/norooot"; mk "$R"; rm -rf "$R/methodology"
expect "missing methodology/ fails" "$R" 1 "missing directory"

R="$TMP/late"; mk "$R"
{ for _ in $(seq 30); do echo line; done; echo '**Status:** draft.'; } > "$R/docs/a.md"
expect "status past line 25 fails" "$R" 1 "docs/a.md"

R="$TMP/skip"; mk "$R"
printf '# Skill\n\nNo status.\n' > "$R/production/skills/s.md"
printf '# Tool\n\nNo status.\n' > "$R/production/tool/t.md"
expect "production subfolders are not checked" "$R" 0

# A folder the check cannot list is an error, never a clean pass.
R="$TMP/unlistable"; mk "$R"
printf '# T\n\nNo status.\n' > "$R/docs/sub/x.md"; chmod 000 "$R/docs/sub"
if [ -r "$R/docs/sub" ]; then ok "unlistable docs folder fails (skipped: running as root)"
else expect "unlistable docs folder fails" "$R" 1 "cannot list"; fi
chmod 755 "$R/docs/sub"
R="$TMP/unlistable2"; mk "$R"; chmod 000 "$R/methodology"
if [ -r "$R/methodology" ]; then ok "unlistable methodology folder fails (skipped: running as root)"
else expect "unlistable methodology folder fails" "$R" 1 "cannot list"; fi
chmod 755 "$R/methodology"

# The repo itself must pass.
if "$CHECK" >/dev/null 2>&1; then ok "repo tree passes"; else bad "repo tree fails"; fi
exit "$fail"
