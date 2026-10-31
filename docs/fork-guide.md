# Fork guide

**Status:** draft. Two things this guide depends on are not done yet, and the parts that rest on them are marked **Pending** below.

For someone who forks this repository to run their own practice on it. You are not the maintainer, and nothing here needs the maintainer's private repositories. The maintainer's own list of repos that receive the kits is private; this guide shows how to write yours.

Forks are welcome. Pull requests are not reviewed or merged (see [`CONTRIBUTING.md`](../CONTRIBUTING.md)), so a fork is a copy that you own from the first commit.

## What to keep and what to drop

Keep what you will use and delete the rest in your first commit. A smaller fork is easier to trust.

| Part | Keep it if | Notes |
|---|---|---|
| `methodology/`, `templates/`, `docs/field-guide/` | you facilitate or evaluate | Content under CC BY-SA 4.0. Your adaptation has to stay under the same licence. |
| `production/`, `tools/` | you edit footage | The Resolve tools need DaVinci Resolve and, mostly, a Mac (see [below](#what-assumes-macos-or-resolve)). `tools/vertcut` and `lutlab` need neither Resolve nor a Mac for most commands. |
| `prompts/` | you use Claude Code skills | Copy single skills into your own `.claude/skills/`. |
| `phase-zero/`, `public-kit/` | you run AI agents across several repos | Optional. Skip them and delete both directories, plus the `README.md` section that describes them. |
| `scripts/`, `.github/workflows/` | always | The doc-link checker, the status check, the site build and the changelog fragments are what keep the docs honest. Keep their tests. |
| `changelog.d/`, `CHANGELOG.md` | you want release notes | Delete the maintainer's history from `CHANGELOG.md` and keep the fragment mechanism. |
| `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md` | never as they stand | They name the maintainer and the maintainer's contact routes. Rewrite them for you. |

Every document under `methodology/`, `docs/` and the top level of `production/` carries a `**Status:**` line, and `scripts/check-methodology-status.sh` fails CI when one is missing. The check skips `production/skills/` and the tool subfolders, and it does not cover the other top-level folders, so a document you add there needs the line by habit. Keep that habit for your own documents. A forecast that reads like a fact is the expensive mistake.

Before you publish, search your fork for the maintainer's name, domain and e-mail address, and replace each with your own.

## Replacing the maintainer's deployment list

`phase-zero/install.sh` installs the phase-zero kit into a repository's `.claude/`, and `public-kit/` into `.claude/public-kit/` with `--public`. The maintainer's script decides which repositories count as deployment targets at run time, from a private registry file, `data/repos.json`, in a private repository outside this one. That file is not here and you cannot see it. The script holds no list of names to edit. This is how it behaves, read from the script:

- **One repo, no list needed.** `./phase-zero/install.sh <repo-dir>` and `./phase-zero/install.sh --check <repo-dir>` copy or verify the kit in that directory and never read a registry. `--public` works the same way. If you have a few repos, run it once per repo and stop here.
- **Several repos need your own registry.** `--list`, `--all <parent-dir>` and `--check --all <parent-dir>` build the roster from `data/repos.json`. Without that file, or without `jq`, they exit with code 2 instead of running on an empty list.
- **Where it looks.** The environment variable `SD_ROOT` names a git clone that holds `data/repos.json` (the default is `~/stack-data`, the maintainer's directory name; set `SD_ROOT` to your own). It reads `origin/main` first, then local `main`, then the working tree, and warns on either fallback.
- **The shape it expects.** A JSON array (or an object with a `repos` array) of entries with these fields:
  - `name`, and `cloneName` when the directory under your parent folder has a different name.
  - `phaseZeroConsumer: true` on each repo that should receive the phase-zero kit.
  - For the public kit, `visibility: "PUBLIC"`, `isArchived: false`, `isFork: false`, and `gone` left false or out. Forks are skipped on purpose: the script treats them as other people's code.

  ```json
  [
    {"name": "my-site", "visibility": "PUBLIC", "isArchived": false, "isFork": false, "phaseZeroConsumer": true},
    {"name": "my-notes", "visibility": "PRIVATE", "isArchived": false, "isFork": false, "phaseZeroConsumer": true}
  ]
  ```

  Keep that file in a repository you control, private if your repo list is private, and point `SD_ROOT` at its clone.
- **Two places to edit by hand, for the public kit.** Both hold the maintainer's repo names, so a repo of yours with the same name would be skipped (`--all` modes) or get the wrong licence template (any mode). `PUBLIC_KIT_EXCLUDE` names one public repo of the maintainer's that is left out; set it to your own exclusions or to an empty string. `public_consumer_license_type` picks a licence template by repo name and has one maintainer-specific case; change it to your methodology-shaped repos, or delete the case so every repo defaults to MIT.
- **Source of truth.** The header of `phase-zero/` says never to edit the copy here, because the maintainer exports it from a private source. In your fork there is no export, so the copy is the source. Edit it directly, and expect your changes to be lost if you ever merge the maintainer's later export over them.

**Pending: release mechanics.** A `KIT_VERSION` file and a stated way to tell which export your fork carries do not exist yet. Until they do, record the upstream commit you forked from (in your own README or changelog), and compare against it by hand when you want newer kit files. This section will change when the release mechanics land.

## Choosing the licence template

The repository is split on purpose, and the split is the part to decide on:

- `LICENSE` is MIT and covers code: `scripts/`, `production/`, `tools/`, `phase-zero/` and `public-kit/`.
- `LICENSE-CONTENT` is CC BY-SA 4.0 and covers everything else: methodology, docs, templates and prompts. Share-alike means an adaptation stays as open as the original.

You may not remove the copyright and licence notice from material you received under either licence. Add your own copyright line beside the maintainer's; do not replace it.

For your own new work, `public-kit/` carries two templates: `LICENSE-MIT.template` for code and `LICENSE-CC-BY-SA-4.0.template` for protocols, frameworks and practice writing. Pick by what the repository holds. A repo that is mostly code takes MIT. A repo that is mostly method or writing takes CC BY-SA if you want adaptations to stay open, and a permissive licence if you do not (that is your call, for your original material). A repo with both takes the same two-file split this one uses, with a scope paragraph at the top of each file. The kit never overwrites a repo's real `LICENSE`; it writes a `LICENSE.recommended` that you review and promote by hand.

## Keep the deny and ask rules

`phase-zero/settings.json` carries a permission block. Today the `deny` list blocks three Desktop Commander file-write tools, and the `ask` list is empty. The installer unions these into a target's existing `.claude/settings.json` and never drops a rule you already have. A rule you add yourself is kept, with one exception: the five `ask` rules named in `RETIRED_ASK` (a message-send tool and some Desktop Commander and osascript tools) are removed on every install, even if you added them. Delete that list in your copy if you want to keep any of them.

`install.sh --check` fails if any kit rule is missing from a consumer, so deleting one shows up as drift. Do not remove a deny rule to make a tool work; allow the one action you need in your own settings and leave the clamp. The reason is the usual one for an agent with write access: a rule that asks first costs a second, and a rule that was never there costs a file. The maintainer moved some outward-facing actions (messages to other people) from a settings prompt to an explicit ask in conversation. If you copy that choice, say so in your own instructions file, because the settings no longer enforce it.

## What assumes macOS or Resolve

- **Resolve.** `production/resolve_mcp.py`, `resolve_workflow.py` and the `rpresolve` writers load DaVinci Resolve's own scripting module at run time. It is not on pip, and Resolve has to be running with scripting enabled. The offline commands (`detect`, `survey`, `measure`, `endcheck`, `selects`, `deliver_check` and others) never connect to Resolve.
- **Mac paths and interpreters.** Defaults assume Homebrew on Apple silicon: `/opt/homebrew/bin/ffmpeg`, `/opt/homebrew/bin/python3.14`, Xcode's Python 3.9 for the MCP server, and cache and log folders under `~/Library`. [`requirements.txt`](../requirements.txt) lists every environment variable that overrides a default.
- **Apple-only features.** `vertcut transcribe` uses `mlx-whisper`, which needs Apple silicon. `rpstills` takes faces from macOS Vision when `swiftc` is present. The Blackmagic RAW helper looks for the SDK under `/Applications`.
- **What runs elsewhere.** CI runs the offline half on Linux. The iPad lane has its own page, [iPad lane](ipad-lane.md), with what is verified and what is not.
- **The agent kits.** The hooks in `phase-zero/` are bash and `jq`, so they run on Linux too.

## Before you publish a fork

1. Replace the owner's name, domain and contact routes in `README.md`, `CONTRIBUTING.md`, `SECURITY.md` and `CLAUDE.md`.
2. Run `scripts/check-doc-links.sh`, `scripts/check-methodology-status.sh` and the test scripts under `scripts/`.
3. Add your own copyright line to `LICENSE` and `LICENSE-CONTENT`.
4. Decide whether to keep the git history.

**Pending: history scrub.** The maintainer has not yet cleaned this repository's git history, and a fork copies all of it. If you want a history that is already scrubbed, wait for that to land and fork afterwards. If you fork now, expect to rebase or re-fork later, and check the history yourself for anything you would not want under your name before you make your fork public. A fork you start from a clean tree with no history (copy the files, `git init`) avoids the question.
