# Review notes ledger

> **Status:** draft, 2026-11-21.

A client's review comments arrive as timeline markers (Resolve's Blackmagic Cloud Presentations and its frame-accurate review tools put them there). This tool turns each round of markers into numbered notes in a ledger, closes them against the cut that fixed them, and prints the change list. Offline, standard library only, no Resolve connection.

The input is the marker TSV of [the cue, marker and sync contract](cue-marker-sync.md#the-marker-tsv). Cut names follow [the lock handoff](lock-handoff.md) (`<project-slug>_v12`). The ledger names the client's notes, so it is refused inside any git working tree.

## The three commands

Run from `production/`:

```
python3 -m rpresolve.review_notes add --markers MARKERS.tsv --cut CUT --ledger LEDGER.tsv
python3 -m rpresolve.review_notes resolve --ledger LEDGER.tsv --note N-001 --in CUT
python3 -m rpresolve.review_notes resolve --ledger LEDGER.tsv --note N-001 --wontfix --reason TEXT
python3 -m rpresolve.review_notes report --ledger LEDGER.tsv [--rounds-allowed N]
```

- `add` appends one round. The first `add` is round 1, each later one adds 1. If every marker in the file is already in the ledger for that cut (the same file added twice), `add` refuses and writes nothing. If only some are, it adds the new ones as the next round, and prints how many were already present with their existing note ids: a client may repeat a note in a later round. If every marker already sits in the ledger under a different cut name, it adds as usual and prints a warning, in case the cut name has a typo. A marker file with a header and no rows adds nothing and does not count as a round.
- `resolve` closes one open note as `done` in a cut, or as `wontfix` with a reason. A note that is already closed is refused.
- `report` prints a Markdown change list by round, open notes first, with open, done and wontfix counts per round. With `--rounds-allowed` it adds a plain line, and says so when the rounds are used up. It never fails on that.

`add` and `resolve` take an exclusive lock on the ledger's folder for the whole read, change and write, so parallel runs lose nothing and ids stay unique. After writing, they re-read the ledger and report success only if the rows are there. A busy lock that does not clear in 10 seconds is a named error, exit 1.

Bad input is a named error and exit 1. `--help` and unknown flags print usage and exit 2.

## The ledger

A TSV with a header, one row per note. Ids are never reused or renumbered; a duplicate id (a hand edit) makes every command refuse until it is fixed.

| Column | Meaning |
|---|---|
| `note_id` | `N-001` upward |
| `round` | 1 for the first `add`, then 2, 3 |
| `cut` | the cut the note was made against |
| `timeline_frame` | the marker's `frame` |
| `timecode` | the marker's `timecode`, copied. Its shape is always checked (`HH:MM:SS:FF` or `HH:MM:SS;FF`, minutes and seconds under 60, frames under the nominal rate). It is checked against `frame` only at an integer rate; at a fractional rate (24000/1001, 30000/1001) the frame is the authority and the arithmetic is skipped |
| `text` | the marker `name`, then ` | ` and its `note` when there is one |
| `status` | `open`, `done` or `wontfix` |
| `resolved_in` | the cut that addressed a `done` note, else empty |
| `reason` | why a `wontfix` note was declined, else empty |

The TSV holds the client's text raw: a note starting with `=`, `+`, `-` or `@` is stored as written, so a spreadsheet may run it as a formula. The Markdown report escapes such text with a backslash.

The marker schema carries no author, so the ledger has no author column.

Duplicate detection compares text with surrounding whitespace stripped and inner runs of whitespace collapsed, and keeps case exact; the stored text is exactly as given.

The lock uses `flock`, which a network share may not support; the tool then reports the ledger as busy. Keep the ledger on a local disk.

Every change rewrites the ledger through a temporary file and an atomic rename.
