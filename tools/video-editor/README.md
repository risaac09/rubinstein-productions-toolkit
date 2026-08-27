# video-editor (prototype)

Driving video editing through Claude Code instead of a GUI editor. This wraps
[`video-use`](https://github.com/browser-use/video-use) (MIT), vendored at
`video-use/` (commit `92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66`, 2026-07-01).

Bounded prototype of the deterministic edit-cutting core. Not a finished tool.

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
# vendored already at tools/video-editor/video-use; for a fresh clone elsewhere:
git clone https://github.com/browser-use/video-use.git

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

## What was verified vs. what needs a real API key

**Verified end to end (no Scribe key needed):**

- `helpers/timeline_view.py <video> <start> <end> -o <out.png>`: filmstrip
  and waveform composite. Ran against a synthetic 8s test clip
  (`test-footage/synthetic_clip.mp4`, generated with `ffmpeg -f lavfi
  testsrc+sine`), produced a correct 10-frame filmstrip with waveform.
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
- Phase 2: Remotion for titles and motion graphics. `SKILL.md` already
  names Remotion (and HyperFrames, Manim) as pluggable animation engines for
  the `overlays` stage of the EDL. None of that was built or exercised here.
  That's the next increment once the cut-and-render core above is trusted
  against real transcripts.

## Phase 2: Remotion motion graphics (`motion/`)

A minimal Remotion project at `motion/`, scaffolded with `create-video`'s
`--blank` template (free tier, no license needed at this scale — Remotion is
free for teams up to 3). One working composition: `src/LowerThird.tsx`, a
name + role title card at 1920x1080/30fps with a spring-eased entrance
(20 frames), a hold (60 frames), and an eased exit (15 frames) — 95 frames /
~3.2s total. Registered as composition id `LowerThird` in `src/Root.tsx`.

**Verified, not just written:**

```bash
cd motion
npm install                                   # ~18s, 367 packages, clean
npx tsc --noEmit                              # typechecks clean
npx remotion render LowerThird out/lower-third.mp4
```

Render took ~8.6s wall time (95 frames, this machine). `ffprobe` confirmed
the output: h264, 1920x1080, 30fps, 3.22s duration, 213KB. Pulled frames at
n=8 (mid-entrance, text visibly lower-opacity and offset) and n=45
(mid-hold, fully settled) and inspected them directly — the card renders
correctly, not a blank or garbled frame. `out/` is gitignored (already set
up by the scaffold), so the rendered MP4 isn't committed; re-render with the
command above to reproduce it.

**How this fits with the video-use core, honestly:**

Not wired together. This is a standalone Remotion composition, rendered to
its own MP4, independent of `video-use/edit/edl.json`. The intended shape,
per `SKILL.md`'s existing mention of Remotion as an `overlays` engine: `video-use`
produces the cut base video from source footage via its EDL; Remotion
renders graphics elements (lower thirds, titles, intro/outro cards) as
separate clips with alpha or as their own MP4s; a compositing step (ffmpeg
overlay filter, most likely inside `helpers/render.py`'s existing overlay
stage) lays the Remotion output on top of the base video at specified
timestamps. None of that compositing step exists yet — this phase only
proves the Remotion side renders deterministically and can iterate the same
way any other React code does (edit `LowerThird.tsx`, re-render, diff the
frame). Wiring the two together is the next increment, not this one.

**Friction notes, for anyone treating this as a repeatable pipeline later:**

- `create-video --blank --no-tailwind` still ships Tailwind in
  `package.json`/`index.css` — the flag didn't suppress it for this
  template. Harmless here since `LowerThird.tsx` doesn't use Tailwind
  classes, but worth knowing before assuming `--no-tailwind` actually drops
  the dependency.
- `npm install` pulled 367 packages for a single lower-third composition.
  Fine for a prototype; would be worth trimming (drop Tailwind, ESLint
  config, etc.) if this becomes a long-lived tool rather than a demo.
- Render time (~8.6s for 95 frames / ~3.2s of output) is fast on this
  machine for a text-only composition. Untested: how render time scales
  with video-in-video compositing, more complex animations, or longer
  durations — the real cost driver for a titling pipeline is unknown until
  something heavier than text-on-a-color-card gets rendered.
- Node 24 / npm 11 worked with no version pinning needed, unlike `video-use`
  which required a Python version pin. No Node-version friction hit in this
  session, but it also wasn't tested against an older Node the way
  `video-use` surfaced the Python 3.9 vs 3.10 mismatch.
- `npx create-video@latest` with a template flag but no `--yes` still opens
  an interactive prompt despite the template flag being present; the actual
  non-interactive invocation needs both `--yes` and a template flag
  together (`npx create-video@latest --yes --blank --no-tailwind motion`).
