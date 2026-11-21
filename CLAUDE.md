# CLAUDE.md

## What this repo is
The public toolkit: a facilitation and measurement methodology, production tools that work on anyone's footage (the Resolve MCP and library, lutlab, rpstills, vertcut), a few generic agent skills, their tests and CI, and an export of two deployment kits (`phase-zero/`, `public-kit/`). Public means public.

## What belongs here
Only what a stranger with their own practice could reuse as is. Rubinstein Productions' own operations, client lifecycle, service delivery, pricing, brand values, agent setup and internal records live in the private `rp-intranet` repo (decision of 2026-10-17, "The toolkit is the generic kit"). A file that names a client, a price, a private note or repo's contents, the author's machine, or the author as the actor of a procedure does not land here.

## The one guardrail
`phase-zero/` and `public-kit/` are an export. Never edit them in this repo. Their source is `kit/phase-zero/` and `kit/public-kit/` in the private stack-data repo; `scripts/sd-kit-export` there privacy-scans, mirrors both directories byte for byte, and redeploys this repo's `.claude/`. Title the commit and its PR with the line it prints, `Export kit from stack-data <sha>`. Any other commit touching these two directories breaks the rule. Deploy commands and the consumer roster are in `phase-zero/README.md` and `phase-zero/install.sh`.

## Who merges
The path a pull request touches decides who merges it.
- **Public edge: the maintainer merges.** `README.md`, `docs/`, the `LICENSE` files, `phase-zero/`, `public-kit/`, `.github/`, `install.sh`, and any path not named below.
- **Plumbing: an agent may merge on green checks.** Code and tests under `production/`, including the spec files there that the docs-match-code test pins.
- A pull request that touches both waits for the maintainer.
- `phase-zero/` and `public-kit/` change only through export pull requests (see the guardrail above).

## Real run before merge, risky paths
A green CI run did not catch #498, where `offload.sh safe --lite` printed SAFE TO FORMAT for a card with unknown originals. A pull request that changes a tool that can delete, format, copy off a card, or publish gets one real run first.
- **Risky paths:** `production/offload.sh`; `production/rpresolve/card.py`; the deliver tools (`production/rpresolve/deliver.py`, `delivercheck.py`, `peakfix.py`, `cmd_deliver_*.py`); anything named backup or offsite; any script or command that deletes, overwrites or formats.
- **Who runs it:** a fresh agent that did not write the change, on the branch's head, before the merge.
- **What it runs:** the changed tool on a synthetic card from `production/card-fixture.sh <empty-temp-dir>` (add `--image` on macOS for a mountable image), or on a scratch copy it made itself. It runs the failing side too: the unknown extension, the missing copy, the case the change was written for. The verdict must fail closed there.
- **Never the only copy.** Nothing is run against a real card, a real archive, a real remote or the only copy of anything. If the check needs real hardware, the pull request says so and waits for the maintainer.
- **In the pull request body:** each command, its exit code (`cmd; echo "exit $?"`, never through a pipe), and the verdict lines it printed, both the pass and the refusal.
- **Merge:** the path rules in "Who merges" stay as they are. A plumbing pull request without this block is not merged.

## Working here
- One fragment per pull request in `changelog.d/` (`scripts/changelog-fragments new SLUG "TEXT"`).
- CI runs the link check, the methodology status check, the instrument test, the fragment and burst-gate tests, shellcheck, and the production unit tests; run the relevant ones before pushing.
- A new `resolve_workflow.py` command is one module, `production/rpresolve/cmd_<name>.py`, plus its own test file; it edits no shared file (`production/rpresolve/cli.py` says how).
- Public voice rules: `public-kit/VOICE-RULES.md`.
