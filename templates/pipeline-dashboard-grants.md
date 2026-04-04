# Say Why — Grant Pipeline

*Fiscal sponsor: TBD (Open Circle Foundation inquiry pending)*
*Status: Building pipeline*

---

## Dataview Queries

### Active Applications

```dataview
TABLE status, funder, grant_range, deadline, next_action
FROM "Practice/Rubinstein Productions/Outreach/grants/funders"
WHERE status != "declined" AND status != "passed"
SORT deadline ASC
```

### Upcoming Deadlines (Next 60 Days)

```dataview
TABLE funder, deadline, status, next_action
FROM "Practice/Rubinstein Productions/Outreach/grants/funders"
WHERE deadline != "" AND deadline <= date(today) + dur(60 days)
SORT deadline ASC
```

### By Funding Angle

```dataview
TABLE funder, angle, grant_range, status
FROM "Practice/Rubinstein Productions/Outreach/grants/funders"
SORT angle ASC
```