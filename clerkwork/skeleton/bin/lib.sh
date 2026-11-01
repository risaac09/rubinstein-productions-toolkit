# shellcheck shell=bash disable=SC2034
# Shared by add-contact, add-project and add-invoice. Sourced, not run.
# Generalized from stack-data scripts/sd-add-contact, 2026-10-22.
#
# Env: CLERKWORK_ROOT   practice root (default: the directory above bin/)
#      CLERKWORK_TODAY  YYYY-MM-DD to act as today (default: the system date)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CLERKWORK_ROOT:-$(cd "$HERE/.." && pwd)}"
DATA="$ROOT/data"
TODAY="${CLERKWORK_TODAY:-$(date +%F)}"
NOW="${TODAY}T$(date -u +%H:%M:%S)Z"
CURRENCY="$(sed -n 's/^currency: *//p' "$ROOT/practice.yaml" 2>/dev/null | head -n 1 | tr -d '\"\047\r' | sed 's/ *$//')"
CURRENCY="${CURRENCY:-USD}"

[[ "$TODAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "CLERKWORK_TODAY must be YYYY-MM-DD, got: $TODAY" >&2; exit 2; }

die() { echo "$(basename "$0"): $*" >&2; exit 1; }

# slug TEXT: lowercase, runs of other characters become one hyphen
slug() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'; }

# add_days DATE N: DATE plus N days, ISO
add_days() {
  jq -nr --arg d "$1" --argjson n "$2" \
    '($d | strptime("%Y-%m-%d") | mktime) + $n * 86400 | strftime("%Y-%m-%d")'
}

# commit LEDGER: replace data/LEDGER.json with stdin, then run validate.
# A ledger that fails validation is put back as it was.
commit() {
  local f="$DATA/$1.json" tmp out
  tmp="$(mktemp "$DATA/.new.XXXXXX")"; cat > "$tmp"
  cp "$f" "$f.bak"; mv "$tmp" "$f"
  if ! out="$("$HERE/validate" "$ROOT" 2>&1)"; then
    mv "$f.bak" "$f"
    echo "$out" >&2
    die "the ledgers do not validate with this change (an earlier defect also blocks it); nothing written"
  fi
  rm -f "$f.bak"
}
