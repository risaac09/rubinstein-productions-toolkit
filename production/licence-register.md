# Licence register format (VP-147)

> **Status:** proposed 2026-10-20, a format with no code yet. Held until the toolkit freeze lifts on 2026-11-01. It extends the register in [consent evidence](consent-evidence.md#licence-register), which covers music and fonts, to every asset a render can contain.

One register per engagement, one row per asset in the delivered work. The point of the format is that a tool can read it: a comp, a mix or a look that cites an asset whose licence is blocked or unknown is flagged before render.

## File

`licence-register.tsv`: a header row, then one row per asset; tab separated, UTF-8, no quoting. It sits with the project, outside any git working tree when it names client material.

## Columns

| Column | Rule |
|---|---|
| `asset` | What it is, specific enough to find: track title and artist, font family and style, template name, preset or model name |
| `kind` | One of `music`, `sfx`, `font`, `motion-template`, `plugin`, `dctl`, `lut`, `model`, `ai-generated`, `footage`, `image`, `other` |
| `version` | Release, version string or commit SHA. Required for `plugin` and `model`, and for any asset taken from a repository: a floating branch name is not a version |
| `source` | Where it was obtained |
| `licence` | The licence's name, with its SPDX identifier where it has one, or "own work" |
| `scope_of_use` | What it may be used for, where and for how long |
| `attribution_required` | The credit the licence asks for, in its words, or `none`. Blank is invalid |
| `commercial_ok` | `yes`, `no` or `unknown`. Blank is invalid |
| `proof` | An id or path for the receipt, licence file, name-table read or file hash that backs the row |
| `checked` | `YYYY-MM-DD`, the day a person compared the scope of use against where the work is going |

## `commercial_ok`

| Value | Meaning | Rule |
|---|---|---|
| `yes` | Use in paid or commercial work is allowed as the row describes it | Needs `proof` and `checked` filled in |
| `no` | Not allowed in paid or commercial work | Blocks a render |
| `unknown` | Nobody has read the licence for this use | Blocks a render, the same as `no` |

Rows are never defaulted to `yes`. A licence that cannot be read is `unknown`.

### Blocked on paid work

These start as `no` and stay `no` until the row's `proof` points to a different, paid or permissive licence that covers the use:

- Creative Commons BY-NC (non-commercial)
- the retired Sampling+ licence
- the madmom models
- Depth Anything at Base size and up
- pyiqa and Q-Align

## Checks

Before a render or a delivery, a tool reads the register and the asset list the work cites (a comp's inventory, a mix's cue sheet, a look's LUTs). It flags:

- an asset the work cites that has no row;
- a row with `commercial_ok` of `no` or `unknown`;
- a row with `yes` but no `proof` or `checked`;
- a row of `plugin` or `model` kind, or one taken from a repository, with no `version`.

A row for an asset the work does not cite is not an error.

## Attribution feeds the manifest

The `attribution_required` text of every row that is not `none` is copied into the [delivery manifest](delivery-manifest.md#licences-used) at the final-approval gate. The manifest shows the asset, the licence and the credit, and nothing else from the register.

## First entries

The first rows are the two typefaces and the sting of the Rubinstein Productions intro, read from the installed font files' own name tables on 2026-10-20. Hashes are shortened here.

| asset | kind | version | source | licence | scope_of_use | attribution_required | commercial_ok | proof | checked |
|---|---|---|---|---|---|---|---|---|---|
| EB Garamond Italic | font | 1.001 | The EB Garamond Project (github.com/octaviopardo/EBGaramond12) | SIL Open Font License 1.1 | Rendering into video, commercial use included; redistributing the font file needs the OFL text beside it | none | yes | Name table: "Copyright 2017 The EB Garamond Project Authors", licence string OFL 1.1; file sha256 4e51b654...c596fe | 2026-10-20 |
| Inter Medium | font | 3.019 | The Inter Project (github.com/rsms/inter) | SIL Open Font License 1.1 | Same as above | none | yes | Name table: "Copyright 2020 The Inter Project Authors", licence string OFL 1.1; file sha256 10d48331...8c1ad7dc9 | 2026-10-20 |
| Intro sting (long cut and bumper) | sfx | | Synthesized from oscillators and noise; no samples, library or model | own work | All uses | none | yes | Synthesis script and the sums of the rendered files, kept with the master | 2026-10-20 |

## Open

- Entries for a given engagement live with that engagement, not in this repository.
