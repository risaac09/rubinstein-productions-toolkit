#!/usr/bin/env bash
# test-validate.sh: bin/validate passes the clean ledgers and fails the dangling one.
#
# Usage: clerkwork/tests/test-validate.sh
# Checks: exit 0 on skeleton/ and examples/practice; exit 1 on examples/negative
# with a FAIL line that names the dangling contact id; exit 1 on a ledger that
# is not JSON. Exit 0 when all hold.
set -euo pipefail

CW="$(cd "$(dirname "$0")/.." && pwd)"
V="$CW/skeleton/bin/validate"
fails=0
fail() { echo "FAIL: $*" >&2; fails=$((fails + 1)); }

"$V" "$CW/skeleton" >/dev/null || fail "skeleton/data should validate"
"$V" "$CW/examples/practice" >/dev/null || fail "examples/practice should validate"

rc=0; out="$("$V" "$CW/examples/negative")" || rc=$?
[ "$rc" -eq 1 ] || fail "examples/negative exited $rc, want 1"
grep -q 'c-ghost-contact' <<<"$out" || fail "failure line does not name c-ghost-contact: $out"
[ "$(wc -l <<<"$out" | tr -d ' ')" -eq 1 ] || fail "want exactly one failure line, got: $out"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp -R "$CW/examples/practice/." "$tmp/"
echo '{not json' > "$tmp/data/contacts.json"
rc=0; "$V" "$tmp" >/dev/null || rc=$?
[ "$rc" -eq 1 ] || fail "a broken ledger exited $rc, want 1"

[ "$fails" -eq 0 ] || exit 1
echo "test-validate: ok"
