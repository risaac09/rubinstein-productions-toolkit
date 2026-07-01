# Phase Zero kit

The portable global-awareness layer. It travels into every repo so every clone
session, local or remote, runs the same infrastructure.

## What it does

When a prompt contains one of the trigger phrases, a `UserPromptSubmit` hook
loads the phase-zero hierarchy into the model's context before the turn runs:

- "activate all agents"
- "engage global awareness"
- "refresh global awareness"
- "delegate to your orchestrator"
- "engage the orchestrator"
- "engage your orchestrator"

The hook's case pattern is the canonical set; when a doc and the hook disagree,
the hook wins and the doc gets fixed.

The orchestrator reads the map (identity, the four directions, the source of
truth, the delegation protocol), names the direction the task faces, then
delegates down to the branch the task needs.

The bookend is the retrospective. When a prompt contains "log learnings",
"retro this chat", or "session retrospective", the same hook loads the
reflect-and-log prompt instead. The agent runs a four-direction retrospective on
the session and logs the one learning worth keeping with `scripts/sd-retro` in
stack-data. Phase zero opens a session, the retrospective closes it. The log is
private and never feeds phase zero back.

## Files

- `phase-zero.md` — the portable core. Identity (Isaac, RP, the Material and
  Meaning research institute, the Third Information Lab), the directions,
  the voice rules, the delegation protocol. Present in every repo.
- `retrospective.md` — the portable retrospective prompt loaded by the retro
  triggers. Present in every repo.
- `hooks/phase-zero-trigger.sh` — the UserPromptSubmit hook (phase-zero and
  retrospective triggers both).
- `settings.json` — the hook registration, copied to a repo with no existing
  settings; merged in (jq) when one exists.
- `install.sh` — the distribution path.

## Source of truth

stack-data holds the canonical, live hierarchy in `stack-data/PHASE-ZERO.md`,
rendered with `stack-data/scripts/phase-zero` (adds live unreviewed-signal
counts across the three listening layers). In stack-data the hook prefers that
renderer; in every other repo it falls back to the portable core here.

## Install (per repo)

    # one repo
    ./install.sh ../saywhy-app

    # every git repo one level down
    ./install.sh --all ..

Re-run any time to refresh the kit. When `phase-zero.md` or the hook changes
here, re-sync to propagate. Per-repo install covers every clone session of that
repo, local or remote.

## Install (per machine, global)

For a local machine where you move across repos in one shell, install the hook
at the user level so the triggers fire in every session, including outside any
repo. Run once per machine:

    # picks up ~/stack-data automatically, or pin it:
    STACK_DATA_DIR=/path/to/stack-data global/install-global.sh

This writes `~/.claude/hooks/phase-zero-trigger.sh`, copies the portable core to
`~/.claude/phase-zero.md` as a fallback, and merges the hook into
`~/.claude/settings.json` without disturbing existing user settings. The global
hook prefers the live stack-data renderer (signal counts) and defers to a repo's
own phase-zero hook when you are inside one, so phase zero never prints twice.
