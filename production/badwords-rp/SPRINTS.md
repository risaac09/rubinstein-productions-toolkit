# BadWords RP — Sprint Log

AI editing automation workflow built on top of [BadWords](https://github.com/veritus-git/BadWords), adapted for Rubinstein Productions' Founder Story / Program Engagement / Organizational Embedding tiers.

## Why this exists

BadWords by default cuts ums, false starts, retakes. Polish optimization. That breaks the Say Why methodology, where exactly those moments are the polyvagal/somatic data the work is built on. So the RP adaptation is not a wrapper, it's a mode switch.

Three modes:

| Mode | Use case | Behavior |
|---|---|---|
| Polish | Program Engagement deliverables, social cuts, corporate testimonials | BadWords default. Cut filler. |
| Witness | Founder Story, Bilingual Dashboard interviews, anything where the speaker is metabolizing | Mark fillers but do not cut. Keep texture. Markers become a heat-map for emotional pivots. |
| Selective | Hybrid use case, e.g. a Founder Story tier with a polished social cut | Mark all, editor decides per-instance. |

The transcription prompt in `~/rp-shared/worker/index.js` already encodes the Witness rule: "Keep ums, false starts, and self-corrections. They are data about how the speaker is processing." This work makes that rule operational at the cut layer, not just the transcript layer.

---

## Sprint 1 — Inventory (closed 2026-05-24)

### Findings

**BadWords (not yet installed)**
- `~/Downloads/BadWords-main/`, MIT, by 17yo solo dev Simon (veritus-git)
- Python + PySide6 GUI, Faster-Whisper local transcription
- Workflow: transcribe → IDE-style transcript editor with color codes (red=filler, blue=retake, green=typo) → assemble new Resolve timeline with cuts and markers
- Mac caveat: not compatible with App Store Resolve, needs official install
- Resolve install path: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/` (subfolders exist, empty)

**RP-side DaVinci automation already built**
- `production/resolve-template-spec.md` v1.1 (April 2026) — three-camera template (iPhone Apple Log, GH7 V-Log, GH5 V-Log L), node structure, bin plan, 4K-only export presets
- `production/resolve_workflow.py` — 916 lines of Resolve API CLI. Already does: new-project, import-media (camera-aware), build-timeline, add-subtitles, auto-subtitle, apply-lut, apply-drx, render-all (youtube/linkedin/master/story)

**saywhy-app (client-facing PWA)**
- `~/saywhy-app/`, deployed to `saywhy.app`
- 4-phase reflection scaffold, audio recording in IndexedDB, paid Whisper transcription via `rp-shared` Cloudflare Worker
- Voice firewall enforced (no em-dash, no rule-of-three, no false agency)
- Transcription prompt explicitly preserves ums and false starts (Witness rule already encoded)

**rp-shared/worker (Cloudflare Worker)**
- `POST /api/saywhy/transcribe` (Claude Sonnet, paid only)
- `POST /api/saywhy/format-pdf` (Claude Sonnet, paid only)
- Stripe checkout, JWT, rate limits

**Media assets**
- 138 LUTs in `~/DaVinci Resolve Media/.LUT/`
- PowerGrades and `.drx` saves not surfaced from filesystem, likely embedded in Resolve projects

### Central RP adaptation problem (named)

BadWords default cuts the texture. Say Why methodology preserves it. The config layer is the fix. See the three-mode table above.

### Sprint 1 → Sprint 2 handoff

Smallest first move for sprint 2: build the config layer. Confirmed cadence: GH issue-tracked in this repo.

---

## Sprint 2 — Config layer (closed 2026-05-24)

[Issue #2](https://github.com/risaac09/rubinstein-productions-toolkit/issues/2)

### Findings

**BadWords settings architecture**
- `DEFAULT_SETTINGS` dict lives at `~/Downloads/BadWords-main/src/config.py:360`
- Persisted to `settings.json` per the docstring at line 357
- `DEFAULT_BAD_WORDS = ["yyy","eee","aaa","umm","uh","ah","mhm"]` at line 56
- Runtime reads `filler_words` from settings with `DEFAULT_BAD_WORDS` fallback (verified in gui.py:7982, 8294, 8417, 9904)

**Cut vs. mark switch found**
- `fs_cut_mode` (default `true`) and `fs_mark_mode` (default `false`) are mutually exclusive toggles in the Fast Silence flow
- UI: gui.py:9523-9550, mutually exclusive via cross-toggle handlers
- Labels: `lbl_cut_silence_directly` / `lbl_mark_silence_with_color`
- For transcript-driven flow, cut behavior is controlled by what the user paints red in the editor

**Whisper verbatim prompt**
- `GOLDEN_INITIAL_PROMPT` at config.py:64 preserves stutters, withdrawals, broken words
- Matches the Say Why transcription rule already in `~/rp-shared/worker/index.js`
- Used in `witness.yaml` and `selective.yaml`

**Anti-finding (worth flagging)**
- The Explore agent fabricated a "Polish mode / Witness mode" mapping in BadWords source. Those terms don't exist in BadWords. The RP modes are entirely our overlay. Verified by direct grep before building configs.

### Deliverables (in this dir)

- [configs/polish.yaml](configs/polish.yaml)
- [configs/witness.yaml](configs/witness.yaml)
- [configs/selective.yaml](configs/selective.yaml)
- [README.md](README.md) — full mapping table, mode workflows, methodology firewall

### Not done (sprint 3 scope)

- `load-config.sh` adapter (YAML → BadWords settings.json merge)
- Confirm BadWords install path on macOS (placeholder noted in README)
- Validate witness.yaml against one real Founder Story clip


---

## Future sprints (placeholder)

- Sprint 3: Install BadWords on Mac, run on one real RP interview, validate the configs
- Sprint 4: Wire BadWords output into `resolve_workflow.py` (new command: `apply-cuts-from-transcript`)
- Sprint 5: Decide whether saywhy-app or a sibling PWA becomes the producer-facing front-end
