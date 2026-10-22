#!/bin/sh
# sheet.sh: a contact sheet of a clip, one JPEG of evenly spaced frames, to
# send to Claude (images go through a chat; video does not).
#
#   sh sheet.sh IN OUT.jpg [COLS [ROWS]]      defaults 6 x 4
#
# select + scale + tile only, with no drawtext (absent from some builds,
# a-Shell's among them), so the cell times are printed here, one line per
# cell, to paste beside the image. Each cell fits a 320 px box, so a 6 x 4
# sheet is at most 1920 x 1280. The clip is cut into one time slot per cell
# and each cell is the first frame of its slot. When slots are 2 s or longer
# only keyframes are decoded (-skip_frame nokey): many times faster on an
# iPad, each cell is the first keyframe in its slot, and a slot with no
# keyframe is skipped. Decoding uses VideoToolbox when ffmpeg lists it under
# -hwaccels.
# FFMPEG and FFPROBE name the commands (defaults ffmpeg, ffprobe).
# Exit: 0 written, 1 ffmpeg failed, 2 refused (usage, OUT exists).
set -u

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
CELL=320
KEYFRAMES_FROM_S=2

die() { echo "sheet: $*" >&2; exit 2; }
[ $# -ge 2 ] && [ $# -le 4 ] || { echo "usage: sh sheet.sh IN OUT.jpg [COLS [ROWS]]" >&2; exit 2; }
in=$1; out=$2; cols=${3:-6}; rows=${4:-4}
for v in "$cols" "$rows"; do
  case "$v" in ''|*[!0-9]*|0) die "COLS and ROWS must be whole numbers from 1" ;; esac
done
[ -f "$in" ] || die "$in is not a file"
[ ! -e "$out" ] || die "$out exists; nothing is overwritten"
case "$in" in -*) in="./$in" ;; esac
case "$out" in -*) out="./$out" ;; esac
cells=$((cols * rows))

dur=$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$in" 2>/dev/null)
case "$dur" in
  ''|N/A|*[!0-9.]*) iv=10; echo "sheet: duration unreadable; one cell every 10 s" >&2 ;;
  # One cell per slot of duration/cells: the first frame in each slot, so frame
  # spacing cannot drift the picks; a hair under, so the last slot starts in time.
  *) iv=$(awk -v d="$dur" -v n="$cells" 'BEGIN { x = d / n - 0.0005; if (x < 0.001) x = 0.001; printf "%.4f", x }') ;;
esac
set --
if awk -v iv="$iv" -v k="$KEYFRAMES_FROM_S" 'BEGIN { exit !(iv >= k) }'; then set -- -skip_frame nokey; fi
keyframes=${1:+, keyframes only}
if "$FFMPEG" -hide_banner -hwaccels 2>/dev/null | grep -qx videotoolbox; then set -- "$@" -hwaccel videotoolbox; fi

"$FFMPEG" -hide_banner -nostdin -v error -stats -n "$@" -i "$in" -map 0:v:0 \
  -vf "select='isnan(prev_selected_t)+gt(floor(t/$iv)\\,floor(prev_selected_t/$iv))',scale=w=$CELL:h=$CELL:force_original_aspect_ratio=decrease:force_divisible_by=2,tile=${cols}x$rows" \
  -frames:v 1 -q:v 3 "$out" || { echo "sheet: ffmpeg failed on $in" >&2; exit 1; }

echo "sheet: $out ($cols x $rows, a cell about every ${iv} s$keyframes)"
awk -v n="$cells" -v iv="$iv" -v c="$cols" 'BEGIN {
  for (i = 0; i < n; i++) {
    t = i * iv
    printf "cell %d (row %d, col %d) about %d:%02d:%04.1f\n", i + 1, int(i / c) + 1, i % c + 1, int(t / 3600), int((t % 3600) / 60), t % 60
  }
}'
