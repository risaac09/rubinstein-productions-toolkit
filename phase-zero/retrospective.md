# Retrospective — reflect and log

The bookend to phase zero. Phase zero opened the session. This closes it. Run a
short, honest retrospective on THIS chat, then log it. No performance.

Read the session and answer in four directions, briefly:

- **North, work.** What the work cost. What stalled, what took more turns than it
  should have.
- **East, innocence.** What surprised you. What you or the operator missed before
  interpreting it.
- **South, transition.** What is still metabolizing. The open thread the next
  session inherits.
- **West, clarity.** What arose and passed. Nothing to store. Optional.
- **Keep.** The one durable learning worth carrying forward.

Then log it. In stack-data:

    scripts/sd-retro --json '{"surface":"code","north":"...","east":"...","south":"...","west":"...","keep":"...","tags":[],"items":[]}'

`items` names the planning ids this session moved (`scripts/sd-ref --list
--state open` shows them), or stays `[]`. An unknown id refuses the entry.

If `scripts/sd-retro` is not in this repo, you are not in stack-data. Emit the
structured entry above so the operator can log it there. The log is private and
loose, out of the entity graph and the PWA index, like the Lived Record. It
never goes public and never feeds phase zero. Surface a learning only when the
operator asks.

## Deep form

Use it, unasked, when this session did any of these: touched more than one
repo; pushed or merged to main; changed a hook, floor, setting or the kit;
started other sessions or a fan-out; had a near-miss or an incident; produced
a decision of record. The word "deep" in the trigger prompt forces it. A light
session stays short; empty fields are honest.

Answer the four directions as above, then add:

- **Keeps.** Up to three, ranked. The first is also `keep`.
- **Threads.** Each open item on its own: what it is, where it lives (file, PR,
  repo), and what blocks it.
- **Counterfactual.** The cheapest check that would have caught the main
  problem earlier.
- **Failure mode.** A candidate for stack-data's `docs/FAILURE-MODES.md`, one sentence, or
  empty.
- **Decision.** A candidate for stack-data's `docs/decisions/`, one sentence, or empty.
- **Evidence.** The PRs, commits, session ids and retro ids the entry rests on.
  Read them back; never write one from memory.
- **Review.** How many `/code-review` findings there were and how many were
  fixed. Omit it when no review ran.

Log it the same way, with the deep fields added:

    scripts/sd-retro --json '{"surface":"code","depth":"deep","north":"...","east":"...","south":"...","west":"...","keeps":["...","..."],"threads":[{"what":"...","where":"...","blocker":"..."}],"counterfactual":"...","failure_mode":"...","decision":"...","evidence":["owner/repo#123"],"review":{"findings":0,"fixed":0},"tags":[],"items":[]}'

A deep retro written later for an earlier session names it with
`"extends":"retro-YYYYMMDD-xxxx"`. Candidates are flags for the operator, not
writes: nothing here edits FAILURE-MODES, the decisions or LEARNINGS. Promotion
stays by hand.
