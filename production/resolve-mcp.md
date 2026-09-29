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
registration. A named overlay that is missing, does not parse, or is not
a JSON object stops the tool with an error; it is never quietly replaced
by the defaults.

Keep the write tools on "ask" in Claude Code's permissions. Their results
name media files, people and transcript text, so treat anything they return
as private.

## Tools

Start with `resolve_status`: it says which project is open, and every write
tool must name that project.

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
| `deliver_check` | offline | A rendered file against its delivery destination: name rule, container, codec, size, exact fps, pixel format, colour tags, audio, captions, loudness and true peak, each PASS, FAIL or SKIP with found and expected. |
| `ingest` | write | Import media under camera bins and tag Input Color Space and Data Level, reading each tag back. Skips files already in the pool. |
| `cut` | write | Build `<prefix>_<clip> [auto]` timelines (and 9:16 copies) from a cut manifest, gated by endcheck. |
| `duplicate_timeline_auto` | write | Copy a timeline to a new ` [auto]` name and compare every item with the origin. |
| `apply_grade` | write, destructive | A LUT on one node, or a `.drx` still checked against its label manifest, on an ` [auto]` timeline's items. |
| `queue_render` | write | Queue one render job from a `resolve-config.json` preset, or for a delivery destination under the house file name. Never starts it. |

The offline tools never connect to Resolve. Every path they take must be
absolute, since the server's working directory is not the caller's.

## The rules the write tools keep

- **Name the project.** `project` must be the open project's exact name;
  `project_id`, when given, its unique id. The pin is re-checked before
  every change, so switching projects mid-run stops the write.
- **Plan first.** `dry_run` defaults to true and returns a `plan_sha`. A real
  run needs that `plan_sha` and is refused when the plan has changed since
  (files, media pool, items or grades differ).
- **Additive only.** Nothing that existed before is modified. New timelines
  end in ` [auto]`. Grades go only onto ` [auto]` timelines. An item whose
  grade version is remote is always refused, because a remote grade is shared
  with every timeline using the clip. A graph that is not default is refused
  unless `overwrite` is true. After a real grade run, every item on other
  timelines that uses the same media is compared with before, and any change
  is reported as a leak.
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
  report; `deliver_check` on the rendered file covers those. See the
  Deliver section of `resolve-template-spec.md`.
- **The UI is put back.** `DuplicateTimeline` makes the copy current and
  `ApplyGradeFromDRX` opens the Color page; queueing makes the timeline
  current. The current timeline, playhead and page are restored after each,
  and any restore problem is in the result.
- **One writer at a time.** Every Claude Code session starts its own server,
  so all Resolve access takes a lock at `~/Library/Caches/rpresolve/resolve.lock`,
  shared with the CLI's write commands. `resolve_status` waits up to 5 s,
  the other reads 10 s and writes 30 s, then report Resolve as busy.
- **A record of every write.** A real run writes a synced "started" line to
  `~/Library/Logs/rpresolve-mcp/writes.jsonl` (mode 0600) before touching
  Resolve and a "finished" line after. If the started line cannot be
  written, the write does not happen.
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

## Check it against a live Resolve

`tests/live_mcp_sandbox.py` runs every tool against a sandbox project and
then checks that nothing that existed before changed. See its docstring
for the arguments. It refuses to run unless the open project's name
contains "Sandbox" and its unique id matches the one you pass. Run it by
hand; CI runs the offline tests only.
