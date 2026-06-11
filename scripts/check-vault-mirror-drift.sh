#!/usr/bin/env bash
# check-vault-mirror-drift.sh
#
# Detect vault-style directory names that have leaked into the toolkit repo.
# The toolkit and the Obsidian vault are separate stacks. When automation
# misroutes vault writes into the toolkit, the symptom is a numbered "NN Name"
# directory (e.g. "02 Practice/", "03 Projects/") appearing in toolkit space.
#
# Behavior:
#   - Scan TOOLKIT_ROOT top level and 2 levels deep.
#   - Flag any directory whose basename matches a vault-style pattern.
#   - Honor an exception list (e.g. _archive/).
#   - Print findings to stdout.
#   - Exit 1 on findings, 0 if clean.
#
# When invoked with --emit-vault-report, also write a dated maintenance note to
# the vault on findings. The launchd daily run uses that flag; manual runs and
# the git pre-commit hook do not.
#
# Pure bash. No external deps beyond find/grep/date.

set -euo pipefail

TOOLKIT_ROOT="${TOOLKIT_ROOT:-$HOME/rubinstein-productions-toolkit}"
VAULT_ROOT="${VAULT_ROOT:-/Users/isaacrubinstein/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain}"
MAINTENANCE_DIR="$VAULT_ROOT/00 System/Maintenance"

# Vault-style directory pattern. Format: two digits + space + one of the
# reserved vault top-level names. Any directory matching this in the toolkit
# is, by definition, drift.
VAULT_PATTERN='^[0-9]{2} (Practice|Research|Projects|Canonical|System|Meta|Writing|Career|Archive|Resources|Capture)( |$)'

# Directories that are allowed even if they would otherwise match. _archive/
# was created intentionally on 2026-05-21 to hold the Arena V2 misroute.
EXCEPTION_BASENAMES=(
  "_archive"
)

EMIT_VAULT_REPORT=0
if [ "${1:-}" = "--emit-vault-report" ]; then
  EMIT_VAULT_REPORT=1
fi

if [ ! -d "$TOOLKIT_ROOT" ]; then
  echo "ERROR: toolkit root not found: $TOOLKIT_ROOT" >&2
  exit 2
fi

is_exception() {
  local name="$1"
  for ex in "${EXCEPTION_BASENAMES[@]}"; do
    if [ "$name" = "$ex" ]; then
      return 0
    fi
  done
  return 1
}

# Collect findings. Depth limited to 2 levels below TOOLKIT_ROOT (so the root
# itself plus immediate children plus grandchildren).
FINDINGS=()

while IFS= read -r dir; do
  base=$(basename "$dir")

  # Exception: _archive and anything below it is allowed.
  rel="${dir#$TOOLKIT_ROOT/}"
  top_segment="${rel%%/*}"
  if is_exception "$top_segment"; then
    continue
  fi

  if [[ "$base" =~ $VAULT_PATTERN ]]; then
    FINDINGS+=("$dir")
  fi
done < <(find "$TOOLKIT_ROOT" -mindepth 1 -maxdepth 3 -type d 2>/dev/null)

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
TODAY=$(date '+%Y-%m-%d')

if [ ${#FINDINGS[@]} -eq 0 ]; then
  echo "[$TIMESTAMP] check-vault-mirror-drift: CLEAN ($TOOLKIT_ROOT)"
  exit 0
fi

echo "[$TIMESTAMP] check-vault-mirror-drift: DRIFT DETECTED in $TOOLKIT_ROOT"
echo
echo "Vault-style directories found inside the toolkit:"
for f in "${FINDINGS[@]}"; do
  echo "  - $f"
done
echo
echo "These directory names are reserved for the Obsidian vault:"
echo "  /Users/isaacrubinstein/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/"
echo
echo "Forbidden pattern: $VAULT_PATTERN"
echo "Exception list: ${EXCEPTION_BASENAMES[*]}"
echo
echo "Likely cause: automation wrote vault content into the toolkit (misrouted path)."
echo "Fix: either move the content into the vault, or archive it under _archive/ if obsolete."

if [ "$EMIT_VAULT_REPORT" -eq 1 ]; then
  if [ -d "$MAINTENANCE_DIR" ]; then
    REPORT="$MAINTENANCE_DIR/$TODAY-toolkit-mirror-drift.md"
    {
      echo "---"
      echo "type: reference"
      echo "cssclasses: [reference]"
      echo "date: $TODAY"
      echo "source: check-vault-mirror-drift.sh"
      echo "---"
      echo
      echo "# Toolkit Mirror Drift Detected ($TODAY)"
      echo
      echo "**Detector:** \`~/rubinstein-productions-toolkit/scripts/check-vault-mirror-drift.sh\`"
      echo "**Run at:** $TIMESTAMP"
      echo "**Toolkit root:** \`$TOOLKIT_ROOT\`"
      echo
      echo "## Findings"
      echo
      for f in "${FINDINGS[@]}"; do
        echo "- \`$f\`"
      done
      echo
      echo "## What this means"
      echo
      echo "These directory names are reserved for the Obsidian vault. Their presence inside the toolkit means automation wrote vault content into the wrong repo, the way Arena V2 did on 2026-05-20."
      echo
      echo "## Next action"
      echo
      echo "Identify which automation wrote each path, fix its target, and either move the content into the vault or archive it under \`_archive/\` in the toolkit."
      echo
      echo "## Forbidden patterns enforced"
      echo
      echo "Regex: \`$VAULT_PATTERN\`"
      echo
      echo "Reserved names: Practice, Research, Projects, Canonical, System, Meta, Writing, Career, Archive, Resources, Capture (when prefixed with two-digit number)."
      echo
      echo "Exceptions: \`${EXCEPTION_BASENAMES[*]}\`"
    } > "$REPORT"
    echo
    echo "Wrote vault report: $REPORT"
  else
    echo
    echo "WARNING: maintenance dir not found, skipping vault report: $MAINTENANCE_DIR" >&2
  fi
fi

exit 1
