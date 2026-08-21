---
type: reference
cssclasses: [reference]
created: 2026-05-23
status: working-draft
canonical: true
license: CC-BY-SA-4.0
companion-to:
  - "Working Context Block"
  - "Total Cost of Ownership"
  - "Total Cost of Ownership - Sources"
  - "master-bibliography"
tags: [canonical, sources, protocol, meta-prompt]
---

# Source-Tracking Protocol

A meta-protocol for responses where sources matter. Applies to canonical doc authoring, research synthesis, public-facing writing, and any response that makes claims attributable to a named source.

## Categories

Every attributable claim falls into one of these:

1. **Primary source (verified this session).** A specific cited work the agent has access to or has actually retrieved in this session. Web pages fetched, vault notes read, books quoted with page numbers. Includes URL or vault path.

2. **Secondary synthesis (verified this session).** A textbook, encyclopedia, commentary, or summary by a named author that has been actually consulted in this session. Includes URL or path.

3. **Cited from prior knowledge.** A specific named source referenced from training data without having verified it this session. Marked explicitly. Format: "Author (year), Title, cited from training data, not verified this session."

4. **Personal / conversational.** Information from Isaac's prior messages, vault context, or stated experience. Cite the message or vault path.

5. **Model synthesis.** Claims that are agent synthesis, inference, or reasoning rather than from a specific source. Marked explicitly with "(model synthesis)" inline.

## Bibliography format

A response that makes source-attributable claims ends with a bibliography in this shape:

```
### Sources used

**Primary (verified this session):**
- [Author / Title / Path or URL]

**Secondary (verified this session):**
- [Author / Title / URL]

**Cited from training data (NOT verified this session, treat with caution):**
- [Author (year), Title]

**Vault / conversation sources:**
- [Vault path or message reference]

**Unsourced model synthesis used in this response:**
- [List the claims that are agent-generated, not from any source]
```

## Standing rules

- If a claim has no real source and is not flagged as synthesis, fix that before sending. Either find the source, demote the claim to flagged synthesis, or remove it.
- Never invent a citation. If a source cannot be verified to exist, do not list it.
- Vague-authority moves ("2,500 years of tradition," "ancient practice," "the tradition says") require either a specific source or removal.
- For vault sources, use the actual path so Isaac can open them.
- Sources section is mandatory, not optional. If the response has no claims worth sourcing, say so explicitly: "Sources: none. Response is entirely model synthesis from the conversation."

## Application to canonical docs (standing convention)

Every canonical doc in `vault/00 Canonical/` has a companion sources document. Naming: `[Doc Title] - Sources.md`.

The companion:
- Is linked from the main doc's `companion-to:` frontmatter
- Lists every named theorist, tradition, or specific concept cited in the main doc
- Uses APA 7th edition (matching master-bibliography format)
- Marks each entry with one of the five provenance categories above
- Maintains a "Verification Queue" section at the bottom for QUEUED items
- Is updated whenever the main doc gains a new attributable claim

A claim should never be more authoritative-sounding in the main doc than its attribution chain in the companion.

## Deeper standard: claim-level tracing (for high-stakes docs)

For canonical docs that will be externally cited, evaluated, or used to ground further work, the theorist-level sources companion is insufficient. A second companion is required: a claim-trace doc that traces each asserted sentence in the prose through four steps to a primary source.

Naming: `[Doc Title] - Claim Trace.md`.

The four-step trace for each claim:

1. **Asserted text.** Exact quote from main doc, with section location.
2. **Logical claim.** The proposition the prose asserts, in plain form.
3. **Document layer.** Intermediate text(s) where this claim is articulated or discussed (textbook, commentary, secondary source).
4. **Primary source.** The foundational text or original research that grounds the claim, with specific location (chapter, page, section, DOI) when known.

Plus the status (VERIFIED / CITED / QUEUED / UNSOURCEABLE / PERSONAL-SYNTHESIS) and notes (caveats, debates, rhetorical-compression flags).

This is the standard Isaac established on 2026-05-23 after noting that the theorist-level companion still leaves individual claims un-traced. A reader of the prose has no way to verify which sentence in the main doc is grounded in which passage of which work without a claim-level trace.

The trace catches rhetorical compressions ("spends ATP that a white body does not"), interpretive bridges between traditions ("In the Abhidhamma, this is cetanā"), and synthesis claims ("the cost is not metaphorical, it is metabolic") that the theorist-level companion can miss.

Worked example: Total Cost of Ownership - Claim Trace (North section traced; East/South/West/table/Say Why queued).

A canonical doc may live with theorist-level companion only until external use is imminent. When the doc moves toward external citation, the claim-level trace becomes mandatory.

## Why this exists

Un-attributed AI-generated summaries have a known failure mode: they read as authoritative without earning the authority. The phrase "what I have is a generated summary" was Isaac's articulation of the concern that triggered this protocol. Both the prior Total Cost of Ownership canonical doc (then titled Four-Direction Oscillation) and the abhidhamma-mapping docs cited zero primary sources despite making authority-laden claims.

This protocol forces the gap visible. Cited-from-training-data sources are real but un-verified; they go in the queue for verification when stakes warrant. Vague-authority claims either earn citation or get cut.

## Application beyond canonical docs

When sources matter in a response (research synthesis, public-facing writing, proposals, anything that will live longer than the chat), include the protocol explicitly as a standing instruction in the prompt or apply it tacitly through the bibliography section. The pre-prompt in Working Context Block references this protocol as a standing capability.

---

**Origin:** Developed in conversation with Claude on 2026-05-23 in response to the discovery that both the Total Cost of Ownership canonical (then titled Four-Direction Oscillation) and the abhidhamma-mapping docs made authority-laden claims without source attribution.
