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
**Status: executed July 2026.** All live prose now uses Founder Story, Program Engagement, and Organizational Embedding; the retired names survive only in explicitly historical tables and in the do-not-use lists. The projection and unit-economics tables moved to the private `rp-intranet` repo on 2026-08-23 (`docs/strategy/business-plan-elements.md`), still carrying their pre-repricing figures and still saying so. Vault follow-up: the canonicals behind `session-facilitation-guide.md` and the Digital Liver offering doc need the same renames before the next sync.

Four tier-naming schemes coexist: the canonical Founder Story, Program Engagement, and Organizational Embedding; the retired Mirror, Map, and Territory; and two generations of Gateway and Mid-Tier labels. `prompts/instantiation-prompt.md` lists the retired names as dead and then uses them in its own service table. One sweep through `client-onboarding.md`, `case-study-template.md`, `instantiation-prompt.md`, and `brand-context.md` closes this. Prices diverge in the same files and should be settled in the same sweep.

**Status: partially mooted 2026-08-21.** `instantiation-prompt.md` and `brand-context.md` moved to `rp-intranet` in the same audit that resolved item 1 above; the tier-naming sweep across them is now that repo's problem. `client-onboarding.md` and `case-study-template.md` stay here and still need the sweep.

### 3. Links that work where readers are
**Status: executed July 2026.** In-repo wiki links became relative markdown links, vault-only and home-dir references are marked in place, and `scripts/check-doc-links.sh` now fails CI on any relative link that does not resolve.

The templates and several methodology files use Obsidian `[[wiki links]]` that resolve only in the author's vault, and some of them point at superseded document names. Convert in-repo targets to relative markdown links, and mark vault-only references as such. A CI link checker (the CI workflow now exists to hang it on) keeps this fixed once it is fixed.

### 4. Publish the field guide
**Status: done, live as of 2026-10-01.** `scripts/build-site.py` renders the field guide as the site's front door with the system map at `/map/`, and the deploy workflow builds it on every push to `main`. GitHub Pages is enabled with Source: GitHub Actions, and the site answers at https://risaac09.github.io/rubinstein-productions-toolkit/ (field guide at the root, map at `/map/`; both returned 200 on 2026-10-01).

### 5. Harden the CLI a step further
The smoke test covers the happy path. The next investments, in value order: shellcheck in CI, quoting audit for org names containing `|` or `#` (they currently corrupt the sed replacements), and a guard when `python3` is absent for `rp-draft`'s URL encoding. A single `rp` umbrella command with subcommands would also make discovery easier than six separate binaries, at the cost of breaking existing muscle memory; do it only if new users matter more than current habit.

**Status: partly executed 2026-10-15.** The `python3` guard is done under Amendment 2, item 7. Shellcheck in CI and the quoting audit for org names containing `|` or `#` are still open, and the `rp` umbrella command stays a choice for later.

### 6. Keep the aspirational separate from the operational
The blueprint's revenue projections and the instantiation prompt's automated-expansion sections read as plans, and some are marked speculative while others are not. A one-line status header on each methodology document (operational, draft, aspirational, superseded) would cost an afternoon and prevent the most expensive category of misreading: a stranger adopting a forecast as a fact.

## What not to change

The bash-and-markdown architecture is a feature. It keeps the toolkit runnable in ten years, inspectable by non-programmers, and free of the platform dependencies the methodology itself argues against. The pressure to turn this into a web application will recur; the correct answer remains a better map of the files, which is what the field guide is.

## Amendment 2 (2026-10-15): cross-discipline gap audit

On 2026-10-15 four readers went through the repository at `main` 2e91950: an engineer, an operator, a cinematographer and designer, and a producer and distributor. Each asked what they could not do from here. This section records what they found. Nothing below is fixed yet; each item names the fix and leaves it for its own pull request. The numbers belong to this amendment and restart at 1. Items 1 to 6 of the review above keep their own numbers.

### Engineering

1. **CI runs 13 of 34 test modules.** `.github/workflows/ci.yml` skips 21 modules and 643 tests: lutlab, rpstills, deliver*, detect, ingest, reframe*, relink, sync, trim*, vertcut_flags, captions*, measure and rpresolve_api. They pass locally on `/usr/bin/python3`. `test_resolve_survey` raises `SkipTest` at import, which aborts any batched `unittest` run that lists it. Fix: move that skip to a per-test decorator, then add the 21 modules to CI.

   **Status: executed 2026-10-15.** `test_resolve_survey` now skips the 24 tests that need `compression.zstd` one by one and runs the other 39 on any Python. CI has a new `production-unit-tests` job that installs `requirements.txt` on Python 3.9 and runs all 34 modules, 1019 tests, and fails on any skip that is not the zstd one, because without the packages 85 tests skip themselves and three modules fail to import. A second job, `resolve-survey-3-14`, runs the survey tests in full on 3.14. The existing stdlib-only job stays as it was, so a stdlib regression still fails there. Checked locally on `/usr/bin/python3` 3.9.6 in a fresh virtual environment from the pinned wheels: 1019 run, 24 skipped, none failed. The runner side is not proven until this pull request's CI run: the pinned versions all publish Linux wheels for 3.9 and ubuntu-latest carries ffmpeg, but I could not run a Linux runner from here, and `setup-python` supplying 3.14 is the one assumption I did not test.
2. **No dependency manifest.** There is no `pyproject.toml` or `requirements.txt`. numpy, scipy, Pillow, OpenCV and imagehash come from the Python 3.9 that ships with Xcode, while `resolve_survey` needs 3.14. Fix: one file that states each tool's interpreter and packages.

   **Status: executed 2026-10-15.** `requirements.txt` at the root names each tool's interpreter and its packages in a header, and pins numpy, scipy, Pillow, OpenCV and imagehash at the versions the suite ran against on 3.9. `resolve_survey` is listed on 3.14. The file is a pip requirements file, so CI installs from it directly instead of keeping a second list.
3. **`tools/video-editor/` is 835 MB of vendored code.** It is third-party (Browser Use, MIT), tracked in git and listed in `.gitignore` at the same time, with lockfiles, a test mp4 and a PNG inside. Fix: decide between a subtree or a submodule pointing at upstream and plain untracking, then do it and keep the clone small.
4. **The README omits half the repo.** "What's Inside" does not mention lutlab, rpstills, vertcut, `tools/`, `dsh/` or `seed-bed/`, which breaks rule 4 of `public-kit/README-SHAPE.md`. The repo also never promoted the public-kit CONTRIBUTING and SECURITY templates to its own root. Fix: complete the README table and promote both templates.
5. **Skills live in two places.** `prompts/skills/branded-deck-build.md` and `.agents/skills/branded-deck-build/SKILL.md` are byte-identical copies, as are the two branded-doc-build files. outreach-email-manager and project-management-coordinator exist as both `.skill` bundles and `.md` files. Fix: name one source per skill and generate or delete the other.
6. **Worktrees and branches have piled up.** 15 worktrees under `.claude/worktrees` and 23 branches remain against the "merged or deleted before wrap" rule. Fix: prune the merged ones and add a count check to session close.
7. **`cli/rp-draft` is unsafe and not generic.** Line 78 passes `$SUBJECT` into `python -c` inside single quotes, so a quote in the subject breaks the call or injects code. It falls back to no encoding without saying so when `python3` is absent, and it hard-codes one person's signature in a tool the README calls generic. Item 5 above is still open. Fix: pass the subject as an argument or on stdin, fail loudly without `python3`, and read the signature from config.

   **Status: executed 2026-10-15.** `rp-draft` now passes the subject, the address and the body to Python as arguments through a `urlencode` helper in `cli/_rp-common.sh`, so nothing typed is ever run as code. The old version also left the body unencoded, so an `&` or `#` in it cut the message off; that is fixed with the rest. Without `python3` the tool stops before it asks for anything and says why. The signature is read from `~/.config/rp/signature` (or `$RP_SIGNATURE_FILE`); with no file the draft has none and says so, and no name is hard-coded. The draft log line goes into the prospect file through `awk` and the environment instead of a `sed` expression, so `/`, `&` and `\` in a subject stay literal. `scripts/cli-smoke-test.sh` gained eight cases: a subject with a quote, `&`, `/` and `%`, the encoded body, the signature file, a missing signature file, and a PATH with no `python3`. Against the old script, eight of them fail.
8. **No versions.** There are no tags, releases or CHANGELOG, and the Resolve spec carries its own inline "Version 1.7". Kit consumers cannot pin a version. Fix: tag releases, keep a CHANGELOG, and point the spec's version at the repo's.

### Operations

9. **Status headers are still unwritten.** Item 6 above is unexecuted: only `source-tracking-protocol.md` has a status header. `methodology-blueprint.md` line 140 still says no client has completed the camera-shipping journey and names Q2 2026. Fix: add the one-line header to every methodology document and update or cut the dated claim.
10. **The measurement framework describes a data layer that does not exist.** Section 4 of `measurement-framework.md` specifies an Airtable implementation, while the stack's data spine is JSON files in stack-data. Fix: rewrite section 4 against the real store or mark it as an unbuilt option.
11. **No instrument ships.** The framework names seven Likert items, NPS at five points and consistency statements, but the repo has no form, scoring sheet or CSV schema, so an evaluator cannot run it from here. Fix: publish the items verbatim with a CSV schema and a worked scoring example.
12. **Two pipelines coexist.** The `rp-*` Markdown CLI and the stack-data contacts and projects files both track prospects, and the README has called the CLI "a default, not a settled call" since August. Fix: settle it in one decision and state which tool is the record.
13. **The blueprint's April gaps have no owner.** Camera-shipping logistics (insurance, timelines, backup gear) are listed as still missing and no document has followed. Fix: assign an owner and a target document, or strike the claim.

### Cinematography and design

14. **No capture standard.** `production/filming-guide.md` is a one-day shoot plan dated March 31 that points at a vault footage path, yet the field guide presents it as the filming guide. Frame rate and log profile per camera, audio capture, sync and slate practice, and card naming are written down nowhere. Fix: write a general capture standard and rename or relocate the shoot plan as an example.
15. **No client-side shooting brief.** For the camera-shipping model, what the client films, how, and what the footage must satisfy does not exist anywhere in `production/`. Fix: write the brief as a client-readable page.
16. **Three colour pipelines, no canonical statement.** `resolve-template-spec.md` puts conversion LUTs on node 1, `resolve-config.json` sets RCM v2, and lutlab's `rcm` works in DaVinci Intermediate in and out. The spec's Open Questions (which V-Log LUT, the Open Gate crop) remain open. Fix: one table per camera that names the pipeline in force, then close or date the open questions.
17. **No design tokens for deliverable form.** The Resolve spec names Marrow, Bone and amber without values. There is no lower-third or caption style standard, and vertcut's PNG captions and Resolve's captions diverge. The six lutlab looks have no look book or reference frames. Fix: publish token values, one caption standard, and a reference frame per look.

### Producer and distributor

18. **Consent tiers have no evidence behind them.** The tiers are defined, but no appearance or talent release, location release, or music and font licence register exists, and the case-study template records a tier with nothing that evidences it. Fix: add release templates and a licence register, and make the case-study template link to them.
19. **"Everything is returned" has no spec.** No return package defines formats, media, manifest or checksums, and the repo has no archive naming or folder standard. Working, final and scraps appear only in the negative-archive ritual. Fix: write the return-package spec and the folder and naming standard.
20. **The production flow has no approval or delivery gate.** Client final approval exists only as a safeguard in the measurement framework. There is no delivery manifest, no platform spec per destination, and no credits or AI-disclosure standard for produced work. Fix: add the gate and the manifest to the production flow and write the platform and credits standards.
21. **Footage copyright is unstated.** Nothing says who owns footage the client shoots on RP-shipped cameras. The repo licenses code and methodology but not deliverables. Fix: state ownership and licence for footage and deliverables in one short document, reviewed by counsel before it ships.

### Cross-cutting

22. **No index of truth ownership.** `CLAUDE.md` calls this repo "the public projection, not a data store", so it cannot be the single source of truth. It lacks a one-page index of which repo or note owns each domain: pricing, pipeline, decisions, kit source and the methodology canonicals, with what is exported here. Fix: write that index and link it from the README and the field guide.
