# The card record

**Status:** operational (schema 2, `card` command). Code: `production/rpresolve/card.py`, `cmd_card.py`; the clip id rule is in `sidecar.py`.

A copied camera card is a folder with its proofs and answers beside it. One loader, `rpresolve.card.load(path)`, takes any path in or beside the copy and returns the record, the proofs and what is still open. Consumers call it; none parses the files itself.

```
<parent>/<folder>/              the card copy
<parent>/<folder>.card.json     the record
<parent>/<folder>.SHA256SUMS    one sha256 per file (offload.sh)
<parent>/<folder>.offsite.txt   written by `offload.sh safe` once an offsite copy verifies
<parent>/<folder>.squeeze.json  legacy squeeze answers, read as answers.squeeze
```

A SHA256SUMS is read as it is and never rewritten: a leading `./` on a path (repeated too) and a `*` binary-mode mark before it are ignored, so older files that `shasum -c` accepts verify here too.

A folder with a `.card.json` or `.SHA256SUMS` beside it is a card whatever it is called. Old folders keep their names and the record carries the id; `sidecar.card_key` names their clips `<cam>-<first 6 of card_id>/<stem>`.

## Schema 2

`offload.sh` writes schema 1: seven keys, no `schema` field. Schema 2 keeps those seven unchanged and adds seven more.

| Key | Type | Rule |
|---|---|---|
| `card_id`, `listing_hash`, `volume_uuid`, `cam`, `files`, `bytes`, `offloaded_utc` | as offload.sh writes them | `card_id` is 12 hex, `listing_hash` 64 hex, `volume_uuid` a string or null |
| `schema` | `{name, version}` | exactly `{"name": "card.record", "version": 2}` |
| `origin` | string | `offload` or `retrofit`. A retrofit has `volume_uuid` null and its id from the listing hash (`card_id` is the first 12 hex of `listing_hash`) |
| `shoot` | `{first_utc, last_utc, days, cameras}` | file write times, UTC days; `cameras` is filled from exif by the index, empty from `init` |
| `project` | slug or null | `a-z`, `0-9`, hyphens |
| `answers` | object | only what a person said. `answers.squeeze` is `{default, clips, by, at}`: `default` covers clips `clips` does not name, `clips` maps a clip id to a factor (1 is spherical). A BRAW file's own tag still answers for itself and is never copied in |
| `clips` | list of `{clip_id, relpath, proxy}` | originals only. An in-card camera proxy (a video under any folder named `Proxy`, any case, anywhere in the card, whose stem equals that of an original outside every `Proxy` folder) is the `proxy` of that original, never a clip of its own; when several originals share the stem, the one beside the `Proxy` folder wins. A proxy with no such original stays out of `clips`, and `card check` and `card show` report it as a note, which fails nothing. `clip_id` is `<cam>-<id6>/<stem>`, unique in the card |
| `history` | list of `{at, event, ...}` | `card mv` appends `{event: "moved", from, to}` |

An optional `note` string is allowed (two early retrofits carry one). Any other top-level key fails validation. A reader of a record whose `schema.version` is above 2 reports it and judges nothing else.

Schema 1 loads as is: its clips are derived from SHA256SUMS (or the folder), its answers are empty or the legacy `squeeze.json` folded in (file names turned into clip ids), and `card set` or `card mv` upgrade it to schema 2 in place.

## The standards

`card check` answers "is this card to standard":

| | Standard | Fails when |
|---|---|---|
| S1 | a record beside the folder | none, unreadable, or `validate` finds a problem |
| S2 | a SHA256SUMS that verifies | missing, a file differs, is missing or is not listed, or the record's `files` or `bytes` disagree (`--quick` skips the hashing) |
| S3 | a squeeze answer for every clip | a clip has no recorded answer and is not a BRAW file with its own tag |
| S4 | an `offsite.txt` | missing, or it does not name this card, a remote and a time |
| S5 | the folder named `<CAM>-<id6>` | the name differs from the record's `cam` and `card_id` |

```
PASS  CAMA-012345  S1 S2 S3 S4 S5
FAIL  sub/OLD-NAME  S3 S4 S5: S3 squeeze CAMA-012345/AAA0001 (+1 more); S4 no offsite.txt ...
```

One line per card, exit 1 on any failure. Give it one card or a root; a root checks every card under it.

## Commands

```
card show PATH
card check PATH... [--quick] [--depth N]
card init --retrofit FOLDER --cam CAM [--apply]
card set PATH [--squeeze F [--clip ID|NAME]...] [--project SLUG] [--apply]
card mv PATH DEST_PARENT [--quick] [--apply]
```

Every writing subcommand is a dry run until `--apply` and refuses to write inside any git tree. None deletes. `init` creates a SHA256SUMS and a record and never overwrites either: an existing SHA256SUMS must verify and is kept. `set` and `mv` replace `card.json` atomically, keeping every key it had; the other sidecars are never rewritten. `mv` renames the folder and its sidecars on one volume (a move across volumes is a copy and a delete, which this command does not do), verifies by hash, undoes the move if it does not verify, then appends to `history`.
