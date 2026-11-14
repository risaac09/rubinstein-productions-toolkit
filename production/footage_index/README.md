# footage_index

Find footage by description on your own machine: "slow push-in on hands chopping herbs", "seagull in flight", "forest floor with backpacks". Everything runs locally; nothing leaves it. It can be served to Claude Code as an MCP server, `footage-recall`.

It sits beside `rpresolve` in `production/` and imports it by package name (`rpresolve.detect`, `rpresolve.vision`, `rpresolve.card`, `rpresolve.methods`). Run it from `production/`.

## What it stores

Under `data_dir` (default `~/.local/share/footage-index`; nothing is written inside this repo):

- **clips:** path, size, mtime, camera, log profile and colour space (`rpresolve.detect`), fps, size, duration, codec, a `client` flag (see `restricted_prefix`), status.
- **shots:** one keyframe every ~4 s (3 to 40 per clip) with its in and out seconds, a 384 px thumbnail (log footage shown through a lutlab CST to Rec.709), a motion cue, face count and largest face (`rpresolve.vision`), mean luma.
- **vec_shots:** a SigLIP 2 image embedding per keyframe (`google/siglip2-base-patch16-384`, Apache-2.0; sqlite-vec, 768-d).
- **captions / fts_captions:** a JSON caption on every 3rd keyframe from Qwen2.5-VL 7B through Ollama on loopback (Apache-2.0): subject, setting, action, shot size, time of day, light, tags (deduped, capped at 12). Frames darker than a mean luma of 0.03 get no caption. FTS5 indexes the fields except "unknown" ones. `recaption-fts` re-stores every caption under the current rules without the model.

Search fuses SigLIP text-to-image similarity with FTS over captions (reciprocal rank), then filters.

## Install

```bash
python3 -m venv ~/.venvs/footage
~/.venvs/footage/bin/python -m pip install -r production/footage_index/requirements.txt
ollama pull qwen2.5vl:7b          # captions; Ollama on 127.0.0.1:11434
```

`rpresolve` needs ffmpeg and ffprobe (see the repo's `requirements.txt` for the paths it reads). The CST step runs lutlab; `tools_python` names the interpreter that has the packages `lutlab` and `rpstills` need (default: the one running the indexer).

To serve it to Claude Code (not done for you):

```bash
claude mcp add --scope user footage-recall \
  --env PYTHONPATH="/path/to/this-repo/production" --env RP_PIPELINE_CONFIG="/path/to/pipeline-config.json" \
  -- ~/.venvs/footage/bin/python -m footage_index.server
```

## Run on one folder, no configuration

```bash
cd production
P=~/.venvs/footage/bin/python
$P -m footage_index --root /Volumes/Footage index [--only Shoots/harvest-day] [--limit N]   # metadata + keyframes, 6 lanes
$P -m footage_index --root /Volumes/Footage embed                                              # SigLIP
$P -m footage_index --root /Volumes/Footage caption [--limit N] [--every 3]                    # slow; leave it overnight
$P -m footage_index find "rocky coastline with waves" --fps-min 50 --sheet /tmp/sheet
$P -m footage_index stats
```

`--root DIR` (or `FOOTAGE_ROOT`) must come before the command and names the one folder to walk. Paths are stored relative to it.

## Configuration

Settings come, highest first, from an environment variable, the `footage_index` section of the JSON file named by `RP_PIPELINE_CONFIG`, then the default. Every key can be set in the environment as `FOOTAGE_<KEY>` in capitals (a few older names are kept: `FOOTAGE_DB`, `FOOTAGE_THUMBS`, `FOOTAGE_EDITS`, `FOOTAGE_TASTE`, `FOOTAGE_EVENTS`, `FOOTAGE_METHODS`, `OLLAMA_HOST_URL`, `FOOTAGE_ROOTS`). An unknown key in the file is an error. `config.example.json` shows all of them with placeholder paths.

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | `~/.local/share/footage-index` | database, thumbnails, log, edit plans |
| `db`, `thumbs`, `log_file`, `edits_dir` | under `data_dir` | each can move on its own |
| `cache_dir` | `~/.cache/footage-index` | CST cubes (lutlab will not write inside a git tree) |
| `roots` | `[]` | list of `{name, mount, volume, catalog, include, skip_dirs}`; empty means the one `--root` folder |
| `skip_prefixes` | `["#recycle/"]` | relpath prefixes never indexed |
| `staging_prefix` | `""` | folder whose copies lose to the same take elsewhere |
| `restricted_prefix` | `""` | clips under it get `client=1`; `find --no-client` leaves them out |
| `required_folder` | `""` | a folder the first root must contain, or `index` refuses (a wrong-mount guard) |
| `review_log` | `""` | JSON-lines log of picks from past reviews, used to rank variants |
| `taste_profile` | `""` | Markdown file of look preferences, used to rank variants |
| `methods_dir` | `production/edit-methods` | method files |
| `tools_python` | the running interpreter | Python for `rpstills` and lutlab subprocesses |
| `ollama_url`, `vlm` | `http://127.0.0.1:11434`, `qwen2.5vl:7b` | captioner |
| `siglip`, `siglip_device` | `google/siglip2-base-patch16-384`, `""` (mps if present, else cpu) | embedder |
| `slot_floor` | `0.10` | SigLIP cosine a slot's best match must clear |

**Roots.** Each entry has a `name`, a `mount` (where this machine reads), an optional `volume` (the path Resolve and the plans name; default the mount, or `/Volumes/<name>` for a named root), an optional `catalog` (a tab-separated file of path, bytes, mtime; a root without one is walked live), `include` (subtrees to keep) and `skip_dirs` (a case-insensitive regex that drops any folder it matches). The first root keeps bare relpaths; the others are stored `<name>:<path>`. Every configured root must be mounted or `index` stops.

## Moving folders, migration, card records

- **Clip key.** `clips.clip_key` survives a move: `card:<CAM>-<id6>/<stem>` when the clip sits in a card folder (from the record beside it, `rpresolve.card.load`, else the folder name), else `file:<bytes>:<lowercase name>`. A full `index` re-points a row whose path is gone to the one new path with the same key, keeping its shots, vectors and captions; only a key that matches nothing is pruned. Several candidates on either side are never guessed.
- **`rekey [--apply]`** re-derives every key from the records now on disk; a dry run prints counts and writes nothing. `--apply` backs the database up first and restores it if the counts move. **`migrate`** does the same for a database that predates `clip_key`; `index` refuses an unmigrated one.
- **`refresh-squeeze [--apply] [--backup PATH]`** compares each done clip's stored thumbnail stretch (thumbnail aspect over coded aspect; BRAW's size from `braw2ppm`) with its current squeeze answer. A clip whose answer is above 1 and whose thumbnails are plain (or drawn at a different factor) is redrawn desqueezed, in place, at the same keyframes, and its shots re-embedded; a clip recorded as 1 over thumbnails drawn at its 1.5x file tag is redrawn plain. Stretch that no answer explains is reported `unexplained` and left alone, as are sources whose aspect cannot be judged (rotated, no thumbnail: `skipped`). Captions, shots and times are never touched; `--apply` backs the db up and restores it if the clip, shot or caption counts move.
- **`index --card PATH`** indexes, embeds and captions one card folder under any root (no prune, no re-point). Without Ollama it says so, exits 0, and leaves the card indexed and embedded.
- **`cameras`** prints the distinct camera strings per card folder as JSON; it writes nothing.
- **Decode route.** A keyframe tries hardware decode for 60 s, then software for 300 s. A clip that fails both routes on three keyframes in a row gets `status='undecodable'` and the last ffmpeg line in `error`; `index --retry-errors` tries again.
- **Squeezed footage.** `sample.desqueeze_factor(path)` above 1 stretches the width before the 384 px scale, so thumbnails and embeddings see the picture as shot.

Every phase resumes: a clip is resampled only when its key (sha256 when the catalog has it, else size and mtime) changes; `embed` and `caption` fill only what is missing.

## Edit methods against a shoot

```bash
$P -m footage_index shoot   Shoots/harvest-day                 # context tags, 0-1 strength, evidence
$P -m footage_index methods Shoots/harvest-day [--sheet DIR]   # top 3 methods, 4 slot thumbnails each
$P -m footage_index plan    <method-id> Shoots/harvest-day [--music PATH]
$P -m footage_index plan    <method-id> Shoots/harvest-day --swap 4      # lettered sheet of slot 4's top 5
$P -m footage_index plan    <method-id> Shoots/harvest-day --swap 4 B    # put B in slot 4
$P -m footage_index plan    --variants [N] Shoots/harvest-day            # N drafts (6)
```

A shoot is a relpath prefix. `shoot` scores travel, event, outdoor, people, food, night, motion, portrait and nature from SigLIP text probes, faces, motion, luma, the fps mix and captions; profiles are cached in the database. Methods are the toolkit's `production/edit-methods/*.json`; a method scores 0.6 x its `fits` tags' mean strength + 0.4 x the share of its slots whose best in-shoot match clears `slot_floor`. `plan` fills each slot from the shoot's own vectors, never reuses a take unless `rules.reuse`, needs at least fps/speed for slow slots, trims to the slot's seconds centred on the keyframe window, and swaps a `low res/` handoff for its full-res sibling. It writes `fills.json`, and `manifest.json` through `rpresolve.methods.to_manifest`, to `<edits_dir>/<shoot>-<method>/`. `--variants N` writes N complete plans to `<edits_dir>/<shoot>-variants/vK-<method>/` plus a ranked `variants.json`; they differ in method, look, pacing and fills (no two share more than half their slots). Ranking adds 0.1 x the share of takes picked in the review log and 0.05 for a look that won in the taste profile or the log.

**Contact sheets.** `sheet --prefix PREFIX --out DIR [--skip SUBSTR ...] [--per-clip 6] [--cols 8] [--rows 6] [--exclude FILE]` tiles every clip under a prefix onto `DIR/sheet-NN.jpg`, each cell labelled with the clip's file stem, second, motion cue, source fps and the gate's flags (`soft`, `dark`, `blown`, `smear`). A take or shot in the exclude file gets a red border. `DIR/sheets.json` maps "sheet 3, row 2, col 5" to a source and a time. It reads the index only.

### Quality gate, trim, variants

`plan` fills only from shots that pass a per-shoot gate, computed from the stored thumbnails, motion, luma and vectors (nothing is written to the database). A shot is rejected, with one reason, when it is `excluded`, `dark` or `blown` (luma outside 0.10 to 0.90), `soft` (Laplacian variance below the shoot's 35th percentile), `motion` (above the 92nd), `keeper` (SigLIP "good photo" minus the worst of four "bad photo" probes, z-scored, not above -0.3) or `sideways`. Passing shots then lose to `other_state` and `copy` (same take, same second, another folder). The thresholds are constants at the top of `gate.py`, tuned on one shoot; read `gate --sheet` on every new shoot before trusting them. `gate PREFIX [--json] [--sheet DIR] [--state NAME]` shows it.

- **One colour state per plan.** Renders (exports, transcodes, handoff copies) are state `graded`, a proxy is `proxy`, anything else is `original:<camera>/<profile>/<colour space>`. A plan uses `--state` if given, else the state with the most gated shots.
- **Take identity.** The camera's own id in the file name, so copies in other folders are one take; a clip without one is grouped by `same_take`.
- **`<edits_dir>/<shoot>-exclude.json`**: `{"shots": [shot ids], "takes": [take ids or relpaths]}`. Listed shots and takes never pass. Any other key (except `why`, a note for a person) makes the gate raise, naming the file and key. `--no-gate` is the one opt-out.
- **`check PREFIX PLAN_DIR [...]`** reads each folder's `manifest.json` and exits 1, one line per span, when a span uses an excluded take or overlaps an excluded shot, or opens a `scale-window` or `crop-window` over a previous take with less than the window's plate left. A folder it cannot read is a `not checked` line. `check PREFIX --shotlist FILE` takes a hand-written `{"shots": [{"name", "source", "in", "out"}]}`. Run it on every plan folder before a draft or a build.
- **Trim.** In and out come from a motion curve around the keyframe (ffmpeg, 12 fps, 160 px wide). A burst centres on the highest motion peak under 0.07; a hold opens on the stillest frame. `--beats beats.json` or `--notes notes.json` lays slots on a beat grid. `--trim centre` forces the centred trim.
- **Per-slot sheets.** For methods of ten slots or fewer, `slots-sheet METHOD PREFIX` makes a lettered sheet per slot with its key sealed beside it, and `compose METHOD PREFIX PICKS.json` (`{"1": "B", "2": "A"}`) writes the chosen plan once. `--variant vK` or `--plan-dir PATH` acts on a variants plan.
- **Minors.** Each plan folder gets `minors.jpg`, every pick ordered by a SigLIP "young child" minus "adult" probe, for a person to read. The probe only ranks; it never clears a shot.

## Scheduling

`run-weekly.sh` runs index, embed and caption in turn; schedule it with cron or launchd. It reads `FOOTAGE_PYTHON`, `FOOTAGE_LOG` and the configuration above.

## Tests

```bash
cd production && python -m pytest -q footage_index/tests
```

The tests build synthetic clips and stub the models; no footage, no GPU and no network. CI runs them.

## Privacy

Thumbnails, captions and the database describe your footage. Keep `data_dir` out of git, shared documents and cloud sessions. The captioner is told not to name people.
