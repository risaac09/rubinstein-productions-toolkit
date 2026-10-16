#!/usr/bin/env bash
# test-burst-gate.sh: run scripts/burst-gate against throwaway branches, one
# failure per gate and one clean pass. G3 is exercised with --tests commands;
# the default test set is not run here. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@localhost GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@localhost

R="$TMP/repo"; mkdir -p "$R/scripts" "$R/changelog.d" "$R/docs" "$R/phase-zero"
cp "$SRC/scripts/burst-gate" "$SRC/scripts/changelog-fragments" "$R/scripts/"
cp "$SRC/changelog.d/README.md" "$R/changelog.d/"
printf '# Changelog\n\n## Unreleased\n\n- Old.\n' > "$R/CHANGELOG.md"
echo base > "$R/docs/a.md"; echo base > "$R/phase-zero/k.sh"
git -C "$R" init -q -b main && git -C "$R" add -A && git -C "$R" commit -qm base
G="$R/scripts/burst-gate"
# Fixtures that would trip a scan are built here, so this file stays clean.
EMD=$'\xe2\x80\x94'
HOMEPATH="/Us""ers/someone""else/notes"
TOKEN="gh""p_$(printf 'a%.0s' $(seq 1 30))"

# branch NAME FILE CONTENT [fragment?]: new branch off main with one commit.
branch() {
  git -C "$R" checkout -q main; git -C "$R" checkout -q -b "$1"
  mkdir -p "$R/$(dirname "$2")"; printf '%s\n' "$3" > "$R/$2"
  [ "${4:-frag}" = nofrag ] || printf 'Changed %s.\n' "$1" > "$R/changelog.d/2026-10-16-$1.md"
  git -C "$R" add -A; git -C "$R" commit -qm "change $1"
}
gate() { (cd "$R" && "$G" --base main --tests true "$@" 2>&1); }
run() { # name exit-wanted text-wanted args...
  name="$1"; want="$2"; text="$3"; shift 3
  out="$(gate "$@")"; rc=$?
  if [ "$rc" -eq "$want" ] && printf '%s' "$out" | grep -qF -- "$text"; then ok "$name"; else bad "$name (rc=$rc, wanted $want / '$text')"; printf '%s\n' "$out" | sed 's/^/       /'; fi
}

printf 'docs/a.md\n' > "$TMP/decl-ok"; printf 'docs/b.md\n' > "$TMP/decl-wrong"

branch clean docs/a.md "plain words here"
run "clean branch passes every gate" 0 "all gates that ran passed" --declared "$TMP/decl-ok"
run "no --declared still passes, with a note" 0 "no --declared given"

branch undeclared docs/a.md "plain words"
run "G1 fails when the diff differs from the declaration" 1 "G1 scope: diff and declared files differ" --declared "$TMP/decl-wrong"

branch kit phase-zero/k.sh "changed" 
run "G1 fails on an edit under phase-zero" 1 "touches the kit exports"
git -C "$R" checkout -q main; git -C "$R" checkout -q -b kit2; mkdir -p "$R/public-kit"; echo x > "$R/public-kit/x.md"; git -C "$R" add -A; git -C "$R" commit -qm kit2
run "G1 fails on an edit under public-kit" 1 "touches the kit exports"

branch dirty docs/a.md "plain words"; echo more >> "$R/docs/a.md"
run "G1 fails on an uncommitted change" 1 "uncommitted changes"
git -C "$R" checkout -q -- docs/a.md

branch homepath docs/a.md "see $HOMEPATH for details"
run "G2 fails on a home path" 1 "G2 privacy: private-looking text"
branch token docs/a.md "key $TOKEN"
run "G2 fails on a token shape" 1 "G2 privacy: private-looking text"
branch taskword docs/a.md "a task-based-routing-for-everything-here approach"
run "G2 does not flag sk- inside a word" 0 "PASS G2 privacy"
branch placeholder docs/a.md "see /Us""ers/you/notes"
run "G2 allows a placeholder user name" 0 "PASS G2 privacy"
printf 'Acme Widgets\n# comment\n' > "$TMP/deny"
branch client docs/a.md "worked with acme widgets"
out="$(cd "$R" && BURST_GATE_DENYLIST="$TMP/deny" "$G" --base main --tests true 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q 'G2 privacy: private' && ok "G2 honours the denylist file, case-insensitively" || bad "G2 denylist (rc=$rc)"
run "G2 skip is reported" 0 "SKIP G2" --skip G2

branch dash docs/a.md "a clause $EMD another"
run "G4 fails on an em-dash in markdown" 1 "G4 style: em-dash"
branch dashcode scripts/x.sh "echo '$EMD'"
run "G4 ignores non-prose files" 0 "PASS G4 style"

branch nofrag docs/a.md "plain" nofrag
run "G5 fails with no fragment" 1 "G5 changelog: no changelog.d/ fragment"
out="$(cd "$R" && "$G" --base main --tests true --fix 2>&1)"; rc=$?
ls "$R"/changelog.d/*nofrag*.md >/dev/null 2>&1 && [ "$rc" -eq 0 ] && ok "G5 --fix writes a fragment and passes" || bad "G5 --fix (rc=$rc): $out"
git -C "$R" checkout -q -f main; git -C "$R" clean -qfd

branch badfrag docs/a.md "plain" nofrag; printf '%s\n' '- bullet marker' > "$R/changelog.d/2026-10-16-badfrag.md"; git -C "$R" add -A; git -C "$R" commit -qm bf
run "G5 fails on a malformed fragment" 1 "a fragment is malformed"

branch export phase-zero/k.sh "e" nofrag
run "an export branch still fails G1 (no exception for workers)" 1 "touches the kit exports"

branch testsfail docs/a.md "plain"
out="$(cd "$R" && "$G" --base main --tests 'echo boom; exit 3' 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q 'G3 tests: exit 3' && printf '%s' "$out" | grep -q boom && ok "G3 fails and shows the output tail" || bad "G3 (rc=$rc)"
run "G3 skip is reported" 0 "SKIP G3" --skip G3

# A JSON declaration, when jq is present.
if command -v jq >/dev/null; then
  branch jsondecl docs/a.md "plain"; printf '{"finding":"f1","files_touched":["docs/a.md"],"claims":[]}\n' > "$TMP/r.json"
  run "G1 reads files_touched from a return JSON" 0 "PASS G1 scope" --declared "$TMP/r.json"
fi

# All failures are listed, not just the first.
branch many docs/a.md "a $EMD b $HOMEPATH" nofrag
out="$(gate)"; rc=$?
[ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q 'FAILED: G2 G4 G5' && ok "the summary lists every failed gate" || bad "summary: $out"

echo
[ "$fail" -eq 0 ] && echo "all burst-gate tests passed" || { echo "burst-gate tests FAILED"; exit 1; }
