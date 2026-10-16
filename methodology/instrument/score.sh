#!/usr/bin/env bash
# score.sh: score a responses CSV with the measurement instrument.
#
# Usage: score.sh [--engagement ID] responses.csv
#
# Procedure and field definitions: methodology/measurement-instrument.md.
# POSIX awk only, no other dependencies. Exit 0 on success, 1 when the file
# fails validation (nothing is scored), 2 on a usage error.
set -u

ENG=""
if [ "${1:-}" = "--engagement" ]; then
  [ $# -ge 2 ] || { echo "usage: $0 [--engagement ID] responses.csv" >&2; exit 2; }
  ENG="$2"; shift 2
fi
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 [--engagement ID] responses.csv" >&2; exit 2; }

awk -F, -v eng="$ENG" -v file="$1" '
BEGIN {
  HEADER = "engagement_id,respondent_id,stakeholder_group,assessment_point,response_date,narrative_clarity,internal_alignment,feeling_heard,safety_in_vulnerability,clarity_gained,co_creation_quality,representative_pride,nps,consistency_q1,consistency_q2,consistency_q3"
  NP = split("pre-engagement mid-point delivery followup-2wk followup-3mo", P, " ")
  for (i = 1; i <= NP; i++) prank[P[i]] = i
  NG = split("leadership staff board beneficiaries funders", G, " ")
  for (i = 1; i <= NG; i++) grank[G[i]] = i
  NI = split("narrative_clarity internal_alignment feeling_heard safety_in_vulnerability clarity_gained co_creation_quality representative_pride", ITEM, " ")
  bad = 0
}
function err(msg) { printf "%s: line %d: %s\n", file, NR, msg > "/dev/stderr"; bad = 1 }
function isint(s) { return s ~ /^[0-9]+$/ }
function band(m) { return m >= 9 ? "exceptional" : m >= 7 ? "strong" : m >= 5 ? "moderate" : m >= 3 ? "low" : "incoherent" }
{ sub(/\r$/, "") }
NR == 1 {
  if ($0 != HEADER) { err("header does not match the schema"); exit_now = 1; exit 1 }
  next
}
$0 == "" { next }
{
  if (NF != 16) { err("expected 16 fields, found " NF); next }
  if (eng != "" && $1 != eng) next
  if ($1 !~ /^[A-Za-z0-9][A-Za-z0-9_-]*$/) err("bad engagement_id")
  if ($2 !~ /^[A-Za-z0-9][A-Za-z0-9_-]*$/) err("bad respondent_id")
  if ($3 !~ /^[a-z][a-z0-9-]*$/ || $3 == "all") err("bad stakeholder_group")
  if (!($4 in prank)) err("unknown assessment_point: " $4)
  if ($5 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/) err("response_date is not YYYY-MM-DD")
  key = $1 SUBSEP $2 SUBSEP $4
  if (key in seen) err("duplicate engagement, respondent and assessment point")
  seen[key] = 1
  for (i = 6; i <= 12; i++) if ($i != "" && !(isint($i) && $i >= 1 && $i <= 5)) err(ITEM[i - 5] " must be blank or an integer 1 to 5")
  if ($13 != "" && !(isint($13) && $13 <= 10)) err("nps must be blank or an integer 0 to 10")
  cq = 0
  for (i = 14; i <= 16; i++) {
    if ($i != "") { cq++; if (!(isint($i) && $i >= 1 && $i <= 10)) err("consistency_q" (i - 13) " must be blank or an integer 1 to 10") }
  }
  if (cq != 0 && cq != 3) err("consistency answers must be all three or none")
  if (bad) next
  nrows++
  p = $4
  usedp[p] = 1
  for (i = 6; i <= 12; i++) if ($i != "") { ln[p, i]++; ls[p, i] += $i }
  if ($13 != "") {
    nn[p]++; ns[p] += $13
    if ($13 >= 9) np_[p]++; else if ($13 >= 7) npas[p]++; else nd[p]++
  }
  if (cq == 3) {
    g = $3
    usedg[p, g] = 1; allg[g] = 1
    cn[p, g]++; cn[p, "all"]++
    for (i = 14; i <= 16; i++) { cs[p, g, i] += $i; cs[p, "all", i] += $i }
    avg = ($14 + $15 + $16) / 3
    ca[p, g] += avg; ca[p, "all"] += avg
    usedcp[p] = 1
  }
}
END {
  if (exit_now) exit 1
  if (bad) exit 1
  printf "instrument-score: %s\n", file
  printf "engagement: %s\n", (eng == "" ? "all" : eng)
  printf "rows: %d\n", nrows

  printf "\nLIKERT (1-5), mean per item\n"
  printf "%-15s %-25s %3s %6s\n", "point", "item", "n", "mean"
  for (a = 1; a <= NP; a++) {
    p = P[a]; if (!(p in usedp)) continue
    for (i = 6; i <= 12; i++) if (ln[p, i] > 0)
      printf "%-15s %-25s %3d %6.2f\n", p, ITEM[i - 5], ln[p, i], ls[p, i] / ln[p, i]
  }

  printf "\nNPS (0-10), mean and net\n"
  printf "%-15s %3s %6s %9s %8s %10s %7s\n", "point", "n", "mean", "promoters", "passives", "detractors", "net"
  for (a = 1; a <= NP; a++) {
    p = P[a]; if (!(p in nn)) continue
    printf "%-15s %3d %6.2f %9d %8d %10d %7.1f\n", p, nn[p], ns[p] / nn[p], np_[p], npas[p], nd[p], 100 * (np_[p] - nd[p]) / nn[p]
  }

  printf "\nCONSISTENCY (1-10), mean per group\n"
  printf "%-15s %-14s %3s %6s %6s %6s %6s  %s\n", "point", "group", "n", "q1", "q2", "q3", "avg", "band"
  for (a = 1; a <= NP; a++) {
    p = P[a]; if (!(p in usedcp)) continue
    ng = 0
    for (g in allg) if ((p, g) in usedg) { ng++; names[ng] = g }
    # canonical groups first in their order, then any others alphabetically
    out = 0
    for (k = 1; k <= NG; k++) if ((p, G[k]) in usedg) order[++out] = G[k]
    for (k = 1; k <= ng; k++) if (!(names[k] in grank)) extra[++ne] = names[k]
    for (x = 1; x <= ne; x++) for (y = x + 1; y <= ne; y++) if (extra[y] < extra[x]) { t = extra[x]; extra[x] = extra[y]; extra[y] = t }
    for (x = 1; x <= ne; x++) order[++out] = extra[x]
    ne = 0
    min = 99; max = -1
    for (k = 1; k <= out; k++) {
      g = order[k]; n = cn[p, g]; m = ca[p, g] / n
      printf "%-15s %-14s %3d %6.2f %6.2f %6.2f %6.2f  %s\n", p, g, n, cs[p, g, 14] / n, cs[p, g, 15] / n, cs[p, g, 16] / n, m, band(m)
      if (m < min) min = m; if (m > max) max = m
    }
    n = cn[p, "all"]; m = ca[p, "all"] / n
    printf "%-15s %-14s %3d %6.2f %6.2f %6.2f %6.2f  %s\n", p, "all", n, cs[p, "all", 14] / n, cs[p, "all", 15] / n, cs[p, "all", 16] / n, m, band(m)
    printf "%-15s spread between group averages: %.2f (highest %.2f, lowest %.2f)\n", p, max - min, max, min
  }
}
' "$1"
