#!/bin/sh
# Build the BRAW frame helper against the installed Blackmagic RAW SDK. Output: ./braw2ppm
set -e
SDK="${BRAW_SDK:-/Applications/Blackmagic RAW/Blackmagic RAW SDK/Mac}"
cd "$(dirname "$0")"
clang++ -std=c++14 -O2 -I"$SDK/Include" braw2ppm.cpp "$SDK/Include/BlackmagicRawAPIDispatch.cpp" \
  -framework CoreFoundation -framework CoreServices -o braw2ppm
echo "built $(pwd)/braw2ppm"
