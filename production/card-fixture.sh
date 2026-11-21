#!/usr/bin/env bash
# card-fixture.sh: build a small synthetic camera-card tree to run the risky tools on
# (offload, deliver, backup, offsite, anything that deletes or formats) instead of a real card.
#
#   card-fixture.sh [--image] <dir>      build into <dir>, which must exist and be empty
#   card-fixture.sh --help               print this header; writes nothing
#
# <dir>/cards/<name>/ holds one card each; <dir>/MANIFEST.tsv lists every file made
# (card, path, bytes, sha256, role, how). Cards:
#   panasonic    DCIM/100_PANA and PRIVATE/PANA_GRP: MOV and MP4 clips, an RW2 and a JPG still,
#                a WAV, an XML sidecar, a byte-identical duplicate clip under another name
#   sony         PRIVATE/M4ROOT/CLIP and DCIM/100MSDCF: MP4 clips with their M01.XML sidecars, an ARW still
#   blackmagic   A BRAW with its proxy MP4 under Proxy/
#   red          A001_C001.RDM with an R3D and a DNG frame (types a lite copy cannot make)
#   types        one small file of every other known video, still, audio and metadata type
#   unknown      the panasonic card plus DCIM/100_PANA/CLIP.zzz, an extension no tool knows
# Where ffmpeg can make the type (MOV MP4 M4V MKV AVI MTS JPG PNG TIFF WAV AIFF FLAC) the file is real, 64x48,
# 3 frames. Types ffmpeg cannot
# make (BRAW, R3D, DNG, RW2, ARW, HEIC) and every file when ffmpeg is missing are marked
# "standin" in the manifest: small bytes with the right name, not a decodable file.
# Nothing comes from real footage.
#
# --image also writes <dir>/card.dmg (macOS, hdiutil): the panasonic card on a 16 MB FAT volume,
# attached at <dir>/mnt to fill it and detached again before the script exits, even on error.
# Elsewhere --image prints "SKIP" and builds only the tree.
#
# Writes only under <dir>. Refuses a <dir> that is missing, not empty, the root, a symlink, or under
# /Volumes (as typed or resolved); the one mount it makes is its own, inside <dir>.
# Exit: 0 ok, 2 refused or failed.
set -u -o pipefail

die() { echo "card-fixture: $*" >&2; exit 2; }
usage() { sed -n '2,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

image=""; dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --image) image=1; shift ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$dir" ] || die "too many arguments"; dir="$1"; shift ;;
  esac
done
[ -n "$dir" ] || die "usage: card-fixture.sh [--image] <dir>"

dir="${dir%/}"
[ -d "$dir" ] || die "not a directory: $dir (it is never created)"
[ ! -L "$dir" ] || die "$dir is a symlink; give the real directory"
ROOT=$(cd -P "$dir" && pwd -P) || die "cannot resolve $dir"
case "$dir" in /Volumes|/Volumes/*) die "$dir is under /Volumes; the fixture never writes to a mounted volume" ;; esac
case "$ROOT" in /|/Volumes|/Volumes/*) die "$ROOT is not a scratch directory" ;; esac
[ -z "$(ls -A "$ROOT")" ] || die "$ROOT is not empty; the fixture never overwrites"
[ -w "$ROOT" ] || die "$ROOT is not writable"

FF=$(command -v "${FFMPEG:-ffmpeg}" 2>/dev/null || true)
MAN="$ROOT/MANIFEST.tsv"
MNT=""
cleanup() {
  if [ -n "$MNT" ]; then hdiutil detach "$MNT" -quiet >/dev/null 2>&1 || hdiutil detach "$MNT" -force -quiet >/dev/null 2>&1; MNT=""; fi
}
trap cleanup EXIT
trap 'exit 2' INT TERM HUP

# base: the directory the cards are written under (the image mount, while one is attached).
base="$ROOT"
place() { # card relpath -> absolute path, refusing anything that could leave the card
  case "$2" in /*|*..*|"") die "refusing path $2" ;; esac
  mkdir -p "$base/cards/$1/$(dirname "$2")" || die "cannot create $2"
  echo "$base/cards/$1/$2"
}
note() { # card relpath role how
  local f="$base/cards/$1/$2" s h
  s=$(wc -c < "$f" | tr -d ' '); h=$(shasum -a 256 < "$f" | cut -d' ' -f1)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$s" "$h" "$3" "$4" >> "$MAN"
}

# ffmpeg arguments per extension; a missing entry, or a failed run, falls back to a stand-in.
vsrc() { echo "-f lavfi -i testsrc2=size=64x48:rate=5:duration=1 -frames:v 3"; }
ff_args() { # ext
  case "$1" in
    mov|mp4|m4v|mkv|avi) echo "$(vsrc) -c:v mpeg4 -q:v 5 -an" ;;
    mts|m2ts) echo "$(vsrc) -c:v mpeg2video -q:v 5 -an" ;;
    mxf) echo "$(vsrc) -c:v mpeg2video -q:v 5 -an" ;;
    jpg) echo "-f lavfi -i testsrc2=size=64x48 -frames:v 1 -c:v mjpeg" ;;
    png) echo "-f lavfi -i testsrc2=size=64x48 -frames:v 1 -c:v png" ;;
    tif) echo "-f lavfi -i testsrc2=size=64x48 -frames:v 1 -c:v tiff" ;;
    wav) echo "-f lavfi -i sine=frequency=440:duration=0.2 -c:a pcm_s16le" ;;
    aif) echo "-f lavfi -i sine=frequency=440:duration=0.2 -c:a pcm_s16be" ;;
    flac) echo "-f lavfi -i sine=frequency=440:duration=0.2 -c:a flac" ;;
    *) return 1 ;;
  esac
}

# make CARD RELPATH ROLE [VARIANT]: a real file when ffmpeg can make it, else a stand-in.
make() {
  local card=$1 rel=$2 role=$3 var=${4:-0} out ext how=standin args
  out=$(place "$card" "$rel") || exit 2
  ext=$(printf '%s' "${rel##*.}" | tr '[:upper:]' '[:lower:]')
  case "$ext" in xml|txt|json|bin)
    printf 'synthetic %s %s %s\n' "$ext" "$rel" "$var" > "$out"; note "$card" "$rel" "$role" text; return ;;
  esac
  if [ -n "$FF" ] && args=$(ff_args "$ext"); then
    # shellcheck disable=SC2086  # the argument string is split on purpose
    if "$FF" -v error -nostdin -y $args -metadata "comment=fixture $rel $var" -f "$(ff_fmt "$ext")" "$out" 2>/dev/null && [ -s "$out" ]; then how=ffmpeg; fi
  fi
  if [ "$how" = standin ]; then
    awk -v e="$ext" -v r="$rel" -v v="$var" 'BEGIN { printf "STANDIN %s %s %s\n", e, r, v; for (i = 0; i < 40; i++) printf "%s %d %s\n", e, i, v }' > "$out"
  fi
  note "$card" "$rel" "$role" "$how"
}
# The container name for -f, so a stand-in-free run never depends on the file name alone.
ff_fmt() {
  case "$1" in
    mov) echo mov ;; mp4|m4v) echo mp4 ;; mkv) echo matroska ;; avi) echo avi ;; mts|m2ts) echo mpegts ;; mxf) echo mxf ;;
    jpg) echo image2 ;; png) echo image2 ;; tif) echo image2 ;; wav) echo wav ;; aif) echo aiff ;; flac) echo flac ;;
  esac
}

: > "$MAN" || die "cannot write $MAN"
printf 'card\tpath\tbytes\tsha256\trole\thow\n' >> "$MAN"

build() {
  # panasonic: the S5 and GH series layout.
  make panasonic DCIM/100_PANA/P1000001.MOV video 1
  make panasonic DCIM/100_PANA/P1000002.MP4 video 2
  make panasonic DCIM/100_PANA/P1000003.RW2 still
  make panasonic DCIM/100_PANA/P1000004.JPG still
  make panasonic DCIM/100_PANA/P1000001.XML sidecar
  make panasonic AUDIO/A0001.WAV audio
  make panasonic PRIVATE/PANA_GRP/INFO.BIN metadata
  cp "$base/cards/panasonic/DCIM/100_PANA/P1000001.MOV" "$(place panasonic DCIM/100_PANA/P1000005.MOV)" || exit 2
  note panasonic DCIM/100_PANA/P1000005.MOV duplicate "copy of P1000001.MOV"
  # sony: clips in M4ROOT with an M01 sidecar each.
  make sony PRIVATE/M4ROOT/CLIP/C0001.MP4 video 3
  make sony PRIVATE/M4ROOT/CLIP/C0001M01.XML sidecar
  make sony PRIVATE/M4ROOT/CLIP/C0002.MP4 video 4
  make sony PRIVATE/M4ROOT/CLIP/C0002M01.XML sidecar
  make sony DCIM/100MSDCF/DSC00001.ARW still
  # blackmagic: a BRAW with its proxy.
  make blackmagic BRAW/A001_C001.braw video
  make blackmagic BRAW/Proxy/A001_C001.mp4 video 5
  # red: types a lite copy cannot make.
  make red A001_C001.RDM/A001_C001_001.R3D video
  make red A001_C001.RDM/A001_C001_002.R3D video
  make red CinemaDNG/CLIP_0001_000001.dng still
  # types: one of each other known type.
  local e
  for e in mov mp4 m4v mkv avi mts m2ts mxf; do make types "VIDEO/T_$e.$e" video 6; done
  for e in jpg png tif heic; do make types "STILLS/S_$e.$e" still 7; done
  for e in wav aif flac; do make types "AUDIO/A_$e.$e" audio 8; done
  for e in xml txt json bin; do make types "META/M_$e.$e" metadata; done
  # unknown: the panasonic card plus one file no tool knows.
  mkdir -p "$base/cards/unknown" || exit 2
  cp -R "$base/cards/panasonic/." "$base/cards/unknown/" || exit 2
  awk -F'\t' -v OFS='\t' '$1 == "panasonic" { $1 = "unknown"; print }' "$MAN" >> "$MAN.tmp" && cat "$MAN.tmp" >> "$MAN" && rm -f "$MAN.tmp"
  printf 'no tool knows this extension\n' > "$(place unknown DCIM/100_PANA/CLIP.zzz)" || exit 2
  note unknown DCIM/100_PANA/CLIP.zzz unknown text
}

if [ -n "$image" ]; then
  if [ "$(uname)" != Darwin ] || ! command -v hdiutil >/dev/null; then
    echo "SKIP: --image needs macOS hdiutil; building the plain tree only" >&2
    image=""
  fi
fi

build

if [ -n "$image" ]; then
  # The disk image holds the panasonic card, filled through a mount that lives inside <dir>.
  mkdir "$ROOT/mnt" || die "cannot create $ROOT/mnt"
  hdiutil create -size 16m -fs MS-DOS -volname CARD -ov "$ROOT/card.dmg" >/dev/null || die "hdiutil create failed"
  hdiutil attach "$ROOT/card.dmg" -mountpoint "$ROOT/mnt" -nobrowse -noautoopen -quiet >/dev/null || die "hdiutil attach failed"
  MNT="$ROOT/mnt"
  # Only a volume attached at our own mount point is written to.
  [ "$(cd -P "$MNT" && pwd -P)" = "$ROOT/mnt" ] && [ "$(df -P "$MNT" | awk 'NR == 2 {print $NF}')" = "$ROOT/mnt" ] || die "the image did not mount at $ROOT/mnt"
  cp -R "$ROOT/cards/panasonic/." "$MNT/" || die "cannot fill the image"
  sync
  cleanup
  rmdir "$ROOT/mnt" 2>/dev/null || true
fi
echo "card-fixture: $(($(wc -l < "$MAN") - 1)) files in $ROOT/cards ($(ls "$ROOT/cards" | tr '\n' ' ')), manifest $MAN${image:+, image $ROOT/card.dmg}"
