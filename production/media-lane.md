# Media lane: colour, edit and sound in one page

What each tool in `production/` does for stills and video, in the order a job
uses them, and what is still a hand step. Every tool here runs offline on a
Mac; most of the offline half also runs on Linux CI. Nothing here sends media
anywhere. These refuse to write inside a git tree: `rpstills develop`,
`finish`, `audit` and `absheet`, `lutlab`, and `rpresolve.interchange`,
`wordsnap`, `shots`, `beats`, `roomtone`, `dxmetrics` and `duckplan`. The older
`rpstills` commands (`index`, `cluster`, `cull`, `review`, `crops`, `look`) and
the iPad recipes write where they are told without checking, so give them an
output path outside any repository.

## The order of a job

| Stage | Stills | Video | Sound |
|---|---|---|---|
| Ingest and check | `rpstills index`, `cluster`, `cull`, `review` | detect, shoot-table, [`tools/ipad-probe`](../tools/ipad-probe/README.md) on an iPad | audio-report |
| Develop and balance | `rpstills develop`: RAW with a per-frame skin solve ([rpstills-develop.md](rpstills-develop.md)) | [`lutlab cst`](lutlab-cst.md): camera log to DaVinci Intermediate or Rec.709; Resolve's Color page for the balance | [`rpresolve.roomtone`](sound-assist.md): find room tone, loop a bed |
| Look | `rpstills look` (a LUT for Resolve) | [`lutlab`](lutlab/README.md) looks, `lutlab pack` for an iPad | a mix spec, when the mix builder lands |
| Cut | `rpstills crops`, `rpstills finish` ([rpstills-finish.md](rpstills-finish.md)) | cut manifest, [`rpresolve.interchange`](interchange.md) to EDL, FCPXML or OTIO, [`wordsnap`, `shots`, `beats`](edit-assist.md) | [`rpresolve.duckplan`](sound-assist.md): music ducked under speech |
| Measure | [`rpstills audit`](rpstills-audit.md), report only | measure, perceive, `lutlab lutstats` | [`rpresolve.dxmetrics`](sound-assist.md): before and after, with lag |
| Review | `rpstills absheet`, `rpstills serve` | proxies through `rpstills serve` | loudness-matched excerpts |
| Deliver | sRGB JPEGs with an embedded profile | deliver_check, qc | loudness fix in deliver_check |

Two edit checks read a cut and write nothing. `python3 -m rpresolve.captionlint
CAPTIONS` lints an .srt or TTML against 42 characters a line, two lines a cue,
20 characters a second, gaps under two frames, overlaps and, where the file
carries positions, 90% title safe; it prints qc's table with FAIL and WARN
rows and exits 1 on a FAIL (`--set max_chars=16` for portrait). `python3 -m
rpresolve.pacing --words W.json` (or `--manifest M`, the cut's spans laid in
record time) reports words per minute, words per minute while speaking, the
pause ratio and, with `--captions`, caption reading speed. Shot lengths are
not repeated there: the rhythm report (VP-132) gives the average shot length
with its median and spread, and `interchange --format edl` turns a manifest
into the EDL it reads.

`python3 -m rpresolve.transcribe MEDIA --backend NAME` writes the Whisper JSON, SubRip and
text these read, from mlx-whisper, whisper.cpp or a transcript made elsewhere; see
[transcribe.md](transcribe.md).

The review step is a loop between Claude Code and the person whose taste
decides. It is written up in [media-review-loop.md](media-review-loop.md):
one decision per message, a lettered sheet or a short excerpt per decision,
an answer that is one letter or one line, and a log of answers outside the
repository. The iPad route, with no Mac, is in [../docs/ipad-lane.md](../docs/ipad-lane.md).

## What stays a hand step

- The grade of record and the final render: DaVinci Resolve, with the Resolve
  MCP server's additive writes and its final-approval gate.
- Listening. Every sound tool reports numbers; none of them is an ear check.
  The person decides on loudness-matched excerpts.
- Taste targets. Stills skin is per person: `rpstills develop` neutralises on a white or grey reference below the face and sets exposure on the cheek, and leaves hue to the complexion (`stills-looks/portrait-neutral-v1.json`). `rpstills audit` checks a set's consistency and its neutrals against working defaults, not a fixed hue.
- Retouch scope. `finish` retouches only the frames it is told to, and logs
  what it changed.
- Imports on another device. EDL, FCPXML and OTIO files and the iPad LUT pack
  are written and checked here; whether a given Resolve build imports them is
  an acceptance step on that device ([interchange.md](interchange.md),
  [lutlab-cst.md](lutlab-cst.md)).

## Known gaps

- No offline per-shot balance for video: the skin solve exists for stills only.
- No shot match from a reference clip: unpaired colour transfer was worse than
  no correction when the content differs, so a paired mode comes first.
- Beats are validated on synthetic loops only; shots finds hard cuts, not
  dissolves.
- The mix builder (an open pull request) is the planned home for the dialogue
  chain; `duckplan` and `dxmetrics` are written so it can adopt them.
- A third-party RAW engine (LightCraft) is an opt-in backend behind an
  environment variable; Apple's engine stays the default.
