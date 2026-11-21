---
description: Clear the corpus inbox by verdict, one note at a time, moving only on a named yes
argument-hint: ""
---

Walk Isaac through the corpus inbox. About 20 minutes. He clears each note; you move nothing on your own.

Rules that hold the whole way through:
- A note moves only on Isaac's named yes for that note. A yes to one note is not a yes to the next.
- One note per message.
- Nothing is ever deleted. No `rm`.
- A tag is a proposal, not an instruction. Ask "move, keep, or skip" and add no recommendation beyond what the tag already says.
- Write nothing to stack-data. Work only inside the vault (`~/vault/Second Brain v2/_INBOX/` and the destinations the tags name).
- Add no park cap or park rule.

Steps:

1. Run `~/stack-data/scripts/sd-inbox-triage --list` for the census. It makes no model call. Then read the latest `YYYY-MM-DD workflow proposal.md` in `_INBOX/`, and read the frontmatter of the top-level inbox notes. Keep the notes whose `triage.verdict` is set and whose `status` is not `cleared` or `dropped`.
2. Walk them by verdict, in this order: DECIDE, EXECUTE, FILE, DEPRECATE, KEEP. Each message gives the note's title, the tag's `why`, and the destination the tag names, then the one question: "move, keep, or skip".
3. On Isaac's named yes for a note, show the exact `git mv` first, then run it inside the vault. For DEPRECATE the destination is `_INBOX/_archive/` (create it if missing) and the note gets `status: dropped`. For every other move the note gets `status: cleared`.
4. After a move, edit the frontmatter as a text insert only, so every other line keeps its bytes: set `status`, add `cleared: <today, YYYY-MM-DD>` and `to: <new path>`. Use the tagger's guards: skip a note modified in the last 30 minutes, and re-read the file right before the edit and stop if it changed. A note with no frontmatter gets no edit; say so.
5. "keep" leaves the note as it is. "skip" leaves it for next week.
6. At the end, on Isaac's word set the proposal's `status:` to `acted` or `declined`. That resumes the weekly text. Do not set it without his word.
7. Close with three lines: what was cleared, what was kept, what was skipped.
