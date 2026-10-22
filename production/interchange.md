# Interchange: a cut manifest as EDL, FCPXML or OTIO

> **Status:** draft 2026-10-22. `python3 -m rpresolve.interchange` writes one clip of a cut manifest as a CMX 3600 EDL, an FCPXML 1.10 file or an OpenTimelineIO JSON file. The writer and its tests are offline. Nothing here has been imported into Resolve yet: the acceptance step below is still open, and until it is done these files are untested outside the toolkit.

`cut` builds a manifest's timelines inside a running copy of desktop Resolve. That is the only way a manifest reaches an edit today. An interchange file carries the same cut to a Resolve that is not running, to Resolve for iPad, or to another editor. The source is still the manifest, read through `cutlist.load_manifest`. The writer changes nothing in the manifest and adds no field to it.

## Usage

Run from `production/`:

```
python3 -m rpresolve.interchange MANIFEST --format edl|fcpxml|otio --out FILE
    [--clip NAME] [--fps 30000/1001] [--media-root DIR] [--title T]
```

- `--clip` names the manifest clip to write. One clip becomes one timeline, its spans laid end to end, as `cut` lays them. A manifest with one clip needs no `--clip`.
- `--fps` sets the rate. Without it the manifest's `fps` is used, and failing that the source's. 23.976, 29.97 and 59.94 are read as 24000/1001, 30000/1001 and 60000/1001, and so is a probed rate such as 2997/100 (what a file encoded with `-r 29.97` reports).
- `--media-root` writes the media URL as `DIR/<file name>`, for media that sits elsewhere on the machine that imports the file (an iPad's Files folder, a drive mounted under another name). The source is still read at its manifest path.
- `--title` names the timeline. The default is the clip's name.

`--out` is refused inside any git working tree, because the file names client media. It is also refused when it names the manifest or the source (a hard link to either included), so a slip of the path cannot overwrite a camera original. The file is written to a temporary file beside `--out` that then replaces it, so a hard link to `--out` keeps its old content. The command prints one summary line, with any warning on stderr, and exits 2 on a refusal.

From Python, `interchange.build(manifest, fmt, ...)` returns the text and a report dict (rate, drop-frame, source timecode, spans, frames, split edits, warnings). `add_arguments(parser)` and `run(args)` are there for a parent CLI.

## How time is counted

- A span covers source frames `[frame(in), frame(out))`, rounding half up (`cutlist.frame`). When the manifest's `fps` is a number that matches the rate, frames are counted at that number (29.97), exactly as `cut` counts them, so the file and the `[auto]` timeline `cut` builds hold the same frames. Counting at the exact 30000/1001 instead would move about one cut in twelve by a frame late in an hour-long source. A string `fps`, `--fps` alone or a probed rate counts at the exact rate. Everything after that is whole frames. Record times are sums of frame counts, so an hour-long cut ends exactly where its spans add up. The tests check this at 23.976, 25 and 29.97.
- When the source is on this machine, ffprobe reads its rate, length, size, sound streams and start timecode. Every source time is offset by that timecode, so EDL source timecodes, the FCPXML asset start and the OTIO available range match the camera's own timecode. EDL hours wrap at 24, as a time-of-day camera clock does past midnight. A span that starts before the source's first frame or runs past its last is refused. A source probed at a different rate from the timeline is refused, because each of these files carries one rate for source and record.
- When the source is not on this machine, the writer assumes a start of 00:00:00:00, 1920x1080 and one stereo sound stream, and it says so. An EDL made that way will not conform against camera files that carry timecode.
- The record side starts at 01:00:00:00.
- 29.97 and 59.94 timelines are drop-frame, unless the source's own timecode is non-drop. Every other rate is non-drop. A source whose timecode is drop-frame (`;`) at any other rate is refused. A 23.976 non-drop label runs 3.6 seconds an hour behind the clock. That is how the label works; the frame count does not drift.

## Split edits

A span may carry two optional fields, in seconds:

| Field | Edit | Effect |
|---|---|---|
| `audio_lead` | J cut | the span's sound starts this much before its picture |
| `audio_tail` | L cut | the span's sound runs this much past its picture |

The neighbouring span's sound gives way by the same amount, as a rolling edit leaves it, so the sound stays one unbroken run as long as the picture. A J cut on the first span, an L cut on the last, and a J cut and an L cut meeting at one edit are refused. How long a split should be is an editing choice; the writer applies whatever the manifest says.

## What each format holds

| | EDL (CMX 3600) | FCPXML 1.10 | OTIO JSON |
|---|---|---|---|
| Picture | a V event per span | an `asset-clip` per span in the spine | a video track |
| Sound | an AA event per span, with its own times | the same `asset-clip` | an audio track |
| Split edits | AA times differ from V times | `audioStart` and `audioDuration` | audio clips differ from video clips |
| Media link | 8-character reel from the file name, and `* FROM CLIP NAME:` | `media-rep` file URL | `ExternalReference` file URL |
| Timecode | source and record timecodes, `FCM` line | `tcStart`, `tcFormat`, asset `start` | `global_start_time`, source ranges |

Every FCPXML time is a whole number of frame durations, written over the frame duration's denominator (`90090/30000s`), or as whole seconds (`5s`). The format and the sequence take the source's size.

## What it does not do

- One source per manifest, as the manifest itself allows. Several cameras, B-roll or music beds are not written.
- No transitions, speed changes, reframes, crops, titles, captions or markers. A reframed 9:16 or 1:1 version is still built by `cut` in Resolve.
- No reading. It writes these files and reads none of them back.
- Sound is written as one AA pair or one audio track. A file with four mono streams is described as four sources in FCPXML, but the EDL and OTIO do not map channels.
- No validation against Apple's DTD inside the tool. See the checks below.
- No high-frame-rate drop-frame sources. Some cameras write drop-frame timecode at 119.88 fps (three frame digits, `22:11:23;000`); drop-frame is counted only at 29.97 and 59.94, so such a source is refused with a message that says so. A 119.88 source with non-drop timecode is read.

## Checks before trusting a file

1. `xmllint --noout FILE.fcpxml` must pass (it only checks that the XML is well formed).
2. If Apple's `FCPXMLv1_10.dtd` is on the machine (it ships inside Final Cut Pro), also run `xmllint --noout --dtdvalid FCPXMLv1_10.dtd FILE.fcpxml`. Final Cut Pro rejects a file that fails the DTD.
3. If OpenTimelineIO is installed, `otiocat FILE.otio` must print the timeline without an error.

## Acceptance step (open)

Import each format into desktop Resolve and into Resolve for iPad, and record what arrives. Use a short cut over your own test footage with timecode, three spans and one J cut, written with and without `--media-root`.

For each import, note the menu path used, the version of Resolve, and:

- whether the timeline imports at all, and at which rate and resolution
- whether every clip links to its media, or which relink step it needed
- whether the cut points land on the frames the summary line names (check the first and last frame of each span against the source timecode)
- whether the J cut survives as a split edit or collapses to a straight cut
- whether the record timecode starts at 01:00:00:00, drop-frame where expected

| Format | Desktop Resolve | Resolve for iPad | Notes |
|---|---|---|---|
| EDL | not yet tried | not yet tried | |
| FCPXML | not yet tried | not yet tried | |
| OTIO | not yet tried | not yet tried | |

Replace a cell with what happened and the date. Until a row says it imports, treat that format as untested for that app.
