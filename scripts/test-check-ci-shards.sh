#!/usr/bin/env bash
# test-check-ci-shards.sh: run scripts/check-ci-shards.sh on fixture workflows and on the real
# one. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# job LIST N: a one-job workflow whose matrix is LIST and whose steps pass N to test-shard.
job() {
  printf 'jobs:\n  t-shard:\n    strategy:\n      matrix:\n        shard: [%s]\n    steps:\n' "$1"
  printf '      - run: scripts/test-shard --check %s\n' "$2"
  printf '      - run: scripts/test-shard ${{ matrix.shard }} %s\n' "$2"
}
# expect NAME WANT-RC LIST N [WANT-TEXT]
expect() {
  job "$3" "$4" > "$TMP/ci.yml"
  out=$("$SRC/scripts/check-ci-shards.sh" "$TMP/ci.yml" 2>&1); rc=$?
  if [ "$rc" -eq "$2" ] && { [ -z "${5:-}" ] || printf '%s' "$out" | grep -qF -- "$5"; }; then ok "$1"
  else bad "$1 (rc=$rc, wanted $2 / '${5:-}')"; fi
}

expect "list and count agree" 0 "0, 1, 2, 3" 4
expect "matrix cut to three, count still four" 1 "0, 1, 2" 4 "t-shard"
expect "count cut to three, matrix still four" 1 "0, 1, 2, 3" 3 "t-shard"
expect "gap in the list" 1 "0, 1, 3" 3 "t-shard"
expect "duplicate in the list" 1 "0, 1, 1, 3" 4 "t-shard"

printf 'jobs:\n  t-shard:\n    steps:\n      - run: scripts/test-shard 0 4\n' > "$TMP/ci.yml"
"$SRC/scripts/check-ci-shards.sh" "$TMP/ci.yml" >/dev/null 2>&1 && bad "no matrix fails" || ok "test-shard with no matrix fails"
printf 'jobs:\n  t:\n    steps:\n      - run: echo hi\n' > "$TMP/ci.yml"
"$SRC/scripts/check-ci-shards.sh" "$TMP/ci.yml" >/dev/null 2>&1 && bad "no sharded job fails" || ok "a workflow with no sharded job fails (the check cannot go quiet)"
"$SRC/scripts/check-ci-shards.sh" "$SRC/.github/workflows/ci.yml" >/dev/null 2>&1 && ok "the real ci.yml passes" || bad "the real ci.yml passes"
exit "$fail"
