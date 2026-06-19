# Phase Zero kit

The portable global-awareness layer. It travels into every repo so every clone
session, local or remote, runs the same infrastructure.

## What it does

When a prompt contains one of the trigger phrases, a `UserPromptSubmit` hook
loads the phase-zero hierarchy into the model's context before the turn runs:

- "activate all agents"
- "engage global awareness"
- "delegate to your orchestrator"

The orchestrator reads the map (identity, the four directions, the source of
truth, the delegation protocol), names the direction the task faces, then
delegates down to the branch the task needs.

## Files

- `phase-zero.md` — the portable core. Identity (Isaac, RP, the Material and
  Meaning research institute, the Third Third Information Lab), the directions,
  the voice rules, the delegation protocol. Present in every repo.
- `hooks/phase-zero-trigger.sh` — the UserPromptSubmit hook.
- `settings.json` — the hook registration, copied to a repo with no existing
  settings; merged in (jq) when one exists.
- `install.sh` — the distribution path.

## Source of truth

stack-data holds the canonical, live hierarchy in `stack-data/PHASE-ZERO.md`,
rendered with `stack-data/scripts/phase-zero` (adds live unreviewed-signal
counts across the three listening layers). In stack-data the hook prefers that
renderer; in every other repo it falls back to the portable core here.

## Install

    # one repo
    ./install.sh ../saywhy-app

    # every git repo one level down
    ./install.sh --all ..

Re-run any time to refresh the kit. When `phase-zero.md` or the hook changes
here, re-sync to propagate.
