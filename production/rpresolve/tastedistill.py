"""
Distil the review log (<project>/review/events.jsonl) into a taste profile.
Standard library only. See production/media-review-loop.md, "Recording the answers".

A question is the line's optional `question` key (warmth, skin, contrast, ...),
else its `step`; a line with a `noise_type` is a separate question per type,
never pooled. The direction an answer prefers is what the chosen label stood for
in `options` (a non-label choice such as `none` stands for itself). A line with
no choice (a reason only) is counted apart and not used.

A rule needs MIN_ANSWERS answers for one direction, a majority of the question's
answers, AND the repeat-pair check: lines sharing a `stimulus` key (one comparison
asked again, perhaps with its options shuffled) must agree in pairs at least
REPEAT_AGREEMENT of the time. A line without `stimulus` joins no repeat group:
different clips offering the same options are not repeats.
"""

import json
from collections import Counter, defaultdict

MIN_ANSWERS = 5
REPEAT_AGREEMENT = 0.8
EXEMPLARS = 3
NON_LABEL = {"none", "hold", "approve", "go"}

HEADER = """# Taste profile

Distilled from the review log. This profile orders options and never picks: it sets
the starting point and the order of the next proposal, and every decision stays
with the reviewer. A rule needs at least %d consistent answers and, where a pair
was asked again, at least %d%% agreement between the repeats. Denoise answers are
counted per noise type, never pooled.
""" % (MIN_ANSWERS, int(REPEAT_AGREEMENT * 100))


def read_events(path):
    """(events, malformed): events are (line number, dict); malformed counts
    unreadable lines, non-objects and lines whose options are not an object."""
    events, malformed = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if isinstance(e, dict) and e.get("options") is None:
                e["options"] = {}
            if not isinstance(e, dict) or not isinstance(e["options"], dict):
                malformed += 1
                continue
            c = e.get("choice")
            if isinstance(c, (dict, list)) or (isinstance(c, str) and e["options"]
                                               and c not in e["options"] and c not in NON_LABEL):
                malformed += 1  # a label the line's own options cannot decode
                continue
            events.append((n, e))
    return events, malformed


def _question(e):
    q = str(e.get("question") or e.get("step") or "unknown")
    return f"{q} [{e['noise_type']}]" if e.get("noise_type") else q


def _direction(e):
    """What the answer prefers, or None for a line-only answer."""
    c = e.get("choice")
    if c is None:
        return None
    return str(e["options"].get(c, c)) if isinstance(c, str) else str(c)


def _repeat_agreement(answers):
    """(agreeing pairs, pairs) over answers sharing a stimulus key."""
    groups = defaultdict(Counter)
    for _, _, key, d in answers:
        if key:
            groups[key][d] += 1
    agree = pairs = 0
    for c in groups.values():
        n = sum(c.values())
        pairs += n * (n - 1) // 2
        agree += sum(k * (k - 1) // 2 for k in c.values())
    return agree, pairs


def distill(events):
    """Per question: counts, the verdict and the evidence. Pure; no I/O."""
    by_q = defaultdict(list)
    for n, e in events:
        d = _direction(e)
        if d is None:
            continue
        opts = tuple(sorted(str(v) for v in e["options"].values()))
        key = str(e["stimulus"]) if e.get("stimulus") else None
        by_q[_question(e)].append((n, opts, key, d))
    out = {}
    for q, answers in sorted(by_q.items()):
        counts = Counter(a[3] for a in answers)
        ranked = counts.most_common()
        top, top_n = ranked[0]
        majority = top_n * 2 > len(answers)
        agree, pairs = _repeat_agreement(answers)
        r = {"total": len(answers), "counts": ranked, "agree": agree, "pairs": pairs,
             "direction": top, "top_n": top_n}
        if top_n < MIN_ANSWERS:
            r["verdict"] = "not yet a rule"
        elif not majority:
            r["verdict"] = "no majority, no rule"
        elif pairs and agree / pairs < REPEAT_AGREEMENT:
            r["verdict"] = "inconsistent, no rule"
        else:
            r["verdict"] = "rule"
            offered = Counter()
            for _, opts, _, _ in answers:
                offered.update(set(opts))
            r["exemplars"] = [a[0] for a in answers if a[3] == top][:EXEMPLARS]
            r["contradictions"] = [a[0] for a in answers if a[3] != top]
            # offered at least MIN_ANSWERS times and never chosen
            r["never"] = sorted(o for o, k in offered.items() if k >= MIN_ANSWERS and not counts[o])
        out[q] = r
    return out


def render(result, answers, malformed, line_only=0):
    out = [HEADER, f"Read {answers} answers; {malformed} malformed lines skipped; "
                   f"{line_only} line-only answers (no choice) not counted.\n"]
    for q, r in result.items():
        counts = ", ".join(f"{d}: {k}" for d, k in r["counts"])
        out.append(f"## {q}\n")
        out.append(f"- Answers: {r['total']} ({counts})")
        out.append("- Repeat check: " + (f"{r['agree']} of {r['pairs']} repeat pairs agree"
                                         if r["pairs"] else "no repeated pairs yet"))
        v = r["verdict"]
        if v == "rule":
            out.append(f"- Rule: prefers {r['direction']} ({r['top_n']} of {r['total']})")
            out.append("- Never: " + (", ".join(r["never"]) or "none"))
            out.append("- Exemplar answers (log line): " + ", ".join(map(str, r["exemplars"])))
            out.append("- Contradicting answers (log line): "
                       + (", ".join(map(str, r["contradictions"])) or "none"))
        elif v == "not yet a rule":
            out.append(f"- {r['top_n']} of {MIN_ANSWERS} for {r['direction']}: not yet a rule")
        else:
            out.append(f"- {v.capitalize()}: {r['total']} answers, repeats {r['agree']} of {r['pairs']}")
        out.append("")
    return "\n".join(out)


def build(path):
    events, malformed = read_events(path)
    line_only = sum(1 for _, e in events if _direction(e) is None)
    return render(distill(events), len(events) - line_only, malformed, line_only)
