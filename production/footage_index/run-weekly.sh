#!/bin/bash
# One indexing pass: new and changed clips, then embeddings, then captions. Schedule it with cron or launchd.
# Settings come from the config file (RP_PIPELINE_CONFIG) or FOOTAGE_ROOT; see config.py.
# FOOTAGE_PYTHON names the interpreter that has requirements.txt installed (default: python3).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY="${FOOTAGE_PYTHON:-python3}"
LOG="${FOOTAGE_LOG:-$HOME/.local/share/footage-index/footage-index.log}"
mkdir -p "$(dirname "$LOG")"
say() { echo "$(date '+%Y-%m-%dT%H:%M:%S') weekly: $*" >> "$LOG"; }
OLLAMA="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"

curl -sf -m 5 "$OLLAMA/api/tags" >/dev/null || { say "skipped captions: Ollama not running"; nocap=1; }
cd "$HERE/.." || exit 1  # the folder holding rpresolve/ and footage_index/
say "start"
"$PY" -m footage_index index || say "index exited $?"
"$PY" -m footage_index embed || say "embed exited $?"
[ "${nocap:-0}" = 1 ] || "$PY" -m footage_index caption || say "caption exited $?"
say "done"
