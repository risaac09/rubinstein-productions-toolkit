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
# keyframe is skipped. A skipped slot moves the later frames up a cell, so
# the printed times are those of the frames ffmpeg picked (showinfo), and
# cells left over at the end are listed as empty (black). Decoding uses
# VideoToolbox when ffmpeg lists it under -hwaccels.
# FFMPEG and FFPROBE name the commands (defaults ffmpeg, ffprobe).
# Exit: 0 written, 1 ffmpeg failed, 2 refused (usage, OUT exists, OUT inside
# a git work tree).
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
# Outputs never go inside a git work tree: OUT's folder resolved physically,
# with cd here and back, never in a subshell (on a-Shell that moves the shell too).
here=$(pwd -P); case "$out" in */*) g=${out%/*} ;; *) g=. ;; esac
case "$g" in /*) ;; '') g=/ ;; *) g=./$g ;; esac
cd -P "$g" 2>/dev/null || die "the folder for $out does not exist"
g=$(pwd -P); cd "$here" || exit 2
while [ -n "$g" ] && [ ! -e "$g/.git" ]; do g=${g%/*}; done
[ -z "$g" ] && [ ! -e /.git ] || die "$out would land in the git work tree ${g:-/}; write it outside any repository"
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

# showinfo logs each picked frame's pts_time (info level, so the log is kept,
# not shown); -update 1 takes OUT as a name, never a %d frame pattern.
log=$("$FFMPEG" -hide_banner -nostdin -nostats -v info -n "$@" -i "$in" -map 0:v:0 \
  -vf "select='isnan(prev_selected_t)+gt(floor(t/$iv)\\,floor(prev_selected_t/$iv))',showinfo,scale=w=$CELL:h=$CELL:force_original_aspect_ratio=decrease:force_divisible_by=2,tile=${cols}x$rows" \
  -frames:v 1 -update 1 -q:v 3 "$out" 2>&1) || {
  printf '%s\n' "$log" | tail -n 3 >&2; echo "sheet: ffmpeg failed on $in" >&2; exit 1; }

echo "sheet: $out ($cols x $rows, a cell about every ${iv} s$keyframes)"
# Rounded once to tenths, then split, so 59.96 s reads 0:01:00.0, never 0:00:60.0.
printf '%s\n' "$log" | sed -n 's/.*Parsed_showinfo.* pts_time: *\([^ ]*\).*/\1/p' |
  awk -v n="$cells" -v c="$cols" 'NR <= n {
    ds = int($1 * 10 + 0.5); if (ds < 0) ds = 0
    printf "cell %d (row %d, col %d) about %d:%02d:%02d.%d\n", NR, int((NR - 1) / c) + 1, (NR - 1) % c + 1, int(ds / 36000), int(ds / 600) % 60, int(ds / 10) % 60, ds % 10
  }
  END { for (i = (NR < n ? NR : n); i < n; i++) printf "cell %d (row %d, col %d) empty (black): some slots had no frame\n", i + 1, int(i / c) + 1, i % c + 1 }'
