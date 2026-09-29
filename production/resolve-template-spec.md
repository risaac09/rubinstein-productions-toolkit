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
- **Source:** Import .srt, apply style

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
8. Subtitles if needed (.srt import → branded style)
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
| Subtitle import | Partial — `add-subtitles` places an .srt on the timeline where the API allows it, but verify the result; it isn't guaranteed on every Resolve version |
| Subtitle style application | **Not scriptable.** The API has no entry point for subtitle font/color/position. This stays a manual Edit-page step, permanently — don't wait for it to get built. |
| Select-pulling | Out of scope by design. Choosing the best take is editorial judgment; the tool automates the container around it, not the cut. |

Two other hard API limits worth knowing before you file a bug against the
script:
- **No node creation.** `apply-lut --node 2` (this spec's Node 2, the
  Low Contrast PowerGrade) only works if that node already exists on the
  clip — add it in the Color page first. The scripting API cannot create
  color-page nodes.
- **Vertical export is resize-only.** The `story` preset changes canvas
  dimensions; it does not reframe subjects. Set per-clip Pan/Zoom manually
  before rendering vertical, or the crop will be arbitrary.

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
as 16 tools: reads of the open project, the offline checks (detect,
survey, measure, endcheck, selects, deliver_check), and additive writes
(ingest, cut, duplicate, grade onto `[auto]` timelines, queue a render
without starting it), each shown as a plan before it runs.

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
colour tags Rec.709 primaries and matrix with the Gamma 2.4 transfer
(Resolve's `ColorSpaceTag` "Rec.709" and `GammaTag` "Gamma 2.4"), limited
range (a `yuvj*` pixel format or a `pc` flag fails; an unflagged YUV
stream counts as limited, as decoders read it). An overlay
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
3. **Check.** `resolve_workflow.py deliver-check <file> --dest <key> --fps
   <timeline fps>` (or MCP `deliver_check`). Exit 0 all pass, 1 any fail,
   2 ffprobe or ffmpeg missing; `--json` for the whole result. The client
   master renders at the timeline's size, so pass `--size` to assert it.
4. **Fix loudness** when that is what failed:
   `resolve_workflow.py deliver-fix-loudness <file> --dest <key>`. A
   two-pass loudnorm (measure, then linear with the measured values) on
   the first audio stream, re-encoded to the destination's audio; video
   and every other stream copied, colour tags kept. It writes
   `<stem>.loudfix<ext>` beside the original, checks it under the
   original's name and confirms the video stream is bit-identical. With
   `--replace`, and only after that passes, the original moves to
   `~/.Trash/deliver-loudfix-<timestamp>/` and the fixed file takes its
   name. If loudnorm had to fall back from linear to dynamic mode (a linear
   gain would have broken the true-peak limit), it says so: listen before
   delivering.

### Not verified until a live queue and a real render

These are set from the scripting README and the config; none has been
seen in a file Resolve rendered with these settings yet.

- **Which transfer tag Resolve writes for "Gamma 2.4"** (bt709, that is
  1-1-1; unspecified, 1-2-1; or something else). This decides how Macs
  play a web upload: a file tagged 1-1-1 is shown with the Rec.709 camera
  curve, brighter in the shadows than the Gamma 2.4 grade, which is the
  "Rec.709-A" issue. The check reports the transfer it finds and asserts
  nothing until `deliver.color.expect.color_transfer` is set from a real
  render.
- The tag strings `Rec.709` and `Gamma 2.4`, the `AudioCodec` strings
  `aac` and `lpcm`, and the `DataBurnIn` string `None`. The queue assumes
  that `SetRenderSettings` returns False for a string Resolve does not
  accept, so that a required step fails and the job is not queued. That
  is an assumption until the sandbox step below confirms it. If Resolve
  instead returns True and keeps its previous value (such as "Same as
  Project"), the job queues with the wrong tags, the read-back lists
  `GammaTag` as unverified, and nothing downstream catches the transfer,
  which deliver-check only reports.
- The sidecar's name: the check expects `<stem>.srt` beside the file and
  names any near miss it finds. Whether the sidecar is SRT or WebVTT is not
  a scripting key. A burn-in or no-captions file fails the check when any
  caption file under its stem sits beside it.
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
and write down what Resolve did. None has been run yet.

1. **Invalid tag strings.** Call `SetRenderSettings({"GammaTag": "Gamma
   2.4 (not a tag)"})`, then the same for `ColorSpaceTag`, `AudioCodec`
   and `DataBurnIn`. Record whether each returns False. If any returns
   True, the queue's refusal on a required step does not protect that
   field, and the valid strings must be confirmed some other way (for
   example `SaveAsNewRenderPreset` and reading the preset back).
2. **Valid tag strings.** Queue `linkedin_16x9` on a short 16:9 sandbox
   timeline with `deliver-queue`, render it by hand, and run
   `deliver-check`. Record the transfer ffprobe reads for "Gamma 2.4" and
   set `deliver.color.expect.color_transfer` from it.
3. **Render mode.** Leave the Deliver page in Individual clips, queue a
   destination, and record `GetCurrentRenderMode` before and after,
   whether `GetRenderJobList` names the mode, and whether putting the mode
   back to Individual clips changes the job already queued (the format and
   codec restore rests on the same assumption).
4. **Job list fields.** Record which of `FrameRate`, `AudioSampleRate`,
   `AudioBitDepth`, `AudioCodec`, `ColorSpaceTag`, `GammaTag` and
   `DataBurnIn` `GetRenderJobList` reports, and how it writes each.
5. **Sidecar name.** Render a sidecar destination and record the caption
   file's name and format.

Remove the queued sandbox job after each step; never start a render from
a script.

---

## Open Questions

- Do you have a preferred V-Log / V-Log L → Rec.709 LUT for the GH7 and GH5, or are you using the Panasonic-supplied ones?
- Is the GH7 shooting in Open Gate (5.7K) that needs crop/scale, or are you already shooting in a 4K mode?
- Do you want an outro card or is the intro sufficient for personal work?

---

*Version 1.3, 2026-09-29: the Deliver section (destinations, names, queue, check, loudness fix). Version 1.2, 2026-08-17: Phase C automation shipped and audited; coverage table above reflects what's actually implemented vs. not scriptable.*
