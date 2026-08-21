# README shape standard

Not a template to copy verbatim — a shape to check a public repo's README
against. Order matters less than coverage; skip a section if it genuinely
doesn't apply, don't skip it because it's more work.

1. **Title + one-line tagline.** What this is, in one sentence, before
   anything else.
2. **Who built it and why (short).** A sentence or two, not a biography.
   Link out to a fuller "why" if one exists rather than inlining it.
3. **What this is / is not.** Especially for a repo that used to be
   something broader — say what narrowed and why, so a reader with the old
   mental model isn't left guessing. See this kit's own source repo,
   `rubinstein-productions-toolkit`, for a worked example ("What's Not Here
   (and why)").
4. **What's inside.** One subsection per top-level directory or major
   piece, each a sentence or two, not a full API reference.
5. **License.** Which lane (code/data/methodology — see this kit's
   `LICENSE-*.template` files) covers what, plainly stated, not just a
   badge.
6. **Quick start.** The shortest real path from clone to first working
   result. Runnable commands, not prose describing commands.
7. **Who this is for.** Named audiences, not "everyone." If the repo has
   a role-based reading path (a field guide, a docs index), point to it
   here.
8. **Footer.** Author, contact or link, nothing more.

## Voice

Apply `VOICE-RULES.md` to every sentence in the README. It's the first
thing anyone reads; if the voice rules slip anywhere, they slip here first
and a reader notices.

## Checking an existing README

Read it once as a stranger would: does it say what the repo is before it
says how great the repo is? Does "what's inside" match what's actually in
the tree right now? Is there a claim (like the toolkit's old "nothing
private lands here") that isn't true anymore? That last check is the one
most likely to have gone stale — repos narrow and split over time, READMEs
don't always keep up.
