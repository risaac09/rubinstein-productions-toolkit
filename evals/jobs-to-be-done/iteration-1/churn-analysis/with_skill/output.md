# Diagnosing 2-Month SaaS Churn — A Jobs-to-Be-Done Analysis

## The core problem with what's been tried

Lower pricing, onboarding emails, and a referral program all treat churn as an **acquisition/pricing problem**. But users cancelling at month 2 are people who already said yes, paid, and showed up. The interventions don't match the disease.

Through a JTBD lens, your client is almost certainly losing the **Little Hire**, not the Big Hire.

---

## The Big Hire / Little Hire Split

Two decisions are happening, and most SaaS teams only optimize the first:

- **Big Hire** — the signup. Won by marketing, pricing page, first impression, onboarding emails. **Your client is winning this.**
- **Little Hire** — the repeated decision to actually *use* the product this Tuesday at 10am when there's something else to do. Won by in-product value, habit formation, and friction reduction.

**Cancelling at month 2 is a Little Hire failure.** People bought the promise, stopped using it in weeks 2–4, and the month-2 bill just made them notice they weren't using it. The cancellation is a lagging indicator — the actual firing happened ~6 weeks earlier, silently, when they stopped logging in.

### Why the three attempted fixes failed

| Intervention | What it addresses | Why it missed |
|---|---|---|
| Lower pricing | Big Hire anxiety ("is it worth it?") | People aren't leaving over price — they're leaving because they stopped hiring it to do anything |
| More onboarding emails | Big Hire → early Little Hire bridge | Email can't create value the product isn't delivering; it just reminds people they're not using it |
| Referral program | New Big Hires from existing users | You can't refer a product you stopped using. This widens a leaky bucket |

---

## What's Actually Happening — Three Hypotheses

You need customer discovery interviews to confirm, but here are the most likely root causes:

### Hypothesis 1: Wrong job, right product
Users hired it for Job A, but the product is really good at Job B. They get a honeymoon period of novelty, realize it doesn't move the needle on the thing they actually cared about, and fire it.
- **Signal**: churned users describe the product's value differently than marketing does
- **Fix**: re-segment. Talk to the customers who *don't* churn — what job are they getting done?

### Hypothesis 2: The "aha moment" happens too late (or never)
The product has value, but it takes 3+ weeks of setup, data entry, or workflow changes before the payoff. Month 2 is exactly when people quit things that haven't paid off yet.
- **Signal**: usage frequency drops sharply after week 2–3; power users all did some specific setup step
- **Fix**: find the activation moment the stickiest users hit, and architect onboarding around hitting it in week 1

### Hypothesis 3: The job is episodic, not recurring
Users hired it for a one-time job (launch a thing, migrate something, fix a problem), got it done, and fired the product correctly. A subscription pricing model is wrong for an episodic job.
- **Signal**: churned users say positive things about the product; usage spikes then drops to zero
- **Fix**: reprice (usage-based, project-based), or expand the product to cover a recurring job adjacent to the episodic one

---

## The Forces of Progress at Month 2

At the cancellation moment, these four forces are in play:

- **Push** (frustration with not using it): *"I keep paying and not logging in"* — this is what drives the cancel click
- **Pull** (toward alternative): usually **non-consumption** — going back to spreadsheets, manual work, or just not doing the job anymore
- **Anxiety** (about cancelling): low — they'll just re-sub if they need it
- **Habit** (of using your product): **never formed** — this is the whole problem

The leverage point: **habit never formed in weeks 1–3.** Everything else is downstream.

---

## What to do next — a 3-step discovery plan

### Step 1: Run churn interviews (10 minimum)
Don't ask "why did you cancel?" — they'll rationalize. Use **timeline reconstruction**:
- "Take me back to when you first signed up — what was happening that week?"
- "Walk me through the last time you logged in. What were you trying to do?"
- "When did you first think 'I might cancel this'? What had just happened?"
- "What are you using instead now?" (this reveals the real competitor, often non-consumption)

Listen for: the moment usage dropped, what they were doing *just before* that, and whether they ever hit an "it worked!" moment.

### Step 2: Instrument the Little Hire
Stop tracking signups and MRR as primary metrics. Track:
- Time-to-first-value (define one specific action = value delivered)
- Week 1, 2, 3, 4 active-use rate (separately)
- Frequency of the job-completing action per user per week
- The single behavior that distinguishes 6-month retained users from month-2 churners

### Step 3: Compare churners to survivors
Interview 5 users who've been around 6+ months alongside the 10 churners. The delta — what the retained users did in weeks 1–2 that churners didn't — is your onboarding redesign brief.

---

## One-sentence reframe for the client

> "You're not losing customers to competitors or price — you're losing them to non-consumption, because the product never became a habit in the first three weeks. Fix the Little Hire, or nothing else matters."

---

## Diagnostic scorecard (current state)

| JTBD Dimension | Score | Gap |
|---|---|---|
| Can state the job without mentioning product? | ? | Unknown — needs interviews |
| All 4 forces mapped? | 3/10 | Only Pull addressed; Push/Habit/Anxiety unexamined |
| Emotional + social dimensions understood? | 2/10 | Treated purely as functional/transactional problem |
| Non-obvious competition mapped? | 2/10 | Non-consumption not on their radar |
| Little Hire tracked separately from Big Hire? | 1/10 | **This is the core blind spot** |
| Churn interviews conducted? | 0/10 | None yet — first priority |

**Current JTBD-adherence score: 2/10.** Path to 10/10 runs through the 3-step discovery plan above, starting with churn interviews this week.
