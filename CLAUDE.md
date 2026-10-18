# CLAUDE.md

## What this repo is
The public toolkit: a facilitation and measurement methodology, production tools that work on anyone's footage (the Resolve MCP and library, lutlab, rpstills, vertcut), a few generic agent skills, their tests and CI, and an export of two deployment kits (`phase-zero/`, `public-kit/`). Public means public.

## What belongs here
Only what a stranger with their own practice could reuse as is. Rubinstein Productions' own operations, client lifecycle, service delivery, pricing, brand values, agent setup and internal records live in the private `rp-intranet` repo (decision of 2026-10-17, "The toolkit is the generic kit"). A file that names a client, a price, a private note or repo's contents, the author's machine, or the author as the actor of a procedure does not land here.

## The one guardrail
`phase-zero/` and `public-kit/` are an export. Never edit them in this repo. Their source is `kit/phase-zero/` and `kit/public-kit/` in the private stack-data repo; `scripts/sd-kit-export` there privacy-scans, mirrors both directories byte for byte, and redeploys this repo's `.claude/`. Title the commit and its PR with the line it prints, `Export kit from stack-data <sha>`. Any other commit touching these two directories breaks the rule. Deploy commands and the consumer roster are in `phase-zero/README.md` and `phase-zero/install.sh`.

## Working here
- One fragment per pull request in `changelog.d/` (`scripts/changelog-fragments new SLUG "TEXT"`).
- CI runs the link check, the methodology status check, the instrument test, the fragment and burst-gate tests, shellcheck, and the production unit tests; run the relevant ones before pushing.
- Public voice rules: `public-kit/VOICE-RULES.md`.
