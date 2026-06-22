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
VAULT="${RP_VAULT:-$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain}"
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
