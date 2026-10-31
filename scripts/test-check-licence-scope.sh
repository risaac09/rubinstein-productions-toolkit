#!/usr/bin/env bash
# test-check-licence-scope.sh: run scripts/check-licence-scope.sh on fixture repos. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@localhost GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@localhost

mkdir -p "$TMP/nogit"; R="$TMP/repo"; mkdir -p "$R/scripts" "$R/docs" "$R/changelog.d"
cp "$SRC/LICENSE" "$R/"
for d in scripts docs changelog.d; do echo x > "$R/$d/f"; done
git -C "$R" init -q -b main && git -C "$R" add -A && git -C "$R" commit -qm base
"$SRC/scripts/check-licence-scope.sh" "$R" >/dev/null 2>&1 && ok "classified tree passes" || bad "classified tree passes"

mkdir "$R/stray"; echo x > "$R/stray/f"; git -C "$R" add -A && git -C "$R" commit -qm stray
out=$("$SRC/scripts/check-licence-scope.sh" "$R" 2>&1) && bad "unclassified dir fails" || { case $out in *"unclassified top-level directory: stray"*) ok "unclassified dir fails";; *) bad "report: $out";; esac; }
sed -i.bak 's/`scripts\/`/scripts/' "$R/LICENSE" && rm -f "$R/LICENSE.bak"
rm -rf "$R/stray"; git -C "$R" add -A && git -C "$R" commit -qm unstray
out=$("$SRC/scripts/check-licence-scope.sh" "$R" 2>&1) && bad "LICENSE drift fails" || { case $out in *"LICENSE scope no longer names scripts/"*) ok "LICENSE drift fails";; *) bad "drift report: $out";; esac; }
"$SRC/scripts/check-licence-scope.sh" "$TMP/nogit" >/dev/null 2>&1; [ $? -eq 2 ] && ok "non-repo fails closed" || bad "non-repo fails closed"
exit "$fail"
