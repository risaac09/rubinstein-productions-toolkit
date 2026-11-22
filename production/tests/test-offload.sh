#!/usr/bin/env bash
# test-offload.sh: run production/offload.sh against fixture cards in a temp
# dir. Hermetic: diskutil, df and rclone are fakes; dd, shasum and touch run
# for real. Every case also checks the fixture card was not changed.
# Exit 1 on any failure.
# shellcheck disable=SC2034  # variables are read inside the eval'd check strings
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
FAKE_PLAIN=$(cd "$T" && pwd -P); export FAKE_PLAIN
cat > "$T/bin/diskutil" <<'EOF'
#!/bin/sh
# Like the real tool, it answers only for a mount point: a card root, which sits directly under FAKE_PLAIN. Any folder deeper is "Could not find disk".
[ "$(cd -P "$2/.." 2>/dev/null && pwd -P)" = "$FAKE_PLAIN" ] || { echo "Could not find disk: $2" >&2; exit 1; }
u="${FAKE_UUID:-11111111-2222-3333-4444-555555555555}"
printf '   Volume Name:              CARD\n   Volume UUID:              %s\n' "$u"
EOF
# stat fake: the device is the first folder under the temp root, so a folder nested in a card is on the card's device and a sibling folder is not.
cat > "$T/bin/stat" <<'EOF'
#!/bin/sh
for p; do :; done
p=$(cd -P "$p" 2>/dev/null && pwd -P) || exit 1
r=${p#"$FAKE_PLAIN"/}; r=${r%%/*}
printf '%s' "$r" | cksum | cut -d' ' -f1
EOF
# smbutil fake: a SERVER_NAME line from FAKE_SMB_HOST, nothing when unset (a local disk).
cat > "$T/bin/smbutil" <<'EOF'
#!/bin/sh
[ -n "${FAKE_SMB_HOST:-}" ] && printf 'SHARE ATTRIBUTE VALUE\nArchive\n   SERVER_NAME   %s\n' "$FAKE_SMB_HOST"
exit 0
EOF
cat > "$T/bin/df" <<'EOF'
#!/bin/sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nfake 100 0 %s 0%% /x\n' "${FAKE_FREE:-999999999}"
EOF
# rclone fake: exit FAKE_RC, write FAKE_LOG into --log-file, and into --match
# FAKE_MATCH when set, else every file under the source (a full match).
cat > "$T/bin/rclone" <<'EOF'
#!/bin/sh
case "$1" in config) printf '[x]\nremote = %s\n' "${FAKE_TARGET-}"; exit 0 ;; esac
case "$1" in listremotes) printf '%s\n' "${FAKE_REMOTES-remote:  b2}"; exit 0 ;; esac
src="$2"; log=""; match=""
while [ $# -gt 0 ]; do case "$1" in --log-file) log="$2" ;; --match) match="$2" ;; esac; shift; done
[ -z "$log" ] || printf '%s\n' "${FAKE_LOG:-}" > "$log"
[ -z "${FAKE_ENV_OUT:-}" ] || env | grep '^RCLONE_' > "$FAKE_ENV_OUT"
if [ -n "$match" ]; then
  if [ -n "${FAKE_MATCH+x}" ]; then printf '%s' "$FAKE_MATCH" > "$match"
  else (cd "$src" && find . -type f | sed 's|^\./||') > "$match"; fi
fi
exit "${FAKE_RC:-0}"
EOF
# dd that writes garbage instead of the input when it has an of= argument.
cat > "$T/bin/dd-corrupt" <<'EOF'
#!/bin/sh
for a in "$@"; do case "$a" in of=*) printf 'garbage' > "${a#of=}"; exit 0 ;; esac; done
exec dd "$@"
EOF
# ffmpeg fake: the last argument is the output; it holds a line derived from the -i input's bytes, so a lite file
# is a function of its original and two different clips never make the same lite file.
cat > "$T/bin/ffmpeg-stub" <<'EOF'
#!/bin/sh
while [ $# -gt 1 ]; do case "$1" in -i) in="$2" ;; esac; shift; done
printf 'lite of %s\n' "$(shasum -a 256 < "$in" | cut -d' ' -f1)" > "$1"
EOF
chmod +x "$T/bin/"*
export FFMPEG_BIN="$T/bin/ffmpeg-stub"
export DISKUTIL_BIN="$T/bin/diskutil" DF_BIN="$T/bin/df" RCLONE_BIN="$T/bin/rclone" SMBUTIL_BIN="$T/bin/smbutil" STAT_BIN="$T/bin/stat"
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
TP=$(cd -P "$T" && pwd -P)
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

# 7b. Route: a share mounted from a slow address refuses copy and safe; the fast
# host, a local disk, and --route-override pass.
mkdir -p "$T/vol/Archive/in"
export OFFLOAD_SMB_ROOT="$T/vol"
out=$(FAKE_SMB_HOST=192.168.1.83 "$O" copy "$T/c1" "$T/vol/Archive/in" --cam S5M2 2>&1); rc=$?
check "slow route refused, nothing written" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "mounted from 192.168.1.83" && [ -z "$(ls "$T/vol/Archive/in")" ]'
out=$(FAKE_SMB_HOST=10.10.10.2 "$O" copy "$T/c1" "$T/vol/Archive/in" --cam S5M2 2>&1); rc=$?
check "fast host passes" '[ "$rc" -eq 0 ]'
mkdir -p "$T/vol/T7/in"
out=$(FAKE_SMB_HOST='' "$O" copy "$T/c1" "$T/vol/T7/in" --cam S5M2 2>&1); rc=$?
check "local disk under the root passes (no SERVER_NAME)" '[ "$rc" -eq 0 ]'
mkdir -p "$T/vol/Archive/ov"
out=$(FAKE_SMB_HOST=192.168.1.83 "$O" copy "$T/c1" "$T/vol/Archive/ov" --cam S5M2 --route-override 2>&1); rc=$?
check "--route-override takes the slow route and says so" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "slow route"'
vd=$(ls -d "$T/vol/Archive/in"/S5M2-*/); vd=${vd%/}
out=$(FAKE_SMB_HOST=192.168.1.83 "$O" safe "$vd" remote:bucket/x 2>&1); rc=$?
check "safe on a slow route refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "mounted from"'
out=$(FAKE_SMB_HOST=192.168.1.83 OFFLOAD_ROUTE_OVERRIDE=1 "$O" safe "$vd" remote:bucket/x 2>&1); rc=$?
check "safe with the override runs the check" '[ "$rc" -eq 0 ]'
unset OFFLOAD_SMB_ROOT

# 8. safe: pass, unhashed files, rclone failure, untracked folder.
d="$T/a1/$n"
run safe "$d" remote:bucket/x
check "safe passes on a clean check" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT" && grep -q "remote remote:bucket/x" "$d.offsite.txt"'
rm -f "$d.offsite.txt"
out=$(FAKE_LOG="NOTICE: 1 hashes could not be checked" "$O" safe "$d" remote:bucket/x 2>&1); rc=$?
check "unhashed files are not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && [ ! -e "$d.offsite.txt" ]'
out=$(FAKE_RC=1 "$O" safe "$d" remote:bucket/x 2>&1); rc=$?
check "rclone failure is not safe" '[ "$rc" -eq 1 ] && [ ! -e "$d.offsite.txt" ]'
out=$(FAKE_MATCH="DCIM/100_PANA/P1000001.XML" "$O" safe "$d" remote:bucket/x 2>&1); rc=$?
check "a check that matched only some files (a filter) is not safe" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "P1000001.MOV" && [ ! -e "$d.offsite.txt" ]'
# rclone reads any flag from RCLONE_<FLAG>: RCLONE_SIZE_ONLY would pass a copy on size alone.
out=$(RCLONE_SIZE_ONLY=true RCLONE_CONFIG_REMOTE_TYPE=b2 FAKE_ENV_OUT="$T/rclone.env" "$O" safe "$d" remote:bucket/x 2>&1); rc=$?
check "rclone flag variables are cleared, remote and config variables kept" '[ "$rc" -eq 0 ] && ! grep -q RCLONE_SIZE_ONLY "$T/rclone.env" && grep -q RCLONE_CONFIG_REMOTE_TYPE "$T/rclone.env"'
rm -f "$d.offsite.txt"
# Same refusals through the fake (no rclone needed): type local, unreadable, union, wrapper chains.
for c in "remote:  local" "" "remote:  union" "remote:  crypt" "remote:  alias"; do
  out=$(FAKE_REMOTES="$c" FAKE_TARGET="/some/dir" "$O" safe "$d" remote:x 2>&1); rc=$?
  check "fake remote '$c' is refused" '[ "$rc" -eq 2 ] && [ ! -e "$d.offsite.txt" ]'
done
out=$(FAKE_REMOTES="remote:  crypt" FAKE_TARGET="remote:" "$O" safe "$d" remote:x 2>&1); rc=$?
check "a crypt that points at itself is refused after the hop limit" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "too deep"'
out=$(FAKE_REMOTES="my b2:  b2" "$O" safe "$d" "my b2:x" 2>&1); rc=$?
check "a remote name with a space is read" '[ "$rc" -eq 0 ]'
rm -f "$d.offsite.txt"
# The type is the column after the name; a description follows it (rclone 1.67+).
out=$(FAKE_REMOTES="remote: local copy for b2" "$O" safe "$d" remote:x 2>&1); rc=$?
check "a local remote with a description is refused" '[ "$rc" -eq 2 ] && [ ! -e "$d.offsite.txt" ]'
out=$(FAKE_REMOTES="remote: b2    nothing kept local" "$O" safe "$d" remote:x 2>&1); rc=$?
check "a b2 remote whose description ends in local passes" '[ "$rc" -eq 0 ]'
rm -f "$d.offsite.txt"
# Remote names are case-sensitive: REMOTE (b2) is not remote (local).
out=$(FAKE_REMOTES="REMOTE: b2
remote: local" "$O" safe "$d" remote:x 2>&1); rc=$?
check "a same-name remote in another case does not lend its type" '[ "$rc" -eq 2 ] && [ ! -e "$d.offsite.txt" ]'
# A remote of type local, or an alias ending at one, is refused; so is one whose type cannot be read.
if command -v rclone > /dev/null; then
  # Real rclone reads the config; the check itself stays the fake and is never reached.
  cat > "$T/bin/rclone-cfg" <<EOF
#!/bin/sh
case "\$1" in listremotes|config) exec rclone "\$@" ;; *) exec "$T/bin/rclone" "\$@" ;; esac
EOF
  chmod +x "$T/bin/rclone-cfg"
  printf '[loc]\ntype = local\n\n[al]\ntype = alias\nremote = loc:/x\n\n[al2]\ntype = alias\nremote = al:\n\n[albare]\ntype = alias\nremote = /some/dir\n\n[far]\ntype = b2\n\n[alfar]\ntype = alias\nremote = far:bucket\n' > "$T/rc.conf"
  safe_cfg() { out=$(RCLONE_BIN="$T/bin/rclone-cfg" RCLONE_CONFIG="$1" "$O" safe "$d" "$2" 2>&1); rc=$?; rm -f "$d.offsite.txt"; }
  for r in loc:x al:x al2:x albare:x missing:x; do
    safe_cfg "$T/rc.conf" "$r"
    check "remote $r is refused" '[ "$rc" -eq 2 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
  done
  for r in far:x alfar:x; do
    safe_cfg "$T/rc.conf" "$r"
    check "remote $r (non-local) passes" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
  done
  printf 'not [valid\n' > "$T/bad.conf"
  safe_cfg "$T/bad.conf" far:x
  check "unreadable config is refused" '[ "$rc" -eq 2 ]'
  # An env override wins over the file, and listremotes then lists the name twice in two cases.
  printf '[MYS3]\ntype = b2\n\n[desc]\ntype = local\ndescription = copy for b2\n' > "$T/env.conf"
  RCLONE_CONFIG_MYS3_TYPE=local safe_cfg "$T/env.conf" MYS3:x
  check "a b2 remote overridden to local in the environment is refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "type local"'
  safe_cfg "$T/env.conf" desc:x
  check "a local remote with a description is refused (real rclone)" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "type local"'
  RCLONE_CONFIG_ENVLOC_TYPE=local safe_cfg "$T/env.conf" envloc:x
  check "a local remote defined only in the environment is refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "type local"'
  RCLONE_CONFIG_ENVB2_TYPE=b2 safe_cfg "$T/env.conf" envb2:x
  check "a b2 remote defined only in the environment passes" '[ "$rc" -eq 0 ]'
else
  echo "skip rclone not installed: local and alias remote cases"
fi
run safe "$T/c1" remote:bucket/x
check "folder without sidecars refused" '[ "$rc" -eq 2 ]'
printf 'rot' >> "$d/DCIM/100_PANA/P1000001.XML"
run safe "$d" remote:bucket/x
check "a copy that rotted is not safe even when rclone passes" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "no longer matches"'

# 8b. safe wants an rclone remote; a local folder, even a real one, is refused.
rm -f "$d.offsite.txt"
run safe "$d" "$T/a1"
check "a local folder is not an offsite copy" '[ "$rc" -eq 2 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && [ ! -e "$d.offsite.txt" ]'

# 8c. copy refuses a destination inside a git work tree.
mkdir -p "$T/repo/sub"; git init -q "$T/repo"
run copy "$T/c1" "$T/repo/sub" --cam S5M2
check "copy into a git work tree refused, nothing written" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "git work tree" && [ -z "$(ls "$T/repo/sub")" ]'

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

# 14. safe --lite: the full copy verifies, a lite copy of each video is offsite.
# lcard NAME: videos at several shapes, a BRAW with a camera proxy, a shared stem, a non-video.
lcard() {
  c="$T/$1"; mkdir -p "$c/DCIM/A" "$c/DCIM/B/Proxy" "$c/DCIM/C"
  head -c 5000 /dev/urandom > "$c/DCIM/A/C0001.MOV"
  head -c 5000 /dev/urandom > "$c/DCIM/A/c0002.mp4"
  head -c 5000 /dev/urandom > "$c/DCIM/B/R001.braw"
  head -c 3000 /dev/urandom > "$c/DCIM/B/Proxy/R001.mp4"
  head -c 5000 /dev/urandom > "$c/DCIM/C/X1.mov"
  head -c 5000 /dev/urandom > "$c/DCIM/C/X1.mxf"
  echo meta > "$c/DCIM/A/C0001.XML"
}
# make_lite DIR COPY: the lite copy for COPY, made by the tool into the new folder DIR.
make_lite() { "$O" lite "$1" "$2" > /dev/null; }
lcard cl; mkdir "$T/al"
run copy "$T/cl" "$T/al" --cam GH7
ld=$(ls -d "$T/al"/GH7-*/); ld=${ld%/}
make_lite "$T/lite" "$ld"; cp -R "$T/lite" "$T/lite.orig"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "lite tier passes and records the tier" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT" && grep -q "^tier lite" "$ld.offsite.txt" && grep -q "^lite_dir $TP/lite$" "$ld.offsite.txt"'
rm -f "$ld.offsite.txt"
run safe "$ld" remote:bucket/x
check "full tier records tier full" '[ "$rc" -eq 0 ] && grep -q "^tier full" "$ld.offsite.txt" && ! grep -q lite_dir "$ld.offsite.txt"'
rm -f "$ld.offsite.txt"
rm "$T/lite/DCIM/C/X1.mxf.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "a missing lite file is not safe and is named" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "NOT safe to format" && printf "%s" "$out" | grep -q "DCIM/C/X1.mxf.mp4" && [ ! -e "$ld.offsite.txt" ]'
: > "$T/lite/DCIM/C/X1.mxf.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "an empty lite file is not safe" '[ "$rc" -eq 1 ] && [ ! -e "$ld.offsite.txt" ]'
cp "$T/lite.orig/DCIM/C/X1.mxf.mp4" "$T/lite/DCIM/C/X1.mxf.mp4"
rm "$T/lite/DCIM/C/X1.mov.mp4"; echo lite > "$T/lite/DCIM/C/X1.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "shared stem needs the full-name .mp4, not the stem .mp4" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "DCIM/C/X1.mov.mp4"'
rm "$T/lite/DCIM/C/X1.mp4"; cp "$T/lite.orig/DCIM/C/X1.mov.mp4" "$T/lite/DCIM/C/X1.mov.mp4"
rm "$T/lite/DCIM/B/R001.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "a BRAW is not covered by its Proxy in the copy alone; its lite file is needed" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "DCIM/B/R001.mp4"'
cp "$T/lite.orig/DCIM/B/R001.mp4" "$T/lite/DCIM/B/R001.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "BRAW covered by its proxy copy, shared stems named in full: passes" '[ "$rc" -eq 0 ] && grep -q "^tier lite" "$ld.offsite.txt"'
rm -f "$ld.offsite.txt"
out=$(FAKE_RC=1 "$O" safe --lite "$T/lite" "$ld" remote:bucket/lite 2>&1); rc=$?
check "rclone failure on the lite tree is not safe" '[ "$rc" -eq 1 ] && [ ! -e "$ld.offsite.txt" ]'
out=$(FAKE_LOG="NOTICE: 1 hashes could not be checked" "$O" safe --lite "$T/lite" "$ld" remote:bucket/lite 2>&1); rc=$?
check "lite hashes that could not compare are not safe" '[ "$rc" -eq 1 ] && [ ! -e "$ld.offsite.txt" ]'
out=$(FAKE_MATCH="DCIM/A/C0001.mp4" "$O" safe --lite "$T/lite" "$ld" remote:bucket/lite 2>&1); rc=$?
check "a lite check that matched only some files is not safe" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "did not match" && [ ! -e "$ld.offsite.txt" ]'
run safe --lite "$T/lite" "$ld" "$T/al"
check "a local path as remote is refused in lite mode" '[ "$rc" -eq 2 ] && [ ! -e "$ld.offsite.txt" ]'
out=$(FAKE_REMOTES="remote:  local" FAKE_TARGET="/some/dir" "$O" safe --lite "$T/lite" "$ld" remote:x 2>&1); rc=$?
check "a local-type remote is refused in lite mode" '[ "$rc" -eq 2 ] && [ ! -e "$ld.offsite.txt" ]'
run safe --lite "$ld" "$ld" remote:bucket/lite
check "the copy as its own lite dir is refused" '[ "$rc" -eq 2 ] && [ ! -e "$ld.offsite.txt" ]'
mkdir "$T/emptylite"
run safe --lite "$T/emptylite" "$ld" remote:bucket/lite
check "an empty lite dir is not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
card cnv; mkdir "$T/anv"; rm "$T/cnv/DCIM/100_PANA/P1000001.MOV"
run copy "$T/cnv" "$T/anv" --cam GH7
nd=$(ls -d "$T/anv"/GH7-*/); nd=${nd%/}
run safe --lite "$T/emptylite" "$nd" remote:bucket/lite
check "a copy with no videos is not safe by the lite tier" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "no video files"'
run safe --lite
check "--lite without a directory is a usage error" '[ "$rc" -eq 2 ]'
run safe --lite "$T/lite" "$ld"
check "lite form needs the remote" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "usage"'
rotd="$ld"; printf 'rot' >> "$rotd/DCIM/A/C0001.XML"
run safe --lite "$T/lite" "$rotd" remote:bucket/lite
check "a rotted full copy is not safe even with a complete lite copy" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "no longer matches"'

# R3D originals with no lite copy are not safe (data-loss repro: .r3d was not counted as video).
c="$T/cr3"; mkdir -p "$c/A001_C001.RDM/A001_C001.RDC" "$c/A001_C002.RDM"
head -c 4000 /dev/urandom > "$c/A001_C001.RDM/A001_C001.RDC/A001_C001_001.R3D"
head -c 4000 /dev/urandom > "$c/A001_C001.RDM/A001_C001.RDC/A001_C001_002.R3D"
head -c 4000 /dev/urandom > "$c/A001_C002.RDM/B_ROLL.mov"
mkdir "$T/ar3"; run copy "$T/cr3" "$T/ar3" --cam GH7
rd=$(ls -d "$T/ar3"/GH7-*/); rd=${rd%/}
mkdir -p "$T/lite3/A001_C002.RDM"; echo lite > "$T/lite3/A001_C002.RDM/B_ROLL.mp4"
run safe --lite "$T/lite3" "$rd" remote:bucket/lite
check "R3D originals with no lite copy are not safe and are named" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "A001_C001_001.R3D" && printf "%s" "$out" | grep -q "A001_C001_002.R3D" && [ ! -e "$rd.offsite.txt" ]'
# A DNG may be a CinemaDNG frame sequence the lite builder cannot make: it blocks too.
c="$T/cdng"; mkdir -p "$c/CLIP_0001"
head -c 4000 /dev/urandom > "$c/CLIP_0001/CLIP_0001_000001.dng"
mkdir "$T/adng"; run copy "$T/cdng" "$T/adng" --cam GH7
rd=$(ls -d "$T/adng"/GH7-*/); rd=${rd%/}
mkdir -p "$T/litedng"; echo lite > "$T/litedng/other.mp4"
run safe --lite "$T/litedng" "$rd" remote:bucket/lite
check "a DNG frame with no lite copy is not safe and is named" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "CLIP_0001_000001.dng" && [ ! -e "$rd.offsite.txt" ]'
# Keep lite_names in step with card.py VIDEO: every extension card.py counts as video must be
# classified there (a video to cover, or r3d, which blocks).
vids=$(sed -n 's/^VIDEO = {\(.*\)}.*/\1/p' "$SRC/production/rpresolve/card.py" | tr -d '". ' | tr ',' ' ')
alt=$(sed -n 's/.*(e !*~ \/^(\([a-z0-9|]*\))\$\/.*/\1/p' "$O" | tr '\n' '|')
miss=""; for e in $vids; do case "|$alt|" in *"|${e#.}|"*) ;; *) miss="$miss $e" ;; esac; done
check "lite_names video list covers card.py VIDEO (missing:${miss:- none})" '[ -n "$vids" ] && [ -n "$alt" ] && [ -z "$miss" ]'

# Fail closed: one unknown extension blocks and is named; a mixed tree of known types passes end to end.
c="$T/cmx"; mkdir -p "$c/DCIM/A" "$c/DCIM/B/Proxy" "$c/AUDIO"
head -c 3000 /dev/urandom > "$c/DCIM/A/V1.MOV"; head -c 3000 /dev/urandom > "$c/DCIM/B/R1.braw"
head -c 2000 /dev/urandom > "$c/DCIM/B/Proxy/R1.mp4"; head -c 2000 /dev/urandom > "$c/DCIM/A/S1.JPG"
head -c 2000 /dev/urandom > "$c/AUDIO/T1.wav"; echo m > "$c/DCIM/A/V1.XML"
mkdir "$T/amx"; run copy "$T/cmx" "$T/amx" --cam GH7
xd=$(ls -d "$T/amx"/GH7-*/); xd=${xd%/}
make_lite "$T/litemx" "$xd"
run safe --lite "$T/litemx" "$xd" remote:bucket/lite
check "mixed known types (video, braw+proxy, still, wav, xml) pass the lite tier" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
c="$T/cmy"; cp -R "$T/cmx" "$c"; head -c 900 /dev/urandom > "$c/DCIM/A/CLIP.zzz"
mkdir "$T/amy"; run copy "$c" "$T/amy" --cam GH7
yd=$(ls -d "$T/amy"/GH7-*/); yd=${yd%/}
run safe --lite "$T/litemx" "$yd" remote:bucket/lite
check "an unknown extension is not safe and is named" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "DCIM/A/CLIP.zzz" && [ ! -e "$yd.offsite.txt" ]'

# A case-changed spelling of the copy or a path inside it is still the copy (case-insensitive APFS keeps the typed case).
lc=$(printf '%s' "$ld" | tr '[:upper:]' '[:lower:]')
if [ "$lc" != "$ld" ] && [ -d "$lc" ]; then
  run safe --lite "$lc" "$ld" remote:bucket/lite
  check "a case-changed spelling of the copy as lite dir is refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "lite dir" && [ ! -e "$ld.offsite.txt" ]'
  run safe --lite "$lc/DCIM" "$ld" remote:bucket/lite
  check "a case-changed lite dir inside the copy is refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "lite dir" && [ ! -e "$ld.offsite.txt" ]'
else
  echo "skip case-changed lite dir tests: the temp filesystem is case-sensitive (or the path has no letters to change)"
fi

# 15. Review 2 fixes.
lcard c15; mkdir "$T/a15"; run copy "$T/c15" "$T/a15" --cam GH7
l2=$(ls -d "$T/a15"/GH7-*/); l2=${l2%/}
# 15a. Only a listed offsite backend passes: archive, union, combine, an alias to archive, an unknown type.
d="$l2"; rm -f "$d.offsite.txt"
for c in "remote:  archive" "remote:  union" "remote:  combine" "remote:  sftp" "remote:  somenewbackend" "remote:  memory"; do
  out=$(FAKE_REMOTES="$c" "$O" safe "$d" remote:x 2>&1); rc=$?
  check "remote type '$c' is not a listed offsite backend" '[ "$rc" -eq 2 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && [ ! -e "$d.offsite.txt" ]'
done
for t in b2 s3 drive; do
  out=$(FAKE_REMOTES="remote:  $t" "$O" safe "$d" remote:x 2>&1); rc=$?
  check "remote type $t passes" '[ "$rc" -eq 0 ]'
done
rm -f "$d.offsite.txt"
if command -v rclone > /dev/null; then
  printf '[arc]\ntype = archive\nremote = /tmp\n\n[alarc]\ntype = alias\nremote = arc:\n' > "$T/rc2.conf"
  safe_cfg "$T/rc2.conf" arc:x
  check "a real archive remote is refused" '[ "$rc" -eq 2 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
  safe_cfg "$T/rc2.conf" alarc:x
  check "an alias to an archive remote is refused" '[ "$rc" -eq 2 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
fi

# 15b. A not-safe run removes an earlier safe record.
run safe "$l2" remote:bucket/x
check "full tier passes first" '[ "$rc" -eq 0 ] && [ -f "$l2.offsite.txt" ]'
out=$(FAKE_RC=1 "$O" safe "$l2" remote:bucket/x 2>&1); rc=$?
check "a failing rerun removes the earlier safe record" '[ "$rc" -eq 1 ] && [ ! -e "$l2.offsite.txt" ]'
run safe "$l2" remote:bucket/x
out=$(FAKE_REMOTES="remote:  local" "$O" safe "$l2" remote:x 2>&1); rc=$?
check "a refused rerun removes the earlier safe record" '[ "$rc" -eq 2 ] && [ ! -e "$l2.offsite.txt" ]'

# 15c. A lite tree holding clips this card does not have is not safe; a lite dir path is stored absolute;
#      a lite pass keeps an earlier full-tier record.
make_lite "$T/lite4" "$l2"; echo lite > "$T/lite4/DCIM/A/C0099.mp4"
run safe --lite "$T/lite4" "$l2" remote:bucket/lite
check "a lite dir with an .mp4 no video on this card needs is not safe" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -qi "C0099.mp4" && [ ! -e "$l2.offsite.txt" ]'
run safe "$l2" remote:bucket/x
rm -rf "$T/lite4"; make_lite "$T/lite4" "$l2"
(cd "$T" && run safe --lite lite4 "$l2" remote:bucket/lite; echo "$rc" > "$T/rc.out"; printf '%s' "$out" > "$T/out.txt")
check "a lite pass after a full pass keeps the full record" '[ "$(cat "$T/rc.out")" = 0 ] && grep -q "^tier full" "$l2.offsite.txt" && ! grep -q "^tier lite" "$l2.offsite.txt" && grep -q "earlier full-tier record" "$T/out.txt"'
check "the SAFE output states the lite limit" 'grep -q "^limit: " "$T/out.txt"'
rm -f "$l2.offsite.txt"
(cd "$T" && run safe --lite lite4 "$l2" remote:bucket/lite)
check "the lite_dir in the record is absolute" 'grep -q "^lite_dir $TP/lite4$" "$l2.offsite.txt"'
rm -f "$l2.offsite.txt"

# 14b. lite never writes to the card, into an offloaded card folder, or into a git working tree.
before=$(find "$T/cl" | LC_ALL=C sort | shasum)
run lite "$T/cl/LITE" "$ld"
check "lite on the card's own volume is refused and names the volume" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "card.s own volume" && [ ! -e "$T/cl/LITE" ] && [ "$before" = "$(find "$T/cl" | LC_ALL=C sort | shasum)" ]'
run lite "$ld/lite" "$ld"
check "lite inside the copy is still refused" '[ "$rc" -eq 2 ] && [ ! -e "$ld/lite" ]'
lcard cl2; mkdir "$T/al2"; FAKE_UUID=22222222-3333-4444-5555-666666666666 "$O" copy "$T/cl2" "$T/al2" --cam GH7 > /dev/null
ld2=$(ls -d "$T/al2"/GH7-*/); ld2=${ld2%/}
run lite "$ld/inner" "$ld2"
check "lite inside another card's offloaded folder is refused with a named error" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "offloaded card folder" && [ ! -e "$ld/inner" ]'
mkdir -p "$ld/DCIM/sub"; run lite "$ld/DCIM/sub/deep" "$ld2"
check "lite deep inside an offloaded card folder is refused" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "offloaded card folder"'
rmdir "$ld/DCIM/sub"
mkdir -p "$T/repo/.git" "$T/repo/sub"
run lite "$T/repo/sub/out" "$ld2"
check "lite inside a git working tree is refused with a named error" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "git working tree" && [ ! -e "$T/repo/sub/out" ]'
FAKE_UUID=22222222-3333-4444-5555-666666666666 run lite "$T/okfolder" "$ld2"
check "lite into a plain new folder elsewhere still works" '[ "$rc" -eq 0 ] && [ -s "$T/okfolder/offload-lite-manifest.txt" ]'

# 15d. Two videos needing one lite file, or names equal ignoring case, block and are named.
c="$T/cdup"; mkdir -p "$c/X" "$c/Y"
for f in X/A.mov X/A.mp4 X/A.mp4.mov Y/B001.mov Y/b001.mp4; do head -c 2000 /dev/urandom > "$c/$f"; done
mkdir "$T/adup"; run copy "$c" "$T/adup" --cam GH7
dd2=$(ls -d "$T/adup"/GH7-*/); dd2=${dd2%/}
mkdir -p "$T/litedup/X" "$T/litedup/Y"
for f in X/A.mov.mp4 X/A.mp4.mp4 Y/B001.mp4 Y/b001.mp4; do echo lite > "$T/litedup/$f"; done
run safe --lite "$T/litedup" "$dd2" remote:bucket/lite
check "videos that need the same lite file are blocked by name" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "X/A.mp4.mov" && printf "%s" "$out" | grep -q "X/A.mp4 " && [ ! -e "$dd2.offsite.txt" ]'
check "a case-only name clash says what happened, not a filter" 'printf "%s" "$out" | grep -q "Y/B001.mov" && printf "%s" "$out" | grep -q "ignoring case" && ! printf "%s" "$out" | grep -q "a filter"'

# 16. The lite copy names its card and is made by the tool. There is no command that writes a manifest
#     for files already in a folder, so no refusal can be followed by a relabel that ends SAFE.
lcard cardA; lcard cardB; echo more >> "$T/cardB/DCIM/A/C0001.XML"; mkdir "$T/aab"
run copy "$T/cardA" "$T/aab" --cam GH7; run copy "$T/cardB" "$T/aab" --cam GH7
idA=$("$O" id "$T/cardA" | awk '/card_id/ {print $2}'); idB=$("$O" id "$T/cardB" | awk '/card_id/ {print $2}')
dA="$T/aab/GH7-$(printf '%s' "$idA" | cut -c1-6)"; dB="$T/aab/GH7-$(printf '%s' "$idB" | cut -c1-6)"
make_lite "$T/lcA" "$dA"
M="$T/lcA/offload-lite-manifest.txt"
run safe --lite "$T/lcA" "$dA" remote:bucket/lite
check "a lite copy made for its own card is safe" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
rm -f "$dA.offsite.txt"
# Fourth fix: the card-volume refusal compares devices, so it holds for a folder deep on the card and fails closed.
before=$(find "$T/cardA" | LC_ALL=C sort | shasum)
run lite "$T/cardA/DCIM/LITE" "$dA"
check "lite deep on the card (DCIM/LITE) is refused and nothing is written" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "card.s own volume" && [ ! -e "$T/cardA/DCIM/LITE" ] && [ "$before" = "$(find "$T/cardA" | LC_ALL=C sort | shasum)" ]'
STAT_BIN=/nonexistent/stat run lite "$T/lcdev" "$dA"
check "lite refuses when the device cannot be read" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "cannot read the device" && [ ! -e "$T/lcdev" ]'
mv "$dA.card-root" "$dA.card-root.gone"; run lite "$T/lcdev" "$dA"
check "lite refuses when the card root is not recorded" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "no mount point is recorded" && [ ! -e "$T/lcdev" ]'
printf '%s\n' "$T/no-such-card" > "$dA.card-root"; run lite "$T/lcdev" "$dA"
check "lite refuses when the recorded card root is not mounted" '[ "$rc" -eq 2 ] && [ ! -e "$T/lcdev" ]'
mv "$dA.card-root.gone" "$dA.card-root"
# Fifth fix: the recorded card root must still hold this card (R1 stale mount folder, R2 same-named cards swapped).
mkdir "$T/stale"; printf '%s\n' "$TP/stale" > "$dA.card-root"; run lite "$T/lcdev" "$dA"
check "R1: a stale mount folder is not the card; lite refuses and says it cannot list the card" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "cannot list the card at" && [ ! -e "$T/lcdev" ]'
printf '%s\n' "$TP/cardB" > "$dA.card-root"; run lite "$T/cardA/LITE2" "$dA"
check "R2: another card at the recorded path is refused, nothing written on either card" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "is not card $idA" && [ ! -e "$T/cardA/LITE2" ] && [ ! -e "$T/cardB/LITE2" ]'
printf '%s\n' "$TP/cardA" > "$dA.card-root"
# Fifth fix: copy on an already offloaded card writes a missing .card-root.
rm -f "$dA.card-root"; run copy "$T/cardA" "$T/aab" --cam GH7
check "copy again on an offloaded card adds the missing .card-root" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "already offloaded" && [ "$(cat "$dA.card-root")" = "$TP/cardA" ]'
# Sixth fix: copy again refreshes .card-root (remount); a copy with no volume UUID and a missing diskutil are refused apart.
printf '%s\n' "$TP/stale" > "$dA.card-root"; cp -R "$T/cardA" "$T/m2"
run copy "$T/m2" "$T/aab" --cam GH7
check "copy again from the remounted card rewrites .card-root" '[ "$rc" -eq 0 ] && [ "$(cat "$dA.card-root")" = "$TP/m2" ]'
run lite "$T/lcm2" "$dA"
check "after the remount, lite to another disk works" '[ "$rc" -eq 0 ] && [ -s "$T/lcm2/offload-lite-manifest.txt" ]'
printf '%s\n' "$TP/cardA" > "$dA.card-root"
DISKUTIL_BIN=/nonexistent/diskutil run lite "$T/lcdev" "$dA"
check "lite names a missing diskutil as the cause" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "no diskutil" && [ ! -e "$T/lcdev" ]'
cp "$dA.card.json" "$dA.card.json.keep"; sed 's/"volume_uuid": "[^"]*"/"volume_uuid": "none"/' "$dA.card.json.keep" > "$dA.card.json"
run lite "$T/lcdev" "$dA"
check "lite refuses a copy that recorded no volume UUID and says to run copy on the mount point" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "no volume UUID" && printf "%s" "$out" | grep -q "mount point" && [ ! -e "$T/lcdev" ]'
mv "$dA.card.json.keep" "$dA.card.json"
# Reproduction 1: card A's lite folder offered for card B is refused, and no command relabels it.
run safe --lite "$T/lcA" "$dB" remote:bucket/lite
check "another card's lite folder is not safe and does not say to regenerate" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "another card" && printf "%s" "$out" | grep -q "fresh lite folder" && ! printf "%s" "$out" | grep -q "regenerate" && [ ! -e "$dB.offsite.txt" ]'
run lite "$T/lcA" "$dB"
check "lite refuses to write into card A's existing folder for card B" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "fresh lite folder" && grep -q "^card_id $idA" "$M"'
run lite-manifest "$T/lcA" "$dB"
check "there is no lite-manifest command to relabel a folder" '[ "$rc" -eq 2 ] && grep -q "^card_id $idA" "$M"'
run safe --lite "$T/lcA" "$dB" remote:bucket/lite
check "after those attempts card A's folder is still not safe for card B" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
# Reproduction 2: a reused folder, card B's clip over card A's folder, A's other clip left over.
make_lite "$T/lcB" "$dB"
cp "$T/lcB/DCIM/A/C0001.mp4" "$T/lcA/DCIM/A/C0001.mp4"
run safe --lite "$T/lcA" "$dB" remote:bucket/lite
check "a reused lite folder (one of B's files, the rest A's) is not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
run lite "$T/lcA" "$dB"
check "the reused folder cannot be re-made over" '[ "$rc" -eq 2 ] && ! printf "%s" "$out" | grep -q "made "'
# Reproduction 3: card A's clip copied over card B's lite file.
cp "$T/lcB/DCIM/A/c0002.mp4" "$T/lcB.keep"
make_lite "$T/lcA2" "$dA"; cp "$T/lcA2/DCIM/A/c0002.mp4" "$T/lcB/DCIM/A/c0002.mp4"
run safe --lite "$T/lcB" "$dB" remote:bucket/lite
check "card A's clip over card B's lite file is not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "does not match" && printf "%s" "$out" | grep -q "fresh lite folder" && ! printf "%s" "$out" | grep -q "regenerate"'
run lite "$T/lcB" "$dB"
check "lite will not rewrite that folder either" '[ "$rc" -eq 2 ]'
cp "$T/lcB.keep" "$T/lcB/DCIM/A/c0002.mp4"
run safe --lite "$T/lcB" "$dB" remote:bucket/lite
check "card B's own lite copy, restored, is safe" '[ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
rm -f "$dB.offsite.txt"
# A lite copy with no manifest (made before this tool, or by hand) is not safe and says how to make one.
mv "$T/lcB/offload-lite-manifest.txt" "$T/lcB.m"
run safe --lite "$T/lcB" "$dB" remote:bucket/lite
check "a lite copy with no manifest is not safe and says to make a fresh one" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "offload.sh lite <new-lite-dir>" && [ ! -e "$dB.offsite.txt" ]'
mv "$T/lcB.m" "$T/lcB/offload-lite-manifest.txt"
M="$T/lcB/offload-lite-manifest.txt"
cp "$M" "$M.keep"
sed 's/^card_id .*/card_id 000000000000/' "$M.keep" > "$M"
run safe --lite "$T/lcB" "$dB" remote:bucket/lite
check "a manifest naming another card is not safe" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "another card" && [ ! -e "$dB.offsite.txt" ]'
awk -F'\t' 'BEGIN{OFS="\t"} $1=="file" && !d {$5="0000000000000000000000000000000000000000000000000000000000000000"; d=1} {print}' "$M.keep" > "$M"
run safe --lite "$T/lcB" "$dB" remote:bucket/lite
check "a manifest source hash that is not this card's is not safe" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "does not match" && [ ! -e "$dB.offsite.txt" ]'
awk -F'\t' 'BEGIN{OFS="\t"} $1=="file" && !d {$5=""; d=1} {print}' "$M.keep" > "$M"
run safe --lite "$T/lcB" "$dB" remote:bucket/lite
check "a manifest line with an empty source hash is not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'
cp "$M.keep" "$M"; rm -f "$M.keep"
# Finding 3: an unreadable lite file gives no hash, and that is a refusal.
if [ "$(id -u)" != 0 ]; then
  chmod 000 "$T/lcB/DCIM/A/C0001.mp4"
  run safe --lite "$T/lcB" "$dB" remote:bucket/lite
  check "an unreadable lite file is not safe" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT" && printf "%s" "$out" | grep -q "cannot hash"'
  chmod 644 "$T/lcB/DCIM/A/C0001.mp4"
fi
# Finding 4: lite writes only into a new folder that is not the copy, inside it, or a file.
run lite "$dB" "$dB"
check "lite refuses the copy itself as the lite folder" '[ "$rc" -eq 2 ]'
run lite "$dB/newlite" "$dB"
check "lite refuses a folder inside the copy, and writes nothing" '[ "$rc" -eq 2 ] && printf "%s" "$out" | grep -q "inside it" && [ ! -e "$dB/newlite" ] && [ ! -e "$dB/offload-lite-manifest.txt" ]'
run lite "$T/cardB" "$dB"
check "lite refuses the card (a non-empty folder) and writes nothing to it" '[ "$rc" -eq 2 ] && [ ! -e "$T/cardB/offload-lite-manifest.txt" ]'
mkdir "$T/emptylite2"; run lite "$T/emptylite2" "$dB"
check "lite accepts an empty folder and fills it" '[ "$rc" -eq 0 ] && [ -s "$T/emptylite2/offload-lite-manifest.txt" ]'
run lite "$T/lcB.x" "$T/aab/nothing"
check "lite for a copy that is not offloaded is refused" '[ "$rc" -eq 2 ] && [ ! -e "$T/lcB.x" ]'
run lite "$T/lcB.y"
check "lite needs both directories" '[ "$rc" -eq 2 ]'
# A failed transcode leaves no half-made lite folder.
out=$(FFMPEG_BIN=false "$O" lite "$T/lcfail" "$dB" 2>&1); rc=$?
check "a failed ffmpeg leaves no lite folder" '[ "$rc" -eq 1 ] && [ ! -e "$T/lcfail" ] && ! ls "$T" | grep -q "lcfail.partial"'
# The manifest records the bytes ffmpeg read (the Proxy .mp4 for a BRAW), and the lite file is made from them.
check "the manifest ties each lite file to the original it read" 'grep -q "DCIM/B/R001.mp4	[0-9a-f]\{64\}	DCIM/B/R001.braw	[0-9a-f]\{64\}	DCIM/B/Proxy/R001.mp4	[0-9a-f]\{64\}" "$T/lcB/offload-lite-manifest.txt"'
# Finding 5: two different clips whose lite files have the same bytes are refused.
cat > "$T/bin/ffmpeg-same" <<'EOF'
#!/bin/sh
while [ $# -gt 1 ]; do shift; done
printf 'same\n' > "$1"
EOF
chmod +x "$T/bin/ffmpeg-same"
out=$(FFMPEG_BIN="$T/bin/ffmpeg-same" "$O" lite "$T/lcsame" "$dB" 2>&1); rc=$?
check "lite refuses one lite output for two different clips" '[ "$rc" -eq 1 ] && [ ! -e "$T/lcsame" ]'
cp -R "$T/lcB" "$T/lcdup"; cp "$T/lcdup/DCIM/A/C0001.mp4" "$T/lcdup/DCIM/A/c0002.mp4"
h1=$(awk -F'\t' '$2=="DCIM/A/C0001.mp4"{print $3}' "$T/lcdup/offload-lite-manifest.txt")
awk -F'\t' -v h="$h1" 'BEGIN{OFS="\t"} $2=="DCIM/A/c0002.mp4"{$3=h} {print}' "$T/lcdup/offload-lite-manifest.txt" > "$T/lcdup/m" && mv "$T/lcdup/m" "$T/lcdup/offload-lite-manifest.txt"
run safe --lite "$T/lcdup" "$dB" remote:bucket/lite
check "safe refuses lite files with the same bytes for different originals" '[ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -q "SAFE TO FORMAT"'

# 13. Help is the header only.
out=$("$O" --help)
check "help stops at the header" 'printf "%s" "$out" | grep -q "safe to format" && ! printf "%s" "$out" | grep -q "set -u"'

# 14. The synthetic card fixture and the verdicts on it (its own file, so CI needs no new step).
"$SRC/production/tests/test-card-fixture.sh" || fail=1

[ "$fail" -eq 0 ] && echo "offload: all cases pass"
exit "$fail"
