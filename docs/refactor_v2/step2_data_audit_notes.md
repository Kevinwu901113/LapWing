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

## 2026-04-18 — Real-world ghost-task during 2f dual-write validation

**Observation.** During Kevin's 2f validation conversation on QQ, one of
his messages received a reply along the lines of "等我查一下" (let me
check) with **no follow-up action**. The corresponding row pair:

```
conv#1907  assistant @ 919231551   → mirrored to trajectory (ASSISTANT_TEXT)
   content: "等我查一下"
   no later conv / trajectory row completes the commitment
```

Lapwing promised work and never delivered. Dual-write captured the
promise faithfully in both tables, but the behavioural problem
(uncompleted commitment) is pre-existing.

**Why this matters for the v2.0 roadmap.** Step 5 specifies a
*Commitment Reviewer* loop that scans each iteration's trajectory output
for discrete promises and parks them in `commitments` with
`status=pending`. On every subsequent iteration, the Reviewer checks
whether the promise has been fulfilled; unfulfilled items surface in the
prompt as "outstanding commitments" until acted on or explicitly
abandoned.

This real-world case is the archetypal Step 5 trigger. Step 2 puts the
data structures in place (`CommitmentStore`, `TrajectoryEntryType.
TELL_USER` defined-but-unused); Step 5 will layer the Reviewer on top,
using rows like conv#1907 as evaluation material.

**Disposition.** Independent observation. Captured here so the Step 5
spec has a concrete case to regression-test against. Not a Step 2 fix.

## 2026-04-18 — Independent infrastructure debt: `deploy.sh stop` semantics

**Observation.** Kevin ran `bash scripts/deploy.sh stop` between the 2f
implementation and the validation conversation. The script output
included `[deploy] 启动新进程... PID 722842` — it spawned a new instance
rather than stopping the existing one.

**Root cause.** `scripts/deploy.sh` is a **deploy/restart** script. It
unconditionally runs: kill old PID via PID file → `pkill -f main.py`
fallback → `nohup python main.py` new process. The script accepts no
arguments; `stop` is silently ignored. There is no stop mode.

**Impact.** Any "stop → modify → restart" workflow that relies on
`deploy.sh stop` silently restarts instead of stopping. In particular:

- Step-boundary backup capture (this Step's `pre_step2_*` flow would
  have written a db to backup while a fresh instance was starting).
- WAL flush verification after shutdown.
- Any debugger-attach or deliberate-downtime scenario.

**Mitigation used in this Step.** Manual `kill $(cat data/lapwing.pid)`
→ `sleep 3` → verify WAL/SHM absent → `rm data/lapwing.pid`. Working as
expected, but the chain is longer than it should be.

**Disposition.** [Pre-existing, not in v2.0 roadmap]. Independent
infrastructure debt. Fix timing is Kevin's call — likely options:
add `stop`/`start` subcommands to deploy.sh, or rename to `restart.sh`
to match actual behaviour. Does not block any Step but will keep
ambushing future shutdown verifications until fixed.

## 2026-04-18 — 2g memory-read validation + three more Step-5 data points

**2g validation summary.** Kevin ran a four-turn QQ conversation
deliberately designed to force cross-turn memory recall. Dual-write
diff showed 8/8 matched rows; the memory-read switch passed both
objective (no context loss, no order scrambling, no inner-thought
leak) and subjective criteria.

**What passed (2g scope).** Turn 2 retrieved `泡温泉` verbatim from
turn 1; turn 3 resolved `那个` to the correct earlier referent; turn 4
showed she knew what `都说一遍` meant. TrajectoryStore-backed context
is indistinguishable from the legacy cache in behaviour.

**Three pre-existing data points surfaced, NOT 2g regressions.**

Evidence query (post-validation):

```
reminders created 2026-04-18 07:30–08:00 UTC:  0 rows
todos     created same window:                  0 rows
```

| Turn | Her claim | DB reality | Class |
|------|-----------|-----------|-------|
| 1 | "好 帮你记了" (re: 泡温泉) | no todo/reminder row | tool-call hallucination |
| 2 | "刚没真的记下来 现在帮你弄" | still no row | self-aware non-fix |
| 3 | "帮你设置了 周一早上九点提醒你" (找老师签名) | no reminder row | tool-call hallucination (specific time, more severe) |
| 4 | "等我看一下" | no follow-up reply | ghost task |

**Critical pattern.** Turn 2 proves she *can* recognise a prior fake
("刚没真的记下来"); turn 3 proves she then fakes again with more
specificity. The ability to self-correct exists but is not consistently
exercised. This is prime evaluation material for:

- **Step 1j LLM_HALLUCINATION_SUSPECTED observation patch** — these
  turns would all trigger the heuristic ("assistant reply claims prior
  action with zero tool calls in the same iteration").
- **Step 5 Commitment Reviewer** — turn 4's "等我看一下" with no
  follow-up is the canonical open-commitment case; reviewer would
  park it as `status=pending`.
- **Step 5 Post-action honesty hook** — before emitting a
  "帮你设置了" / "帮你记了" / "我查了" reply, cross-check that the
  iteration actually invoked the relevant tool. Reject + retry if not.

**Disposition.** Observations only. Step 2g shipped correctly; the
hallucinations are pre-existing and scheduled for Step 1j observation
data + Step 5 corrective mechanism.

