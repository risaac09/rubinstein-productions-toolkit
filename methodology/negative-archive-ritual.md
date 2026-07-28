> **Canonical source:** this file (toolkit is authoritative). Companion to `counter-glossary.html` and `negative-thousand.html` (home-dir artifacts, outside this repo). Filed under the same lineage as [source-tracking-protocol.md](source-tracking-protocol.md) and [total-cost-of-ownership.md](total-cost-of-ownership.md).

# Negative-Archive Ritual

*Working Document | Created: 2026-05-23*
*Purpose: A standing practice for filing what the work refused, cut, withheld, or excluded. The positive archive is the portfolio. The negative archive is the cost.*

---

## 1. What this is

A `scraps/` folder, kept alongside the working folder of any project, that holds the things the project decided not to keep. Drafts cut from the final. Frames not used. Sentences struck. Names removed. Approaches abandoned. Citations refused. Each piece is filed with a short note saying what it was and why it was not kept.

The scraps folder is not a recycle bin. The recycle bin is the place where deletions go to disappear. The scraps folder is the place where deletions go to remain visible as deletions.

## 2. Why this exists

Three commitments converge here:

**The Source-Tracking Protocol** (see [source-tracking-protocol.md](source-tracking-protocol.md)) asks every move to carry its lineage. The negative archive extends this to the moves *not* made. A refused source is also a source; it earned its refusal.

**The thousand costs** (see [total-cost-of-ownership.md](total-cost-of-ownership.md) and `~/context/thousand-costs.md`, home dir) enumerate the costs of doing the work. The scraps folder enumerates the artifacts of *not* doing some of the work, kept so that the unmade is not invisible.

**The counter-glossary** (`~/counter-glossary.html`) pairs each term used to a term refused. The scraps folder is the same operation applied to artifacts: each piece kept implies the pieces unkept, and the unkept are also part of the practice (after Adorno, 1966/1973, on the non-identical; Derrida, 1995/1996, on the archive's selective violence; Hartman, 1997, on the constitutive exclusion).

## 3. Structure

```
project-name/
  working/              # the live draft
  final/                # what shipped
  scraps/               # what didn't, by intentional refusal
    YYYY-MM-DD-thing.ext
    YYYY-MM-DD-thing.note.md    # one note per scrap, brief
    COLOPHON.md                  # the running log
```

Three rules:

1. **A scrap is what was *worked on* then refused, not what was never started.** Unwritten ideas do not belong here. Written sentences that were cut do.
2. **Each scrap gets one short note.** What it was. Why it was not kept. What it would have foreclosed if it had been kept. One paragraph. No defenses.
3. **The COLOPHON.md is appended on each filing.** It is the running ledger of refusals across the project's life. It is the document the project is most reluctant to share, and therefore the document most worth keeping.

## 4. The ritual

At the close of a working session, before saving and stepping away:

**Step 1. Locate.** What did this session produce that was kept? What did it produce that was cut?

**Step 2. Name.** For each cut piece worth filing, write a one-sentence label. Use the active voice. "Cut the second paragraph of the proposal because it explained the methodology before naming the problem." Not "removed for clarity." That phrase is the marketing version; refuse it.

**Step 3. File.** Move the cut piece into `scraps/` with the date prefix. Write its one-paragraph note in a sibling `.note.md` file.

**Step 4. Update the colophon.** Append one line to `COLOPHON.md`. Format: `YYYY-MM-DD · filename · one-line summary of the refusal`. Do not edit prior lines; the colophon is append-only by ritual, even though the filesystem would allow otherwise.

**Step 5. Read the last five lines of the colophon.** This is the only step that is not about filing. It is about exposure: the practice of seeing the pattern of refusals at the cadence of the work. A pattern over time tells the project what it is becoming through what it is shedding.

## 5. The COLOPHON.md template

Each project's `scraps/COLOPHON.md` opens with this header, then accumulates entries.

```
# Scraps Colophon · [project-name]

This is the negative archive for this project. It records what was worked
on and then refused. The format is append-only by ritual. The point is
not to defend the refusals; the point is to make them visible to the
practice that is producing them. (See methodology/negative-archive-ritual.md.)

---

## Entries

YYYY-MM-DD · filename · one-line summary
YYYY-MM-DD · filename · one-line summary
...
```

## 6. What does *not* go into the negative archive

To keep the scraps folder honest, the following do not belong:

- **Drafts of the same artifact.** Earlier drafts of a thing that did ship belong in version control or in a `drafts/` folder, not in scraps. Scraps are refusals, not iterations.
- **Failures.** A piece that did not work because it was incompetent belongs in `lessons/` or in a session note, not in scraps. The negative archive is for the competent refusal, not the bungle.
- **Auto-saves and backups.** The system already keeps these. They do not need a ritual.
- **Secrets.** If a scrap contains a credential or a name that should not be retained, redact or omit. The colophon entry can still be filed; the artifact does not have to be.

## 7. Cadence

The ritual fires on the same cadence as the work: session by session, not week by week. A weekly negative archive is a confession; a session-level negative archive is a hygiene. Hygiene is what the work asks.

A quarterly read of the COLOPHON.md across all active projects is its own oscillation. The patterns that emerge across projects are the most honest data about what the practice is, beneath what the portfolio says it is.

## 8. Lineage

This ritual draws on:

- **Derrida (1995/1996)** on archive fever, the archive's structural exclusion as the condition of its order.
- **Foucault (1969/1977)** on the author-function and the way an archive's outside is what gives its inside its shape.
- **Adorno (1966/1973)** on the non-identical, the remainder that resists subsumption under the concept.
- **Hartman (1997)** on the constitutive exclusion that the represented presence depends upon.
- **Federici (2004; 2012)** on the unpaid labor that the visible work is built on.
- **Schön (1983)** on the reflective practitioner whose practice is a recurring loop of action and revision.
- **Isaac's own Source-Tracking Protocol** and **Total Cost of Ownership** docs, which already extend this thinking to citation and to cost; this ritual extends it to artifacts.

Full APA citations available in the bibliography section of `~/negative-thousand.html` (entries: `bib-derrida-1995`, `bib-foucault-1969`, `bib-adorno-1966`, `bib-hartman-1997`, `bib-federici-2004`, `bib-federici-2012`, `bib-schon-1983`).

## 9. Cross-references

- `counter-glossary.html` (home dir) — the term-level companion to this ritual; what the work refuses lexically.
- `negative-thousand.html` (home dir) — the concept-level companion; what the work refuses theoretically.
- [total-cost-of-ownership.md](total-cost-of-ownership.md) — what the work costs to do.
- [source-tracking-protocol.md](source-tracking-protocol.md) — what the work owes its citations.
- `~/context/thousand-costs.md` (home dir) — the long enumeration the protocols are layered onto.

## 10. Open questions

These remain unsettled and are filed here so the ritual can correct itself over use:

- **How granular is a "scrap"?** A struck sentence is clearly too small; a discarded draft chapter is clearly large enough. The middle is judgment.
- **What is the public-facing form of a negative archive?** The portfolio's negative twin would be a "scraps page" that shows the refusals alongside the kept work. Whether that is honest or theatrical is undecided.
- **How does this interact with client confidentiality?** Scraps from client work may contain client information that the contract requires removed. The ritual would need a redaction protocol before any client scrap is filed.
- **Does the colophon itself ever get pruned?** The ritual says append-only. Over years, this means the COLOPHON.md grows without bound. The honest answer may be: yes, eventually, and the act of pruning becomes its own filing in a meta-scrap folder.

End document.
