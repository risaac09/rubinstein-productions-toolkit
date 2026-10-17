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

At tag time the maintainer runs `scripts/changelog-fragments compile`. It moves every fragment, newest first, to the top of `## Unreleased` in `CHANGELOG.md`, adds the roadmap statuses below to `docs/ROADMAP.md`, and deletes the files. That commit is the one place `CHANGELOG.md` changes under Unreleased and the one place a status line is added to the roadmap.

## Roadmap status fragments

A pull request that finishes, moves or blocks a `docs/ROADMAP.md` item does not edit that file. It adds a second fragment under `changelog.d/roadmap/`:

```bash
scripts/changelog-fragments status A3.30 pillow-move '**Status: executed 2026-10-17, [#180](https://github.com/risaac09/rubinstein-productions-toolkit/pull/180).** What was done.'
```

- `A3.30` is Amendment 3, item 30. The item must exist in `docs/ROADMAP.md` when `compile` runs, or it stops.
- Line 1 of the file is `item: A3.30`. The rest is one paragraph of ready Markdown that carries its own lead (`**Status: ...**`, `**Update ...:**` or `**Open: ...**`).
- `compile` adds each paragraph after the item's last line, indented like the existing ones, oldest fragment first, so two fragments for one item keep their order.
- CI fails a pull request that adds a `**Status`, `**Update` or `**Open` line to `docs/ROADMAP.md` directly.
- The roadmap fragment is in addition to the changelog fragment, not instead of it.

`scripts/changelog-fragments lint` checks every fragment in the tree. `scripts/changelog-fragments pr origin/main` runs the pull-request rules locally.
