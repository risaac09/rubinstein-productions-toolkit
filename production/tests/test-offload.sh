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
cat > "$T/bin/diskutil" <<'EOF'
#!/bin/sh
printf '   Volume Name:              CARD\n   Volume UUID:              %s\n' "${FAKE_UUID:-11111111-2222-3333-4444-555555555555}"
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
chmod +x "$T/bin/"*
export DISKUTIL_BIN="$T/bin/diskutil" DF_BIN="$T/bin/df" RCLONE_BIN="$T/bin/rclone" SMBUTIL_BIN="$T/bin/smbutil"
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
# lite_tree DIR: the lite files the naming rule asks for.
lite_tree() {
  mkdir -p "$1/DCIM/A" "$1/DCIM/B" "$1/DCIM/C"
  for f in DCIM/A/C0001.mp4 DCIM/A/c0002.mp4 DCIM/B/R001.mp4 DCIM/C/X1.mov.mp4 DCIM/C/X1.mxf.mp4; do echo lite > "$1/$f"; done
}
lcard cl; mkdir "$T/al"
run copy "$T/cl" "$T/al" --cam GH7
ld=$(ls -d "$T/al"/GH7-*/); ld=${ld%/}
lite_tree "$T/lite"
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
echo lite > "$T/lite/DCIM/C/X1.mxf.mp4"
rm "$T/lite/DCIM/C/X1.mov.mp4"; echo lite > "$T/lite/DCIM/C/X1.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "shared stem needs the full-name .mp4, not the stem .mp4" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "DCIM/C/X1.mov.mp4"'
rm "$T/lite/DCIM/C/X1.mp4"; echo lite > "$T/lite/DCIM/C/X1.mov.mp4"
rm "$T/lite/DCIM/B/R001.mp4"
run safe --lite "$T/lite" "$ld" remote:bucket/lite
check "a BRAW is not covered by its Proxy in the copy alone; its lite file is needed" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -q "DCIM/B/R001.mp4"'
echo lite > "$T/lite/DCIM/B/R001.mp4"
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
mkdir -p "$T/litemx/DCIM/A" "$T/litemx/DCIM/B"; echo l > "$T/litemx/DCIM/A/V1.mp4"; echo l > "$T/litemx/DCIM/B/R1.mp4"
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
lite_tree "$T/lite4"; echo lite > "$T/lite4/DCIM/A/C0099.mp4"
run safe --lite "$T/lite4" "$l2" remote:bucket/lite
check "a lite dir with an .mp4 no video on this card needs is not safe" '[ "$rc" -eq 1 ] && printf "%s" "$out" | grep -qi "C0099.mp4" && [ ! -e "$l2.offsite.txt" ]'
run safe "$l2" remote:bucket/x
rm -rf "$T/lite4"; lite_tree "$T/lite4"
(cd "$T" && run safe --lite lite4 "$l2" remote:bucket/lite; echo "$rc" > "$T/rc.out"; printf '%s' "$out" > "$T/out.txt")
check "a lite pass after a full pass keeps the full record" '[ "$(cat "$T/rc.out")" = 0 ] && grep -q "^tier full" "$l2.offsite.txt" && ! grep -q "^tier lite" "$l2.offsite.txt" && grep -q "earlier full-tier record" "$T/out.txt"'
check "the SAFE output states the lite limit" 'grep -q "^limit: " "$T/out.txt"'
rm -f "$l2.offsite.txt"
(cd "$T" && run safe --lite lite4 "$l2" remote:bucket/lite)
check "the lite_dir in the record is absolute" 'grep -q "^lite_dir $TP/lite4$" "$l2.offsite.txt"'
rm -f "$l2.offsite.txt"

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

# 13. Help is the header only.
out=$("$O" --help)
check "help stops at the header" 'printf "%s" "$out" | grep -q "safe to format" && ! printf "%s" "$out" | grep -q "set -u"'

[ "$fail" -eq 0 ] && echo "offload: all cases pass"
exit "$fail"
