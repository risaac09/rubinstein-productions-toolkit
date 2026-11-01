# clerkwork

Status: draft. Not yet runnable, PR 1 of 3.

Be a decision-maker, not an implementer.

What it will be:

- A private repo for the admin of a solo practice, started from `clerkwork/skeleton/`.
- Three flat JSON ledgers: contacts, projects and financials, written only through scripts in `bin/` that a validator gates.
- `bin/today`, which prints the decisions you owe (each a yes, an edit or a no) and the calls the agent already made.
- An agent does the filing, drafting and chasing. You send, pay, publish and decide.
- Claude Code first, bash and jq only. No build step.

What exists in this pull request: the skeleton with its schemas and empty ledgers, `add-contact`, `add-project`, `add-invoice`, `today` and `validate`, a fake practice in `examples/` and two shell tests in `tests/`. The agent's instructions, hooks and `init` come in PR 2. The front door (README, field guide, drawings, secret-scan CI step) comes in PR 3.

To try the scripts now, copy `skeleton/` somewhere outside this repo and run `bin/validate` and `bin/today` there.

The decision behind it is stack-data decision 2026-10-22, "The practice admin kit folds into the toolkit as clerkwork". It lives in a private repo, so it is cited here and not linked.

clerkwork imports nothing from `methodology/`, `production/`, `templates/` or `prompts/skills/`.
