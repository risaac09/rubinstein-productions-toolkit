# changelog.d

One file per pull request. Do not edit `CHANGELOG.md` under `## Unreleased`; CI fails the pull request if you do.

```bash
scripts/changelog-fragments new my-change "What changed, in one paragraph. (#123)"
```

That writes `changelog.d/<date>-my-change.md`. Rules:

- Plain text, one entry, no blank lines inside it, no heading, no leading `- `.
- Name the amendment item or pull request when there is one.
- A change that needs no entry (a typo, a re-run) adds a one-line fragment: `skip: reason`.
- A pull request that touches only `phase-zero/`, `public-kit/` or `.claude/` needs no fragment.

At tag time the maintainer runs `scripts/changelog-fragments compile`. It moves every fragment, newest first, to the top of `## Unreleased` in `CHANGELOG.md` and deletes the files. That commit is the one place `CHANGELOG.md` changes under Unreleased.

`scripts/changelog-fragments lint` checks every fragment in the tree. `scripts/changelog-fragments pr origin/main` runs the pull-request rules locally.
