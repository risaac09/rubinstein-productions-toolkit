---
name: market-listener
description: Synthesize scraped market data into actionable signals for Rubinstein Productions. Use this skill to review the signals queue, surface competitor moves, warm-lead activity, topic trends, and content-pattern insights, and propose outreach or content-response actions. Trigger for "what's the market saying", "review signals", "any warm leads this week", "what are competitors doing", "market digest", "what should I write about next", or whenever Isaac wants to orient to external activity before making moves.
---

# Market Listener

Turns raw scraping data into decisions. Reads what competitors/adjacencies/prospects are doing on social, surfaces what matters, and proposes actions Isaac can take.

## What this skill does

1. Reads the signal queue (`~/stack-data/data/signals.json`) and watched accounts (`~/stack-data/data/market.json`)
2. Cross-references with RP's contacts, content, and project pipeline
3. Synthesizes findings into:
   - **Warm-lead activity** — prospects posting high-engagement content worth reacting to
   - **Competitor moves** — what similarly-positioned practices are shipping
   - **Topic trends** — conversation patterns in the adjacency space RP hasn't yet addressed
   - **Content-response opportunities** — posts that invite Isaac's voice in comments
4. Proposes actions: who to reach out to, what to comment on, what content gap to fill
5. Marks signals as reviewed (writes back to signals.json) after action is taken

## What this skill does NOT do

- Does not send outreach (that's `outreach-email-manager`)
- Does not publish content (that's `content-publishing-agent`)
- Does not scrape (that's `scripts/listen-niche`)
- Does not surface from nothing — if signals.json is empty, ask Isaac to run `bash ~/stack-data/scripts/listen-niche && bash ~/stack-data/scripts/listen-digest` first

## Workflow

### Step 1: Read current state

```bash
# Unreviewed signals, most recent first
jq '[.[] | select(.reviewed == false)] | sort_by(.at) | reverse' ~/stack-data/data/signals.json

# Current watched accounts
jq '.[] | {handle, category, priority, fetchedAt, topPosts: (.topPostsLast30d | length)}' ~/stack-data/data/market.json

# Latest weekly digest (if present)
ls -t ~/stack-data/reports/signals_*.md | head -1
```

### Step 2: Group and interpret

Sort signals by type and importance:

- **warm-lead-activity** (highest priority) — prospects publishing. Reaction window is short.
- **competitor-move** — what are peers shipping that's working? Pattern-match against RP's own cadence.
- **engagement-spike** — any post in any watched category that hit threshold. Study the why.
- **topic-trend** — repeated themes across multiple watched accounts. Content opportunity.
- **mention-of-rp** (if ever present) — direct reaction required.

For each signal, ask:
1. What does this signal tell me about the market's attention right now?
2. Is this a *reaction window* (comment/engage directly) or a *learning window* (study the pattern, use it later)?
3. Does this change what RP should publish next?

### Step 3: Propose actions

Output structure:

```
## This week's signals

**Warm leads (X)**
  → @handle published [summary]. Action: [comment / DM / email via outreach-email-manager]

**Competitor moves (X)**
  → @handle is [pattern]. Relevance: [what RP should learn/ignore]

**Topic trends (X)**
  → [topic] appearing in [N accounts]. Content gap: [what RP hasn't said]

**Content-response (X)**
  → @handle's post on [topic] invites [Isaac's specific angle]
```

For each proposed action, be specific:
- Which skill or CLI executes it (e.g. `outreach-email-manager`, drafting a Gmail, publishing a reel)
- What the content/message should be (one sentence of direction, not the full draft — that's the other skill's job)
- Ask Isaac before acting

### Step 4: Mark signals reviewed

After Isaac confirms the next action (or explicitly dismisses a signal), update `signals.json`:

```bash
# Mark reviewed with action taken
jq --arg id "$SIGNAL_ID" --arg action "$ACTION_DESC" \
  'map(if .id == $id then .reviewed = true | .action = $action else . end)' \
  ~/stack-data/data/signals.json > /tmp/signals.tmp && \
  mv /tmp/signals.tmp ~/stack-data/data/signals.json

# Emit event
bash ~/stack-data/scripts/sd-event update signal "$SIGNAL_ID" "skill:market-listener" "Reviewed: $ACTION_DESC"
```

Then rebuild the index: `bash ~/stack-data/scripts/sd-build-index`

## Interpretive stance (voice)

RP's market-listening is NOT about matching competitors. It's about detecting:

- **Where is collective attention moving?** (topic trends)
- **What is working right now in adjacent methodology spaces?** (competitor moves — as anthropology, not imitation)
- **Who is already articulating the problem RP solves?** (warm leads — they've already primed themselves)
- **What conversation is RP absent from that it should be in?** (content gaps)

Read signals through the Voice Liberation lens. The question is always: *where can Isaac say the thing only he can say?*

Do not recommend generic "engagement" moves (likes, follows, generic comments). Every proposed action should be specific to RP's voice and methodology.

## Cadence

- **Weekly** (Sundays after weekly-market-listen GH Action runs): full signal review
- **On-demand** (before outreach or publishing decisions): targeted pull from signals.json
- **After a Mirror/Map/Territory proposal is sent**: scan signals for related-topic patterns to shape follow-up

## Reference files

- `~/stack-data/data/signals.json` — signal queue (unreviewed = actionable)
- `~/stack-data/data/market.json` — watched accounts with posts + metrics
- `~/stack-data/registry/listening-targets.yaml` — who is being watched and why
- `~/stack-data/reports/signals_YYYY-MM-DD.md` — weekly human-readable digest
- `~/stack-data/dist/index.json` — use `.signalsQueue[]` and `.market[]` fields

## Related skills

- `outreach-email-manager` — execute reach-outs identified by this skill
- `content-publishing-agent` — ship content-response identified by this skill
- `network-stewardship` — when warm-lead signals indicate a relationship that needs tending
- `rubinstein-productions-coo` — when competitor moves have pricing/positioning implications
