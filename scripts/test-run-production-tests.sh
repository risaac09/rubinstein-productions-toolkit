#!/usr/bin/env bash
# test-run-production-tests.sh: run scripts/run-production-tests in a throwaway
# tree with a fake optional job limiter on a temp HOME: present, absent, not
# executable, timing out with 75, and a failing suite. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

R="$TMP/repo"; H="$TMP/home"
mkdir -p "$R/scripts" "$R/production/tests" "$H/scripts"
cp "$SRC/scripts/run-production-tests" "$R/scripts/"
printf 'import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n' > "$R/production/tests/test_stub.py"
RUN="$R/scripts/run-production-tests"

# expect NAME WANT-EXIT WANT-TEXT: runs the runner on the temp HOME.
expect() {
  out="$(HOME="$H" "$RUN" 2>&1)"; rc=$?
  if [ "$rc" -eq "$2" ] && printf '%s' "$out" | grep -qF -- "$3"; then ok "$1"; else bad "$1 (rc=$rc, wanted $2 / '$3')"; printf '%s\n' "$out" | sed 's/^/       /'; fi
}
wrapper() { printf '#!/bin/bash\n%s\n' "$1" > "$H/scripts/heavy-slot"; chmod "${2:-755}" "$H/scripts/heavy-slot"; }

rm -f "$H/scripts/heavy-slot"
expect "no wrapper: the tests run plainly" 0 "OK"

wrapper 'echo "$@" > "$HOME/args"; shift 3; exec "$@"'
expect "wrapper present: the tests run under it" 0 "OK"
[ "$(cut -d' ' -f1-4 "$H/args")" = "--timeout 1800 -- python3" ] && ok "called with a 1800s timeout" || bad "wrong call: $(cat "$H/args")"

wrapper 'exit 99' 644
expect "wrapper not executable: skipped" 0 "OK"

wrapper 'echo "gave up" >&2; exit 75'
expect "wrapper timeout: non-zero with a clear message" 75 "no job slot free within 1800s"

wrapper 'shift 3; exec "$@"'
printf 'import unittest\nclass T(unittest.TestCase):\n    def test_no(self): self.fail("boom")\n' > "$R/production/tests/test_stub.py"
expect "a failing suite stays a failure under the wrapper" 1 "FAILED"

[ "$fail" -eq 0 ] && echo "all run-production-tests checks passed" || { echo "run-production-tests checks failed"; exit 1; }
