# Reference library

**Status:** operational.

A private shelf of references taken from your own finished work, grade targets first. A reference is a set of numbers (`targets.json`), never a deliverable and never a borrowed still: only work you made and own goes in. No stock-frame sites, public-domain, Creative Commons or commercial stills.

## Layout

The library lives outside every git repository (a repository is public or may become so). Its root holds a marker file, `REFERENCE-LIBRARY-ROOT.md`, which says what the library is. One folder per reference:

```
<library>/
  REFERENCE-LIBRARY-ROOT.md
  stills/    <reference-slug>/  ref.yaml, targets.json, the still
  idents/    <reference-slug>/  ref.yaml, the ident
  mixes/     <reference-slug>/  ref.yaml, the mix
  stings/    <reference-slug>/  ref.yaml, the sting
  cuts/      <reference-slug>/  ref.yaml, the cut
```

Pixels and audio stay in this tree and out of git. Derived numbers, such as a `targets.json`, may be copied into a private repository. Only `stills/` has tooling today; the other shelves are folder convention so every kind of reference has one home.

What the code reads: only the marker file (see "The refusal"). It never opens `ref.yaml`. Every `ref.yaml` field below is convention: write them so a reference can be understood and its permission checked later, but expect no tool to validate them. Do not use a field name as if a command honoured it.

`ref.yaml` fields:

| Field | Meaning |
|---|---|
| `what` | what the item is, in one line (convention) |
| `reference_for` | what it is a reference for, such as `skin tone in warm interiors` or `loudness of a short sting` (convention) |
| `source` | the project and deliverable it came from |
| `licence` | `own work` |
| `allowed_uses` | what the targets may be used for, such as `grade targets for matching` |
| `date` | when the still was taken |
| `targets` | path of `targets.json`, relative to the reference folder (stills only) |

## Making a `targets.json`

```
python3 production/resolve_workflow.py perceive hero.png --out /path/outside/repo/pkt --targets
```

`--targets` writes `targets.json` beside `packet.json`. The packet holds frames, so it stays out of the library; copy only `targets.json` in. The file carries the `cine.targets` header (schema, version 1, transfer, generated_by, generated_at) and numbers only:

- luma percentiles p1, p10, p50, p90 and p99 in IRE, and display contrast in stops;
- the Lab palette (k-means, fixed seed);
- mean chroma and hue for three fixed luma bands: shadows under 33 IRE, mids 33 to 66, highlights 66 and over, and the hue shift of shadows against highlights in degrees (-180 to 180);
- the near-neutral cast (median a\*, b\*);
- the largest face's skin Lab, when one is found, read on the cheeks, with `skin_warnings` naming anything that makes it untrustworthy (a clipped cheek, a face without landmarks).

Two runs on one still give the same file apart from `generated_at`. A file tagged PQ, HLG, linear or log gets a `transfer_note` and no bands.

## The refusal

`rpresolve.refs.is_reference(path)` is true when any folder above the path holds the marker. The render plan's destination checks, `deliver-manifest` and `perceive --out` all refuse such a path, so a reference is never delivered over. Other tools that write or ship files reuse the same function.
