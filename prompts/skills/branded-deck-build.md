---
name: branded-deck-build
description: Build or finish a PowerPoint deck when a brand template (or an example deck) and source material both exist. Use for client-facing decks, executive briefs, readouts, status updates, assessment presentations, and any .pptx that has to look native to someone else's brand system. Trigger for "build a deck", "finish this deck", "make slides", "executive brief", "readout deck", "put this in the template", "match the brand", "deck QA", "why does this deck look off", or any request pairing a template with content to place in it. Also use when reviewing an existing deck against a template.
---

# Branded deck build

## What this skill is for

You have two inputs: a brand template (or an example deck someone already accepted) and source material. You have to produce a deck that looks native to that template and makes its point to a specific audience.

The quality of the result is decided by mechanism, not taste. Decks fail because the builder guessed the brand instead of reading it, poured content into a layout without re-rendering, or shipped a decision slide that looked like a context slide. Every one of those is checkable. This skill is the checklist.

**The gut check, applied at the end:** drop the deck into the template's own slide sorter. Does it look like it belongs? If not, the template has not been matched yet.

---

## Phase 0. Confirm the three inputs

Do not start composing until all three exist. Name any that are missing and ask for them.

| Input | What it is | If missing |
|---|---|---|
| **Brand source** | A `.pptx`/`.potx` template, or an example deck already accepted by the audience | Ask. Do not invent a palette. |
| **Content source** | The material the deck is built from, with the facts already settled | Ask. Do not draft claims to fill slides. |
| **The decision** | What the audience must know, decide, or do after reading | Ask. A deck without this becomes a document. |

Write the decision as one sentence before building anything. That sentence determines which slide gets the loudest treatment.

---

## Phase 1. Extract template truth (never guess it)

Guessed brand colors are always slightly wrong, and slightly wrong reads as off. Observed drift from real work: `#FE6D00` typed where the template says `#FD6D00`, `#0082A6` where the template says `#0082A5`. One bit off, every time, because someone re-typed from memory instead of reading the file.

Unzip and read. Produce a `BRAND-PROFILE.md` before drawing a single shape.

```bash
python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" template.pptx tpl/
```

Then work through all eight steps. Skipping any one of them is where decks go wrong.

**1. Read `docProps/core.xml` first.** `dc:creator` and `dcterms:created` tell you whether the template is client-native or yours. That changes what you are allowed to alter. Also check `docMetadata/LabelInfo.xml` for sensitivity labels that must survive a round-trip.

**2. Map masters before trusting any theme.** Read `ppt/slideMasters/_rels/*.rels`. A real client template often carries many masters, most on stock Office themes. Picking `theme1.xml` because it sorts first is a mistake. Identify which master each layout you intend to use actually hangs from.

**3. Never derive the palette from `clrScheme` alone.** Frequency-count literal colors across layouts, masters, and slides, and take the modal values as the working palette:

```bash
grep -rho 'srgbClr val="[0-9A-Fa-f]\{6\}"' tpl/ppt/slideLayouts tpl/ppt/slideMasters | sort | uniq -c | sort -rn | head -20
```

Then look for hexes stated in *instruction text* on the template's own slides (icon-bank slides, "delete this slide once done" slides). Those are the official palette. The two sets often disagree inside the same file. Record both, and note which surfaces use which. Where they conflict, the drawn shapes are the working truth and the instruction text is the brand-book truth.

**4. Never derive fonts from `fontScheme`.** It routinely says Calibri or Arial while every placeholder overrides to the real display face.

```bash
grep -rho 'typeface="[^"]*"' tpl/ppt/slideLayouts tpl/ppt/slideMasters | sort | uniq -c | sort -rn
```

Build the role map from what wins: title face, weight, size, color; kicker; body; small labels. Also check for `ppt/fonts/`. If absent, the fonts are not embedded, and the whole brand voice silently falls back on any machine that lacks them. Say so in the profile.

Grep for misspelled faces while you are here. A typo like `Monsteratt` renders as a silent fallback, survives every visual review, and is only ever caught in the XML.

**5. Find the real background.** The page ground is often a full-bleed shape with an alpha value, not `<p:bg>`. Reproduce the mechanism, not a flattened hex, or anything you layer over it will look wrong. Confirm by rendering a page and sampling actual pixels:

```bash
soffice --headless --convert-to pdf template.pptx
pdftoppm -jpeg -r 150 template.pdf pg
python3 -c "from PIL import Image; im=Image.open('pg-1.jpg'); print(im.getpixel((60,400)), im.getpixel((640,80)))"
```

> **Dependencies.** These commands need LibreOffice, Poppler (`pdftoppm`), Pillow, and `markitdown`. On a fresh sandbox: `apt-get install -y libreoffice-impress poppler-utils && pip install "markitdown[pptx,docx]" Pillow`. If your environment wraps `soffice` (Claude Code ships `scripts/office/soffice.py` in its pptx skill because bare `soffice` hangs in some sandboxes), use the wrapper. **If you cannot render and view images in your environment, stop and say so.** Everything below Phase 3 assumes you can look at the output, and a deck built without looking will ship defects regardless of how carefully the XML was written.

**6. Extract footer geometry in EMU and treat it as immovable.** Record exact x/y/width/height for every logo and tagline, converted to inches (EMU / 914400). Record where content must stop. Nothing enters the footer strip.

**7. Copy brand images out of `ppt/media/` and reuse them byte-identical.** Taglines and logos are usually images, not text. Never retype a tagline. You will get the face and the color wrong, and a stretched logo is an instant reject. Preserve native aspect ratio by computing height from width using real pixel dimensions.

**8. Inventory the layouts and thumbnail them.**

```bash
soffice --headless --convert-to pdf template.pptx && pdftoppm -jpeg -r 100 template.pdf tpl-thumb
```

Claude Code's bundled pptx skill has `scripts/thumbnail.py`, which builds a labeled grid in one step. If you use it, always pass a second argument naming the deck, since it defaults to `thumbnails` and two decks thumbnailed in one directory silently overwrite each other. Look at the grid. Note which content shapes have no layout (dense tables, timelines, swimlanes usually have none), because those are the slides you will have to build on a blank layout, and they are where consistency breaks.

### The output of Phase 1

```markdown
# BRAND-PROFILE: <template name>
Origin: <dc:creator>, created <date>, modified <date>. Client-native / ours.
Fonts: title <face weight size color> · kicker <...> · body <...> · label <...>
Embedded: yes/no  (if no: warn that rendering machines need the font installed)
Working palette (modal, from XML): <hex> <role> ...
Official palette (from instruction text): <hex> ...
Background: <mechanism, e.g. greige @30% alpha over white → renders #F0EDEA>
Footer: <asset> at (x,y) w×h in · content stops at y=<n>in
Layouts available: <n>, <names and purpose>
Missing layouts: <forms with no layout>
Semantic color code: <which color means structure, which means signal, which means risk>
```

Keep this file. It is the portable artifact. It is what you hand to any model, in any tool, on any future session, and it is worth more than the deck.

---

## Phase 2. Freeze the content before you fork the form

The strongest sequence observed in real work: settle the claims, source-check them, and only then design. One deck's content was locked at 19:07, and two different visual treatments of that same locked content were produced at 19:37 and 20:03. Because the facts were frozen, the design comparison was clean.

Do it in that order. Specifically:

- **Source-check every number and claim against the content source.** Note where each came from.
- **Mark what you do not have as `[TK]`,** and never let a `[TK]` become an invented number. A named gap beats a fabricated figure, always. If a reviewer's open question has no answer yet, encode it on the artifact face: `Tool TBD, confirm with <name>`. Name who owes the answer.
- **Label illustrative content in the title itself,** set in the accent color: `Step 4, Analyze: Sample SWOT`. A subtitle disclaimer is not enough. The title is what gets quoted and screenshotted.
- **Put source references in the speaker notes,** naming the exact documents each slide draws on. This survives the handoff when someone else presents.

Watch for form generating new content obligations. Drawing a four-phase timeline forces a phase taxonomy that no sentence in the source ever asserted. If the diagram invents structure, that structure now needs sourcing too.

---

## Phase 3. Compose

### Build inside the client's own file

Do not start a fresh deck and rebuild the brand. Duplicate the template or the accepted example, and work in it. Masters, footer lockup, logo, tagline, and file lineage all carry over for free.

Then strip weight rather than rebuilding brand. One source deck went from 13.5 MB with 123 media files to 519 KB with 7, while the slide count grew from 16 to 24, purely by removing unused layout art.

Do all structural work (add, delete, reorder) before editing any slide's content.

### One message per slide

The single highest-value composition move. When a slide carries current state, future state, and a blocking decision at once, split it. The version that gave the blocking decision its own slide beat the version that buried it as the third gray block on a shared slide, and the difference was not subtle.

### Rank with form, not just with words

If three slides use the same text-stack layout, visual weight is flat, and the reader has to do the ranking themselves. Give the one thing that needs action the treatment nothing else gets: the deck's only full-bleed inverted slide, the only large accent-colored question, the only oversized numeral. Reserve it. It only works because it is rare.

### Give every slide a one-line takeaway under the title

A reader who skims only the kicker lines should get the whole argument. Set it in the accent color. State the so-what, not the topic.

### Use color semantically

Assign each palette color a job and hold it: one color for structure (headers, bars, quantitative), one for signal (kickers, the single highlighted data point, the ask), one for risk. The accent is punctuation, never a field. A saturated accent behind body text overwhelms it.

Tint data marks and keep saturation for phase bands and milestones. Full-strength brand color as data ink was demoted to tints in every chain where someone iterated on it.

### Numbers as marks, not entombed in sentences

`New Leader score: 8.1 in FY25 to 7.3 FY26 YTD` inside a paragraph is a number nobody sees. Chart it, or make it a stat card. One consistent stat-card pattern reused for every data source (thin accent border, off-white fill, large accent numeral, small gray caption) is worth more than three bespoke treatments.

**But never truncate an axis on a bounded scale.** Starting a 10-point scale at 6.2 to make a 0.4 decline look steep is a data-integrity failure that no amount of brand fidelity redeems.

### Address the audience in the verb

Rewrite third-person description into second-person instruction where the slide is asking for something. `Complete one timed Journey` beats `One coordinated Journey provides timed tasks`. Compress the closing ask to verb-first labels of two or three words under an explicit eyebrow that names who must act.

### Write two so-whats when there are two audiences

Deciders need an implications column. End users need plain language about what changes for them. These are different slots on different slides, not one sentence trying to serve both.

### Demote detail, do not delete it

Keep the main flow at decision altitude. Per-domain detail goes to a labeled appendix. Operational instruments go to companion documents named on the slide face. Detail migrating to the appendix is healthy; detail being cut is usually a loss.

### End framework slides in an output or a decision

A framework slide populated with invented examples reads as findings. Rebuild it as the process plus its outputs: `Output: org-level ranking`, `Decision: leadership endorses the prioritized scope`.

### Do not pre-empt the client's own process step

The flashiest consulting slide in one chain (a P1/P2/P3 priority ranking) did not survive client contact. It made a call the client's own prioritization step was supposed to make. It was deleted, not softened. Rank when ranking is your job.

### Two-tier treatment of the client's own artifacts

**Authority beats consistency for data. Consistency beats authority for concepts.**

Paste a data artifact the client already trusts as-is, even when it is off-brand and visually alien. Redraw conceptual diagrams into the deck's brand. Restyling someone's survey heatmap costs you their trust in the number; restyling their process curve costs nothing.

### Echo the client's vocabulary

Adopt their renames verbatim and immediately. `Readiness Assessment` not `survey`. `Key Partner Meetings` not `focus groups`. When they rename the deliverable, the rename propagates the same day and holds through ship.

### Replace placeholders with real names as soon as they are known

Keep brackets only where the client must fill them, and say who in the kicker: `[SCM leads to confirm areas]`. Ship the argument skeleton with visible bracketed asks, then replace them with client-supplied truth. Never invent names to look finished.

---

## Phase 4. QA (required, and not optional)

Your first render always has real defects. The failure that ships is nearly always a failure to look.

A kicker text box placed over an inherited body placeholder produced garbled overlapping text that shipped through **seven consecutive generations** of scripted edits, because every pass edited XML and none re-rendered.

```bash
markitdown out.pptx                                   # content, order, typos
soffice --headless --convert-to pdf out.pptx
rm -f slide-*.jpg && pdftoppm -jpeg -r 150 out.pdf slide
ls -1 "$PWD"/slide-*.jpg
```

If you have Claude Code's pptx skill, also run `python scripts/office/validate.py out.pptx --original template.pptx`. Pass `--original` for anything template-derived, so the template's own schema faults are not reported as yours.

Then open every image and inspect it as if you know there are problems. Fresh eyes beat re-reading your own generator. A subagent works well here.

### The checklist

| # | Check | Why it is on the list |
|---|---|---|
| 1 | **Text overflow or cut off at any box or slide edge** | Most common defect. Real content is longer than template content. |
| 2 | **Refilled placeholders re-checked for geometry** | Containers without autofit overflow exactly where the deck was just fixed. |
| 3 | Titles that wrap into the content below | Long titles on fixed-geometry layouts collide. Check every one. |
| 4 | Template decoration mispositioned after text replacement | An underline placed for a one-line title breaks on two lines. |
| 5 | Authored text sitting over an inherited layout element | Ghosted logos behind subtitles on cover slides are common. |
| 6 | Footer present and identical on every content slide | Missing on two slides is the classic tell. |
| 7 | Title and closing slides carry the same system as content slides | Unless explicitly asked otherwise. |
| 8 | Nothing enters the reserved footer zone | Use the y-limit from the brand profile. |
| 9 | Page numbers present | A long deck without them cannot be discussed. |
| 10 | No duplicate slide titles | Three slides called "Next Steps" cannot be referenced. |
| 11 | Contrast holds at presentation distance | Small low-contrast text inside colored boxes disappears in a room. |
| 12 | Logos at native aspect ratio | Compute height from width using real pixel dimensions. |
| 13 | No leftover placeholder text | `markitdown out.pptx \| grep -iE "\bx{3,}\b\|lorem\|\[insert\|TODO\|\.\.\.\|XX%"` |
| 14 | Fonts audited in XML, not by eye | Grep `typeface=`. Misspellings fall back silently. |
| 15 | Theme and layout counts have not exploded | Pasting slides imports their masters. Many themes means many visual systems. |
| 16 | Spelling of titles and headers specifically | These are what get quoted. |
| 17 | Axes not truncated on bounded scales | Data honesty. |
| 18 | **Speaker notes rotated with their slides** | A slide read 75% while its note still said 44%. The visible layer was updated and the notes layer was not. |
| 19 | Placeholder zones filled or consciously re-dated | An `FPO` risks zone survived three consecutive weekly cycles. |
| 20 | Hidden and work-in-progress slides removed | Two cycles shipped a hidden `TBD/WIP` slide riding along at the end. |
| 21 | On-slide date agrees with the filename date | They drift apart on recurring artifacts. |
| 22 | Deck size sane, no stray video or animated GIF | Check `du -sh` on `ppt/media/`. |
| 23 | Voice rules applied to the final copy | Sweep em-dashes to colons. Check the promotional verbs. |

Fix, **re-render**, re-check. One fix routinely creates another. Only call it done after a clean full pass.

### Consistency as a measured quantity

```bash
ls tpl/ppt/theme/ | wc -l ; ls tpl/ppt/slideLayouts/ | wc -l
```

A deck accreted from many sources shows it here. One real deck ran 14 themes and 83 layouts across 33 slides, which is exactly what "three competing visual systems" looks like from the inside. The unified version collapsed to 4 and 34. Measure before and after any consistency pass.

---

## Phase 5. Review, fix, and verify

When reviewing a deck (yours or someone else's), write the review as a standalone document first, before touching the file.

- **Number every defect to a slide.**
- **Rank it P0 (blocks showing anyone) / P1 (blocks publishing) / P2 (polish).** Rank so that what gets dropped is what was designed to be droppable. In one real loop, P0 landed 2 of 4, P1 landed 2 of 5, and P2 landed 0 of 4. That gradient is normal. Plan for it.
- **Give each defect a verdict and a paste-ready rebuild prompt** carrying the brand specs.

Then **verify the fix pass against the review, prescription by prescription.** In that same loop, 6 of 13 prescriptions silently did not land, and one successful fix introduced a new overflow. Without a verify step, both kinds of gap stay invisible.

Record deliberate skips. Nothing otherwise distinguishes "deferred on purpose" from "forgotten."

### Answering someone else's review comments

Use a disposition ledger. Quote each comment, mark it Done / Already reflected / Superseded / Partially done / OPEN, and collect unresolved items into a numbered ask-list. Stamp provisional content `DRAFT` on the slide face with a do-not-publish speaker note.

### When the file has already been edited by a human

**Edit their saved file in place. Do not regenerate from your build script.** Regenerating wipes their filled-in names, links, and deleted slides. Open the current file, confirm the slide count and their changes, then apply only the requested fix. Preserve their copy edits. Flag typos rather than silently rewriting them.

### Version hygiene

Pin chronology to `docProps` (`dcterms:modified`, `cp:revision`, `dc:creator`), never to filenames. In one real pair, the file named "April" was the May output and the file named with a May date was last saved two weeks earlier. **Filenames lie. `core.xml` does not.**

Increment the filename each generation and archive superseded versions. During live rework, move replaced slides to the deck tail rather than deleting them, but delete the tail before shipping. One deck shipped carrying both its new stakeholder slides and its superseded ones, and an outside reader could land on the stale set.

---

## Anti-patterns

| Anti-pattern | What it looks like |
|---|---|
| **Eyeballed brand** | Re-typing hexes from memory. Always one bit off. |
| **Placeholder maximalism** | Trusting layouts so completely that no authored formatting exists, then shipping a title that overprints the first bullet. |
| **Camouflaged decision** | The one item needing action wearing the same shape as background context. |
| **Genre-label subtitles** | "Executive brief" as the cover subtitle instead of the thesis. |
| **Numbers entombed in sentences** | Figures mid-paragraph that no executive will ever extract. |
| **Truncated-axis drama** | Bounded scale starting above zero to exaggerate a small move. |
| **Invented example content** | Frameworks populated with fake entries, read by clients as findings. |
| **Refill without refit** | Pouring real content into placeholders and never re-rendering. |
| **Accretion decks** | Pasting slides from other decks, importing their masters and their palettes. |
| **Silent font fallback** | A typo'd typeface name, invisible in every visual review. |
| **Unmerged replacement slides** | Building the fix as its own file and never merging it, while the placeholder ships onward. |
| **Deleting without bridging** | Cutting hard-to-restyle slides and never shipping the promised pointer to where that content went. |
| **Template media baggage** | Carrying megabytes of unused layout art into every deliverable. |
| **Byte-identical duplicates** | `deck copy.pptx` accumulating in the working folder. Check md5 before treating one as a version. |
| **Media accretion through slide reuse** | Copying one slide from another deck brings its payload. A single countdown slide carried a 44 MB GIF across. |
| **Placeholder rot** | An `FPO` or `TBD` zone that survives cycle after cycle because nobody swept it. |
| **Stale notes layer** | Visible slide updated, speaker note left contradicting it. |
| **The mega-deck** | Operating detail crammed into the executive artifact instead of routed to a companion at the right altitude. |
| **Presenting the instrument as the readout** | Showing the facilitation board (questions and time-boxes) back to the audience instead of building a results artifact. |
| **Strategy layer on a slide** | Negotiation prep, situation reads, and tonal notes escaping the prose note. |
| **Unbounded compilation decks** | An archive that accretes weekly summaries to 84 slides. Useful as a record, unpresentable as a deck. |
| **Entertainment openers** | Icebreaker slides consuming meeting time. Delete the genre rather than restyling it. |

---

## Altitude

### Mint the set in one run

Do not wait to be asked for the short version. In the strongest example, the full readout, a two-slide cut, and a one-slide cut were all produced inside fifty minutes from one evidence base. Altitudes designed together stay consistent. Altitudes compressed weeks apart drift.

| Altitude | Audience | Budget |
|---|---|---|
| Full readout | The working team | Whatever the evidence needs |
| Two-slide | A meeting agenda item | Roughly two thirds of the full word count |
| One-slide | Executives, and anything forwardable | 150 to 220 words |

### Write the verdict sentence before any slide

It has to be short enough to survive every altitude verbatim and strong enough that other people quote it in their own decks. One real example, `The change scaffolding held. Go-live readiness did not.`, was minted in the evidence base with the instruction to state it once and let it carry, then appeared unchanged in three altitudes and as a pull quote in a separate briefing.

### Compress by deleting zones, not by shrinking type

**If text overflows its box, the content is wrong, not the font size.**

What survives compression, in order: the verdict sentence, then item heads, then the ask, then item bodies squeezed to one clause. Counts, narrative, and methodology go first. Put plainly: **the analyst's material dies first and the decision material dies last.**

### What a one-slide artifact contains

A letterspaced kicker naming the source and date. A title. A one-sentence verdict band. Roughly three columns of three chips each, every chip a bold head plus one gray clause. A numbered ask band at the bottom. The brand footer. Cap each column at three chips and let the fuller altitude hold the rest, which is exactly what the two-slide version is for.

Rebuild it as its own composition. A one-slide artifact is not a shrunk deck.

Refresh the dates at distillation time. The original timeline will have slipped, and the distilled version is the one that travels.

### Split by genre, not just by length

A reference library keeps every row with owner and cadence. A signal instrument keeps seven picks with trend arrows and one italic line each. Key each pick back to its library category so a reader can drill in, and make each artifact name its counterpart on the slide face.

When one deck's content is another deck's detail, ship an explicit handoff line rather than duplicating or leaving a gap. A cut without a pointer reads as an omission.

Give any dense instrument a "How to read this" strip explaining what the arrows mean and what it pairs with.

**End every altitude in an ask.** A numbered priorities band, a "what we will ask of you" box, or a closing ask slide.

### A briefing is two artifacts

A prose note holds the strategy layer: the read on the person, the timed arc, verbatim opening and closing lines, anticipated questions, tonal notes. The deck holds only what the audience sees. **Never let the strategy layer onto a slide.**

---

## Recurring-cadence decks

A monthly or weekly deck's job is to be recognized instantly. Identity comes from editing last cycle's file, not rebuilding.

**Fix the skeleton, rotate the zones.** Decide which regions change each cycle and which never do. In a good weekly status, the objective sentence stayed byte-for-byte identical across three weeks and only the dated zones moved.

**A weekly project status is one slide.** Stakeholders, a stable objective, a milestone table with a color legend, key accomplishments as of a date, activities within the next two weeks, and risks. It is read, not presented, so roughly 200 words in a quad chart is correct here even though it would be wrong on a presented slide.

**Keep media byte-identical between cycles** and stay under about 2 MB. Media accretion is the defining failure of recurring decks: one copied slide carried a 44 MB animated GIF background into a meeting deck, another added a 23 MB video icebreaker, and the deck reached 73 MB. The next cycle cut it to 2 MB by deleting decoration, not by restyling anything.

**Delete decoration rather than restyling it,** and use the template's own branded banner slides as section dividers. That converts brand assets into navigation and removes the need for stock photography. The result is often more on-template than the client's own decks, which is the correct posture: hold their brand more strictly than they do under time pressure.

**Update state words in existing titles** rather than adding slides for the same topic: `Coming Soon: X` becomes `X: Now Live`, `Training Completion` becomes `Access: Still Required`.

**Stamp the date in the frame,** and make the on-slide date agree with the filename date.

**Build next cycle's data slide before the data exists,** with visible TBD slots.

**Share components verbatim across genres** so two artifacts cannot disagree. The same milestone table belongs in the walking deck and the weekly status.

A walking deck answers why, what, how, who, and when in that order, then risks and next steps.

Keep an accreting archive deck as the system of record so the presented deck can stay short. Do not present the archive.

---

## Working method

Alternate channels deliberately. Humans edit in PowerPoint to absorb meeting reality, and rough in-meeting notes are fine at that stage. Scripted passes then restructure and polish.

Iterate the hardest one or two slides in an isolated file cloned from the deck, then port the final state back. This keeps the churn off the main artifact.

Split one body of content into genre-shaped artifacts rather than one mega-deck: the deck is the decision surface, a field guide holds the protocols, a workbook holds the data capture. Name the companions on the slide face.

### Whose brand, though

**Palette follows the speaker, not the audience.** Use the client's brand when the artifact speaks for the program. Use your own brand when you are the one speaking, as in a deck pitching your own role. Brand identity is voice attribution.

Where the client's palette lacks a level you need, extend it by darkening an existing accent for hierarchy bands. Never import a foreign hue to carry a semantic role.

### Wear verification on the surface

State currency and its source on the slide: `Confirmed current as of June 2026 (per Karen)`. Leave `[TK]` and `[LINK, PLACEHOLDER]` visible rather than faking them. Claim `Every figure traceable to a named source file. None estimated.` only when an evidence base actually backs every figure.
