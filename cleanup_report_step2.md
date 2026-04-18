# Step 2 Cleanup Report — TrajectoryStore / Commitments / Attention + Conversation Write Path Migration

**Branch**: `refactor/step2-trajectory-store`
**Baseline tag**: `pre_recast_v2_step2` → `0e782e4` (Step 1 merge commit)
**Date range**: 2026-04-18
**Pre-step-2 baseline**: 920 tests (Step 1 complete, `recast_v2_step1_complete`)
**Final test count**: **1033** pass (net +113 vs. pre-branch 920)

Blueprint v2.0 Step 2 executed per Kevin's 2026-04-18 brief + in-flight
scope revision (§"Scope 修订"): ConversationMemory is no longer deleted
end-of-step — it survives as a non-conversation facade (todos / reminders
/ user_facts / discoveries / interest_topics / FTS5 index). Only the
conversation write path and session machinery are stripped.

---

## 1. Commit Sequence

| Commit   | Step | Summary                                                              |
|----------|------|----------------------------------------------------------------------|
| 9adfa27  | 2a   | TrajectoryStore skeleton with append-only timeline                   |
| 473832a  | 2b   | CommitmentStore skeleton                                             |
| 14d94f2  | 2c   | AttentionManager singleton + think_conversational wiring             |
| 6f36787  | 2d   | scripts/migrate_to_trajectory.py dry-run + write path                |
| abf9168  | 2e   | --init-schema subcommand + production migration (1351 rows)          |
| 2b814a4  | 2f   | ConversationMemory mirrors writes to TrajectoryStore (dual-write)    |
| d8a824a  | —    | docs: log 2f real-world observations — ghost task + deploy.sh stop   |
| 645c5ad  | 2g   | Switch conversational history reads to TrajectoryStore               |
| d89ca95  | —    | docs: log 2g validation — memory-read PASS + 3 Step-5 case studies   |
| 4cb8de5  | 2h   | Disable ConversationMemory writes to conversations table             |
| 4461ded  | 2i   | Rename __consciousness__ to __inner__ in active code                 |
| bd6185e  | 2j   | Remove session machinery + archive + DROP production sessions table  |
| *TBD*    | 2k   | OpenAI stop_reason normalisation + tool_use branch test (Step 1 debt)|
| *TBD*    | 2l   | Integration tests + this cleanup report                              |

---

## 2. New Files

| Path                                                    | Lines | Purpose                                                         |
|---------------------------------------------------------|-------|-----------------------------------------------------------------|
| `src/core/trajectory_store.py`                          | 301   | Cross-channel behaviour timeline + TrajectoryEntryType enum     |
| `src/core/commitments.py`                               | 264   | CommitmentStore skeleton (Step 5 populates)                     |
| `src/core/attention.py`                                 | 197   | In-memory AttentionState + event-sourced recovery               |
| `src/core/trajectory_compat.py`                         | 92    | Transitional shim TrajectoryEntry → `{role, content}` dict      |
| `scripts/migrate_to_trajectory.py`                      | 475   | conversations → trajectory migration (dry-run / execute / init-schema) |
| `scripts/verify_dual_write.py`                          | 257   | Snapshot + diff helper for 2f/2g validation runs                |
| `scripts/drop_sessions_table.py`                        | 132   | Archive + DROP legacy sessions table (2j one-shot)              |
| `tests/core/test_trajectory_store.py`                   | 296   | 21 tests                                                        |
| `tests/core/test_commitments.py`                        | 172   | 13 tests                                                        |
| `tests/core/test_attention.py`                          | 199   | 15 tests                                                        |
| `tests/core/test_trajectory_compat.py`                  | 144   | 15 tests                                                        |
| `tests/core/test_brain_load_history.py`                 | 96    | 4 tests                                                         |
| `tests/memory/test_conversation_dual_write.py`          | 218   | 11 tests (13 originally, -2 session deletions in 2j)            |
| `tests/scripts/__init__.py`                             | 0     |                                                                 |
| `tests/scripts/test_migrate_to_trajectory.py`           | 349   | 25 tests                                                        |
| `tests/integration/test_step2_trajectory_integration.py`| *new* | 9 end-to-end tests                                              |
| `docs/refactor_v2/step2_data_audit_notes.md`            | —     | Rolling audit notes: data gap + 2f/2g validation case studies   |

## 3. Deleted Code (Inline — class still exists)

`ConversationMemory` loses 7 session-scoped methods + session DDL (2j):

| Location (before)                       | Lines | Reason                                                   |
|-----------------------------------------|-------|----------------------------------------------------------|
| `src/memory/conversation.py` append_to_session        | ~22   | Session writes — concept removed, Step 4 redefines       |
| `src/memory/conversation.py` get_session_messages    | ~4    | Read by session — dead                                   |
| `src/memory/conversation.py` _load_session_history   | ~22   | DB→cache hydration for session — dead                    |
| `src/memory/conversation.py` load_session_from_snapshot | ~3 | Snapshot recovery for condensed session — dead           |
| `src/memory/conversation.py` clear_session_cache      | ~3    | Dead                                                     |
| `src/memory/conversation.py` replace_session_history  | ~3    | Dead (compactor callers deleted 2j)                      |
| `src/memory/conversation.py` remove_last_session      | ~16   | Dead                                                     |
| `src/memory/conversation.py` _session_store attribute | ~1    | Cache map for dead methods                               |
| `src/memory/conversation.py` ALTER TABLE session_id / parent_session_id / lineage_root_id / compression_summary | ~14 | Schema migration blocks for removed columns |
| `src/memory/conversation.py` sessions table DDL + indexes | ~19  | Table no longer created on fresh installs               |
| `src/memory/conversation.py` session_id in search_history/get_messages SELECT + output dict | ~8 | Output shape cleanup |
| `src/memory/compactor.py` session_manager param + branch  | ~18   | Dead parameter + unreachable branch                      |
| `src/core/brain.py` _ThinkCtx.session_id + 3 threading sites | ~4  | Always-None in production; removed                       |

**Production DB**: `sessions` table (149 rows) archived to
`~/lapwing-backups/pre_step2_20260418_135452/sessions_archive.json`
(73KB, rows preserved as JSON), then `DROP TABLE sessions`. Related
indexes (`idx_sessions_chat_id_status`, `idx_sessions_last_active`,
`idx_conversations_session_id`) dropped in the same transaction.

Inline ConversationMemory conversation-write path (2h — stripped, not
deleted because the signature is kept):

| Location                  | Change                                                                  |
|---------------------------|-------------------------------------------------------------------------|
| `append()`                | Body reduced; no INSERT INTO conversations / FTS sync                   |
| `append_to_session()` (before 2j) | Same treatment then deleted in 2j                               |
| `remove_last()`           | Body replaced with log + early return when trajectory is wired          |
| `remove_last_session()` (before 2j) | Same then deleted                                               |

## 4. Schema Changes

### 4.1 New tables (in `data/lapwing.db`)

```sql
CREATE TABLE trajectory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    entry_type TEXT NOT NULL,
    source_chat_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    content_json TEXT NOT NULL,
    related_commitment_id TEXT,
    related_iteration_id TEXT,
    related_tool_call_id TEXT
);
CREATE INDEX idx_traj_timestamp ON trajectory(timestamp);
CREATE INDEX idx_traj_chat      ON trajectory(source_chat_id, timestamp);
CREATE INDEX idx_traj_type      ON trajectory(entry_type, timestamp);
CREATE INDEX idx_traj_iteration ON trajectory(related_iteration_id, timestamp);

CREATE TABLE commitments (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    target_chat_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source_trajectory_entry_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    status_changed_at REAL NOT NULL,
    fulfilled_by_entry_ids TEXT,
    reasoning TEXT
);
CREATE INDEX idx_commit_status ON commitments(status, created_at);
CREATE INDEX idx_commit_chat   ON commitments(target_chat_id, status);
```

### 4.2 Dropped (one-shot in 2j)

- `sessions` table + all its indexes
- `idx_conversations_session_id` (orphan index on conversations.session_id,
  a column that stays in the schema but is no longer written to; Step 3
  drops the conversations table entirely)

### 4.3 Mutation log (v2.0 Step 1 table) — Step 2 emitters

All four Step-2 event types were already declared in the MutationType enum
during Step 1 (§2.1 future-reservation). Step 2 is the first step with
emitters:

| Event                       | Emitter                                          |
|-----------------------------|--------------------------------------------------|
| `TRAJECTORY_APPENDED`       | `TrajectoryStore.append`                         |
| `ATTENTION_CHANGED`         | `AttentionManager.update`                        |
| `COMMITMENT_CREATED`        | `CommitmentStore.create`                         |
| `COMMITMENT_STATUS_CHANGED` | `CommitmentStore.set_status`                     |

---

## 5. Data Migration Audit (Step 2e)

Production `data/lapwing.db` state captured at start:

```
conversations total:     1354 rows
  919231551 (Kevin QQ):   780
  __consciousness__:      574
bad_role_count:             0
missing_ts_count:           0
empty_content_count:        3
sessions table:           149 rows
```

Migration run (`scripts/migrate_to_trajectory.py --execute`) produced:

```
trajectory writes:       1351 rows
  USER_MESSAGE:           369   (all 919231551 user rows)
  ASSISTANT_TEXT:         411   (all 919231551 assistant rows)
  INNER_THOUGHT:          571   (574 __consciousness__ - 3 empty discards)
                                split: 282 actor=lapwing / 289 actor=system
source_chat_id:
  919231551:              780   (= 369 + 411)
  __inner__:              571   (= 282 + 289)
```

Invariant: **1354 read = 1351 migrated + 3 discarded** ✓

Discards (all `empty_content` in consciousness/assistant, preserved in
dry-run report):

| id   | reason        | chat_id             | role      |
|------|---------------|---------------------|-----------|
| 1728 | empty_content | __consciousness__   | assistant |
| 1752 | empty_content | __consciousness__   | assistant |
| 1878 | empty_content | __consciousness__   | assistant |

Legacy `conversations` table **preserved** (Step 3 drops it per roadmap).

### 5.1 Pre-data-capture gap observation

Project start: **2026-03-29**. Earliest migrated trajectory row:
**2026-04-03T00:19:37** (conv id=1, text "3分钟后叫我"). Five-day gap
between project start and first captured conversation event. Timestamps
for all 1354 legacy rows parse as valid ISO-8601 → this is a genuine
"nothing was written" gap, not a parse failure. Likely early-development
window when the conversations table didn't exist yet or was rebuilt.
Logged in `docs/refactor_v2/step2_data_audit_notes.md`. No retroactive
data recovery attempted.

### 5.2 Session data

149 rows archived verbatim to JSON before DROP:

```
status histogram:  dormant=9 / deleted=135 / condensed=3 / active=2
chat_id histogram: 919231551=23 / __consciousness__=126
archive:           ~/lapwing-backups/pre_step2_20260418_135452/sessions_archive.json (73KB)
```

Step 4 ("main loop unification" + redefined session semantics bound to
attention focus) can read this archive if a pre/post comparison is needed.

---

## 6. Sub-Phase Flow — Dual-Write → Read-Switch → Write-Disable

The ConversationMemory migration was split into three independently
committed sub-phases (2f / 2g / 2h) with a real-conversation validation
between each. Both validation runs were conducted by Kevin on the QQ
adapter; diffs were captured by `scripts/verify_dual_write.py` and
pasted into the cleanup audit notes.

### 6.1 Sub-phase A (2f) — dual-write

- `ConversationMemory.append` writes to the legacy `conversations` table
  AND mirrors to `TrajectoryStore`.
- Validation: Kevin ran a four-turn QQ conversation. Diff reported
  **8 conversations rows ↔ 8 trajectory rows matched**, zero unmatched
  on either side. PASS.
- One pre-existing ghost task surfaced (`conv#1907 "等我查一下"` — no
  follow-up). Logged as Step 5 Commitment Reviewer evaluation material.

### 6.2 Sub-phase B (2g) — read-path switch

- `brain._load_history` (new consolidated helper at brain.py:140) now
  reads from `TrajectoryStore.relevant_to_chat(chat_id, n=MAX_HISTORY_TURNS*2,
  include_inner=False)` and converts via `trajectory_compat`.
- `ConversationCompactor` switched to the same path; its dead session
  branch was tagged for removal in 2j.
- Validation: Kevin ran a four-turn callback-heavy conversation
  (`帮我记一下我下周末去泡温泉` → `你刚刚记了什么` → `除了那个…` →
  `把你记住的都说一遍`). Cross-turn memory retrieval **worked**:
  turn 2 recalled `泡温泉` verbatim, turn 3 resolved `那个`, turn 4
  showed she knew what `都说一遍` meant. No order scrambling, no
  inner-thought leak. PASS.
- Two more Step-5 data points: turn 1 and turn 3 each claimed a
  reminder was set; `reminders` table query for the validation window
  returned **0 rows**. Self-aware-but-inconsistent hallucination
  pattern captured in audit notes.

### 6.3 Sub-phase C (2h) — write-path disable

- `ConversationMemory.append` / `append_to_session` no longer write to
  the `conversations` table or its FTS index; trajectory is the sole
  write target.
- `remove_last` / `remove_last_session` became no-ops when trajectory
  is wired. Rationale: trajectory is append-only, and mutation_log
  already captures LLM failures via `LLM_REQUEST` without a matching
  `LLM_RESPONSE`.
- Legacy tests that relied on `append()` populating the FTS index
  (`test_conversation_fts.py` / `test_conversation_archive.py`) were
  rewritten to seed data via direct SQL, exercising the read APIs
  over pre-existing rows — the real-world shape post-2h.
- Fixture hang root-caused and fixed in `test_conversation_archive.py`
  (an async-fixture `return m` without `yield`/close caused pytest
  cleanup to deadlock on assertion failure). Now uses `yield` + `close`.

---

## 7. Cleanup Invariants (grep exit criteria)

All verified in `src/` (tests + docs excluded):

```
grep 'INSERT INTO conversations'             →  only conversations_fts
                                                (FTS backfill path in init)

grep 'conversation_memory\.(append|write|   →  0 hits
      insert|update|delete)'

grep '__consciousness__'                     →  1 hit: comment on
                                                consciousness.py:259

grep 'session_id|session_manager|            →  2 hits, both comments:
      _session_store|append_to_session|         src/memory/conversation.py:192
      get_session_messages|…'                   src/core/trajectory_compat.py:4
```

### 7.1 Reachability chain — compactor `session_manager` param

Compactor's `session_manager=None` default parameter was unreachable dead
code even before 2j:

1. `ConversationCompactor.__init__` accepts `session_manager=None`.
2. `src/core/brain.py:123` constructs it without passing `session_manager`.
3. grep across `src/` for `ConversationCompactor(` → **single call site,
   no kwarg passed**. Default None always applied.
4. Grep for `session_manager=` as a kwarg across `src/` → **no hit**
   in post-deletion source. Parameter was truly orphaned.

Removal is safe by construction.

### 7.2 Reachability chain — PromptSnapshotManager.session_id

`src/core/prompt_builder.py:30-49` defines `PromptSnapshotManager._session_id`
— kept intentionally in Step 2 because:

1. It is an **opaque cache key**, not a ConversationMemory session concept.
2. `freeze()` / `get()` with a `session_id` argument are never called in
   `src/` (grep hits: only unit tests). Only `invalidate()` is called
   (3 times in `brain.py`).
3. The cached value (`_frozen`) is therefore always None at runtime.
   Removing the attribute is a valid but out-of-scope refactor.

Flagged as **Step 3 debt** — StateSerializer will reimplement the
prefix-cache snapshot with explicit keys.

### 7.3 Reachability chain — __consciousness__ → __inner__ rename

`src/core/consciousness.py:256` used to declare `chat_id = "__consciousness__"`;
after 2i it declares `"__inner__"`. Data flow into trajectory:

1. `consciousness._think_freely` builds `internal_message` and calls
   `brain.think(chat_id="__inner__", user_message=internal_message)`.
2. `brain.think` → `_prepare_think` calls `memory.append("__inner__",
   "user", internal_message)` (prompt side) and later
   `memory.append("__inner__", "assistant", reply)` (response side).
3. `memory.append` detects `trajectory is not None`, calls
   `_mirror_to_trajectory(chat_id="__inner__", ...)`.
4. `_mirror_to_trajectory` maps `chat_id == "__inner__"` →
   `entry_type = INNER_THOUGHT`, `source_chat_id = "__inner__"`,
   actor = `lapwing` (assistant) or `system` (user/prompt side).
5. `trajectory.append` writes + emits `TRAJECTORY_APPENDED`.

Verified by `tests/integration/test_step2_trajectory_integration.py::
TestInnerTickRemap::test_inner_write_categorised_as_inner_thought`.

---

## 8. Debt Registry (delta vs. Step 1)

### Cleared in Step 2

- **Step 1 debt #7.4a — OpenAI stop_reason normalisation**: closed in 2k.
  `_mut_stop_reason` now maps `stop`→`end_turn` / `tool_calls`→`tool_use`
  / `length`→`max_tokens`; others pass through.
- **Step 1 debt #7.4b — OpenAI tool_use branch test missing**: closed in
  2k. `tests/logging/test_llm_router_tracking.py` now has dedicated
  coverage for the tool_calls branch + the new pass-through + length
  mappings.

### Adjusted in Step 2

- **Step 1 debt #7.1 — `message.received` / `message.sent` → `TRAJECTORY_APPENDED`**:
  original plan was "Step 2 migrates the SSE event source". The dispatcher
  emits in-process pub/sub events that the desktop SSE route consumes;
  trajectory is durable persistence. Merging them requires the
  dispatcher→mutation_log subscription plumbing that is part of Step 4's
  main-loop unification. **Rescheduled to Step 4**. Both event systems
  continue to run in parallel during Step 2/3.

### New — carried forward

1. **[Pre-existing, not in v2.0 roadmap] `get_last_interaction` /
   `get_all_chat_ids` ghost methods.**
   - Location: `src/api/routes/status_v2.py` (calls inside try/except);
     8 test files mock them.
   - Defined: nowhere in source.
   - Current behaviour: production calls raise AttributeError silently;
     tests override via mock.
   - Need product decision: what should these return? Is the desktop
     status page actually using their values?
   - Schedule: separate, does not block v2.0 Steps.

2. **[Pre-existing infrastructure] `scripts/deploy.sh stop` silently
   restarts.**
   - Root cause: the script has no argument parsing; it unconditionally
     runs kill-old + spawn-new.
   - Impact: every Step's "stop → modify → restart" flow is unreliable
     without a manual `kill $(cat data/lapwing.pid)`.
   - Fix: add `start`/`stop` subcommands, or rename the script to
     `restart.sh` to match actual behaviour.

3. **[Step 3 debt] `trajectory_compat` transitional shim.**
   - `src/core/trajectory_compat.py` (92 lines). The StateSerializer
     lands in Step 3 and replaces the legacy `{role, content}` dict
     shape with a richer prompt rendering; this file deletes then.

4. **[Step 3 debt] Conversations table + FTS5 index still present.**
   - Table retained with 1354 pre-migration rows; FTS still works over
     them via `search_history` / `search_deep_archive`. Step 3 drops
     both and kills the associated legacy API.

5. **[Step 3 debt] PromptSnapshotManager explicit cache keys.**
   - See §7.2. StateSerializer reimplements the prefix-cache in a form
     that doesn't borrow the word "session".

6. **[Step 4 debt] Consciousness path still uses `chat_id="__inner__"`
   sentinel.**
   - The active string was renamed in 2i but dispatch still happens via
     `chat_id` and `_mirror_to_trajectory`'s remap branch. Step 4 (main
     loop unification) introduces a dedicated brain.think_inner entry
     point and removes the sentinel-based dispatch.

7. **[Step 4 debt] Session semantics re-specification.**
   - Sessions were removed wholesale in 2j; Step 4 re-introduces them
     bound to attention focus rather than chat_id. The archived
     `sessions_archive.json` (149 rows) is available as reference data.

8. **[Step 4 debt] SSE desktop event source (`message.received` /
   `message.sent`) not yet mutation-log-driven.**
   - See "Adjusted" above — rescheduled from Step 2 to Step 4.

9. **[Step 5 evaluation corpus] Two real-world hallucination cases +
   one ghost-task case captured during 2f/2g validation.**
   - Details in `docs/refactor_v2/step2_data_audit_notes.md`. Concrete
     inputs for Step 5's Commitment Reviewer + post-action honesty hook
     to regression-test against.

---

## 9. Desktop Compatibility Matrix

The desktop v2 frontend consumes three data streams:

1. **FastAPI routes under `src/api/routes/`** — unchanged in Step 2.
2. **Dispatcher SSE** for live message events — unchanged in Step 2.
   `dispatcher.submit("message.received"/"message.sent", ...)` still
   fires inside brain; routing remains in-process.
3. **Direct DB reads** where applicable — only status_v2's `get_last_interaction`
   (ghost, debt #1 above) and the `/system/*` endpoints (no conversation-table
   dependency).

No conversation-flow route reads from the `conversations` table directly;
all history goes through brain.memory, which now routes to trajectory.
The desktop frontend therefore **does not need changes in Step 2**.

Verified via grep:
```
grep 'conversations' src/api/            →  no hits (only via brain.memory)
```

---

## 10. Test Count Trajectory

| Milestone       | Count | Delta |
|-----------------|-------|-------|
| Pre-Step-2 (Step 1 merge)          | 920   | —     |
| After 2a (TrajectoryStore)         | 941   | +21   |
| After 2b (Commitments)             | 954   | +13   |
| After 2c (Attention + wiring)      | 969   | +15   |
| After 2d (migration dry-run)       | 994   | +25   |
| After 2f (dual-write tests)        | 1007  | +13   |
| After 2g (compat + load_history)   | 1026  | +19   |
| After 2h (archive/fts rewrites)    | 1026  | ±0 (rewrote, didn't add) |
| After 2i (inner-tick rename)       | 1026  | ±0 (renamed, didn't add) |
| After 2j (session removal)         | 1024  | -2  (deleted two session-specific tests) |
| After 2k (OpenAI normalisation)    | 1027  | +3    |
| After 2l (integration test)        | 1036  | +9    |

**Final**: 1036 pass (target in the 1030s after the 2 session deletions —
net +116 across Step 2).

---

## 11. Step 2 Exit Checklist

- [x] TrajectoryStore implementation + 21 unit tests
- [x] CommitmentStore skeleton + 13 unit tests
- [x] AttentionManager singleton + 15 unit tests + brain entry-point wiring
- [x] Migration script with --init-schema / --dry-run / --execute / --force
- [x] Production data migrated (1351 rows, invariant satisfied)
- [x] ConversationMemory dual-write (2f) → read switch (2g) → write disable (2h)
- [x] Real-conversation validation at 2f and 2g — both PASS
- [x] __consciousness__ sentinel renamed to __inner__ in active code
- [x] Session machinery fully removed (methods, attribute, DDL, DB table)
- [x] OpenAI stop_reason normalisation (Step 1 debt cleared)
- [x] 9 end-to-end integration tests
- [x] Full suite green (1036 pass)
- [x] Cleanup report with debt registry + reachability proofs
- [x] Desktop compatibility verification (no route changes required)

## 12. Next Step Preview (not executed)

**Step 3** per blueprint: StateSerializer replaces PromptBuilder;
`brain.get_context()` deleted; `trajectory_compat` shim deleted;
`conversations` table + FTS index dropped; `search_history` /
`search_deep_archive` / `get_active` legacy APIs either migrated or
removed.

The entire `memory_tools_v2.py` caller set for the legacy search APIs
(`tools/memory_tools_v2.py:267, 317`) becomes a Step 3 decision point.
