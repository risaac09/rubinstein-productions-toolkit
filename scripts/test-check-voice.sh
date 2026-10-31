#!/usr/bin/env bash
# test-check-voice.sh: run scripts/check-voice.sh on fixture trees. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
EMD=$(printf '\342\200\224')

mk() { mkdir -p "$TMP/$1/docs"; printf '# T\n' > "$TMP/$1/README.md"; printf '%s\n' "$2" > "$TMP/$1/docs/a.md"; }
mk clean "A plain sentence."
mk dash "One line.
Two $EMD three."
mk verb "We navigate the field."
mk phrase "Let us lean into it, then circle back, unpacking and doubling down."
mkdir -p "$TMP/readme"; printf 'x %s y\n' "$EMD" > "$TMP/readme/README.md"
mk near "Navigation is fine."

"$SRC/scripts/check-voice.sh" "$TMP/clean" >/dev/null 2>&1 && ok "clean passes" || bad "clean passes"
out=$("$SRC/scripts/check-voice.sh" "$TMP/dash" 2>&1) && bad "em-dash fails" || { case $out in *"docs/a.md:2: em-dash"*) ok "em-dash fails with file:line";; *) bad "em-dash report: $out";; esac; }
out=$("$SRC/scripts/check-voice.sh" "$TMP/verb" 2>&1) && bad "verb fails" || { case $out in *"docs/a.md:1: banned verb"*) ok "banned verb fails with file:line";; *) bad "verb report: $out";; esac; }
out=$("$SRC/scripts/check-voice.sh" "$TMP/phrase" 2>&1) && bad "phrase forms fail" || { [ "$(printf '%s\n' "$out" | grep -c 'banned verb')" -eq 4 ] && ok "all four phrase forms flagged" || bad "phrase report: $out"; }
mk entity "Two &mdash; three, four &#8212; five."
out=$("$SRC/scripts/check-voice.sh" "$TMP/entity" 2>&1) && bad "dash entities fail" || { case $out in *"docs/a.md:1: em-dash"*) ok "dash entities flagged";; *) bad "entity report: $out";; esac; }
out=$("$SRC/scripts/check-voice.sh" "$TMP/readme" 2>&1) && bad "README-only em-dash fails" || { case $out in *"README.md:1: em-dash"*) ok "README-only tree reported with filename";; *) bad "readme report: $out";; esac; }
"$SRC/scripts/check-voice.sh" "$TMP/near" >/dev/null 2>&1 && ok "navigation is not flagged" || bad "navigation is not flagged"
exit "$fail"
