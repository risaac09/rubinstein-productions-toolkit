#!/bin/sh
# proxy.sh: a small H.264 proxy of a clip, for review on a phone or iPad or
# to hand to a cloud session. Long edge 1280 by default, never upscaled.
#
#   sh proxy.sh IN OUT.mp4 [BITRATE [LONG_EDGE]]     defaults 6M and 1280
#
# Encoder: h264_videotoolbox (hardware; rate set with -b:v, the control that
# works on iPadOS), else libx264, else mpeg4. Decoding uses VideoToolbox too
# when ffmpeg lists it under -hwaccels. An encoder that is listed but
# fails is followed by the next one. Output is 8-bit 4:2:0, the first video
# and first audio stream (AAC 160k), with faststart for streaming. Colour tags
# pass through as the source has them, so a V-Log clip makes a V-Log proxy.
# FFMPEG names the command (default ffmpeg).
# Exit: 0 written, 1 every encoder failed, 2 refused (usage, OUT exists).
set -u

FFMPEG="${FFMPEG:-ffmpeg}"
CANDIDATES="h264_videotoolbox libx264 mpeg4"

die() { echo "proxy: $*" >&2; exit 2; }
[ $# -ge 2 ] && [ $# -le 4 ] || { echo "usage: sh proxy.sh IN OUT.mp4 [BITRATE [LONG_EDGE]]" >&2; exit 2; }
in=$1; out=$2; rate=${3:-6M}; edge=${4:-1280}
[ -f "$in" ] || die "$in is not a file"
[ ! -e "$out" ] || die "$out exists; nothing is overwritten"
case "$edge" in ''|*[!0-9]*) die "LONG_EDGE must be a whole number of pixels" ;; esac
case "$rate" in ''|*[!0-9.kKmM]*) die "BITRATE looks like 6M or 2500k" ;; esac
# A leading - would be read as an option.
case "$in" in -*) in="./$in" ;; esac
case "$out" in -*) out="./$out" ;; esac

encoders=$("$FFMPEG" -hide_banner -encoders 2>/dev/null | awk 'NF >= 2 { print $2 }')
# Hardware decode where the build has it: about 3x faster on 5.7K HEVC 10-bit
# (measured on an M-series Mac), and the probe's clip rows show the iPad's figure.
set --
if "$FFMPEG" -hide_banner -hwaccels 2>/dev/null | grep -qx videotoolbox; then set -- -hwaccel videotoolbox; fi
vf="scale=w=min($edge\\,iw):h=min($edge\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p"
for enc in $CANDIDATES; do
  printf '%s\n' "$encoders" | grep -qx "$enc" || continue
  if "$FFMPEG" -hide_banner -nostdin -v error -stats -n "$@" -i "$in" -map 0:v:0 -map '0:a:0?' -vf "$vf" \
      -c:v "$enc" -b:v "$rate" -c:a aac -b:a 160k -movflags +faststart "$out"; then
    echo "proxy: $out ($enc, -b:v $rate, long edge at most $edge${1:+, decoded with videotoolbox})"
    exit 0
  fi
  echo "proxy: $enc failed; trying the next encoder" >&2
  rm -f "$out"   # only ever a partial file of this run: OUT did not exist at the start
done
echo "proxy: no encoder worked (tried those present of: $CANDIDATES)" >&2
exit 1
