# CLAUDE.md

## What this repo is
The public toolkit: methodology, prompts, CLI tools, and the phase-zero kit source. Take what's useful is the license posture; nothing private lands here (that went to rp-intranet).

## The one guardrail
This repo is the kit source. Edit `phase-zero/` here, then redeploy with `phase-zero/install.sh --all <parent-dir>`. The deployed `.claude/` copies in the consuming repos are byte-identical output; never edit one in place, the next install overwrites it and the edit dies silently. Consuming repos, current thirteen (updated 2026-08-05: isaacrubinstein.com rejoins, unarchived 2026-07-28 with live cloud sessions, its 08-05 one-off install formalized so --check --all watches it; before that 2026-08-02: mercer archived at its operator's call, the time-box honored; and 2026-07-20, the thinning wave: second-brain-v2 folded into rp-shared, royal-metrics into three-type-evaluation, third-information-lab into material-and-meaning-institute, all three archived): stack-data, second-brain-mirror, rp-shared (the products repo, saywhy-app and slop-tools merged in and no longer carry their own copy; apps/apparatus absorbed second-brain-v2), rubinsteinproductions, rp-intranet, alchemy, material-and-meaning-institute, home-scripts, gene-keys-data, three-type-evaluation, statehouse-dashboard, isaacrubinstein.com, and this repo itself. `install.sh` enforces this through an explicit `CONSUMERS` allowlist. A direct one-repository install remains available for an intentional exception.

## Routing
- Tier: none, the kit source, not a data store. The spine is stack-data, Tier 1, the operational source of truth, a sibling clone (`../stack-data`).
- The six phase-zero trigger phrases work here through the deployed `.claude/` kit: "activate all agents", "engage global awareness", "refresh global awareness", "delegate to your orchestrator", "engage the orchestrator", "engage your orchestrator".
- Route research, citation, and lineage tasks to stack-data and its `research-bibliographer` agent.
- Session close is "log learnings"; it runs the retrospective from the kit.
