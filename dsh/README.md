# DSH kit

The deployment kit that wires a **DeepSeek Harness (DSH)** agent on the
**OpenRouter** lane into the Rubinstein Productions stack: the session brief
that carries Isaac's work style into every session, the model routing table
for the OpenRouter/DeepSeek/Qwen lane, and the installer that keeps the shared
skill home in sync with `prompts/skills/`.

This is the DSH counterpart to `phase-zero/` (which targets Claude Code).
Same content philosophy, different mechanism: phase zero injects through
Claude Code hooks and `~/.claude/settings.json`; DSH injects through its
user-global instruction file and the shared agent skill home. Both are
kit-deployed, both point at the same canonical methodology.

## Why DSH needs its own kit

DSH loads instructions differently from Claude Code:

- **User-global brief**: `$DSH_HOME/AGENTS.md` (default `~/.dsh/AGENTS.md`)
  loads in every session, in every workspace. This kit's `install.sh` deploys
  it there. It carries who Isaac is, the repo map, the voice rules, how to
  read him, the four directions, the four operating slips, the gear-phrase
  boundary, model routing, and the skill index.
- **Project instructions**: DSH auto-loads `AGENTS.md` and `CLAUDE.md` walking
  from the session working directory up to the git root. stack-data already
  ships both, so DSH sessions that run in stack-data get the canonical
  orientation for free. No per-repo hook or settings merge is needed.
- **Skills**: DSH discovers skills from `<projectRoot>/.dsh/skills`,
  `<projectRoot>/.agents/skills`, custom roots, `$DSH_HOME/skills`, and the
  shared agent home `~/.agents/skills` (default `$DSH_AGENTS_HOME`). The
  installer keeps `~/.agents/skills` in sync with this repo's
  `prompts/skills/` so the toolkit stays the single source of truth.

## Files

- `AGENTS.md` — the user-global session brief, deployed byte-for-byte to
  `~/.dsh/AGENTS.md`. Kept under a few KB so it never busts an instruction
  budget; deeper context arrives from stack-data's `CLAUDE.md` when the
  session workspace is stack-data.
- `model-routing.md` — the DSH/OpenRouter routing table: cheapest default
  (`deepseek/deepseek-v4-flash-0731`), coding lane (`qwen/qwen3-coder`), hard
  reasoning (`qwen/qwen3-max-thinking`, `deepseek/deepseek-v4-pro`),
  multimodal (`qwen/qwen3.5-397b-a17b`), local free lane. Ask-first policy
  inherited from `phase-zero/model-routing.md`.
- `install.sh` — the distribution path. See Usage below.
- `README.md` — this file.

## Install (per machine)

```bash
# brief + the 5 toolkit-mapped skills (matches the Claude Code side)
bash dsh/install.sh

# also add every other prompts/skills/*.md (agentic-development, etc.)
bash dsh/install.sh --skills-all

# merge the OpenRouter models list and the cheapest default into ~/.dsh/settings.yaml
bash dsh/install.sh --settings

# inspect without changing anything
bash dsh/install.sh --dry-run
```

Re-run any time to refresh the kit. Every deployed file is either a copy with
a timestamped backup or a symlink into this repo; no hand-edited files are
overwritten without a backup, and nothing is ever removed.

## Settings (manual, once)

If you do not use `--settings`, add the three lanes to
`~/.dsh/settings.yaml` under `llm-pi-ai.providers.openrouter1.models`:

```yaml
- id: qwen/qwen3-coder
  name: Qwen 3 Coder 480B — coding agents
- id: qwen/qwen3-max-thinking
  name: Qwen 3 Max Thinking — hard reasoning
- id: qwen/qwen3.5-397b-a17b
  name: Qwen 3.5 397B — multimodal generalist
```

and set the default to the lowest-cost lane:

```yaml
agent-default-model:
  provider: openrouter1
  model: deepseek/deepseek-v4-flash-0731
```

DSH hot-reloads `settings.yaml`; refresh the GUI Models page to see the
change. The session picks the default on its next start.

## Verify

1. Refresh the DSH GUI, open a NEW session with the workspace set to
   `stack-data`.
2. The first request carries `<system-reminder>` text naming
   `~/.dsh/AGENTS.md`, then `AGENTS.md`/`CLAUDE.md` from stack-data.
3. The skill catalog shows the RP skill set; loading e.g. `isaac-voice`
   resolves to the toolkit file.
4. Ask the agent "what's my work style" — the answer should follow the voice
   rules while stating them.

## The project layer (future)

DSH also discovers `<projectRoot>/.dsh/skills` at the highest local rank.
stack-data can carry stack-specific skills there so they travel with the
repo and load on every clone. Nothing in this kit depends on it; it is the
natural next step if stack-data wants skills that do not belong in the public
toolkit.

## Maintenance

This kit is kit-deployed. Edit these files, re-run `install.sh`, and it
propagates. Never edit the deployed `~/.dsh/AGENTS.md` or the skill symlinks
by hand; they get overwritten or pointed back on redeploy.

The phase-zero kit remains the canonical voice for rules. When `dsh/` and
`phase-zero/` disagree, phase zero wins and this kit gets fixed.