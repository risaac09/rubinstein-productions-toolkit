# Credits and AI Disclosure

> **Status:** accepted 2026-10-16. Two short standards for produced work, built from what the production tooling does and what the consent tiers already promise. This document states no copyright or licence terms.

## Credits

**A credit is a consent matter before it is a style matter.** Nobody is named on a piece of work who has not agreed to be, in the words they approved. The [consent tiers](../methodology/measurement-framework.md) govern case studies and shared data, not the client's own film, but the same posture applies: a participant who wants to stay unnamed stays unnamed, and an identified case study needs the Tier 3 permission on top of the credit. The credits line goes through the [final-approval gate](resolve-template-spec.md#the-final-approval-gate) with the rest of the work, so each person named has read their own credit.

**Who is credited, and for what.** The camera-shipping model puts the means of production in the participant's hands, so the credit says what each person did.

- **Filmed by**: the participant who held the camera, where they consented to be named. It is the accurate description of the footage and the structural point of the method.
- **Facilitated by**: the person who held the witness position during filming.
- **Edited by**: the person who cut the picture. If one person did both, one line may carry both roles.
- **Colour, sound and captions**: credited when a different person did them, or left to the single post-production credit when one person did all of it.
- **Commissioned by**: the organization, where it agrees to be named and where it commissioned the work.

Other roles are credited when a person did them and agreed to be named. Nobody is credited for a role they did not do.

**Order.** The role that made the footage first (filmed by), then the role that shaped it (facilitated, edited), then the rest, then the commissioner. A different order is fine if it is stated in the approval record.

**Form.**

- On the work: a short end card or a closing line, readable at the platform's smallest frame. It names roles and people and nothing else. It carries no slogan. A cut under 60 seconds has no end card, and the credit goes in the post text.
- In the description, caption or page text: the same names in the same order, in plain text, so the credit survives a platform that crops the end card. On a cut under 60 seconds this is the only place the credit appears.
- **Licence-required credit.** A credit that a licence demands, such as a track or a font, goes in the end card or closing line when the work has one, and in the post text always. A cut under 60 seconds has no end card, so there it appears in the post text and nowhere else. The register's attribution column, in [consent evidence](consent-evidence.md#licence-register), says what each licence asks for. This credit goes through the gate with the rest of the credits line.
- In the [manifest](delivery-manifest.md): a line saying where the credits appear, or "none requested".

**Names.** Spelled as each person spells them, checked at the gate. A name is never abbreviated, anglicized or made up from an email address.

**No credit.** A participant who chooses not to be named is not named. The work carries the roles without the names, or no credits line. This is the participant's choice, recorded at the gate, and it can be changed afterward at the participant's request, as the measurement framework's withdrawal right already provides.

## AI disclosure

Some steps in the production flow are machine-assisted. Nothing in this repo generates a face, a voice or footage, and the practice uses no generative content in client deliverables. The standard says what is stated, to whom, and where.

### What counts as machine-assisted

The steps the tooling in this repo performs, which a client may want to know about:

- **Auto captions.** Resolve's speech recognition transcribes the audio. The transcript is a draft.
- **Face-tracked reframing.** macOS Vision finds the speaker's face to place the 9:16 and 1:1 crops, and a person checks the result.
- **Stabilization.** Resolve's stabilizer changes the picture of an item.
- **Loudness correction.** An ffmpeg two-pass normalization adjusts the audio level. Video is copied untouched.
- **Analysis and drafting.** Where an AI model reads a transcript to code it, to draft a summary or to propose a cut, the output is a proposal that a person accepts or discards. The trim review works this way: it writes a proposals file and marks a timeline, and deletes nothing. The same rule holds for any model-assisted coding or summary.

Generative content is a separate class and is named separately: a synthesized voice, a generated or altered image of a person, generated footage or music, or any picture or sound the camera did not record. Client deliverables contain none of it.

### What is stated, to whom

1. **To the client, before work starts and again at handover.** The scope names which machine-assisted steps will be used. The [manifest](delivery-manifest.md) lists the ones that were. A client can ask for a step to be left out, and the answer is yes unless it makes the work impossible, in which case the scope is renegotiated.
2. **On the work, never for assistive steps.** A caption file produced by speech recognition and corrected by a person is a captioned video. It does not carry a label. A caption file not checked by a person does not ship.
3. **Platform labels.** Some platforms ask for a label on synthetic or altered media and change their rules often. The kit owner looks up the current rule for each destination at queue time and applies it.

### Rules that do not bend

- **No synthetic likeness or voice of a participant.** No cloned voice, no face swap, no generated stand-in. In a client deliverable the ban on generative content already covers this, with no exception. In any other work the one exception is the person's written consent for that use, kept in the approval record. A participant never needs to give this consent to be filmed.
- **Words stay the participant's own.** A machine does not write, rephrase or fill a participant's words on camera. Captions follow what was said. A correction to a caption is made against the audio.
- **A person checks what a machine drafted.** Captions are read against the audio, reframes are watched, and an AI-drafted summary or cut list is read before anyone acts on it. The gate shows the client the captions as burnt in or as the sidecar file.
- **The disclosure is true.** If a step was machine-assisted and the manifest does not say so, the manifest is wrong and is written again.

### Disclosure wording

Plain statements, one per fact, with no apology and no boast.

- "Captions were generated by speech recognition and corrected by a person."
- "The vertical and square versions were reframed with face detection and checked by a person."
- "No part of this film was generated by AI." Use it only when it is true of the whole piece, which is every client deliverable.
