#!/bin/sh
# Fetch the third-party video-use skill (Browser Use, MIT) at a pinned commit
# into tools/video-editor/video-use/. Nothing from upstream is stored in this repo.
set -eu

PIN="92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66"
REPO_URL="https://github.com/browser-use/video-use"

here=$(cd "$(dirname "$0")" && pwd)
dest="$here/video-use"

if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
  echo "fetch-video-use: $dest exists and is not empty; refusing to overwrite." >&2
  echo "Move it aside or remove it yourself, then run this again." >&2
  exit 1
fi

# If anything below fails before the pin is verified, remove the .git folder we
# just made (and the folder itself if it is then empty), so a re-run starts clean.
cleanup() {
  rm -rf "$dest/.git"
  rmdir "$dest" 2>/dev/null || true
}
trap cleanup EXIT

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

trap - EXIT
echo "video-use at $got in $dest"
