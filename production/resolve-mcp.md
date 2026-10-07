# The Resolve MCP server

`resolve_mcp.py` is a Model Context Protocol server over stdio that lets an
MCP client (Claude Code here) drive DaVinci Resolve through the same library
the `resolve_workflow.py` CLI uses. It is standard-library Python, with no
SDK and nothing to install, and runs on macOS's `/usr/bin/python3` (3.9),
where Resolve's scripting module, numpy and OpenCV live.

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
tool must name that project. There are 23 tools: 5 reads, 10 offline tools
(one of them, `deliver_captions`, writes a file beside a render) and 8
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
| `apply_grade` | write, destructive | A LUT on one node, or a `.drx` still checked against its label manifest, on an ` [auto]` timeline's items. |
| `create_captions` | write | Resolve's auto captions on an ` [auto]` timeline that has no subtitle items, with characters per line and line breaks for its shape (`deliver.captions`), read back from the subtitle track. |
| `queue_render` | write | Queue one render job from a `resolve-config.json` preset, or for a delivery destination under the house file name. Never starts it. |
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
- **Deliverables by destination.** With `destination`, `name` and
  `target_dir` in place of `preset` and `output_dir`, `queue_render` names
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
  page; `sync` makes each new timeline current to place clips on it (and,
  importing, opens its bin); `trim_review_markers` makes the timeline
  current while marking. The current timeline, playhead, page and media
  pool folder are restored after each, and any restore problem is in the
  result.
- **One writer at a time.** Every Claude Code session starts its own server,
  so all Resolve access takes a lock at `~/Library/Caches/rpresolve/resolve.lock`,
  shared with the CLI's write commands. `resolve_status` waits up to 5 s,
  the other reads 10 s and writes 30 s, then report Resolve as busy.
- **A record of every write.** A real run writes a synced "started" line to
  `~/Library/Logs/rpresolve-mcp/writes.jsonl` (mode 0600) before touching
  Resolve and a "finished" line after, which names what was created and
  any timeline a failed build left behind (`left_behind`). If the started
  line cannot be written, the write does not happen.
- **Calls that are never made.** A test scans all the code the server can
  reach for `LoadProject`, `CreateProject`, `SaveProject`, `StartRendering`,
  any `Delete*`, `eval` and `exec`, as names or strings.

## Where output goes

Output files name media and words, so they are refused inside any git
working tree, not just this one. A result too long for one reply pages
with `offset` and `limit` and writes every row to
`~/Library/Caches/rpresolve/mcp` (or `$RPRESOLVE_MCP_OUT`); pass `out` to
choose the file.

## Things that cost time to find

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

## Check it against a live Resolve

`tests/live_mcp_sandbox.py` runs every tool against a sandbox project and
then checks that nothing that existed before changed. See its docstring
for the arguments. It refuses to run unless the open project's name
contains "Sandbox" and its unique id matches the one you pass. Run it by
hand; CI runs the offline tests only.
