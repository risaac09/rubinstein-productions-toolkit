#!/usr/bin/env bash
# engage-log.global — user-level SessionEnd hook (OPS-10): one sd-ai-engage
# fragment per session, written to a spool, never landed from here.
#
# Why a spool and not `sd-ai-engage --land`: on 2026-10-31 many builders each
# landed their own fragment; every sd-land push re-ran the full validate gate,
# so cost grew as O(N^2) and fragments stranded when the gate timed out
# (data/incidents/inc-20261031-sd-land-contention.json). This hook only writes
# a file under $SD_ENGAGE_SPOOL. scripts/sd-engage-batch-land, run nightly by
# one job, lands the whole spool in a single push.
#
# What it records, all read from the transcript, nothing invented:
#   pool claude, model (last assistant message), tokens (in/out/cacheRead/
#   cacheWrite, one count per message id as sd-token-ledger does), codexHandoff
#   (true when the session ran `sd-agent-sync handoff <from> codex ...`),
#   basisSource "reconstructed". costUsd is left absent: Claude here is a
#   subscription pool with no metered dollar figure. routedTier and secondRound
#   are the session's own judgments and are not guessed.
#
# Limits, by design: the row is a snapshot at the first SessionEnd (a resumed
# session is not logged twice, so later tokens are missed); model is the last
# assistant message's, so a mixed-model session reads as one; subagent sidechains
# are not in the parent transcript and are not counted. The row says
# basisSource reconstructed for these reasons.
#
# Fast and safe: no network, no git writes (one local read of origin/main), no validate. Any error is logged to
# $SD_ENGAGE_SPOOL/hook.log and the hook exits 0, so it can never fail a wrap.
# Needs jq; silent without it.
#
# Source of truth: stack-data/kit/phase-zero/global/. Installed by
# install-global.sh; edit the source and redeploy.

set -uo pipefail
SPOOL="${SD_ENGAGE_SPOOL:-$HOME/.local/share/stack-data/engage-spool}"
SD="${STACK_DATA_DIR:-$HOME/stack-data}"

log() { mkdir -p "$SPOOL" 2>/dev/null && printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$SPOOL/hook.log" 2>/dev/null; return 0; }

run() {
  command -v jq >/dev/null 2>&1 || return 0
  local input sid tp reason
  input="$(cat 2>/dev/null || true)"
  sid="$(printf '%s' "$input" | jq -r '.session_id // ""')"
  tp="$(printf '%s' "$input" | jq -r '.transcript_path // ""')"
  reason="$(printf '%s' "$input" | jq -r '.reason // ""')"
  [ -n "$sid" ] && [ -f "$tp" ] || { log "skip: no session_id or transcript"; return 0; }
  mkdir -p "$SPOOL" || return 0
  # One row per session: skip if the spool or the clone's log already has it.
  grep -qsF "\"$sid\"" "$SPOOL"/aie-*.json "$SPOOL"/rejected/aie-*.json "$SD"/data/ai-engagement-log/*.json 2>/dev/null && return 0
  # The clone's working tree lags main when it is on a branch or was not pulled after a land; main's log is a local read.
  git -C "$SD" grep -q -F -e "\"$sid\"" origin/main -- data/ai-engagement-log 2>/dev/null && return 0

  local facts
  facts="$(jq -nR '
    reduce (inputs | fromjson? | select(type == "object")) as $l ({m: {}, model: null, codex: false};
      if $l.type == "assistant" and ($l.message.usage | type) == "object" then
        .m[$l.message.id // ($l.uuid // "x")] |= (if . == null or ($l.message.usage.output_tokens // 0) > (.output_tokens // 0) then $l.message.usage else . end)
        | (if ($l.message.model // "") | (. != "" and . != "<synthetic>") then .model = $l.message.model else . end)
        | (if any($l.message.content[]?; .type == "tool_use" and ((.input.command? // "") | tostring | test("sd-agent-sync handoff +[a-z]+ +codex"))) then .codex = true else . end)
      else . end)
    | {model, codex, u: [.m[]]}
    | {model, codex,
       tokens: {in: ([.u[].input_tokens // 0] | add // 0), out: ([.u[].output_tokens // 0] | add // 0),
                cacheRead: ([.u[].cache_read_input_tokens // 0] | add // 0), cacheWrite: ([.u[].cache_creation_input_tokens // 0] | add // 0)},
       n: (.u | length)}' "$tp")" || { log "jq failed on $tp"; return 0; }
  # A session that never produced output (an auth failure, an aborted start) is not an engagement.
  [ "$(printf '%s' "$facts" | jq '.n > 0 and .tokens.out > 0')" = true ] || return 0

  local slug date now id frag
  slug="$(printf '%s' "$facts" | jq -r '.model // ""' | sed -E 's/^claude-//; s/-[0-9]{8}$//; s/-([0-9]+)-([0-9]+)$/-\1.\2/')"
  date="$(date -u +%F)"; now="$(date -u +%FT%TZ)"
  id="aie-$(printf '%s' "$date" | tr -d -)-$(LC_ALL=C head -c 256 /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | cut -c1-4)"
  frag="$SPOOL/$id.json"
  printf '%s' "$facts" | jq --arg id "$id" --arg created "$now" --arg date "$date" --arg sid "$sid" \
      --arg model "$slug" --arg reason "$reason" '
    {id: $id, created: $created, date: $date, session: $sid, direction: "west", pool: "claude"}
    + (if $model != "" then {model: $model} else {} end)
    + {tokens, codexHandoff: .codex, basisSource: "reconstructed",
       note: ("wrap hook (SessionEnd" + (if $reason != "" then ": " + $reason else "" end) + "); no routing judgment recorded"),
       tags: [], items: []}' > "$frag.tmp" || { rm -f "$frag.tmp"; log "write failed for $sid"; return 0; }
  # Hold the row to the repo contract when a clone is here. A model slug the
  # contract refuses is dropped on its own (the key is optional); a row that
  # still fails is dropped rather than left to fail the batch lander.
  if [ -f "$SD/scripts/lib/engage-keys-contract.jq" ] && ! jq -e -f "$SD/scripts/lib/engage-keys-contract.jq" "$frag.tmp" >/dev/null 2>&1; then
    jq 'del(.model)' "$frag.tmp" > "$frag.tmp2" && mv "$frag.tmp2" "$frag.tmp"
  fi
  if [ -f "$SD/scripts/lib/engage-keys-contract.jq" ] && ! jq -e -f "$SD/scripts/lib/engage-keys-contract.jq" "$frag.tmp" >/dev/null 2>&1; then
    rm -f "$frag.tmp"; log "contract refused row for $sid"; return 0
  fi
  [ ! -e "$frag" ] || { rm -f "$frag.tmp"; log "id collision for $sid"; return 0; }
  mv "$frag.tmp" "$frag"
}

run || log "hook error (exit $?)"
exit 0
