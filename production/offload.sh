#!/usr/bin/env bash
# offload.sh: copy a camera card to archive storage, verify it byte for byte,
# and say "safe to format" only once an offsite copy also checks out.
#
#   offload.sh id   <card>                          card id, listing hash, counts
#   offload.sh copy <card> <dest-parent> --cam CAM  copy, verify, write sidecars
#   offload.sh safe <copy-dir> <rclone-remote>      offsite check, then the verdict
#
# copy writes <dest-parent>/<CAM>-<id6>/ plus <CAM>-<id6>.SHA256SUMS and
# <CAM>-<id6>.card.json beside it. It works inside _partial-<CAM>-<id6>/ and
# renames only after every file's read-back hash equals the card's. It never
# writes to the card, never deletes, and never creates <dest-parent>: a parent
# that is missing is how a dropped network share looks, so it refuses.
#
# The card id is the first 12 hex of sha256(VolumeUUID + the sorted
# "relpath size" listing), OS junk excluded and mtimes ignored, so a time-zone
# change keeps the id and an added clip changes it. The listing hash (the same
# without the VolumeUUID) matches a dual-slot twin.
#
# Exit: 0 ok, 1 copy or check failed, 2 refused (usage, gate, destination).
# Check a finished copy later: cd <CAM>-<id6> && shasum -a 256 -c ../<CAM>-<id6>.SHA256SUMS
set -u -o pipefail

DD_BIN="${DD_BIN:-dd}"
DF_BIN="${DF_BIN:-df}"
DISKUTIL_BIN="${DISKUTIL_BIN:-diskutil}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
OFFLOAD_NOW="${OFFLOAD_NOW:-}"
# Read-back bypasses the page cache where dd supports it (macOS), so the hash
# comes from the destination, not from RAM. GNU dd's direct I/O fails on tmpfs.
if [ -z "${READ_FLAGS+x}" ]; then
  if [ "$(uname)" = Darwin ]; then READ_FLAGS="iflag=direct"; else READ_FLAGS=""; fi
fi

die()  { echo "offload: $*" >&2; exit 2; }
fail() { echo "offload: $*" >&2; exit 1; }
sha()  { shasum -a 256 | cut -d' ' -f1; }
now()  { if [ -n "$OFFLOAD_NOW" ]; then echo "$OFFLOAD_NOW"; else date -u +%Y-%m-%dT%H:%M:%SZ; fi; }
size() { wc -c < "$1" | tr -d ' '; }

# Files on the card, relative, sorted, junk pruned. One per line.
# shortcut: a file name holding a newline breaks the listing; camera cards never write one.
card_find() { # DIR f|d
  (cd "$1" && find . \( -name .Spotlight-V100 -o -name .fseventsd -o -name .Trashes \
      -o -name .TemporaryItems -o -name 'System Volume Information' \) -prune \
      -o -type "$2" ! -name . ! -name .DS_Store ! -name '._*' -print) | sed 's|^\./||' | LC_ALL=C sort
}
card_files() { card_find "$1" f; }
card_dirs()  { card_find "$1" d; }
listing() { card_files "$1" | while IFS= read -r f; do printf '%s %s\n' "$f" "$(size "$1/$f")"; done; }

volume_uuid() {
  local u
  u=$("$DISKUTIL_BIN" info "$1" 2>/dev/null | awk -F': *' '/Volume UUID/ {print $2; exit}')
  echo "${u:-none}"
}

# Sets ID, LHASH, NFILES, NBYTES, VUUID for card $1.
card_id() {
  local lst; lst=$(listing "$1") || die "cannot list $1"
  [ -n "$lst" ] || die "no files on $1"
  VUUID=$(volume_uuid "$1")
  LHASH=$(printf '%s\n' "$lst" | sha)
  ID=$(printf '%s\n%s\n' "$VUUID" "$lst" | sha | cut -c1-12)
  NFILES=$(printf '%s\n' "$lst" | wc -l | tr -d ' ')
  NBYTES=$(printf '%s\n' "$lst" | awk '{s += $NF} END {printf "%.0f", s}')
}

# Prints nothing when the read fails (pipefail), so a failed read is never a hash.
read_hash() { local h; h=$("$DD_BIN" if="$1" bs=1048576 $READ_FLAGS 2>/dev/null | sha) && echo "$h"; }

cmd_id() {
  [ $# -eq 1 ] || die "usage: offload.sh id <card>"
  [ -d "$1" ] || die "not a directory: $1"
  card_id "$1"
  printf 'card_id %s\nlisting_hash %s\nvolume_uuid %s\nfiles %s\nbytes %s\n' "$ID" "$LHASH" "$VUUID" "$NFILES" "$NBYTES"
}

cmd_copy() {
  local card="" parent="" cam=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --cam) [ $# -ge 2 ] || die "--cam needs a value"; cam="$2"; shift 2 ;;
      -*) die "unknown option $1" ;;
      *) if [ -z "$card" ]; then card="$1"; elif [ -z "$parent" ]; then parent="$1"; else die "too many arguments"; fi; shift ;;
    esac
  done
  [ -n "$card" ] && [ -n "$parent" ] && [ -n "$cam" ] || die "usage: offload.sh copy <card> <dest-parent> --cam CAM"
  printf '%s' "$cam" | grep -qE '^[A-Z0-9]{2,12}$' || die "--cam must be 2 to 12 of A-Z 0-9, got '$cam'"
  [ -d "$card" ] || die "card not found: $card"
  [ -d "$parent" ] || die "destination parent missing: $parent (a dropped share looks like this; it is never created)"
  [ -w "$parent" ] || die "destination parent not writable: $parent"
  # Footage and sidecars never go inside a git work tree (walk up from the physical path).
  local g; g=$(cd -P "$parent" && pwd -P) || die "cannot resolve $parent"
  while [ -n "$g" ] && [ ! -e "$g/.git" ]; do g=${g%/*}; done
  [ -z "$g" ] && [ ! -e /.git ] || die "destination $parent is inside the git work tree ${g:-/}; write it outside any repository"

  card_id "$card"
  local name; name="$cam-$(printf '%s' "$ID" | cut -c1-6)"
  local final="$parent/$name" partial="$parent/_partial-$name"
  local sums="$parent/$name.SHA256SUMS" json="$parent/$name.card.json"

  if [ -e "$final" ]; then
    if [ -f "$json" ] && grep -q "\"card_id\": \"$ID\"" "$json"; then
      echo "already offloaded: $final (card $ID)"; exit 0
    fi
    die "destination exists: $final; never offloading into it"
  fi
  # Sidecars are written just before the rename, so a run cut off between the two
  # leaves them beside _partial; only sidecars from another card are refused.
  if [ -e "$json" ] && ! grep -q "\"card_id\": \"$ID\"" "$json"; then
    die "sidecar for another card exists: $json"
  fi

  local twin; twin=$(grep -l "\"listing_hash\": \"$LHASH\"" "$parent"/*.card.json 2>/dev/null | head -n 1)
  [ -z "$twin" ] || echo "note: same clips as $(basename "$twin" .card.json) (a dual-slot twin?); copying anyway"

  local free; free=$("$DF_BIN" -Pk "$parent" | awk 'NR == 2 {print $4}')
  [ -n "$free" ] && [ "$free" -ge $(( NBYTES / 1024 * 11 / 10 + 1 )) ] \
    || die "not enough space on $parent: need 1.1x $NBYTES bytes"

  echo "card $ID ($cam): $NFILES files, $NBYTES bytes -> $final"
  # Plain mkdir, parents first, so a share that drops mid-run fails here
  # instead of building a tree on the boot disk.
  [ -d "$partial" ] || mkdir "$partial" || fail "cannot create $partial"
  local d
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    [ -d "$partial/$d" ] || mkdir "$partial/$d" || fail "cannot create $partial/$d"
  done < <(card_dirs "$card")

  # shortcut: one file at a time, no per-file time budget; add lanes and a budget when card.sh wraps this.
  local f
  copy_one() {
    "$DD_BIN" if="$card/$1" of="$partial/$1" bs=1048576 2>/dev/null || return 1
    touch -c -r "$card/$1" "$partial/$1"
  }
  while IFS= read -r f; do
    # A same-size file left by an earlier try is not recopied; the hash below judges it.
    if [ -f "$partial/$f" ] && [ "$(size "$partial/$f")" = "$(size "$card/$f")" ]; then continue; fi
    copy_one "$f" || fail "copy failed: $f (card untouched, $partial kept)"
  done < <(card_files "$card")

  local tmp; tmp=$(mktemp -d) || fail "mktemp failed"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" EXIT
  : > "$tmp/sums"
  local bad=0 a b
  while IFS= read -r f; do
    a=$(shasum -a 256 < "$card/$f" | cut -d' ' -f1)
    b=$(read_hash "$partial/$f") || fail "cannot read back $partial/$f (is direct I/O supported here? try READ_FLAGS=)"
    if [ "$a" != "$b" ]; then
      echo "mismatch, copying once more: $f" >&2
      copy_one "$f" && b=$(read_hash "$partial/$f")
      [ "$a" = "$b" ] || { echo "mismatch after retry: $f" >&2; bad=1; }
    fi
    printf '%s  %s\n' "$a" "$f" >> "$tmp/sums"
  done < <(card_files "$card")
  [ "$bad" -eq 0 ] || fail "copy did not verify; card untouched, $partial kept"
  [ "$(wc -l < "$tmp/sums" | tr -d ' ')" = "$NFILES" ] || fail "file count changed during the copy"
  # Every file in the copy must be on the card: no strays (a Finder .DS_Store) ride along.
  local extra; extra=$( (cd "$partial" && find . -type f | sed 's|^\./||' | LC_ALL=C sort) | LC_ALL=C comm -23 - <(card_files "$card"))
  [ -z "$extra" ] || fail "files in $partial that are not on the card: $(printf '%s' "$extra" | tr '\n' ' ')"

  local id_before="$ID"; card_id "$card"
  [ "$ID" = "$id_before" ] || fail "card changed during the copy ($id_before -> $ID); $partial kept"

  cp "$tmp/sums" "$sums" || fail "cannot write $sums"
  printf '{\n  "card_id": "%s",\n  "listing_hash": "%s",\n  "volume_uuid": "%s",\n  "cam": "%s",\n  "files": %s,\n  "bytes": %s,\n  "offloaded_utc": "%s"\n}\n' \
    "$ID" "$LHASH" "$VUUID" "$cam" "$NFILES" "$NBYTES" "$(now)" > "$tmp/json"
  cp "$tmp/json" "$json" || fail "cannot write $json"
  cmp -s "$tmp/sums" "$sums" && cmp -s "$tmp/json" "$json" || fail "sidecar read-back differs"
  mv "$partial" "$final" || fail "rename failed: $partial"
  echo "verified: $final"
  echo "NOT safe to format yet: run 'offload.sh safe $final <remote>' once the offsite copy lands"
}

cmd_safe() {
  [ $# -eq 2 ] || die "usage: offload.sh safe <copy-dir> <rclone-remote>"
  local dir="${1%/}" remote="$2"
  [ -d "$dir" ] || die "not a directory: $dir"
  # Only an rclone remote ("name:path") is offsite; a bare path, even one on
  # another disk, is a local folder and never earns the verdict.
  # shortcut: a remote of type local still passes; rclone's config is not read here.
  printf '%s' "$remote" | grep -qE '^[A-Za-z0-9_][A-Za-z0-9_. -]+:' \
    || die "not an rclone remote: $remote (want name:path); a local folder is not an offsite copy"
  local json="$dir.card.json"
  [ -f "$json" ] && [ -f "$dir.SHA256SUMS" ] || die "no sidecars beside $dir: it was not offloaded by this tool"
  local id; id=$(sed -n 's/.*"card_id": "\([0-9a-f]*\)".*/\1/p' "$json")
  [ -n "$id" ] || die "no card_id in $json"
  # The offsite copy came from this folder, so first prove the folder still
  # matches the card's hashes; rclone alone would pass a copy that rotted before upload.
  local base; base=$(basename "$dir")
  (cd "$dir" && shasum -a 256 -c "../$base.SHA256SUMS" > /dev/null 2>&1) \
    || fail "NOT safe to format: $dir no longer matches $base.SHA256SUMS; card $id"
  local log; log=$(mktemp) || fail "mktemp failed"
  # shellcheck disable=SC2064
  trap "rm -f '$log'" EXIT
  "$RCLONE_BIN" check "$dir" "$remote" --one-way --log-file "$log" --log-level NOTICE --ask-password=false < /dev/null
  local rc=$?
  # B2 and others report a file they could not hash as a pass on size alone.
  if grep -q "hashes could not be checked" "$log"; then
    fail "NOT safe to format: rclone could not compare some hashes (see its log); card $id"
  fi
  [ "$rc" -eq 0 ] || fail "NOT safe to format: rclone check exited $rc; card $id"
  printf 'card_id %s\nremote %s\nchecked_utc %s\n' "$id" "$remote" "$(now)" > "$dir.offsite.txt" \
    || fail "cannot write $dir.offsite.txt"
  echo "SAFE TO FORMAT: card $id is verified at $dir and at $remote"
}

[ $# -ge 1 ] || die "usage: offload.sh id|copy|safe ..."
sub="$1"; shift
case "$sub" in
  id) cmd_id "$@" ;;
  copy)
    # Hold off idle sleep for the copy; a closed lid without a display still sleeps.
    if [ -z "${OFFLOAD_CAFFEINATED:-}" ] && command -v caffeinate > /dev/null; then
      OFFLOAD_CAFFEINATED=1 exec caffeinate -is "$0" copy "$@"
    fi
    cmd_copy "$@" ;;
  safe) cmd_safe "$@" ;;
  -h|--help) sed -n '2,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command $sub" ;;
esac
