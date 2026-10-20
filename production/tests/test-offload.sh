#!/usr/bin/env bash
# test-offload.sh: run production/offload.sh against fixture cards in a temp
# dir. Hermetic: diskutil, df and rclone are fakes; dd, shasum and touch run
# for real. Every case also checks the fixture card was not changed.
# Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
O="$SRC/production/offload.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

mkdir "$T/bin"
cat > "$T/bin/diskutil" <<'EOF'
#!/bin/sh
printf '   Volume Name:              CARD\n   Volume UUID:              %s\n' "${FAKE_UUID:-11111111-2222-3333-4444-555555555555}"
EOF
cat > "$T/bin/df" <<'EOF'
#!/bin/sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nfake 100 0 %s 0%% /x\n' "${FAKE_FREE:-999999999}"
EOF
# rclone fake: exit FAKE_RC, write FAKE_LOG into --log-file.
cat > "$T/bin/rclone" <<'EOF'
#!/bin/sh
log=""; while [ $# -gt 0 ]; do [ "$1" = --log-file ] && log="$2"; shift; done
[ -n "$log" ] && printf '%s\n' "${FAKE_LOG:-}" > "$log"
exit "${FAKE_RC:-0}"
EOF
# dd that writes garbage instead of the input when it has an of= argument.
cat > "$T/bin/dd-corrupt" <<'EOF'
#!/bin/sh
for a in "$@"; do case "$a" in of=*) printf 'garbage' > "${a#of=}"; exit 0 ;; esac; done
exec dd "$@"
EOF
chmod +x "$T/bin/"*
export DISKUTIL_BIN="$T/bin/diskutil" DF_BIN="$T/bin/df" RCLONE_BIN="$T/bin/rclone"
export OFFLOAD_CAFFEINATED=1 OFFLOAD_NOW=2026-10-20T12:00:00Z READ_FLAGS=""

# card NAME: a small camera card with nested folders, a clip, OS junk.
card() {
  c="$T/$1"; mkdir -p "$c/DCIM/100_PANA" "$c/PRIVATE/PANA_GRP" "$c/.fseventsd" "$c/.Spotlight-V100"
  head -c 300000 /dev/urandom > "$c/DCIM/100_PANA/P1000001.MOV"
  head -c 2000 /dev/urandom > "$c/DCIM/100_PANA/P1000001.XML"
  echo meta > "$c/PRIVATE/PANA_GRP/INFO.BIN"
  echo junk > "$c/.fseventsd/x"; echo junk > "$c/.DS_Store"; echo junk > "$c/DCIM/._P1000001.MOV"
  touch -t 202610031234 "$c/DCIM/100_PANA/P1000001.MOV"
}
tree() { (cd "$1" && find . -type f -exec shasum -a 256 {} + | LC_ALL=C sort) | shasum -a 256; }
run() { out=$("$O" "$@" 2>&1); rc=$?; }

# 1. Happy path.
card c1; before=$(tree "$T/c1"); mkdir "$T/a1"
run copy "$T/c1" "$T/a1" --cam S5M2
id=$("$O" id "$T/c1" | awk '/card_id/ {print $2}'); n="S5M2-$(printf '%s' "$id" | cut -c1-6)"
check "copy exits 0" '[ "$rc" -eq 0 ]'
check "copy says not safe yet" 'printf "%s" "$out" | grep -q "NOT safe to format yet"'
check "tree copied, PRIVATE included" '[ -f "$T/a1/$n/PRIVATE/PANA_GRP/INFO.BIN" ] && cmp -s "$T/c1/DCIM/100_PANA/P1000001.MOV" "$T/a1/$n/DCIM/100_PANA/P1000001.MOV"'
check "junk excluded" '[ ! -e "$T/a1/$n/.fseventsd" ] && [ ! -e "$T/a1/$n/.DS_Store" ] && [ ! -e "$T/a1/$n/DCIM/._P1000001.MOV" ]'
check "mtime kept" '[ ! "$T/a1/$n/DCIM/100_PANA/P1000001.MOV" -nt "$T/c1/DCIM/100_PANA/P1000001.MOV" ] && [ ! "$T/a1/$n/DCIM/100_PANA/P1000001.MOV" -ot "$T/c1/DCIM/100_PANA/P1000001.MOV" ]'
check "sidecar passes shasum -c" '(cd "$T/a1/$n" && shasum -a 256 -c "../$n.SHA256SUMS" > /dev/null)'
check "card.json names the id" 'grep -q "\"card_id\": \"$id\"" "$T/a1/$n.card.json" && grep -q "\"files\": 3" "$T/a1/$n.card.json"'
check "no _partial left" '! ls -d "$T/a1"/_partial-* > /dev/null 2>&1'
check "card unchanged" '[ "$(tree "$T/c1")" = "$before" ]'

# 2. Re-run on a finished copy is a no-op; a foreign folder of that name is refused.
run copy "$T/c1" "$T/a1" --cam S5M2
check "re-run says already offloaded" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "already offloaded"'
mkdir "$T/a2"; mkdir "$T/a2/$n"; echo other > "$T/a2/$n/x"
run copy "$T/c1" "$T/a2" --cam S5M2
check "existing folder refused" '[ "$rc" -eq 2 ] && [ "$(ls "$T/a2/$n")" = x ]'

# 3. Id: mtime-blind, content-sensitive, twin found by listing hash.
id1=$("$O" id "$T/c1" | awk '/card_id/ {print $2}')
touch -t 201001010000 "$T/c1/DCIM/100_PANA/P1000001.XML"
check "id ignores mtime" '[ "$("$O" id "$T/c1" | awk "/card_id/ {print \$2}")" = "$id1" ]'
cp -R "$T/c1" "$T/c1b"; echo new > "$T/c1b/DCIM/100_PANA/P1000002.MOV"
check "id changes with a new file" '[ "$("$O" id "$T/c1b" | awk "/card_id/ {print \$2}")" != "$id1" ]'
cp -R "$T/c1" "$T/twin"
tid=$(FAKE_UUID=99999999-0000-0000-0000-000000000000 "$O" id "$T/twin" | awk '/card_id/ {print $2}')
check "twin has another id" '[ "$tid" != "$id1" ]'
out=$(FAKE_UUID=99999999-0000-0000-0000-000000000000 "$O" copy "$T/twin" "$T/a1" --cam S5M2 2>&1); rc=$?
check "twin copy notes the dual-slot match" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "same clips as $n"'

# 4. Missing parent (a dropped share) is refused and nothing is created.
run copy "$T/c1" "$T/gone/Clients" --cam S5M2
check "missing parent refused" '[ "$rc" -eq 2 ] && [ ! -e "$T/gone" ]'

# 5. Corruption: verify fails, _partial kept, nothing renamed.
card c5; before=$(tree "$T/c5"); mkdir "$T/a5"
out=$(DD_BIN="$T/bin/dd-corrupt" "$O" copy "$T/c5" "$T/a5" --cam GH7 2>&1); rc=$?
check "corrupt copy exits 1" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "did not verify"'
check "corrupt copy keeps _partial, no final" 'ls -d "$T/a5"/_partial-GH7-* > /dev/null 2>&1 && ! ls -d "$T/a5"/GH7-* > /dev/null 2>&1'
check "corrupt copy leaves card unchanged" '[ "$(tree "$T/c5")" = "$before" ]'

# 6. Resume: the next run reuses the _partial left by case 5 and finishes.
run copy "$T/c5" "$T/a5" --cam GH7
check "resume finishes" '[ "$rc" -eq 0 ] && ! ls -d "$T/a5"/_partial-* > /dev/null 2>&1 && ls -d "$T/a5"/GH7-* > /dev/null 2>&1'

# 7. Gates.
run copy "$T/c1" "$T/a1" --cam s5m2
check "lower-case cam refused" '[ "$rc" -eq 2 ]'
mkdir "$T/a7"
out=$(FAKE_FREE=10 "$O" copy "$T/c1" "$T/a7" --cam S5M2 2>&1); rc=$?
check "no space refused, nothing written" '[ "$rc" -eq 2 ] && [ -z "$(ls "$T/a7")" ]'
mkdir "$T/empty"
run id "$T/empty"
check "empty card refused" '[ "$rc" -eq 2 ]'

# 8. safe: pass, unhashed files, rclone failure, untracked folder.
d="$T/a1/$n"
run safe "$d" remote:bucket/x
check "safe passes on a clean check" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT" && grep -q "remote remote:bucket/x" "$d.offsite.txt"'
rm -f "$d.offsite.txt"
out=$(FAKE_LOG="NOTICE: 1 hashes could not be checked" "$O" safe "$d" remote:bucket/x 2>&1); rc=$?
check "unhashed files are not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && [ ! -e "$d.offsite.txt" ]'
out=$(FAKE_RC=1 "$O" safe "$d" remote:bucket/x 2>&1); rc=$?
check "rclone failure is not safe" '[ "$rc" -eq 1 ] && [ ! -e "$d.offsite.txt" ]'
run safe "$T/c1" remote:bucket/x
check "folder without sidecars refused" '[ "$rc" -eq 2 ]'
printf 'rot' >> "$d/DCIM/100_PANA/P1000001.XML"
run safe "$d" remote:bucket/x
check "a copy that rotted is not safe even when rclone passes" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "no longer matches"'

# 9. A read-back that fails is reported as a read failure, not a mismatch.
cat > "$T/bin/dd-noread" <<'EOF'
#!/bin/sh
for a in "$@"; do case "$a" in of=*) exec dd "$@" ;; esac; done
exit 1
EOF
chmod +x "$T/bin/dd-noread"
card c9; mkdir "$T/a9"
out=$(DD_BIN="$T/bin/dd-noread" "$O" copy "$T/c9" "$T/a9" --cam GH7 2>&1); rc=$?
check "failed read-back says so" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "cannot read back" && ! printf "%s" "$out" | grep -q mismatch'

# 10. A stray file in _partial (Finder .DS_Store) stops the rename.
card c10; mkdir "$T/a10"
DD_BIN="$T/bin/dd-corrupt" "$O" copy "$T/c10" "$T/a10" --cam GH7 > /dev/null 2>&1
p=$(ls -d "$T/a10"/_partial-GH7-*); echo x > "$p/.DS_Store"
run copy "$T/c10" "$T/a10" --cam GH7
check "stray file in _partial refused" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "not on the card" && [ -d "$p" ]'
rm "$p/.DS_Store"
run copy "$T/c10" "$T/a10" --cam GH7
check "resume after removing the stray" '[ "$rc" -eq 0 ]'

# 11. Cut off after the sidecars but before the rename: the rerun finishes.
card c11; mkdir "$T/a11"
"$O" copy "$T/c11" "$T/a11" --cam GH7 > /dev/null 2>&1
f=$(ls -d "$T/a11"/GH7-??????); mv "$f" "$T/a11/_partial-$(basename "$f")"
run copy "$T/c11" "$T/a11" --cam GH7
check "rerun after a cut-off rename finishes" '[ "$rc" -eq 0 ] && [ -d "$f" ]'
echo '{ "card_id": "000000000000" }' > "$T/a11/OTHER.card.json"; mkdir "$T/a11b"
cp "$T/a11/OTHER.card.json" "$T/a11b/GH7-$(basename "$f" | cut -d- -f2).card.json"
run copy "$T/c11" "$T/a11b" --cam GH7
check "sidecar from another card refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "another card"'

# 12. The real macOS read-back path (iflag=direct), where the platform has it.
if [ "$(uname)" = Darwin ]; then
  card c12; mkdir "$T/a12"
  out=$(env -u READ_FLAGS "$O" copy "$T/c12" "$T/a12" --cam GH7 2>&1); rc=$?
  check "copy with iflag=direct read-back" '[ "$rc" -eq 0 ]'
fi

# 13. Help is the header only.
out=$("$O" --help)
check "help stops at the header" 'printf "%s" "$out" | grep -q "safe to format" && ! printf "%s" "$out" | grep -q "set -u"'

[ "$fail" -eq 0 ] && echo "offload: all cases pass"
exit "$fail"
