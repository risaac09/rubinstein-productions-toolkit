#!/bin/sh
# Fetch the third-party video-use skill (Browser Use, MIT) at a pinned commit
# into tools/video-editor/video-use/, then apply keep-source-frame-rate.patch
# (ours, hunks only) so renders keep the source frame rate. Nothing from
# upstream is stored in this repo.
set -eu

PIN="92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66"
REPO_URL="https://github.com/browser-use/video-use"

here=$(cd "$(dirname "$0")" && pwd)
dest="$here/video-use"
patch="$here/keep-source-frame-rate.patch"

if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
  echo "fetch-video-use: $dest exists and is not empty; refusing to overwrite." >&2
  echo "Move it aside or remove it yourself, then run this again." >&2
  exit 1
fi

# The guard above means $dest was absent or empty, so everything in it now came
# from this run. If anything below fails before the pin is verified, remove it
# so a re-run starts clean.
cleanup() {
  rm -rf "$dest"
}
trap cleanup EXIT
# A signal must run the EXIT trap too; plain sh does not do that on its own.
trap 'exit 1' INT TERM HUP

mkdir -p "$dest"
git -C "$dest" init -q
git -C "$dest" remote add origin "$REPO_URL"
# A shallow fetch of one commit is quick; fall back to a full fetch if the
# server will not serve a bare commit.
git -C "$dest" fetch -q --depth=1 origin "$PIN" || git -C "$dest" fetch -q origin
git -C "$dest" checkout -q --detach "$PIN"

got=$(git -C "$dest" rev-parse HEAD)
if [ "$got" != "$PIN" ]; then
  echo "fetch-video-use: expected $PIN but got $got." >&2
  exit 1
fi

# The pin is verified, so the patch must apply exactly. If upstream moved or the
# patch drifted, fail here rather than hand back a tree that still forces 24 fps.
if ! git -C "$dest" apply --check "$patch"; then
  echo "fetch-video-use: $patch does not apply to $PIN." >&2
  echo "Update the patch against that commit; nothing was left in $dest." >&2
  exit 1
fi
git -C "$dest" apply "$patch"

trap - EXIT
echo "video-use at $got in $dest, patched with $(basename "$patch")"
