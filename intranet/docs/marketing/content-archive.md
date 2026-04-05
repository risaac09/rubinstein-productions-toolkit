# Content Archive Strategy

Instagram is the primary publishing surface but a brittle archive. If @rubinsteinproductions is restricted, shadowbanned, or the platform changes the rules, years of published work disappear. This doc is the hedge: a weekly export pipeline that treats `stack-data/archives/` as the authoritative archive.

---

## Principle

**The platform is a channel. The archive is owned.** IG renders the work; stack-data preserves it.

---

## Pipeline (extends existing weekly IG scrape)

The existing GH Action (`weekly-ig-scrape.yml`) pulls post metadata into `content.json`. The archive extension adds media pulldown + local backup:

```
1. Apify instagram-scraper runs (existing, weekly, Monday)
2. merge-ig-posts.mjs updates stack-data/data/content.json (existing)
3. [new] archive-ig-media.mjs downloads media files not yet archived
4. [new] files stored in stack-data/archives/ig/YYYY/MM/{postId}.{ext}
5. [new] manifest written to stack-data/archives/ig/manifest.json
```

**Storage shape:**
```
stack-data/archives/ig/
  manifest.json              # postId → local path, size, hash, archived_at
  2026/
    04/
      {postId}.jpg           # single images
      {postId}-1.jpg         # carousels, indexed
      {postId}-2.jpg
      {postId}.mp4           # reels / video posts
      {postId}.json          # caption + metadata snapshot
```

---

## What gets archived

| Asset | Archived? |
|---|---|
| Post media (image, video, carousel) | Yes — all |
| Caption + hashtags | Yes — frozen at archive time |
| Post URL + shortcode | Yes — in manifest |
| Engagement snapshot (likes, comments) | Yes — at time of archive |
| Comment thread full content | No (too noisy; metricsHistory in content.json sufficient) |
| Stories / Highlights | Not in first pass — add later if needed |

---

## Backup tier

**Tier 1 — stack-data repo itself.** Private GitHub repo. Version-controlled. First line of defense.

**Tier 2 — local Time Machine backup.** stack-data is on Isaac's working machine; it's included in standard backup. Automatic.

**Tier 3 — external cold storage (quarterly).** Once per quarter, rsync `stack-data/archives/` to an external drive or offline storage. Manual, 15 min.

**Rule:** media files do NOT get committed to the stack-data git repo if they push it past 1GB. Instead, manifest.json is committed (tiny, text) and media files are excluded via .gitignore. Media backup relies on Tier 2 + Tier 3.

---

## Recovery scenarios

### Scenario 1: IG account restricted
- Content is unreachable on platform but fully intact in `archives/ig/`
- Republish selectively to owned site or other platforms using archived media + manifest.json

### Scenario 2: stack-data repo corrupted
- Recover from GitHub remote
- If remote also lost, recover from Time Machine + external drive

### Scenario 3: Media file loss (single post)
- Check if IG post still accessible; if yes, re-scrape that single post
- If not, the post is lost — archive manifest retains metadata + caption

---

## Implementation plan

1. Write `archive-ig-media.mjs` in `stack-data/scripts/` (reads content.json, downloads missing media)
2. Add to `weekly-ig-scrape.yml` workflow after the merge step
3. Add `archives/ig/**/*.jpg` + `*.mp4` to stack-data `.gitignore`; keep manifest.json committed
4. First-run backfill: run once locally to archive all historical posts currently in content.json
5. Quarterly cold-storage sync: add to calendar as recurring task (aligns with quarterly stewardship cycle in `business-development/referral-rhythm.md`)

---

## Related

- `reference_apify.md` memory — Apify actor details
- `stack-data/scripts/scrape-ig.sh` — existing scrape pipeline
- `it/index.md` — infrastructure overview

---

*"IG is how they find the work. The archive is where the work lives."*
