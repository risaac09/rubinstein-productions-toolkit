---
name: branded-doc-build
description: Build, finish, or review a Word document when a brand template (or an example document) and source material both exist. Use for client-facing guides, field instruments, one-pagers, change logs, FAQs, email kits, briefs, and any .docx that has to look native to someone else's brand system. Trigger for "write this up as a doc", "make a Word document", "build a field guide", "one-pager", "turn this into a docx", "review this document", "match the brand", "plain version", or any request pairing a template with content to place in it. Also use when reviewing a document someone else authored.
---

# Branded document build

## What this skill is for

You have a brand template (or an accepted example document) and source material, and you have to produce a Word document that looks native and does a job for a named reader.

The governing decision is not visual. It is **how much form this document's audience warrants**, and that choice is made per artifact, not per document class. In one real production batch, three documents built from the same source on the same day got three different levels of styling on purpose.

---

## Phase 0. Confirm the three inputs

| Input | What it is | If missing |
|---|---|---|
| **Brand source** | A `.docx`/`.dotx` template, or a document already accepted by this audience | Ask. Do not invent a palette. |
| **Content source** | The material the document is built from | Ask. |
| **The reader and the job** | Who reads it, and what they do differently afterward | Ask. This sets the form budget. |

---

## Phase 1. Set the form budget

Match styling to the document's job. Getting this wrong is more damaging than getting the hex values wrong.

| Tier | When | Treatment |
|---|---|---|
| **Working doc** | The client will edit it, fill it in, or build from it (survey specs, email kits, drafts) | Stock defaults. Real `Heading 1/2/3` and list styles. No color. Editability beats polish. |
| **Brand-lite** | Internal leadership reads it once and decides (one-pagers, change logs, briefs) | One brand color on headings, near-black body, tight margins. No logo, no header art. The brand signal is one color. Spend the rest of the budget on fitting the argument. |
| **Full brand** | A durable instrument someone carries into a room and uses repeatedly (field guides, playbooks, role guides) | Cover page, TOC, running header and footer, branded tables, section-per-page, full type system. |

A working doc dressed as a durable instrument wastes effort and makes the client afraid to edit it. A durable instrument shipped as raw defaults will not survive being used.

---

## Phase 2. Extract template truth (never guess it)

Across five real artifacts in one program, five slightly different teals were used, because everyone approximated the brand by eye. Pin exact tokens before the first build.

```bash
python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" template.docx tpl/
```

**1. Read `docProps/core.xml` first.** `dc:creator`, `cp:lastModifiedBy`, and `dcterms:modified` tell you whether this is a client file or yours. If it is theirs, you are in preserve-provenance mode for the rest of the task.

**2. Check whether the theme actually belongs to the brand.** Read `word/theme/theme1.xml`. Client templates are routinely a costume over a foreign skeleton: one real "branded" client guide turned out to be a software vendor's template whose theme was still the vendor's own green, with the client's colors applied only as run-level overrides. Anything theme-inheriting (hyperlinks, newly added table rows) would leak green.

If the theme is foreign, say so in the profile. It means the brand is surface-deep and will not survive normal editing.

**3. Frequency-count the real colors and fonts.**

```bash
grep -rho 'w:color w:val="[0-9A-Fa-f]\{6\}"' tpl/word/document.xml tpl/word/styles.xml | sort | uniq -c | sort -rn | head
grep -rho 'w:ascii="[^"]*"' tpl/word/styles.xml | sort | uniq -c | sort -rn | head
grep -rho 'w:shd[^/]*w:fill="[0-9A-Fa-f]\{6\}"' tpl/word/styles.xml | sort | uniq -c | sort -rn | head
```

**4. Check whether headings are real styles or direct formatting.** Bold 11pt paragraphs standing in for `Heading 1` break the navigation pane, TOC generation, and every text-extraction tool. If the template does this, do not copy the habit.

**5. Record margins, header/footer content, and page setup** from `word/sectPr` and `word/header*.xml`.

Write the result to `BRAND-PROFILE.md`. Keep it. It is the portable artifact, worth more than any single document, and it is what you carry to a new tool or a new session.

> **Dependencies.** `soffice` (LibreOffice), `pdftoppm` (Poppler), and `markitdown` are needed to render and read. On a fresh sandbox: `apt-get install -y libreoffice-writer poppler-utils && pip install "markitdown[docx]"`. **If you cannot render and view pages in your environment, say so before building.** A document built without looking at it ships defects that no amount of careful XML avoids.

---

## Phase 3. Author in markdown first, project to Word

Write the deliverable as a markdown working file, then build each Word document from it.

**The markdown is the superset. Each docx is a scoped projection.** One 4,066-word recommendations file produced a full-fidelity 4,097-word document for the working team and an 832-word build-spec extract for whoever configures the form. A 1,767-word email kit produced an 888-word client document, because the internal strategy gates and distribution guidance were stripped before handoff.

**Strip internal scaffolding from anything client-facing.** Decision gates, strategy notes, and your own reasoning stay in the markdown. Generic-template residue is the same failure from the other direction: a list like "JIRA, Azure DevOps, ServiceNow, etc." reads as a template nobody bothered to localize.

### Whose brand

**Palette follows the speaker, not the audience.** Use the client's brand when the document speaks for the program. Use your own brand when you are the one speaking, as in a document pitching your own role or practice. Brand identity is voice attribution, and getting this backwards makes a self-advocacy document read as a program deliverable.

Where the client's palette lacks a level you need, extend it by darkening an existing accent. Never import a foreign hue to carry a semantic role.

### Content discipline, before any formatting

- **Mark every value the client must supply as a bracketed placeholder,** and gate the document with a top note listing what must be confirmed before sending. Then `grep -o '\[[^]]*\]'` the built file before handoff. A shipped client template once carried `[topic]` in its own footer.
- **Never let a placeholder become an invented value.** A named gap beats a fabricated figure. Name who owes the answer.
- **Ship the trade-offs inside the deliverable.** The strongest one-pager in the corpus carries its own "Trade-off:" line and a "Still open:" list of undecided items. Intellectual honesty is content, not a caveat you deliver verbally.
- **Wear verification on the surface.** State currency and its source in the document: `Confirmed current as of June 2026 (per the program lead)`. Claim `Every figure traceable to a named source file. None estimated.` only when an evidence base actually backs every figure.
- **When you change an instrument that carries a trend, ship a change log** sorted Kept / Reworded / Cut / Fixed / New, flag every reworded trend item so the seam is not misread later, and list open decisions at the bottom.

---

## Phase 4. Build

### Put brand in styles, not in runs

Define `Normal`, the heading styles, and table styles once, carrying the type and palette. Then a later restyle, or a Plain variant, touches definitions rather than thousands of lines.

Both a client guide and an early field guide in the corpus violated this, painting brand color per run over wrong-theme templates. The cost arrived later: producing the Plain twin meant editing 2,129 lines of XML instead of a handful of style definitions.

### A one-pager must actually render on one page

Verify by rendering and counting pages. Buy the space with form before cutting a sentence: margins near 0.55in, body at 8.5pt, headings at 9.5pt, borderless tables. The best one-pager in the corpus kept all 619 words of its source, including its trade-off and open-decisions sections, and still fit.

```bash
soffice --headless --convert-to pdf out.docx
pdfinfo out.pdf | grep Pages
```

### Long-form instruments need navigation

Cover page with title, audience, date, and a one-paragraph mission in italics. A TOC. Numbered sections, each starting on a new page. Running header carrying document identity, footer carrying `Org · Document · Page N`. A role-to-section routing table up front so a reader finds their part without reading the rest. A glossary appendix for domain acronyms.

Close every procedural step with a colored `Output.` line naming the artifact produced and where it gets recorded. That single convention is what makes a guide usable mid-session rather than only readable beforehand.

**If you write a TOC field, update it or build the entries statically.** An un-updated field renders as an empty "Contents" page everywhere except Word after a manual refresh. This defect shipped in a real field guide.

### Ship a Plain variant when the context needs one

Trigger it when the document will be printed in grayscale, pasted into email or SharePoint, or read by people for whom low-contrast brand gray at 11pt is hard.

Produce it by script over the unzipped XML, never by retyping: set every `w:color` to `000000` and every `w:shd` fill to `FFFFFF`, across **both** `document.xml` and `styles.xml`. Keep the logos, the tagline art, and the layout. Identity is carried by the imagery, legibility by the monochrome text.

Name it `<same name> (Plain).docx` and keep both files in the same folder. The branded file stays the file of record.

---

## Phase 5. Reviewing a document someone else wrote

**Add anchored comments. Never edit their text.** Return the document in their own file with creator, revision history, and modified date preserved. A review that rewrites the author's sentences costs you the author.

Never let a script stamp its own metadata over the client's. Preserving provenance is what makes received-versus-produced auditable months later.

### The comment repertoire

Good document review is operational-gap hunting, not copyediting. Work this list:

| Ask | Example |
|---|---|
| **Where is it submitted?** | "The playbook never says where or how a request is actually submitted." |
| **Who owns it?** | "Placeholder, fill in the actual owner." |
| **What does it cost?** | "Credit mechanics are undefined. Readers will ask all three." |
| **Where does the artifact live?** | "Without a named home this section will not happen consistently." |
| **Does this contradict another section?** | "This conflicts with When Not to Ask an Expert." |
| **Is this generic template residue?** | "Reads like a generic template rather than our process." |
| **What is the strongest sentence?** | Name it, so the author protects it through revision. |

That last one matters more than it looks. A review that only lists problems tends to get the good parts edited out along with the bad.

---

## Phase 6. QA

```bash
markitdown out.docx                                    # content, order, headings
soffice --headless --convert-to pdf out.docx
pdfinfo out.pdf | grep Pages                           # page-count promises
rm -f pg-*.jpg && pdftoppm -jpeg -r 120 out.pdf pg
ls -1 "$PWD"/pg-*.jpg
```

Then look at every page.

| # | Check |
|---|---|
| 1 | Page count matches the promise a "one-pager" or "two-pager" makes |
| 2 | TOC populated, not an empty Contents page |
| 3 | No leftover brackets: `grep -o '\[[^]]*\]'` unless they are intentional fill-ins |
| 4 | Headings are real styles, so the navigation pane works |
| 5 | Tables do not break across pages mid-row where it hurts |
| 6 | Running header and footer correct on every page, including the first |
| 7 | Contrast holds in grayscale if this will be printed |
| 8 | Internal scaffolding stripped from client-facing builds |
| 9 | Provenance preserved if the file was received |
| 10 | Voice rules applied: em-dashes swept, promotional verbs gone, active voice |

---

## Anti-patterns

| Anti-pattern | What it costs |
|---|---|
| **Run-level brand painting** | A restyle becomes a 2,000-line XML operation instead of a 10-line style edit. |
| **Heading-free direct formatting** | Bold paragraphs standing in for headings break navigation, TOC, and extraction. |
| **Un-updated TOC field** | Ships as a blank Contents page outside Word. |
| **Unfilled bracketed placeholder** | `[topic]` in a shipped footer. Grep before handoff. |
| **Internal scaffolding leaking to the client** | Strategy gates and distribution notes that were never meant for them. |
| **Generic template residue** | Tool lists and process language that were never localized. |
| **Editing a client's text during review** | Destroys their revision history and their ownership. |
| **Deleting argument to hit a page limit** | Compress form first. Cut sentences only when form is exhausted. |
| **Five slightly different brand teals** | Approximating by eye across artifacts. Pin tokens once. |
| **Script stamping over client metadata** | Received-versus-produced becomes unauditable. |

---

## Working method

Author every deliverable as a markdown working file first, with frontmatter and a numbered filename prefix, then build the Word projections from it. Keep the markdown as the record. When the document needs to change, change the markdown and rebuild, except when a human has already edited the Word file.

**When a human has edited the file, edit their file in place. Do not regenerate from your build script.** Regenerating wipes their filled-in names, links, and deletions. Open the current file, confirm what changed, apply only the requested fix, and flag typos rather than silently rewriting their words.
