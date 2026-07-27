#!/bin/bash
# cli-smoke-test.sh — end-to-end exercise of the rp-* CLI against a temp dir.
#
# Runs the full prospect and grant lifecycle in a throwaway RP_OUTREACH_DIR,
# then greps the produced files for the writes each command claims to make.
# Works on both GNU (Linux/CI) and BSD (macOS) userlands; that portability is
# exactly what this test guards.
#
# Usage: scripts/cli-smoke-test.sh
set -u

CLI="$(cd "$(dirname "$0")/../cli" && pwd)"
export RP_OUTREACH_DIR="$(mktemp -d)"
trap 'rm -rf "$RP_OUTREACH_DIR"' EXIT

pass=0
fail=0
check() { # check DESCRIPTION COMMAND...
  desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1)); echo "FAIL: $desc"
  fi
}

# Syntax
for t in _rp-common.sh rp-prospect rp-pipeline rp-update rp-draft rp-followup rp-grant; do
  check "bash -n $t" bash -n "$CLI/$t"
done

# Help flags exit 0
for t in rp-prospect rp-pipeline rp-update rp-draft rp-followup rp-grant; do
  check "$t --help" "$CLI/$t" --help
done

# Prospect lifecycle
check "rp-prospect create" "$CLI/rp-prospect" "Acme Org" "https://acme.org" "Jane Doe" "Director" "jane@acme.org"
P="$RP_OUTREACH_DIR/prospects/acme-org.md"
check "prospect file exists" test -f "$P"
check "rp-update to contacted" "$CLI/rp-update" acme-org contacted "Sent intro"
check "status written" grep -q "^status: contacted" "$P"
check "touch logged" grep -q "| contacted | Sent intro" "$P"
check "followup date set" grep -Eq '^next_followup: "[0-9]{4}-[0-9]{2}-[0-9]{2}"' "$P"
check "rp-pipeline runs" "$CLI/rp-pipeline"
check "rp-followup runs" "$CLI/rp-followup"

# Grant lifecycle
check "rp-grant add" bash -c "printf 'Test Fdn\nfoundation\nhttps://t.org\nhealth\n\$10K\n2099-01-01\nnarrative-SDOH\n' | '$CLI/rp-grant' add"
G="$RP_OUTREACH_DIR/grants/funders/test-fdn.md"
check "funder file exists" test -f "$G"
check "rp-grant update" "$CLI/rp-grant" update test-fdn loi-prep
check "funder status written" grep -q "^status: loi-prep" "$G"
check "rp-grant log" "$CLI/rp-grant" log test-fdn "Drafted LOI"
check "action logged" grep -q "| Drafted LOI" "$G"
check "rp-grant deadlines" "$CLI/rp-grant" deadlines

echo ""
echo "cli-smoke-test: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
