#!/usr/bin/env bash
# sync-methodology.sh — copy vault methodology files → toolkit/methodology/
#
# Usage: bash scripts/sync-methodology.sh
#
# Strips Obsidian frontmatter (leading ---...--- block) and converts
# [[wikilinks]] to plain text, then prepends a canonical header.
# Run this after editing vault files to keep the toolkit snapshot current.

set -euo pipefail

VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain"
TOOLKIT="$(cd "$(dirname "$0")/.." && pwd)/methodology"
DATE=$(date +%Y-%m-%d)

strip_and_write() {
  local vault_rel="$1"   # path relative to VAULT
  local toolkit_file="$2"
  local canonical_label="$3"

  local src="$VAULT/$vault_rel"
  local dst="$TOOLKIT/$toolkit_file"

  if [[ ! -f "$src" ]]; then
    echo "SKIP (not found): $vault_rel"
    return
  fi

  # Strip leading frontmatter block (---\n...\n---\n) if present
  local content
  content=$(awk '
    BEGIN { in_fm=0; done_fm=0; line=0 }
    {
      line++
      if (line==1 && /^---$/) { in_fm=1; next }
      if (in_fm && /^---$/) { in_fm=0; done_fm=1; next }
      if (!in_fm) print
    }
  ' "$src")

  # Convert [[wikilinks]] to plain text (strip the [[ ]])
  content=$(echo "$content" | sed 's/\[\[\([^]]*\)\]\]/\1/g')

  # Write header + content
  {
    echo "> **Canonical source:** \`$canonical_label\` in vault — last synced $DATE. Edit there; this file is a snapshot for skills and public repo use."
    echo ""
    echo "$content"
  } > "$dst"

  echo "OK: $toolkit_file (synced from $vault_rel)"
}

# Vault-sourced files
strip_and_write \
  "03 Projects/Rubinstein Productions/07 Outreach/01 grants/Say Why — Theory of Change.md" \
  "theory-of-change.md" \
  "03 Projects/Rubinstein Productions/07 Outreach/01 grants/Say Why — Theory of Change.md"

strip_and_write \
  "03 Projects/Rubinstein Productions/05 Methodology/RP Session Facilitation Guide.md" \
  "session-facilitation-guide.md" \
  "03 Projects/Rubinstein Productions/05 Methodology/RP Session Facilitation Guide.md"

strip_and_write \
  "03 Projects/Rubinstein Productions/05 Methodology/Nomadic Indicators Codebook.md" \
  "nomadic-indicators-codebook.md" \
  "03 Projects/Rubinstein Productions/05 Methodology/Nomadic Indicators Codebook.md"

strip_and_write \
  "03 Projects/Rubinstein Productions/07 Outreach/01 grants/Say Why — Evaluation Framework.md" \
  "evaluation-framework.md" \
  "03 Projects/Rubinstein Productions/07 Outreach/01 grants/Say Why — Evaluation Framework.md"

strip_and_write \
  "03 Projects/Rubinstein Productions/05 Methodology/Digital Liver Offering Definition.md" \
  "digital-liver-offering.md" \
  "03 Projects/Rubinstein Productions/05 Methodology/Digital Liver Offering Definition.md"

# enif.md uses the consolidated RP Measurement Framework (replaces full 780-line ENIF)
strip_and_write \
  "03 Projects/Rubinstein Productions/05 Methodology/RP Measurement Framework.md" \
  "enif.md" \
  "03 Projects/Rubinstein Productions/05 Methodology/RP Measurement Framework.md"

echo ""
echo "Done. Toolkit-canonical files (glossary, facilitation-protocol, methodology-blueprint) were NOT touched."
echo "Commit the changes: git add methodology/ && git commit -m 'sync methodology from vault $DATE'"
