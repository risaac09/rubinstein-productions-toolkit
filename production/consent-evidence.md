# Consent Evidence

> **Status:** draft, 2026-10-16. A standard for what proves each consent tier, with checklists for the releases that sit under them and a licence register for music and fonts. The repo states no legal terms. Every release here is a checklist of what to record, not wording to sign.

The measurement framework defines three consent tiers. A tier is a promise, and a promise with nothing behind it cannot be shown to anyone who asks. This page names the one artifact that proves each tier, lists what the releases under tier 3 must record, and gives a register for the music and fonts used in a piece of work. It also says where the evidence lives, which is never here.

## The three tiers

The tiers come from [Consent Layering in the measurement framework](../methodology/measurement-framework.md). They are quoted here and not redefined:

- **Tier 1 (all clients):** Internal learning only
- **Tier 2 (opt-in):** Anonymized aggregate data for methodology refinement
- **Tier 3 (explicit permission + compensation):** Identified case studies for marketing

## The one artifact per tier

| Tier | The artifact that proves it | Where it sits |
|---|---|---|
| 1 | The consent clause in the signed scope | The signed scope, in the engagement record |
| 2 | A written opt-in: a signature or a reply email | Kept with the engagement |
| 3 | A signed case-study release | Kept with the engagement; its id goes on the case study |

Tier 1 needs no separate document. If the signed scope has no consent clause, tier 1 has no evidence and the engagement does not start.

Tier 2 is an opt-in, so silence is not evidence. A reply email counts when it says yes to the aggregate use. Keep the email itself, not a note that one arrived.

A tier 3 release names four things:

1. **The uses.** Each place the case study or its clips may appear. A use not named is not covered.
2. **The people shown.** Every person identified or on camera, by name.
3. **A compensation reference.** A pointer to where the compensation is recorded. The amount does not go in the release or in this repo.
4. **How to withdraw.** The measurement framework gives clients the ability to withdraw from case studies retroactively. The release says who to tell and what happens to published pieces.

One release can cover a whole engagement or one piece. Either way, the case study carries the id of the release that covers it.

## Release checklists

These three releases sit under tier 3 and under any footage with a person or a place in it. Each list is what the release must record. Use a template from your own counsel or your own jurisdiction. This page does not supply one.

### Appearance release

- [ ] Full name of the person, and the date of signing
- [ ] Who is recording, and for which engagement
- [ ] What is recorded: image, voice, name, words, or all of these
- [ ] Each use the person agrees to, as named uses
- [ ] Where and for how long the footage may be shown
- [ ] Whether the person may see the cut before it is published, and who has final approval
- [ ] Whether the person is under the age of majority, and who signed for them
- [ ] How to withdraw, and who to tell
- [ ] A reference to the compensation record, if there is compensation
- [ ] The signature or the reply that stands in for it

### Location release

- [ ] The place, described so a stranger could find it
- [ ] Who has authority over it, and in what capacity (owner, tenant, manager)
- [ ] The dates and hours of filming
- [ ] What may be shown: interior, exterior, signage, artwork, other people's property in frame
- [ ] Each use the footage may be put to, as named uses
- [ ] Any area, object or hour excluded from filming
- [ ] Who to tell if the place needs a shot removed after publication
- [ ] The signature or the reply that stands in for it

### Talent release

For anyone who performs, presents or is directed on camera as part of the work, including a facilitator, a host or a hired performer. It is separate from the appearance release because the terms around the work differ.

- [ ] Full name, role in the piece, and the date of signing
- [ ] What the person contributes: performance, narration, words, music, direction
- [ ] Each use of the contribution, as named uses
- [ ] Whether the person is credited and how, as in [credits and AI disclosure](credits-and-disclosure.md)
- [ ] Whether the person's voice or likeness may be altered by a machine-assisted step, and which
- [ ] A reference to the compensation record
- [ ] How to withdraw, and who to tell
- [ ] The signature or the reply that stands in for it

## Licence register

Keep one register per engagement. Every piece of music and every font in the delivered work gets a row, whether it was bought, free, bundled with a tool or supplied by the client. A row is complete when someone else can find the proof without asking you.

| Asset | Source | Licence | Scope of use | Proof | Checked |
|---|---|---|---|---|---|
| `<track title and artist>` | `<where it was obtained>` | `<licence name or type>` | `<what it may be used for, where, for how long>` | `<id or path of the receipt, licence file or email>` | `<YYYY-MM-DD>` |
| `<font family and style>` | `<foundry or library>` | `<licence name or type>` | `<screen, print, embedding, number of users>` | `<id or path>` | `<YYYY-MM-DD>` |
| `<asset>` | `<source>` | `<licence>` | `<scope>` | `<id or path>` | `<YYYY-MM-DD>` |

Check the register against the final cut before the [delivery manifest](delivery-manifest.md) is written. An asset with no proof is replaced or cleared before delivery. The checked date is the day a person compared the scope of use against where the work is going, not the day the row was made.

## Where the evidence lives

Evidence lives in the private engagement record. The engagement record holds a `consent_evidence` reference: an id, or a path inside the private record. This page and the rest of the public repo show the field name only.

Evidence names people. Signed releases, opt-in emails, location details and the licence register itself never go in this repo, in an example, or in a commit message.

The case study carries the reference. The [Case Study template](../templates/case-study-template.md) has a required "Consent evidence" line for the tier and the evidence id. A case study with no tier 3 evidence id is not published.

## Open

- This page does not say how long evidence is kept after an engagement ends. Retention is a decision for the practice and its counsel.
- The page does not cover recordings of sessions that are never published. Those fall under tier 1 and the signed scope.
