# Starting a project from a card

**Status:** operational. Code: `production/rpresolve/project.py`, `cmd_project.py`. Example config: `production/project.example.json`.

`project start` takes a camera card and runs the footage-first chain as far as it can without a person, then says what it needs. Each step is an existing command; this one only orders them and keeps the state.

```
project start CARD [--project SLUG] [--cam CAM] [--apply]
project answer SLUG [--squeeze F [--clip ID]...] [--resolved] [--apply]
project status SLUG
```

`CARD` is a card volume (copied with `offload.sh copy`, which needs `--cam`) or a card folder that already has its record (not copied again). Every step that writes is a dry run until `--apply`: a dry run runs the read-only commands and prints what the rest would do. Nothing is deleted.

## The nine steps

| | Step | Runs | Stops when |
|---|---|---|---|
| 1 | copy and verify | `offload.sh copy`, then the record | a volume has no `--cam` or no landing root; a dry run stops after naming the copy |
| 2 | identify | `card set --project SLUG`, `detect --json` | `detect` fails. Clips it marks review or corrupt are listed in `open` |
| 3 | open questions | `card load`, then `sheets --only` for the clips in question | any clip has no squeeze answer (none recorded, no BRAW tag). Answer with `project answer` |
| 4 | index | the configured indexer, `index --card CARD` | the indexer fails; skipped with a note when none is configured |
| 5 | contact sheets | `sheets CARD -o <sheets root>/<slug>/sheets` | sheets fail; skipped when no sheets root |
| 6 | Resolve colour | prints the `set_color_management` call with preset `managed-dwg` | always, until a person creates the empty project (the API cannot) and runs it; then `project answer SLUG --resolved --apply` |
| 7 to 9 | shot list to deliver | nothing; `project status` prints the next command from [edit-methods.md](edit-methods.md) | by hand |

`project start` again resumes: finished steps are not repeated, the questions are asked afresh from the record.

## State and config

State is `<work root>/<slug>/project.json`: the steps with their status, the card folders by path and `open`. Paths come from the JSON file named by `RP_PIPELINE_CONFIG` (`landing_root`, `work_root`, `indexer`, `offsite_remote`, `sheets_root`); each can be given on the command line instead (`--landing`, `--work-root`, `--indexer`, `--remote`, `--sheets-root`). With no config, give every path on the command line; a missing indexer or sheets root skips its step with a note. `indexer` is a command, or `{"argv": [...], "env": {...}}`; it is run as `<indexer> index --card CARD`.

## Status

`project status SLUG` lists the nine steps with their state, `open` questions, the next command for steps 7 to 9, and whether the card may be formatted: yes only when an `offsite.txt` names it (`card check` standard S4).
