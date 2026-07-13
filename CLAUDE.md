# CLAUDE.md

## What this repo is
The public toolkit: methodology, prompts, CLI tools, and the phase-zero kit source. Take what's useful is the license posture; nothing private lands here (that went to rp-intranet).

## The one guardrail
This repo is the kit source. Edit `phase-zero/` here, then redeploy with `phase-zero/install.sh --all <parent-dir>`. The deployed `.claude/` copies in the consuming repos are byte-identical output; never edit one in place, the next install overwrites it and the edit dies silently. Consuming repos, current sixteen (updated 2026-07-13): stack-data, second-brain-mirror, second-brain-v2, rp-shared (the products repo, saywhy-app and slop-tools merged in and no longer carry their own copy), royal-metrics, rubinsteinproductions, rp-intranet, alchemy, material-and-meaning-institute, home-scripts, gene-keys-data, three-type-evaluation, claude-memory, statehouse-dashboard, mercer, and this repo itself. Royal-metrics is decision-gated and mercer is time-boxed. Drop both from the list when they archive.

## Routing
- Tier: none, the kit source, not a data store. The spine is stack-data, Tier 1, the operational source of truth, a sibling clone (`../stack-data`).
- The six phase-zero trigger phrases work here through the deployed `.claude/` kit: "activate all agents", "engage global awareness", "refresh global awareness", "delegate to your orchestrator", "engage the orchestrator", "engage your orchestrator".
- Route research, citation, and lineage tasks to stack-data and its `research-bibliographer` agent.
- Session close is "log learnings"; it runs the retrospective from the kit.
