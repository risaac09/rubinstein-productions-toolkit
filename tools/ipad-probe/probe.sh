#!/bin/sh
# probe.sh: what this device's ffmpeg, ffprobe, python3 and shell can do,
# printed as one Markdown table to paste into a chat with Claude.
#
#   sh probe.sh               probe and print the table (well under a minute)
#   sh probe.sh CLIP          also read CLIP's first video stream and time
#                             5 s of its decode, in software and VideoToolbox
#   sh probe.sh --self-test   check the probe's own parsers; needs no ffmpeg
#
# Written for a-Shell on the iPad (dash, no bash), and runs the same under
# macOS sh and Linux dash. FFMPEG, FFPROBE and PYTHON name the commands it
# calls (defaults ffmpeg, ffprobe, python3). Scratch files go in a new folder
# under TMPDIR and are removed at exit; nothing else is written.
#
# Row kinds: version, build, filter and encoder (present or absent in
# ffmpeg's own -filters and -encoders lists), smoke (a 2 s lavfi job: pass,
# fail or skipped), python (module present or absent, and whether python's
# subprocess can run ffprobe), tool (the shell commands the recipes use,
# each run once on a known input: works or missing), clip.
# Exit: 0 once the table is printed (absent rows are findings, not errors),
# 1 when --self-test fails, 2 on a usage error.
set -u

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
PYTHON="${PYTHON:-python3}"

FILTERS="lut3d lut1d haldclut zscale colorspace tonemap ebur128 loudnorm afftdn adeclick sidechaincompress acrossfade silencedetect scdet signalstats waveform vectorscope subtitles drawtext"
ENCODERS="h264_videotoolbox hevc_videotoolbox libx264 libx265 libkvazaar libsvtav1 prores_ks aac"
PY_MODULES="numpy scipy PIL cv2"
SMOKE_FRAMES=50   # testsrc2 at 25 fps for 2 s
ABC_SHA256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

usage() { echo "usage: sh probe.sh [CLIP] | sh probe.sh --self-test" >&2; exit 2; }

work="${TMPDIR:-/tmp}"
work="${work%/}/ipad-probe.$$"
mkdir "$work" || { echo "probe: cannot make $work (set TMPDIR to a writable folder)" >&2; exit 2; }
trap 'rm -rf "$work"' EXIT
# A signal must run the EXIT trap too; plain sh does not do that on its own.
trap 'exit 1' INT TERM HUP

# --- parsers (the self-test pins each on canned text) ------------------------

# Names from `ffmpeg -filters` or `-encoders`, one per line: the second field.
# Old builds print three flag columns (TSC), new ones two; the name is $2 in both.
names_of() { awk 'NF >= 2 { print $2 }'; }
listed() { grep -qx -- "$1" "$2" 2>/dev/null; }
# The value of "key" : "value" in loudnorm's JSON block (the last one printed).
json_field() { sed -n "s/.*\"$1\" *: *\"\([^\"]*\)\".*/\1/p" | tail -n 1; }
# Integrated loudness from ebur128's summary (the line that starts with I:).
ebur_i() { sed -n 's/^ *I: *\([-0-9.inf]*\) LUFS.*/\1/p' | tail -n 1; }
# The time of every cut scdet logged.
scdet_times() { sed -n 's/.*lavfi\.scd\.score: *[0-9.]*, lavfi\.scd\.time: *\([0-9.]*\).*/\1/p'; }
# The last speed= ffmpeg's progress line printed (progress lines end in CR).
speed_of() { tr '\r' '\n' | sed -n 's/.*speed= *\([0-9.]*x\).*/\1/p' | tail -n 1; }
# A path made safe inside a quoted filter option: backslash and colon escaped.
# shortcut: a path holding a single quote still breaks it; TMPDIR never has one.
fesc() { printf '%s' "$1" | sed 's/[\\:]/\\&/g'; }
# A 2-point identity .cube (red varies fastest, as the format requires).
write_cube() {
  printf 'LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n' > "$1"
}
# Is A within [LO, HI]? (decimals, so awk)
between() { awk -v a="$1" -v lo="$2" -v hi="$3" 'BEGIN { exit !(a != "" && a + 0 >= lo && a + 0 <= hi) }'; }

self_test() {
  n=0
  bad=0
  check() { # NAME GOT WANT
    n=$((n + 1))
    if [ "$2" != "$3" ]; then
      echo "self-test: FAIL $1: got '$2', want '$3'" >&2
      bad=$((bad + 1))
    fi
  }
  printf '%s\n' 'Filters:' '  T.. = Timeline support' '  ------' \
    ' TS lut3d             V->V       Adjust colors using a 3D LUT.' \
    ' .. lut3d_extra       V->V       Not a filter, only a longer name.' \
    ' TSC loudnorm         A->A       EBU R128 loudness normalization' > "$work/f"
  names_of < "$work/f" > "$work/fn"
  check "name in the two-flag layout" "$(listed lut3d "$work/fn" && echo y)" y
  check "name in the three-flag layout" "$(listed loudnorm "$work/fn" && echo y)" y
  check "a prefix of a longer name is absent" "$(listed lut3d_ext "$work/fn" || echo n)" n
  check "an unlisted name is absent" "$(listed zscale "$work/fn" || echo n)" n
  printf '%s\n' 'Encoders:' ' V..... = Video' ' ------' \
    ' V....D libx264              libx264 H.264 / AVC' \
    ' A....D aac                  AAC (Advanced Audio Coding)' | names_of > "$work/en"
  check "encoder listed" "$(listed libx264 "$work/en" && echo y)" y
  check "encoder unlisted" "$(listed libx265 "$work/en" || echo n)" n

  write_cube "$work/c.cube"
  check "cube line count" "$(wc -l < "$work/c.cube" | tr -d ' ')" 9
  check "cube header" "$(sed -n 1p "$work/c.cube")" "LUT_3D_SIZE 2"
  check "cube red varies fastest" "$(sed -n 3p "$work/c.cube")" "1 0 0"
  check "cube last node" "$(sed -n 9p "$work/c.cube")" "1 1 1"

  printf '%s\n' '[Parsed_loudnorm_0 @ 0x1] ' '{' '	"input_i" : "-23.54",' \
    '	"input_tp" : "-7.10",' '	"normalization_type" : "dynamic",' '	"target_offset" : "0.05"' '}' > "$work/j"
  check "loudnorm input_i" "$(json_field input_i < "$work/j")" "-23.54"
  check "loudnorm normalization_type" "$(json_field normalization_type < "$work/j")" dynamic
  check "loudnorm missing key" "$(json_field output_i < "$work/j")" ""

  printf '%s\n' '[Parsed_ebur128_0 @ 0x1] t: 1.9  TARGET:-23 LUFS  M: -21.1 S:-120.7  I: -70.0 LUFS  LRA: 0.0 LU' \
    '[Parsed_ebur128_0 @ 0x1] Summary:' '' '  Integrated loudness:' '    I:         -21.1 LUFS' \
    '    Threshold: -31.1 LUFS' > "$work/e"
  check "ebur128 summary, not the per-frame line" "$(ebur_i < "$work/e")" "-21.1"

  printf '%s\n' '[Parsed_scdet_1 @ 0x1] lavfi.scd.score: 19.113, lavfi.scd.time: 1' \
    '[Parsed_scdet_1 @ 0x1] lavfi.scd.score: 31.5, lavfi.scd.time: 2.48' > "$work/s"
  check "scdet times" "$(scdet_times < "$work/s" | tr '\n' ' ')" "1 2.48 "

  printf 'frame=   10 speed=1.5x \rframe=   20 speed=2.25x \r\n' > "$work/p"
  check "last progress speed" "$(speed_of < "$work/p")" "2.25x"
  check "filter path escaping" "$(fesc 'a b:c\d')" 'a b\:c\\d'
  check "between, inside" "$(between 1.0 0.9 1.1 && echo y)" y
  check "between, outside" "$(between 1.2 0.9 1.1 || echo n)" n
  check "between, empty" "$(between '' 0 1 || echo n)" n

  if [ "$bad" -gt 0 ]; then
    echo "self-test: $bad of $n checks failed" >&2
    return 1
  fi
  echo "self-test: ok ($n checks)"
}

# --- the table ---------------------------------------------------------------

f_present=0; e_present=0; s_pass=0; s_run=0; p_present=0
row() { # KIND NAME RESULT [DETAIL]
  case "$1:$3" in
    filter:present) f_present=$((f_present + 1)) ;;
    encoder:present) e_present=$((e_present + 1)) ;;
    smoke:pass) s_pass=$((s_pass + 1)); s_run=$((s_run + 1)) ;;
    smoke:fail) s_run=$((s_run + 1)) ;;
    python:present) p_present=$((p_present + 1)) ;;
  esac
  printf '| %s | %s | %s | %s |\n' "$1" "$2" "$3" "$(printf '%s' "${4:-}" | tr '|\n\r' '/  ')"
}
ff() { "$FFMPEG" -hide_banner -nostdin "$@"; }
last_err() { tail -n 1 "$work/err" 2>/dev/null; }

smoke_rows() {
  if ! ff -v error -f lavfi -i "testsrc2=s=64x36:r=25:d=0.2" -f null - 2> "$work/err"; then
    row smoke lavfi fail "$(last_err)"
    for s in lut3d loudnorm ebur128 scdet ffprobe h264_videotoolbox; do
      row smoke "$s" skipped "no lavfi test sources in this build"
    done
    return
  fi
  row smoke lavfi pass "testsrc2 and sine sources work"

  if listed lut3d "$work/filters"; then
    write_cube "$work/id.cube"
    if ff -v error -f lavfi -i "testsrc2=s=320x180:r=25:d=2" \
        -vf "lut3d=file='$(fesc "$work/id.cube")'" -f framecrc - > "$work/crc" 2> "$work/err"; then
      frames=$(grep -vc '^#' "$work/crc")
      if [ "$frames" -eq "$SMOKE_FRAMES" ]; then
        row smoke lut3d pass "2 s through an identity .cube written with printf, $frames frames"
      else
        row smoke lut3d fail "$frames frames, want $SMOKE_FRAMES"
      fi
    else
      row smoke lut3d fail "$(last_err)"
    fi
  else
    row smoke lut3d skipped "filter absent"
  fi

  if listed loudnorm "$work/filters"; then
    ff -f lavfi -i "sine=f=1000:r=48000:d=2" -af loudnorm=print_format=json \
      -c:a pcm_s16le "$work/ln.wav" 2> "$work/err"
    li=$(json_field input_i < "$work/err")
    rate=$("$FFPROBE" -v error -show_entries stream=sample_rate -of csv=p=0 "$work/ln.wav" 2>/dev/null)
    case "$li" in
      ""|*inf*) row smoke loudnorm fail "no JSON measurement: $(last_err)" ;;
      *) row smoke loudnorm pass "input_i $li LUFS; output ${rate:-unknown} Hz, so end every chain with aresample=48000" ;;
    esac
  else
    row smoke loudnorm skipped "filter absent"
  fi

  if listed ebur128 "$work/filters"; then
    ff -f lavfi -i "sine=f=1000:r=48000:d=2" -af ebur128 -f null - 2> "$work/err"
    ei=$(ebur_i < "$work/err")
    if between "$ei" -40 0; then row smoke ebur128 pass "I $ei LUFS"; else row smoke ebur128 fail "no summary: $(last_err)"; fi
  else
    row smoke ebur128 skipped "filter absent"
  fi

  if listed scdet "$work/filters"; then
    ff -f lavfi -i "testsrc2=s=160x90:r=25:d=1" -f lavfi -i "color=c=red:s=160x90:r=25:d=1" \
      -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0,scdet=t=10" -f null - 2> "$work/err"
    scdet_times < "$work/err" > "$work/cuts"
    cuts=$(wc -l < "$work/cuts" | tr -d ' ')
    if [ "$cuts" -eq 1 ] && between "$(cat "$work/cuts")" 0.9 1.1; then
      row smoke scdet pass "one cut at $(cat "$work/cuts") s, as made"
    else
      row smoke scdet fail "$cuts cuts ($(tr '\n' ' ' < "$work/cuts")), want one at 1 s"
    fi
  else
    row smoke scdet skipped "filter absent"
  fi

  if ff -v error -f lavfi -i "testsrc2=s=64x36:r=25:d=2" -f lavfi -i "sine=r=48000:d=2" -shortest \
      -c:v rawvideo -c:a pcm_s16le "$work/t.nut" 2> "$work/err"; then
    dur=$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$work/t.nut" 2> "$work/err")
    if between "$dur" 1.9 2.1; then row smoke ffprobe pass "duration $dur s"; else row smoke ffprobe fail "duration '$dur': $(last_err)"; fi
  else
    row smoke ffprobe fail "could not write the test file: $(last_err)"
  fi

  if listed h264_videotoolbox "$work/encoders"; then
    if ff -stats -f lavfi -i "testsrc2=s=1280x720:r=25:d=2" -c:v h264_videotoolbox -b:v 4M \
        "$work/vt.mp4" 2> "$work/err"; then
      codec=$("$FFPROBE" -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$work/vt.mp4" 2>/dev/null)
      row smoke h264_videotoolbox pass "720p, -b:v 4M, ${codec:-unread}, speed $(speed_of < "$work/err")"
    else
      row smoke h264_videotoolbox fail "$(last_err)"
    fi
  else
    row smoke h264_videotoolbox skipped "encoder absent"
  fi
}

python_rows() {
  if ! pyv=$("$PYTHON" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null); then
    row version python3 "not found" "$PYTHON"
    return
  fi
  row version python3 "$pyv" "$PYTHON"
  for m in $PY_MODULES; do
    if v=$("$PYTHON" -c "import $m; print(getattr($m, '__version__', 'unknown'))" 2>/dev/null); then
      row python "$m" present "$v"
    else
      row python "$m" absent
    fi
  done
  # What rpresolve needs: subprocess can run ffprobe, and whether it is a file
  # (delivercheck checks os.path.isfile, which an app built-in may not pass).
  r=$("$PYTHON" -c '
import os, shutil, subprocess, sys
p = sys.argv[1]
w = shutil.which(p)
try:
    r = subprocess.run([p, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=60)
    ok = r.returncode == 0 and "version" in r.stdout
    print("%s rc=%d which=%s isfile=%s" % ("pass" if ok else "fail", r.returncode, w, os.path.isfile(w or p)))
except Exception as e:
    print("fail %s: %s which=%s" % (type(e).__name__, e, w))
' "$FFPROBE" 2>&1 | tail -n 1)
  case "$r" in
    pass*) row python "subprocess ffprobe" pass "${r#pass }" ;;
    *) row python "subprocess ffprobe" fail "${r#fail }" ;;
  esac
}

tool() { # NAME WANT SNIPPET: run SNIPPET, compare its output with WANT
  got=$(eval "$3" 2>/dev/null) || got=""
  if [ "$got" = "$2" ]; then row tool "$1" works; else row tool "$1" missing "got '$got'"; fi
}

tool_rows() {
  tool awk b "echo 'a b' | awk '{ print \$2 }'"
  tool sed xb "echo ab | sed 's/a/x/'"
  tool grep 1 "printf 'a\nb\n' | grep -c a"
  tool sort a "printf 'b\na\n' | LC_ALL=C sort | head -n 1"
  tool tail b "printf 'a\nb\n' | tail -n 1"
  tool cut b "echo a:b | cut -d: -f2"
  tool tr AB "echo ab | tr ab AB"
  tool wc 2 "printf 'a\nb\n' | wc -l | awk '{ print \$1 }'"
  tool "mkdir -p, cp -p" x "mkdir -p '$work/t1/t2' && printf x > '$work/t1/a' && cp -p '$work/t1/a' '$work/t1/t2/b' && cat '$work/t1/t2/b'"
  tool find 1 "find '$work/t1' -type f -name b | wc -l | awk '{ print \$1 }'"
  tool "sha256sum" "$ABC_SHA256" "printf abc | sha256sum | cut -d' ' -f1"
  tool "shasum -a 256" "$ABC_SHA256" "printf abc | shasum -a 256 | cut -d' ' -f1"
  tool "python3 hashlib" "$ABC_SHA256" "printf abc | \"\$PYTHON\" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'"
}

clip_rows() { # CLIP
  if ! "$FFPROBE" -v error -select_streams v:0 -show_entries \
      stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,color_range,color_space,color_transfer,color_primaries \
      -of default=nw=1 "$1" > "$work/clip" 2> "$work/err"; then
    row clip stream fail "$(last_err)"
    return
  fi
  # ffprobe 7 and later also print streams inside stream groups: keep each key, and index, once.
  row clip stream read "$(awk -F= '!seen[$1]++' "$work/clip" | tr '\n' ' ')"
  na=$("$FFPROBE" -v error -select_streams a -show_entries stream=index -of csv=p=0 "$1" 2>/dev/null | grep . | LC_ALL=C sort -u | wc -l | tr -d ' ')
  row clip "audio streams" "$na"
  if ff -stats -t 5 -i "$1" -map 0:v:0 -f null - 2> "$work/err"; then
    row clip "decode 5 s, software" "$(speed_of < "$work/err")" "realtime is 1x"
  else
    row clip "decode 5 s, software" fail "$(last_err)"
  fi
  if ff -stats -hwaccel videotoolbox -t 5 -i "$1" -map 0:v:0 -f null - 2> "$work/err"; then
    row clip "decode 5 s, videotoolbox" "$(speed_of < "$work/err")" "realtime is 1x"
  else
    row clip "decode 5 s, videotoolbox" fail "$(last_err)"
  fi
}

probe() { # [CLIP]
  echo "# Device probe"
  echo
  echo "Paste from here to the end into the chat. $(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null), $(uname -srm 2>/dev/null || echo 'uname missing')."
  echo
  echo "| kind | name | result | detail |"
  echo "|---|---|---|---|"
  conf=""
  if "$FFMPEG" -version > "$work/ffv" 2>&1; then
    row version ffmpeg "$(sed -n '1s/^ffmpeg version \([^ ]*\).*/\1/p' "$work/ffv")" "$(command -v "$FFMPEG" 2>/dev/null || echo "$FFMPEG")"
    conf=$(sed -n 's/^configuration: //p' "$work/ffv")
    for flag in gpl videotoolbox libzimg libass libfreetype; do
      case " $conf " in
        *" --enable-$flag "*) row build "$flag" enabled ;;
        *) row build "$flag" "not enabled" ;;
      esac
    done
    if ff -hwaccels 2>/dev/null | grep -qx videotoolbox; then
      row build "hwaccel videotoolbox" listed "the recipes decode with it"
    else
      row build "hwaccel videotoolbox" "not listed" "the recipes decode in software"
    fi
  else
    row version ffmpeg "not found" "$FFMPEG"
  fi
  if "$FFPROBE" -version > "$work/fpv" 2>&1; then
    row version ffprobe "$(sed -n '1s/^ffprobe version \([^ ]*\).*/\1/p' "$work/fpv")" "$(command -v "$FFPROBE" 2>/dev/null || echo "$FFPROBE")"
  else
    row version ffprobe "not found" "$FFPROBE"
  fi

  ff -filters 2>/dev/null | names_of > "$work/filters"
  ff -encoders 2>/dev/null | names_of > "$work/encoders"
  nf=0; for f in $FILTERS; do
    nf=$((nf + 1))
    if listed "$f" "$work/filters"; then row filter "$f" present; else row filter "$f" absent; fi
  done
  ne=0; for e in $ENCODERS; do
    ne=$((ne + 1))
    if listed "$e" "$work/encoders"; then row encoder "$e" present; else row encoder "$e" absent; fi
  done

  smoke_rows
  python_rows
  tool_rows
  if [ -n "${1:-}" ]; then clip_rows "$1"; fi

  np=0; for m in $PY_MODULES; do np=$((np + 1)); done
  echo
  echo "summary: filters $f_present/$nf present, encoders $e_present/$ne present, smoke $s_pass/$s_run pass, python modules $p_present/$np present"
  if [ -n "$conf" ]; then
    echo
    echo "ffmpeg configuration:"
    echo
    echo '```'
    echo "$conf"
    echo '```'
  fi
}

case "${1:-}" in
  --self-test) [ $# -eq 1 ] || usage; self_test; exit $? ;;
  -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
esac
[ $# -le 1 ] || usage
if [ $# -eq 1 ]; then
  if [ ! -f "$1" ]; then
    case "$1" in -*) usage ;; esac
    echo "probe: $1 is not a file" >&2; exit 2
  fi
  case "$1" in -*) set -- "./$1" ;; esac   # so ffmpeg does not read it as an option
fi
probe "${1:-}"
