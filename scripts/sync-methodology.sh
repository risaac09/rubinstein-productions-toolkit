#!/usr/bin/env bash
# sync-methodology.sh — sync vault canonicals → toolkit/methodology/
#
# Single canonical sync script for the vault → toolkit pipeline.
# Replaces the older mirror-side `~/second-brain-mirror/bin/sync-snapshots.sh`
# (deleted 2026-04-27).
#
# Behavior:
#   - Strips Obsidian frontmatter (leading ---...--- block).
#   - Strips the vault's `> **Canonical:**` self-declaration blockquote.
#   - Strips `[[wikilinks]]` to plain text (toolkit is public; wikilinks
#     don't render meaningfully outside Obsidian).
#   - Preserves any user-customized "Canonical source:" header in the toolkit
#     file — only the `last synced YYYY-MM-DD` date is auto-updated. If no
#     header exists yet, a generic default is written.
#   - Idempotent — re-running with no vault changes is a no-op.
#
# Usage: bash scripts/sync-methodology.sh
# Run after editing any vault canonical, then commit the toolkit changes.

set -euo pipefail

VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain"
TOOLKIT="$(cd "$(dirname "$0")/.." && pwd)/methodology"
DATE=$(date +%Y-%m-%d)

# Format: "vault-relative-path|toolkit-filename"
# (All canonicals consolidated into 00 Canonical/ on 2026-04-27.)
PAIRS=(
  "00 Canonical/RP Measurement Framework.md|enif.md"
  "00 Canonical/RP Session Facilitation Guide.md|session-facilitation-guide.md"
  "00 Canonical/Digital Liver Offering Definition.md|digital-liver-offering.md"
  "00 Canonical/Nomadic Indicators Codebook.md|nomadic-indicators-codebook.md"
  "00 Canonical/Say Why — Theory of Change.md|theory-of-change.md"
  "00 Canonical/Say Why — Evaluation Framework.md|evaluation-framework.md"
)

CHANGED=0
for pair in "${PAIRS[@]}"; do
  VPATH="${pair%%|*}"
  TFILE_NAME="${pair##*|}"
  VFILE="$VAULT/$VPATH"
  TFILE="$TOOLKIT/$TFILE_NAME"

  if [[ ! -f "$VFILE" ]]; then
    echo "  SKIP (vault file missing): $VPATH" >&2
    continue
  fi

  TMP=$(mktemp)

  # 1. Determine the header.
  #    - If existing toolkit file has a customized "Canonical source:" header,
  #      preserve it but bump the date.
  #    - Otherwise write a generic default.
  HEADER=""
  if [[ -f "$TFILE" ]]; then
    FIRST=$(head -1 "$TFILE")
    if [[ "$FIRST" == "> **Canonical source:**"* ]]; then
      HEADER=$(printf '%s' "$FIRST" | sed -E "s|last synced [0-9]{4}-[0-9]{2}-[0-9]{2}|last synced $DATE|")
    fi
  fi
  if [[ -z "$HEADER" ]]; then
    HEADER=$(printf '> **Canonical source:** `%s` in vault — last synced %s. Edit there; this file is a snapshot for skills and public repo use.' "$VPATH" "$DATE")
  fi

  # 2. Build body: strip frontmatter, strip self-declaration blockquote, strip wikilinks.
  BODY=$(awk '
    BEGIN { in_fm = 0; past_fm = 0; line = 0 }
    {
      line++
      # First line is opening of frontmatter
      if (line == 1 && /^---$/) { in_fm = 1; next }
      # Closing of frontmatter
      if (in_fm && /^---$/) { in_fm = 0; past_fm = 1; next }
      # Inside frontmatter — skip
      if (in_fm) next
      # Skip the self-declaration blockquote line(s) right after frontmatter
      if (past_fm && /^> \*\*Canonical:\*\*/) { next }
      # Skip blank lines until we hit real content
      if (past_fm == 1 && /^[[:space:]]*$/) { next }
      past_fm = 2  # signal: real content started
      print
    }
  ' "$VFILE")

  # Strip [[wikilinks]] to plain text — handle [[A|B]] (display B) and [[A]] (display A)
  BODY=$(printf '%s' "$BODY" \
    | sed -E 's/\[\[([^]|]+)\|([^]]+)\]\]/\2/g' \
    | sed -E 's/\[\[([^]]+)\]\]/\1/g')

  # 3. Write header + blank + body
  {
    printf '%s\n\n' "$HEADER"
    printf '%s\n' "$BODY"
  } > "$TMP"

  # 4. Compare; rewrite only if different (avoid touching mtime when no change)
  if ! cmp -s "$TMP" "$TFILE"; then
    mv "$TMP" "$TFILE"
    echo "  UPDATED: methodology/$TFILE_NAME"
    CHANGED=$((CHANGED + 1))
  else
    rm "$TMP"
    echo "  unchanged: methodology/$TFILE_NAME"
  fi
done

echo ""
echo "Snapshots regenerated: $CHANGED file(s) changed."
echo "Toolkit-canonical files (glossary, facilitation-protocol, methodology-blueprint) NOT touched — they have no vault counterpart."

if [[ "$CHANGED" -gt 0 ]]; then
  echo ""
  echo "Next step: review and commit toolkit changes:"
  echo "  cd ~/rubinstein-productions-toolkit && git diff methodology/"
  echo "  git add methodology/ && git commit -m \"sync methodology from vault $DATE\" && git push"
fi
