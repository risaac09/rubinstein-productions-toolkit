# Cue, marker and sync contract (VP-146)

> **Status:** aspirational, proposed 2026-10-20, a spec with no code yet. Held until the toolkit freeze lifts on 2026-11-01. The numbers in [the sync window](#the-sync-window) are house working defaults, not figures from a standard.

Three lanes put marks on one timeline and two of them judge audio against picture. This page is the one set of rules they share: how markers are named so no lane overwrites another's, which way an offset is signed, how far a cue may sit from its picture event, and which files a check reads.

## The marker TSV

`markers.tsv` has a header row and one row per marker, tab separated, UTF-8, no quoting. A tab or newline inside a field is not allowed.

| Column | Rule |
|---|---|
| `frame` | Integer timeline frame, counted from zero at the timeline start |
| `timecode` | `HH:MM:SS:FF` at the timeline rate, a copy of `frame` for people |
| `duration_frames` | Integer, 1 or more |
| `name` | Starts with a lane prefix and a colon, then free text: `story:hook ends`, `snd:sting hit`, `fx:lockup` |
| `colour` | The Resolve marker colour name |
| `note` | Free text, may be empty |
| `fps` | The timeline rate as a fraction, for example `24000/1001`, the same on every row |

`frame` and `timecode` must agree at the rate in `fps`. Rows are sorted by `frame`, then `name`.

### Lane prefixes

| Prefix | Lane |
|---|---|
| `story:` | editing and narrative |
| `snd:` | sound |
| `fx:` | Fusion and motion |

A lane writes, edits and deletes only the markers carrying its own prefix. It reads the others and never changes them. A marker with no prefix belongs to a person: no tool touches it. A tool that writes markers to a timeline adds only its own prefix; one that removes its markers matches on the prefix, never on colour. A lane keeps its own colour so a person can tell them apart on the timeline; which colour each lane takes is unset: a lane proposes one and Isaac signs it.

## The sync sign

One rule for every lane, the sign used by ITU-R BT.1359:

```
offset_ms = picture_ms - audio_ms
```

- **Positive means sound leads**: the audio event comes before its picture event.
- **Negative means sound lags**: the audio comes after the picture.
- Zero is on the frame.

Frames convert at the timeline rate: one frame at 24000/1001 is 41.7 ms. A report gives the signed offset in milliseconds and in frames, and says which way it points in words ("sound leads by 41.5 ms"), so a reader never has to remember the sign.

This contract takes the BT.1359 sign and treats early sound as the stricter side, which is why the lead limit is the smaller number. The standard itself is not yet read at source (see Open), so the sign is its documented convention as the lanes agreed it, and the numbers below are house defaults. A fixture or report written with the opposite sign (audio minus picture) is rewritten to match.

## The sync window

Working defaults, signed by Isaac on 2026-10-20 as house defaults until BT.1359 and EBU R37 are read at source:

| Check | Default |
|---|---|
| Sound leading picture | fail above 40 ms |
| Sound lagging picture | fail above 60 ms |
| Onset detection tolerance | 1 ms |

They are house numbers. They are not quoted from either standard, and a report that prints them says "house working default". A one-frame lead at 24000/1001 (41.7 ms) fails the 40 ms default; that is the intended result, not a rounding bug.

### `intended_offset_ms`

When a person chooses a lead or lag on purpose (a hit placed one frame early for feel), the cue file records it as `intended_offset_ms` together with who chose it and when. The check then judges the deviation, `offset_ms - intended_offset_ms`, against the window. A cue with no `intended_offset_ms` is judged against zero. The field is never inferred and never set by a tool on its own.

### The 3-frame amendment

A cue placed early or late on purpose lands off its picture keyframe. A hit cue therefore matches the nearest picture keyframe within 3 frames, and the offset is measured against that keyframe's time. Two conditions go with it:

1. A cue with no keyframe inside the window fails. It is never matched to a farther one.
2. The window must stay under half the smallest gap between keyframes in the file, so one cue cannot match two keyframes. A file whose keyframes sit 6 frames apart or closer fails the check instead of matching ambiguously.

## The files a check reads

Checks read files, never typed values.

- `cues.json`, written by the sound lane: each cue carries `cue_id`, `kind` (`hit`, `riser`, `tail`), `measured_from`, `onset_ms`, `wav_start_frame`, `fps`, and `intended_offset_ms` when set. `onset_ms` is measured from the audio by onset detection, never typed.
- `lockup.json`, written by the Fusion lane: `lockup_frame` and `keyframes` (frames), `fps`.
- Both are at timeline zero and the same `fps`; a pair with different rates is refused.

Picture timing wins. When the two disagree beyond the window, sound is adapted, not picture.

## Fixtures the check must pass

- A hit 2 frames late and a hit 2 frames early each report an offset with the sign this page gives (early positive, late negative), and the early one is flagged against the lead limit, the late one against the lag limit, each only when it exceeds it.
- A cue with `intended_offset_ms` of one frame early passes when its measured offset matches it, and fails when it drifts past the window from it.
- A cue with no keyframe within 3 frames fails; a keyframe pair 6 frames apart or closer fails the file.
- A typed offset in place of a measured onset is refused.

## Open

- BT.1359 and EBU R37 are not yet read at source. When they are, the defaults are replaced by cited thresholds and this page says so.
- Marker colour per lane is unset.
