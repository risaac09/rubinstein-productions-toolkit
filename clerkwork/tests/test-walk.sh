#!/usr/bin/env bash
# test-walk.sh: walk a fresh practice through add-contact, add-project and add-invoice.
#
# Usage: clerkwork/tests/test-walk.sh
# Copies skeleton/ to a temp dir, pins the date, adds a contact, a project and
# an invoice, and asserts what bin/today prints. Also checks the refusals:
# duplicate id, duplicate normalized email, a second invoice, and that
# add-invoice never writes a paid status. Exit 0 when all hold.
set -euo pipefail

CW="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp -R "$CW/skeleton/." "$tmp/"
cd "$tmp"
export CLERKWORK_TODAY=2026-10-26

fails=0
fail() { echo "FAIL: $*" >&2; fails=$((fails + 1)); }
has() { grep -qF -- "$1" <<<"$out" || fail "output lacks: $1"; }
refuses() { # refuses DESC CMD...
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then fail "should refuse: $d"; fi
}

bin/add-contact "Ada Example" --email Ada@Example.com --next "Send the proposal" --due 2026-10-23 --kind call >/dev/null
refuses "duplicate id" bin/add-contact "Ada Example" --email other@example.com --next x --due 2026-11-01
refuses "duplicate normalized email" bin/add-contact "Ada E" --email " ada@example.COM " --next x --due 2026-11-01
refuses "no next action" bin/add-contact "Bo Example"

bin/add-project c-ada-example "Spring film" --value 3000 --deliverable "Rough cut:2026-11-10" >/dev/null
refuses "unknown contact" bin/add-project c-nobody "Ghost film" --value 1
bin/add-project c-ada-example "Autumn photos" --value 900 >/dev/null
jq '(.[] | select(.id == "p-autumn-photos") | .status) = "delivered"' data/projects.json > p.tmp && mv p.tmp data/projects.json

bin/add-invoice p-spring-film >/dev/null
refuses "second invoice" bin/add-invoice p-spring-film
refuses "unknown project" bin/add-invoice p-nobody

jq -e '.[0] | .kind == "invoice" and .status == "draft" and .paid == null and .amount == 3000 and .due == "2026-11-25"' \
  data/financials.json >/dev/null || fail "invoice should be a draft for 3000, unpaid, due in 30 days"

out="$(bin/today)"
has "Calls made"
has "Decisions owed"
has "c-ada-example  touch overdue 3d: Send the proposal"
has "f-spring-film-1  invoice draft, not sent: 3000 USD for p-spring-film"
has "p-autumn-photos  delivered, not invoiced"
grep -qF "p-spring-film  delivered" <<<"$out" && fail "p-spring-film is active, not delivered"

bin/validate >/dev/null || fail "the walked ledgers should validate"

[ "$fails" -eq 0 ] || exit 1
echo "test-walk: ok"
