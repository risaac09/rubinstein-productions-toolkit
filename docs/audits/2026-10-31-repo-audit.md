# Repo audit, 2026-10-31

**Status:** draft, 2026-10-31.

Report only. This audit changes no code. It follows the `ponytail-audit` method: correct, safe, holds under load, tested, fast, lean, in that order.

**What this repo does:** a facilitation and measurement methodology, plus production tools that work on anyone's footage: a DaVinci Resolve MCP server and command line (`production/rpresolve/`), a LUT lab (`production/lutlab/`), a stills pipeline (`production/rpstills/`), and small tools (`tools/vertcut/`, `tools/ograf-render/`, `tools/ipad-probe/`). Most outputs name private media, people and transcripts, and the repo is public, so the main safety rule is "private output never lands inside a git working tree".

**Load assumed:** one person on one Mac, running one tool at a time, on media that can be hours long, shoots of thousands of frames, and sources on a network share that may be asleep. The MCP server serves one local client.

**How to read a finding:** each has where it is, what goes wrong, and the smallest fix. "Test: no" means no test fails when it breaks. "Open PR" means a held pull request already changes that file; check it before fixing here. Nothing below is fixed by an open PR (#272, #286 to #292).

Counts: 10 must fix, 25 should fix, 5 nice to have. 40 in all.

## Must fix

1. **`transcribe` writes transcripts with no output guard** (`production/rpresolve/transcribe.py:231-235`)
   - Problem: the `--out`, `--srt` and `--txt` files are written with plain `open(path, "w")`. Run from inside any repo, the full transcript of a person's speech lands in that working tree, ready for `git add -A`.
   - Fix: check all three paths with `paths.out_problem(p, any_git_tree=True)` before the backend runs, and write with `paths.write_private`.
   - Test: no.

2. **The command-line guard refuses only this repo, and four writers skip the safe write** (`production/rpresolve/cli.py:152-157`, `cmd_detect.py:34,49`, `cmd_measure.py:35,52`, `cmd_shoot_table.py:74`, `proof.py:241`, `cmd_story_packet.py:34` against `:43`)
   - Problem: `cli.write_private` and the direct `out_problem(...)` calls omit `any_git_tree=True`, so `detect --out <another repo>/x.tsv` writes full media paths into that other repo. The MCP tools pass `any_git_tree=True` for the same outputs, so the rule differs by entry point. `detect`, `measure`, `shoot-table` and `pipeline-proof` also write with plain `open`, so an `--out` that is a hard link to a tracked file truncates it; `write_private` exists to stop that.
   - Fix: pass `any_git_tree=True` in `cli.write_private` and route the four plain writes through it.
   - Test: only the in-repo refusal (`production/tests/test_detect.py:681`).

3. **The MCP `survey` tool can be told to write anywhere** (`production/rpresolve/mcp/tools_offline.py:497-500`, `production/rpresolve/workflows.py:333-342`, `production/resolve_survey.py:1427-1438`). Open PR #290 changes `tools_offline.py`.
   - Problem: `projects` and `tree_labels` are free strings appended to the child's argv after `--out`. `projects: ["A", "--out", "/elsewhere/x.md"]` makes argparse take the second `--out`. The server checked only the first one against any git tree; the child checks only this repo.
   - Fix: give both arrays' items `"pattern": "^[^-]"` and `"minLength": 1`, or put `--` before the values.
   - Test: no.

4. **rpstills `index`, `run`, `cluster`, `cull`, `crops` and `review` have no git-tree guard** (`production/rpstills/__main__.py:77-112`, `production/rpstills/index.py:347,423-427`)
   - Problem: these write client JPEG proxies, `index.jsonl` (source paths) and `review.html` into any out folder, including a repo. `develop`, `finish`, `audit` and `absheet` all refuse a git tree; the commands that run first do not.
   - Fix: call `rpresolve.paths.out_problem(os.path.join(out, "index.jsonl"), any_git_tree=True)` once at the top of `main` for these commands.
   - Test: no.

5. **`vertcut cut` writes into the current folder by default** (`tools/vertcut/lib/vertcut_cut.py:387`)
   - Problem: the default `-o vertcut-out` is relative. Run from a repo root, the captioned clips and `.srt` files (client speech) land in the tree; `.gitignore` covers neither `vertcut-out/` nor `*.mp4`. The recipes in `tools/ipad-probe/recipes/` refuse a git tree.
   - Fix: refuse an output folder inside a git tree, as `tools/ipad-probe/recipes/loudness.sh:43-48` does.
   - Test: no.

6. **A rerun keeps a stale proxy, and two frames can share one id** (`production/rpstills/index.py:136-141`, `:101-105`)
   - Problem: an existing `proxies/<stem>.jpg` is reused without checking it came from this file. Index card A, then a different `P1000001.jpg` from card B into the same out folder, and faces, sharpness, crops and review thumbnails describe card A's picture. Separately, `X1.jpg` and `X1.jpeg` in one folder both get id `X1`, because the fallback id still strips the extension.
   - Fix: reuse a proxy only when it is newer than its source (or put source size and mtime in its name); keep the extension in the fallback id.
   - Test: no.

7. **One failed Vision batch throws away every face in the shoot** (`production/rpstills/vision.py:56-64`, used by `index.py:388` and `finish.py:348`)
   - Problem: images go to the helper in batches of 64. If any batch times out or exits non-zero, `faces()` returns `None` and the results of every earlier batch are dropped. A 5,000-frame shoot is then indexed with no faces, and `finish` stops, after one log line.
   - Fix: keep the batches that worked and mark only the failed batch's files (or retry that batch one file at a time).
   - Test: no (mocked only).

8. **An empty `items` list grades, reframes or stabilizes every item** (`production/rpresolve/workflows.py:837,1033,1206`, schemas at `production/rpresolve/mcp/tools_write.py:810-812` and the matching `reframe_apply` and `stabilize` schemas)
   - Problem: `wanted = sorted(set(items)) if items else all`. A client that sends `items: []` to mean "none" gets the whole track changed (stabilize is capped at 10 items).
   - Fix: add `"minItems": 1` to the three schemas.
   - Test: no.

9. **`true_peak_per_cut` passes when no shot was measured** (`production/rpresolve/qc.py:354-362`). Open PR #287 changes `qc.py`.
   - Problem: a shot whose loudness read fails is skipped with `continue`. If every read fails, the row says PASS, "N shots, worst None dBTP", and unmeasured shots still count in N.
   - Fix: count the failed shots and return WARN (or FAIL) naming them; leave them out of N.
   - Test: no.

10. **`ograf-render` deletes the old render and any `frames/` folder before it knows the new one will work** (`tools/ograf-render/ograf-render.mjs:317-318`)
    - Problem: `--out ~/project` where `~/project/frames/` holds other images loses them (`rmSync(..., {recursive: true})`). A rerun with bad `--data` deletes the previous good `.mov` and then fails at template load.
    - Fix: render frames into `mkdtempSync(join(outDir, ".frames-"))` and write `NAME.mov` to a temp name, renaming only after the checks pass.
    - Test: no.

## Should fix

11. **`endcheck` crashes on an out-point past the end of the audio** (`production/rpresolve/cutlist.py:359`)
    - Problem: with an empty envelope, `db` is `None` and `f"{db:.0f}"` raises `TypeError`, so every other row of the run is lost.
    - Fix: word the reason "no audio at the out-point" when `env` is empty.
    - Test: no.

12. **`HH:MM:SS:FF` at 29.97 is read as real seconds** (`production/rpresolve/cutlist.py:587-596`, `tools/vertcut/lib/vertcut_cut.py:144-146`, against `production/rpresolve/captions.py:337`)
    - Problem: Resolve's non-drop timecode counts 30 frames per label second. `captions.parse_timecode` handles that; the other two do not. A span at `01:00:00:00` lands 3.6 s early.
    - Fix: make both call `captions.parse_timecode` for the four-part form.
    - Test: no (only fps 24 is tested).

13. **Three frame-rate parsers snap NTSC rates differently** (`production/rpresolve/delivercheck.py:166-174`, `interchange.py:103-111`, `deliver.py:601-608`). Open PR #287 changes `delivercheck.py`.
    - Problem: `parse_fps` snaps only the exact strings `29.97` and `23.976`, so `--fps 29.970` becomes 2997/100 and a correct 30000/1001 file fails `deliver_check`; `interchange.parse_rate` would pass the same file.
    - Fix: one parser with the `parse_rate` tolerance, used by all three.
    - Test: exact strings only.

14. **Malformed JSON-RPC stops the MCP server** (`production/rpresolve/mcp/protocol.py:117-133,152-158,175`)
    - Problem: `notifications/cancelled` with list `params` raises in the reader thread, which dies, so the main loop waits forever. A request id that is a list cannot go into the `cancelled` set and raises before the handler's `try`, so the server exits.
    - Fix: wrap each line in `read_loop` in `try/except Exception` that answers INVALID_REQUEST, and accept only a dict `params` and a string or integer id.
    - Test: no.

15. **Long offline work runs while holding the Resolve lock, and cannot be cancelled** (`production/rpresolve/mcp/tools_write.py:560-561,606-607`)
    - Problem: `sync` runs its audio correlation and `trim_review_markers` its silence analysis inside `ResolveLock`, and `relink` walks every search root there. On a slow share this takes minutes; other calls get `ResolveBusy` after 30 s, and a client cancel does nothing.
    - Fix: measure before taking the lock, as `ingest` and `cut` already do, and pass `ctx.check_cancel` into the loops.
    - Test: no.

16. **Path matches ignore Unicode normal form in three write tools** (`production/rpresolve/workflows.py:1043,1050`, `production/rpresolve/markers.py:43,50`)
    - Problem: `reframe_apply` and `trim_review_markers` compare `realpath` strings directly. A plan that holds `Café.mov` in NFC and a clip Resolve stores in NFD (macOS file names) refuse every item. `ingest` and `relink` already normalise.
    - Fix: compare `unicodedata.normalize("NFC", os.path.realpath(p))`.
    - Test: no for these tools (`test_ingest.py`, `test_relink.py` cover the others).

17. **`apply_grade`'s plan hash covers file paths, not file contents** (`production/rpresolve/workflows.py:884-886`)
    - Problem: the hash includes the LUT or .drx path but not its bytes, and drops the .drx label manifest. Swap the file between the dry run and the real run and the old hash still applies the new grade.
    - Fix: add the sha256 of the LUT, .drx and manifest to the hashed plan, and require absolute paths.
    - Test: no.

18. **The MCP schema check is looser than JSON Schema** (`production/rpresolve/mcp/schema.py:43`, `:36-39`, `:70-78`)
    - Problem: `re.search` with `$` accepts a trailing newline, so a `cut` prefix of `"SW\n"` passes and names timelines with a newline. NaN passes every minimum and maximum. Whole floats are turned into integers only at the top level, so `items: [2.0]` crashes at list indexing.
    - Fix: use `re.fullmatch`, reject non-finite numbers, and coerce inside arrays.
    - Test: other bad prefixes only.

19. **`develop` and `finish` record results only at the end** (`production/rpstills/develop.py:745,858`, `production/rpstills/finish.py:274-275,353`)
    - Problem: a full disk at frame 200, or Ctrl-C, loses `calibration.json` or `finish.json` for every frame, while the TIFFs and JPEGs already written stay on disk unrecorded. `finish_frame` has no per-frame catch at all.
    - Fix: write the record after each frame (`develop.write` already merges rows from disk) and catch per frame.
    - Test: no.

20. **The review sheet puts file names into HTML unescaped** (`production/rpstills/review.py:82`)
    - Problem: `innerHTML` with `src="${f.proxy}"` breaks for a name with `#`, `?` or `%`, and a `"` or `<` in a file name injects markup into the page.
    - Fix: build the `img` with `document.createElement` and set `src` to `encodeURI(f.proxy)`.
    - Test: no.

21. **`cluster` mishandles time going backwards, mixed time zones and an empty shoot** (`production/rpstills/cluster.py:85-99,127`, `production/rpstills/times.py:37`)
    - Problem: a negative gap (two cameras, a rolled counter) passes every `gap > X` test, so frames a day apart join one burst. A sidecar time with `+01:00` next to naive EXIF times raises `TypeError`. `summary` calls `max()` on an empty list after `clusters.json` is written.
    - Fix: sort by time or use `abs(gap)`, drop `tzinfo` in `parse_iso`, and use `max(..., default=0)`.
    - Test: no.

22. **`crops` preview fails on no frames and on many flagged frames** (`production/rpstills/crops.py:160-165`)
    - Problem: an empty plan makes a zero-height image and `Image.new` raises. Every flagged frame is always drawn at 216 px a row, so a few hundred flagged frames exceed JPEG's height limit and the save fails, after `crops.json` was written.
    - Fix: return early on an empty plan and cap the rows with a "+N more" line.
    - Test: no.

23. **One bad LUT header stops the whole library scan** (`production/lutlab/library.py:59-60,97-100`)
    - Problem: `LUT_3D_SIZE` with no number, or a non-number, raises `IndexError` or `ValueError`; only `OSError` is caught, so no registry is written.
    - Fix: catch `(OSError, ValueError, IndexError)` and skip the file.
    - Test: no.

24. **`lutlab apply --video` does not set the colour matrix that `verify` uses** (`production/lutlab/apply.py:78-79` against `production/lutlab/verify.py:38`)
    - Problem: `verify` decodes with `in_color_matrix=bt709`; `apply` does not and tags nothing on output. On an untagged clip ffmpeg's default conversion differs, so the preview and the check measure different pixels. The only test that would catch it is skipped in CI and uses a tagged source.
    - Fix: put the same `scale=in_color_matrix=bt709:out_color_matrix=bt709` before `lut3d` and tag the output bt709.
    - Test: tagged source only, skipped in CI.

25. **`whisper_cpp` has no timeout** (`production/rpresolve/transcribe.py:153`)
    - Problem: `subprocess.run` with no `timeout` hangs forever on a file on a sleeping share, and buffers all of whisper's output.
    - Fix: add a `timeout` scaled to the duration and send stdout to `DEVNULL`.
    - Test: no.

26. **Long recordings are decoded whole into memory, more than once** (`production/rpresolve/shots.py:61-79`, `production/rpresolve/dxmetrics.py:109-134`, `production/rpresolve/qc.py:492,749`, `production/lutlab/balance.py:155-171`)
    - Problem: `shots` holds every frame of a 3 h clip (about 0.9 GB, then a copy per dissolve length); `dxmetrics.decode` turns a 3 h stream into about 6 GB of float64; `qc` runs the same full-file `ebur128` pass twice; `balance --frames 8` holds eight full-resolution float64 frames (about 4.7 GB at 6048x4032).
    - Fix: refuse above a stated duration or frame budget, reuse the first `ebur128` result in `qc`, and shrink each frame inside the loop in `balance`.
    - Test: no.

27. **Audio stream choice differs between decoders** (`production/rpresolve/cutlist.py:258-261` against `0:a:N` in `audioreport.py`, `besttake.py`, `shoot_table.py`)
    - Problem: `decode_pcm` has no `-map`, so ffmpeg picks the audio stream with the most channels. With a stereo track 0 and a 4-channel track 1, `trim-review`, `wordsnap`, `beats` and `endcheck` hear track 1 while `besttake` and `shoot-table` hear track 0.
    - Fix: add `-map 0:a:0` in `decode_pcm`.
    - Test: no.

28. **Helper binaries are compiled straight to their cached name, by three copies of the same code** (`production/rpstills/vision.py:30-44`, `production/rpstills/develop.py:274-290`, `production/rpresolve/vision.py:23-40`)
    - Problem: `swiftc -o <final path>` writes in place. An interrupted compile, or two processes at once, can leave a broken executable that `os.access(X_OK)` accepts, and every later run fails until someone deletes the cache.
    - Fix: one shared function that compiles to a temp name and `os.replace`s it.
    - Test: no.

29. **`offload.sh copy` accepts a destination inside a git working tree** (`production/offload.sh:91-92`)
    - Problem: `copy /Volumes/CARD . --cam A7` from a repo folder writes the card's footage and sidecars there. The iPad `verify-copy.sh` refuses this case.
    - Fix: the same walk-up `.git` check on `$parent`, exit 2.
    - Test: no.

30. **`offload.sh safe` says SAFE TO FORMAT for a local folder** (`production/offload.sh:190-199`)
    - Problem: any rclone path counts as the offsite copy, so `safe <dir> <dir>` or a folder on the same disk passes and prints "SAFE TO FORMAT".
    - Fix: require a `remote:` form and refuse when it resolves to `$dir`.
    - Test: no (the fake rclone always gets `remote:bucket/x`).

31. **`burst-gate` skips every check for a file name git quotes** (`scripts/burst-gate:61-68`)
    - Problem: git writes non-ASCII names in quotes (`"b/caf\303\251.md"`). The awk rule `/^\+\+\+ / { path = "" }` then drops all of that file's added lines from the privacy and voice scans, and a quoted `phase-zero/` name misses the export check.
    - Fix: run both `git diff` calls with `-c core.quotepath=false` and fail on any name that still starts with `"`.
    - Test: no.

32. **`burst-gate` G3 says "as CI runs them" but runs fewer checks** (`scripts/burst-gate:12-13,148-162`)
    - Problem: the default set leaves out `production/tests/test-offload.sh`, the Node tests in `tools/ograf-render/`, shellcheck and the binary checks that `ci.yml` runs, so a branch passes the gate and fails CI.
    - Fix: add `test-offload.sh` and `node --test` on the tracked `test*.mjs`, or reword the header.
    - Test: no.

33. **`vertcut` adds one ffmpeg input and one overlay per caption cue, with no limit** (`tools/vertcut/lib/vertcut_cut.py:514-526`)
    - Problem: cues are up to 3 words, so a 10-minute clip is about 500 looped inputs and 500 chained overlays on 1080x1920 frames; memory and time blow up with no warning.
    - Fix: count cues first and refuse above a stated limit, telling the user to cut a shorter clip.
    - Test: no.

34. **Code and docs name the author as the actor of a procedure** (`production/rpresolve/mcp/tools_write.py:427,891`, `production/rpresolve/mcp/server.py:18`, `production/rpresolve/mcp/session.py:6`, `production/rpresolve/trimreview.py:7`, `production/rpstills/cluster.py:19`, `production/rpstills/audit.py:46`, `production/rpresolve/mixbuild.py:175`, `production/rpstills-audit.md:5`, `production/cue-marker-sync.md:51`). Open PR #290 changes `server.py`.
    - Problem: strings that name the author as the one who starts renders reach a stranger's client through the MCP tool descriptions and server instructions. The repo's own rule says the author is not the actor here.
    - Fix: "the operator" or "a person"; for the signed defaults, "working defaults until ...".
    - Test: no.

35. **Two production docs describe one business's client procedure** (`production/return-package.md:24,113-139`, `production/delivery-manifest.md:87-124`). Open PRs #289 and #287 change these files.
    - Problem: "RP deletes its copy 90 days after...", "RP sends a new review copy": a named producer's retention clock and client service steps, which the repo rule puts in the private ops repo.
    - Fix: make the actor "the producer" and the numbers defaults to set, or move the RP-specific sections to the private repo.
    - Test: no.

## Nice to have

36. **`timeline_items` and `media_pool` build every row, then slice one page** (`production/rpresolve/mcp/tools_read.py:209-213,291-295`). Open PR #290 changes this file.
    - Problem: a 1,500-item timeline with grades costs tens of thousands of API calls for every page, inside the 10 s read lock.
    - Fix: slice to `offset` and `limit` before building rows; count the total separately.

37. **`rpstills serve` shows the blind key and has two small gaps** (`production/rpstills/serve.py:74-85`, `production/rpstills/absheet.py`)
    - Problem: the directory listing shows `key.json` next to `sheet.jpg`, so the A/B/C answer is one tap away for the reviewer. A `Range` header with thousands of digits makes `int()` raise with no reply. There is no `Host` check against DNS rebinding.
    - Fix: write the key to a sibling folder outside the served one, cap the header length, and reject a `Host` that is not the bound address or `localhost`.

38. **CI has no `permissions` block and the Pages job uses tag-pinned actions** (`.github/workflows/ci.yml:1-14`, `.github/workflows/deploy-architecture.yml:34-46`). Open PRs #286 and #272 change `ci.yml`.
    - Problem: CI gets the repo's default token scope, and a moved tag on an action with `pages: write` runs new code with that right.
    - Fix: `permissions: contents: read` in `ci.yml`, and commit SHAs for the deploy actions.

39. **A methodology doc calls itself internal and points at private notes** (`methodology/three-type-architecture.md:5,27`)
    - Problem: "It is an internal canonical", and "Read alongside" names a positioning note that is not in the repo. A reader cannot follow the reference.
    - Fix: drop the internal framing and the private reference, or link the public `measurement-framework.md` instead.

40. **The author's machine and storage are named in comments and docs** (`production/rpresolve/api.py:42`, `production/rpresolve/detect.py:13`, `production/resolve-template-spec.md:1013`, `production/rpstills-develop.md:106`, `prompts/skills/agentic-development.md:43`). Open PRs #289 and #290 change `resolve-template-spec.md`.
    - Problem: the author's own machine model, the author's network storage, and a personal monthly spend figure. Harmless, but the repo rule names the author's machine as out of scope.
    - Fix: "on a recent Apple silicon Mac", "against real footage", and drop the spend figure.

Left out: smaller items (a `stabilize` plan hash that can be replayed, `workflows.py:1227-1229`; the render-in-progress refusal applied by only some writes; per-row word scans in `trimreview.py` and `pacing.py`; `manifest.py:66-71` printing a frame label as a duration; the `burst-gate` placeholder filter at `:138` dropping a whole line; `verify-copy.sh` using `mkdir -p` after a long hash).

## Delete, merge, split

- **Merge the output guards.** `production/lutlab/guard.py:22` and `production/rpresolve/paths.py:41` implement "not in a git tree" twice, with different defaults; the rpresolve one defaults to this repo only. One function with `any_git_tree=True` as the default closes findings 1, 2, 4 and 5 together.
- **Merge the timecode and frame-rate parsers.** `cutlist.parse_timecode`, `vertcut_cut.parse_tc` and `captions.parse_timecode`; `delivercheck.parse_fps`, `interchange.parse_rate`, `deliver.exact_fps` and ad hoc reads in `stingsync.py`, `sync.py`, `picreview.py`, `rhythm.py`, `proof.py`. Keep `captions.parse_timecode` and one rate parser (findings 12, 13).
- **Merge the Swift helper builders** in `rpstills/vision.py`, `rpstills/develop.py` and `rpresolve/vision.py` into one (finding 28).
- **Merge the cube writers** `rpstills/look.py:79` and `lutlab/cube.py:102`; they differ on overwrite and neither escapes a `"` in the title.
- **One constant for the `[auto]` suffix.** `AUTO = " [auto]"` is defined in `clipline.py:47`, `cut.py:61`, `syncbuild.py:37` and `workflows.py:604`; a change to one must reach all four.
- **Move out, per the 2026-10-18 cut:** the RP-specific parts of `production/return-package.md` and `production/delivery-manifest.md` (finding 35), and the internal framing of `methodology/three-type-architecture.md` (finding 39).
- **Kit export, fix at the kit source, not here:** I made no findings in `phase-zero/`, `public-kit/` or `.claude/`.

## Verdict

Fix the git-tree guard first: findings 1 to 5 are one rule (private output never lands in a git working tree) applied in some entry points and not others, and merging the two guard functions with `any_git_tree=True` as the default closes most of them at once.

## Not checked

- Nothing was run against real media, Resolve, a network share or a browser. Findings on timeouts, memory and ffmpeg colour handling (24 to 26) come from reading the code.
- Read only in part: `production/rpresolve/mixbuild.py`, `reframe.py`, `perceive.py`, `cut.py`, `syncbuild.py`, `clipline.py`, `production/lutlab/report.py`, `model.py`, `fit.py`, the BRAW helper, and the methodology documents beyond the privacy sweep.
- Not run: the `tools/ograf-render` and `tools/ipad-probe` tests.
- `phase-zero/` and `public-kit/` were skipped as a kit export.
- Git history was not reviewed in this audit; only the current tree was read.
