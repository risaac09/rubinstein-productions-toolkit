# Repo atlas: interconnection map and thin-down verdict

A ready-to-run prompt for a strong orchestration model (Opus 4.8 at high
effort, or better) with local clones of every candidate repo one level down
from a single parent directory. Fill the two parameters, then paste
everything below the line as the task.

Parameters:

- `PARENT_DIR`: the directory holding the clones.
- `TARGET_KEEP`: how many repos the operator wants left standing. A range
  is fine.

---

You are mapping a personal GitHub estate to answer one question: which repos
does the operator actually use and need, and what should happen to the rest.
The clones live one level down from `PARENT_DIR`. Target size after
thinning: `TARGET_KEEP`. The operator suspects duplication. Your deliverable
is a map plus a verdict per repo, with evidence for every claim.

## Ground rules

1. Repo identity comes from `git remote get-url origin`, never from the
   folder name. Directory names drift from repo names, and a basename audit
   fabricates missing and duplicate findings.
2. A named gap beats a fabricated edge. If you cannot verify a connection,
   write "unverified" and say what would verify it.
3. Read-only. No pushes, no file changes, no branch creation, no installs.
4. Stay out of raw personal material: vault contents, session logs, lived
   records, anything a repo's own docs mark private. Structural facts only:
   sizes, dates, paths, link counts.
5. Every claim carries its evidence: a file path, a git ref, or a registry
   field.

## Method

**Phase 1, the declared map.** If the estate has a repo registry (in this
stack: `stack-data/data/repos.json`), read it first. It declares name,
visibility, description, archived flag, and last push per repo. The registry
is the declared state, the clones are the actual state, and your map is the
diff between them: repos declared but not cloned, cloned but not declared,
described one way and built another.

**Phase 2, the observed map.** For each clone, cheap facts first:

- Purpose: `README.md` and `CLAUDE.md`, first 40 lines each.
- Pulse: the last five commits that are not bot or sync commits.
- Deploy surface: Pages config, `.github/workflows`, published URLs.
- Size, language, and whether the repo says another repo absorbed it.

**Phase 3, the edges.** For each repo, grep across all the other clones for
its name to catch inbound references, then hunt these connection types:

- Sibling-clone reads: `../repo-name` paths in code, docs, or scripts.
- Shared-asset sync: sync scripts pulling from a shared source repo.
- Data consumption: one repo reading another's built output, such as a
  published `dist/index.json`.
- Shared backends: a common API worker and the routes each repo calls.
- Kit or template deployment: one repo's files copied into the others.
- Absorption trails: a repo whose function another repo claims to carry
  now. Check both sides' docs before believing either.
- Registry or docs mentions with nothing behind them. Mark those edges dead.

**Phase 4, the verdicts.** Classify every repo:

- `spine`: others read from it; breaking it breaks them.
- `active-surface`: deployed or in weekly use, with real commits in the
  last 60 days.
- `feeder`: an input pipeline to a spine or a surface.
- `absorbed`: another repo carries its function now. Name the successor.
- `dormant`: no real commits in 90 days and nothing reads it, but it holds
  unique content worth keeping somewhere.
- `dead`: superseded or empty. Nothing reads it, nothing unique to save.

**Phase 5, the thin stack.** Name the `TARGET_KEEP` repos to keep, one line
each on why. For every other repo, give one of: merge into a named repo
(what moves and where it lands), archive (what to extract first, if
anything), or delete. Order the moves by switching cost, cheapest first, and
for each move name every inbound link it breaks: deploy URLs, registry
entries, workflow references, sibling-clone paths.

## Delegation

The reading is cheap-model work. Fan phases 2 and 3 out to subagents on the
cheapest capable tier, a batch of repos per agent, returning structured
facts only. Hold phase 4 and phase 5 at the top level: the keep-or-kill
judgment is what the operator is paying the orchestration tier for.

## Output contract

1. Edge table: from, to, edge type, evidence path, live or dead.
2. Per-repo verdict: one paragraph. Classification, evidence, confidence.
3. The thin stack: the keep list, one line of why per repo.
4. Migration order: numbered steps, each with its breakage list.
5. Named gaps: what you could not verify, and how the operator can.

Do not soften verdicts to be polite. A wrong keep costs maintenance
attention forever. A wrong archive costs one unarchive to undo.
