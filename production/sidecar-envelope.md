# Sidecar envelope (schema v2)

**Status:** operational.

Every JSON sidecar a toolkit tool writes (`perceive`, `audio-report`, `story-packet`, `overlay-check`) carries one header, so one validator checks all of them and a later step can join them on one key. Code: `production/rpresolve/sidecar.py` (`header`, `validate`, `card_key`, `source_sha256`). Stdlib only.

## Header

Top level, with nothing beside it except the lane key.

| Field | Type | Rule |
|---|---|---|
| `schema` | `{name, version}` | `name` is `<lane>.<tool>`, e.g. `cine.perceive`, `sound.audio-report`, `story.story-packet`, `fusion.overlay-check`. `version` is an integer, bumped on any breaking change |
| `clip_id` | string or null | `<card folder>/<stem>`. The card folder is the nearest folder above the file named `<CAM>-<id6>` (`^[A-Za-z0-9]+-[0-9a-f]{6}$`, the name `offload.sh` gives a card copy); otherwise the file's own folder (renders, flat handoff folders). `offload.sh` keeps the card tree, so `S5II-a1b2c3/DCIM/100_PANA/P1000110.MOV` gives `S5II-a1b2c3/P1000110`, and the Archive and Work copies give the same id. An id two clips in one shoot share is flagged (`shared_ids`) and never joined on. Null when the sidecar covers more than one clip; its records then carry a `clip_id` each |
| `source` | `{path, sha256}` | `path` is absolute (a tool resolves a relative one against its working folder). `sha256` is copied from the card's `<CAM>-<id6>.SHA256SUMS` when one covers the file, otherwise null. No tool re-hashes a source for the header |
| `transfer` | `{tag, read_as}` or null | `tag` is ffprobe's `color_transfer` (`untagged` when empty); `read_as` is the curve the tool assumed, e.g. `BT.1886 display: Rec.709 primaries, 2.4 gamma, D65`. Null for a tool that reads no picture (sound) |
| `primaries` | `{tag, read_as}` or null | The same for `color_primaries` |
| `generated_by` | `{tool, version}` | `tool` is the module, e.g. `rpresolve.perceive`; `version` is the toolkit's `git rev-parse --short HEAD` when run from a checkout, else null |
| `generated_at` | string | ISO 8601 UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `<lane>` | object | Exactly one of `cine`, `sound`, `story`, `fusion`, matching the schema's lane, holding everything the tool measured |

A `cine.*` sidecar must have non-null `transfer` and `primaries`: picture tools name their curve.

## Lane payload conventions

- Time is seconds from the source's start: `t_s` for a moment, `start_s` and `end_s` for a span, three decimals. A frame number may sit beside it only with `fps`.
- Paths inside the payload are relative to the sidecar's folder (images, slices), so a folder can move.
- A picture tool that emits a ratio (contrast, lighting) needs a non-null `transfer` whose `read_as` matches the file's tag family; otherwise it writes null for the ratio and a `transfer_note` saying why.
- Units go in the key name where they are not obvious: `_s`, `_ire`, `_stops`, `_pct`, `_lufs`, `_db`.
- A stem split from a mix keeps the source's `clip_id` and names `stem` inside `sound`; a report on a range adds `start_s` and `end_s` there.
- Sidecars hold frames, words or measurements of media, so write them outside any git tree.

## Example

```json
{
  "schema": {"name": "cine.perceive", "version": 2},
  "clip_id": "S5II-a1b2c3/P1000110",
  "source": {"path": "/Volumes/Work/Active/2026-x/03-Renders/P1000110_v01.mov", "sha256": null},
  "transfer": {"tag": "bt709", "read_as": "BT.1886 display: Rec.709 primaries, 2.4 gamma, D65"},
  "primaries": {"tag": "bt709", "read_as": "Rec.709"},
  "generated_by": {"tool": "rpresolve.perceive", "version": "1a07979"},
  "generated_at": "2026-10-20T19:40:00Z",
  "cine": {"t_s": 12.5, "files": {"sheet": "sheet.png"}, "luma": {"ire": {"p50": 38.0}}, "faces": []}
}
```

A render's `clip_id` names the source clip it was cut from when there is one; a timeline render covering many clips has `clip_id` null and the time in timeline seconds.

## Check

```python
from rpresolve import sidecar
problems = sidecar.validate(json.load(open("report.json")))  # [] when sound
```

Each tool moves onto the envelope in its own change; until then its output may predate it.
