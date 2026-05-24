#!/usr/bin/env bash
# load-config.sh
#
# Adapter: configs/{mode}.yaml -> BadWords settings.json.
#
# Reads one of polish/witness/selective from this dir's configs/, extracts
# the badwords_settings block, and merges it into BadWords' settings.json
# preserving keys the YAML doesn't override. Atomic write via tmpfile + mv.
#
# Usage:
#   load-config.sh <mode>                       # write
#   load-config.sh <mode> --dry-run             # print merged JSON, no write
#   load-config.sh <mode> --badwords-dir <path> # override install dir
#
# Modes: polish | witness | selective
#
# Default BadWords dir: $BADWORDS_DIR or ~/Downloads/BadWords-main
# Settings.json lives at <badwords-dir>/src/settings.json per osdoc.py:102
# (self-contained install pattern; install_dir = dirname(osdoc.py)).
#
# Deps: python3 with PyYAML (stdlib), jq. Both shipped or pre-installed on
# Isaac's Mac.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIGS_DIR="$SCRIPT_DIR/configs"

BADWORDS_DIR="${BADWORDS_DIR:-$HOME/Downloads/BadWords-main}"
DRY_RUN=0
MODE=""

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)        DRY_RUN=1; shift ;;
    --badwords-dir)   BADWORDS_DIR="$2"; shift 2 ;;
    -h|--help)        usage ;;
    polish|witness|selective)
                      MODE="$1"; shift ;;
    *)                echo "error: unknown arg: $1" >&2; usage ;;
  esac
done

[ -n "$MODE" ] || { echo "error: mode required (polish|witness|selective)" >&2; usage; }

YAML="$CONFIGS_DIR/$MODE.yaml"
[ -f "$YAML" ] || { echo "error: config not found: $YAML" >&2; exit 1; }

SETTINGS_FILE="$BADWORDS_DIR/src/settings.json"
SETTINGS_DIR="$(dirname "$SETTINGS_FILE")"

if [ ! -d "$SETTINGS_DIR" ]; then
  echo "error: BadWords src dir not found: $SETTINGS_DIR" >&2
  echo "       (looked for install at: $BADWORDS_DIR)" >&2
  echo "       override with --badwords-dir or BADWORDS_DIR env var" >&2
  exit 1
fi

command -v jq >/dev/null    || { echo "error: jq not on PATH" >&2; exit 1; }
command -v python3 >/dev/null || { echo "error: python3 not on PATH" >&2; exit 1; }

# Extract badwords_settings as JSON. Fail loudly on missing block.
OVERRIDES_JSON="$(python3 - "$YAML" <<'PY'
import sys, json, yaml
with open(sys.argv[1]) as f:
    doc = yaml.safe_load(f)
if not isinstance(doc, dict) or 'badwords_settings' not in doc:
    sys.stderr.write("error: no badwords_settings block in YAML\n")
    sys.exit(2)
json.dump(doc['badwords_settings'], sys.stdout)
PY
)"

# Read existing settings.json or start from empty object. BadWords self-heals
# missing keys against DEFAULT_SETTINGS on load (osdoc.py:260), so an empty
# base is safe: deep-merge fills the rest on next launch.
if [ -f "$SETTINGS_FILE" ]; then
  BASE_JSON="$(cat "$SETTINGS_FILE")"
else
  BASE_JSON="{}"
fi

# Shallow merge: overrides win. Sufficient for our YAML schema (all values
# are scalars or flat lists; no nested dicts to deep-merge).
MERGED="$(jq -n \
  --argjson base "$BASE_JSON" \
  --argjson overrides "$OVERRIDES_JSON" \
  '$base * $overrides')"

if [ "$DRY_RUN" = "1" ]; then
  echo "$MERGED" | jq .
  exit 0
fi

TMP="$(mktemp -t badwords-settings.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
echo "$MERGED" | jq . > "$TMP"
mv "$TMP" "$SETTINGS_FILE"

echo "wrote $MODE config to $SETTINGS_FILE"
