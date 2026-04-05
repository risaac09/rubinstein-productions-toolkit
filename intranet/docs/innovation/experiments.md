# Experiments Log

Every R&D experiment — active, canonized, or killed — with its hypothesis, kill criterion, and outcome.

Format: newest first. An experiment graduates to `operations/` or `learning/` canon when it's shipped and used repeatedly; otherwise it gets killed.

---

## Template

```markdown
## YYYY-MM → EXP-NN — Short title

**Loop:** methodology | tooling | product | creative-research
**Hypothesis:** the one sentence you're testing
**Kill criterion:** what "this isn't working" looks like
**Timebox:** N weeks
**Pilot:** who or what is the real-world test
**Status:** active | killed | canonized
**Outcome:** what was learned (filled in at end)
```

---

## Active

### 2026-04 → EXP-02 — Video essay pilot

**Loop:** creative-research (marketing overlap)
**Hypothesis:** A single long-form video essay on Say The Thing methodology, produced at the quality bar RP holds for client work, will either feel like a structural publishing form worth anchoring quarterly OR will feel like off-instrument labor. One pilot reveals which.
**Kill criterion:** If production takes more than 3 working days, or if the pilot produces a piece Isaac can't stand behind by its own standards, the form is killed (not the idea — the commitment).
**Timebox:** 6 weeks (write → shoot → edit → publish).
**Pilot:** one essay, topic TBD from methodology or a canonized case study.
**Status:** active
**Outcome:** *(filled in post-publication: did it feel structural, or did it feel like performance?)*

---

## Canonized

### 2026-04 → EXP-01 — Unified stack-data layer

**Loop:** tooling
**Hypothesis:** If all practice data (contacts, projects, content, activities, financials) lives in one flat-JSON git-as-database repo, then PWAs, agents, and the CLI can share one source of truth without a framework.
**Kill criterion:** If integrating stack-data into two PWAs takes more than one session, the approach is over-engineered.
**Timebox:** 1 session (did it in a single working day).
**Pilot:** rp-lifecycle + royal-metrics.
**Status:** canonized
**Outcome:** Shipped. See `it/index.md` service catalog. Flat JSON + jq + shell scripts + localStorage-cached JS client worked. No framework required. Wiring into a PWA is ~3 lines (script tag + StackData.configure + StackData.getXxx). Sync-to-pwa.sh handles sanitization per PWA.

---

## Killed

*None yet.*

---

## Considering (not yet committed)

Ideas that might become experiments. They sit here until someone writes a hypothesis + kill criterion for them.

- **Monthly group facilitation container** — recurring 90-min session for past clients. Product-adjacent. Originality check: does this dilute one-to-one depth or deepen the network?
- **Public client-brief endpoint** — client fills a form, gets an AI-generated brief within 24h. Requires CF Workers ($500 tier). BD tool.
- **Weekly newsletter from stack-data** — automatically generated from activities + content + financials. Could become audience development or could become slop.
- **Audio log / podcast** — lower production cost than video, closer to thinking aloud. Fit with ultrarunning content?
