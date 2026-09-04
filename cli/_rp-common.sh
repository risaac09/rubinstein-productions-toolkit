#!/bin/bash
# _rp-common.sh — shared helpers for the rp-* outreach CLI tools.
#
# Sourced by every rp-* script. Because install.sh symlinks the scripts into
# /usr/local/bin, each script resolves its real directory through the symlink
# before sourcing this file:
#
#   src="${BASH_SOURCE[0]}"
#   while [ -L "$src" ]; do
#     dir="$(cd -P "$(dirname "$src")" && pwd)"
#     src="$(readlink "$src")"; [ "${src#/}" = "$src" ] && src="$dir/$src"
#   done
#   . "$(cd -P "$(dirname "$src")" && pwd)/_rp-common.sh"

# Vault + outreach roots. Override with RP_VAULT / RP_OUTREACH_DIR.
VAULT="${RP_VAULT:-$HOME/vault/Second Brain}"
OUTREACH="${RP_OUTREACH_DIR:-$VAULT/Practice/Rubinstein Productions/Outreach}"

# fm FILE FIELD — print the value of a top-level frontmatter scalar.
# Handles both quoted (org: "Acme") and bare (status: ready) values, and
# returns the first match only.
fm() {
  grep "^$2:" "$1" 2>/dev/null | head -1 | sed "s/^$2: *//; s/^\"//; s/\"$//"
}

# slugify TEXT — lowercase, spaces to hyphens, strip to [a-z0-9-].
slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-'
}

# sedi EXPR FILE — in-place sed. GNU sed takes -i with no argument; BSD/macOS
# sed requires -i '' (a separate empty backup-suffix argument).
if sed --version >/dev/null 2>&1; then
  sedi() { sed -i "$1" "$2"; }
else
  sedi() { sed -i '' "$1" "$2"; }
fi

# date_add_days N — print today+N days as YYYY-MM-DD. BSD date uses -v, GNU uses -d.
date_add_days() {
  date -v+"$1"d +%Y-%m-%d 2>/dev/null || date -d "+$1 days" +%Y-%m-%d
}

# open_url URL — open in the default browser: macOS open, then xdg-open,
# else print the URL for manual copy.
open_url() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1"
  else echo "  Open this URL manually:"; echo "  $1"
  fi
}
