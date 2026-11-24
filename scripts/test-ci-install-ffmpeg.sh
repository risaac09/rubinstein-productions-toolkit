#!/usr/bin/env bash
# test-ci-install-ffmpeg.sh: run scripts/ci-install-ffmpeg against stand-in
# sudo, timeout, apt-get, ffmpeg and ffprobe commands. Three cases: apt works,
# apt fails twice then works, apt never works. Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

B="$TMP/bin"; mkdir -p "$B"
printf '#!/bin/sh\nexec "$@"\n' > "$B/sudo"
printf '#!/bin/sh\nshift 3\nexec "$@"\n' > "$B/timeout"   # drops -k 10 SECONDS
printf '#!/bin/sh\n' > "$B/dpkg"
# apt-get fails while the count in $APT_FAILS is above zero, counting down once per call.
cat > "$B/apt-get" <<'APT'
#!/bin/sh
echo "$*" >> "$APT_LOG"
n=$(cat "$APT_FAILS")
if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$APT_FAILS"; exit 100; fi
APT
printf '#!/bin/sh\necho "ffmpeg version test"\n' > "$B/ffmpeg"
printf '#!/bin/sh\necho "ffprobe version test"\n' > "$B/ffprobe"
printf '#!/bin/sh\n' > "$B/sleep"
chmod +x "$B"/*
export APT_LOG="$TMP/apt.log" APT_FAILS="$TMP/fails"

run() { echo "$1" > "$APT_FAILS"; : > "$APT_LOG"; PATH="$B:$PATH" "$SRC/scripts/ci-install-ffmpeg" > "$TMP/out" 2> "$TMP/err"; }

run 0; rc=$?
[ $rc -eq 0 ] && [ "$(wc -l < "$APT_LOG")" -eq 2 ] && grep -q "ffmpeg version" "$TMP/out" \
  && ok "apt works: one update, one install, version printed" || bad "apt works (rc $rc)"
grep -q "Acquire::Retries=3" "$APT_LOG" && grep -q "install -y -qq ffmpeg" "$APT_LOG" \
  && ok "apt is called with the timeouts and the package" || bad "apt arguments"

run 2; rc=$?
[ $rc -eq 0 ] && [ "$(grep -c "trying again" "$TMP/err")" -eq 2 ] \
  && ok "apt fails twice, then works: exit 0 after two retries" || bad "two failures then success (rc $rc)"

run 99; rc=$?
[ $rc -eq 1 ] && grep -q "3 times" "$TMP/err" && [ "$(wc -l < "$APT_LOG")" -eq 3 ] \
  && ok "apt never works: exit 1 after three tries" || bad "always failing (rc $rc)"

exit $fail
