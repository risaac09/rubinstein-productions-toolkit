#!/bin/sh
# verify-copy.sh: copy a card (or any folder) to one or more destinations,
# hash the source once, and check every copy against that manifest.
#
#   sh verify-copy.sh SRC DEST [DEST ...]
#
# Each DEST must not exist yet, or be an empty folder, and its parent must
# exist: a missing parent is how an unplugged drive looks, so it refuses
# rather than creating one. Folders are compared by physical path, so no
# spelling (./x, an absolute path, a symlink) gets a DEST inside SRC or one
# folder counted twice; a DEST inside a git work tree is refused too (card
# media never goes in a repository). SRC is only read. Junk the OS writes (.DS_Store,
# ._*, .Spotlight-V100, .fseventsd, .Trashes, .TemporaryItems) is left out of
# the copy and the manifest. File times are kept (cp -p).
#
# Writes DEST/ and DEST.SHA256SUMS beside it, one "<sha256>  <path>" line per
# file: the format `shasum -a 256 -c` and `sha256sum -c` read from inside
# DEST. Prints a line per destination, then how many copies verified. Only a
# person knows whether two verified copies sit on two different devices, so
# the script counts and says what is still missing; it never says "format".
#
# Hashing: sha256sum, else shasum -a 256, else python3's hashlib (a-Shell has
# python3; whether it has the other two is a probe row). CP and PYTHON name
# the commands used (defaults cp, python3).
# Exit: 0 every copy verified, 1 a copy failed its check, 2 refused.
set -u

CP="${CP:-cp}"
PYTHON="${PYTHON:-python3}"
ABC_SHA256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
PYHASH='
import hashlib, sys
f = sys.stdin.buffer if sys.argv[1] == "-" else open(sys.argv[1], "rb")
h = hashlib.sha256()
for b in iter(lambda: f.read(1 << 20), b""):
    h.update(b)
print(h.hexdigest())
'

die() { echo "verify-copy: $*" >&2; exit 2; }
usage() { echo "usage: sh verify-copy.sh SRC DEST [DEST ...]" >&2; exit 2; }
strip() { # a path without trailing slashes ("/" stays "/"), with ./ before a leading -
  s=$1
  while [ "${#s}" -gt 1 ] && [ "${s%/}" != "$s" ]; do s=${s%/}; done
  case "$s" in -*) s="./$s" ;; esac
  printf '%s' "$s"
}
here=$(pwd -P) || die "cannot read the current folder"
phys() { # DIR: its physical path (symlinks and .. resolved) in rp. cd here and back,
  # never in a subshell: on a-Shell a subshell's cd moves the shell too. ./ keeps CDPATH out.
  case "$1" in /*) rp=$1 ;; *) rp=./$1 ;; esac
  cd -P "$rp" || die "cannot open $1"
  rp=$(pwd -P)
  cd "$here" || die "cannot return to $here"
}
git_tree() { # PHYSICAL_PATH: true when it or a folder above it holds .git
  g=$1
  while [ -n "$g" ]; do [ -e "$g/.git" ] && return 0; g=${g%/*}; done
  [ -e /.git ]
}

[ $# -ge 2 ] || usage
src=$(strip "$1"); shift
[ -d "$src" ] || die "$src is not a folder"
phys "$src"; src_p=$rp

seen="
"
for d in "$@"; do
  d=$(strip "$d")
  case "$d" in */*) parent=${d%/*}; [ -n "$parent" ] || parent=/ ;; *) parent=. ;; esac
  [ -d "$parent" ] || die "$parent does not exist (unplugged drive?); nothing was created"
  if [ -d "$d" ]; then phys "$d"; dp=$rp; else phys "$parent"; dp=${rp%/}/${d##*/}; fi
  [ "$dp" != "$src_p" ] || die "$d is the source"
  case "$dp" in "${src_p%/}"/*) die "$d is inside the source" ;; esac
  case "$seen" in *"
$dp
"*) die "$d is named twice; one folder is one copy" ;; esac
  seen="$seen$dp
"
  ! git_tree "$dp" || die "$d is inside a git work tree; card media never goes in one"
  if [ -e "$d" ]; then
    [ -d "$d" ] || die "$d exists and is not a folder"
    [ -z "$(ls -A "$d" 2>/dev/null)" ] || die "$d is not empty; nothing is merged or overwritten"
  fi
  [ ! -e "$d.SHA256SUMS" ] || die "$d.SHA256SUMS already exists; nothing is overwritten"
done

if [ "$(printf abc | sha256sum 2>/dev/null | cut -d' ' -f1)" = "$ABC_SHA256" ]; then hasher=sha256sum
elif [ "$(printf abc | shasum -a 256 2>/dev/null | cut -d' ' -f1)" = "$ABC_SHA256" ]; then hasher=shasum
elif [ "$(printf abc | "$PYTHON" -c "$PYHASH" - 2>/dev/null)" = "$ABC_SHA256" ]; then hasher=python
else die "no working SHA-256 tool (tried sha256sum, shasum -a 256, $PYTHON hashlib)"
fi
hash_file() { # reads through a redirect, so a name starting with - is safe
  case $hasher in
    sha256sum) sha256sum < "$1" | cut -d' ' -f1 ;;
    shasum) shasum -a 256 < "$1" | cut -d' ' -f1 ;;
    python) "$PYTHON" -c "$PYHASH" - < "$1" ;;
  esac
}

tmp="${TMPDIR:-/tmp}"
tmp="${tmp%/}/verify-copy.$$"
mkdir "$tmp" || die "cannot make $tmp (set TMPDIR to a writable folder)"
trap 'rm -rf "$tmp"' EXIT
trap 'exit 1' INT TERM HUP

# Files under SRC, relative, sorted, junk pruned. find prints SRC/<path>; awk
# keeps <path> (no cd, so no subshell changes the shell's folder on a-Shell).
# The physical path, so a symlinked SRC is listed and not taken as empty.
# shortcut: a file name holding a newline breaks the listing; cameras never write one.
find "$src_p" \( -name .Spotlight-V100 -o -name .fseventsd -o -name .Trashes -o -name .TemporaryItems \) -prune \
    -o -type f ! -name .DS_Store ! -name '._*' -print |
  awk -v p="${src_p%/}/" 'index($0, p) == 1 { print substr($0, length(p) + 1) }' | LC_ALL=C sort > "$tmp/list"
nfiles=$(wc -l < "$tmp/list" | tr -d ' ')
[ "$nfiles" -gt 0 ] || die "$src has no files to copy"

echo "verify-copy: hashing $nfiles files in $src ($hasher)"
bytes=0
while IFS= read -r rel; do
  h=$(hash_file "$src/$rel") || die "cannot read $src/$rel"
  [ -n "$h" ] || die "cannot hash $src/$rel"
  printf '%s  %s\n' "$h" "$rel" >> "$tmp/sums"
  bytes=$((bytes + $(wc -c < "$src/$rel")))
done < "$tmp/list"

verified=0
total=0
for d in "$@"; do
  d=$(strip "$d")
  # Every DEST was empty, with no manifest, when checked above: one holding either
  # now is an earlier DEST under another name (a case-only difference, a dangling link).
  if [ -e "$d.SHA256SUMS" ] || [ -n "$(ls -A "$d" 2>/dev/null)" ]; then
    echo "skipped: $d is the same folder as an earlier DEST; not counted"
    continue
  fi
  total=$((total + 1))
  mkdir -p "$d" || die "cannot make $d"
  cp "$tmp/sums" "$d.SHA256SUMS" || die "cannot write $d.SHA256SUMS"
  bad=0
  while IFS= read -r rel; do
    case "$rel" in */*) mkdir -p "$d/${rel%/*}" ;; esac
    # Counted once, by the check below, which finds it missing or different.
    "$CP" -p "$src/$rel" "$d/$rel" || echo "verify-copy: copy failed: $rel" >&2
  done < "$tmp/list"
  while IFS= read -r line; do
    want=${line%%  *}
    rel=${line#*  }
    got=""
    [ -f "$d/$rel" ] && got=$(hash_file "$d/$rel")
    if [ "$got" != "$want" ]; then
      echo "verify-copy: MISMATCH $d/$rel" >&2
      bad=$((bad + 1))
    fi
  done < "$tmp/sums"
  if [ "$bad" -eq 0 ]; then
    verified=$((verified + 1))
    echo "verified: $d ($nfiles files, $bytes bytes; manifest $d.SHA256SUMS)"
  else
    echo "FAILED: $d ($bad of $nfiles files missing or different)"
  fi
done

if [ "$verified" -lt "$total" ]; then
  echo "result: $verified of $total copies verified. Keep the card; fix the failed copy and run again to a new folder."
  exit 1
fi
if [ "$verified" -ge 2 ]; then
  echo "result: $verified verified copies. The card can be formatted only if two of them are on different devices."
else
  echo "result: 1 verified copy. Make a second, on another device, before formatting the card."
fi
