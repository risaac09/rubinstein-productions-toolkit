# Media review loop

> **Status:** draft, 2026-10-22. How colour, edit and sound decisions pass between Claude Code and the person reviewing, on the desktop app, a phone or an iPad. Most of this is a working agreement, not code. The one tool it adds is `rpstills serve`, a read-only file server; every other command named here belongs to its own stage and is documented there.

Claude can measure a picture or a mix but cannot see taste in it, and it cannot hear at all. So the loop is built for a person who answers fast and often: Claude makes one small thing to look at or listen to, the reviewer answers in a tap or a line, the answer is written down, and over many answers the written record becomes a taste profile that the next proposal starts from.

## One decision per message

- **One stimulus, one question.** A message carries one sheet, one clip or one audio packet, and asks one thing. Two decisions are two messages.
- **The answer is short.** A letter (`B`), a rating from 1 to 5, `approve` or `go` for a single proposal, `none`, `hold`, or one line ("skin too magenta", "hum under the second answer"). A reason is welcome and never required.
- **Nothing anchors the choice.** Options carry labels, not scores, ranks or a "recommended" tag. Order is shuffled (absheet shuffles by seed; the audio packet seals its key). Measurements such as audit numbers, scopes and loudness come after the answer, unless the question is about the measurement itself.
- **Hold is always an answer.** `hold` stops the step with nothing changed. Claude does not re-ask in the same message thread until something new is on the table.

## What Claude sends at each step

| Step | What Claude makes | Command | Sent through | The answer |
|---|---|---|---|---|
| Brief | One line: what is being judged and what the options differ in | none | chat | none needed |
| Triage, stills | The review sheet for a whole session; on a phone, sheets of up to nine frames | `rpstills review`, `rpstills absheet` | desktop: Browser pane through `serve`; phone or iPad: SendUserFile | keep and drop in the sheet, or letters |
| Triage, video | A contact sheet of frames per clip | `tools/ipad-probe/recipes/sheet.sh` (see [the iPad lane](../docs/ipad-lane.md)) | SendUserFile | clip numbers to keep |
| Look | One A/B/C sheet of the same frame under two to nine looks, plus `key.json` | `rpstills absheet` | SendUserFile, display render | a letter, or `none` and a line |
| Look, numbers | Skin Lab, hue and vectorscope angle, clipping, eye line and headroom per finished file, after the pick | `rpstills audit` | chat (the table), audit.json stays local | nothing, or a line |
| Grade on video | A 10 to 20 s proxy (recipe below) and one scopes image | ffmpeg | desktop: Browser pane through `serve`; phone or iPad: SendUserFile up to 25 MiB, or Safari through `serve` on a private network | a letter or 1 to 5 |
| Edit | A proxy render of the proposed cut; after approval, an interchange file for the editor | `rpresolve.interchange` (see [interchange](interchange.md)) | as for video | `approve`, `hold` or a line |
| Sound | Two or more 20 to 30 s excerpts of the same span, loudness matched, labelled A, B, C with a sealed key | `rpresolve.mixbuild ab`; the numbers after the answer from [sound assist](sound-assist.md) | desktop: Browser pane through `serve`; phone or iPad: SendUserFile | a letter and, ideally, a line |
| Gate | Before a render or batch longer than about 30 minutes, or any replacement of a file a client can see: one full-frame sheet per clip and the listening excerpts | the commands above | as above | `go` or `hold` |

The commands in full (`python` is the tool venv's interpreter from the [rpstills README](rpstills/README.md); `python3` is any Python 3.9 or later; rpresolve finds ffmpeg and ffprobe at the Homebrew paths and ignores `PATH`, so elsewhere set `RPRESOLVE_FFMPEG` and `RPRESOLVE_FFPROBE`, as in [requirements.txt](../requirements.txt)):

```bash
cd production

# A/B/C sheet: 2 to 9 images, labels drawn on, order shuffled by the seed, <= 2000 px and < 1 MiB
python -m rpstills absheet look-a.jpg look-b.jpg look-c.jpg --out <review dir>/look-01 --seed 7

# Measurements of finished files, report only (no pass or fail until ranges are set)
python -m rpstills audit <finished dir> --out <review dir>/audit

# Blind audio packet: A.wav, B.wav ... matched within 0.5 LU, key sealed in sealed/key.json
python3 -m rpresolve.mixbuild ab --out <review dir>/mix-01 take1.wav take2.wav --start 42 --seconds 25 [--noise-type rumble]

# The approved cut as a timeline file for Resolve
python3 -m rpresolve.interchange <manifest> --format fcpxml --out <review dir>/cut-v3.fcpxml
```

Without `mixbuild`, cut the same span from each file with `-ss` and `-t`, measure each excerpt's integrated loudness with ffmpeg's `ebur128` filter, and turn the louder ones down with `volume` to the quietest; never use single-pass `loudnorm` for a comparison, because it changes the dynamics being compared.

A proxy that plays everywhere (8-bit 4:2:0 H.264 High, AAC, index at the front for streaming). A 10-bit 4:2:2 log source needs the `format=yuv420p` step, or Safari will not play it:

```bash
ffmpeg -ss <start> -t 20 -i <source> -vf scale=-2:720,format=yuv420p \
  -c:v libx264 -preset medium -profile:v high -b:v 6M -maxrate 8M -bufsize 12M \
  -c:a aac -b:a 160k -movflags +faststart proxy.mp4
```

A 20 s proxy made this way from a 1080p 10-bit 4:2:2 ProRes test signal came to 14.6 MiB. The size is about seconds x 6.2 Mbit/s / 8, so 30 s is the most this recipe fits under 25 MiB.

One scopes image for a frame (picture, luma waveform in IRE, vectorscope; 1280 wide, about 0.5 MB on a test pattern and 1 to 1.5 MB on real footage, taller for 4:3 and 3:2 frames):

```bash
ffmpeg -ss 5 -i proxy.mp4 -frames:v 1 -vf "format=yuv444p,split=3[f][a][b];\
[a]waveform=graticule=green:scale=ire,scale=1280:360[w];\
[b]vectorscope=mode=color3:graticule=green,scale=360:360,pad=1280:360:(ow-iw)/2:0[v];\
[f]scale=1280:-2[p];[p][w][v]vstack=3,format=rgb24" scopes.png
```

## Surfaces

| Surface | Images | Video and audio | Answering |
|---|---|---|---|
| Desktop app, local session | SendUserFile with display render shows them in the chat | Browser pane on `http://127.0.0.1:8780/` from `serve`; click a file in the listing | chat, the question tool's options, the next-step buttons |
| Phone over Remote Control | SendUserFile; a tap-to-open card is the floor, inline rendering varies by version | SendUserFile up to 25 MiB; inline playback on iOS is unverified | chat and the question tool (its questions stay open until answered) |
| iPad over Remote Control | as the phone | as the phone, or Safari on `serve` bound to a private-network address | as the phone |
| Cloud session, no desktop machine | SendUserFile | SendUserFile; derivatives only | as the phone |

Rules that hold on every surface:

- **Client media never goes into an Artifact.** An Artifact is a page on a third party's servers behind a URL that can be shared onward. Use one, if at all, only for synthetic or your own test media.
- **Client media stays on the machine or the private network.** A cloud session gets derivatives (proxies, stills, transcripts) and only when the engagement's consent allows it.
- **Prefer `serve` over uploads for anything that moves.** It has no size cap and sends nothing to a third party.

### Size limits

| What | Limit | Why |
|---|---|---|
| Any SendUserFile file | 30 MiB hard cap; aim for 25 MiB | larger uploads fail outright |
| Images | 2000 px long edge, JPEG under 1 MiB | legible on a phone, quick on a slow link |
| Video proxy | 720p, 10 to 20 s, the recipe above | about 15 MiB for 20 s |
| Audio excerpt | 20 to 30 s, 48 kHz | 24-bit stereo WAV is about 0.29 MB/s, so 30 s is about 8.6 MB |

Uploads also fail now and then on a dropped connection, even for small files. Retry once, then fall back to `serve` and a link. Quote any path with spaces in the tool call.

## serve: the review file server

```bash
cd production
python -m rpstills serve <review dir>                    # http://127.0.0.1:8780/
python -m rpstills serve <review dir> --bind <private-network address> --port 8780
python3 rpstills/serve.py <review dir>                   # the same; standard library only, Python 3.9 or later
```

Python's built-in `http.server` answers a Range request with the whole file and status 200. Safari (on an iPad, every browser is Safari underneath) needs 206 Partial Content to play or seek a video, so a page served that way plays on the desktop and fails on the iPad. `serve` is the same server with byte ranges added:

- GET and HEAD only. POST, PUT and the rest get 501. It writes nothing anywhere.
- One root folder. A request with `..` in its path, encoded or not, or one that reaches a symlink pointing outside the root, gets 403.
- One byte range per request: `bytes=0-99`, `bytes=100-`, `bytes=-500`. The reply is 206 with `Content-Range`, `Accept-Ranges: bytes` and an `ETag`. A range starting past the end gets 416. A multi-range or malformed header is ignored and the whole file is sent, which the HTTP standard allows. An `If-Range` that does not match sends the whole file, so a proxy rendered again under the same name is never spliced with the old one.
- Fixed types for the review formats: mp4, m4v, mov, wav, m4a, jpg, png, html and json. Upper-case camera extensions match too.
- `Cache-Control: no-cache` on every file, so the browser checks again each time and nobody judges a stale render.
- Binds to 127.0.0.1 by default. Bound to any other address it says so on stderr: there is no login and no encryption, so bind only to a private-network interface and serve only a folder of derivatives.

To open it in the desktop app's Browser pane, add an entry to `.claude/launch.json` in the project you work from:

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "review",
      "runtimeExecutable": "python3",
      "runtimeArgs": ["<toolkit>/production/rpstills/serve.py", "<review dir>", "--port", "8780"],
      "port": 8780
    }
  ]
}
```

The folder listing at `/` links every file; a `.mp4` or `.wav` opened from it plays in the pane, and `review.html` from `rpstills review` works as it does from disk.

Measured with a 10 s 720p H.264 test clip (3.6 MB): `curl -H 'Range: bytes=0-99'` returned `206 Partial Content`, `Content-Range: bytes 0-99/3630194`, `Content-Length: 100`, `Content-Type: video/mp4` and 100 bytes of body; `curl -I` returned 200 with `Accept-Ranges: bytes` and the full length; `ffprobe` and `ffmpeg -ss 7` read the clip over HTTP with range requests. Playback in Safari on an iPad has not been tested on a device yet.

## Recording the answers

Every answer becomes one line of JSON in an append-only log beside the project's media, at `<project>/review/events.jsonl` (decided 2026-10-22). The log never sits inside a git working tree and never goes into this repository. Claude writes the line right after the answer, with a JSON encoder rather than hand-built quoting; `serve` has no write path on purpose.

| Key | What |
|---|---|
| `ts` | UTC time of the answer, ISO 8601 |
| `project` | the project id, never a client's or a person's name |
| `step` | `triage`, `look`, `grade`, `edit`, `sound` or `gate` |
| `options` | each label and what it stood for (look file and version, parameter change, source file), taken from `key.json` or the sealed audio key after the answer, so the line decodes without the sheet |
| `choice` | the label, a rating 1 to 5, `approve` (edit), `go` (gate), `none` or `hold`; null when the answer is only a line, which goes in `reason` |
| `reason` | the reviewer's line, verbatim, or null |
| `device` | `desktop`, `phone` or `ipad`; screens differ, and none of them is calibrated against the others |
| `noise_type` | denoise and noise-treatment sheets only: `rumble`, `wind`, `hiss`, `room`, or `other:WORD` with a word that says what. `mixbuild ab --noise-type T` keeps it in the sealed key; otherwise Claude asks or states the type before the sheet goes out |

```json
{"ts": "2026-10-22T14:03:11Z", "project": "P-0001", "step": "look", "options": {"A": "natural-v2", "B": "natural-v2, +200 K", "C": "warm-v1"}, "choice": "B", "reason": "skin reads less grey", "device": "ipad"}
```

Readers ignore keys they do not know, so a line may carry more (`repeat_of` for a repeated comparison, for instance). About one comparison in ten is a repeat of an earlier one in a new order, which shows how consistent the answers are.

The log is distilled, by hand or by a scheduled task, into a **taste profile** kept privately, outside this repository (the location is decided, 2026-10-22): per question (warmth, skin, contrast, pace, music level) the preferred direction with its count, a short "never" list, the ids of exemplar answers, and the answers that contradict each rule. A rule enters the profile only after at least five consistent answers. The profile sets the starting point of the next proposal and the order of options. It never makes a choice on its own.

### Denoise answers are counted per noise type

Whether a denoiser helps depends on what it removes. A reviewer may prefer the treated clip against wind on a mic and the untreated one when the same car rumble is part of the scene. So any denoise or noise-treatment decision carries a `noise_type` in its log line, and the taste profile counts answers per type: "1 of 5 for rumble: not yet a rule". Five consistent answers are five for one type; answers for different types are never pooled, and a rule for one type says nothing about another. Until a type has a rule of its own, the dialogue default (arnndn with `std.rnnn` over afftdn, decided 2026-10-22) stays the starting point for dialogue; the profile orders options and never picks.

```json
{"ts": "2026-10-23T12:51:04Z", "project": "P-0002", "step": "sound", "options": {"A": "untreated", "B": "arnndn std.rnnn alone"}, "choice": "A", "reason": null, "device": "desktop", "noise_type": "rumble"}
```

## Gates that stay human

The quick loop never replaces these:

- **The ear check.** Claude cannot hear. Every audio decision is the reviewer's; loudness, noise and sibilance numbers are evidence beside it, not a verdict.
- **Final approval** of a grade, a cut or a mix before anything is delivered.
- **Anything sent to a client** or anyone outside: an explicit yes in chat for that send, every time.
- **Long renders and replacements.** Before a render or batch longer than about 30 minutes, or any change to a file a client can already see: the full-frame sheet, the listening excerpts, and a yes.

## Known limits

- `serve` has no login, no TLS and no write path, sends one range per request, binds IPv4 only, refuses symlinks that leave the root (copy or hard-link media in instead), and its folder listing shows every name in the folder.
- There is no review page with an A/B wipe, region marking or tap-to-rate; answers go through chat and the question tool.
- The stills review sheet exports `selects.json` as a browser download. That works on the desktop and goes nowhere useful on a phone, so phone triage uses absheet sheets and letters.
- How the phone and iPad apps show SendUserFile video and audio, and the question tool's option previews, is unverified. Before relying on them, send one JPEG, one MP4 under 25 MiB and one WAV to the device and note which play inline.
