# Step 2 — Data Audit Notes (rolling)

This file is a scratchpad of audit observations discovered during Step 2
execution. The final narrative goes into `cleanup_report_step2.md`. All
timestamps here are real-world.

## 2026-04-18 — Timeline gap between project start and earliest trajectory entry

**Observation.** The legacy `conversations` table holds 1354 rows; after
Step 2e migration, `trajectory` holds 1351 rows. The earliest migrated
entry is:

```
id=1 ts=2026-04-03T00:19:37.898586  source=919231551  actor=user
     text='3分钟后叫我'
```

Project began on 2026-03-29, so there is a 5-day stretch between project
start and the first captured conversation event.

**Likely cause.** Early development window — either the `conversations`
table didn't exist / wasn't being written to, or a prior schema rebuild
wiped data. The stored timestamps are all valid ISO-8601 (no gaps from
unparseable rows), so this is a true "no rows were written" gap rather
than corruption.

**Disposition.** Independent observation. Not in Step 2 scope to repair.
No retroactive data recovery attempted. Logged here for the cleanup
report's data audit section.
