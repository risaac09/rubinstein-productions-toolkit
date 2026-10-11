# The Resolve MCP server

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

Keep the write tools on "ask" in Claude Code's permissions. Their results
name media files, people and transcript text, so treat anything they return
as private.

## Tools

Start with `resolve_status`: it says which project is open, and every write
tool must name that project. There are 25 tools: 5 reads, 10 offline tools
(one of them, `deliver_captions`, writes a file beside a render) and 10
writes.

| Tool | Kind | What it does |
|---|---|---|
| `resolve_status` | read | Toolkit commit and dirty flag, whether Resolve is reachable, the open project's name, unique id, colour science, timelines and render queue, and which helper tools are installed. Works with Resolve closed. |
| `list_timelines` | read | Every timeline: index, name, unique id, frame rate, start and end frames, track counts, which is current, which end in ` [auto]`. |
| `timeline_items` | read | One timeline's items per track: timeline and source frames, media file, input colour space and data level, transform, and grade (version, colour group, nodes with labels, LUTs and tools). Reads a timeline without making it current. |
| `media_pool` | read | The folder tree (capped at 300 folders) and one folder's clips with their properties. |
| `render_queue_status` | read | Every render job with its status, whether a render is running, and the Deliver page's format and codec. |
| `detect` | offline | Camera and picture profile per media file, with the proposed Input Color Space and Data Level. |
| `survey` | offline | The read-only survey of every project from Resolve's databases on disk (needs Python 3.14). |
| `measure` | offline | Luma, clipping, legal range, skin tone in faces found by macOS Vision, and camera match for a video file. |
| `endcheck` | offline | For each span in a cut manifest: does the out-point land in a pause, on the intended words, inside the approved text. |
| `selects` | offline | Proposed spans from a word-level transcript that stay inside an approved text. |
| `reframe_plan` | offline | A static crop per span for the 9:16 and 1:1 versions, centred on the speaker's face: frames sampled across each span, faces from macOS Vision tracked across the samples (the one in the most samples, then the larger, or the one nearest `speaker_x`), the crop kept inside the picture area, and every sampled face box (10% margin) checked inside it. When the default scale (2.25) leaves bars or loses the face, the widest filled scale is tried; else the span needs a manual reframe or a split, and a span with no face gets no crop (`allow_bars` keeps the default when it holds the face, named `bars`). Writes a manifest copy with per-span reframes (what `cut` reads) and a TSV and JSON report beside it. |
| `deliver_check` | offline | A rendered file against its delivery destination: name rule, folder (its destination's subfolder), container, codec, size, exact fps, pixel format, colour tags and range, audio, captions (a sidecar `.srt` whose cues sit inside the video), loudness and true peak, each PASS, FAIL or SKIP with found and expected. |
| `deliver_captions` | offline, writes a file | Resolve's caption sidecar beside a render (`<stem>_<track>.ttml`, timed from the timeline's timecode) to the zero-based `<stem>.srt` a platform reads: the file's start timecode taken off, every cue checked against the video's length, the `.srt` read back, the TTML moved to the Trash. Dry run first. |
| `sync_measure` | offline | Where one recording sits against another from the sound both heard (a camera and a recorder, or two cameras): the offset in seconds and in frames with the half-frame residual of frame placement, clock drift (ms per minute, ppm, over the overlap, and the retime that would cancel it), and each window's normalized correlation and peak ratio. A weak or inconsistent result is not called a match. |
| `trim_review` | offline | Long silences from the source audio, fillers, repeats and Whisper loops from the word JSON, as proposals (keep, tighten, cut-candidate) in a TSV. Over a cut manifest the times run along each clip. Nothing is cut. |
| `ingest` | write | Import media under camera bins and tag Input Color Space and Data Level, reading each tag back. Skips files already in the pool. |
| `cut` | write | Build `<prefix>_<clip> [auto]` timelines and their 9:16 and 1:1 versions (`aspects`, default 9:16) from a cut manifest, gated by endcheck. Each version's items take their span's crop from `reframe_plan` (else the clip's `face_x`), every transform read back. A version with no reframe is not built and is named UNREFRAMED, nor is one whose input scaling is not scaleToFit or scaleToCrop (REFUSED); one whose picture does not fill the frame is built and named BARS. A timeline a failed build leaves behind is named in `left_behind` and in the journal. Each timeline is decided on its own: one that exists is skipped, and a missing version beside an existing 16:9 is built from it while its items still match the manifest's spans. |
| `duplicate_timeline_auto` | write | Copy a timeline to a new ` [auto]` name and compare every item with the origin. |
| `set_color_management` | write | Set the colour-management Project Settings `ingest` needs (preset `managed`: `colorScienceMode` `davinciYRGBColorManagedv2`) on the open project, each value read back and every key read again after. Refuses unless the project is fresh: no timelines and no clips in the media pool. Never creates, loads or saves a project; a person makes the project in Resolve. |
| `timeline_from_clips` | write | A new ` [auto]` timeline from media-pool clips one after another: the clips directly in a bin, or clips named by unique id, path or name, in name, path or given order (natural order, so P2 comes before P10). Picture only, on V1, following the project's frame rate and size; a clip at another rate is conformed by Resolve to real time and the plan says so. Every item is read back: the clip, its first source frame, its length, no gap or overlap. The timeline goes into the media-pool bin open in Resolve (`lands_in_bin` says which). |
| `apply_grade` | write, destructive | A LUT on one node, or a `.drx` still checked against its label manifest, on an ` [auto]` timeline's items. |
| `create_captions` | write | Resolve's auto captions on an ` [auto]` timeline that has no subtitle items, with characters per line and line breaks for its shape (`deliver.captions`), read back from the subtitle track. |
| `queue_render` | write | Queue one render job for a delivery destination, under the house file name. A destination is the only way to queue a render. Never starts it. |
| `sync` | write | Stack dual-system sound on a new ` [auto]` timeline: the reference on V1/A1, the other on A2 (or V2/A2) at the measured offset, every placement read back (start and source start exactly, length within a frame). Pool clips, or files imported into a new bin the run owns; only those may also go through AutoSyncAudio, which is checked against the measured offset. |
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
- **Additive only.** Nothing that existed before is modified. New timelines
  end in ` [auto]`. One tool sets a project setting, `set_color_management`,
  and it does so only on a fresh project, one with no timeline and no clip in
  its media pool, so nothing anyone has made is reached; it refuses any other
  project, never creates, loads or saves one, and is annotated destructive so
  a client asks first. Grades and auto captions go only onto ` [auto]`
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
  the file by the house rule and also refuses when a fixed-size
  destination gets a timeline of another shape (a 9:16 destination wants
  the 9:16 copy), when the folder sits under
  `/Volumes` with its share unmounted, when it is inside any git working
  tree, for a sidecar destination when a caption file under the file's
  stem (.srt, .vtt, .scc, .ttml or .xml, in any case) exists, when a queued
  job already writes the same file, and when captions are wanted but the
  timeline has no subtitle track. Each Deliver field goes in its own `SetRenderSettings`
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
- **Markers only.** `trim_review_markers` adds markers to an ` [auto]`
  timeline and reads each back; it never cuts, ripples, moves or deletes,
  and refuses a row whose frame already holds a marker.
- **The UI is put back.** `DuplicateTimeline` makes the copy current and
  `ApplyGradeFromDRX` opens the Color page; queueing makes the timeline
  current; `create_captions` makes the timeline current and opens the Edit
  page; `sync` and `timeline_from_clips` make each new timeline current to
  place clips on it (and, `sync` importing, opens its bin);
  `trim_review_markers` makes the timeline
  current while marking. The current timeline, playhead, page and media
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

Still in the script, with `python3 resolve_workflow.py <command> --help`:

- Read Resolve and change nothing (Resolve Studio must be running):
  `list-projects`, `list-timelines`, `list-render-formats` and `info`.
- Never connect to Resolve: `survey`, `detect`, `measure`, `manifest`,
  `endcheck`, `selects`, `reframe-plan`, `deliver-check`, `deliver-captions`,
  `deliver-fix-loudness`, `sync-measure` and `trim-review` (the TSV).

Four of these have no tool yet: `manifest` (it builds the cut manifest that
`endcheck`, `reframe_plan`, `cut` and `trim_review` read), `deliver-fix-loudness`
(the loudness step of the deliver loop), `list-projects` and
`list-render-formats`. `info` is covered by `resolve_status`,
`list_timelines` and `render_queue_status` together. Each other command has a
tool of the same name (with underscores), with a few flags left behind: the
script's `sync-measure` takes `--ref-stream` and
`--other-stream`, and its `survey` takes `--projects-dir`, `--metadata-cache`
and `--no-metadata-cache`, none of which the tools do.

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
  shared fakes only (`tests/test_mcp_project_timeline.py`). Neither has run
  against a live Resolve yet. Run `tests/live_mcp_sandbox.py` on a sandbox
  project before relying on them. What is unconfirmed: that
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
- A transform can read back perfectly and still leave bars. At `cut`'s
  default 2.25 output pixels per source pixel, a 720-row source covers
  1620 of 1920 rows of a 9:16 frame, and a picture letterboxed to a
  360-row band covers 810. The sandbox 9:16 that rendered with bars was
  built at that default; `cut` now names BARS, and `reframe_plan`
  measures the picture area.
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

## Check it against a live Resolve

`tests/live_mcp_sandbox.py` runs every tool against a sandbox project and
then checks that nothing that existed before changed. See its docstring
for the arguments. It refuses to run unless the open project's name
contains "Sandbox" and its unique id matches the one you pass. Run it by
hand; CI runs the offline tests only.
