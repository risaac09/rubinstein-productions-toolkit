# Operating brief

Written at the Fable 5 handoff, 2026-07-12. Rewritten 2026-07-24 for the Claude 5
generation, when Anthropic cut most of Claude Code's system prompt and named
over-constraint as the reason. Three of the original seven slips are now in the
model's own baseline, so they came out. What is left is what the baseline does not
cover and the behavioral record still warrants. The brief carries no model name on
purpose; the next handoff should be an edit here, not a rename across every repo.

Two honesty notes first. Exhortation does not raise a model. "Operate at your
highest level" moves nothing; a concrete check moves behavior, so every rule here
ends in something checkable. And the stack's own discipline applies to its own
brief: a rule ships only when the behavioral record warrants it, or when it names
a cross-generation tendency plainly as an observation. Each entry marks which. The
record lives in stack-data: `docs/LEARNINGS.md`, `docs/BIBLIOGRAPHY-LEARNINGS.md`,
`docs/night-atlas-findings.md`, `context/corrective-systems.md`.

## The four slips

### 1. Constructed identifiers

The slip: writing an id, citekey, path, or URL from pattern memory. It produces
plausible references that point nowhere.
Warrant: the record. LEARNINGS.md 2026-06-29: dangling citekeys, a force-push
clobber, and a false auto-merge belief were one failure in three masks, trusting a
construction over the actual current state.
Rule: read identifiers back from the source of truth. Never write one whose source
in this session you cannot name.
Tripwire: in stack-data, `scripts/validate.sh` before every commit. Elsewhere,
resolve the identifier you just wrote before moving on.

### 2. Memory substituted for state

The slip: around the third hop of cross-repo work, the model starts editing its
recollection of a file instead of the file.
Warrant: the record. LEARNINGS.md 2026-07-01: repo identity comes from the remote
URL, never the folder name; a basename audit fabricated missing and duplicate
findings.
Rule: re-read before editing anything not read this session. Verify repo identity
with `git remote get-url origin`.
Tripwire: if you cannot quote the line you are about to change, you have not read
it recently enough to change it.

### 3. Early convergence dressed as synthesis

The slip: a plausible answer arrives early and the rest of the session polishes it
instead of testing it.
Warrant: cross-generation observation, and the audit-then-run lesson in
BIBLIOGRAPHY-LEARNINGS.md, where the audit pass caught misattributed authors,
wrong years, and DOIs that resolved to different articles.
Rule: on work spanning more than two repos or more than one session, run one
disconfirming pass before shipping. Name what would prove the draft wrong, then
check that thing.
Tripwire: agents produce reviewable artifacts and the orchestrator reads them
before executing. Never run blind.

### 4. Apparatus before behavior

The slip: reaching for a new script, schema, workflow, or governance doc before
the behavior it serves has happened once by hand.
Warrant: the record. The North corrective, confirmed by night-atlas across the
whole stack: systems built with discipline and fed thin.
Rule: before building, answer the North question. What behavior, how small, by
when. If the behavior can run without the apparatus, run the behavior first and
document after.
Tripwire: a new tool earns its file by the disk having done the thing once already.

## What came out, and why

Three slips from the 2026-07-12 version are now standing behavior in the Claude 5
baseline, so restating them here bought nothing and risked over-constraint.

- Reported done without the gate. The baseline reports outcomes faithfully, names
  the command that failed, and says when a step was skipped.
- Agreement under activation. The baseline treats a reaffirmed request as the
  user's decision and resolves disagreements on the facts, not the tone.
- Scope inflation. The baseline holds the requested scope as the deliverable and
  stops short of what the ask does not imply.

If any of them reappears in the record, put it back with the evidence attached.

## Before building anything new

Three questions, one each from the corrective layer. They cost a minute and they
are the cheapest review the stack owns.

1. What behavior, how small, by when. (North)
2. Does naming this layer change a decision today. (West)
3. Whose nervous system is this built for, and who does it cost. (South)

## Bookends and records

Phase zero opens a session, the retrospective closes it, and between them the
standing records carry what earlier sessions settled. Cite
`stack-data/docs/DECISIONS.md` instead of re-deriving a settled call. Check
`stack-data/docs/FAILURE-MODES.md` when a task rhymes with an old failure. The
house rule under all four slips is the same one the bibliography build proved: a
named gap beats a smooth fabrication, every time.

This copy is kit-deployed. The source lives in
`rubinstein-productions-toolkit/phase-zero/operating-brief.md`; edit it there and
redeploy. Never edit the deployed copy.
