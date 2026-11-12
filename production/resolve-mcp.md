# The Resolve MCP server

**Status:** operational.

`resolve_mcp.py` is a Model Context Protocol server over stdio that lets an
MCP client (Claude Code here) drive DaVinci Resolve through the `rpresolve`
library beside it. It is the only way this toolkit writes to Resolve:
`resolve_workflow.py` keeps read-only and offline commands only (see
[The command-line script](#the-command-line-script)). The server is
standard-library Python, with no SDK and nothing to install, and runs on
macOS's `/usr/bin/python3` (3.9), where Resolve's scripting module, numpy and
OpenCV live.

Resolve is edited live by a person while the server runs. Everything below
follows from that: the server reads freely, writes only in addition to what
exists, shows a plan before any write, and puts the UI back the way it
found it.

## Register it

```bash
claude mcp add --scope user --transport stdio resolve \
  -e "RESOLVE_SCRIPT_API=/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting" \
  -e "RESOLVE_SCRIPT_LIB=/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so" \
  -- /usr/bin/python3 -X faulthandler /path/to/rubinstein-productions-toolkit/production/resolve_mcp.py
```

Point it at a checkout that stays current (a clone you pull, not a feature
branch). `claude mcp get resolve` should show it connected, and in a new
session `/mcp` lists it. Resolve does not need to be running for the server
to start: it connects on the first tool that needs Resolve, and reconnects
if Resolve was restarted. Resolve Studio's Preferences > System > General >
External scripting using must be set to Local.

Delivery destinations come from `production/resolve-config.json`. To lay a
private overlay over it (a destination's `target_dir` on a share, a changed
tolerance), keep the overlay outside any repository and add
`-e "RPRESOLVE_CONFIG=/path/outside/any/repo/deliver-overlay.json"` to the
registration. The overlay's `deliver` and `destinations` lie field by
field over `resolve-config.json`'s, so an edit there still holds under an
overlay that only adds a `target_dir`. A named overlay that is missing,
does not parse, or is not a JSON object stops the tool with an error; it
is never quietly replaced by the defaults.

Whether the write tools sit on "ask" in Claude Code's permissions is the
operator's call: each dry-runs first, needs its plan_sha, journals every
run and never starts a render, so a dialog per write adds little. Their
results name media files, people and transcript text, so treat anything
they return as private.

## Tools

Start with `resolve_status`: it says which project is open, and every write
tool must name that project. There are 43 tools: 5 reads, 19 offline tools
(one of them, `deliver_captions`, writes a file beside a render) and 19
writes.

| Tool | Kind | What it does |
|---|---|---|
| `resolve_status` | read | Toolkit commit on disk and the commit the server loaded (`stale` when they differ: restart it), dirty flag, one line per mounted media volume, whether Resolve is reachable, the open project's name, unique id, colour science, timelines and render queue, and which helper tools are installed. Works with Resolve closed. |
| `list_timelines` | read | Every timeline: index, name, unique id, frame rate, start and end frames, track counts, which is current, which end in ` [auto]`. |
| `timeline_items` | read | One timeline's items per track: timeline and source frames, media file, input colour space and data level, transform, and grade (version, colour group, nodes with labels, LUTs and tools). `index` is the raw track position, a dissolve counted; a video row's `clip` is the clip number `apply_effect` and `vertical_apply` count by, null for a dissolve (`T29`). Reads a timeline without making it current. |
| `media_pool` | read | The folder tree (capped at 300 folders) and one folder's clips with their properties. |
| `render_queue_status` | read | Every render job with its status, whether a render is running, and the Deliver page's format and codec. |
| `detect` | offline | Camera and picture profile per media file, with the proposed Input Color Space and Data Level. |
| `survey` | offline | The read-only survey of every project from Resolve's databases on disk (needs Python 3.14). |
| `measure` | offline | Luma (p1, p5, p50, p95, p99), clipping, legal range, highlight tail slope, shadow classes (readable, sculpted, lost), skin tone and per-face lighting ratio in stops in faces found by macOS Vision, and camera match for a video file, with the transfer named. The CLI's `--delivery` adds the master-against-encode readable-shadow row. |
| `endcheck` | offline | For each span in a cut manifest: does the out-point land in a pause, on the intended words, inside the approved text. |
| `selects` | offline | Proposed spans from a word-level transcript that stay inside an approved text. |
| `reframe_plan` | offline | A static crop per span for the 9:16, 1:1, 4:5 or 16:9 versions or the master shape (`aspects`; `master` needs `master_size`), or, with `items`, a framing plan for a timeline's clips that `reframe_apply` writes (see Framing below). Each crop is centred on the subject's face: frames sampled across each span, faces from macOS Vision tracked across the samples (the one in the most samples, then the larger; or the one nearest `speaker_x` and `speaker_y`; or the largest with `prefer`), the crop kept inside the picture area (black bars trimmed, a dark scene kept whole), and every sampled face box (10% margin, clipped to the picture) checked inside it. With no scale on the clip the planner derives the widest scale that fills the frame for the source and the aspect (a zoom of about 4 for a 5952x2646 source into 9:16); `allow_bars` starts from the Ep 002 punch-in (2.25) instead and keeps it when it holds the face, named `bars`. A crop passes only when at least `min_checked` (default half, never under 3) of the span's samples had a checked face; else the span is manual. `subject_region` and `min_face_height` leave an audience out. Footage with no speech takes a manifest with `"speech": false` and only `source_path` and `clips`. A span with no face gets no crop. The crop keeps the whole head and room above it (Framing). Writes a manifest copy with per-span reframes (what `cut` reads) and a TSV and JSON report beside it. |
| `deliver_check` | offline | A rendered file against its delivery destination: name rule (the destination's: clip, master or montage), folder (its destination's subfolder), container, codec, size, exact fps, pixel format, colour tags and range, a `log_flat` row that FAILs a picture whose sampled frames (five, by ffmpeg `signalstats`) never reach near black or near white, the look of a log render carrying bt709 tags ([T30](resolve-traps.md); a heuristic, skipped for a log, HLG or PQ transfer tag, and a deliberately flat look also fails it), audio, captions (a sidecar `.srt` whose cues sit inside the video), loudness and true peak, each PASS, FAIL or SKIP with found and expected. A destination with `tail_fade` true (`social_9x16_music`), or a call with `expect_fade` (`--expect-fade`), adds a `tail` row: the 100 ms ending at the last video frame (not the file's end, where the audio's codec padding sits) at least 12 dB below the window 3 s to 1.5 s before that frame, with the window named in the row (FAIL too when the sound stops before the picture does), SKIP under 4 s, with no audio or with `loudness` false. The name row is judged on the file's own name (live-tested 2026-11-11), so a file renamed after the render is judged on the new name; the tool takes no alternative name. To check a planned name before rendering, read the `output` and `CustomName` a `queue_render` dry run returns (the same `name_problems` rule backs both), or give the CLI or module a stand-in file of that name in the destination's subfolder. |
| `deliver_captions` | offline, writes a file | Resolve's caption sidecar beside a render (`<stem>_<track>.ttml`, timed from the timeline's timecode) to the zero-based `<stem>.srt` a platform reads: the file's start timecode taken off, every cue checked against the video's length, the `.srt` read back, the TTML moved to the Trash. Dry run first. |
| `sync_measure` | offline | Where one recording sits against another from the sound both heard (a camera and a recorder, or two cameras): the offset in seconds and in frames with the half-frame residual of frame placement, clock drift (ms per minute, ppm, over the overlap, and the retime that would cancel it), and each window's normalized correlation and peak ratio. A weak or inconsistent result is not called a match. |
| `trim_review` | offline | Long silences from the source audio, fillers, repeats and Whisper loops from the word JSON, as proposals (keep, tighten, cut-candidate) in a TSV. Over a cut manifest the times run along each clip. Nothing is cut. |
| `shoot_table` | offline | One CSV row per clip of a shoot: camera, start, duration, fps, size, profile, lens and audio, with long-take, VFR, off-speed, weak-audio and unexpected-camera flags, a `clock_base` per row, a per-camera `camera_offset` for a clock set wrong, and a capture-audio audit (streams, noise floor, clipping, dead channel, dual mono, room tone). |
| `qc` | offline | One FAIL-first QC table for a rendered file: black, freeze, scene cuts, loudness range, silence, phone-speaker proxy, sha256, and with `destination` deliver_check's rows (plus social fps and safe zone for a social one). With `words`, `stems` or `shots`, speech against the rest, stems summed against the mix, and true peak and silence at each cut. WARN rows never fail the table. |
| `pipeline_proof` | offline | A test render against a reference render: luma and chroma shift, range, level shift, PSNR, SSIM and VMAF (where ffmpeg has libvmaf), PASS or FAIL per row. Needs numpy. |
| `deliver_manifest` | offline | A delivery folder's `manifest.md` and `checksums.txt` as a preview, or with `verify` a re-check of `checksums.txt` against the files. Writes nothing; the CLI's `deliver-manifest` writes the two files. |
| `perceive` | offline | One frame as scopes, a Lab palette and numbers (faces, skin, lighting ratio), with an optional reference, as a packet folder outside any repository. Needs numpy. |
| `audio_report` | offline | Loudness over time, spectrogram, peaks, clipping, clicks, silences and octave balance of one audio stream: `audio-report.json` and two PNGs in a folder outside any repository, flags in the reply. Needs numpy. |
| `ograf_template_check` | offline | The installed OGraf templates against the toolkit's copies, by sha256: installed, missing or stale per file, with a restart notice when a file went in after Resolve started. Reads only; never copies. |
| `overlay_check` | offline | A graphic master rendered with alpha: tags, frame rate, clear and opaque frames, edge fringe and brand colour at named pixels, PASS, FAIL or SKIP per row. Needs numpy. |
| `edit_methods` | offline | The edit methods in `production/edit-methods/` (reusable social edit recipes, one timed slot per shot), each checked against the method format: id, fits tags, aspect, length, slot count; with `id` or `path`, one method in full or why it is not sound. See [edit-methods.md](edit-methods.md). |
| `ingest` | write | Import media under camera bins and tag Input Color Space and Data Level, reading each tag back. Skips files already in the pool, and derived files (renders and exports), which are not tagged (live-tested 2026-11-11, [T30](resolve-traps.md)). |
| `cut` | write | Build `<prefix>_<clip> [auto]` timelines and their 9:16 and 1:1 versions (`aspects`, default 9:16) from a cut manifest, gated by endcheck. Each version's items take their span's crop from `reframe_plan` (else the clip's `face_x`), every transform read back. A version with no reframe is not built and is named UNREFRAMED, nor is one whose input scaling is not scaleToFit or scaleToCrop (REFUSED); one whose picture does not fill the frame is built and named BARS. A timeline a failed build leaves behind is named in `left_behind` and in the journal. Each timeline is decided on its own: one that exists is skipped, and a missing version beside an existing 16:9 is built from it while its items still match the manifest's spans. The operator's exclude list can be passed as `exclude` (absolute path; JSON with a `takes` list of file stems, names or path suffixes, the same file and matching as `assemble_timeline`): a manifest in which a span of a clip asked for plays from a source on it is refused in the dry run and the real run, before endcheck and before the sources are hashed; a file with no `takes` list is refused; the file's hash is part of the `plan_sha`; and the result carries `exclude` (`path`, `takes`, `matches`) or null. With no list the result says so: `exclude` is null and `exclude_note` reads `no exclude list given: nothing was checked for excluded takes`, in the summary too. A cut manifest has no music, so only span sources are checked. |
| `duplicate_timeline_auto` | write | Copy a timeline to a new ` [auto]` name and compare every item with the origin. |
| `set_current_timeline` | write | Make an ` [auto]` timeline the project's current one, read back, so the frame-reading tools (`get_image`, `perceive` on a render) can see it before its render. Returns `previous`, `previous_unique_id` and `current`; `restore_to` puts the operator's own timeline back. It is the one exception to [auto]-only: it accepts any timeline of the project, by unique id (use this when two share a name) or name, so it can make a non-[auto] timeline current; it is a view switch and changes nothing in either timeline. With no current timeline, `previous` is null and there is nothing to restore. Changes nothing else: no page switch, no playhead move. |
| `set_color_management` | write | Set the colour-management Project Settings `ingest` needs (preset `managed`: `colorScienceMode` `davinciYRGBColorManagedv2`) on the open project, each value read back and every key read again after. Refuses unless the project is fresh: no timelines and no clips in the media pool. Never creates, loads or saves a project; a person makes the project in Resolve. |
| `timeline_from_clips` | write | A new ` [auto]` timeline from media-pool clips one after another: the clips directly in a bin, or clips named by unique id, path or name, in name, path or given order (natural order, so P2 comes before P10). Picture only, on V1, following the project's frame rate and size; a clip at another rate is conformed by Resolve to real time and the plan says so. Every item is read back: the clip, its first source frame, its length, no gap or overlap. The timeline goes into the media-pool bin open in Resolve (`lands_in_bin` says which). |
| `import_timeline` | write | An FCPXML file (what `rpresolve.interchange` and `reel` write) as a new ` [auto]` timeline through Resolve's `ImportTimelineFromFile`, its source clips imported into the bin open in Resolve. The plan lists what the file says (`rpresolve.fcpxmlread`): every clip with its lane, record position and length, the transitions, and media missing on this machine. After the real run every video clip is read back at its record position and length on some video track (Resolve decides which track a lane lands on) and at its ZoomX where the file set a static scale, and every transition at its position and length as the transition item Resolve placed, naming the kind (a Dip to Color imports as a Cross Dissolve). Keyframed transforms and retimes are not read back; see [FCPXML import](#fcpxml-import). A timeline that does not read back is left in place and named under `left_behind`. The operator's exclude list can be passed as `exclude` (absolute path; the same file and matching as `assemble_timeline`): a file with a clip, audio or video, that plays from a file on it is refused in the dry run and the real run, offline media included; the file's hash is part of the `plan_sha`, and the result carries `exclude` (`path`, `takes`, `matches`) or null. With no list the result says so: `exclude` is null and `exclude_note` reads `no exclude list given: nothing was checked for excluded takes`, in the summary too, whether the run imported or failed its read-back. An event with no file path (a ref-clip into a compound, or an asset whose `src` is relative) cannot be matched to the list: with a list given, `unchecked` names each one (`name`, `record`, `why`), `warnings` and the summary say `N event(s) could not be matched to a file and were not checked`, and the file still imports. |
| `apply_grade` | write, destructive | A LUT on one node, or a `.drx` still checked against its label manifest, on an ` [auto]` timeline's items. |
| `apply_effect` | write | A Fusion macro from `production/fusion-macros/` ([fusion-macros.md](fusion-macros.md)) on items of one track of an ` [auto]` timeline: a new comp named `rp-fx <effect>` per item, the macro's tools built in it by `AddTool` with their inputs and keyframes, wired between MediaIn1 and MediaOut1, and read back (every tool, keyframes on the macro's frames, no handle overshoot between keys, the wiring). An item that already has a Fusion comp is refused, since `AddFusionComp` makes the new comp the one that renders, unless every comp it has is an emptied `rp-fx ...` comp (only MediaIn1 and MediaOut1, `T9`): the macro is then built into that comp, which is loaded as the active one and renamed, the plan row has `reuse` (the comp's name) and the result row `reused` (live-tested 2026-11-11, [T9](resolve-traps.md)). The dry run's summary line still reads "would add a Fusion comp" for such an item and does not mention the `person-matte-text` stand-in matte; read each target's `reuse` field. The real run does not itself prove the reused comp is the one that renders; the check is one comp on the item and a rendered frame. A comp with tools, or one not named `rp-fx ...`, is still refused. Otherwise nothing that existed changes. `dip-to-black` is moved to end on the item's last frame. Items are numbered as `vertical_apply` numbers them: clips only, from 1, with a dissolve (a track item that has no media pool clip, `T29`) not counted; a title, generator or compound clip has none either and is not counted. `apply_grade`, `stabilize` and `reframe_apply` still take raw track positions (`timeline_items` `index`). `spans` takes the manifest's own 0-based span indices instead (span `i` is clip `i + 1`); giving both is refused. A clip number or span past the clip count is refused with the clip count and the raw item count (live-tested 2026-11-11). Every track item that is not counted (a dissolve, title, generator or compound clip) is listed in the result's `skipped`, plan and real run alike: `track`, the raw 1-based `position`, `name` and `reason`, whatever `items` or `spans` asked for, and the dry run's summary says "N item(s) skipped" when there are any (a track with none gets an empty list and no mention). Skipping never renumbers: clip n is the nth item with a media pool clip, as the manifest's spans count. An offline clip is believed to keep its media pool item, so it is counted and not reported in `skipped` (believed, not live-tested; nothing here detects offline). `reason` is one fixed text for every row, because the item API this module uses gives no kind: a dissolve, title, generator and compound clip read alike, and `name` is the clue. A refusal for a clip count that does not match also names the skipped items by track and raw position. A `skipped` row on a title, generator or compound clip is from the code and the fakes, not tested live; the per-row `skipped` in a real run's `results` is something else, the macro inputs that could not be set. Each target row carries the clip number (`index`) and the raw track position (`position`), and both are in the `plan_sha`. `ledger` (absolute path of the plan's `drafts.json` from the draft farm) and `draft` (the picked plan, needed when the file holds several) check the effect against what the draft drew: the dry run flags a span the draft marked `not_drawn` (`ledger.flagged`), the real run refuses unless `override` (a list of effect names) names the effect, and the ledger check and `override` are in the `plan_sha`. With no `ledger` the result says none was checked. |
| `assemble_timeline` | write | A method manifest ([edit-methods.md](edit-methods.md)) as a new ` [auto]` timeline at a given size: each span appended from its clip at the clip's own frame rate, so 24, 25, 29.97, 30 and 60 fps sources sit on one timeline (`import_timeline` needs every source at the timeline's rate); static scales set as Zoom; the manifest's music cut by ffmpeg into a bed (from its `in`, as long as the picture plus the end black, a one second fade out baked into a 24-bit 48 kHz WAV in `bed_dir`, default beside the manifest, refused inside a git tree) and that file on A1 from the first frame, read back as one item of that length (live-tested 2026-11-11: one item on A1 at the timeline's first frame, as long as the picture plus the end black). The bed is that length rounded to a sample count, so it can be a fraction of a frame shorter than the timeline (16.609 s against 399 frames at 24 fps, 16.625 s); Resolve rounds the A1 item up to the full frame count and its `source_end` reads two frames less than its duration, so compare the item's start and duration with the picture, not `source_end`. `SetFades` is not used ([T18](resolve-traps.md)). Clips are imported into a `methods [auto]` bin. Every item is read back for source, start and length; a dissolve is added on the outgoing item with `AddTransition` and its length read back. Colour ([T30](resolve-traps.md)): `detect` runs on every source, and the dry run lists per file the input colour space it found and what a clip already in the `methods [auto]` bin reads. On a colour-managed project a clip the run imports and `detect` can pin gets Input Color Space and Data Level, written and read back (a file in another bin is imported again as a new clip, and that clip is tagged); a clip that was in the pool is never written, and one that is log by `detect` and reads Rec.709 is a problem (exit status 1, the timeline left in place); a file `detect` cannot pin gets a note. A derived file (a render or export) is imported with action `none` and a note, and reads Input Color Space "Project" and Data Level "Auto" ([T30](resolve-traps.md), live-tested 2026-11-11). An unmanaged project is not tagged. A `scale-window` or `crop-window` on any span after the first opens over the shot before it playing on: the montage then goes on V2 (every span in its order and at its frame, so item N is still span N) and V1 holds a plate under each window span, the previous clip counted on from where its span stopped (37 frames, or 26 for `crop-window`, or the span's length when shorter; source, start and length read back; live-tested 2026-11-11). A plate starts on the source frame after the one the previous span ends on, and its length is the macro's timeline frames counted in the source's own rate (37 timeline frames read back as 46 source frames on a 29.97 fps clip). The dry run's `plates` list gives each plate's span, window, frames, record frame and spare handle in seconds (`handle_s`), and the real run adds `placed` (name, start, frames, source start). In the manifest's `effects`, `span_index` is 0-based (span_index 2 is the third span, `index` 3 in the plan), while the plan's `index` and `apply_effect`'s `items` count from 1. The window comps themselves are not placed by this tool: `apply_effect` with the result's `track` puts them on V2, and until then V2 covers the plate at full frame. A source with less handle than a plate needs is refused in the dry run. The result's `track` says where the montage is: pass it as `track` to `apply_effect`, `apply_grade`, `reframe_apply`, `stabilize` and `vertical_apply`, and grade V1 too. A manifest with no such span is built on V1 alone. The operator's exclude list can be passed as `exclude` (absolute path; JSON with a `takes` list of file stems, names or path suffixes): a manifest naming a take on it, as a span's source or as its music file, is refused in the dry run and the real run, a file with no `takes` list is refused, the file's hash is part of the `plan_sha`, and the result carries `exclude` (`path`, `takes`, `matches`) or null. With no list the result says so: `exclude` is null, `exclude_note` reads `no exclude list given: nothing was checked for excluded takes`, and the summary repeats it. `ledger`, `draft` and `override` work as for `apply_effect` ([edit-methods.md](edit-methods.md#drafts)): each manifest effect the draft marked `not_drawn` is flagged in the dry run and refused in the real run unless `override` names it, and a ledger that is not this manifest's (its effects and spans differ) is refused. |
| `vertical_apply` | write, destructive | The 9:16 finish of a method build, on a ` [auto]` timeline: when the target does not exist, the source timeline is duplicated to it and the copy set to 1080x1920. Items are the track's clips in order, an item with no media pool clip (a dissolve, `T29`) skipped, the same count `apply_effect` uses for `items`. The result's `skipped` lists each such item on the target track, and on the track below when `track` is 2 or above (`track`, raw `position`, `name`, `reason`), and the dry run's summary says "N item(s) skipped" when there are any; it does not change the numbering. An offline clip is believed to keep its media pool item, so it is counted and not reported in `skipped` (believed, not live-tested; nothing here detects offline). `reason` is one fixed text for every row, because the item API this module uses gives no kind: a dissolve, title, generator and compound clip read alike, and `name` is the clue. The refusal for a crops.json whose item count differs from the track's clips also names the skipped items by track and raw position. Its plan rows carry the clip number as `index` only; the raw track position is in `apply_effect` rows (`position`) and in `timeline_items` (`index`) (live-tested 2026-11-11). Each item gets `Scaling` fill and the Pan from a reviewed `crops.json` (offline: `resolve_workflow.py vertical plan` writes it and the review sheets, with `--centre edge` the default and the face planner only on `--centre face`; `vertical draft` cuts a silent 540x960 `preview.mp4` of every item at its centre, a window item opening over the previous item's shot at that item's crop, for the operator to watch before this tool runs; `vertical review` marks the plan once the sheets are read and the preview is watched, and refuses without a `preview.mp4` from `vertical draft` beside the plan, drawn from its current crops. `plan --out` must be a folder that does not exist yet), and the centres and text sizes of its `rp-fx` comps are refitted and re-rendered (`zoom-push`, `text-word`, the word of `person-matte-text`, and the two windows: `crop-window` gets `Rectangle1.Center` on the crop centre, `scale-window` gets `Transform1.Pivot` on it and nothing else, so the crop-centre content stays put through the window and the full-frame snap; each comp's dry-run row lists `set`). Refuses an unreviewed plan, an item count, name, file or zoom that differs from the plan, an existing target of another size, and any target without the suffix. It does not look for the preview itself; `vertical review`, which sets the mark, does. The result's `unmoved_comps` names each item and Fusion comp it left as it was, every comp but `zoom-push`, `text-word`, `person-matte-text`, `scale-window` and `crop-window`. The two window comps are moved, not listed there, because on a render (live-tested 2026-11-11) both windows opened off the crop centre of a 9:16 item ([T34](resolve-traps.md)); the centring is not yet confirmed on a render, so look at each window's first and last plate frame. A `person-matte-text` comp gets `Text1.Center` x on the crop centre (its y kept) and `Text1.Size` refitted like `text-word`'s, and both are read back; its matte is not moved, because it sits on the subject in the source frame and the crop does not move that, so the dry run's comp `notes` (and the summary) say when the stand-in `SubjectMatte_PLACEHOLDER` is still there to be replaced by the Magic Mask. With `track` 2 or above it also frames the plates on the track below: an item that starts where montage item N (N > 1) starts and plays the file of item N - 1 gets item N - 1's Scaling fill, Pan from its cx (with the plate's own zoom) and Tilt 0, so the outgoing shot does not move at the cut. The plan's `plates` list names each one (under which item, continuing which, cx, pan), is part of the `plan_sha`, and is read back in `plate_results` (a plate that fails makes `exit_status` 1). Anything else on that track is left alone and named in `unframed_below`. Live-tested 2026-11-11: on a 7-item build with two plates, each plate's Pan equalled the Pan of the item it continues, and the plan, `plate_results` and an independent `timeline_items` read agreed (the read was taken with another 9:16 timeline current, [T27](resolve-traps.md)). A render of that build showed the outgoing shot holding its framing across both cuts ([T34](resolve-traps.md)). From the code, it does not re-clamp a crop centre: `crops.json` from `vertical plan` is already clamped (not tested with a hand-edited centre outside the clamp). A plan holding a BRAW item passes `vertical review` only when its preview was drawn from a proxy (`proxy` on the item in `crops.json`, or `--proxy-map FILE` on `vertical plan` and `vertical draft`); with none, `plan` and `draft` refuse and name the two ways forward ([T31](resolve-traps.md)). `plan`, `draft` and `review` read every other source with ffmpeg. A retimed item carries `speed` in `crops.json` and the preview draws it at that speed. |
| `create_captions` | write | Resolve's auto captions on an ` [auto]` timeline that has no subtitle items, with characters per line and line breaks for its shape (`deliver.captions`), read back from the subtitle track. |
| `queue_render` | write | Queue one render job for a delivery destination, under the house file name: a clip name, a client master, or for a music montage `<Client>_<slug>_<aspect>.mp4`. A destination is the only way to queue a render. Warns, and still queues, when files in the target folder or its destination subfolders carry another client prefix. Never starts it. |
| `sync` | write | Stack dual-system sound on a new ` [auto]` timeline: the reference on V1/A1, the other on A2 (or V2/A2) at the measured offset, every placement read back (start and source start exactly, length within a frame). Pool clips, or files imported into a new bin the run owns; only those may also go through AutoSyncAudio, which is checked against the measured offset. |
| `link_proxies` | write, changes a clip's proxy link | Link each BRAW clip to its ProRes LT proxy in `~/Movies/RPProxies/<project>` and set the project to prefer proxies, each read back. A clip no timeline uses then takes the rate it was filmed at. A real `ingest` starts those proxies in the background (`lutlab/braw/braw-proxy.sh`, log `proxies.log` beside them). Run it again as they land. |
| `relink` | write, changes an offline clip | Point the open project's offline clips (File Path missing on disk) at files under `search_roots` that verify as the same take: start timecode, frame count (one frame of slack), resolution and frame rate must all be known and agree. Never on a name alone; a clip with no timecode, a still and an audio-only clip are UNVERIFIABLE and left alone; two verified files of different sizes are AMBIGUOUS and left alone; identical copies resolve to the earliest search root. An online clip is never touched. Uses `MediaPool.RelinkClips` (the folder only), which should leave the clip's identity, timeline items, grade and tags alone (not yet confirmed on a live project: try a scratch project first); each clip is read back, and a property that reads differently afterwards is reported, never set back. `max_items` relinks a sample; dry run again before the rest. |
| `stabilize` | write, changes an item | Call `TimelineItem.Stabilize()` on video items of one track (`track`, default 1) of an ` [auto]` timeline (any other is refused); `items` picks item numbers from 1 (default all) and `max_items` keeps only the first N of them, so a sample can go first. Makes the timeline current, opens the Color page and puts the playhead at each item's start, then calls `Stabilize()` item by item, recording the bool it returned and the seconds it took; a cancel stops before the next item. **The effect is not verified:** stabilization is not visible through `GetProperty` (a 2026-09-26 spike found SmartReframe's result invisible there, and the same is assumed until a live run shows otherwise), so the result says what `Stabilize()` returned and nothing more (`verified` is false). `Stabilize()` uses the item's current stabilizer settings (mode, strength, smooth, zoom) from the Color page, which the API cannot set. It can take a long time on 6K items; the call holds the Resolve lock throughout, so use `max_items` first. |
| `reframe_apply` | write, changes an item | Set ZoomX, ZoomY, Pan and Tilt on the video items of one track of an ` [auto]` timeline from a framing plan (`reframe_plan` with `items`), each read back. Each item takes the plan entry for its file (by source range when the file has several). The timeline's size and input scaling must be the plan's; an item with its own Scaling, a clip of another resolution, an entry with no crop and a zoom below fill (unless `allow_bars`) are refused. An entry whose move is `none`, or one the item already carries, writes nothing. After the real run, a change on another timeline using the same media is reported as a leak. |
| `trim_review_markers` | write | The trim-review rows as markers (a colour per kind) on an ` [auto]` timeline, mapped through the items that play the source, each read back; a frame that already holds a marker is refused. Adds markers only. |

`production/rpresolve/grade.py` is a helper library, not a tool and not a
stub. It uses the standard library only and holds the LUT and `.drx`
apply-and-read-back code and the grade reader that `apply_grade` and
`timeline_items` use. It registers no MCP tool, and no `grade` or `match`
tool exists.

The offline tools never connect to Resolve. Every path they take must be
absolute, since the server's working directory is not the caller's.
`deliver_captions` writes `<stem>.srt` beside the render and moves
Resolve's `.ttml` to `~/.Trash/deliver-captions-<timestamp>/`, so it
keeps the write tools' plan-first contract below (dry run by default, the
real run only with its `plan_sha`, a journal line before and after) while
naming no project and taking no lock. It refuses inside any git working
tree and never overwrites an `.srt`.

## The rules the write tools keep

- **Name the project.** `project` must be the open project's exact name;
  `project_id`, when given, its unique id. The pin is re-checked before
  every change, so switching projects mid-run stops the write.
- **Plan first.** `dry_run` defaults to true and returns a `plan_sha`. A real
  run needs that `plan_sha` and is refused when the plan has changed since
  (files, media pool, items or grades differ).
- **Additive only, with five exceptions.** Nothing that existed before is modified. The exceptions are `set_color_management`, which sets a project setting only on a fresh project, one with no timeline and no clip in its media pool, so nothing anyone has made is reached (it refuses any other project, never creates, loads or saves one, and is annotated destructive so a client asks first), `relink`, which may point a clip that is offline at plan time at a file that verifies as the same take, `stabilize`, which runs Resolve's stabilizer on items, changing them, but only on ` [auto]` timelines, the same boundary grades keep, and `reframe_apply`, which sets Zoom, Pan and Tilt on items of ` [auto]` timelines only and compares every other use of the same media before and after. `vertical_apply` sets the same kind of framing (the item's own Scaling, Pan, Tilt and the centres of its `rp-fx` comps) on a 9:16 ` [auto]` copy. `relink` never touches an online clip, never relinks on a name alone, moves or deletes no file, and journals every old path against its new one, so a relink can be undone by relinking back. New timelines
  end in ` [auto]`. Grades and auto captions go only onto ` [auto]`
  timelines, and `create_captions` refuses a timeline that already has any
  subtitle item. An item whose
  grade version is remote is always refused, because a remote grade is shared
  with every timeline using the clip. A graph that is not default is refused
  unless `overwrite` is true. After a real grade run, every item on other
  timelines that uses the same media is compared with before, and any change
  is reported as a leak.
- **Settings the first custom write changes are reported, never corrected
  (opt-in guard).**
  `cut` and `sync` switch each new timeline to custom settings
  (`useCustomSettings` 1). A Blackmagic forum report (board 12, topic 212784;
  Windows and Linux, Resolve 20.3 to 21.1.1; none from a Mac) says that write
  also changes colour management, input and output scaling and the monitor
  format, and that some of those keys cannot be set back. The guard has not
  yet run against a live project, so what happens on this Mac is not known.
  **The guard is off by default.** It reads settings through
  `Timeline.GetSettings()` (else `GetSetting()` with no key), and
  `GetSettings()` on Resolve 21.1.1.10 is unconfirmed. A call that hangs
  inside Resolve cannot be caught by a try/except, so the guard makes no
  whole-settings read until someone turns it on. With it off, `cut` and `sync`
  make no `GetSettings()` call and no `GetSetting()` call without a key (they
  still read back single keys such as the frame rate and the input scaling),
  `settings_drift` is null on every timeline's entry,
  `settings_drift_rows` is empty and nothing is warned. To enable it, set
  `RPRESOLVE_SETTINGS_GUARD=on` in the MCP server's environment (`1`, `yes`
  and `true` also work; any other value, `off` included, leaves it off),
  then restart the server (with the Claude CLI, add
  `-e "RPRESOLVE_SETTINGS_GUARD=on"` to the registration, as for
  `RPRESOLVE_CONFIG` above). Make the first measurement on a scratch project,
  never a client project.
  With the guard on, each tool reads the timeline's settings before and
  after its writes and names any key that reads differently, apart from the
  ones it wrote on purpose (the custom flag, the frame rate, the size). The
  result carries it as `settings_drift` (on each timeline's entry) and
  `settings_drift_rows` (a list on the whole result), and the summary names
  it SETTINGS DRIFT. A 16:9 or a sync
  timeline is compared with itself before the write; a 9:16 or 1:1 with the
  16:9 it was copied from. Nothing is set back, and `exit_status` and `plan_sha`
  are the same with the guard on or off; a failure's message gains a short
  settings-drift clause when the guard is on and found drift. SETTINGS NOT CHECKED
  means the settings could not be read, or the last reading failed, so the
  comparison is missing or partial; the result's `notes` say why. Only keys
  that both readings hold are compared, so a few that exist only on a custom
  timeline are not seen.
- **Renders are queued, never started.** `queue_render` refuses while a
  render runs and when the output file already exists. Resolve cannot read
  back the Deliver page's settings, so the tool puts back the format and
  codec and lists the fields it changed but cannot restore (target folder,
  name, size, video and audio on, and for a destination the frame rate,
  audio, colour tags, captions and Data Burn-in too). A destination job is
  queued in Single clip mode, read back first, and the mode is put back
  after; the result also lists the render settings the job takes from the
  Deliver page as it stands (`carried_over`).
- **Deliverables by destination.** `queue_render` takes a `destination`,
  with `name` and `target_dir`, and nothing else: the render presets, with
  their `preset`, `output_dir` and `custom_name` arguments, are gone, and a
  call that still passes one is refused before it runs, with a message that
  says to give a `destination`. It names
  the file by the house rule (a clip name, `<Client>_<slug>_master.mov`,
  or for a montage destination such as `social_9x16_music`
  `<Client>_<slug>_<aspect>.mp4`) and also refuses when a fixed-size
  destination gets a timeline of another shape (a 9:16 destination wants
  the 9:16 copy), when the folder sits under
  `/Volumes` with its share unmounted, when it is inside any git working
  tree, for a sidecar destination when a caption file under the file's
  stem (.srt, .vtt, .scc, .ttml or .xml, in any case) exists, when a queued
  job already writes the same file, and when captions are wanted but the
  timeline has no subtitle track. It only warns, and still queues, when a
  master or montage name's client differs from the client prefix of files
  already in the target folder or in any destination's subfolder of it
  (`Acme` beside `AcmeCo`): one project keeps one prefix (live-tested 2026-11-11: the warning compares prefixes case-sensitively, names up to three of the other-prefix files and this job's prefix, does not change the `plan_sha`, and the job is queued with `exit_status` 0). The file is written to `<target_dir>/<destination key>/` (for example `.../social_9x16_music/Client_slug_9x16.mp4`); the folder is made if missing and named in `made_folder`. The aspect in a montage name comes from the destination, so a name part called `aspect` is refused. A destination whose config `target_dir` is null (`social_9x16_music` ships that way) is refused with "no target folder" unless the call passes `target_dir` (a folder outside any git tree) or a config overlay sets it. Each Deliver field goes in its own `SetRenderSettings`
  call; if Resolve refuses a required one, nothing is queued. The result
  shows what Resolve's job list holds and names the fields it does not
  report. When the job list holds different values, the job is still in
  the queue: the summary says so and names the job to remove or check
  (only a run that queued nothing says FAILED); `deliver_check` on the rendered file covers those. See the
  Deliver section of `resolve-template-spec.md`.
- **Dual-system sound is placed, never retimed.** `sync` refuses a
  measurement that is not a match (and says which windows agree on which
  offset), places the other recording at the measured offset (the drift
  line at the overlap's midpoint) rounded to a frame, and reports when
  that rounding plus half the drift leaves either end of the overlap more
  than half a frame out, with the retime that would cancel the drift. `AutoSyncAudio` changes the clips it links, so it runs only on
  clips the same call imported into its own new bin, and its result is
  compared with the measured offset through a second ` [auto]` timeline.
- **Stabilization changes the item, and is not read back.** `stabilize`
  calls `TimelineItem.Stabilize()` only on ` [auto]` timelines and only on the
  items its dry run listed (`plan_sha` covers the timeline, the track, the
  item ids and their source frames; `max_items` shortens the list before it is
  hashed, so a sample and the full run have different plans). Each item's
  result is the bool `Stabilize()` returned plus its elapsed seconds. Nothing
  else is checked: stabilization does not show in `GetProperty`, so
  `verified` is false in every result and a True means only that Resolve said
  so. Judge a sample by eye before the rest. The stabilizer's mode, strength,
  smooth and zoom are whatever the item has on the Color page; the API cannot
  set them. The call is long and the Resolve lock is held throughout, so other
  Resolve tools wait and may report Resolve as busy. A cancel or an error
  between items stops there and the journal's finished line keeps the items
  done so far.
- **Markers only.** `trim_review_markers` adds markers to an ` [auto]`
  timeline and reads each back; it never cuts, ripples, moves or deletes,
  and refuses a row whose frame already holds a marker.
- **The UI is put back.** `DuplicateTimeline` makes the copy current and
  `ApplyGradeFromDRX` opens the Color page; queueing makes the timeline
  current; `create_captions` makes the timeline current and opens the Edit
  page; `sync` and `timeline_from_clips` make each new timeline current to
  place clips on it (and, `sync` importing, opens its bin);
  `trim_review_markers` makes the timeline
  current while marking; `stabilize` makes the timeline current, opens the
  Color page and moves the playhead to each item. The current timeline, playhead, page and media
  pool folder are restored after each, and any restore problem is in the
  result.
- **One writer at a time.** Every Claude Code session starts its own server,
  so all Resolve access takes a lock at `~/Library/Caches/rpresolve/resolve.lock`,
  shared by every session's server. `resolve_status` waits up to 5 s,
  the other reads 10 s and writes 30 s, then report Resolve as busy.
- **A record of every write.** A real run writes a synced "started" line to
  `~/Library/Logs/rpresolve-mcp/writes.jsonl` (mode 0600) before touching
  Resolve and a "finished" line after, which names what was created and
  any timeline a failed build left behind (`left_behind`). If the started
  line cannot be written, the write does not happen.
- **Calls that are never made, and one place that writes.** A test scans the
  non-test `.py` files under `production/` (everything except the top-level
  `production/tests/`), the script and the launcher among them.
  It fails any file that names `LoadProject`, `CreateProject`, `SaveProject`,
  `StartRendering`, any `Delete*`, `eval`, `exec` or one of the other calls that
  close, import, restore, archive or export a project, as a name or a string.
  It also fails any file outside `production/rpresolve/` that names a call on
  its list of Resolve write calls (`ImportMedia`, `SetLUT`, `AddRenderJob`,
  `OpenPage`, `LoadRenderPreset`, `GrabStill`, `Quit` and the rest) or a name
  that starts with a change verb (Set, Add, Create, Import, Load, Save, Grab,
  Export, Convert, Render and the others). It is a name tripwire and nothing
  more: a call built at run time (a string assembled and passed to `getattr`)
  and a direct python import of Resolve's own module are not caught, and test
  files, the hand-run live harness `production/tests/live_mcp_sandbox.py`
  among them, are not scanned. The library and this server are the only code
  the scan allows those write calls in.

## The command-line script

`resolve_workflow.py` no longer writes to Resolve. A write goes through the
server above: the project is named and pinned, the plan is shown first, the
real run needs its `plan_sha`, every change is read back, and each real run
leaves a journal line. The script's write commands kept no journal, made the
dry run and the `plan_sha` optional, and several acted on whatever project or
timeline was current. `render --start` started every queued job and
`clear-queue` deleted every queued job, the ones the server had queued
included. They are retired, not hidden: running one exits 2 and says what to
use instead (the table below says the same), without connecting to Resolve.

| Retired command | Do this instead |
|---|---|
| `new-project` | No tool creates a project: a person makes it in Resolve (`CreateProject` makes the new project current, which can drop an editor's unsaved work). Then `set_color_management` sets Color science to DaVinci YRGB Color Managed on that fresh, empty project, or set it by hand in Project Settings > Color Management. `ingest` refuses a project that is not DaVinci YRGB Color Managed, and makes its own camera bins and a Review bin; it does not make the old Audio, Selects, Timeline, Graphics and Exports tree, which you can make by hand if you want it. Set the resolution and frame rate in Project Settings too. |
| `import-media` | `ingest`: media goes under camera bins in `parent` (default `Source`) and each clip's Input Color Space and Data Level are tagged from `detect`. There is no custom bin and no clip colour; `timeline_items` shows each item's input colour space. |
| `build-timeline` | No tool builds an intro and outro timeline: make it by hand. `cut`, `sync`, `duplicate_timeline_auto` and `timeline_from_clips` make ` [auto]` timelines; `timeline_from_clips` is the one for "these clips, in this order" (a test ladder, a camera comparison, a selects reel). |
| `add-subtitles`, `auto-subtitle`, `captions` | `create_captions` on an ` [auto]` timeline. An `.srt` cannot be placed by script on Resolve 21: use File > Import > Subtitle by hand. |
| `render`, `render-all`, `deliver-queue` | `queue_render` with a `destination`, a delivery destination in `resolve-config.json`, under the house file name. It queues the job and never starts it; a person starts the render on the Deliver page. `render-all` is one `queue_render` call per destination. The four render presets `render` and `render-all` took (`youtube`, `linkedin`, `master`, `story`) no longer exist; the Deliver section of `resolve-template-spec.md` lists the destinations and their frame sizes. |
| `clear-queue` | No tool. Remove jobs on the Deliver page; no tool deletes anything. |
| `apply-lut`, `apply-drx` | `apply_grade` on an ` [auto]` timeline. A `.drx` needs a label manifest beside it, a hand-written `<name>.json` holding `{"num_nodes": 3, "labels": ["CST IN", "", "CST OUT"]}`: the node count and the node labels the graph should show after the apply, so the result can be read back (either key may be left out). The `--camera` filter by clip colour is gone; choose items by number with `items` (`timeline_items` lists them). |
| `open-page` | No tool. Click the page tab in Resolve. |
| `export-project` | No tool. Export the project by hand in Resolve: from the Project Manager, or with File > Export Project Archive. The command only wrote a project file to disk and changed nothing in Resolve; it is retired with the rest. |
| `ingest`, `cut`, `sync` | The tools of the same names. |
| `trim-review --markers` | `trim_review_markers`. |

The menu paths this table gives for creating and exporting a project
(Project Settings > Color Management > Color science, the Project Manager,
File > Export Project Archive) are written from memory and have not been
checked against a live Resolve; the labels may differ by version.

Still in the script: `python3 resolve_workflow.py --help` lists the commands,
one line each, and `<command> --help` gives a command's flags. Each command is
one module, `rpresolve/cmd_<name>.py` (the name with its hyphens as
underscores), whose docstring says what it does; from `production/` it also
runs alone as `python3 -m rpresolve.cmd_<name>`. The script finds the modules
at import, so a new command adds its module and its tests and edits no shared
file, this one included (`rpresolve/cli.py` says how).

- Read Resolve and change nothing (Resolve Studio must be running):
  `list-projects`, `list-timelines`, `list-render-formats`, `info` and `audio-state`.
- Every other command never connects to Resolve.

A command has an MCP tool when one of the same name with underscores is in
[Tools](#tools); `info` is covered by `resolve_status`, `list_timelines` and
`render_queue_status` together. A command with a tool keeps a few flags the
tool lacks: the script's `sync-measure` takes `--ref-stream` and
`--other-stream`, and its `survey` takes `--projects-dir`, `--metadata-cache`
and `--no-metadata-cache`, none of which the tools do.

`audio-state` reads one timeline's audio from Resolve and changes nothing: the
audio tracks (type, name, on or off, item count), each audio item's `Audio*`
properties (volume, Voice Isolation, Dialogue Leveler, as the item reports
them) and its source's audio mapping, the Deliver page's format, codec and
render mode, the audio codec, sample rate and bit depth of the render jobs
queued for that timeline, and the project's loudness-meter settings. `-o`
writes the JSON (refused inside any git tree). The report ends with what the
API cannot read: the mixer, clip and track EQ, Dynamics and effects, the AI
Music Remixer, the Deliver page's Audio tab and render range (written by
`SetRenderSettings`, never read back), and loudness itself. `deliver-check
--audio-state FILE`, and the `deliver_check` tool's `audio_state`, add rows
`job_audio_codec`, `job_audio_sample_rate` and `job_audio_bit_depth` that
compare the queued jobs with the destination (SKIP when no job reports a
field).

`queue_render` puts the Deliver page back through `rpresolve.renderguard`: the
format and codec, the render mode and the timeline's mark In and Out are read
before, set back (also when the queue raises) and read again, and a value that
did not come back is a warning. Resolve has no `GetRenderSettings`, so
`AudioSampleRate` and the `SelectAllFrames` render range cannot be read or put
back; the result's `render_guard.hand_reset` and the summary name them. The
guard also restores any touched key when a Resolve does answer
`GetRenderSettings` with a dict. It never starts a render.

`perceive --targets` also writes `targets.json` for a private reference library;
see `reference-library.md`.

`audio-report` measures one audio stream, whole or between `--start` and
`--end`, and writes `audio-report.json`, `loudness.png` (short-term and
momentary loudness over time, silences shaded, clips and clicks marked) and
`spectrogram.png` into the `-o` folder, which is refused inside any git
tree. The JSON holds integrated loudness, LRA and true peak (read as
`deliver_check` reads them), per-channel peak, RMS, crest factor, DC and
clip runs, clicks, silences, dropouts, stereo correlation and balance,
octave balance, and flags, each flag with its value, threshold and first
times; `--ref` adds a reference file's numbers and the difference.
`--dest` or `--target` sets the loudness target. Every threshold is a
working default, not a standard. It measures and does not listen: a clean
report does not mean a voice is clear, so a person still hears the hardest
passage as an A/B before a mix is called done.

`sting-sync` re-measures each hit of an intro's sound sting from the audio and
checks it against the lockup's picture keyframes: signed offsets in frames and
ms, FAIL first. It has no tool; exit 0 pass, 1 any FAIL, 2 an input missing or
breaking the contract.

To run a read-only command from a terminal, export the two variables the
registration above sets (`RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB`).
`rpresolve.api.connect` adds Resolve's `Scripting/Modules` folder to the
import path itself, so `PYTHONPATH` is needed only for a Resolve installed
somewhere else.

## Where output goes

Output files name media and words, so they are refused inside any git
working tree, not just this one. A result too long for one reply pages
with `offset` and `limit` and writes every row to
`~/Library/Caches/rpresolve/mcp` (or `$RPRESOLVE_MCP_OUT`); pass `out` to
choose the file.

## Things that cost time to find

- `set_color_management` and `timeline_from_clips` are tested against the
  shared fakes only (`tests/test_mcp_project_timeline.py`). `timeline_from_clips`
  ran live on 2026-10-11 (Resolve 21.1.1.10) and 2026-11-07 (21.2.0.6); the
  fresh-project write of `set_color_management` has not. Run
  `tests/live_mcp_sandbox.py` on a sandbox project before relying on them. What is unconfirmed: that
  `Project.SetSetting('colorScienceMode', ...)` takes effect and reads back on
  21.1.1.10 (the 21.1 README marks `SetSetting` deprecated for
  `SetSettings`), and how `AppendToTimeline` with no `recordFrame` places a
  conformed clip. Both tools read everything back and refuse to call a
  mismatch a success. What a live dry run did confirm (2026-10-08, read
  only): both plans read a real project's bins and clips; GH7 and S5II
  plans matched the timelines made by hand frame for frame (3030 and 3135);
  Resolve truncates a conformed clip's length (35 PYXIS clips at 30 fps on a
  29.97 fps timeline, 7723 frames against 7758 rounded), so the plan
  truncates too.
- `timeline_from_clips` places picture only. How Resolve lays a multichannel
  clip's sound over audio tracks that do not exist yet has not been checked.
- Whether the timeline and output colour spaces and the tone and gamut mapping
  can be set through the API, so that `set_color_management` could set more
  than `colorScienceMode`, is open.

- Resolve's scripting library leaves the process in the C locale once
  connected. A text file opened without `encoding="utf-8"` then decodes as
  ASCII and fails on the first non-ASCII byte.
- The client's stdin is the protocol, and ffmpeg, ffprobe and exiftool
  read stdin when they inherit it. The server moves the protocol to private
  descriptors before importing anything, points fd 0 at `/dev/null` and fd 1
  at stderr, and passes `stdin=DEVNULL` to every subprocess.
- The Resolve client can crash at interpreter shutdown, so the server
  always leaves with `os._exit`.
- Source frames on a timeline item count in the source clip's own frame
  rate, not the timeline's.
- `Timeline.CreateSubtitlesFromAudio` returns False, and makes nothing,
  when called from the Deliver page; from the Edit page it returns True
  once the items are made (22 in 36 s on a 37 s timeline). It works on the
  current timeline. Its settings keys and values are Resolve constants,
  which are floats (`resolve.SUBTITLE_LANGUAGE` is 0.0), so only None
  marks a missing one.
- `MediaPool.AppendToTimeline` of an imported `.srt` returns True and
  places nothing (Resolve 21.0.4.5).
- `GetIsTrackEnabled` answers False for every track of a timeline that is
  not current.
- `get_image` and `get_thumbnails` with source `timeline` draw the current
  timeline only, and no tool of this server makes another one current
  ([T33](resolve-traps.md), live-tested 2026-11-11).
- A transform can read back perfectly and still leave bars. At `cut`'s
  default 2.25 output pixels per source pixel, a 720-row source covers
  1620 of 1920 rows of a 9:16 frame, and a picture letterboxed to a
  360-row band covers 810. The sandbox 9:16 that rendered with bars was
  built at that default; `cut` now names BARS, and `reframe_plan`
  measures the picture area and, with no scale named, derives the widest
  one that fills the frame instead of starting from 2.25.
- `MediaPool.AppendToTimeline` works on the current timeline and answers
  truthy even when it places nothing, including for a track index that
  does not exist yet; `sync` adds tracks with `AddTrack` first and reads
  every placement back. `recordFrame` counts absolute timeline frames (a
  25 fps timeline starts at 90000); `startFrame`/`endFrame` count source
  frames at the source's own rate.
- Timeline markers are addressed by frames from the timeline's start
  (the scripting README's `GetMarkers` example), unlike `recordFrame`;
  not yet confirmed on a live Resolve (see the Trim review section of
  `resolve-template-spec.md`).
- Whether a second `useCustomSettings` write on an already custom timeline
  changes those keys again is not known. `cut` makes that write twice on
  every 16:9 (once in `build_clip`, once in its size step) and once on each
  version, and `settings_drift` (with the guard enabled) names the write after
  which each key changed.
  `Timeline.GetSettings()` is in the 21.1 scripting README and stub, and
  `GetSetting()` with no key is its older, deprecated form; neither has been
  confirmed on Resolve 21.1.1.10, which is why the guard is off by default
  until one run on a scratch project confirms the calls. A key that reads
  differently because the tool wrote the frame rate or the size would be a
  false alarm; the guard leaves those writes out.
- Live-verified against Resolve Studio 21.0.4.5, from the script these notes
  used to live in. `Timeline.ApplyGradeFromDRX(path, mode, items)` does not
  exist in 21.0.4, and the scripting README's example for it is stale. The
  working call is per item: `item.GetNodeGraph().ApplyGradeFromDRX(path,
  gradeMode)` returns a bool, with `gradeMode` 0 for no keyframes, 1 for
  source timecode aligned and 2 for start frames aligned. It very likely
  replaces the item's node graph, which is why `apply_grade` reads the graph
  back and checks it against the label manifest.
- Grades go through the `Graph` object from `item.GetNodeGraph()`:
  `GetNumNodes()`, `GetNodeLabel(i)`, `GetLUT(i)`, `SetLUT(i, path)`,
  `GetToolsInNode(i)` and `SetNodeEnabled(i, bool)`. Node indexes are
  1-based. `TimelineItem.GetNumNodes`, `SetLUT` and `GetLUT` are deprecated
  aliases.
- `SetLUT` only accepts a LUT Resolve has scanned (`Project.RefreshLUTList()`
  rescans), and `GetLUT` may report the path relative to a LUT folder.
  `apply_grade` rescans first, sends the path it was given (a link in a LUT
  folder goes by its own path, not its target's) and reports a read-back that
  differs as a failure.
- `GetToolsInNode(i)` returns None for a node whose corrections are all at
  their defaults; it is reliable only for OFX nodes.
- The scripting API cannot create colour-page nodes. A LUT onto a node
  beyond what the clip already has is refused (`cannot add node N`). Add the
  node on the Color page first, or apply a `.drx` that already contains it.
- Unknown `resolve.CONSTANT` names return None silently, so a missing
  constant reads as a value rather than an error. Check them.
- Piped output from the Resolve client is lost unless flushed before the
  interpreter exits, and the client can segfault at shutdown after some
  calls: the server and the script both leave through
  `rpresolve.api.exit_clean` (flush both streams, then `os._exit`).
- Subtitle styling (font, colour, position) has no scripting entry point. It
  stays a manual Edit-page step.
- Queueing a render never reframes. A 9:16 or 1:1 destination refuses a
  timeline of another shape. `reframe_plan` plans a crop per span and `cut`
  applies it to the 9:16 and 1:1 versions (Zoom, Pan and Tilt, each read back).
- **Pan and Tilt are not timeline pixels** (Resolve 21.1.1.10, measured
  2026-10-15 on six fixtures). Each is measured against the fitted picture
  on its own axis: a Pan of P moves the picture right by
  P × (source width × fit) / timeline width, and a Tilt of T moves it up by
  T × (source height × fit) / timeline height, where fit is the input
  scaling's factor before Zoom. They read as pixels only on an axis where
  the fitted picture spans the timeline, which is why Pan looked confirmed
  on the Ep 002 9:16 and Tilt did not. A 9:16 from a UHD source needs a
  Tilt 3.16 times the pixel move; a 2.25:1 master (3:2 at scale full
  frame with crop into 5952x2646) needs 0.667 of it. Negative Tilt moves
  the picture down and shows the top of the source. `tilt-fixture
  --variant` rebuilds any of the six; `tilt-check` reads a still of it.

## FCPXML import

What Resolve Studio 21.2.0.6 made of a hand-written FCPXML 1.10 file
through `ImportTimelineFromFile` (spike S0, 2026-11-07, two lavfi clips at
30000/1001, a click track, a ProRes 4444 clip with alpha), read back through
`GetItemListInTrack` and `GetProperty`:

| In the file | What Resolve placed | Read back |
|---|---|---|
| spine `asset-clip`s | V1 and A1 items at the file's record positions and lengths | yes |
| `<transition name="Cross Dissolve">` with its `effect` resource | a 15-frame item named Cross Dissolve on V1, no media-pool clip, `GetLeftOffset` None | yes: position, length and name |
| `<transition name="Dip to Color Dissolve">` | a Cross Dissolve (the name is not mapped) | placed as Cross Dissolve |
| `<adjust-transform scale="1.2 1.2"/>` | ZoomX and ZoomY 1.2 | yes |
| `<adjust-transform>` with a keyframed `scale` | ZoomX 1.0 (keyframes, if any, are invisible to `GetProperty`) | no |
| connected `asset-clip lane="1"` (video with alpha) | V2, at its record position | yes |
| connected `asset-clip lane="-1"` (audio only) | A2, at its record position | yes |
| `<timeMap>` retime | an item of the right length whose media path reads empty and `GetRightOffset` negative: broken | no |

So `import_timeline` checks clips, static scale and transitions, and
`rpresolve.interchange` writes only what the first six rows cover: cuts,
cross dissolves, a static scale per clip, and lanes. Dips, keyframed moves
and speed changes stay hand steps on the Edit page.

## Framing

Every crop the toolkit plans, for the master timeline shape or a platform
version, keeps the subject's whole head. The rules, in `rpresolve/reframe.py`:

- **The head is above the face box.** Vision's face box stops near the
  brows, so the head's top is taken `HEAD_ABOVE` (0.6) face heights above
  it, and the check covers the head, not only the face.
- **Room above the head.** The crop moves up, never down, until the median
  head has `HEADROOM` (0.65) face heights of room above it and no sample's
  head is cut. Both values are tunable constants. HEADROOM came from the editor's
  hand fixes on two standing performers (a 2026-10 festival shoot), so those
  two clips are its calibration, not its proof.
- **Do nothing when nothing is needed.** The master shape starts from
  centre fill (the shot as shot). When that already keeps every head with
  its room, the plan's `move` is `none` and `reframe_apply` writes nothing.
  Platform versions start centred on the face, as before.
- **Fallbacks say why.** A head that runs past the top of the picture (the
  subject at the sensor's edge), or a crop that cannot give the room the
  picture holds without losing the face below, is `fallback` with the
  reason. A shot the camera framed tight passes with a note: nothing above
  the head was there to keep.
- **Squeeze is read.** An anamorphic source is planned on its desqueezed
  picture (width × squeeze). The squeeze comes from the manifest's
  `squeeze`, the `squeeze` argument or the file's tag (BRAW writes
  `anamorphic`), in that order; with none it is 1.0, and the summary says
  it was assumed. The S5II container carries no squeeze flag, so give it.
  The transform for a squeezed clip assumes Resolve fits its desqueezed
  picture once the clip's Pixel Aspect Ratio is set. No live run has
  checked that yet, so `reframe_apply` warns on every squeezed item.
- **BRAW is judged on stills.** ffmpeg cannot decode BRAW. Give `stills`
  (any size of the source's shape: Resolve's exported frames, a proxy) and
  `src`, the size Resolve reads (6048x4032 for the Pyxis). A still of
  another shape is refused, and stills are never removed.
- **Zoom never goes below fill** unless `allow_bars` is set.

The order on a live project: `reframe_plan` with `items` (offline) writes
the framing plan, `duplicate_timeline_auto` makes the ` [auto]` copy, then
`reframe_apply` writes the plan onto it, dry run first.

## Check it against a live Resolve

`tests/live_mcp_sandbox.py` runs every tool against a sandbox project and
then checks that nothing that existed before changed. See its docstring
for the arguments. It refuses to run unless the open project's name
contains "Sandbox" and its unique id matches the one you pass. Run it by
hand; CI runs the offline tests only.

Last runs: 2026-10-11, PASS on Resolve Studio 21.1.1.10; 2026-11-07, PASS, 24 of
24, on 21.2.0.6, with `--timeline "MCP_twenty_30 [auto]"`,
`--lut "Looks/soft-daylight-60.cube"`,
`--bin "A3 ingest/Panasonic DC-GH7"` and a `--render-dir` outside any repo.
Give `--bin` a bin of video clips: a bin that holds a still fails the
`timeline_from_clips` check by that tool's own refusal, not Resolve's.
