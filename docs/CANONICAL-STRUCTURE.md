# Canonical structure — Rubinstein Productions toolkit

This repo (`~/rubinstein-productions-toolkit/`) and the Obsidian Second Brain vault are **separate stacks** with separate canonical paths. They must not bleed into each other. This document defines the directory boundary and the guards that enforce it.

## Legitimate top-level structure

| Path | What lives here |
|---|---|
| `research/` | Research artifacts. Note: `research/arena/` was consolidated into the vault on 2026-06-11 (commit 5ca1834) and must not be recreated here; the canonical Arena copies live in the vault at `02 Research/15 Synthesis/07 Arena`, and the Canonical Paths registry lists `research/arena` as a deprecated path. |
| `cli/` | CLI tools |
| `Rubinstein Productions/` | Toolkit-side RP material |
| `scripts/` | Bash automation and hook sources |
| `docs/` | Internal documentation (this file lives here) |
| `methodology/` | Methodology-as-code |
| `templates/` | Document and skill templates |
| `prompts/` | Reusable prompts |
| `production/` | Production assets |
| `evals/` | Skill evals |
| `architecture/` | System-map artifact |
| `phase-zero/` | AI-agent session infrastructure kit source; deployed via `phase-zero/install.sh` |
| `public-kit/` | Public-repo hygiene kit source; deployed via `phase-zero/install.sh --public` |
| `dist/` | Build output |
| `context-provenance/` | Self-contained, unrelated tool (AI-attribution/provenance tracking); being spun out to its own repo, `context-provenance` — see README.md's "What's Not Here" |
| `seed-bed/` | Seed-bed working area |
| `_archive/` | Quarantine for misrouted writes and obsolete material |
| Repo root files | `README.md`, `LICENSE`, `LICENSE-CONTENT`, working notes |

## Forbidden — reserved for the vault

These directory names exist in the vault and **must never** appear inside the toolkit. The pattern is two digits + space + a reserved word.

```
02 Practice/
02 Research/
03 Projects/
00 Canonical/
00 System/
00 Meta/
01 Writing/
04 Career/
05 Archive/
06 Resources/
07 Capture/
```

Formal regex:

```
^[0-9]{2} (Practice|Research|Projects|Canonical|System|Meta|Writing|Career|Archive|Resources|Capture)( |$)
```

Exception: `_archive/` is allowed (intentionally created on 2026-05-21 to hold the Arena V2 misroute).

## Why this matters

On 2026-05-20, the `arena-v2-encounters` scheduled task wrote vault content into the toolkit at `02 Practice/03 Foundations Research/15 Synthesis/07 Arena/`. The misroute was silent — no error, no alarm. The full incident is in the vault at `00 System/Maintenance/2026-05-21-arena-v2-blocked-run.md`.

The guards below exist so this class of bug fails loud and on a fixed schedule.

## Guards

### 1. Drift detector

`scripts/check-vault-mirror-drift.sh`

Pure bash. Scans the toolkit (root + 2 levels deep), flags any directory matching the forbidden pattern, honors `_archive/` as an exception. Prints findings. Exits 0 clean, exit 1 on drift.

Run manually:

```bash
~/rubinstein-productions-toolkit/scripts/check-vault-mirror-drift.sh
```

### 2. Pre-commit hook

`.git/hooks/pre-commit` (source: `scripts/hooks/pre-commit-vault-mirror-check.sh`)

Chains the system-wide secret scanner with the drift detector. Both must pass for the commit to proceed.

Install on a clean checkout:

```bash
cd ~/rubinstein-productions-toolkit
cp scripts/hooks/pre-commit-vault-mirror-check.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

### 3. Daily detector

`~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist`

launchd job. Runs the detector daily at 04:30 local with `--emit-vault-report`. On drift, writes a dated note to the vault at `00 System/Maintenance/YYYY-MM-DD-toolkit-mirror-drift.md`. Logs to `~/Library/Logs/toolkit-mirror-check.log`.

This is the part that catches misroutes from non-git automation (the way the Arena V2 misroute happened).

Manage:

```bash
launchctl load   ~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist
launchctl unload ~/Library/LaunchAgents/com.rubinsteinproductions.toolkit-mirror-check.plist
launchctl list | grep toolkit-mirror-check
```

## When the detector fires

Two outcomes only:

1. **The content belongs in the vault** — move it to the matching vault path and fix whichever automation wrote it.
2. **The content is obsolete** — move it under `_archive/` in the toolkit.

Do not silently delete the detector's exception list. Each exception removes a guard.
