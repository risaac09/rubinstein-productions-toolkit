#!/usr/bin/env bash
# check-ci-shards.sh [ci.yml]: every job that calls scripts/test-shard must list the shards
# 0..N-1 in its matrix, exactly once each, and pass that same N to test-shard. The matrix list
# and the count are typed apart, so cutting one without the other would stop a shard's modules
# while every check stays green. It fails closed: a test-shard call whose count it cannot read
# (any spelling but the two exact ones below), a job with no `test-shard --check` call, a matrix
# exclude, a step `if` on matrix.shard, or no sharded job at all, is a named failure.
# Accepted calls: `scripts/test-shard --check N` and `scripts/test-shard ${{ matrix.shard }} N`.
set -euo pipefail
f="${1:-$(dirname "$0")/../.github/workflows/ci.yml}"
awk '
function finish(   n, i, want) {
  if (job != "" && used) {
    seen++
    n = split(list, a, /[^0-9]+/); cnt = 0; want = ""
    for (i = 1; i <= n; i++) if (a[i] != "") { cnt++; want = want (want == "" ? "" : ",") a[i] }
    exp_ = ""; for (i = 0; i < cnt; i++) exp_ = exp_ (i ? "," : "") i
    if (!chk) { print job ": no test-shard --check call"; bad = 1 }
    if (excl) { print job ": matrix exclude can drop a shard"; bad = 1 }
    if (ifs) { print job ": matrix.shard in an if can skip a shard"; bad = 1 }
    if (unread) { print job ": cannot read the shard count of " unread " test-shard call(s)"; bad = 1 }
    if (list == "") { print job ": calls test-shard with no matrix shard list"; bad = 1 }
    else if (want != exp_) { print job ": matrix shards [" want "] are not 0.." cnt - 1; bad = 1 }
    for (i in ns) if (ns[i] != cnt) { print job ": test-shard is given " ns[i] " but the matrix has " cnt " shards"; bad = 1 }
  }
  job = ""; list = ""; used = 0; chk = 0; excl = 0; ifs = 0; unread = 0; delete ns
}
/^  [A-Za-z0-9_-]+:[ ]*$/ { finish(); job = $1; sub(/:$/, "", job); next }
/^[^ ]/ { finish(); next }
job != "" && /shard:[ ]*\[/ { list = $0; sub(/.*\[/, "", list); sub(/\].*/, "", list) }
job != "" && /^[ ]*(- )?exclude:/ { excl = 1 }
job != "" && /^[ ]*(- )?if:.*matrix\.shard/ { ifs = 1 }
job != "" && /scripts\/test-shard/ && !/^[ ]*#/ {
  used = 1
  if (match($0, /scripts\/test-shard --check [0-9]+([^0-9]|$)/)) { chk = 1; v = substr($0, RSTART, RLENGTH); sub(/^.*--check /, "", v); sub(/[^0-9].*$/, "", v); ns[++k] = v }
  else if (match($0, /scripts\/test-shard \$\{\{ matrix\.shard \}\} [0-9]+([^0-9]|$)/)) { v = substr($0, RSTART, RLENGTH); sub(/^.*\}\} /, "", v); sub(/[^0-9].*$/, "", v); ns[++k] = v }
  else unread++
}
END { finish(); if (!seen) { print "no job calls scripts/test-shard"; bad = 1 } exit bad }
' "$f" && echo "ci shards: every matrix list matches the count it passes to test-shard"
