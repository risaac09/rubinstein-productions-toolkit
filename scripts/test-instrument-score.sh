#!/usr/bin/env bash
# test-instrument-score.sh: check methodology/instrument/score.sh against the
# worked example and against files it must refuse. Exit 1 on any failure.
set -u
cd "$(dirname "$0")/.." || exit
SCORE=methodology/instrument/score.sh
EX=methodology/instrument/responses-example.csv
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# 1. The worked example reproduces the output recorded in measurement-instrument.md.
if "$SCORE" "$EX" 2>&1 | diff -u methodology/instrument/expected-output.txt - >/dev/null; then ok "worked example matches expected-output.txt"; else bad "worked example differs from expected-output.txt"; fi

# 2. Hand-computed anchors, so the golden file cannot drift on its own.
out=$("$SCORE" "$EX")
for want in \
  "delivery        narrative_clarity           6   4.17" \
  "delivery        internal_alignment          6   3.33" \
  "delivery          6   8.67         4        1          1    50.0" \
  "followup-3mo      6   8.83         4        2          0    66.7" \
  "followup-3mo    staff            3   6.00   6.67   5.00   5.89  moderate" \
  "followup-3mo    all              6   7.33   7.50   6.33   7.06  strong" \
  "spread between group averages: 2.78"; do
  case "$out" in *"$want"*) ok "anchor: $want" ;; *) bad "missing anchor: $want" ;; esac
done

# 3. Engagement filter: an unknown id scores zero rows and prints no tables.
if "$SCORE" --engagement nope "$EX" | grep -q '^rows: 0$'; then ok "filter to an unknown engagement scores 0 rows"; else bad "engagement filter"; fi

# 3b. The pre-engagement point carries the Narrative Clarity item (Before figure) beside the consistency questions.
printf '%s\n%s\n%s\n' "$(head -1 "$EX")" "d,r1,staff,pre-engagement,2000-01-01,3,,,,,,,,5,5,4" "d,r2,staff,pre-engagement,2000-01-01,2,,,,,,,,4,4,3" > "$TMP/pre.csv"
if "$SCORE" "$TMP/pre.csv" | grep -q '^pre-engagement  narrative_clarity           2   2.50$'; then ok "pre-engagement scores narrative_clarity"; else bad "pre-engagement narrative_clarity"; fi

# 4. CRLF line endings score the same as LF.
sed 's/$/\r/' "$EX" > "$TMP/crlf.csv"
if "$SCORE" "$TMP/crlf.csv" | tail -n +2 | diff -q <(tail -n +2 methodology/instrument/expected-output.txt) - >/dev/null; then ok "CRLF input scores the same"; else bad "CRLF input"; fi

# 5. Files that must be refused: exit 1 and no scores.
HDR=$(head -1 "$EX")
refuse() {  # name, file
  if "$SCORE" "$2" >"$TMP/out" 2>"$TMP/err"; then bad "accepted: $1"; elif [ -s "$TMP/out" ]; then bad "printed scores for: $1"; elif [ ! -s "$TMP/err" ]; then bad "no message for: $1"; else ok "refuses $1"; fi
}
printf '%s\n' "wrong,header" > "$TMP/h.csv";                                         refuse "a wrong header" "$TMP/h.csv"
printf '%s\n%s\n' "$HDR" "d,r1,staff,delivery,2000-01-01,6,,,,,,,,,," > "$TMP/l.csv";   refuse "a Likert value of 6" "$TMP/l.csv"
printf '%s\n%s\n' "$HDR" "d,r1,staff,delivery,2000-01-01,,,,,,,,11,,," > "$TMP/n.csv"; refuse "an NPS of 11" "$TMP/n.csv"
printf '%s\n%s\n' "$HDR" "d,r1,staff,delivery,2000-01-01,,,,,,,,,5,5," > "$TMP/c.csv";  refuse "a partial consistency row" "$TMP/c.csv"
printf '%s\n%s\n' "$HDR" "d,r1,staff,delivery,2000-01-01,,,,,,,,,0,5,5" > "$TMP/z.csv"; refuse "a consistency value of 0" "$TMP/z.csv"
printf '%s\n%s\n' "$HDR" "d,r1,staff,sometime,2000-01-01,,,,,,,,5,,," > "$TMP/p.csv"; refuse "an unknown assessment point" "$TMP/p.csv"
printf '%s\n%s\n%s\n' "$HDR" "d,r1,staff,delivery,2000-01-01,,,,,,,,5,,," "d,r1,staff,delivery,2000-01-02,,,,,,,,6,,," > "$TMP/d.csv"; refuse "a duplicate respondent row" "$TMP/d.csv"
printf '%s\n%s\n' "$HDR" "d,r1,staff,delivery,2000-01-01,3,3" > "$TMP/s.csv";        refuse "a short row" "$TMP/s.csv"

[ "$fail" -eq 0 ] && echo "test-instrument-score: PASS"
exit "$fail"
