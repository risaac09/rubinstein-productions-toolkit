#!/bin/sh
# loudness.sh: two-pass loudnorm of a file's first audio stream, the way the
# toolkit's delivercheck does it: measure, normalise with the measured values
# asking for linear mode (one gain for the whole file), then measure the
# result again.
#
#   sh loudness.sh IN OUT [I [TP [LRA]]]      defaults -16 LUFS, -1.5 dBTP, 11 LU
#
# With LRA left out, the target range is lifted to the measured LRA + 1 (at
# most 50) as delivercheck does, so a wide-range source can keep one gain
# instead of being compressed. An LRA given here is used as it is.
# OUT ending .wav gets 24-bit PCM. Any other OUT gets AAC 256k with the video
# streams copied untouched. Output is 48 kHz: loudnorm works at 192 kHz and
# emits that rate, so the chain ends with aresample=48000.
# Prints the first-pass measurement, normalization_type (linear, or dynamic
# when loudnorm refused one gain, with the numbers it decided on), and the
# result as measured from OUT, with a note when its true peak is over TP (a
# lossy encode adds peaks). FFMPEG names the command (default ffmpeg).
# Exit: 0 written, 1 ffmpeg failed or nothing measurable, 2 refused (usage,
# OUT exists, OUT inside a git work tree).
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
in=$1; out=$2; I=${3:--16}; TP=${4:--1.5}; LRA=${5:-11}; lra_given=${5:+y}
num "$I" I; num "$TP" TP; num "$LRA" LRA
[ -f "$in" ] || die "$in is not a file"
[ ! -e "$out" ] || die "$out exists; nothing is overwritten"
case "$in" in -*) in="./$in" ;; esac
case "$out" in -*) out="./$out" ;; esac
# Outputs never go inside a git work tree: OUT's folder resolved physically,
# with cd here and back, never in a subshell (on a-Shell that moves the shell too).
here=$(pwd -P); case "$out" in */*) g=${out%/*} ;; *) g=. ;; esac
case "$g" in /*) ;; '') g=/ ;; *) g=./$g ;; esac
cd -P "$g" 2>/dev/null || die "the folder for $out does not exist"
g=$(pwd -P); cd "$here" || exit 2
while [ -n "$g" ] && [ ! -e "$g/.git" ]; do g=${g%/*}; done
[ -z "$g" ] && [ ! -e /.git ] || die "$out would land in the git work tree ${g:-/}; write it outside any repository"

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
if [ -z "$lra_given" ]; then
  LRA=$(awk -v l="$LRA" -v m="$mlra" 'BEGIN { x = m + 1; if (x < l) x = l; if (x > 50) x = 50; print x }')
  [ "$LRA" = 11 ] || echo "target LRA $LRA LU: the measured LRA + 1, as delivercheck sets it, so one gain can keep the range"
fi

case "$out" in
  *.wav|*.WAV) set -- -map 0:a:0 -c:a pcm_s24le ;;
  *) set -- -map '0:v?' -c:v copy -map 0:a:0 -c:a aac -b:a 256k ;;
esac
ln="loudnorm=I=$I:TP=$TP:LRA=$LRA:measured_I=$mi:measured_TP=$mtp:measured_LRA=$mlra"
ln="$ln:measured_thresh=$mth:offset=$off:linear=true:print_format=json,aresample=48000"
echo "loudness: pass 2 of 3, writing $out" >&2
# On failure OUT can only be this run's partial file (it did not exist at the start).
second=$("$FFMPEG" -hide_banner -nostdin -nostats -n -i "$in" "$@" -af "$ln" -ar 48000 "$out" 2>&1) ||
  { rm -f "$out"; fail "ffmpeg could not write $out: $(printf '%s\n' "$second" | tail -n 1)"; }
kind=$(printf '%s\n' "$second" | json_field normalization_type)
echo "normalization_type: ${kind:-unknown}"
if [ "$kind" != linear ]; then
  # loudnorm keeps one gain only when the measured LRA is not 0 and the
  # measured threshold not -70 (a steady tone or near silence fails these
  # first), then only when the gained true peak stays under TP and the
  # measured LRA is within the target; show the numbers so the choice is visible.
  awk -v i="$I" -v mi="$mi" -v mtp="$mtp" -v tp="$TP" -v mlra="$mlra" -v lra="$LRA" -v mth="$mth" 'BEGIN {
    g = i - mi
    if (mlra + 0 == 0 || mth + 0 == -70)
      printf "why: loudnorm uses one gain only when the measured LRA is above 0 LU and the threshold above -70 LUFS; measured LRA %s LU, threshold %s LUFS (a steady signal such as a tone measures LRA 0)\n", mlra, mth
    else
      printf "why: one gain of %+.1f dB puts true peak at %.1f dBTP (TP %s); measured LRA %s LU (target %s)\n", g, mtp + g, tp, mlra, lra
  }'
fi

echo "loudness: pass 3 of 3, measuring $out" >&2
after=$(measure "$out") || fail "ffmpeg could not measure $out"
rtp=$(printf '%s\n' "$after" | json_field input_tp)
echo "result: I $(printf '%s\n' "$after" | json_field input_i) LUFS, TP $rtp dBTP, measured again from $out"
# 0.05 dB of slack: loudnorm's own reading of a PCM result wobbles by about 0.01 dB.
if awk -v r="$rtp" -v t="$TP" 'BEGIN { exit !(r != "" && r + 0 > t + 0.05) }'; then
  echo "note: true peak $rtp dBTP is over TP $TP (lossy encoding adds peaks); for a hard ceiling run again with a lower TP"
fi
