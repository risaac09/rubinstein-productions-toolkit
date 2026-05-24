# BadWords RP — Config Layer

AI editing automation for Rubinstein Productions, built on [BadWords](https://github.com/veritus-git/BadWords). Three modes (`polish`, `witness`, `selective`) that map RP service tiers to BadWords settings.

## Why this exists

BadWords cuts ums, false starts, and retakes by default. That's optimal for clean talking-head delivery and a structural problem for Say Why methodology, where exactly those moments are the polyvagal data the work is built on.

This layer is the mode switch. Pick the YAML that matches the deliverable. Load it into BadWords before transcription.

## The three modes

| Mode | Use case | Cuts? | Filler list | Silence min | Whisper prompt |
|---|---|---|---|---|---|
| `polish` | Program Engagement, social, corporate testimonial | Yes, on Assemble | Broad (20 fillers) | 0.18s | Clean |
| `witness` | Founder Story, Bilingual Dashboard, anything coded | No | Empty | 2.0s | GOLDEN verbatim |
| `selective` | Hybrid deliverables | Manual confirm | Broad (20 fillers) | 0.6s | GOLDEN verbatim |

See [configs/polish.yaml](configs/polish.yaml), [configs/witness.yaml](configs/witness.yaml), [configs/selective.yaml](configs/selective.yaml) for the full settings.

## Mapping: BadWords settings → RP modes

Source of truth: `~/Downloads/BadWords-main/src/config.py` (line 360, `DEFAULT_SETTINGS`).

### Cut vs. mark

BadWords exposes two mutually exclusive toggles in its Fast Silence flow:
- `fs_cut_mode` (default `true`) — silences cut directly
- `fs_mark_mode` (default `false`) — silences placed as markers

For transcript-driven flow, the user controls cut behavior by what they paint red in the transcript editor. Red = cut on Assemble. So the RP modes shape both the FS toggles and the auto-painting behavior (via `filler_words`).

### Settings keys we override per mode

| Setting | Polish | Witness | Selective | BadWords default |
|---|---|---|---|---|
| `fs_cut_mode` | `true` | `false` | `false` | `true` |
| `fs_mark_mode` | `false` | `true` | `true` | `false` |
| `filler_words` | 20 fillers | `[]` | 20 fillers | `["yyy","eee","aaa","umm","uh","ah","mhm"]` |
| `silence_min_dur` | 0.18 | 2.0 | 0.6 | 0.2 |
| `silence_threshold_db` | -42.0 | -50.0 | -42.0 | -42.0 |
| `ui_spin_pad` | 0.05 | 0.5 | 0.15 | 0.1 |
| `ai_initial_prompt` | empty | GOLDEN | GOLDEN | GOLDEN |
| `ai_suppress_silence` | `true` | `false` | `false` | `false` |
| `ai_vad_filter` | `true` | `false` | `false` | `false` |
| `algo_fuzzy_threshold` | 75 | 90 | 80 | 75 |
| `algo_retake_lookahead` | 80 | 40 | 60 | 80 |
| `algo_distance_penalty` | 2.0 | 4.0 | 3.0 | 2.0 |
| `xml_preserve_track_order` | `false` | `true` | `true` | `false` |
| `timestamp_precise` | `false` | `true` | `true` | `false` |

### Settings we never touch

- `ai_ultra_precise` stays `true` across all modes
- `ai_temperature` stays `0.0` (deterministic)
- `compute_type`, `device` are environment-specific, set per-machine
- `editor_font_*`, `accent_color`, `gui_lang`, shortcuts — user UI preference
- Telemetry settings — user privacy decision

## How to load a config

```bash
~/rubinstein-productions-toolkit/production/badwords-rp/load-config.sh witness
~/rubinstein-productions-toolkit/production/badwords-rp/load-config.sh polish --dry-run
~/rubinstein-productions-toolkit/production/badwords-rp/load-config.sh selective --badwords-dir /path/to/BadWords
```

`load-config.sh` reads `configs/<mode>.yaml`, extracts `badwords_settings`, and shallow-merges it into BadWords' `settings.json`. Keys not present in the YAML are preserved (font size, telemetry choice, shortcuts, model run history, etc.). Atomic write via tmpfile + mv.

Flags:
- `--dry-run` prints the merged JSON to stdout, no write
- `--badwords-dir <path>` overrides the install location (default `~/Downloads/BadWords-main`, or `$BADWORDS_DIR`)

### BadWords install path on macOS

BadWords is **self-contained**: it writes `settings.json` next to its source files, not to `~/Library/Application Support/`. See `osdoc.py:102`:

```
self.settings_file = os.path.join(self.install_dir, 'settings.json')
```

Where `install_dir = dirname(osdoc.py)`. So the file lives at `<BadWords>/src/settings.json`. Fallback path (if install dir not writable): `/tmp/BadWords_Fallback/settings.json`. The adapter targets the primary path; the fallback isn't covered.

## Workflow per mode (operational)

### Polish
1. Load `polish.yaml`
2. Transcribe (clean prompt, no verbatim capture)
3. BadWords auto-paints red on the 20 filler words
4. Review red sweep, add any missed
5. Click Assemble → new timeline with cuts applied

### Witness
1. Load `witness.yaml`
2. Transcribe (GOLDEN verbatim prompt; ums preserved)
3. BadWords marks silences > 2s, does NOT paint filler red
4. Do NOT click Assemble unless you want a marker-only timeline
5. Read the marker pattern as a heat-map. Pairs with `nomadic-indicators-coder` skill.

### Selective
1. Load `selective.yaml`
2. Transcribe (GOLDEN verbatim)
3. BadWords paints red on filler candidates
4. Review every red mark; confirm or clear per-instance
5. Click Assemble → cuts you confirmed are applied; markers remain

## Voice and methodology firewall

This layer encodes a methodological commitment, not just an editing preference. Witness mode preserving the texture is the same rule that lives in `~/rp-shared/worker/index.js`:

> "Keep ums, false starts, and self-corrections. They are data about how the speaker is processing."

If you change `witness.yaml` to cut anything, you've broken the contract with Say Why methodology. The Founder Story tier depends on it.

## Sprint state

- Sprint 1: inventory (closed, see [SPRINTS.md](SPRINTS.md))
- Sprint 2: config layer (this work, in flight)
- Sprint 3 (in flight): `load-config.sh` shipped; install BadWords, validate `witness.yaml` on one real Founder Story clip
- Sprint 4: wire BadWords XML output into `production/resolve_workflow.py`
- Sprint 5: producer-facing front-end (saywhy-app extension or sibling PWA)

## References

- Upstream: https://github.com/veritus-git/BadWords (MIT, by Szymon Wolarz)
- Issue tracker: https://github.com/risaac09/rubinstein-productions-toolkit/issues
- Sister tool: [production/resolve_workflow.py](../resolve_workflow.py) (Resolve API CLI)
- Methodology source: `~/rp-shared/worker/index.js` transcription prompt
