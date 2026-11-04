#!/bin/sh
# Build the BRAW frame helper against the installed Blackmagic RAW SDK. Output: ./braw2ppm, or $BRAW2PPM_OUT
set -eu
SDK="${BRAW_SDK:-/Applications/Blackmagic RAW/Blackmagic RAW SDK/Mac}"
OUT="${BRAW2PPM_OUT:-$(cd "$(dirname "$0")" && pwd)/braw2ppm}"
cd "$(dirname "$0")"
clang++ -std=c++14 -O2 -I"$SDK/Include" braw2ppm.cpp "$SDK/Include/BlackmagicRawAPIDispatch.cpp" \
  -framework CoreFoundation -framework CoreServices -o "$OUT"
echo "built $OUT"
