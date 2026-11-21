#!/usr/bin/env bash
# offload.sh: copy a camera card to archive storage, verify it byte for byte,
# and say "safe to format" only once an offsite copy also checks out.
#
#   offload.sh id   <card>                          card id, listing hash, counts
#   offload.sh copy <card> <dest-parent> --cam CAM  copy, verify, write sidecars
#   offload.sh safe <copy-dir> <rclone-remote>      offsite check, then the verdict
#   offload.sh safe --lite <lite-dir> <copy-dir> <rclone-remote>
#                                                   lite tier: the full copy verifies locally, and a
#                                                   lite copy (one H.264 .mp4 per video) is offsite
#   offload.sh lite <new-lite-dir> <copy-dir>       make the lite copy and its manifest in one step
#
# copy and safe refuse to start on a share mounted from a slow address (Wi-Fi,
# the tailnet): smbutil must report OFFLOAD_FAST_HOST (10.10.10.2) for the share,
# or --route-override takes the slow route on purpose.
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
# Lite tier: <lite-dir> mirrors <copy-dir>; each video (mov mp4 m4v mts m2ts mxf avi mkv braw,
# any case) at <dir>/<stem>.<ext> needs <dir>/<stem>.mp4, or <dir>/<name>.mp4 (full name plus
# .mp4) when two videos in that folder share a stem. A .mp4 in a Proxy/ folder beside a .braw of
# the same stem is that BRAW's lite and needs none of its own; non-video files need none. The
# card is safe when the full copy verifies and `rclone check --one-way <lite-dir> <remote>`
# passes; no full offsite copy is required. <copy>.offsite.txt records "tier lite" or "tier full".
#
# The lite copy names its card, and is made by this tool: `offload.sh lite <new-lite-dir> <copy-dir>`
# transcodes each video from the verified copy (ffmpeg) into a folder that does not exist yet (or is
# empty), and writes <lite-dir>/offload-lite-manifest.txt in the same step: the card id, and per lite
# file its hash and the hash of the original, checked against SHA256SUMS before and after the
# transcode. No command writes a manifest for files already in a folder, and `lite` refuses a
# non-empty folder, so another card's lite folder is never relabelled. safe --lite refuses a lite dir
# with no manifest, a manifest for another card, a lite file that no longer matches it, an unreadable
# lite file, or lite files with the same bytes for different originals. Limit: a manifest typed by hand in the same format
# would pass; the tool has no way to tell. A BRAW video is transcoded from its Proxy .mp4 (recorded in the manifest); with none, `lite` refuses.
#
# Exit: 0 ok, 1 copy or check failed, 2 refused (usage, gate, destination).
# Check a finished copy later: cd <CAM>-<id6> && shasum -a 256 -c ../<CAM>-<id6>.SHA256SUMS
set -u -o pipefail

DD_BIN="${DD_BIN:-dd}"
DF_BIN="${DF_BIN:-df}"
DISKUTIL_BIN="${DISKUTIL_BIN:-diskutil}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
SMBUTIL_BIN="${SMBUTIL_BIN:-smbutil}"
OFFLOAD_NOW="${OFFLOAD_NOW:-}"
# Network shares mount under OFFLOAD_SMB_ROOT; a share counts as fast only when
# smbutil reports it mounted from OFFLOAD_FAST_HOST (the wired address).
OFFLOAD_SMB_ROOT="${OFFLOAD_SMB_ROOT:-/Volumes}"
OFFLOAD_FAST_HOST="${OFFLOAD_FAST_HOST:-10.10.10.2}"
OFFLOAD_ROUTE_OVERRIDE="${OFFLOAD_ROUTE_OVERRIDE:-}"
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

# route_check DIR: when DIR sits on an SMB share under OFFLOAD_SMB_ROOT, the share
# must be mounted from the fast host. The mount point looks the same over Wi-Fi,
# a hostname on the tailnet, or 10GbE, and a slow route makes every copy and
# every hash read-back pay eightfold (2026-11-01: a 161 GB card, two safe checks
# and an import, about three hours, on an Archive mounted from a Wi-Fi address).
# A local disk under the root (a T7) reports no SERVER_NAME and passes.
# --route-override, or OFFLOAD_ROUTE_OVERRIDE=1, takes the slow route on purpose.
route_check() {
  local p root share srv
  p=$(cd -P "$1" 2>/dev/null && pwd -P) || return 0
  root=$(cd -P "$OFFLOAD_SMB_ROOT" 2>/dev/null && pwd -P) || return 0
  case "$p" in "$root"/*) ;; *) return 0 ;; esac
  share=${p#"$root"/}; share=${share%%/*}
  srv=$("$SMBUTIL_BIN" statshares -m "$root/$share" 2>/dev/null | awk '/SERVER_NAME/ {print $2; exit}')
  [ -n "$srv" ] || return 0
  [ "$srv" = "$OFFLOAD_FAST_HOST" ] && return 0
  if [ -n "$OFFLOAD_ROUTE_OVERRIDE" ]; then
    echo "note: $share is mounted from $srv, not $OFFLOAD_FAST_HOST; taking the slow route on --route-override" >&2
    return 0
  fi
  die "$share is mounted from $srv, not $OFFLOAD_FAST_HOST: a slow route (Wi-Fi or the tailnet) makes every copy and hash check pay eightfold; remount from the fast host, or pass --route-override to take it anyway"
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
  route_check "$parent"
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

REMOTE_RE='^[A-Za-z0-9_][A-Za-z0-9_. -]+:'

# lite_names: copy-relative paths on stdin; stdout is "L <lite file a video needs>" or
# "C <path>" for a video whose lite name another video also needs, "U <path>" for a file that blocks the verdict: an unknown extension, or a video the lite
# builder cannot make (r3d, and dng, which may be a CinemaDNG frame sequence). Fail closed: only the known non-video list needs nothing.
# The video list must cover production/rpresolve/card.py VIDEO (test-offload.sh checks it).
lite_names() {
  awk '
    { p[NR] = $0; n = NR }
    END {
      for (i = 1; i <= n; i++) {
        f = p[i]; d = ""; b = f
        if (match(f, /\/[^\/]*$/)) { d = substr(f, 1, RSTART - 1); b = substr(f, RSTART + 1) }
        if (!match(b, /\.[^.]*$/)) { print "U " f; continue }
        e = tolower(substr(b, RSTART + 1)); st = substr(b, 1, RSTART - 1)
        if (e ~ /^(r3d|dng)$/) { print "U " f; continue }
        if (e ~ /^(jpg|jpeg|heic|heif|png|tif|tiff|arw|cr2|cr3|nef|raf|rw2|orf|xml|bin|txt|json|csv|xmp|thm|wav|aif|aiff|mp3|aac|m4a|flac|srt|cpi|bim|bdm|mpl|clpi|sif|rmd|ale)$/) continue
        if (e !~ /^(mov|mp4|m4v|mts|m2ts|mxf|avi|mkv|braw)$/) { print "U " f; continue }
        v++; D[v] = d; B[v] = b; S[v] = st; E[v] = e; cnt[d "/" st]++
        if (e == "braw") braw[d "/" st] = 1
      }
      for (k = 1; k <= v; k++) {
        d = D[k]
        if (E[k] == "mp4" && d ~ /(^|\/)Proxy$/) {
          pd = d; sub(/(^|\/)Proxy$/, "", pd)
          if (braw[pd "/" S[k]]) continue
        }
        o = (cnt[d "/" S[k]] > 1) ? B[k] ".mp4" : S[k] ".mp4"
        N[k] = (d == "" ? o : d "/" o); seen[tolower(N[k])]++
      }
      # Two videos that need one lite file (or two that differ only by case, one file on APFS): "C", never merged.
      for (k = 1; k <= v; k++) {
        if (!(k in N)) continue
        if (seen[tolower(N[k])] > 1) print "C " (D[k] == "" ? "" : D[k] "/") B[k]; else print "L " N[k] "\t" (D[k] == "" ? "" : D[k] "/") B[k]
      }
    }'
}

LITE_MANIFEST=offload-lite-manifest.txt
LITE_FRESH="make a fresh lite folder for this card with: offload.sh lite <new-lite-dir> <copy-dir>; never edit or reuse another card's lite folder"
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"

is_sha() { printf '%s' "$1" | grep -qE '^[0-9a-f]{64}$'; }

# lite_same_bytes MANIFEST: lite files that hold the same bytes though their originals differ (a transcoder
# that wrote one thing for two clips). Two byte-identical originals may share lite bytes.
lite_same_bytes() { awk -F'\t' '$1 == "file" { if ($3 in o && o[$3] != $5) print $2; o[$3] = $5 }' "$1"; }

# lite_manifest_check LITE COPY ID NEEDMAP: refuse (exit 1) unless the manifest names this card and
# covers every needed lite file with its current hash and the original's hash from SHA256SUMS.
lite_manifest_check() {
  local lite="$1" dir="$2" id="$3" needmap="$4" m="$1/$LITE_MANIFEST" base l s h w
  base=$(basename "$dir")
  [ -s "$m" ] || fail "NOT safe to format: the lite copy has no manifest naming its card ($LITE_MANIFEST), so it cannot be tied to card $id; $LITE_FRESH; a manifest written by hand does not count"
  [ "$(sed -n 's/^card_id //p' "$m" | head -n 1)" = "$id" ] \
    || fail "NOT safe to format: the lite manifest names another card, not $id; $LITE_FRESH"
  # Every manifest line: the original's hash must be this card's.
  local badsrc
  badsrc=$(awk -F'\t' 'NR == FNR { sum[substr($0, 67)] = substr($0, 1, 64); next } $1 == "file" && (length($5) != 64 || length($7) != 64 || sum[$4] != $5 || sum[$6] != $7) { print $4 }' "$dir.SHA256SUMS" "$m") \
    || fail "NOT safe to format: cannot read the lite manifest; card $id"
  [ -z "$badsrc" ] || fail "NOT safe to format: the lite manifest lists an original hash that does not match this card's $base.SHA256SUMS: $(printf '%s' "$badsrc" | tr '\n' ' '); card $id"
  local dup; dup=$(lite_same_bytes "$m")
  [ -z "$dup" ] || fail "NOT safe to format: lite files with the same bytes stand for different originals: $(printf '%s' "$dup" | tr '\n' ' '); card $id"
  while IFS=$'\t' read -r l s; do
    [ -n "$l" ] || continue
    h=$(shasum -a 256 < "$lite/$l" 2>/dev/null | cut -d' ' -f1)
    is_sha "$h" || fail "NOT safe to format: cannot hash lite file $l; card $id"
    w=$(printf 'file\t%s\t%s\t%s\t' "$l" "$h" "$s")
    awk -F'\t' -v w="$w" 'index($0, w) == 1 { f = 1 } END { exit !f }' "$m" \
      || fail "NOT safe to format: lite file $l does not match the lite manifest (changed since it was made, or not this card's clip $s); $LITE_FRESH; card $id"
  done <<NEEDMAP
$needmap
NEEDMAP
}

# cmd_lite LITE COPY: make the lite copy and its manifest in one step, into a folder that does not
# exist yet (or is empty). Each lite file is transcoded from the verified copy; the manifest records
# the hash of the original as read before and after the transcode, so the tie is made when the lite
# file is made, never afterwards. There is no command that writes a manifest for files already there.
cmd_lite() {
  [ $# -eq 2 ] || die "usage: offload.sh lite <new-lite-dir> <copy-dir>"
  local lite="${1%/}" dir="${2%/}"
  [ -d "$dir" ] && [ -f "$dir.card.json" ] && [ -f "$dir.SHA256SUMS" ] || die "no sidecars beside $dir: it was not offloaded by this tool"
  if [ -e "$lite" ] || [ -L "$lite" ]; then
    [ -d "$lite" ] && [ ! -L "$lite" ] && [ -z "$(ls -A "$lite" 2>/dev/null)" ] \
      || die "$lite already exists and is not an empty folder; $LITE_FRESH"
  fi
  local parent; parent=$(cd -P -- "$(dirname "$lite")" 2>/dev/null && pwd -P) || die "no parent folder for $lite"
  local p="$parent"
  while :; do
    [ "$p" -ef "$dir" ] && die "lite dir $lite is the copy or inside it; the lite tier needs its own tree"
    [ -f "$p.SHA256SUMS" ] && die "lite dir $lite is inside the offloaded card folder $p; the lite tier needs its own tree"
    [ -e "$p/.git" ] && die "lite dir $lite is inside a git working tree ($p); make the lite folder elsewhere"
    [ "$p" = / ] && break
    p=$(dirname "$p")
  done
  route_check "$dir"; route_check "$parent"
  lite="$parent/$(basename "$lite")"
  [ ! -f "$lite.SHA256SUMS" ] || die "lite dir $lite is an offloaded card folder; the lite tier needs its own tree"
  local cu; cu=$(sed -n 's/.*"volume_uuid": "\([^"]*\)".*/\1/p' "$dir.card.json")
  [ -z "$cu" ] || [ "$cu" = none ] || [ "$(volume_uuid "$parent")" != "$cu" ] \
    || die "lite dir $lite is on the card's own volume ($cu); the tool never writes to the card; make the lite folder on another disk"
  local id; id=$(sed -n 's/.*"card_id": "\([0-9a-f]*\)".*/\1/p' "$dir.card.json")
  [ -n "$id" ] || die "no card_id in $dir.card.json"
  local base; base=$(basename "$dir")
  (cd "$dir" && shasum -a 256 -c "../$base.SHA256SUMS" > /dev/null 2>&1) \
    || fail "$dir no longer matches $base.SHA256SUMS; no lite copy made"
  local cls bad
  cls=$(cut -c67- "$dir.SHA256SUMS" | lite_names | LC_ALL=C sort -u) || die "cannot list the videos in $dir"
  bad=$(printf '%s\n' "$cls" | sed -n 's/^[UC] //p' | tr '\n' ' ')
  [ -z "$bad" ] || die "no lite copy can cover these (unknown type, or a shared lite name): $bad"
  local map; map=$(printf '%s\n' "$cls" | sed -n 's/^L //p')
  [ -n "$map" ] || die "no video files in $dir, so a lite copy covers nothing"
  command -v "$FFMPEG_BIN" > /dev/null || die "no ffmpeg ($FFMPEG_BIN): cannot make the lite copy"
  local work="$lite.partial-$$"
  mkdir "$work" || die "cannot create $work"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" EXIT
  printf 'offload-lite-manifest 1\ncard_id %s\n' "$id" > "$work/$LITE_MANIFEST"
  local l s r sh h1 h2 lh n=0
  while IFS=$'\t' read -r l s; do
    [ -n "$l" ] || continue
    r="$s"
    # ffmpeg cannot read BRAW: the camera's Proxy .mp4 (also in SHA256SUMS) is what gets transcoded, and the manifest says so.
    case "$s" in *.[bB][rR][aA][wW])
      r=$(cut -c67- "$dir.SHA256SUMS" | awk -v d="$(dirname "$s")" -v b="$(basename "$s")" '
        BEGIN { st = b; sub(/\.[^.]*$/, "", st); want = (d == "." ? "" : d "/") "Proxy/" st }
        { q = $0; if (match(q, /\.[^.\/]*$/) && tolower(substr(q, RSTART)) == ".mp4" && substr(q, 1, RSTART - 1) == want) { print q; exit } }')
      [ -n "$r" ] || fail "$s has no Proxy .mp4 in the copy for ffmpeg to read; no lite copy made" ;;
    esac
    sh=$(awk -v p="$r" 'substr($0, 67) == p { print substr($0, 1, 64); exit }' "$dir.SHA256SUMS")
    is_sha "$sh" || fail "no recorded hash for $r in $base.SHA256SUMS; no lite copy made"
    h1=$(shasum -a 256 < "$dir/$r" 2>/dev/null | cut -d' ' -f1)
    [ "$h1" = "$sh" ] || fail "$r no longer matches its recorded hash; no lite copy made"
    mkdir -p "$work/$(dirname "$l")" || fail "cannot create folder for $l"
    "$FFMPEG_BIN" -nostdin -v error -y -i "$dir/$r" -map 0:v:0 -map '0:a?' -c:v libx264 -preset veryfast -crf 28 \
      -vf scale=-2:720 -c:a aac -b:a 96k -movflags +faststart "$work/$l" < /dev/null \
      || fail "ffmpeg failed on $s; no lite copy made"
    h2=$(shasum -a 256 < "$dir/$r" 2>/dev/null | cut -d' ' -f1)
    [ "$h2" = "$sh" ] || fail "$r changed while it was being transcoded; no lite copy made"
    [ -s "$work/$l" ] || fail "ffmpeg wrote no lite file for $s; no lite copy made"
    lh=$(shasum -a 256 < "$work/$l" | cut -d' ' -f1)
    is_sha "$lh" || fail "cannot hash lite file $l; no lite copy made"
    printf 'file\t%s\t%s\t%s\t%s\t%s\t%s\n' "$l" "$lh" "$s" "$(awk -v p="$s" 'substr($0, 67) == p { print substr($0, 1, 64); exit }' "$dir.SHA256SUMS")" "$r" "$sh" >> "$work/$LITE_MANIFEST"; n=$((n + 1))
  done <<NEEDMAP
$map
NEEDMAP
  local dup; dup=$(lite_same_bytes "$work/$LITE_MANIFEST")
  [ -z "$dup" ] || fail "lite files with the same bytes stand for different originals: $(printf '%s' "$dup" | tr '\n' ' '); no lite copy made"
  if [ -d "$lite" ]; then rmdir "$lite" || fail "cannot replace the empty folder $lite"; fi
  mv "$work" "$lite" || fail "cannot move the lite copy into place at $lite"
  trap - EXIT
  echo "made $lite: card $id, $n lite files, manifest $LITE_MANIFEST (upload it with them)"
}

cmd_safe() {
  local lite=""
  if [ "${1:-}" = --lite ]; then
    [ $# -ge 2 ] || die "--lite needs a directory"
    lite="${2%/}"; shift 2
  fi
  [ $# -eq 2 ] || die "usage: offload.sh safe [--lite <lite-dir>] <copy-dir> <rclone-remote>"
  local dir="${1%/}" remote="$2"
  [ -d "$dir" ] || die "not a directory: $dir"
  # A verdict record exists only after the latest run said SAFE: drop the old one before any check
  # (a failed run must not leave an earlier "safe" for card check and project status to read).
  local prev=""
  [ ! -f "$dir.offsite.txt" ] || prev=$(cat "$dir.offsite.txt")
  rm -f "$dir.offsite.txt" || die "cannot remove the old $dir.offsite.txt"
  route_check "$dir"
  if [ -n "$lite" ]; then
    [ -d "$lite" ] || die "not a directory: $lite"
    route_check "$lite"
    # By inode, not path text: case-insensitive APFS keeps the typed case. Absolute from here on,
    # so rclone never reads a name with a colon as a remote and the record is meaningful later.
    local p; p=$(cd -P -- "$lite" && pwd -P) || die "cannot resolve $lite"
    lite="$p"
    while :; do
      [ "$p" -ef "$dir" ] && die "lite dir $lite is the copy or inside it; the lite tier needs its own tree"
    [ -f "$p.SHA256SUMS" ] && die "lite dir $lite is inside the offloaded card folder $p; the lite tier needs its own tree"
    [ -e "$p/.git" ] && die "lite dir $lite is inside a git working tree ($p); make the lite folder elsewhere"
      [ "$p" = / ] && break
      p=$(dirname "$p")
    done
  fi
  # Only an rclone remote ("name:path") is offsite; a bare path, even one on
  # another disk, is a local folder and never earns the verdict.
  printf '%s' "$remote" | grep -qE "$REMOTE_RE" \
    || die "not an rclone remote: $remote (want name:path); a local folder is not an offsite copy"
  # Only a listed offsite backend passes; any other type (local, archive, union, combine, a backend
  # this script has not seen) is refused, so a new rclone backend fails closed. A wrapper (alias,
  # crypt, ...) passes only when its target resolves to a listed backend: the `remote =` line is
  # all that is read from `config show`, nothing else is kept or printed. Never contacts the remote.
  local r="$remote" hops=0 t n
  while :; do
    n=${r%%:*}
    # config show gives the type rclone resolves (a file section with any env override); a remote defined
    # only in the environment has no section, so its type is the column after its exact name in listremotes.
    t=$("$RCLONE_BIN" config show "$n" < /dev/null 2>/dev/null | sed -n 's/^type *= *//p' | head -n 1)
    [ -n "$t" ] || t=$("$RCLONE_BIN" listremotes --long < /dev/null 2>/dev/null | awk -v n="$n:" 'index($0, n) == 1 { split(substr($0, length(n) + 1), f, " "); print f[1]; exit }')
    case "$t" in
      "") die "cannot read the type of rclone remote '$n'; refusing" ;;
      b2|s3|drive|gcs|azureblob|dropbox|onedrive) break ;;
      alias|crypt|compress|chunker|hasher|cache)
        hops=$((hops + 1)); [ "$hops" -le 8 ] || die "alias chain from $remote is too deep; refusing"
        r=$("$RCLONE_BIN" config show "$n" < /dev/null 2>/dev/null | sed -n 's/^remote *= *//p' | head -n 1)
        printf '%s' "$r" | grep -qE "$REMOTE_RE" || die "$t '$n' does not point at an rclone remote; a local folder is not an offsite copy" ;;
      *) die "rclone remote '$n' is of type $t, not a known offsite backend (b2, s3, drive, gcs, azureblob, dropbox, onedrive); a local folder is not an offsite copy; refusing" ;;
    esac
  done
  local json="$dir.card.json"
  [ -f "$json" ] && [ -f "$dir.SHA256SUMS" ] || die "no sidecars beside $dir: it was not offloaded by this tool"
  local id; id=$(sed -n 's/.*"card_id": "\([0-9a-f]*\)".*/\1/p' "$json")
  [ -n "$id" ] || die "no card_id in $json"
  # The offsite copy came from this folder, so first prove the folder still
  # matches the card's hashes; rclone alone would pass a copy that rotted before upload.
  local base; base=$(basename "$dir")
  (cd "$dir" && shasum -a 256 -c "../$base.SHA256SUMS" > /dev/null 2>&1) \
    || fail "NOT safe to format: $dir no longer matches $base.SHA256SUMS; card $id"
  # Lite tier: every video needs its lite file, and those files are what rclone checks.
  local src="$dir" need="" needmap=""
  if [ -n "$lite" ]; then
    src="$lite"
    local cls bad clash
    cls=$(cut -c67- "$dir.SHA256SUMS" | lite_names | LC_ALL=C sort -u) || fail "cannot list the videos in $dir"
    bad=$(printf '%s\n' "$cls" | sed -n 's/^U //p' | tr '\n' ' ')
    [ -z "$bad" ] || fail "NOT safe to format: no lite copy can cover (unknown or unmakeable type): $bad; card $id"
    clash=$(printf '%s\n' "$cls" | sed -n 's/^C //p' | tr '\n' ' ')
    [ -z "$clash" ] || fail "NOT safe to format: these videos need the same lite file name (equal, or equal ignoring case), so one lite file cannot cover each: $clash; card $id"
    needmap=$(printf '%s\n' "$cls" | sed -n 's/^L //p')
    need=$(printf '%s\n' "$needmap" | cut -f1)
    [ -n "$need" ] || fail "NOT safe to format: no video files in $dir, so a lite copy covers nothing; card $id"
    local miss="" l
    while IFS= read -r l; do [ -s "$lite/$l" ] || miss="$miss $l"; done <<NEED
$need
NEED
    [ -z "$miss" ] || fail "NOT safe to format: lite copy is missing or empty for:$miss; card $id"
    # Clip numbers restart per card, so a lite tree built for another card can cover this one by name.
    # Partial signal only: an .mp4 in the lite dir that no video on this card needs means it holds another card's clips.
    local extra
    extra=$( (cd "$lite" && find . -type f -iname '*.mp4' | sed 's|^\./||' | tr 'A-Z' 'a-z' | LC_ALL=C sort) | LC_ALL=C comm -23 - <(printf '%s\n' "$need" | tr 'A-Z' 'a-z' | LC_ALL=C sort) ) \
      || fail "cannot list $lite"
    [ -z "$extra" ] || fail "NOT safe to format: the lite dir holds .mp4 files no video on this card needs (another card's lite tree?): $(printf '%s' "$extra" | tr '\n' ' '); card $id"
    lite_manifest_check "$lite" "$dir" "$id" "$needmap"
    need=$(printf '%s\n%s\n' "$need" "$LITE_MANIFEST")
  fi
  local log match; log=$(mktemp) && match=$(mktemp) || fail "mktemp failed"
  # shellcheck disable=SC2064
  trap "rm -f '$log' '$match'" EXIT
  # rclone reads any flag from RCLONE_<FLAG> (RCLONE_SIZE_ONLY, RCLONE_EXCLUDE): the
  # check runs without them, keeping the config, its password and remotes set in the environment.
  local bin="$RCLONE_BIN"
  ( for v in $(compgen -e); do
      case "$v" in RCLONE_CONFIG*|RCLONE_PASSWORD_COMMAND) ;; RCLONE_*) unset "$v" ;; esac
    done
    exec "$bin" check "$src" "$remote" --one-way --match "$match" --log-file "$log" --log-level NOTICE --ask-password=false < /dev/null )
  local rc=$?
  # B2 and others report a file they could not hash as a pass on size alone.
  if grep -q "hashes could not be checked" "$log"; then
    fail "NOT safe to format: rclone could not compare some hashes (see its log); card $id"
  fi
  [ "$rc" -eq 0 ] || fail "NOT safe to format: rclone check exited $rc; card $id"
  # A check that left files out (a filter, by any route) still exits 0: every
  # hashed file (lite tier: every lite file) must be among the matches.
  local unmatched; unmatched=$( { if [ -n "$lite" ]; then printf '%s\n' "$need"; else cut -c67- "$dir.SHA256SUMS"; fi; } | LC_ALL=C sort | LC_ALL=C comm -23 - <(LC_ALL=C sort "$match")) \
    || fail "NOT safe to format: cannot compare rclone's matches with $base.SHA256SUMS; card $id"
  [ -z "$unmatched" ] || fail "NOT safe to format: rclone did not match $(printf '%s' "$unmatched" | tr '\n' ' ') (a filter?); card $id"
  local tier="tier full"; [ -z "$lite" ] || tier=$(printf 'tier lite\nlite_dir %s' "$lite")
  # A lite pass does not undo an earlier full-tier record for this card: keep that one.
  if [ -n "$lite" ] && printf '%s\n' "$prev" | grep -qx "card_id $id" && printf '%s\n' "$prev" | grep -qx "tier full"; then
    printf '%s\n' "$prev" > "$dir.offsite.txt" || fail "cannot write $dir.offsite.txt"
    echo "note: the earlier full-tier record for this card is kept ($dir.offsite.txt)"
  else
    printf 'card_id %s\nremote %s\n%s\nchecked_utc %s\n' "$id" "$remote" "$tier" "$(now)" > "$dir.offsite.txt" \
      || fail "cannot write $dir.offsite.txt"
  fi
  if [ -n "$lite" ]; then
    echo "SAFE TO FORMAT: card $id is verified at $dir and a lite copy ($lite) is at $remote"
    echo "limit: the manifest was written when the lite files were made from this card; a hand-typed manifest would also pass"
  else echo "SAFE TO FORMAT: card $id is verified at $dir and at $remote"; fi
}

[ $# -ge 1 ] || die "usage: offload.sh id|copy|safe|lite ..."
sub="$1"; shift
# --route-override may sit anywhere after the subcommand.
args=()
# Exported, so the caffeinate re-exec of copy keeps it.
for a in "$@"; do case "$a" in --route-override) export OFFLOAD_ROUTE_OVERRIDE=1 ;; *) args+=("$a") ;; esac; done
set -- ${args[@]+"${args[@]}"}
case "$sub" in
  id) cmd_id "$@" ;;
  copy)
    # Hold off idle sleep for the copy; a closed lid without a display still sleeps.
    if [ -z "${OFFLOAD_CAFFEINATED:-}" ] && command -v caffeinate > /dev/null; then
      OFFLOAD_CAFFEINATED=1 exec caffeinate -is "$0" copy "$@"
    fi
    cmd_copy "$@" ;;
  safe) cmd_safe "$@" ;;
  lite) cmd_lite "$@" ;;
  -h|--help) sed -n '2,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command $sub" ;;
esac
