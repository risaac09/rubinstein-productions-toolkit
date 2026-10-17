> **Status:** draft. The item wording is quoted from the [Measurement Framework](measurement-framework.md) and the [Nomadic Indicators Codebook](nomadic-indicators-codebook.md) and is not changed here. The open points were decided 2026-10-16, and the instrument is a first working version until it has run on an engagement. This file is owned by this repo; the two documents it quotes are owned by the private corpus (see [TRUTH-OWNERSHIP](../docs/TRUTH-OWNERSHIP.md)), so if the wording below and theirs differ, theirs is right.

# Measurement Instrument

The Measurement Framework names the questions a client is asked. This file turns them into something an evaluator can run: the items in order, a response file with a fixed header, a scoring procedure, and a worked example with its answer. Everything here is plain text. The files that go with it are in [`instrument/`](instrument/).

| File | What it is |
|---|---|
| [`instrument/responses-example.csv`](instrument/responses-example.csv) | Twelve invented responses. Respondent codes, dates and scores are made up. |
| [`instrument/score.sh`](instrument/score.sh) | Scores a response file. POSIX awk, no other dependency. |
| [`instrument/expected-output.txt`](instrument/expected-output.txt) | What `score.sh` prints for the example. Section 6 reproduces it by hand. |
| [`../scripts/test-instrument-score.sh`](../scripts/test-instrument-score.sh) | Checks the script against the example and against files it must refuse. |

## 1. Where each part comes from

| Part | Source | Status |
|---|---|---|
| Seven Likert statements, 1 to 5 | Framework section 1, "Client Satisfaction Scores" | Wording canonical. Anchors and schedule decided 2026-10-16. |
| NPS question, 0 to 10 | Framework section 1 | Wording and scale canonical. Calculation decided 2026-10-16. |
| Five assessment points | Framework section 1 | Canonical. Which items go at which point decided 2026-10-16. |
| Three Consistency Questions, 1 to 10 | Framework section 2 and Codebook, "Consistency Scoring Protocol" | Wording and bands canonical. Aggregation decided 2026-10-16. |
| Stakeholder groups | Codebook, cross-stakeholder table | Five names canonical. A founder counts as `leadership` (2026-10-16). |
| Response file, procedure, script | This file | First working version. |

## 2. The items

Items are quoted exactly. The label in parentheses is the framework's own name for what the item measures; it is a column name in the response file and is not shown to the respondent.

### 2.1 Likert statements, rated 1 to 5

1. "We can articulate our mission with ease and specificity" (Narrative Clarity)
2. "Our lived culture matches our public narrative" (Internal Alignment)
3. "The facilitation process honored my actual experience" (Feeling Heard)
4. "I felt secure being authentically seen" (Safety in Vulnerability)
5. "I understand my work differently through this process" (Clarity Gained)
6. "This was collaborative, not extractive" (Co-Creation Quality)
7. "We're proud to share this publicly" (Representative Pride)

**Anchors (decided 2026-10-16).** 1 is "strongly disagree" and 5 is "strongly agree". The middle points carry no label. The same line is in [Measurement Framework](measurement-framework.md) section 1, synced from the corpus canonical.

### 2.2 Net Promoter question, rated 0 to 10

"How likely are you to recommend this process to similar organizations?"

### 2.3 The Three Consistency Questions, rated 1 to 10

Asked of diverse stakeholders (the codebook names staff, board and beneficiaries; its table adds leadership and funders).

1. "This narrative holds my contradictory experience."
2. "I can locate myself in this story without erasing complexity."
3. "This makes our conflicts generative rather than destructive."

### 2.4 Assessment points

The framework collects satisfaction scores at five points. These are the values of the `assessment_point` column.

| Value | Framework name |
|---|---|
| `pre-engagement` | Pre-Engagement |
| `mid-point` | Mid-Point |
| `delivery` | Delivery |
| `followup-2wk` | 2-Week Follow-up |
| `followup-3mo` | 3-Month Follow-up |

**Items per point (decided 2026-10-16).** The framework does not say which items are asked at which point, and some are plainly unanswerable early: nobody can rate whether the facilitation honored their experience before it has begun. Narrative Clarity can be asked early, and the framework's Narrative Clarity score is a pre and post measure, so it needs a Before figure. The schedule is:

| Assessment point | Items asked |
|---|---|
| `pre-engagement` | Likert item 1 (Narrative Clarity) and the three consistency questions |
| `mid-point` | Likert item 4 (Safety in Vulnerability) |
| `delivery` | The seven Likert items and NPS |
| `followup-2wk` | None |
| `followup-3mo` | The three consistency questions and NPS |

Asking clarity and consistency before and after gives the dashboard a delta for each. The response file still allows any item to be blank at any point, so `score.sh` needed no change for the clarity item; it reports `narrative_clarity` at `pre-engagement` like any other cell. The example file holds two of the five points, `delivery` and `followup-3mo`, and follows this schedule for both.

**When each point falls (decided 2026-10-16).** Each point is tied to an event in the engagement. The phases are Discovery, Development, Production and Delivery, as in the [case study template](../templates/case-study-template.md). Discovery maps to Container, Development to Excavation, Production to Performance and Delivery to Translation.

| Assessment point | Falls at | Sent by | Respondent-code key |
|---|---|---|---|
| `pre-engagement` | When the scope is signed, before the first session | The facilitator, as a link to the form | Only in the private engagement record |
| `mid-point` | The end of Phase 2 (Development), never in the filming session | The facilitator, as a link | Only in the private engagement record |
| `delivery` | At final approval | The facilitator, as a link | Only in the private engagement record |
| `followup-2wk` | 14 days after delivery. A conversation; the schedule above asks no survey items | The facilitator | Only in the private engagement record |
| `followup-3mo` | 90 days after delivery | The facilitator, as a link | Only in the private engagement record |

A Founder Story has no mid-point. The filming session is the Zero-Measurement Zone (see the [Session Facilitation Guide](session-facilitation-guide.md), session type 3), so no form is sent in it or around it. The key from respondent code to person is never written into the response file, the form or any shared document. The client-facing form is [`templates/respondent-sheet.md`](../templates/respondent-sheet.md).

**Funded cohorts.** A funded cohort runs both schedules side by side. The [evaluation framework](evaluation-framework.md) waves (intake, 30 days, 60 days, 6 months) are interview-coded narrative coherence; the survey runs at the five points above, and `score.sh` stays at five.

Framework section 6 lists five more Likert dimensions for the 2-week conversation: Narrative Accuracy, Functional Utility, Representative Pride, Future Willingness and Stakeholder Clarity. It gives names and no statements, so they stay out of the response file until the corpus canonical gives the statements (decided 2026-10-16); the corpus owns that wording. Representative Pride appears in both lists; the statement in 2.1 is the only text the canon provides for it.

### 2.5 A plain-text form

Any form tool that exports CSV will do, as long as the exported columns can be renamed to match section 3. One form per assessment point, with the items that point uses. [`templates/respondent-sheet.md`](../templates/respondent-sheet.md) is a client-facing version of it:

```
Respondent code: ______   Group: ______   Date: ______

For each statement, circle a number from 1 to 5,
1 is "strongly disagree" and 5 is "strongly agree".         1  2  3  4  5
  We can articulate our mission with ease and specificity
  ... (items 2 to 7 of section 2.1, one line each)

How likely are you to recommend this process to similar
organizations?                          0  1  2  3  4  5  6  7  8  9  10

For each statement, circle a number from 1 to 10.     1  2  3  4  5  6  7  8  9  10
  This narrative holds my contradictory experience.
  I can locate myself in this story without erasing complexity.
  This makes our conflicts generative rather than destructive.
```

## 3. The response file

One CSV file, one row per respondent per assessment point. The header row is fixed and `score.sh` refuses any file whose header differs. Fields are separated by commas and may not contain commas or quotation marks, so free text (a testimonial, a comment) belongs in a separate file keyed by the same three identifiers.

| Column | Type | Rule |
|---|---|---|
| `engagement_id` | text | Letters, digits, `-` and `_`. Matches the engagement record in [measurement-framework.md](measurement-framework.md) section 4. |
| `respondent_id` | text | A code, never a name. Same character rule. The key from code to person is kept apart from this file. |
| `stakeholder_group` | text | Lowercase letters, digits and `-`. The codebook's five are `leadership`, `staff`, `board`, `beneficiaries`, `funders`. A founder counts as `leadership`. Other values are accepted and sorted after those. `all` is reserved. |
| `assessment_point` | enum | One of the five values in 2.4. |
| `response_date` | date | `YYYY-MM-DD`. |
| `narrative_clarity` | integer 1 to 5, or blank | Item 1 of 2.1. |
| `internal_alignment` | integer 1 to 5, or blank | Item 2. |
| `feeling_heard` | integer 1 to 5, or blank | Item 3. |
| `safety_in_vulnerability` | integer 1 to 5, or blank | Item 4. |
| `clarity_gained` | integer 1 to 5, or blank | Item 5. |
| `co_creation_quality` | integer 1 to 5, or blank | Item 6. |
| `representative_pride` | integer 1 to 5, or blank | Item 7. |
| `nps` | integer 0 to 10, or blank | The question in 2.2. |
| `consistency_q1` | integer 1 to 10, or blank | Question 1 of 2.3. |
| `consistency_q2` | integer 1 to 10, or blank | Question 2. |
| `consistency_q3` | integer 1 to 10, or blank | Question 3. |

A blank means the item was not asked or not answered; the two are not told apart. Rules the script enforces: the three consistency columns are all filled or all blank; the combination of `engagement_id`, `respondent_id` and `assessment_point` appears once. A file that breaks any rule is refused whole, and the message names the line.

## 4. Scoring procedure

Run these steps in order on the rows that remain after the file passes validation. "Per assessment point" means each of the five values is scored separately and never pooled.

**Likert, per item.** For each assessment point and each of the seven item columns, take the non-blank values. The score is their sum divided by their count, shown with the count `n`. An item with no values at that point is not reported. There is no composite across items.

**NPS, mean.** For each assessment point, take the non-blank `nps` values. The mean is their sum divided by their count. The framework's targets in its section 9 ("Average NPS", 8.0 and 9.0+) are only meaningful on this reading, because the conventional net score cannot be 8.0.

**NPS, net.** On the same values, count promoters (9 or 10), passives (7 or 8) and detractors (0 to 6). The net score is 100 times (promoters minus detractors) divided by the number of values, shown to one decimal; it runs from -100 to 100.

**Headline (decided 2026-10-16).** The mean is the headline NPS, with the net score shown beside it. The framework states the 0 to 10 scale and not the method; the mean is what its benchmark uses, and the net score is the standard definition of an NPS, whose cut points are the ones above. The script prints both.

**Consistency, per respondent.** For each row with all three consistency values, the respondent's consistency average is `(q1 + q2 + q3) / 3`.

**Consistency, per group.** For each assessment point and each `stakeholder_group`, over the respondents in that group: the group's `q1`, `q2` and `q3` are the means of each question, and the group's `avg` is the mean of its respondents' consistency averages. The row `all` does the same over every respondent at that point, so each respondent counts once whatever the size of their group.

**Consistency, band.** Read each `avg` against the codebook's bands without rounding it first: 9 or more is exceptional, 7 up to 9 is strong, 5 up to 7 is moderate, 3 up to 5 is low, below 3 is incoherent. The canonical bands are the integer ranges 9-10, 7-8, 5-6, 3-4 and 1-2 and do not say where an average of 8.4 falls; reading each band as starting at its lower number is the choice made here.

**Consistency, spread.** For each assessment point, spread is the highest group `avg` minus the lowest. The framework says high variance between groups "indicates consistency failure at intersections" and sets no threshold, and none is set here, so the script reports the spread and does not call it high or low.

**Decided 2026-10-16.** The band edges, the equal weight per respondent and the use of spread in place of a variance stand as written. There is no spread threshold until three engagements give data to set one.

## 5. Running it

```bash
methodology/instrument/score.sh methodology/instrument/responses-example.csv
methodology/instrument/score.sh --engagement demo-001 responses.csv   # one engagement
scripts/test-instrument-score.sh                                     # the checks
```

The output has three tables, one each for Likert, NPS and consistency. Exit status is 0 when scored, 1 when the file is refused, 2 for a usage mistake. Output goes to the terminal; redirect it to keep it. The script reads the file and writes nothing.

## 6. Worked example

The example file has two assessment points. At `delivery`, six respondents answered the seven Likert items and the NPS question. At `followup-3mo`, the same six answered the NPS question and the three consistency questions. Respondents `r01` and `r02` are `leadership`, `r03` to `r05` are `staff`, and `r06` is `beneficiaries`. All of it is invented.

**Likert at `delivery`.** Values by respondent `r01` to `r06`, then the sum and the mean (sum divided by 6):

| Item | Values | Sum | Mean |
|---|---|---|---|
| `narrative_clarity` | 5 4 4 3 4 5 | 25 | 4.17 |
| `internal_alignment` | 4 4 3 2 3 4 | 20 | 3.33 |
| `feeling_heard` | 5 4 5 4 4 5 | 27 | 4.50 |
| `safety_in_vulnerability` | 5 5 4 3 4 5 | 26 | 4.33 |
| `clarity_gained` | 4 4 5 4 4 5 | 26 | 4.33 |
| `co_creation_quality` | 5 4 5 4 5 5 | 28 | 4.67 |
| `representative_pride` | 5 5 4 3 4 5 | 26 | 4.33 |

**NPS.** At `delivery` the values are 10, 9, 9, 6, 8, 10. Their sum is 52, so the mean is 52 / 6 = 8.67. Promoters are the four values of 9 or 10, the passive is the 8, and the detractor is the 6, so the net is 100 x (4 - 1) / 6 = 50.0. At `followup-3mo` the values are 9, 10, 8, 7, 9, 10: sum 53, mean 8.83; four promoters, two passives, no detractors, net 100 x 4 / 6 = 66.7.

**Consistency at `followup-3mo`.** Values are `q1 q2 q3`, then the respondent's average:

| Respondent | Group | Values | Average |
|---|---|---|---|
| `r01` | leadership | 8 8 7 | 7.67 |
| `r02` | leadership | 9 8 8 | 8.33 |
| `r03` | staff | 6 7 5 | 6.00 |
| `r04` | staff | 5 6 4 | 5.00 |
| `r05` | staff | 7 7 6 | 6.67 |
| `r06` | beneficiaries | 9 9 8 | 8.67 |

Group rows are the means of the respondent values:

| Group | n | q1 | q2 | q3 | avg | Band |
|---|---|---|---|---|---|---|
| leadership | 2 | 8.50 | 8.00 | 7.50 | 8.00 | strong |
| staff | 3 | 6.00 | 6.67 | 5.00 | 5.89 | moderate |
| beneficiaries | 1 | 9.00 | 9.00 | 8.00 | 8.67 | strong |
| all | 6 | 7.33 | 7.50 | 6.33 | 7.06 | strong |

For `all`, q1 is (8 + 9 + 6 + 5 + 7 + 9) / 6 = 7.33, and `avg` is (7.67 + 8.33 + 6.00 + 5.00 + 6.67 + 8.67) / 6 = 42.33 / 6 = 7.06. The spread is 8.67 - 5.89 = 2.78.

**Reading it.** On the invented data the narrative holds for leadership and the beneficiary respondent and is moderate for staff, whose lowest answer is question 3 (conflict made generative), at 5.00. At delivery, Internal Alignment is the weakest Likert item at 3.33, which is where the gap between lived culture and public narrative shows. The nets of 50.0 and 66.7 and the means of 8.67 and 8.83 are two views of one group: the means are above the framework's year-1 target of 8.0, while the net counts how many are clearly promoting. With six respondents none of these is evidence of anything; the example exists to show the arithmetic.

`instrument/expected-output.txt` is what `score.sh` prints for the same file, and `scripts/test-instrument-score.sh` fails if the script's output stops matching the numbers in this section.

## 7. Using it on a real engagement

Copy the header row from the example into a new file kept with the engagement's other records, add rows, and run the script. Keep real responses out of any public repository. The example's engagement `demo-001` and its respondent codes are placeholders and carry no information about any client.
