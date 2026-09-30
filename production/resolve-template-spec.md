# DaVinci Resolve Template — Personal Post-Production
*Phase B: General-Purpose Multi-Camera Template*

---

## Project Settings

- **Resolution:** 3840 × 2160 (UHD 4K) — all exports, no exceptions
- **Timeline framerate:** 23.976 fps (default) or 29.97 fps — set per project
- **Color science:** DaVinci YRGB Color Managed

---

## Camera Sources & Color Pipeline

Three cameras, three different log profiles. Each needs its own conversion path before the shared creative grade.

| Camera | Log Profile | Conversion | Notes |
|--------|-----------|------------|-------|
| **iPhone 15 Pro** | Apple Log | TheOneLUT (AppleLog → Arri709 V5, 33pt .cube) | Already in Powergrades folder |
| **Panasonic GH7** | V-Log L (or V-Log) | V-Log to Rec.709 LUT (Panasonic-supplied or custom) | GH7 shoots 5.7K Open Gate — may need to scale/crop to 4K timeline |
| **Panasonic GH5** | V-Log L | V-Log L to Rec.709 LUT (Panasonic-supplied or custom) | Different sensor / color response than GH7 — may need separate base correction |

### Node Structure (per clip)

```
Node 1: Source Conversion (camera-specific LUT)
  → iPhone: TheOneLUT
  → GH7: V-Log to Rec.709
  → GH5: V-Log L to Rec.709

Node 2: PowerGrade — Low Contrast (shared across all sources)

Node 3: Manual creative grade (per clip, as needed)
```

The key: Node 1 normalizes all three cameras to the same Rec.709 starting point. Node 2 applies the unified Low Contrast look. Node 3 is where you do per-clip work to match across cameras or push the creative grade.

### PowerGrades to Save in Resolve

- **Low Contrast** — the base creative look (already exists)
- **iPhone Source** — TheOneLUT as a saved node
- **GH7 Source** — V-Log conversion as a saved node
- **GH5 Source** — V-Log L conversion as a saved node

Tag each clip on import so you know which source node to apply.

---

## Branded Intro

- **Asset:** `say-why-intro-4k.png` (3840 × 2160)
- Marrow background, "Say Why" in Bone, amber accent line
- Optional — use only when the project calls for it
- This is a personal creative brand, not RP client delivery

---

## Subtitles

- **Font:** Inter 500
- **Color:** White or Bone (#f2ece4) on semi-transparent dark bar
- **Position:** Lower third, consistent
- **Source:** Auto captions on the `[auto]` timeline (`captions`, see Deliver),
  or File > Import > Subtitle by hand; the style is applied by hand

---

## Export Presets — Always 4K (3840 × 2160)

| Preset | Codec | Bitrate | Use |
|--------|-------|---------|-----|
| YouTube | H.265 | 40–60 Mbps | Primary web delivery |
| LinkedIn | H.264 | 20–30 Mbps | Social feed |
| Master | ProRes 422 HQ | — | Archive / highest quality |
| Instagram / Story | H.264 (1080×1920 crop) | 15–20 Mbps | Vertical reformat |

---

## Project Bin Structure

```
📁 Source
   📁 iPhone
   📁 GH7
   📁 GH5
   📁 Audio (external)
📁 Selects
📁 Timeline
📁 Graphics
   └── say-why-intro-4k.png
📁 Exports
```

Separate source bins by camera so you can batch-apply the correct source conversion node.

---

## Workflow

1. Import media → Source bins (separated by camera)
2. Tag clips by camera source
3. Pull selects
4. Build timeline
5. Apply camera-specific source conversion (Node 1) — batch per camera bin
6. Apply Low Contrast PowerGrade (Node 2) — all clips
7. Manual creative grade (Node 3) — per clip, match across cameras
8. Subtitles if needed (auto captions on the `[auto]` timeline, or .srt import by hand → branded style)
9. Audio work in Fairlight
10. Queue 4K export presets in Deliver page
11. Render

---

## Phase C: CLI Automation

`resolve_workflow.py` implements this against the DaVinci Resolve Scripting
API. Resolve must be running — the API connects to a live instance, not
headless. Coverage against the original Phase C list, checked 2026-08-17:

| Bullet | Status |
|---|---|
| Auto-create project with bin structure | Done (`new-project`) |
| Auto-queue render presets | Done (`render` / `render-all`) |
| Auto-apply source conversion nodes by camera tag | Done (`import-media` tags clips by clip color; `apply-lut --camera <key>` filters by it) |
| Auto-sort media into camera bins by metadata | Not done — `--camera` on `import-media` is still a human-supplied flag, not metadata-driven |
| Auto-apply Low Contrast PowerGrade | Not done — no PowerGrade/gallery-still API call exists in the script; use `apply-drx` with a hand-exported `.drx` instead |
| Subtitle import | Auto captions done (`captions`, 2026-09-29): Resolve transcribes an `[auto]` timeline and the subtitle track is read back. An .srt cannot be placed by script on Resolve 21: `MediaPool.AppendToTimeline` returns True and places nothing, so `add-subtitles` counts the items on every video, audio and subtitle track before and after, exits 0 only when subtitle items alone grew, and otherwise exits 1 naming each track that changed (or that none did); import an .srt by hand (File > Import > Subtitle) |
| Subtitle style application | **Not scriptable.** The API has no entry point for subtitle font/color/position. This stays a manual Edit-page step, permanently — don't wait for it to get built. |
| Select-pulling | Out of scope by design. Choosing the best take is editorial judgment; the tool automates the container around it, not the cut. |

Two other hard API limits worth knowing before you file a bug against the
script:
- **No node creation.** `apply-lut --node 2` (this spec's Node 2, the
  Low Contrast PowerGrade) only works if that node already exists on the
  clip — add it in the Color page first. The scripting API cannot create
  color-page nodes.
- **Vertical export is resize-only.** The `story` preset changes canvas
  dimensions; it does not reframe subjects. `reframe-plan` plans a crop
  per span and `cut --aspects 9x16 1x1` applies it (see Reframe below);
  a timeline built any other way needs its Pan/Zoom set before rendering
  vertical, or the crop will be arbitrary.

### Offline survey of existing projects

`resolve_workflow.py survey --out <path outside this repo>` runs
`resolve_survey.py`, which reads every project in the local disk database
straight from its `Project.db` and never connects to Resolve, so it is safe
while Resolve is open. It snapshots each database with SQLite's online
backup API over a read-only connection and reports timelines, grades (node
labels, LUTs, OFX plugins), color science fields and media roots, plus the
patterns that recur across projects. Values it cannot decode print as
`UNDECODED`. It needs Python 3.14+ for `compression.zstd`
(`/opt/homebrew/bin/python3.14`); the blob formats and field map are
documented in its module docstring. The report names projects and media
paths, so `--out` is refused inside this repository.

### MCP server

[`resolve_mcp.py`](resolve-mcp.md) serves the same library to Claude Code
as 23 tools: reads of the open project, the offline checks (detect,
survey, measure, endcheck, selects, reframe_plan, deliver_check,
sync_measure, trim_review), the offline caption conversion (deliver_captions), and
additive writes (ingest, cut, duplicate, grade onto `[auto]` timelines,
auto captions on `[auto]` timelines, queue a render without starting it,
stack dual-system sound on a new `[auto]` timeline, trim-review markers on
`[auto]` timelines), each write shown as a plan before it runs.

---

## Reframe

The 9:16 and 1:1 versions of a clip are static crops, one per span,
centred on the speaker's face (plan A7). The check: the face box stays
inside the crop on every sampled frame. Two steps: plan offline, then
`cut` applies the plan and reads every transform back.

### Plan

`resolve_workflow.py reframe-plan <manifest> [--aspects 9x16 1x1]
[--samples 8] [--per-second 0.5] [--speaker-x PX] [--allow-bars] [--only
CLIP ...] --out <copy.json>`, or the MCP `reframe_plan`. Offline: ffmpeg
reads frames, macOS Vision finds faces (the same cached helper `measure`
uses, `rpresolve/vision.py`), nothing touches Resolve. The manifest itself
is never changed.

For each span of each clip:

1. **Samples.** Frames spread evenly across `[in, out)`, each in the
   middle of its slice: 8 a span, and at least 0.5 a second (a 25 s span
   gets 13), never more than 120. A time at or past the source's end is
   not read: the span is flagged, and one wholly past the end gets no
   crop, while the rest of the plan goes on.
2. **Picture area.** The rows and columns whose mean luma is above 24 of
   255 in at least one sample. A call recording can letterbox its panes
   inside the 16:9 frame, and a crop that leaves the picture shows bars.
3. **The face.** Vision's boxes are linked across samples into tracks by
   overlap (IoU 0.2 or more, or centres within three quarters of a box
   width). A track in at least half as many samples as the most present
   one is eligible: someone in the span. The primary face is the track
   present in the most samples, a tie going to the larger median box.
   When another eligible track is at half the primary's area or more, the
   choice is flagged for review: on a two-up call Vision often misses the
   speaker as they turn or lean, so the steadier listener can win on
   presence, and "larger" says nothing about who is speaking either.
   `--speaker-x` names the speaker (source pixels): the eligible track
   whose median centre is nearest.
   Every sample is judged. On a sample where the primary face is missing,
   the box nearest its path is checked in its place when it lies within
   three face widths and no other eligible track holds it: a lean that
   jumps further than the linking distance starts a track of its own, and
   may still be the speaker. Otherwise the sample is unchecked. Either case
   flags the span for review, whatever its status.
4. **The crop.** Centred on the median face centre, clamped so the crop
   stays inside the picture area, at the clip's `reframe.scale` (default
   2.25 output pixels per source pixel, `cut`'s convention). When that
   loses the face, the middle of the checked boxes' extent (each grown by
   the margin), clamped the same way, is tried at the same scale: a face
   that sits still and then moves can fit a window its median cannot.
   When any centre at a scale holds every box, the extent's middle does
   (to the 0.1 px it is rounded to), so a crop that fails both fails at
   that scale. The report names the centre used (`median` or `extent`).
5. **The check.** Every checked box (the primary face's, and any taken in
   its place), grown by 10% of its width and height on each side, is
   mapped through that exact transform (centre and scale rounded as
   written) and must sit inside the output frame. Reported over all the
   span's samples: inside, unchecked, the share inside (an unchecked
   sample never counts as inside), the worst sample's time and its slack
   in output pixels (negative: outside by that much).
6. **Fallback.** When the default scale leaves bars or loses the face,
   the widest scale that still fills the frame is tried and checked the
   same way, median then extent (rounded up at the fourth decimal, so
   rounding never opens a bar). When that fails too, the span is
   `manual`: it needs a manual reframe or a split. A span with no face in
   any sample is `no_face` and gets no centre. `--allow-bars` keeps the
   default scale when it holds the face but leaves bars (status `bars`,
   the Ep 002 look, named as such).

Statuses: `pass` (the default scale fills and holds), `fallback` (the
widest filled scale holds), `bars` (only with `--allow-bars`), `manual`,
`no_face`. Exit 0 when every crop holds; 2 when a span needs a person
(`manual`, `no_face`, `bars`, or a flagged span: a choice of face, a
face off its track, an unchecked sample); 1 on an error, including a
Vision helper that cannot run (no face is ever invented).

Output: the manifest copy at `--out`, whose spans carry an entry per
aspect, and `<stem>.reframe.tsv` and `<stem>.reframe.json` beside it. The
format extends the manifest: a clip-level `reframe.face_x` still works,
and a span may carry its own.

```json
{"in": 12.0, "out": 37.5, "end_words": "...",
 "reframe": {"9x16": {"x": 400.0, "y": 360.0, "scale": 6.0, "src": [1280, 720],
                      "area": [0, 200, 1280, 520], "face_x": 400.0, "face_y": 380.0,
                      "centre": "median", "status": "fallback", "inside": [13, 13]},
             "1x1": {"status": "manual", "reason": "... needs a manual reframe or a split"}}}
```

`x`, `y` are the crop centre in source pixels and `scale` output pixels
per source pixel; the rest is the record. An entry without `x` means no
crop. The TSV has a row per span and aspect: `clip, span, in, out, aspect,
status, review, samples, face_samples, face_x, face_y, hand_face_x, dx,
centre, scale, zoom, pan, tilt, fills, inside, unchecked, share, worst_t, worst_px,
reason` (`hand_face_x` is the clip's hand-set `face_x`, `dx` the
difference). The JSON adds every scale tried with its check, the primary
face's box on each sample, any box checked in its place (`off_track`) and
the unchecked samples' times.

### The transform

Resolve fits a source whose size differs from the timeline's by
`fit = min(W/sw, H/sh)` (`timelineInputResMismatchBehavior` scaleToFit,
the default; scaleToCrop uses the max), zooms about the frame centre, then
moves the picture in timeline pixels. A crop of `s` output pixels per
source pixel centred on source point `(cx, cy)` is

    ZoomX = ZoomY = s / fit
    Pan  = (sw/2 - cx) * s
    Tilt = (cy - sh/2) * s

and a source point `(x, y)` lands at `X = W/2 + (x - cx) s`,
`Y = H/2 + (y - cy) s`. The widest crop with no bars over a picture area
`aw x ah` is `s = max(W/aw, H/ah)`.

| Source | 9:16 at 2.25 | 9:16 widest filled | 1:1 widest filled |
|---|---|---|---|
| 1280x720 | Zoom 2.6667 | s 2.6667, Zoom 3.1605 | s 1.5, Zoom 1.7778 |
| 1920x1080 | Zoom 4 | s 1.7778, Zoom 3.1605 | s 1.0, Zoom 1.7778 |
| 3840x2160 | Zoom 8 | s 0.8889, Zoom 3.1605 | s 0.5, Zoom 1.7778 |

At 2.25 a 720-row source covers 1620 of 1920 rows: the default, taken
from the Ep 002 script, never filled a 9:16 frame from that source. On
1080p and 4K sources it crops tightly (a 480x853 window of a 4K frame),
and the fallback carries.

Pan's sign and units are confirmed on a render: in the sandbox 9:16
render (below) the face centre sits where `X = W/2 + (x - cx) s` puts it,
within 3 pixels, from Vision on the render and on the source frame. Tilt's
sign assumes Resolve's y axis points up; no render has confirmed it, so
every report row whose crop sets a Tilt says so (and the summary counts
them), and `cut` says so whenever it writes a Tilt other than 0.

### Build

`resolve_workflow.py cut <copy.json> --project ... --aspects 9x16 1x1`,
or the MCP `cut` with `aspects`. Each version is a duplicate of the
clip's 16:9, set to 1080x1920 or 1080x1080 and read back; each V1 item
gets its span's transform (ZoomX, ZoomY, Pan, Tilt), and every property is
read back. The input scaling is read before anything is duplicated, from
the 16:9 (else the project; when neither reports it, scaleToFit and a
note), and the new timeline must read the same. The dry run's `plan_sha`
covers the source's resolution and the project's input scaling, since the
Zoom shown depends on both: a change to either before the real run
refuses it, and a 16:9 that reads another scaling than the plan's refuses
its versions.

- **The clip-level `face_x`** (the Ep 002 form) is built as before: every
  item gets ZoomX, ZoomY and Pan for a crop centred on `face_x` at the
  source's middle row, no Tilt, and the result notes that nothing checked
  the face inside it. A span's own entry wins over it.
- **UNREFRAMED.** A version with a span that has neither, or an entry
  that `reframe-plan` left without a crop, is not built. The dry run lists
  it under `unreframed` and leaves it out of `would_create`; the real run
  names it UNREFRAMED with the span and the reason, and exits 1. A 9:16 is
  never built quietly at Zoom 1.
- **REFUSED.** An input scaling other than scaleToFit or scaleToCrop, or
  a V1 item of the 16:9 with its own `Scaling` (the Inspector's per-clip
  Crop, Fit, Fill or Stretch; the transform is computed for 0, use project
  settings): the version is not built. An item that does not report its
  `Scaling` is counted in a note and taken as 0. The dry run lists it under `refused` and leaves it
  out of `would_create`; the real run names it REFUSED and exits 1.
- **BARS.** A version whose picture does not fill the frame (by the entry's
  picture area, else the whole source frame) is built, and its result
  names each span ("the picture covers 1620 of 1920 rows"); exit 2.
- **LEFT BEHIND.** A version that fails after the duplicate (a write that
  does not read back, a different number of V1 items, a different input
  scaling) names the timeline it left behind in `left_behind`, in the
  result, the summary and the MCP journal, with how many of its items were
  transformed. A re-run skips a timeline that exists, so delete that one
  first. A 16:9 that fails its read-back is named the same way.
- **One timeline at a time.** Each name is decided on its own: one that
  exists is skipped and listed under `skipped_existing`. A version missing
  beside a 16:9 that exists (the 1:1 asked for after the 9:16 was cut, say)
  is built from that 16:9 when its V1 items are still the manifest's spans:
  the same source clip, source frames, durations and order. When they are
  not (a person trimmed it), the version is refused and a new prefix is
  the way to build it.

### Why the sandbox 9:16 had bars (2026-09-30)

The sandbox's MCP-built 9:16 (`MCP_<clip>_9x16 [auto]`) and its copy
`A6 deliver 9x16 [auto]` rendered with black bars above and below. The MCP
write journal shows `cut` built it from a manifest whose clips carry a
hand-set `reframe.face_x`, and reported the 9:16 created, which it does
only when every ZoomX, ZoomY and Pan read back: the transform was the
default scale, 2.25 output pixels per source pixel. The source is a call
recording whose picture is a letterboxed band inside its 1280x720 frame,
and the rendered picture's height matches that band at 2.25. So it was
never a letterbox at Zoom 1: at 2.25 a 720-row source covers 1620 of 1920
rows, a 360-row band covers 810, and nothing checked that the picture
filled the frame. `cut` now names that case BARS, and `reframe-plan`
measures the picture area and prefers the widest filled crop. The live
read-back of those two timelines' transforms is owed (Resolve was closed).

### A two-up call recording, planned offline (2026-09-30)

A dry run of `reframe-plan` over a real episode's final manifest (a
1280x720 two-up call recording, both aspects) showed what this kind of
source does. Its per-span numbers stay out of this public document.

- **Which face.** Both panes hold a face of similar size in every span,
  so without `--speaker-x` the choice is a guess and is flagged; with it,
  the named face is taken.
- **The default scale 2.25** held the face, and every crop left bars
  (810 of 1920 rows; 810 of 1080).
- **9:16 filled** is a window a third of a pane's width, upscaled more
  than five times, and a filled 9:16 of a small pane rarely holds a moving
  speaker: most spans came out `manual`.
- **1:1 filled** (a window the pane's height) held on nearly every span.

So for such a source a filled 9:16 needs splits or manual reframes, and
the decision between that and the letterboxed look (`--allow-bars`) is
Isaac's.

### What is known and what is not

Owed live, in the sandbox ("RP Automation Sandbox"), Resolve being closed
on 2026-09-30:

1. Read back the transforms of the MCP-built 9:16 and
   `A6 deliver 9x16 [auto]` (the diagnosis above predicts ZoomX 2.6667,
   Pan 832.5 on every item).
2. Build one reframed clip's 9:16 and 1:1 under a new prefix
   (`A7reframe`) from a planned copy, and read back every transform and
   the input scaling setting. No render queued.
3. Tilt's sign: set a Tilt on a sandbox item, export a still
   (`ExportCurrentFrameAsStill`), and find the face with Vision.
4. Whether `timelineInputResMismatchBehavior` reads on a duplicated
   timeline, and whether scaleToCrop's Pan counts the same pixels.
5. What `GetProperty("Scaling")` reads on an item appended by `cut`
   (expected 0, use project settings) and on one set to Fill in the
   Inspector (expected 3).

Gaps in the method:

- The picture area is found by luma: a grey call-app background counts
  as picture, and a logo or caption in a bar widens it.
- In a two-up, a crop can cross the seam into the other pane; the check
  covers the face and the bars only.
- Vision's box is the face, without hair or chin; the 10% margin is a
  guess to calibrate on stills.
- Samples are 2 s apart at the minimum rate, so a turn or lean shorter
  than that can be missed.
- Rotated sources are untested. The planner works in displayed pixels, and
  `cut` refuses an entry planned on a size other than the pool clip's
  `Resolution`.
- A 5.3x upscale is soft; judge it on a still before choosing it.
- The per-span build and the 1:1 are tested against fakes only.

North register: self-consuming. The manifest copy feeds `cut`; the TSV
explains a crop `cut` refuses or names, read when a span needs a person.
No new standing surface.

---

## Deliver

A deliverable is rendered for a **destination**: what one platform wants,
kept in `resolve-config.json` under `destinations`, laid over the house
rules under `deliver`. These sit beside the older `render_presets`, which
`render` and `render-all` still use.

| Destination | Container, video | Size | Audio | Loudness | Captions |
|---|---|---|---|---|---|
| `youtube_16x9` | mp4, H.265 | 3840x2160 | AAC 48 kHz stereo | -14 LUFS | sidecar .srt |
| `youtube_16x9_hd` | mp4, H.265 | 1920x1080 | AAC 48 kHz stereo | -14 LUFS | sidecar .srt |
| `linkedin_16x9` | mp4, H.264 | 1920x1080 | AAC 48 kHz stereo | -16 LUFS | sidecar .srt |
| `linkedin_9x16` | mp4, H.264 | 1080x1920 | AAC 48 kHz stereo | -16 LUFS | burnt in |
| `linkedin_1x1` | mp4, H.264 | 1080x1080 | AAC 48 kHz stereo | -16 LUFS | burnt in |
| `substack_16x9` | mp4, H.264 | 1920x1080 | AAC 48 kHz stereo | -16 LUFS | sidecar .srt |
| `client_master` | mov, ProRes 422 HQ | the timeline's | LPCM 24-bit 48 kHz | none (unnormalized) | none |

Each destination sets its own frame size; the "always 4K" rule under Project
Settings and Export Presets describes the render presets.

House rules, all in the config: integrated loudness within +/-0.5 LU of the
target, true peak at or under -1.0 dBTP, frame rate equal to the timeline's,
colour tags Rec.709 primaries and matrix, with the transfer tagged per
destination: web files use `GammaTag` "Rec.709-A", which writes the
transfer as bt709 (asserted by the check), and the client master keeps
the house "Gamma 2.4", which writes it unspecified (reported only). Isaac
compared both renders and chose Rec.709-A for the web on 2026-09-29. Limited
range (a `yuvj*` pixel format or a `pc` flag fails; an unflagged YUV
stream counts as limited, as decoders read it), and auto-caption line
length by frame shape (`deliver.captions`: 42 characters on one line for
landscape, 20 on up to two lines for portrait, 24 on up to two lines for
square). An overlay
kept outside this repository (`--config` on the CLI, `RPRESOLVE_CONFIG` for
the MCP server) lies over `resolve-config.json`: it changes any one field
of a destination, adds a private `target_dir`, adds a destination, or
removes one with `null`, and leaves every other value as the repo file
has it. An overlay
that is missing or does not parse stops the deliver commands (exit 1)
and the MCP tools; they never carry on with the defaults.

### File names

```
SW001_Guest_01_example-clip_16x9.mp4      show code, episode, guest, clip, slug, aspect
Client_example-slug_master.mov            client master
```

Show code: 1 to 8 capital letters. Episode: 3 digits. Guest and client:
letters and digits only. Clip index: 01 to 99. Slug: lowercase letters and
digits in words joined by single hyphens, up to 60 characters. Aspect:
`16x9`, `9x16` or `1x1`, taken from the destination, as is the extension.
Names are validated when a job is queued and again when the file is checked.

### Folders

The name carries the aspect but not the platform, so each destination
renders into its own subfolder of the target folder, named by its
`subfolder` (default: the destination key):

```
05-Deliverables/
  youtube_16x9/SW001_Guest_01_example-clip_16x9.mp4   (+ .srt)
  linkedin_16x9/SW001_Guest_01_example-clip_16x9.mp4  (+ .srt)
  linkedin_9x16/SW001_Guest_01_example-clip_9x16.mp4
  client_master/Client_example-slug_master.mov
```

One clip delivered to several platforms keeps one name per aspect and
never collides. The real queue makes the subfolder when it is missing
(a dry run makes nothing) and removes it again when nothing was queued.
A file already sitting at the subfolder's name is refused. `"subfolder":
null` in a config renders straight into the target folder. `deliver-check`
has a `folder` row: PASS in the destination's own subfolder, FAIL in
another destination's (a LinkedIn check of YouTube's file), SKIP in a
folder no destination owns.

### The loop

Queue, render, captions to .srt (sidecar destinations), fix loudness,
check. Isaac chose on 2026-09-29 to run the loudness fix on every render
as the standard last step, so the check that follows judges the file that
ships.

0. **Captions first**, for a destination that wants them (sidecar or burnt
   in): `resolve_workflow.py captions --project ... --timeline "<name>
   [auto]" --dry-run`, then again with `--plan-sha`; or the MCP
   `create_captions`. See Captions below. The queue refuses a captioned
   destination whose timeline has no subtitle items.
1. **Queue.** `resolve_workflow.py deliver-queue --project ... --timeline ...
   --dest <key> --show SW --episode 1 --guest Guest --index 1 --slug
   example-clip --target-dir <folder> --dry-run`, then again with
   `--plan-sha`; or the MCP `queue_render` with `destination`, `name` and
   `target_dir`. It sets format and codec, the render mode Single clip
   (read back before the job is added; format, codec and mode are put back
   after), then size, frame rate, audio codec, bit depth and sample rate,
   colour tags, captions (`ExportSubtitle`, `SubtitleFormat` `SeparateFile`
   or `BurnIn`), Data Burn-in `None` (the house `deliver.data_burn_in`, so
   a timecode burn-in kept for review copies stays off deliverables),
   render all frames, and never replace existing files. Every other render
   setting comes from the Deliver page as it stands, and the result names
   them (`carried_over`). It refuses, with every reason, when a
   destination of fixed size gets a timeline of another shape, meaning
   another orientation or a width:height more than 1% off (a 9:16
   destination takes the timeline's 9:16 copy: Resolve scales a 16:9
   picture into a 1080x1920 frame with bars, and every row of
   deliver-check would still pass); when the target folder is missing;
   when it sits under `/Volumes` and that share is not mounted (a dropped share leaves its folders on the
   boot disk; `/volumes/work` and `/System/Volumes/Data/Volumes/Work` count
   as `/Volumes/Work`); when it is inside a git working tree; when the file already
   exists, or a queued job writes the same file (under any spelling); for a sidecar
   destination, when any caption file under the file's stem already sits
   there (`.srt`, `.vtt`, `.scc`, `.ttml` or `.xml`, in any case); when
   captions are wanted and the timeline has no subtitle track, or only
   empty ones. It never starts the render.
2. **Render.** Isaac starts it on the Deliver page.
3. **Captions to .srt**, for a sidecar destination:
   `resolve_workflow.py deliver-captions <file> --dest <key>` (or the MCP
   `deliver_captions`). Resolve writes the sidecar as TTML timed from the
   timeline's timecode (see Captions below); this makes the zero-based
   `<stem>.srt` a platform reads, and moves the TTML to the Trash.
4. **Fix loudness**, the standard last step:
   `resolve_workflow.py deliver-fix-loudness <file> --dest <key> --replace`.
   A two-pass loudnorm (measure, then linear with the measured values) on
   the first audio stream, re-encoded to the destination's audio; video
   and every other stream copied, colour tags kept. It writes
   `<stem>.loudfix<ext>` beside the original, checks it under the
   original's name and confirms the video stream is bit-identical. With
   `--replace`, and only after that passes, the original moves to
   `~/.Trash/deliver-loudfix-<timestamp>/` and the fixed file takes its
   name; the `.srt` beside it keeps matching. If loudnorm had to fall back
   from linear to dynamic mode (a linear gain would have broken the
   true-peak limit), it says so: listen before delivering. For a sidecar
   destination whose TTML has not been made into an `.srt` yet, `--replace`
   refuses before any work (the fixed file's captions row would fail):
   run step 3 first. The client master has no loudness target, so this
   step is skipped for it.
5. **Check.** `resolve_workflow.py deliver-check <file> --dest <key> --fps
   <timeline fps>` (or MCP `deliver_check`). Exit 0 all pass, 1 any fail,
   2 ffprobe or ffmpeg missing; `--json` for the whole result. The client
   master renders at the timeline's size, so pass `--size` to assert it.
   For a sidecar destination the captions row fails when the `.srt` is
   missing (and, when Resolve's TTML is still beside the file, its note
   says to run deliver-captions), does not parse, or has a cue that
   starts before 0 or ends more than 0.5 s after the video does: an
   `.srt` timed from the timeline's 01:00:00:00 fails here.

### Captions

**Making them.** `captions` (MCP `create_captions`) runs Resolve's auto
captions (`Timeline.CreateSubtitlesFromAudio`, Resolve Studio) on one
`[auto]` timeline. The line length and line breaks come from
`deliver.captions` for the timeline's shape, read from its resolution:
Resolve's default of 42 characters on one line overflows a 1080-wide 9:16
frame when burnt in (clipped at both edges on the 2026-09-29 renders), and
about 20 fit. It refuses a timeline that is not `[auto]`, one that already
has any subtitle item (additive only), a run while a render is in
progress, and a Resolve that does not define a constant the settings need
(an unknown `resolve.CONSTANT` reads as None). It makes the timeline
current and opens the Edit page for the call, then puts back the current
timeline and page. It then reads the subtitle tracks back and fails when
no item is there, whatever the call returned. On the sandbox the call
took 36 s on a 37 s portrait timeline (45 s for the whole command) and
gave 22 items, the longest line 22 characters on one or two lines (39 on
one line at the defaults). Whether
20 characters sit inside the frame when burnt in is checked at Isaac's
next render.

**The sidecar.** A render with `SubtitleFormat` `SeparateFile` writes
`<stem>_<subtitle track name>.ttml` beside the file (such as
`<stem>_Subtitle 1.ttml`): IMSC1 TTML, `ttp:timeBase="media"`,
`ttp:frameRate="25"`, cues as `<p begin="01:00:00.039"
end="01:00:03.519">`, timed from the timeline's timecode. The rendered
file carries the same start as a timecode tag (ffprobe: stream tag
`timecode=01:00:00:00`). `deliver-captions <file> --dest <key> [--track
NAME] [--keep-ttml] [--json]`:

- refuses unless the destination's captions are `sidecar`, the file sits
  outside any git working tree, `<stem>.srt` does not exist yet (nothing
  is overwritten), and one `<stem>_*.ttml` sits beside it; with several
  (one per subtitle track), `--track` names the one to convert and the
  others stay where they are;
- reads the TTML with every namespace, clock times with fractions or
  frames (`ttp:frameRate`, `ttp:frameRateMultiplier`, sub-frames), offset
  times (`12.5s`, `300f`, `500ms`), `dur` (with `end` too, the earlier
  end wins), begin offsets on `body` and
  `div` (parallel time containers; `timeContainer="seq"` is refused),
  `<br/>` as a line break and spans flattened, a `<p>` anywhere but in a
  `div` or the `body` refused so that none is lost (anything else in a
  `<p>`, such as `ttm:desc` or `ttm:agent`, is left out, and text in an
  element outside TTML is named in a warning); media time
  (Resolve's) or non-drop SMPTE time, where a label counts frames at
  `ttp:frameRate` and is divided by the effective rate (TTML2 I.3), and
  where discontinuous markers (TTML2's default) refuse `dur` and a timed
  `body` or `div`;
- takes off the file's start timecode (the video stream's `timecode` tag,
  else another stream's, else the format's), counted at the file's own
  frame rate; drop-frame (`;`) is counted as SMPTE drop-frame at 29.97 and
  59.94 and refused at any other rate. With no timecode tag it refuses
  unless the first cue already starts inside the file. After the shift
  every cue must start at or after 0 and end within the video's duration
  plus 0.5 s, or it refuses with the numbers; at a non-integer rate
  (23.976, 29.97) the refusal also gives the start as a timecode label
  (3.6 s an hour less at non-drop) and says whether the cues fit with
  that taken off instead, the sign of a TTML timed in label time. Which
  of the two Resolve writes at those rates is still open: a sandbox
  render of a 23.976 `[auto]` timeline settles it;
- writes `<stem>.srt` (UTF-8, LF line ends, cues numbered from 1,
  `HH:MM:SS,mmm`), reads it back (same cue count, text and times as the
  TTML, or the new `.srt` is removed again), then moves the TTML to
  `~/.Trash/deliver-captions-<timestamp>/` unless `--keep-ttml`. A move
  that fails leaves both files beside the render and says so.

The MCP `deliver_captions` never connects to Resolve, but it writes a file
and moves another, so it keeps the write tools' contract: a dry run by
default that shows the offset, the cue span and the `.srt` path, a real
run only with that dry run's `plan_sha`, and a journal line before and
after. Exit codes on the CLI: 0 written and verified, 1 refused or
failed, 2 ffprobe missing.

On the 2026-09-29 sandbox renders (copies): 22 cues, the first at
0.039 s and the last ending at 36.880 s, the video's length; after the
loudness fix with `--replace`, deliver-check passed every row it asserts.

### Not verified until a live queue and a real render

These are set from the scripting README and the config. The first renders
with these settings (sandbox, 2026-09-29) answered two of them; the rest
are still open.

- **Settled 2026-09-29 by a real render:** `GammaTag` "Gamma 2.4" writes
  the transfer as unspecified (1-2-1), so each player guesses the gamma;
  "Rec.709-A" writes bt709 (1-1-1). Isaac compared the two renders and
  chose Rec.709-A for web destinations, whose check now asserts transfer
  bt709. The client master keeps Gamma 2.4.
- The tag strings `Rec.709` and `Gamma 2.4`, the `AudioCodec` strings
  `aac` and `lpcm`, and the `DataBurnIn` string `None`. The queue assumes
  that `SetRenderSettings` returns False for a string Resolve does not
  accept, so that a required step fails and the job is not queued. That
  is an assumption until the sandbox step below confirms it. If Resolve
  instead returns True and keeps its previous value (such as "Same as
  Project"), the job queues with the wrong tags, the read-back lists
  `GammaTag` as unverified, and nothing downstream catches the transfer,
  which deliver-check only reports.
- **The sidecar's name and format**: `<stem>_<track name>.ttml`, IMSC1
  TTML timed from the timeline's timecode (see Captions);
  `deliver-captions` makes the `<stem>.srt` the check expects. The format
  is not a scripting key. A burn-in or no-captions file fails the check
  when any caption file under its stem sits beside it.
- Which fields `GetRenderJobList` reports. The queue result compares the
  ones it does report and lists the rest as unverified.
- Channel count cannot be set through the API (the render takes the
  timeline's output bus). `VideoQuality` (bit rate), `EncodingProfile`,
  `MultiPassEncode`, `PixelAspectRatio`, `ExportAlpha`, `AlphaMode`,
  `UniqueFilenameStyle`, `UseFullExtents`, `AddFrameHandles`,
  `ClipStartFrame` and `TimelineStartTimecode` are not set, so they carry
  over from the Deliver page; every queue result lists them as
  `carried_over`, and the config's `resolve` block can set any of them per
  destination.
- Whether `GetRenderJobList` reports a job's render mode, and how Resolve
  names the files of a job queued in Individual clips mode. The queue
  sets Single clip and reads it back with `GetCurrentRenderMode` before
  `AddRenderJob`; it has not been seen on a live Resolve yet.

### Live steps owed (sandbox project only)

Run each in the "RP Automation Sandbox" project, pinned by its unique id,
and write down what Resolve did. Steps 2 and 5 ran on 2026-09-29 (the
transfer tag and the sidecar above), including step 2's Rec.709-A
comparison, which settled the web tag; no result for steps 1, 3 and 4 is
recorded here.

1. **Invalid tag strings.** Call `SetRenderSettings({"GammaTag": "Gamma
   2.4 (not a tag)"})`, then the same for `ColorSpaceTag`, `AudioCodec`
   and `DataBurnIn`. Record whether each returns False. If any returns
   True, the queue's refusal on a required step does not protect that
   field, and the valid strings must be confirmed some other way (for
   example `SaveAsNewRenderPreset` and reading the preset back).
2. **Valid tag strings.** Queue `linkedin_16x9` on a short 16:9 sandbox
   timeline with `deliver-queue`, render it by hand, and run
   `deliver-check`. Done 2026-09-29: "Gamma 2.4" reads unspecified,
   "Rec.709-A" reads bt709, and web destinations now assert bt709.
3. **Render mode.** Leave the Deliver page in Individual clips, queue a
   destination, and record `GetCurrentRenderMode` before and after,
   whether `GetRenderJobList` names the mode, and whether putting the mode
   back to Individual clips changes the job already queued (the format and
   codec restore rests on the same assumption).
4. **Job list fields.** Record which of `FrameRate`, `AudioSampleRate`,
   `AudioBitDepth`, `AudioCodec`, `ColorSpaceTag`, `GammaTag` and
   `DataBurnIn` `GetRenderJobList` reports, and how it writes each.
5. **Sidecar name.** Done: `<stem>_<track name>.ttml`, IMSC1 TTML (see
   Captions).
6. **Burnt-in caption width.** Render `linkedin_9x16` from a timeline
   captioned with the portrait settings (20 characters, two lines) and
   look at the widest caption in the frame.

Remove the queued sandbox job after each step; never start a render from
a script.

---

## Sync

Dual-system sound: a camera clip plus a separately recorded audio file, or
two cameras in one room. Two steps: measure offline, then stack the pair
on a new `[auto]` timeline. Making a multicam clip from it stays a hand
step (the API has no multicam call).

### Measure

`resolve_workflow.py sync-measure <reference> <other> [--fps F] [--window
S] [--json]`, or the MCP `sync_measure`. Offline; nothing touches Resolve.
The reference is the camera clip.

- Both files' audio is decoded to mono at 8 kHz (ffmpeg, every channel
  averaged) and cross-correlated with FFTs (numpy). A coarse pass runs on
  the whole of both, block-averaged to 1 kHz (lower past about an hour
  each), and finds the lag over everything the two could share. A fine
  pass at 8 kHz searches 1 s either side of it on 30 s windows across the
  overlap: a head, a tail and at least one between (up to 7), or the whole
  overlap as one window when it is under 90 s.
- **Offset:** where the other file's first frame lands on the
  reference's clock. Positive when the other started later; negative when
  it started earlier. With drift measured, it is the drift line's value
  at the overlap's midpoint, where one placement errs least (the head
  window's own reading is `head_offset_s`); with one window, or windows
  that do not lie on one line, the head window's. Each file's zero is its first video frame (its first audio
  sample when it has none), so an audio stream that starts late in its
  container is counted. That assumes Resolve also plays such a stream
  from its own start time, which is not yet seen live (item 7 below); the
  report adds a note whenever either file's audio starts off its first
  frame.
- **Frames:** the offset times the reference's frame rate (or `--fps`),
  placed at the whole frame by rounding half up. Frame placement leaves a
  residual of up to half a frame (20 ms at 25 fps, 8.3 ms at 60): the
  report gives the one this offset leaves at the midpoint, beside that
  inherent bound; drift adds to it toward either end.
- **Confidence**, per window: the normalized correlation at the peak
  (-1 to 1; its sign is the polarity, and an inverted mic reads as a match
  with polarity "inverted") and the peak over the highest correlation more
  than 50 ms away. A window under 2x or under 0.1 is not a match, nor is
  an overlap under 5 s.
- **One moment is not a match.** Each window's correlation is measured
  again with its strongest second left out, and must still reach 0.1. Two
  unrelated files, each silent (a muted input, a closed noise gate) or
  quietly noisy but for one click, line up at correlation 1.0 on that
  click; without it the rest reads 0.000 and the report names the moment.
  A door slam over talk that also lines up keeps the rest well above 0.1.
  A file of digital silence is refused as that, before any correlation.
- **Sound that repeats.** A window searches only 1 s either side of the
  coarse lag, so its ratio cannot see a second match further away. The
  coarse pass can: its peak over the best lag more than 1 s away. Under
  2x, that runner-up is measured window by window as the match is, and
  when every window there passes too, the pair is refused as ambiguous
  with both offsets named (a looped music bed, one sting at both ends, a
  repeated countdown). When the runner-up fails, the low coarse ratio came
  from noise the fine pass sees through: on synthetic pairs with heavy
  low-frequency rumble (wind or handling on a camera mic), 13 of 27
  correct matches had a coarse ratio under 2x, and all 13 still match.
- **Drift:** a straight line through every window's offset gives the
  clock drift, in ms per minute and ppm, and what it adds up to over the
  overlap. What counts is where the placement leaves the ends of the
  overlap: the frame rounding plus half the drift (`worst_ms`). Over half
  a frame, the report says so and gives the speed that would cancel the
  drift (`retime_pct`, for the other clip) and where the clip's first
  frame then belongs (`retime_offset_s`, retimed about that frame);
  nothing corrects it (exit 2). Counting the drift alone, as before, let
  25 ppm over 10 minutes (15 ms) exit 0 while one end sat 23 ms out. When the line slopes by more than a quarter sample per
  window, the windows are measured again with the other file stretched by
  that slope: a drifting clock smears a window's peak (50 ppm over 30 s is
  1.5 ms), and on a synthetic 50 ppm pair the stretch took each window's
  correlation from about 0.3 back to 0.99. The stretched pass is kept
  only when it raises the windows' correlation; a slope fitted through a
  step lowers it, and the first pass stands.
- **Windows off the line:** one clock keeps every window within about a
  millisecond of its drift line (within microseconds after the stretch,
  on synthetic drift). A window more than 2 ms or a tenth of a frame off
  it, whichever is more (4 ms at 25 fps), means the pair lines up
  differently in different places: a call recorded at both ends, a
  recorder that dropped samples, an edited file. That is not a match, and
  the report groups the windows by the offset they agree on. A line
  through three windows absorbs two thirds of a step at one end, so the
  limit catches a step down to 0.3 frame; synthetic audio that lost 30 to
  45 ms of samples (a USB or OBS dropout) is refused, where the earlier
  half-frame limit let it pass as 140 to 230 ppm of drift.
- **A slope beyond two clocks:** on a clean line, more than 300 ppm is not
  a match. Two crystal clocks stay well inside it; a 0.1% pull-up or
  pull-down (1000 ppm, a recorder set to 48.048 or 47.952 kHz) reads like
  this, and the reason names it.

Exit status: 0 a match; 2 a match whose placement leaves either end of
the overlap more than half a frame out; 1 no match or a file that cannot
be read.

**Measured on real pairs, 2026-09-29** (read-only copies in a temp
folder; nothing from them is in this repository):

| Pair | Result |
|---|---|
| A video call's own video file and its separate audio file (77 min) | offset 0.0000 s, correlation +1.000, drift 0.00 ppm: MATCH |
| The same call recorded at both ends: a screen recording and the call app's audio file (82 and 79 min) | NO MATCH. Five middle windows agree on +127.3261 s (correlation +0.94 to +0.97; within 0.001 ms of each other across 52 minutes, one computer's clock); the head and tail windows find +127.142 s, inverted (-0.43, -0.69). Each voice reaches the two files by its own path, 184 ms apart, so no one placement holds both |
| Two unrelated recordings | peak 1.05x, correlation +0.03: NO MATCH |

Each measurement of a 77 to 82 minute pair took about 5 s on the M4 Max
(decode included; the correlation alone 0.4 s).

### Stack

`resolve_workflow.py sync <reference> <other> --project "<open project>"
[--project-id ID] [--bin NAME] [--name "<name> [auto]"] [--autosync]
--dry-run`, then again with `--plan-sha`; or the MCP `sync`.

- **Clips.** `reference` and `other` name media-pool clips by unique id,
  file path or clip name (one clip each, or it is refused). With `--bin`
  they are files instead, imported into a new bin of that name at the
  pool's root, which the run then owns; a bin of that name that exists
  already, or a file already in the pool, is refused. The new timelines
  are made in that bin, and the folder Isaac had open is put back.
- **Measured first.** The files are measured as above, at the timeline's
  frame rate. A result that is not a match is refused with its reasons
  (and the offset groups), and nothing is built.
- **The timeline:** `<reference> sync [auto]` (or `--name`, which must
  end ` [auto]`; an existing name is refused), at the reference's frame
  rate, set while it is empty and read back. Resolve spells an NTSC rate
  short (`29.97`); the frame math uses the rate it stands for
  (30000/1001), since a plain 29.97 puts a placement 0.108 frame off per
  hour of offset. The reference goes on V1/A1
  from the start; the other on A2 (an audio file, `mediaType` 2) or V2/A2
  (a camera) at the offset in whole frames. A negative offset moves the
  reference later instead, so nothing is trimmed from either.
- **Every placement is read back.** `AppendToTimeline` works on the
  current timeline, so the new timeline is made current (and put back
  after); tracks are made with `AddTrack` first (an added audio track's
  type is read back: `stereo` for 2 channels, `mono` for 1); then each
  placement must show exactly one item of its clip on its track, at
  exactly its planned start and source start (whole frames the plan
  chose; one frame off would add to the half frame of rounding), with its
  length within one frame (a rate conversion can round it). An item
  that no placement explains is a problem too. A track that already holds
  items when a clip is due there is not placed onto.
- **Drift:** the other clip is placed at the offset the drift line gives
  at the overlap's midpoint. When the rounding plus half the drift leaves
  either end more than half a frame out, that is reported (exit 2), with
  the retime that would cancel the drift and where the retimed clip's
  first frame belongs. Nothing retimes the clip; that is a hand step.
- **AutoSyncAudio** (`--autosync`, MCP `autosync`) runs only with `--bin`:
  it links the audio into the clips it syncs, changing media-pool items,
  so it touches only clips this run imported. It runs after the stacked
  timeline is built and read back, with waveform mode on the mix of every
  channel, keeping the camera's own audio and metadata. Its answer is
  never trusted alone: the stacked timeline is read again (a change there
  is a problem), the reference clip's properties before and after are
  compared, and a second timeline, `<name> (AutoSyncAudio) [auto]`, holds
  the synced reference so its items show where Resolve put the other
  file. The verdict is `agrees` (within a frame of the measured offset),
  `disagrees` (exit 1), or `unverifiable` (exit 2) when the other file is
  not an item of its own there.

Exit status: 0 built and read back (or planned with `--dry-run`); 2 built
with drift over the threshold or an unverifiable AutoSyncAudio; 1 refused
or failed.

### What is known and what is not

From the live spikes on Resolve 21.0.4.5 (pipeline note, spikes 13 to 22),
used here: `AppendToTimeline` returns a truthy value even when it places
nothing; `recordFrame` counts absolute timeline frames (a 25 fps timeline
starts at 90000 for 01:00:00:00); `startFrame`/`endFrame` count source
frames at the source's own rate, `endFrame` exclusive (Ep 002).

Not yet seen on a live Resolve; owed, in the sandbox, with Resolve open on
"RP Automation Sandbox":

1. Resolve was closed on 2026-09-29, so no stacked timeline has been
   built yet. Build one from the named dual-system clip (a camera and a
   recorder in one room; the sandbox manifest does not name one yet) and
   record each placement's read-back.
2. Whether a camera clip appended with no `mediaType` at `trackIndex` 1
   puts its audio on A1, and where a clip with more than two channels
   lands.
3. The `FPS` and `Frames` Resolve gives an audio-only clip (the project's
   rate, or its own), which the plan uses for its length.
4. `AutoSyncAudio`: the real values of the `AUDIO_SYNC_*` constants,
   whether it returns True, whether the synced audio shows on a timeline
   as an item of its own (the verification's premise; if not, every run
   reads `unverifiable`), and whether it changes a timeline that already
   holds the clip.
5. The residual on the rendered tracks: render the stacked timeline and
   measure the render's two channels with `sync-measure`, for the A7 check
   (+/-0.5 frame or better). A nudge below a frame, if ever wanted, is a
   hand step in Fairlight (untried).
6. A true acoustic dual-system pair has not been measured: the real pairs
   above are a call's own files and a call recorded at both ends. Its
   windows' distance from the drift line is the check on the 2 ms / tenth
   of a frame limit (a talker who moves between a lav and a camera mic
   changes the path by about 3 ms a metre).
7. Whether Resolve honours an audio stream's start time. OBS and phone
   files often start their audio 20 to 40 ms after the first video frame
   (ffprobe's `start_time`); the offset counts that. If Resolve plays
   audio sample 0 at video frame 0 instead, the stacked pair is out by
   that much (about a frame at 25 fps), and the read-back cannot see it,
   since it checks frames. Import a file whose audio `start_time` is not
   zero, read its audio item's placement against its video item, and
   record whether Resolve honours it.

## Trim review

Where an edit could be tightened, as a list of proposals: long silences
from the source audio, filler words and repeats from the mlx_whisper word
JSON. Nothing is cut, rippled or deleted, by the TSV or by the markers.

### The TSV

`resolve_workflow.py trim-review <manifest-or-source> [--words W] [--out
TSV] [--silence-db -45|auto] [--min-silence 0.8] [--tighten 1.2] [--cut
2.5] [--soft-fillers] [--no-audio]`, or the MCP `trim_review`. Offline.

Columns: `start, end, kind, text, confidence, suggestion`, then `clip,
span, source_start, source_end`. Over a cut manifest each row names its
clip and span and its times run along that clip's timeline (spans laid end
to end at the manifest's fps, as `cut` builds them), with the source time
beside them; over a whole source, times are source seconds.

| Kind | Marker | From | Suggestion |
|---|---|---|---|
| `silence` | Blue | 10 ms windows below -45 dBFS (or `auto`: the quietest tenth of the windows plus 10 dB, within -70 to -30) for at least 0.8 s | cut-candidate from 2.5 s, tighten from 1.2 s, keep below |
| `filler` | Yellow | um, uh, erm, er (high confidence); hmm, mm, mhm, ah (medium); with `--soft-fillers` also "like", "you know", "I mean" (low) | cut-candidate; keep for the low ones |
| `repeat` | Purple | a word or two said again back to back ("I I", "we were we were") | tighten; keep for an emphatic double ("very very", "no no", low) |
| `asr-loop` | Red | a phrase Whisper wrote three times or more (`cutlist.repetition_loops`) | keep: re-transcribe first; rows inside it are dropped |

A silence is high confidence when no word's midpoint falls inside it, and
medium when one does (Whisper heard something quiet there, or stretched a
word across the pause) or when there are no words.

What the words cannot give: Whisper large-v3-turbo leaves most fillers
out of its transcript, and its word times drift by about 0.2 s. A row says
where to look; the cut is made on the waveform, and a filler Whisper did
not write is not listed. On a 4-minute excerpt of a real call
(2026-09-29): 38 silences (21 high confidence, 17 medium; 9 suggest
tighten, the rest keep), 5 repeats, and no hard filler at all, since the
transcript held none; with `--soft-fillers` and `auto` (the call app's
noise gate reads as digital silence, so the threshold went to -70 dBFS),
19 silences and 17 soft fillers. The review took 0.15 s.

### Markers

`resolve_workflow.py trim-review <manifest-or-source> [--words W]
--markers --timeline "<name> [auto]" --project "<open project>"
--dry-run`, then again with `--plan-sha`; or the MCP
`trim_review_markers`.

- `[auto]` timelines only. The rows are computed for the stretches of
  the source the timeline plays: the items whose media is the source file
  (video tracks, or audio tracks when no video item uses it) map source
  seconds to timeline frames. An item that plays at another speed is
  skipped and reported.
- One marker per row, a colour per kind (table above), named for the row
  ("filler: um", "silence 2.4 s"), with its suggestion, confidence, text
  and source times in the note, and `rpresolve-trim-review:<n>` as custom
  data. Its duration covers the row.
- Resolve holds one marker per frame. A row whose frame already holds a
  marker is refused and listed (never moved or merged), and so is a
  second row on a frame an earlier one took.
- Each marker is read back with `GetMarkers` (colour, name, note,
  duration, custom data); the markers that were there before must be
  unchanged, and none may appear that was not planned. The timeline is
  made current while marking, then the current timeline, page and
  playhead are put back.
- Nothing is cut, rippled or deleted: the code calls `AddMarker` and
  reads, and the forbidden-call scan (any `Delete*`) covers it.

Owed live (Resolve was closed on 2026-09-29): run the markers on one
existing sandbox `[auto]` timeline (an `A5asis_*` or `SW002final_*` one)
and record whether `AddMarker` takes frames from the timeline's start (the
scripting README's `GetMarkers` example reads "timeline offset 96"; the
code assumes it, unlike `recordFrame`, which is absolute), whether it
works on a timeline that is not current, and how `GetMarkers` spells its
keys and durations.

### North register

The TSV is a new human-fed surface, fed once per episode by the cut run.
Kill criterion: six unfed weeks. If six weeks pass with no review TSV read
or acted on, the TSV retires and trim review keeps its markers only, with
a one-line note here saying when.

---

## Open Questions

- Do you have a preferred V-Log / V-Log L → Rec.709 LUT for the GH7 and GH5, or are you using the Panasonic-supplied ones?
- Is the GH7 shooting in Open Gate (5.7K) that needs crop/scale, or are you already shooting in a 4K mode?
- Do you want an outro card or is the intro sufficient for personal work?

---

*Version 1.5, 2026-09-29: sync (dual-system sound measured by FFT cross-correlation with drift and confidence, stacked on an `[auto]` timeline with every placement read back; AutoSyncAudio only on clips the run imported, checked against the measurement) and trim review (a proposals TSV and markers, nothing deleted; its kill criterion). Version 1.4, 2026-09-29: captions (auto captions by frame shape, Resolve's TTML sidecar to a zero-based .srt, the check's cue-time rule), the loop with the loudness fix as its standard last step, the Gamma 2.4 transfer tag read from a real render. Version 1.3, 2026-09-29: the Deliver section (destinations, names, queue, check, loudness fix). Version 1.2, 2026-08-17: Phase C automation shipped and audited; coverage table above reflects what's actually implemented vs. not scriptable.*
