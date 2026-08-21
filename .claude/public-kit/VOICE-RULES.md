# Public voice rules

The canonical, reconciled version of rules that were hand-duplicated (and
drifting) across `rubinsteinproductions/CLAUDE.md` and `stack-data/CLAUDE.md`.
Apply these to all public-facing copy: READMEs, marketing sites, published
content, public repo docs.

- **No em-dashes.** Use commas or periods.
- **No rule-of-three.** Three-part lists where the third item is filler.
  Two beats three. Four-plus items as honest enumeration is fine.
- **No promotional or business-jargon verbs.** "Leverages", "empowers",
  "transforms", "unlocks", "navigate", "deep dive", "lean into". Say what the
  thing actually does.
- **Active voice, real subjects.** No false agency — inanimate things don't
  act, name the human. State the actor and the action plainly.
- **No throat-clearing openers.** "Here's the thing:", "Let me be clear",
  "The truth is,".
- **No vague declaratives.** "The implications are significant", "the stakes
  are high". Name the specific thing.
- **No adverb stacking.** "Really", "just", "literally", "genuinely",
  "honestly", "actually".
- **No binary contrasts.** "Not X, it's Y". State Y.
- **Vary sentence length.** No staccato. Long, long, longer, short.

## Provenance

- `rubinsteinproductions/CLAUDE.md` carries the fuller public-facing
  articulation (all nine rules above, written for the public marketing
  site's copy standard).
- `stack-data/CLAUDE.md` carries a terser working-voice variant: "No
  em-dashes. No rule-of-three. No promotional verbs (leverages, empowers,
  transforms, unlocks). Active voice. Concrete nouns. Short sentences. Echo
  his words. If he says 'the thing,' say 'the thing.'"
- The two lists overlap on em-dashes, rule-of-three, promotional verbs, and
  active voice — reconciled above into one canonical statement of each.
  `stack-data`'s "concrete nouns", "short sentences", and "echo his words"
  are Isaac's own working-voice preferences for talking to Claude, not
  public-copy rules, so they're deliberately not carried into this public
  kit. If a consuming repo wants that fuller personal-voice register too,
  it stays in that repo's own CLAUDE.md rather than this shared kit.

Deployed by `phase-zero/install.sh --public` into `.claude/public-kit/` in
every `PUBLIC_CONSUMERS` repo. Edit here, then redeploy.
