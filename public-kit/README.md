# Public Kit

A second, small deployment kit alongside `phase-zero/` — for public-repo
hygiene instead of AI-agent session infrastructure. Independent allowlist,
independent install flag, independent purpose. A repo can take `phase-zero/`
without this kit (`rp-intranet` — private, no public-hygiene need), this kit
without `phase-zero/` (`risaac09` — a profile repo, explicitly not a
phase-zero consumer), both, or neither.

## What it does

Deploys reference copies of this kit's files into `<repo>/.claude/public-kit/`
— never repo root. Nothing here overwrites a repo's actual `LICENSE`,
`README.md`, `CONTRIBUTING.md`, or `SECURITY.md` automatically; those are
templates a human reviews and promotes to root deliberately. The deployed
copies are the reference a maintainer diffs their repo's real files against.

## Files

- `VOICE-RULES.md` — the canonical public-facing voice rules, reconciled
  from the versions that were hand-duplicated (and drifting) across
  `rubinsteinproductions/CLAUDE.md` and `stack-data/CLAUDE.md`.
- `README-SHAPE.md` — not a template, a checklist: what a public repo's
  README should cover and in roughly what order.
- `LICENSE-MIT.template` — default for code-shaped repos.
- `LICENSE-CC-BY-SA-4.0.template` — default for methodology-shaped repos
  (protocols, frameworks, practice-writing). See that file for why
  ShareAlike, not permissive, is the default for this category.
- `CONTRIBUTING.md.template`, `SECURITY.md.template` — solo-maintainer-sized
  defaults; fill in the bracketed sections per repo.

## Per-repo-type default

`install.sh --public` picks the LICENSE template per repo automatically:
methodology-shaped repos get the CC BY-SA default, everything else gets
MIT. See `public_consumer_license_type()` in `../phase-zero/install.sh` for
the current mapping — it's a small `case` statement, not a config file,
so a new consumer's type is one line to add.

## Install

    # one repo
    ../phase-zero/install.sh --public ../some-repo

    # every listed PUBLIC_CONSUMERS repo under the parent directory
    ../phase-zero/install.sh --public --all ..

    # verify, no writes
    ../phase-zero/install.sh --public --check --all ..

Shares the installer script with `phase-zero/` (the `--public` flag
switches which kit and which allowlist it acts on) rather than
duplicating the install/check/drift-detection logic in a second script.
The two kits' file sets, allowlists, and target directories
(`.claude/*.md` + `.claude/hooks/` for phase-zero, `.claude/public-kit/`
here) never overlap.

## Consumers

The `PUBLIC_CONSUMERS` allowlist in `../phase-zero/install.sh` is scoped to
repos that are actually public: `alchemy`, `statehouse-dashboard`,
`gene-keys-data`, `rubinsteinproductions`, `risaac09`,
`three-type-evaluation` (its public paper side only — the rest of that repo
stays private, untouched either way since this kit only ever writes under
`.claude/`), and this repo itself. Fully independent of the `phase-zero`
`CONSUMERS` list — being on one implies nothing about the other.
