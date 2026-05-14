# Proposal Builder

Branded PDF generator for evaluation consulting proposals, capabilities statements, and cover letters. Reproduces the Ink/Ochre/Teal visual identity used across all RP eval-side outbound documents.

## What's here

| File | Purpose |
|---|---|
| `build_capabilities_statement_generic.py` | 1-page capabilities statement PDF. Generic-recipient version; copy-and-adapt for tailored variants per recipient. |
| `build_cover_letter.py` | Cover letter PDF builder with the shared visual identity. |
| `README.md` | This file. |

Bid-specific build scripts (proposals tailored to a particular RFP, named after the prospect) live in the private intranet repo at `~/rp-intranet/scripts/proposal-builder/`. The pattern is copy-and-adapt: clone a prior bid script to start a new proposal.

## Dependencies

```bash
pip3 install --user reportlab
```

Tested with `reportlab 4.4.10` on Python 3.9.

## How a new proposal gets built

The current pattern is **copy-and-adapt**: each proposal is its own Python script that imports nothing from the others. Reuse happens by copying the styling block (colors, ParagraphStyles, helper functions, page templates) verbatim and replacing the story.

1. Copy an existing bid script from `~/rp-intranet/scripts/proposal-builder/` → `build_<prospect>_proposal.py` (working dir of your choice, typically the prospect's vault folder).
2. Update at the top:
   - Module docstring
   - `OUTPUT_DIR` (vault path to the prospect folder)
   - `OUTPUT_FILE` filename
3. Update `later_pages()` — the running header text on pages 2+.
4. Replace the story (everything after `story = []`) with the new proposal content using the helper functions:
   - `section_header(text)` — H1, dark-blue bold
   - `sub_header(text)` — H2, ochre/gold bold
   - `sub_sub_header(text)` — H3, teal bold
   - `body(text)` — justified body paragraph (supports `<b>`, `<i>` inline)
   - `bullet(text)` — bulleted body paragraph
   - `small(text)` — slate small text (footnotes, "available on request")
   - `ochre_rule()`, `thin_rule()` — horizontal rules
   - Tables: build with `Table(...)` + `TableStyle(...)`
5. Run: `python3 build_<prospect>_proposal.py`

## Visual identity (don't change without need)

Brand colors live at the top of each script:

- `INK` `#0F1729` — H1 titles, top accent bar
- `OCHRE` `#9A6B2F` — H2 subheaders, gold accent rules
- `DATA` `#2B6A6E` — H3 sub-subheaders, table header backgrounds
- `GRAPHITE` `#2A2D34` — body text
- `SLATE` `#5A6170` — footers, meta, small text
- `RULE` `#D8D2C3` — thin separators, table grid lines
- `DATA_WASH` `#E6F3F4` — alternating table row background
- `OCHRE_WASH` `#F5EDE0` — total row background in tables

Typography is Helvetica throughout (system font). H1 17pt, H2 13pt, H3 11pt, body 10.5pt with 14pt leading.

Page format: US Letter, 1-inch left/right margins, 0.9" top, 0.85" bottom. Top accent: 8pt INK bar + ochre line. Bottom accent: 0.75pt ochre line. Page numbers centered at bottom.

## Page-count compression

For tight RFP page limits, compress default spacing:

- `subtitle.spaceAfter`: 24 → 14
- `h1.spaceBefore`: 28 → 16, `h1.spaceAfter`: 10 → 6
- `h2.spaceBefore`: 18 → 10, `h2.spaceAfter`: 8 → 5
- `h3.spaceBefore`: 12 → 8, `h3.spaceAfter`: 6 → 4
- `body.leading`: 16 → 14, `body.spaceAfter`: 8 → 5
- All `PageBreak()` calls removed (let content flow naturally)

For longer proposals where page count is not a constraint, restore the original values for more breathing room.

## Refactor backlog

The current pattern requires hand-editing a Python file per proposal. Real fix: a single `build_proposal.py` that takes a markdown file path and a config (output path, header text, brand-color overrides) as arguments, parses the markdown, and emits the PDF. That requires writing a markdown-to-ParagraphStyle parser. Until then, copy-and-adapt is the workflow.

Other follow-ups:

- Pin the reportlab version in a `requirements.txt`.
- Extract brand colors into a single `brand.py` shared with the RP intranet CSS so a color change is a one-place edit.
