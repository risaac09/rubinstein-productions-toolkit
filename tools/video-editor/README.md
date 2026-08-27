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
