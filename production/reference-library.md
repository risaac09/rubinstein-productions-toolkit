# Reference library

A private shelf of grade targets taken from your own finished work. A reference is a set of numbers (`targets.json`), never a deliverable and never a borrowed still: only work you made and own goes in. No stock-frame sites, public-domain, Creative Commons or commercial stills.

## Layout

The library lives outside every git repository (a repository is public or may become so). Its root holds a marker file, `REFERENCE-LIBRARY-ROOT.md`, which says what the library is. One folder per reference:

```
<library>/
  REFERENCE-LIBRARY-ROOT.md
  <reference-slug>/
    ref.yaml
    targets.json
```

`ref.yaml` fields:

| Field | Meaning |
|---|---|
| `source` | the project and deliverable the still came from |
| `licence` | `own work` |
| `allowed_uses` | what the targets may be used for, such as `grade targets for matching` |
| `date` | when the still was taken |
| `targets` | path of `targets.json`, relative to the reference folder |

## Making a `targets.json`

```
python3 production/resolve_workflow.py perceive hero.png --out /path/outside/repo/pkt --targets
```

`--targets` writes `targets.json` beside `packet.json`. The packet holds frames, so it stays out of the library; copy only `targets.json` in. The file carries the `cine.targets` header (schema, version 1, transfer, generated_by, generated_at) and numbers only:

- luma percentiles p1, p10, p50, p90 and p99 in IRE, and display contrast in stops;
- the Lab palette (k-means, fixed seed);
- mean chroma and hue for three fixed luma bands: shadows under 33 IRE, mids 33 to 66, highlights 66 and over, and the hue shift of shadows against highlights in degrees (-180 to 180);
- the near-neutral cast (median a\*, b\*);
- the largest face's skin Lab, when one is found.

Two runs on one still give the same file apart from `generated_at`. A file tagged PQ, HLG, linear or log gets a `transfer_note` and no bands.

## The refusal

`rpresolve.refs.is_reference(path)` is true when any folder above the path holds the marker. The render plan's destination checks, `deliver-manifest` and `perceive --out` all refuse such a path, so a reference is never delivered over. Other tools that write or ship files reuse the same function.
