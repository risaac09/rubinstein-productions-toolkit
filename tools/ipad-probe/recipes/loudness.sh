#!/bin/sh
# loudness.sh: two-pass loudnorm of a file's first audio stream, the way the
# toolkit's delivercheck does it: measure, normalise with the measured values
# asking for linear mode (one gain for the whole file), then measure the
# result again.
#
#   sh loudness.sh IN OUT [I [TP [LRA]]]      defaults -16 LUFS, -1.5 dBTP, 11 LU
#
# OUT ending .wav gets 24-bit PCM. Any other OUT gets AAC 256k with the video
# streams copied untouched. Output is 48 kHz: loudnorm works at 192 kHz and
# emits that rate, so the chain ends with aresample=48000.
# Prints the first-pass measurement, normalization_type (linear, or dynamic
# when loudnorm refused one gain, with the numbers it decided on), and the
# result as measured from OUT. FFMPEG names the command (default ffmpeg).
# Exit: 0 written, 1 ffmpeg failed or nothing measurable, 2 refused.
set -u

FFMPEG="${FFMPEG:-ffmpeg}"

die() { echo "loudness: $*" >&2; exit 2; }
fail() { echo "loudness: $*" >&2; exit 1; }
num() { case "$1" in ''|*[!0-9.-]*) die "$2 must be a number, got '$1'" ;; esac; }
json_field() { sed -n "s/.*\"$1\" *: *\"\([^\"]*\)\".*/\1/p" | tail -n 1; }
measure() { # FILE: loudnorm's measuring pass over the first audio stream, JSON on stdout
  "$FFMPEG" -hide_banner -nostdin -nostats -i "$1" -map 0:a:0 \
    -af "loudnorm=I=$I:TP=$TP:LRA=$LRA:print_format=json" -f null - 2>&1
}

[ $# -ge 2 ] && [ $# -le 5 ] || { echo "usage: sh loudness.sh IN OUT [I [TP [LRA]]]" >&2; exit 2; }
in=$1; out=$2; I=${3:--16}; TP=${4:--1.5}; LRA=${5:-11}
num "$I" I; num "$TP" TP; num "$LRA" LRA
[ -f "$in" ] || die "$in is not a file"
[ ! -e "$out" ] || die "$out exists; nothing is overwritten"
case "$in" in -*) in="./$in" ;; esac
case "$out" in -*) out="./$out" ;; esac

echo "loudness: pass 1 of 3, measuring $in" >&2
first=$(measure "$in") || fail "ffmpeg could not measure $in (no audio stream?): $(printf '%s\n' "$first" | tail -n 1)"
mi=$(printf '%s\n' "$first" | json_field input_i)
mtp=$(printf '%s\n' "$first" | json_field input_tp)
mlra=$(printf '%s\n' "$first" | json_field input_lra)
mth=$(printf '%s\n' "$first" | json_field input_thresh)
off=$(printf '%s\n' "$first" | json_field target_offset)
case "$mi$mtp$mlra$mth$off" in
  *inf*|*nan*) fail "nothing measurable in $in (silent?): input_i '$mi'" ;;
esac
[ -n "$mi" ] && [ -n "$mtp" ] && [ -n "$mlra" ] && [ -n "$mth" ] && [ -n "$off" ] ||
  fail "loudnorm printed no measurement for $in"
echo "measured: I $mi LUFS, TP $mtp dBTP, LRA $mlra LU, threshold $mth LUFS"

case "$out" in
  *.wav|*.WAV) set -- -map 0:a:0 -c:a pcm_s24le ;;
  *) set -- -map '0:v?' -c:v copy -map 0:a:0 -c:a aac -b:a 256k ;;
esac
ln="loudnorm=I=$I:TP=$TP:LRA=$LRA:measured_I=$mi:measured_TP=$mtp:measured_LRA=$mlra"
ln="$ln:measured_thresh=$mth:offset=$off:linear=true:print_format=json,aresample=48000"
echo "loudness: pass 2 of 3, writing $out" >&2
second=$("$FFMPEG" -hide_banner -nostdin -nostats -n -i "$in" "$@" -af "$ln" -ar 48000 "$out" 2>&1) ||
  fail "ffmpeg could not write $out: $(printf '%s\n' "$second" | tail -n 1)"
kind=$(printf '%s\n' "$second" | json_field normalization_type)
echo "normalization_type: ${kind:-unknown}"
if [ "$kind" != linear ]; then
  # loudnorm keeps one gain only when the gained true peak stays under TP and
  # the measured LRA is within the target; show both so the choice is visible.
  awk -v i="$I" -v mi="$mi" -v mtp="$mtp" -v tp="$TP" -v mlra="$mlra" -v lra="$LRA" 'BEGIN {
    g = i - mi
    printf "why: one gain of %+.1f dB puts true peak at %.1f dBTP (TP %s); measured LRA %s LU (target %s)\n", g, mtp + g, tp, mlra, lra
  }'
fi

echo "loudness: pass 3 of 3, measuring $out" >&2
after=$(measure "$out") || fail "ffmpeg could not measure $out"
echo "result: I $(printf '%s\n' "$after" | json_field input_i) LUFS, TP $(printf '%s\n' "$after" | json_field input_tp) dBTP, measured again from $out"
