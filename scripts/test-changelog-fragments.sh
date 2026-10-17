#!/usr/bin/env bash
# test-changelog-fragments.sh: exercise scripts/changelog-fragments in throwaway
# repositories. Includes the two-branch merge that motivated the convention.
# Exit 1 on any failure.
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@localhost GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@localhost

R="$TMP/repo"; mkdir -p "$R/scripts" "$R/changelog.d"
cp "$SRC/scripts/changelog-fragments" "$R/scripts/"
cp "$SRC/changelog.d/README.md" "$R/changelog.d/"
printf '# Changelog\n\nPreamble.\n\n## Unreleased\n\n- Old entry.\n\n### 2026-10-01\n\n- Dated entry.\n' > "$R/CHANGELOG.md"
echo base > "$R/a.txt"
git -C "$R" init -q -b main && git -C "$R" add -A && git -C "$R" commit -q -m base
CF="$R/scripts/changelog-fragments"
inrepo() { (cd "$R" && "$@"); }

# new: writes a dated, valid fragment.
f="$(inrepo "$CF" new first "Added a thing. (#1)")"
case "$f" in changelog.d/*-first.md) ok "new writes a dated fragment" ;; *) bad "new path: $f" ;; esac
inrepo "$CF" lint >/dev/null && ok "lint accepts a valid fragment" || bad "lint rejects a valid fragment"
rm -f "$R/$f"

# lint rejects malformed fragments.
lintbad() { # name content
  printf '%b' "$2" > "$R/changelog.d/$1.md"
  if inrepo "$CF" lint >/dev/null 2>&1; then bad "lint accepted: $1"; else ok "lint rejects: $1"; fi
  rm -f "$R/changelog.d/$1.md"
}
lintbad empty ''
lintbad heading '# Title\n'
lintbad bullet '- text\n'
lintbad blank 'one\n\ntwo\n'
lintbad skipnoreason 'skip:\n'
lintbad UPPER 'text\n'
if inrepo "$CF" new "bad slug" "x" >/dev/null 2>&1; then bad "new accepted a slug with a space"; else ok "new rejects a bad slug"; fi

# pr rules, on branches.
git -C "$R" checkout -q -b nofrag; echo x >> "$R/a.txt"; git -C "$R" commit -qam change
if inrepo "$CF" pr main >/dev/null 2>&1; then bad "pr passed with no fragment"; else ok "pr fails with no fragment"; fi
inrepo "$CF" new nofrag "Changed a." >/dev/null; git -C "$R" add -A; git -C "$R" commit -qm frag
inrepo "$CF" pr main >/dev/null 2>&1 && ok "pr passes with a fragment" || bad "pr failed with a fragment"

git -C "$R" checkout -q main; git -C "$R" checkout -q -b direct
printf -- '- Direct edit.\n' > "$TMP/line"
awk -v l="$(cat "$TMP/line")" '{print} $0=="## Unreleased"{getline x; print x; print l}' "$R/CHANGELOG.md" > "$TMP/cl" && cp "$TMP/cl" "$R/CHANGELOG.md"
inrepo "$CF" new direct "Also a fragment." >/dev/null
git -C "$R" add -A; git -C "$R" commit -qm direct
out="$(inrepo "$CF" pr main 2>&1)" && bad "pr passed a direct Unreleased edit" || { case "$out" in *"under '## Unreleased'"*) ok "pr fails a direct Unreleased edit" ;; *) bad "wrong message: $out" ;; esac; }

git -C "$R" checkout -q main; git -C "$R" checkout -q -b preamble
sed -i.bak 's/^Preamble\./Preamble, reworded./' "$R/CHANGELOG.md"; rm -f "$R/CHANGELOG.md.bak"
inrepo "$CF" new preamble "Reworded the preamble." >/dev/null; git -C "$R" add -A; git -C "$R" commit -qm pre
inrepo "$CF" pr main >/dev/null 2>&1 && ok "pr allows editing outside Unreleased" || bad "pr blocked a preamble edit"

git -C "$R" checkout -q main; git -C "$R" checkout -q -b skipper
inrepo "$CF" new skipper "skip: typo only" >/dev/null; echo y >> "$R/a.txt"; git -C "$R" add -A; git -C "$R" commit -qm s
inrepo "$CF" pr main >/dev/null 2>&1 && ok "pr accepts a skip fragment" || bad "pr rejected a skip fragment"

git -C "$R" checkout -q main; git -C "$R" checkout -q -b export
mkdir -p "$R/phase-zero"; echo z > "$R/phase-zero/z.sh"; git -C "$R" add -A; git -C "$R" commit -qm "Export kit from stack-data abc"
inrepo "$CF" pr main >/dev/null 2>&1 && ok "pr exempts kit-export paths" || bad "pr demanded a fragment for an export"

# Two parallel branches merge with no conflict; compile then folds both in.
git -C "$R" checkout -q main
for b in one two; do
  git -C "$R" checkout -q -b "par-$b" main
  echo "$b" > "$R/file-$b.txt"
  inrepo "$CF" new "par-$b" "Entry from branch $b." >/dev/null
  git -C "$R" add -A; git -C "$R" commit -qm "$b"
done
git -C "$R" checkout -q main
git -C "$R" merge -q --no-edit par-one && git -C "$R" merge -q --no-edit par-two && ok "two branches merge with no conflict" || bad "merge conflicted"

inrepo "$CF" compile --dry-run 2>/dev/null | grep -q 'Entry from branch one' && ok "compile --dry-run lists entries" || bad "dry run missing an entry"
inrepo "$CF" compile >/dev/null && ok "compile runs" || bad "compile failed"
[ -z "$(find "$R/changelog.d" -type f ! -name README.md)" ] && ok "compile removes the fragments" || bad "fragments left behind"
sed -n '/^## Unreleased/,/^### /p' "$R/CHANGELOG.md" > "$TMP/unrel"
grep -q 'Entry from branch one' "$TMP/unrel" && grep -q 'Entry from branch two' "$TMP/unrel" && grep -q 'Old entry' "$TMP/unrel" && ok "compile keeps old entries and adds new" || bad "Unreleased wrong: $(cat "$TMP/unrel")"
[ "$(grep -c '^- ' "$R/CHANGELOG.md")" -eq 4 ] && ok "no entry lost or duplicated" || bad "entry count: $(grep -c '^- ' "$R/CHANGELOG.md")"
grep -q 'Dated entry' "$R/CHANGELOG.md" && ok "dated section untouched" || bad "dated section lost"

# Byte-for-byte round trip: split the Unreleased entries into fragments, empty
# the section, compile, and the file must come back identical.
git -C "$R" checkout -q main
cp "$R/CHANGELOG.md" "$TMP/orig"
cat > "$R/CHANGELOG.md" <<'EOT'
# Changelog

Preamble.

## Unreleased

- Newest entry (#9).
- Middle entry with `code` and a [link](x.md).
- Oldest entry.

### 2026-10-01

- Dated entry.
EOT
cp "$R/CHANGELOG.md" "$TMP/want"
awk '/^## Unreleased/{u=1;print;next} /^### /{u=0} u&&/^- /{next} {print}' "$TMP/want" | cat -s > "$TMP/emptied"
awk '/^## Unreleased/{print;print "";next} {print}' "$TMP/emptied" | cat -s > "$R/CHANGELOG.md"
i=0; total=$(grep -c '^- ' "$TMP/want" | tr -d ' ')
# the dated section's bullet is not an Unreleased entry
total=$(awk '/^## Unreleased/{u=1;next} /^### /{u=0} u&&/^- /{c++} END{print c}' "$TMP/want")
awk '/^## Unreleased/{u=1;next} /^### /{u=0} u&&/^- /{sub(/^- /,"");print}' "$TMP/want" | while IFS= read -r line; do
  i=$((i + 1))
  printf '%s\n' "$line" > "$R/changelog.d/9-$(printf '%04d' $((total - i + 1)))-rt.md"
done
inrepo "$CF" compile >/dev/null
cmp -s "$R/CHANGELOG.md" "$TMP/want" && ok "compile reproduces the changelog byte for byte" || { bad "round trip differs"; diff "$TMP/want" "$R/CHANGELOG.md" | head; }
git -C "$R" checkout -q -- CHANGELOG.md 2>/dev/null; cp "$TMP/orig" "$R/CHANGELOG.md"

# Roadmap status fragments.
mkdir -p "$R/docs"
cat > "$R/docs/ROADMAP.md" <<'EOT'
# Roadmap

## Amendment 2

1. **Two item.**

   **Status: executed.** Old.
2. **Second item.**

## Amendment 3

### Engineering

1. **Three one.**

   **Status: executed 2026-10-16.** Kept.
2. **Three two.** Body.

### Added

30. **Last item.**

   **Status: executed.** Kept too.
EOT
git -C "$R" add -A; git -C "$R" commit -qm roadmap
cp "$R/docs/ROADMAP.md" "$TMP/road0"
inrepo "$CF" status A3.2 two '**Status: executed 2026-10-17.** Done.' >/dev/null && ok "status writes a roadmap fragment" || bad "status failed"
inrepo "$CF" status A3.30 last '**Update 2026-10-17:** More, with `a\\b` and `&`.' >/dev/null
inrepo "$CF" status A2.1 two1 '**Status: executed.** Again.' >/dev/null
inrepo "$CF" lint >/dev/null && ok "lint accepts roadmap fragments" || bad "lint rejects roadmap fragments"
printf 'item: nope\ntext\n' > "$R/changelog.d/roadmap/bad.md"
inrepo "$CF" lint >/dev/null 2>&1 && bad "lint accepted a bad item key" || ok "lint rejects a bad item key"
rm -f "$R/changelog.d/roadmap/bad.md"
inrepo "$CF" compile --dry-run 2>&1 | grep -q 'Done\.' && ok "dry run shows the roadmap diff" || bad "dry run missed roadmap"
inrepo "$CF" compile >/dev/null && ok "compile applies roadmap statuses" || bad "roadmap compile failed"
cat > "$TMP/road1" <<'EOT'
# Roadmap

## Amendment 2

1. **Two item.**

   **Status: executed.** Old.

   **Status: executed.** Again.
2. **Second item.**

## Amendment 3

### Engineering

1. **Three one.**

   **Status: executed 2026-10-16.** Kept.
2. **Three two.** Body.

   **Status: executed 2026-10-17.** Done.

### Added

30. **Last item.**

   **Status: executed.** Kept too.

   **Update 2026-10-17:** More, with `a\\b` and `&`.
EOT
cmp -s "$R/docs/ROADMAP.md" "$TMP/road1" && ok "statuses land under the right items" || { bad "roadmap wrong"; diff "$TMP/road1" "$R/docs/ROADMAP.md"; }
[ -z "$(find "$R/changelog.d/roadmap" -type f 2>/dev/null)" ] && ok "compile removes roadmap fragments" || bad "roadmap fragments left"
inrepo "$CF" status A3.99 ghost 'Nothing.' >/dev/null
inrepo "$CF" compile >/dev/null 2>&1 && bad "compile accepted a missing item" || ok "compile fails on a missing item"
rm -f "$R"/changelog.d/roadmap/*.md
git -C "$R" add -A; git -C "$R" commit -qm after

# pr: a direct status line fails, a fragment passes.
git -C "$R" checkout -q -b rdirect main
awk '{print} /Kept too/{print ""; print "   **Status: executed 2026-10-17.** Direct."}' "$R/docs/ROADMAP.md" > "$TMP/rm" && cp "$TMP/rm" "$R/docs/ROADMAP.md"
inrepo "$CF" new rdirect "Roadmap edit." >/dev/null; git -C "$R" add -A; git -C "$R" commit -qm rdirect
out="$(inrepo "$CF" pr main 2>&1)" && bad "pr passed a direct roadmap status" || { case "$out" in *"Add a fragment"*ROADMAP*|*ROADMAP*"Add a fragment"*) ok "pr fails a direct roadmap status" ;; *) bad "wrong message: $out" ;; esac; }
git -C "$R" checkout -q main

echo
[ "$fail" -eq 0 ] && echo "all changelog fragment tests passed" || { echo "changelog fragment tests FAILED"; exit 1; }
