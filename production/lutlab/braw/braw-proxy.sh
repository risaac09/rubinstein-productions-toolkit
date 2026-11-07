#!/bin/bash
# braw-proxy.sh OUT_DIR clip.braw...: one ProRes LT proxy per clip, 1920 wide, in
# the shape the SDK decodes (a flagged anamorphic comes out desqueezed, which is
# the shape Resolve expects of a proxy for it). Film Gen 5, so it matches the
# source. Timecode comes from the clip; there is no sound, so Resolve keeps the
# source's. Done means both the proxy's and the decoder's frame counts matched: a
# clip already done is skipped, a failed one is removed, exit 1.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
BRAW2PPM="${BRAW2PPM:-$HERE/braw2ppm}"
LANES="${BRAW_PROXY_LANES:-2}"
[ $# -ge 2 ] || { echo "usage: braw-proxy.sh OUT_DIR clip.braw..." >&2; exit 2; }
OUT=$1; shift
mkdir -p "$OUT"

frames_of() { ffprobe -v error -count_packets -select_streams v:0 -show_entries stream=nb_read_packets -of csv=p=0 "$1" | head -1; }

one() {
  local clip=$1 base dst part err tc want got rate play size w h decoded expect
  base=$(basename "$clip" .braw); dst="$OUT/$base.mov"; part="$OUT/.$base.part.mov"; err="$OUT/.$base.err"
  [ -s "$dst" ] && { echo "skip $base"; return 0; }
  tc=$(ffprobe -v error -show_entries stream_tags=timecode:format_tags=timecode -of csv=p=0 "$clip" 2>/dev/null | grep -m1 -E '^[0-9]{2}:')
  # An off-speed clip (24 fps in a 29.97 project) holds fewer frames than the SDK's
  # count. Resolve reads the frames one for one against that count, so the proxy
  # does too, frame n for frame n, holding the last frame to the end. Retiming it
  # to real time put it seconds out of sync. braw2ppm exits 5 at the real end.
  "$BRAW2PPM" "$clip" 0 1 /dev/null 2>"$err"
  want=$(sed -nE 's/^rate .* frames ([0-9]+) .*/\1/p' "$err" | head -1)
  rate=$(exiftool -s3 -VideoFrameRate "$clip" 2>/dev/null); play=$(sed -nE 's/^rate ([0-9.]+) .*/\1/p' "$err" | head -1); rate=${rate:-$play}
  [ -n "$want" ] || { echo "FAIL $base: no frame count" >&2; return 1; }
  "$BRAW2PPM" "$clip" 0 1000000 - 2>"$err" | tee >(wc -c > "$err.bytes") | ffmpeg -hide_banner -loglevel error -y \
    -f ppm_pipe -framerate "$play" -i - \
    -vf "tpad=stop_mode=clone:stop_duration=3600,scale=1920:-2:flags=bicubic,format=yuv422p10le" \
    -frames:v "$want" -c:v prores_ks -profile:v 1 -vendor apl0 ${tc:+-timecode "$tc"} "$part"
  sleep 1
  size=$(sed -nE 's/.*decoded ([0-9]+)x([0-9]+).*/\1 \2/p' "$err" | head -1); read -r w h <<<"$size"
  decoded=$(( $(cat "$err.bytes") / (w * h * 6 + 19) )); expect=$(awk -v n="$want" -v r="$rate" -v p="$play" 'BEGIN{printf "%d", n*r/p}')
  got=$(frames_of "$part")
  if [ "$want" = "$got" ] && [ $((decoded - expect)) -ge -2 ] && [ $((decoded - expect)) -le 2 ]; then
    mv "$part" "$dst"; rm -f "$err" "$err.bytes"; echo "ok $base $got frames from $decoded at $rate"
  else
    rm -f "$part"; echo "FAIL $base: $got of $want frames, decoded $decoded of about $expect" >&2; return 1
  fi
}
export -f one frames_of; export OUT BRAW2PPM
# shellcheck disable=SC2016  # $1 belongs to the inner bash
printf '%s\0' "$@" | xargs -0 -P "$LANES" -I{} bash -c 'one "$1"' _ {}
