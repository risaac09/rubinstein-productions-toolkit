#!/usr/bin/env bash
# check-voice.sh [ROOT]: fail on em-dashes (U+2014) and banned jargon verbs in
# ROOT/README.md and ROOT/docs/ (ROOT defaults to the repo). Prints file:line per hit.
set -eu
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
# Verb list from the stop-slop skill, references/phrases.md "Business Jargon".
BANNED='navigat(e|es|ed|ing)|unpack(s|ed|ing)?|lean(s|ed|ing)? into|doubl(e|es|ed|ing) down|circl(e|es|ed|ing) back'
EMD=$(printf '\342\200\224')
cd "$ROOT"
[ -f README.md ] || { echo "no README.md under $ROOT" >&2; exit 2; }
targets="README.md"
[ -d docs ] && targets="$targets docs"
n=0
# shellcheck disable=SC2086
out=$(grep -rnIHi -e "$EMD" -e '&mdash;' -e '&#8212;' -e '&#x2014;' $targets || true)
[ -z "$out" ] || { printf '%s\n' "$out" | sed 's/^\([^:]*:[0-9]*\):.*/\1: em-dash/'; n=1; }
# shellcheck disable=SC2086
out=$(grep -rnIHiE -o -e "\\b($BANNED)\\b" $targets || true)
[ -z "$out" ] || { printf '%s\n' "$out" | sed 's/^\([^:]*:[0-9]*\):\(.*\)/\1: banned verb "\2"/'; n=1; }
[ "$n" -eq 0 ] || { echo "voice check failed" >&2; exit 1; }
echo "voice check clean"
