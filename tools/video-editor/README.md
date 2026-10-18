# video-editor (prototype)

Driving video editing through Claude Code instead of a GUI editor. This wraps
[`video-use`](https://github.com/browser-use/video-use) (MIT), which is not
stored in this repo. `fetch-video-use.sh` clones it into `video-use/` at commit
`92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66` (2026-07-01), and `.gitignore` keeps
that folder out of version control. The `motion/` folder and these notes are
Rubinstein Productions work. `video-use` is Browser Use's work under its own
MIT licence; the `LICENSE` file lives in the upstream repo and in the folder
the script creates. The current tree no longer carries those files. Earlier
commits still do, with the same licence file.

Bounded prototype of the deterministic edit-cutting core. Not a finished tool.

Paths below that start with `video-use/` exist only after you run the fetch
script (see Setup).

## What video-use actually does

It is a Claude Code skill (`SKILL.md` + `helpers/`), not a standalone CLI
app. The agent reads `SKILL.md`, converses with the user about the footage,
and drives the helper scripts directly. Pipeline:

1. **Transcribe.** `helpers/transcribe.py` calls ElevenLabs Scribe for
   word-level timestamps and speaker diarization, cached to
   `edit/transcripts/`.
2. **Pack.** `helpers/pack_transcripts.py` collapses the raw Scribe JSON into
   a compact phrase-level markdown (`takes_packed.md`), the primary artifact
   the LLM reasons over (word-boundary precision at roughly 1/10 the tokens).
3. **LLM reasoning.** The agent, not a script, picks cut points from the
   packed transcript and drills into ambiguous moments with
   `helpers/timeline_view.py` (a filmstrip and waveform PNG for a time
   range).
4. **EDL.** Cut decisions serialize to `edit/edl.json` (sources, ranges,
   grade preset, overlays, subtitles; see format below).
5. **Render.** `helpers/render.py` executes the EDL deterministically with
   ffmpeg: per-segment extract, lossless concat, grade, overlays, subtitles
   (always last), loudness normalization. `helpers/grade.py` is the
   standalone color-grade filter-chain tool it also uses per segment.
6. **Self-evaluate.** The agent renders a preview, samples `timeline_view`
   at every cut boundary, and checks for pops, flashes, and misaligned
   overlays before showing the user anything.

No frame-by-frame vision model. The transcript plus on-demand visual
composites is the only thing the LLM reasons over. The render itself is
plain deterministic ffmpeg.

## Setup that actually worked

Environment: macOS, ffmpeg 8.1.2 (Homebrew), uv 0.11.25, Python 3.10 (the
system default is 3.9.6; video-use's `pyproject.toml` requires `>=3.10`, so a
project-local venv pinned to 3.10 was necessary).

```bash
# from the repo root: fetch video-use at the pinned commit and apply
# keep-source-frame-rate.patch (see "Frame rate" below; the script fails and
# removes the folder if the pin or the patch does not check out)
# (refuses to run if video-use/ already has files in it; if you have an older
# checkout with the vendored copy, move that folder aside first, then copy
# back any .venv, edit/ or .env you kept in it once the fetch has run)
sh tools/video-editor/fetch-video-use.sh

cd tools/video-editor/video-use
uv venv .venv --python 3.10
uv sync --python 3.10          # requests, librosa, matplotlib, pillow, numpy
brew install ffmpeg            # already present in this environment
```

No console scripts. Every helper is invoked directly as
`python helpers/<name>.py`, resolved relative to this directory.

ElevenLabs Scribe needs `ELEVENLABS_API_KEY` in `.env` at this directory's
root (see `.env.example`) or in the environment. Not configured in this
environment. See below for what that does and doesn't block.

## Frame rate

Upstream's `render.py` extracts every segment with `-r 24`, which turns a
23.976 source (the house capture standard) into 24 fps. The fetch script
applies `keep-source-frame-rate.patch`, a hunks-only diff of `helpers/render.py`
beside it (upstream's MIT licence; the header names the pin). It probes the
first range's source with `ffprobe` and extracts every segment at that exact
rational, such as 24000/1001. Rule for mixed-rate EDLs: the first range's
source sets the rate for the whole render, because the concat is lossless, and
a source at another rate is converted to it. If the probe fails it falls back
to 24. Concat, overlay and loudnorm assume no rate, so nothing else changed.

Verified 2026-10-17 on synthetic 8 s clips (ffmpeg `testsrc2` + `sine`,
1920 by 1080), each cut into two ranges (4.5 s), with and without a 2 s
ProRes 4444 alpha overlay made by ffmpeg (the Remotion recipe was not rerun for
this):

| Source | Output rate | Frames | Video / audio duration |
|---|---|---|---|
| 24000/1001 | 24000/1001 | 108 | 4.5045 s / 4.5233 s |
| 30000/1001 | 30000/1001 | 135 | 4.5045 s / 4.5233 s |
| 25 | 25 | 113 | 4.520 s / 4.521 s |

The overlay and plain renders matched. Sampled frames show the overlay inside
its window and the bare pattern after it. Audio runs under one frame longer
than video (AAC frame padding), inside the 30 ms edge fades' reach; no drift
was seen. Not checked: real footage, a mixed-rate EDL, a variable-frame-rate
source, UHD.

If upstream moves the pin, regenerate the patch against the new commit. Worth
offering upstream as a pull request (hard-coded 24 fps is a bug for any
23.976 or 25 fps user); not opened.

## What was verified vs. what needs a real API key

**Verified end to end (no Scribe key needed):**

- `helpers/timeline_view.py <video> <start> <end> -o <out.png>`: filmstrip
  and waveform composite. Ran against a synthetic 8s test clip
  (`test-footage/synthetic_clip.mp4`, generated with `ffmpeg -f lavfi
  testsrc+sine`), produced a correct 10-frame filmstrip with waveform.
  The clip and a `uv.lock` were local additions to the earlier vendored
  copy, not upstream files, so the fetched copy has neither. Regenerate
  the clip with that ffmpeg command, and `uv sync` makes its own lock, so
  dependency versions can differ from the earlier runs recorded here.
- `helpers/render.py edit/edl.json -o edit/preview.mp4 --preview
  --no-subtitles`: the full deterministic render stage, run against a
  hand-authored EDL (`edit/edl.json`, not committed, a session output)
  cutting two ranges from the synthetic clip. Confirmed: per-segment
  extraction, lossless concat, per-segment `warm_cinematic` grade, two-pass
  loudness normalization (-14 LUFS target), output duration matched the
  EDL's `total_duration_s` (4.538s measured vs. 4.5s specified, encoder
  rounding).
- `helpers/grade.py <in> -o <out> --preset neutral_punch`: standalone
  color-grade filter chain, ran clean against the synthetic clip.
- `helpers/grade.py --list-presets`: confirms the four shipped presets
  (`subtle`, `neutral_punch`, `warm_cinematic`, `none`) and their exact
  ffmpeg filter strings.
- Every helper's `--help` output, confirming CLI argument shapes match
  `SKILL.md`'s documentation.

**Could NOT verify. Needs a real `ELEVENLABS_API_KEY`:**

- `helpers/transcribe.py <video>`: confirmed it fails fast and cleanly
  (`ELEVENLABS_API_KEY not found in .env or environment`) rather than
  hanging or silently degrading, but the actual Scribe call, word-level
  timestamp accuracy, and diarization were not exercised.
- `helpers/transcribe_batch.py`: parallel multi-file transcription, same
  blocker.
- `helpers/pack_transcripts.py`: needs real `transcripts/*.json` from a
  Scribe call to pack into `takes_packed.md`. Not run.
- The full agent-driven flow (converse, confirm strategy, select cuts from a
  real transcript, self-eval loop). This is the part `SKILL.md` actually
  describes as the product. What was verified here is the deterministic
  execution floor underneath it: given any valid EDL, render.py, grade.py,
  and timeline_view.py work correctly. Whether an agent reliably produces a
  good EDL from a real transcript is untested.

No transcript was mocked or substituted as a stand-in for a real Scribe
result. The gap above is real, not papered over.

## EDL format (for reference)

```json
{
  "version": 1,
  "sources": {"clip_id": "/abs/path/to/source.mp4"},
  "ranges": [
    {"source": "clip_id", "start": 0.5, "end": 2.5, "beat": "A", "quote": "...", "reason": "..."}
  ],
  "grade": "warm_cinematic",
  "overlays": [],
  "subtitles": null,
  "total_duration_s": 2.0
}
```

## Next steps

- Get an ElevenLabs API key into this environment and re-run
  `transcribe.py` then `pack_transcripts.py` against real footage with
  speech, to verify the actually hard part: word-boundary cut accuracy and
  whether the packed transcript gives the LLM enough signal to pick good
  cuts.
- Register as a Claude Code skill (`ln -sfn` into `~/.claude/skills/`) once
  there's real footage to edit against, per `video-use/install.md`.
- Run the frame-rate patch against real 23.976 footage and a mixed-rate EDL,
  then decide whether to offer it upstream.

## Phase 2: Remotion motion graphics (`motion/`)

A minimal Remotion project at `motion/`, scaffolded with `create-video`'s
`--blank` template (free tier, no license needed at this scale: Remotion is
free for teams up to 3). One component, `src/LowerThird.tsx`, a name + role
title card with a spring-eased entrance, a hold and an eased exit, timed in
seconds (about 3.2s). It is registered in `src/Root.tsx` as `LowerThird`
(3840 by 2160), `LowerThirdPortrait` (2160 by 3840) and `LowerThirdSquare`
(2160 by 2160), all at 24000/1001 fps (23.976). Width, height and fps are
props. The first version was 1920 by 1080 at 30 fps (95 frames); the
verification notes below describe that version.

### Alpha render recipe

A lower third that goes over footage in Resolve needs a transparent background.
The recipe below was run on `LowerThirdPortrait` and, with `--props`, on
`LowerThird`, then probed with `ffprobe` and `ffmpeg`: ProRes 4444, 24000/1001
fps, 76 frames, a transparent first frame and an opaque frame mid-hold. On
2026-10-16 the UHD render was imported into Resolve on a track above UHD
23.976 footage, and the transparency held: the footage showed around the
block, with no black frame.

```bash
cd tools/video-editor/motion
npm ci
npx remotion render src/index.ts LowerThird out/lower-third.mov \
  --codec=prores --prores-profile=4444 \
  --pixel-format=yuva444p10le --image-format=png \
  --props='{"name":"Name Here","role":"Role Here"}'
```

- `--codec=prores` with `--prores-profile=4444` writes ProRes 4444 in a `.mov`.
- `--pixel-format=yuva444p10le` carries the alpha channel.
- `--image-format=png` is required with that pixel format. `motion/remotion.config.ts`
  sets JPEG for frames, and a JPEG frame has no alpha, so Remotion refuses the
  render without the PNG flag.
- Swap `LowerThird` for `LowerThirdPortrait` or `LowerThirdSquare` for 9:16 or 1:1.
- `--props` also takes `width`, `height` and `fps`, for example
  `'{"name":"A","role":"B","width":1920,"height":1080,"fps":25}'` for an HD, 25 fps project.
- `--props` also takes `tokens`, the brand values by token name (see
  `production/design-tokens.md`). Keep them in a props file outside the repo,
  or in `motion/*.local.json`, which git ignores, and pass the path:
  `--props=./brand.local.json`. The file holds `name` and `role` too.

`npx remotion render` has no per-flag `--help`; `npx remotion help render`
lists commands only. The flags above were checked by running them on Remotion
4.0.518, and the alpha recipe again on 4.0.536.

**Verified, not just written (first version, 1920 by 1080 at 30 fps):**

```bash
cd motion
npm install                                   # ~18s, 367 packages, clean
npx tsc --noEmit                              # typechecks clean
npx remotion render LowerThird out/lower-third.mp4
```

Render took ~8.6s wall time (95 frames, this machine). `ffprobe` confirmed
the output: h264, 1920x1080, 30fps, 3.22s duration, 213KB. Pulled frames at
n=8 (mid-entrance, text visibly lower-opacity and offset), n=45 (mid-hold,
fully settled), and n=94 (last rendered frame, fully faded to black) and
inspected them directly: the card enters, holds, and exits correctly, not a
blank or garbled frame. `out/` is gitignored (already set up by the
scaffold), so the rendered MP4 isn't committed; re-render with the command
above to reproduce it.

A `/code-review` pass on this commit caught one real bug before push: the
exit fade's interpolate window ended at frame 95, one past the last frame
Remotion actually renders (0..94), so the card never fully finished fading
out; the last frame showed a faint ghost of the card instead of black.
Fixed by ending the exit window at `TOTAL_DURATION - 1`; re-rendered and
confirmed frame 94 is now fully black.

**How this fits with the video-use core:**

No new code is needed. `render.py`'s overlay stage already composites any
file named in the EDL's `overlays` list: it shifts the overlay so its first
frame lands at `start_in_output`, lays it over the base video with ffmpeg's
`overlay` filter for `duration` seconds, and burns subtitles after it. The
ffmpeg `overlay` filter reads the ProRes 4444 alpha channel, so the alpha
render from the recipe above goes straight in:

```json
"overlays": [
  {"file": "lower-third.mov", "start_in_output": 1.0, "duration": 3.17}
]
```

A relative `file` resolves against the EDL's own folder.

Verified 2026-10-17, at the pinned commit with ffmpeg 9.0.2: a 1920 by 1080
lower third rendered with the alpha recipe (`"width":1920,"height":1080` in
`--props`), laid by `render.py` over a two-range cut of a synthetic 8 s clip.
A frame mid-hold shows the block over the test pattern with the pattern
visible around it. A frame after the window shows the pattern alone, with no
black frame and no ghost of the block.

Two rules come from how the stage works:

- **Render the overlay at the size of the base video.** The filter neither
  scales nor centres it. `render.py` scales a landscape base to 1920 wide
  (1280 wide with `--draft`) and a portrait base to 1920 tall (1280 tall with
  `--draft`), so a UHD lower third fits none of them. Pass `width` and
  `height` in `--props` to match, for example 1920 by 1080 or 1080 by 1920.
- **Set `duration` to the overlay's own length**, 76 frames at 23.976 or
  about 3.17 s, so the window does not cut the exit short.

What this does not cover: the base now keeps the source rate (see Frame rate),
but nothing here was checked on real footage or at UHD.

**Friction notes, for anyone treating this as a repeatable pipeline later:**

- `create-video --blank --no-tailwind` still ships Tailwind in
  `package.json`/`index.css`; the flag didn't suppress it for this
  template. Harmless here since `LowerThird.tsx` doesn't use Tailwind
  classes, but worth knowing before assuming `--no-tailwind` actually drops
  the dependency.
- `npm install` pulled 367 packages for a single lower-third composition.
  Fine for a prototype; would be worth trimming (drop Tailwind, ESLint
  config, etc.) if this becomes a long-lived tool rather than a demo.
- Render time (~8.6s for 95 frames, ~3.2s of output) is fast on this
  machine for a text-only composition. Untested: how render time scales
  with video-in-video compositing, more complex animations, or longer
  durations. The real cost driver for a titling pipeline is unknown until
  something heavier than text-on-a-color-card gets rendered.
- Node 24 / npm 11 worked with no version pinning needed, unlike `video-use`
  which required a Python version pin. No Node-version friction hit in this
  session, but it also wasn't tested against an older Node the way
  `video-use` surfaced the Python 3.9 vs 3.10 mismatch.
- `npx create-video@latest` with a template flag but no `--yes` still opens
  an interactive prompt despite the template flag being present; the actual
  non-interactive invocation needs both `--yes` and a template flag
  together (`npx create-video@latest --yes --blank --no-tailwind motion`).
