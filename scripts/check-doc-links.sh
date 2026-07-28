#!/usr/bin/env bash
# check-doc-links.sh — verify that relative markdown links point at real files.
#
# Scans every tracked *.md file for inline links, resolves relative targets
# against the containing file's directory, and fails on any that do not exist.
# External links (http, https, mailto, obsidian) and pure #anchors are skipped;
# a #fragment on a relative link is stripped before the existence check.
#
# Pure bash + grep + sed. Exit 1 on findings, 0 if clean.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail=0
while IFS= read -r file; do
  # Inline targets: ](target) — one per line, parentheses in URLs are rare here.
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    case "$target" in
      http://*|https://*|mailto:*|obsidian://*|\#*) continue ;;
    esac
    path="${target%%#*}"
    [ -n "$path" ] || continue
    if [ ! -e "$(dirname "$file")/$path" ]; then
      echo "BROKEN: $file -> $target"
      fail=1
    fi
  done < <(grep -oE '\]\([^)]+\)' "$file" | sed 's/^](//; s/)$//')
done < <(git ls-files '*.md')

if [ "$fail" -eq 0 ]; then
  echo "check-doc-links: CLEAN"
fi
exit "$fail"
