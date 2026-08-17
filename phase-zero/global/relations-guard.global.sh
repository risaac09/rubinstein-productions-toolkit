#!/usr/bin/env bash
# relations-guard — PreToolUse hook on Read|Grep|Glob.
#
# Denies reads targeting the closed 05_RELATIONS/partner folder in the vault.
# Enforces the privacy law in isaac-twin SKILL.md and 06_relational_field.md
# mechanically: that folder's contents may only be opened when a task
# explicitly requires it and Isaac has explicitly opened it, and even then
# never beyond what the task requires. No PreToolUse hook watched Read/Grep/
# Glob before this (only Edit/Write/NotebookEdit did), so a Workflow subagent
# doing a vault-wide sweep at HIGH/MAX never loaded the twin and had nothing
# stopping it from reading the closed folder. Confirmed 2026-08-16 (actor-
# layer adversarial review, F03).
#
# Override: set RELATIONS_GUARD_OPEN=1 for a session where Isaac has
# explicitly opened the folder for a specific task. Per-session only; never
# set it in a persistent shell profile or a launchd job's environment.
#
# Known gap: only catches an explicit file_path/path argument containing
# 05_RELATIONS. A Grep or Glob call with no path argument, run from a cwd
# already inside the folder, is not caught here; the auto-mode classifier
# and the twin's own privacy-law prose are the layers for that. Fail-open on
# jq errors: a broken detector must not brick reads.

set -u

MATCH="05_RELATIONS"

[ "${RELATIONS_GUARD_OPEN:-0}" = "1" ] && exit 0

input=$(cat)
target=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)

case "$target" in
  *"$MATCH"*) ;;
  *) exit 0 ;;
esac

jq -nc --arg t "$target" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: ("05_RELATIONS is closed by the privacy law in isaac-twin SKILL.md and 06_relational_field.md: read only what a task explicitly requires and Isaac has explicitly opened. Denied path: " + $t + ". If this task is one of those explicit exceptions, set RELATIONS_GUARD_OPEN=1 for this session and retry.")
  }
}'
