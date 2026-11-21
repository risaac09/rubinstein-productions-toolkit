#!/usr/bin/env bash
# test-card-fixture.sh: build the synthetic cards with production/card-fixture.sh, check its refusals,
# then run offload.sh's verdicts on them end to end. The unknown extension and the R3D card must fail
# closed under `safe --lite`; the clean cards must pass. rclone, diskutil, df and smbutil are fakes.
# Exit 1 on any failure.
# shellcheck disable=SC2034  # variables are read inside the eval'd check strings
set -u
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
F="$SRC/production/card-fixture.sh"; O="$SRC/production/offload.sh"
T=$(mktemp -d)
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
printf '   Volume UUID:              11111111-2222-3333-4444-555555555555\n'
X
printf '#!/bin/sh\nexit 0\n' > "$T/bin/smbutil"
printf '#!/bin/sh\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 100 0 999999999 0%% /x\\n"\n' > "$T/bin/df"
cat > "$T/bin/rclone" <<'X'
#!/bin/sh
case "$1" in config) printf '[x]\ntype = b2\n'; exit 0 ;; listremotes) echo "remote:  b2"; exit 0 ;; esac
src="$2"; match=""
while [ $# -gt 0 ]; do case "$1" in --match) match="$2" ;; --log-file) : > "$2" ;; esac; shift; done
[ -z "$match" ] || (cd "$src" && find . -type f | sed 's|^\./||') > "$match"
exit 0
X
chmod +x "$T/bin/"*
export DISKUTIL_BIN="$T/bin/diskutil" DF_BIN="$T/bin/df" RCLONE_BIN="$T/bin/rclone" SMBUTIL_BIN="$T/bin/smbutil"
export OFFLOAD_CAFFEINATED=1 OFFLOAD_NOW=2026-10-20T12:00:00Z READ_FLAGS=""
run() { out=$("$O" "$@" 2>&1); rc=$?; }
copy_card() { # card -> sets CD to the copy dir
  mkdir -p "$T/arch/$1"; run copy "$D/cards/$1" "$T/arch/$1" --cam FX
  [ "$rc" -eq 0 ] || echo "copy of $1 failed: $out"
  CD=$(ls -d "$T/arch/$1"/FX-*/); CD=${CD%/}
}

# Lite tree for the panasonic clips (one .mp4 per video; the duplicate needs its own).
mkdir -p "$T/lite/DCIM/100_PANA"
for s in P1000001 P1000002 P1000005; do echo lite > "$T/lite/DCIM/100_PANA/$s.mp4"; done

before=$(cd "$D/cards/panasonic" && find . -type f -exec shasum -a 256 {} + | LC_ALL=C sort | shasum -a 256)
copy_card panasonic; PD=$CD
run safe "$PD" remote:bucket
check "panasonic: full safe passes" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
run safe --lite "$T/lite" "$PD" remote:bucket
check "panasonic: lite safe passes" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "full-tier record"'
after=$(cd "$D/cards/panasonic" && find . -type f -exec shasum -a 256 {} + | LC_ALL=C sort | shasum -a 256)
check "the fixture card is unchanged by offload" '[ "$before" = "$after" ]'

copy_card unknown; UD=$CD
run safe --lite "$T/lite" "$UD" remote:bucket
check "unknown extension: lite fails closed, names the file, writes no record" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "CLIP.zzz" && [ ! -e "$UD.offsite.txt" ]'

copy_card red; RD=$CD
mkdir -p "$T/litered"; echo lite > "$T/litered/x.mp4"
run safe --lite "$T/litered" "$RD" remote:bucket
check "R3D and DNG card: lite fails closed and names them" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "R3D" && printf "%s" "$out" | grep -q "dng"'

# The mountable image, macOS only.
if [ "$(uname)" = Darwin ] && command -v hdiutil >/dev/null; then
  mkdir "$T/img"; out=$("$F" --image "$T/img" 2>&1); rc=$?
  check "--image builds card.dmg and leaves nothing mounted" '[ "$rc" -eq 0 ] && [ -f "$T/img/card.dmg" ] && ! mount | grep -q "$T/img"'
  mkdir "$T/ro"; MNT="$T/ro"
  if hdiutil attach "$T/img/card.dmg" -mountpoint "$MNT" -readonly -nobrowse -noautoopen -quiet >/dev/null 2>&1; then
    check "the image holds the panasonic card" '[ -f "$MNT/DCIM/100_PANA/P1000001.MOV" ] && [ -f "$MNT/PRIVATE/PANA_GRP/INFO.BIN" ]'
    hdiutil detach "$MNT" -quiet >/dev/null 2>&1; MNT=""
  else MNT=""; bad "the image attaches"; fi
else
  echo "skip --image: needs macOS hdiutil"
fi

[ "$fail" -eq 0 ] && echo "card-fixture: all cases pass"
exit "$fail"
