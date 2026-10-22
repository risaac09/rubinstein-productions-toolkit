# iPad lane: a shoot from card to delivery with no Mac

> **Status:** draft, 2026-10-22. Desk research plus tests of the toolkit's scripts on macOS and Linux. Nothing here has been run on an iPad. The [one-hour probe](#the-one-hour-on-device-probe) is how each open row gets settled.

What an iPad can do for each stage of a shoot when no Mac is in reach, which app does it best, what Claude can do there, and what this toolkit provides.

---

## The answer in brief

- A person can run the whole chain by hand on an iPad: DaVinci Resolve for iPad (Photo, Cut, Color and Deliver pages), Final Cut Pro for iPad, the Files app, and a-Shell for ffmpeg checks, hashes and loudness.
- Claude cannot operate any of those apps. Resolve for iPad has no scripting API and no MCP. No Shortcuts actions for it were found. Claude has no way to type into a-Shell.
- With no Mac, Claude has two roles. In chat it reads images and text and writes files (`.cube`, EDL or FCPXML, SRT, `sh` scripts). In a Claude Code cloud session it computes on small derivatives kept in a GitHub repository. Remote Control adds a third role only when a computer other than the iPad is switched on and reachable.
- What the toolkit adds is files that cross that gap: a LUT pack, timeline interchange files, and the probe with its recipes. Each of them waits on the on-device probe.

## How the marks work

| Mark | Meaning |
|---|---|
| VERIFIED | A vendor source (feature sheet, release notes, help page, App Store listing), or measured by this toolkit's own tests on desktop ffmpeg |
| UNVERIFIED | A third-party write-up, a forum post or issue report, an inference, or vendor sources that disagree |
| probe *n* | Settled by item *n* of [the probe](#the-one-hour-on-device-probe) |

The Resolve rows come from Blackmagic's "DaVinci Resolve 21.1 Studio and iPad Features" sheet (September 2026). The sheet itself says the iPad app changes faster than the sheet, so a "No" there can be out of date.

## Verdict per task

| Task | Verdict | Best on the iPad | Claude's role | Marks |
|---|---|---|---|---|
| Ingest | Feasible, by hand | Files with a USB-C card reader or SSD; `verify-copy.sh` in a-Shell for the manifest and the copy count | Writes the commands; reads the recipe's output | Card readers and SSDs in Files: press reports, UNVERIFIED for your hardware, probe 4. Hashing in a-Shell: probe 2. Throughput and card access from a-Shell: probe 4 |
| Cull | Stills feasible, video partial | Resolve Photo page or Apple Photos for ratings and albums; Final Cut Pro for iPad Transcript and Visual Search for footage | Ranks contact sheets (`sheet.sh`) and reads transcripts; takes no RAW and no video | Claude upload limits VERIFIED. Resolve IntelliSearch is a Studio feature on iPad VERIFIED. Sheet speed on device: probe 3 |
| RAW develop | Feasible, by hand | Resolve Photo page (nodes, curves, LUTs on stills); Photomator or Pixelmator Pro on Apple's RAW engine | Advises settings from exported JPEGs; writes a starting `.cube` | Apple's iPadOS 27 RAW list names the Panasonic GH7 and S5 II (DC-S5M2) VERIFIED. Resolve decoding those RW2 files UNVERIFIED, probe 8. Full-resolution photo export needing Studio UNVERIFIED |
| Retouch | Light work feasible | Pixelmator Pro for iPad (Apple Creator Studio); Resolve Photo page with Studio (Blemish Removal, Face Refinement; Face Reshaper on M-series only) | Critiques before and after exports; does no pixel work | Resolve FX by chip VERIFIED. Creator Studio's iPad apps from press reports, UNVERIFIED |
| Edit | Feasible, by hand | Final Cut Pro for iPad for fast cutting; Resolve Cut page when grade and delivery stay in Resolve | Builds a rough cut as EDL, FCPXML or OTIO in a cloud session, from a transcript or shot log | No Edit, Fusion or Fairlight page in Resolve for iPad VERIFIED. FCPXML import and export in the 21.x App Store notes, seen only through a summarising fetch: UNVERIFIED. Import of the toolkit's files: probe 7 |
| Color | Feasible; the iPad's strongest stage | Resolve Color page: nodes, qualifiers, windows, trackers; Studio adds DCTL, Film Look Creator, Magic Mask on M-series; Micro Color Panel supported | Writes `.cube` files (camera transforms, looks); reads scope screenshots and stills and returns numbers per node; applies nothing | Pages, panels and Studio rows VERIFIED. LUT import route UNVERIFIED, probe 5. V-Log input transform: probe 6. Scope types: probe 6 |
| Sound | Partial | Resolve Cut-page mixer with Dialogue Leveler (free) and Voice Isolation (Studio); Logic Pro for iPad for music (Stem Splitter, Mastering Assistant: VERIFIED, Apple's notes); `loudness.sh` for the delivery target | Writes ffmpeg chains and reads the loudness JSON; cannot listen, so the ear check stays human | No Fairlight effects, buses or automation on iPad VERIFIED. loudnorm in a-Shell: probe 2. Ferrite's current feature set UNVERIFIED |
| Captions | Feasible with limits | Final Cut Pro for iPad Generate Captions (US English, iPadOS 26 or later); Resolve Studio transcription on M-series | Proofreads, re-breaks and translates SRT text and returns a corrected file | FCP captions VERIFIED. Resolve transcription on M-series with Studio VERIFIED. Resolve's timeline subtitle track is "No" on iPad, so an SRT export from Resolve: probe 9 |
| Deliver | Feasible | Resolve Deliver page (H.264, H.265, and ProRes on M-series, all hardware encoded; share to installed apps); FCP for iPad share | Chooses bitrate and container; writes the ffmpeg line for web encodes | Resolve codecs and sharing VERIFIED. a-Shell encoders: probe 2 |
| QC | Partial | Scopes in Resolve or FCP; the probe's rows and `loudness.sh` output pasted into chat | Reads ffprobe and loudness text and scope screenshots | The toolkit's automated checks (`qc`, `deliver_check`, `endcheck`) need a host with full ffmpeg; whether its Python runs in a-Shell: probe 2 |

## Resolve for iPad, from the vendor sheet

VERIFIED from the September 2026 sheet unless marked. "Studio" means the in-app purchase.

- **Pages:** Photo, Cut, Color and Deliver. A trick from 2022 that revealed the other pages was called a bug by Blackmagic; nothing confirms it in 21.x. Do not build on it (UNVERIFIED).
- **Automation:** Scripting API No. AI Assistants (MCP) No. Workflow integration No. Third-party effects, plugins and encode plugins No. Remote grading and rendering No. App menu access No.
- **Timeline:** compound clips, keyframe curves, subtitles track, sub-clips, multicam clip creation, optimized media, render in place, power and smart bins, relink disk search: all No.
- **Color:** Micro Color Panel and Speed Editor Yes. DCTL, ARRI CDL and LUT in a node, Film Look Creator, HDR scopes: Studio. MultiMaster trim pass and CDL export: No. Node cleanup and combiners: No.
- **Audio:** audio mixer, voiceover, clip markers Yes. Dialogue Leveler Yes (free). Voice Isolation Studio. Dialogue Separator and Music Remixer Studio on M-series only. Fairlight effects, buses, ADR: No.
- **AI by chip:** Magic Mask, UltraNR, IntelliScript, transcription and subtitles from audio are Studio on M-series and absent on A-series. Relight needs an iPad Pro with 16 GB of RAM.
- **Codecs:** Blackmagic RAW, H.264, H.265, HEIF, images and audio; ProRes on M-series. Every supported format is hardware decoded and encoded. Image sequences as video: No.
- **Projects:** library local, network (PostgreSQL) or Blackmagic Cloud; collaboration Yes. The App Store listing says iPad projects and archives open in desktop Resolve 20; whether a 21.x iPad project opens in desktop 21.x is not stated (UNVERIFIED).
- **Files:** access is "subject to iPad access restrictions" and needs the user's permission, which affects sidecars and image sequences. A forum report has Resolve dropping an SSD mid-import while Files kept it mounted (UNVERIFIED).

## Contradictions, each turned into a probe item

| What disagrees | Sources | Settled by |
|---|---|---|
| Is a-Shell's ffmpeg a native arm64 build with VideoToolbox, or a WebAssembly command (software only, slow)? One reading of the a-Shell issues and release notes says native, built `--disable-gpl` with VideoToolbox; the a-Shell commands README lists a WebAssembly ffmpeg with libx264 installed by `pkg install`. Both may exist side by side. | a-Shell repo, issues and release notes (UNVERIFIED as a whole) | Probe 2: the `encoder` rows for h264_videotoolbox and libx264, the VideoToolbox smoke run, and the ffmpeg path. Probe 3: decode speed on a real clip |
| Has a-Shell's Python got numpy? The maintainer says the full app bundles numpy; pip installs pure-Python packages only, so scipy and OpenCV are absent. | a-Shell repo and issues (UNVERIFIED) | Probe 2: the `python` rows |
| Which Blackmagic sheet? One research pass used the August 2024 (Resolve 19) sheet, others the September 2026 (21.1) sheet. This page uses the 2026 sheet. Where third-party 21.0 write-ups say keyframe curves, Bezier keyframes and MultiMaster arrived on iPad, the 2026 sheet still says No. | Blackmagic sheets (VERIFIED); third-party 21.0 write-ups (UNVERIFIED) | Probe 10: look for keyframe curves and MultiMaster in the app |
| "Background rendering and analysis: No" on the sheet, while the 20.3.1 release notes mention background render on iPadOS 26 with M4 or later. Probably two different features. | Sheet and App Store notes (both VERIFIED, unreconciled) | Probe 10 |
| "FCPXML 12." FCPXML file versions are 1.x; "12" names Final Cut Pro 12 compatibility. Which 1.x version the iPad app writes, and whether it reads the toolkit's FCPXML 1.10, is open. EDL, AAF and OTIO import on iPad appear in no source. | 21.x App Store notes, seen only through a summarising fetch (UNVERIFIED) | Probe 7: import each file; export one FCPXML from the iPad and read its `version` attribute |
| LUT import route: one tutorial says copy `.cube` files to Files > On My iPad > DaVinci Resolve > LUT, then Settings > Color Management > Update Lists; another post gives a different route. The sheet lists "LUT in a node" under Studio. | Tutorials (UNVERIFIED); sheet (VERIFIED) | Probe 5, with and without Studio |
| Remote Control needs a Mac? One research pass said so. Claude Code's documentation lists macOS, Linux and Windows hosts. The docs are right, but the host still cannot be the iPad. | Claude Code docs (VERIFIED) | Not a probe item: a precondition. No second computer, no Remote Control |
| Does iPadOS 27 read HFS+? Press reports say iPadOS 18 and later read APFS, exFAT and FAT but not HFS+. | heise, Satechi (UNVERIFIED) | Probe 11: plug in the SSD you will use |

## The one-hour on-device probe

Do these in order. The first six are the hour; the rest are worth doing when time allows. Paste every result into one chat so it becomes a single trial note.

| # | Check | Command or tap path | What it unblocks | Min |
|---|---|---|---|---|
| 1 | Which iPad, which apps | Settings > General > About (model, iPadOS version, free storage). Resolve: is Studio purchased? a-Shell: version | Every chip-gated and Studio-gated row: transcription, Magic Mask, UltraNR, ProRes, Relight | 3 |
| 2 | What a-Shell can do | Copy `tools/ipad-probe` into Files > On My iPad > a-Shell. In a-Shell: `cd ~/Documents/ipad-probe`, `sh probe.sh --self-test`, then `sh probe.sh > probe.md` and share `probe.md` to the chat | Native or WebAssembly ffmpeg; lut3d, loudnorm, ebur128, scdet, drawtext; encoders; numpy; whether the toolkit's Python can call ffprobe (`subprocess` and `isfile` rows) | 5 |
| 3 | Is 4K practical in a-Shell | Copy one 4K camera clip into a-Shell's folder; `sh probe.sh CLIP.MOV > clip.md` | Decode speed in software and with VideoToolbox: whether proxies and sheets get made on the iPad or wait for a host | 5 |
| 4 | The recipes on real media | `pickFolder` to a card, then `sh recipes/verify-copy.sh CARD DEST` to an SSD folder; `sh recipes/proxy.sh CLIP.MOV p.mp4`; `sh recipes/loudness.sh CLIP.MOV l.mov`; `sh recipes/sheet.sh CLIP.MOV s.jpg` | Hashing route and MB/s from a card; whether a-Shell can reach a card and an SSD at once; real proxy and loudness timings | 15 |
| 5 | LUT import | Unzip a LUT pack (`lutlab pack`) into Files > On My iPad > DaVinci Resolve > LUT; Resolve: Settings > Color Management > Update Lists; Color page: put `check/cast-magenta.cube` on a node over grey | The import route; whether LUTs need Studio; whether 65-point files are listed | 8 |
| 6 | Colour management and scopes | Color page on a V-Log clip: look for an input colour space per clip, or a Color Space Transform effect; open the scopes menu | Whether the camera-transform cubes are the only V-Log route (Lumix V-Log files are tagged Rec.709, so Resolve cannot learn V-Log from the tags); which scopes exist | 8 |
| 7 | Timeline interchange | Import an EDL, an FCPXML and an OTIO written by `rpresolve.interchange`; export one FCPXML from the iPad | Which formats import, whether media relinks by name, which FCPXML version the iPad writes | 10 |
| 8 | RAW on the Photo page | Open one RAW from each camera body you shoot | Whether stills develop in Resolve or in an Apple-engine app | 3 |
| 9 | Captions out | Studio transcription on a 30 s clip; look for an SRT export in Deliver. FCP: Generate Captions, then share and see what file comes out | Where a caption file comes from on the iPad | 5 |
| 10 | Features the sources disagree on | Look for keyframe curves, MultiMaster, background render in Resolve; Shortcuts app > Apps: does DaVinci Resolve offer actions? | Corrects the vendor-sheet rows above | 3 |
| 11 | Storage | Copy a 10 GB folder Files to SSD and time it; unplug the SSD with Resolve open on media from it | Real throughput; whether to import to On My iPad before editing | 10 |
| 12 | Claude on the iPad | Attach a JPEG sheet and a HEIC from Photos to a chat; in a claude.ai/code cloud session, attach one image | Whether images reach a cloud session from the iPad (documented only for Remote Control) | 3 |

## Claude on the iPad

- **Chat in the Claude app.** VERIFIED: uploads up to 500 MB per file and 20 files per chat; images JPEG, PNG, GIF and WebP up to 8000 x 8000 px (HEIC is not listed); video and audio are not supported uploads, so send contact sheets, scope screenshots and transcripts. With code execution on, it writes files (Python, up to 30 MB each). Remote MCP connectors already added on claude.ai work on mobile; local MCP servers, such as this toolkit's Resolve server, do not.
- **A Claude Code cloud session** from claude.ai/code or the app's Code tab. VERIFIED: an Ubuntu VM (about 4 vCPU, 16 GB RAM, 30 GB disk) that clones a GitHub repository and returns a branch or pull request; it keeps running while the iPad sleeps. ffmpeg is not preinstalled; a setup script can `apt install` it (UNVERIFIED that it finishes inside the setup window). It cannot see the iPad's files or the local network. GitHub refuses files over 100 MiB, so originals do not fit; proxies, stills, stems and transcripts do. Session transcripts are stored by Anthropic, so client media goes there only with permission.
- **Remote Control.** VERIFIED: drives a Claude Code session on a computer you own (macOS, Linux or Windows) from the app or claude.ai/code; files and local tools stay on that computer. It needs that computer on and reachable. The iPad cannot be the host.
- **What none of them do:** operate Resolve, Final Cut Pro or a-Shell, or listen. A person runs every command and every tap.

## Storage rules

- **No iCloud Drive for working media.** Files offers iCloud Drive first. Choose On My iPad or an external SSD instead, so large media is not synced, offloaded by Optimize Storage, or held by a third party.
- **Working media lives on On My iPad or an external SSD.** exFAT for an SSD that also meets other computers (UNVERIFIED, probe 11). If Resolve works from an SSD, keep a copy of the project archive on the iPad too.
- **Format a card only after a second verified copy, on a different device.** `verify-copy.sh` hashes the card once, checks every copy against that, and counts. A copy in On My iPad and one on an SSD are two devices; two folders on one SSD are not.
- **Cloud is third-party storage.** Blackmagic Cloud, claude.ai uploads, cloud sessions and GitHub hold whatever is put there. Client media goes only where the client has agreed; prefer derivatives (sheets, small proxies, transcripts) over originals.

## What the toolkit provides for the iPad

- **[`tools/ipad-probe`](../tools/ipad-probe/README.md):** the probe and four recipes (verified copy, proxy, two-pass loudness, contact sheet), POSIX `sh` that runs under a-Shell's `dash`.
- **A LUT pack** from `lutlab pack` ([lutlab cst, lutstats and pack](../production/lutlab-cst.md)): camera-to-Rec.709 transforms for footage tagged Rec.709, a cast cube that proves the import route, a README with the Files path, and checksums. Made on a computer; copied to the iPad by a person.
- **Timeline interchange files** from `python3 -m rpresolve.interchange` ([interchange](../production/interchange.md)): EDL, FCPXML 1.10 and OTIO from a cut manifest, made on a Mac or in a cloud session (there, point `RPRESOLVE_FFPROBE` at the installed ffprobe; rpresolve looks only at the Homebrew path). Its acceptance table records what the iPad imports.
- **Decision sheets and the review loop** ([media review loop](../production/media-review-loop.md)): one choice per message, answered with a letter from the phone or iPad.

What it does not provide: an iPad app, any way to drive Resolve for iPad, or the toolkit's Python checks inside a-Shell. Those checks look for ffmpeg as a file (`delivercheck` tests `os.path.isfile`), face boxes need macOS Vision through `swiftc`, and transcription uses mlx-whisper, which is macOS-only. Probe 2's `python` rows say how far that gap is.

## Sources

- Blackmagic Design, [DaVinci Resolve 21.1 Studio and iPad Features](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_Studio_21_Features.pdf), September 2026.
- [DaVinci Resolve for iPad](https://apps.apple.com/us/app/davinci-resolve-for-ipad/id1581363826) on the App Store, version 21.1.1, and its version history.
- Apple, [Final Cut Pro for iPad release notes](https://support.apple.com/en-us/102731); [RAW formats supported by iPadOS 27](https://support.apple.com/en-us/127493); [Logic Pro for iPad release notes](https://support.apple.com/en-us/101628).
- [a-Shell](https://github.com/holzschu/a-Shell) repository and issues, and its [App Store listing](https://apps.apple.com/us/app/a-shell/id1473805438).
- Anthropic, [what you can upload to Claude](https://support.claude.com/en/articles/8241126-what-kinds-of-documents-can-i-upload-to-claude-ai); Claude Code [on the web](https://code.claude.com/docs/en/claude-code-on-the-web), [cloud environments](https://code.claude.com/docs/en/cloud-environments) and [Remote Control](https://code.claude.com/docs/en/remote-control).
- GitHub, [about large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
- LUT import route: [Epic Tutorials](https://epictutorials.com/blogs/articles/how-to-import-luts-into-davinci-resolve-on-ipad) (third party).
