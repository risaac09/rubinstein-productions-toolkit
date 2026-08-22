#!/usr/bin/env bash
# DSH deployment kit installer.
#
# Deploys the DeepSeek Harness (DSH) user-global session brief and keeps the
# shared agent skill home in sync with this repo's prompts/skills/ so the
# toolkit stays the single source of truth. Idempotent: safe to re-run any
# time the kit changes.
#
# Usage:
#   bash dsh/install.sh                 # brief + the 5 toolkit-mapped skills
#   bash dsh/install.sh --skills-all    # also every other prompts/skills/*.md
#   bash dsh/install.sh --settings      # merge OpenRouter models + default model
#   bash dsh/install.sh --dry-run       # print what would change, change nothing
#
# The settings step rewrites ~/.dsh/settings.yaml through Python's yaml module
# when available; otherwise it prints the exact block to add by hand. It never
# removes existing keys.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$KIT_DIR")"
DRY_RUN=0
SKILLS_ALL=0
DO_SETTINGS=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --skills-all) SKILLS_ALL=1 ;;
    --settings) DO_SETTINGS=1 ;;
    -h|--help) sed -n '1,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
AGENTS_HOME="${DSH_AGENTS_HOME:-$HOME/.agents}"
SKILLS_DIR="$AGENTS_HOME/skills"
SKILL_SRC_DIR="$ROOT/prompts/skills"

# Skills whose SKILL.md symlinks to a toolkit file. Default set matches the
# Claude Code side (~/.claude/skills). --skills-all extends to every *.md.
DEFAULT_MAP=(branded-deck-build branded-doc-build outreach-email-manager project-management-coordinator rubinstein-productions-coo)

say() { printf 'dsh/install: %s\n' "$*"; }
warn() { printf 'dsh/install: WARNING: %s\n' "$*" >&2; }

# 1. Deploy the user-global session brief.
brief_src="$KIT_DIR/AGENTS.md"
brief_dst="$DSH_HOME/AGENTS.md"
if [ -f "$brief_dst" ] && ! cmp -s "$brief_src" "$brief_dst"; then
  if [ "$DRY_RUN" -eq 1 ]; then
    say "would back up $brief_dst"
  else
    say "backing up $brief_dst -> $brief_dst.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$brief_dst" "$brief_dst.bak-$(date +%Y%m%d-%H%M%S)"
  fi
fi
if [ "$DRY_RUN" -eq 1 ]; then
  say "would deploy $brief_dst"
else
  mkdir -p "$DSH_HOME"
  cp "$brief_src" "$brief_dst"
  say "deployed $brief_dst"
fi

# 2. Skill sync: symlink toolkit files into the shared agent skill home.
if [ "$SKILLS_ALL" -eq 1 ]; then
  mapfile -t map < <(cd "$SKILL_SRC_DIR" && ls *.md 2>/dev/null | sed 's/\.md$//' || true)
else
  map=("${DEFAULT_MAP[@]}")
fi

[ "$DRY_RUN" -eq 1 ] || mkdir -p "$SKILLS_DIR"
for name in "${map[@]}"; do
  src="$SKILL_SRC_DIR/$name.md"
  dst="$SKILLS_DIR/$name/SKILL.md"
  if [ ! -f "$src" ]; then
    warn "no toolkit file $src; skipping $name"
    continue
  fi
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    say "already linked: $name"
    continue
  fi
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      say "would back up existing copy $dst"
    else
      say "backing up existing copy $dst -> $dst.bak-$(date +%Y%m%d-%H%M%S)"
      cp -p "$dst" "$dst.bak-$(date +%Y%m%d-%H%M%S)"
    fi
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    say "would link $name -> $src"
  else
    rm -f "$dst"
    mkdir -p "$SKILLS_DIR/$name"
    ln -s "$src" "$dst"
    say "linked $name"
  fi
done

# 3. Settings merge (only with --settings).
if [ "$DO_SETTINGS" -eq 1 ]; then
  settings="$DSH_HOME/settings.yaml"
  if [ ! -f "$settings" ]; then
    warn "no $settings; create it first"
  elif [ "$DRY_RUN" -eq 1 ]; then
    say "would merge models into $settings"
  elif python3 -c 'import yaml' >/dev/null 2>&1; then
    cp "$settings" "$settings.bak-$(date +%Y%m%d-%H%M%S)"
    python3 - "$settings" <<'PY'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f) or {}
providers = doc.setdefault('llm-pi-ai', {}).setdefault('providers', {})
or1 = providers.setdefault('openrouter1', {})
models = or1.setdefault('models', [])
ids = {m.get('id') for m in models if isinstance(m, dict)}
add = [
    {'id': 'qwen/qwen3-coder', 'name': 'Qwen 3 Coder 480B - coding agents'},
    {'id': 'qwen/qwen3-max-thinking', 'name': 'Qwen 3 Max Thinking - hard reasoning'},
    {'id': 'qwen/qwen3.5-397b-a17b', 'name': 'Qwen 3.5 397B - multimodal generalist'},
]
for m in add:
    if m['id'] not in ids:
        models.append(m)
        ids.add(m['id'])
adm = doc.setdefault('agent-default-model', {})
adm.setdefault('provider', 'openrouter1')
adm.setdefault('model', 'deepseek/deepseek-v4-flash-0731')
with open(path, 'w') as f:
    yaml.safe_dump(doc, f, sort_keys=False)
print('dsh/install: merged models + default into', path)
PY
  else
    warn 'python3 yaml unavailable; add this block to settings.yaml by hand:'
    cat <<'BLOCK'
llm-pi-ai:
  providers:
    openrouter1:
      models:
        - id: qwen/qwen3-coder
          name: Qwen 3 Coder 480B - coding agents
        - id: qwen/qwen3-max-thinking
          name: Qwen 3 Max Thinking - hard reasoning
        - id: qwen/qwen3.5-397b-a17b
          name: Qwen 3.5 397B - multimodal generalist
BLOCK
  fi
fi

echo
say 'verify:'
say '  1. Refresh the DSH web GUI, open a NEW session in the stack-data workspace.'
say '  2. The first request carries "Instructions from: ~/.dsh/AGENTS.md" (plus stack-data CLAUDE.md/AGENTS.md).'
say '  3. The skill catalog lists the RP skill set; loading one resolves to the toolkit file.'
say '  4. Edit the kit in this repo and re-run install.sh; never edit deployed copies.'