# Roadmap: An Architect's Review

A structural assessment of the toolkit as of July 2026, written the way a departing architect leaves notes for whoever maintains the system next. The methodology corpus is strong and unusually honest. The weaknesses are the ordinary ones of a repository that grew out of one person's working files: duplicated sources of truth, vocabulary drift, and links that assume the author's machine.

## What this pass changed

- **CLI portability.** The write-path tools used macOS-only forms (`sed -i ''`, `date -v`, `open`), so the pipeline failed on Linux despite the README's no-dependencies claim. Shared helpers in `cli/_rp-common.sh` now pick the right form at runtime, every tool answers `-h`/`--help`, and `scripts/cli-smoke-test.sh` runs the full prospect and grant lifecycle in CI on every pull request.
- **Role-based entry.** `docs/field-guide/` gives writers, producers, and evaluators a reading order, a first working session, and warnings about the corpus's known inconsistencies, so the repository is navigable by someone who did not build it.

## What to fix next, in order

### 1. One source of truth per document
**Status: executed July 2026.** The `grants/` copies are now pointer stubs, the codebook is declared canonical for coding, the framework's section 3 is a pointer, and `enif.md` became `measurement-framework.md` (the sync mapping in `scripts/sync-methodology.sh` was updated to match). One follow-up lives outside this repo: the vault canonical `RP Measurement Framework.md` needs the same section-3 cut, or the next sync restores the duplication.

`grants/theory-of-change.md` and `grants/evaluation-framework.md` are older snapshots of the same-named files in `methodology/`, and they still carry retired framework names. Either delete the `grants/` copies and link to `methodology/`, or reduce them to stub files that point at the canonical version. Duplicated prose drifts; it already has.

**Status: mooted 2026-08-21.** The repo-scope audit found `grants/` held one real document (`fiscal-sponsorship-strategy.md`, live business strategy) behind two pointer stubs. The real document moved to `rp-intranet`; the stubs were deleted outright rather than fixed, since `methodology/theory-of-change.md` and `methodology/evaluation-framework.md` are already directly canonical and need no pointer.

The same decision is owed between `methodology/enif.md` and `methodology/nomadic-indicators-codebook.md`. The framework document claims to have absorbed the codebook, yet the codebook still ships separately and is the richer coding reference. Recommendation: declare the codebook canonical for coding, cut the framework's summary of it to a pointer, and rename `enif.md` to match the framework's actual current name.

### 2. One vocabulary for the tiers
**Status: executed July 2026.** All live prose now uses Founder Story, Program Engagement, and Organizational Embedding; the retired names survive only in explicitly historical tables and in the do-not-use lists. The projection and unit-economics tables in the blueprint kept their old dollar figures and now say so. Vault follow-up: the canonicals behind `session-facilitation-guide.md` and `digital-liver-offering.md` need the same renames before the next sync.

Four tier-naming schemes coexist: the canonical Founder Story, Program Engagement, and Organizational Embedding; the retired Mirror, Map, and Territory; and two generations of Gateway and Mid-Tier labels. `prompts/instantiation-prompt.md` lists the retired names as dead and then uses them in its own service table. One sweep through `client-onboarding.md`, `case-study-template.md`, `instantiation-prompt.md`, and `brand-context.md` closes this. Prices diverge in the same files and should be settled in the same sweep.

**Status: partially mooted 2026-08-21.** `instantiation-prompt.md` and `brand-context.md` moved to `rp-intranet` in the same audit that resolved item 1 above; the tier-naming sweep across them is now that repo's problem. `client-onboarding.md` and `case-study-template.md` stay here and still need the sweep.

### 3. Links that work where readers are
**Status: executed July 2026.** In-repo wiki links became relative markdown links, vault-only and home-dir references are marked in place, and `scripts/check-doc-links.sh` now fails CI on any relative link that does not resolve.

The templates and several methodology files use Obsidian `[[wiki links]]` that resolve only in the author's vault, and some of them point at superseded document names. Convert in-repo targets to relative markdown links, and mark vault-only references as such. A CI link checker (the CI workflow now exists to hang it on) keeps this fixed once it is fixed.

### 4. Publish the field guide
**Status: executed July 2026, one switch remaining.** `scripts/build-site.py` renders the field guide as the site's front door with the system map at `/map/`, and the deploy workflow builds it on every push to `main`. The site has never actually deployed because GitHub Pages is not enabled on the repository and the workflow token cannot enable it; flip it once in Settings, Pages, Source: GitHub Actions.

The GitHub Pages site currently serves only the agentic-architecture map. The field guide is markdown and could join it, either as rendered pages beside the map or by pointing the site's navigation back at the repository. Decide whether Pages is a system diagram or the toolkit's front door; right now it is a diagram with a front door's URL.

### 5. Harden the CLI a step further
The smoke test covers the happy path. The next investments, in value order: shellcheck in CI, quoting audit for org names containing `|` or `#` (they currently corrupt the sed replacements), and a guard when `python3` is absent for `rp-draft`'s URL encoding. A single `rp` umbrella command with subcommands would also make discovery easier than six separate binaries, at the cost of breaking existing muscle memory; do it only if new users matter more than current habit.

### 6. Keep the aspirational separate from the operational
The blueprint's revenue projections and the instantiation prompt's automated-expansion sections read as plans, and some are marked speculative while others are not. A one-line status header on each methodology document (operational, draft, aspirational, superseded) would cost an afternoon and prevent the most expensive category of misreading: a stranger adopting a forecast as a fact.

## What not to change

The bash-and-markdown architecture is a feature. It keeps the toolkit runnable in ten years, inspectable by non-programmers, and free of the platform dependencies the methodology itself argues against. The pressure to turn this into a web application will recur; the correct answer remains a better map of the files, which is what the field guide is.
