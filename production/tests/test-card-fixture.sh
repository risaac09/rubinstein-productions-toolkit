#!/usr/bin/env bash
# test-card-fixture.sh: build the synthetic cards with production/card-fixture.sh, check its refusals,
# then run offload.sh's verdicts on them end to end. The unknown extension and the R3D card must fail
# closed under `safe --lite`; the clean cards must pass. rclone, diskutil, df and smbutil are fakes.
# Exit 1 on any failure.
# shellcheck disable=SC2034  # variables are read inside the eval'd check strings
set -u
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
F="$SRC/production/card-fixture.sh"; O="$SRC/production/offload.sh"
T=$(mktemp -d) || exit 1
MNT=""
trap '[ -z "$MNT" ] || hdiutil detach "$MNT" -force -quiet >/dev/null 2>&1; rm -rf "$T"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
snap() { (cd "$1" && find . | LC_ALL=C sort); }

[ -x "$F" ] || { echo "FAIL $F is missing or not executable"; exit 1; }

# --help writes nothing.
mkdir "$T/h"; before=$(snap "$T/h")
out=$(cd "$T/h" && "$F" --help); rc=$?
check "--help exits 0, prints the header, writes nothing" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "Writes only under" && [ "$(snap "$T/h")" = "$before" ]'

# Refusals: each exits 2 and leaves the target alone.
mkdir "$T/full"; echo x > "$T/full/keep"; ln -s "$T/full" "$T/link"
for case_ in "/Volumes" "/" "$T/missing" "$T/full" "$T/link"; do
  out=$("$F" "$case_" 2>&1); rc=$?
  check "refuses $case_" '[ "$rc" -eq 2 ]'
done
check "a refused non-empty dir is untouched" '[ "$(snap "$T/full")" = "$(printf ".\n./keep")" ] && [ ! -e "$T/missing" ]'
"$F" >/dev/null 2>&1; check "no argument is a usage error" '[ $? -eq 2 ]'
# A symlink stays a symlink however it is spelled.
mkdir "$T/real"; ln -s "$T/real" "$T/rlink"
for case_ in "$T/rlink//" "$T/rlink/."; do
  "$F" "$case_" >/dev/null 2>&1; rc=$?
  check "refuses $case_ and writes nothing through it" '[ "$rc" -eq 2 ] && [ -z "$(ls -A "$T/real")" ]'
done
# A directory that cannot be listed is not an empty one (root reads anything, so not as root).
if [ "$(id -u)" -ne 0 ]; then
  mkdir "$T/blind"; echo old > "$T/blind/MANIFEST.tsv"; chmod 300 "$T/blind"
  "$F" "$T/blind" >/dev/null 2>&1; rc=$?; chmod 700 "$T/blind"
  check "an unreadable dir is refused and its file is kept" '[ "$rc" -eq 2 ] && [ "$(cat "$T/blind/MANIFEST.tsv")" = old ] && [ "$(ls -A "$T/blind")" = MANIFEST.tsv ]'
fi
# //Volumes is /Volumes: refused as a volume, before the directory is even listed.
if [ -d /Volumes ]; then
  out=$("$F" //Volumes 2>&1); rc=$?
  check "//Volumes is refused as a mounted volume" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -Eq "under /Volumes|not a scratch directory"'
fi
# A % in the directory name is not a file-name pattern: nothing lands in the sibling it would name.
mkdir -p "$T/p%d" "$T/p1/cards/types/STILLS" "$T/p1/cards/panasonic/DCIM/100_PANA"; echo keep > "$T/p1/cards/types/STILLS/S_jpg.jpg"
"$F" "$T/p%d" >/dev/null 2>&1; rc=$?
check "a % in the path writes nothing outside it" '[ "$rc" -eq 0 ] && [ "$(cat "$T/p1/cards/types/STILLS/S_jpg.jpg")" = keep ] && [ "$(find "$T/p1" -type f | wc -l | tr -d " ")" -eq 1 ]'
"$F" "$T/h" "$T/h" >/dev/null 2>&1; check "two arguments are refused" '[ $? -eq 2 ]'

# Build, and check nothing lands outside the directory given.
mkdir "$T/parent" "$T/parent/d"; echo sibling > "$T/parent/sibling"
before=$(cd "$T/parent" && find . -path ./d -prune -o -print | LC_ALL=C sort)
out=$("$F" "$T/parent/d" 2>&1); rc=$?
check "build exits 0" '[ "$rc" -eq 0 ]'
check "nothing written outside the directory" '[ "$(cd "$T/parent" && find . -path ./d -prune -o -print | LC_ALL=C sort)" = "$before" ]'
D="$T/parent/d"; M="$D/MANIFEST.tsv"
check "all six cards exist" 'for c in panasonic sony blackmagic red types unknown; do [ -d "$D/cards/$c" ] || exit 1; done'
nman=$(($(wc -l < "$M") - 1)); nfs=$(find "$D/cards" -type f | wc -l | tr -d ' ')
check "manifest lists every file ($nman of $nfs)" '[ "$nman" -eq "$nfs" ]'
check "manifest hashes match the files" 'awk -F"\t" "NR > 1 {print \$4 \"  cards/\" \$1 \"/\" \$2}" "$M" | (cd "$D" && shasum -a 256 -c - >/dev/null 2>&1)'
check "unknown extension, duplicate and sidecar are present" 'grep -q "CLIP.zzz.*unknown" "$M" && grep -q "duplicate" "$M" && grep -q "sidecar" "$M"'
check "a second build into the same dir is refused" '! "$F" "$D" >/dev/null 2>&1'

# Fakes, as in test-offload.sh.
mkdir "$T/bin"
cat > "$T/bin/diskutil" <<'X'
#!/bin/sh
# the temp root is another volume from the cards below it
u=11111111-2222-3333-4444-555555555555
[ "${2%/}" != "$FAKE_PLAIN" ] || u=99999999-other-volume
printf '   Volume UUID:              %s\n' "$u"
X
printf '#!/bin/sh\nexit 0\n' > "$T/bin/smbutil"
printf '#!/bin/sh\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 100 0 999999999 0%% /x\\n"\n' > "$T/bin/df"
cat > "$T/bin/rclone" <<'X'
#!/bin/sh
case "$1" in config) printf '[x]\ntype = b2\n'; exit 0 ;; listremotes) echo "remote:  b2"; exit 0 ;; esac
src="$2"; match=""
while [ $# -gt 0 ]; do case "$1" in --match) match="$2" ;; --log-file) : > "$2" ;; esac; shift; done
[ -z "$match" ] || (cd "$src" && find . -type f | sed 's|^\./||' | grep -v "${FAKE_RCLONE_DROP:-^$}") > "$match"
exit 0
X
chmod +x "$T/bin/"*
FAKE_PLAIN=$(cd "$T" && pwd -P); export FAKE_PLAIN
export DISKUTIL_BIN="$T/bin/diskutil" DF_BIN="$T/bin/df" RCLONE_BIN="$T/bin/rclone" SMBUTIL_BIN="$T/bin/smbutil"
export OFFLOAD_CAFFEINATED=1 OFFLOAD_NOW=2026-10-20T12:00:00Z READ_FLAGS=""
run() { out=$("$O" "$@" 2>&1); rc=$?; }
copy_card() { # card -> sets CD to the copy dir
  mkdir -p "$T/arch/$1"; run copy "$D/cards/$1" "$T/arch/$1" --cam FX
  [ "$rc" -eq 0 ] || echo "copy of $1 failed: $out"
  CD=$(ls -d "$T/arch/$1"/FX-*/); CD=${CD%/}
}

# Lite tree for the panasonic clips (one .mp4 per video; the duplicate needs its own).
# ffmpeg fake: the output is a function of the input's bytes.
cat > "$T/bin/ffmpeg-stub" <<'X2'
#!/bin/sh
while [ $# -gt 1 ]; do case "$1" in -i) in="$2" ;; esac; shift; done
printf 'lite of %s\n' "$(shasum -a 256 < "$in" | cut -d' ' -f1)" > "$1"
X2
chmod +x "$T/bin/ffmpeg-stub"; export FFMPEG_BIN="$T/bin/ffmpeg-stub"

before=$(cd "$D/cards/panasonic" && find . -type f -exec shasum -a 256 {} + | LC_ALL=C sort | shasum -a 256)
copy_card panasonic; PD=$CD
run safe "$PD" remote:bucket
check "panasonic: full safe passes" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
"$O" lite "$T/lite" "$PD" > /dev/null
run safe --lite "$T/lite" "$PD" remote:bucket
check "panasonic: lite safe passes" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "full-tier record"'
after=$(cd "$D/cards/panasonic" && find . -type f -exec shasum -a 256 {} + | LC_ALL=C sort | shasum -a 256)
check "the fixture card is unchanged by offload" '[ "$before" = "$after" ]'

# A file the remote does not hold: the full verdict fails closed and names it.
copy_card sony; SD=$CD
FAKE_RCLONE_DROP=C0002.MP4 run safe "$SD" remote:bucket
check "sony: a clip missing from the remote fails closed, is named, writes no record" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "C0002.MP4" && [ ! -e "$SD.offsite.txt" ]'
run safe "$SD" remote:bucket
check "sony: full safe passes when the remote holds every file" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'

copy_card unknown; UD=$CD
run safe --lite "$T/lite" "$UD" remote:bucket
check "unknown extension: lite fails closed, names the file, writes no record" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "CLIP.zzz" && [ ! -e "$UD.offsite.txt" ]'

copy_card red; RD=$CD
mkdir -p "$T/litered"; echo lite > "$T/litered/x.mp4"
run safe --lite "$T/litered" "$RD" remote:bucket
check "R3D and DNG card: lite fails closed and names them" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "R3D" && printf "%s" "$out" | grep -q "dng"'

# The mountable image, macOS only.
if [ "$(uname)" = Darwin ] && command -v hdiutil >/dev/null; then
  mkdir "$T/im g"; out=$("$F" --image "$T/im g/" 2>&1); rc=$?
  check "--image builds card.dmg (path with a space) and leaves nothing mounted" '[ "$rc" -eq 0 ] && [ -f "$T/im g/card.dmg" ] && ! mount | grep -q "$T/im g"'
  # A signal that kills hdiutil attach part way: the system finishes the mount after the client has gone.
  # The shim plays that out (it returns as if killed, and the real attach lands a second later); the
  # script must still leave nothing mounted.
  mkdir "$T/sig" "$T/shim"
  cat > "$T/shim/hdiutil" <<X
#!/bin/sh
case "\$1" in attach) (sleep 1; exec /usr/bin/hdiutil "\$@" >/dev/null 2>&1) & : > "$T/attaching"; sleep 0.5; exit 143 ;; esac
exec /usr/bin/hdiutil "\$@"
X
  chmod +x "$T/shim/hdiutil"
  PATH="$T/shim:$PATH" "$F" --image "$T/sig" >/dev/null 2>&1 & pid=$!
  i=0; while [ ! -e "$T/attaching" ] && [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done
  kill -TERM "$pid" 2>/dev/null; wait "$pid"; rc=$?; sleep 2
  check "a signal during attach exits 2 and leaves nothing mounted" '[ -e "$T/attaching" ] && [ "$rc" -eq 2 ] && ! mount | grep -q "$T/sig"'
  hdiutil detach "$T/sig/mnt" -force -quiet >/dev/null 2>&1
  mkdir "$T/ro"; MNT="$T/ro"
  if hdiutil attach "$T/im g/card.dmg" -mountpoint "$MNT" -readonly -nobrowse -noautoopen -quiet >/dev/null 2>&1; then
    check "the image holds the panasonic card" '[ -f "$MNT/DCIM/100_PANA/P1000001.MOV" ] && [ -f "$MNT/PRIVATE/PANA_GRP/INFO.BIN" ]'
    hdiutil detach "$MNT" -quiet >/dev/null 2>&1; MNT=""
  else MNT=""; bad "the image attaches"; fi
else
  echo "skip --image: needs macOS hdiutil"
fi

[ "$fail" -eq 0 ] && echo "card-fixture: all cases pass"
exit "$fail"
