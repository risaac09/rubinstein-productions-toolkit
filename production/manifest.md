# Cut manifest: versions 1 and 2

> **Status:** operational, version 2 added 2026-10-22. Version 1 is read forever; nothing rewrites a version 1 file.

A cut manifest is the JSON file `cut`, `endcheck`, `trim_review`, `reframe_plan`, `pacing` and `interchange` share. It names the footage, the frame rate and, per clip, the spans that play in order. The code is `rpresolve/cutlist.py`.

## Version 1

`"version": 1`, or no `version` at all.

| Field | Meaning |
|---|---|
| `source_path`, `source_sha256` | the one source file and its SHA-256 |
| `fps` | the cut's frame rate (a number; 29.97 is counted as 29.97) |
| `words` | an mlx_whisper word JSON for the source |
| `approved_text` | what every clip may say |
| `clips[].name`, `clips[].spans[]` | spans `{in, out, end_words?, source_text?, reframe?}`, seconds in the source, `[in, out)` |
| `clips[].reframe` | `{face_x, scale?}` for the whole clip |
| `speech: false` | footage with no spoken words; only `source_path` and `clips` are needed |

Spans must run forward and not overlap.

## Version 2

`"version": 2`. Three changes: several sources, spans in any order, approved text optional.

| Field | Meaning |
|---|---|
| `sources` | non-empty list of `{id, path, sha256, words, approved_text?}`. `id` is unique and `[a-z0-9_-]+`. `sha256` and `words` are required unless `speech` is `false`, which needs only `path` |
| `fps`, `speech`, `squeeze`, `reframe_plan`, `clips` | as in version 1 (`squeeze` is for the whole manifest) |
| `clips[].spans[].source` | the id of the source the span plays from. Optional when there is one source |
| `clips[].spans[].reuse` | `true` allows a span to overlap an earlier span of the same clip and source |

`source_path`, `source_sha256`, `words` and `approved_text` do not appear at the top of a version 2 file; the loader refuses them. Spans play in list order, so they can run back and forth through a source. Two spans of one clip that overlap in one source are refused unless the later one says `reuse`: that is nearly always a slip. A source with no `approved_text` has no claim gate: `endcheck` still checks the end word and the clean cut, and reports the text verdict `none` (ok, and never counted as a pass).

## Reading either version

Tools never read `source_path` directly. `cutlist.sources(m)` returns `[{id, path, sha256, words, approved_text}]` for either version (a version 1 manifest is one source, id `main`), `cutlist.source_for(m, span)` the source a span plays from, and `cutlist.single_source(m, "tool")` the only source or a refusal.

| Tool | Sources |
|---|---|
| `endcheck` | several; rows gain `source` |
| `trim_review` (TSV and MCP) | several; a version 2 TSV gains a last column, `source` (a version 1 TSV is unchanged) |
| `cut` | several; one 16:9 `[auto]` timeline per clip, each item placed from its own source's media-pool clip. The 9:16 and 1:1 versions are not built for a clip over several sources (named `unreframed`; pass `make_9x16` false for 16:9 only) |
| `interchange` (EDL, FCPXML, OTIO) | several; one asset per source, events in span order |
| `pacing` | several; each span reads its own source's words file (`--words` is refused) |
| `trim_review_markers`, `reframe_plan` | one; a manifest with more is refused naming the tool |

A split edit (`audio_lead`, `audio_tail`) at a change of source is refused by `interchange` and `pacing`: the sound would come from two files. Cut on the picture there.

## Converting

```sh
python3 -m rpresolve.manifestup OLD.json --out NEW.json
```

Writes the same cut as version 2: one source `main`, every span, clip and reframe as it was. Never in place; refused when `--out` exists, is the input, or lies inside a git working tree. There is no way back to version 1. From Python: `cutlist.to_v2(m)`, or `cutlist.build_manifest(..., version=2)`.

## Not yet

Reframing from several sources; a different `squeeze` or frame rate per source.
