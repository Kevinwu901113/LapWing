# Step 2 Cleanup Report — TrajectoryStore / Commitments / Attention + Conversation Write Path Migration

**Branch**: `refactor/step2-trajectory-store`
**Baseline tag**: `pre_recast_v2_step2` → `0e782e4` (Step 1 merge commit)
**Date range**: 2026-04-18
**Pre-step-2 baseline**: 920 tests (Step 1 complete, `recast_v2_step1_complete`)
**Final test count**: **1038** pass (net +118 vs. pre-branch 920)

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

Discards (all three are **zero-length assistant replies** from the
consciousness tick loop — not schema violations; Lapwing literally
emitted `""` instead of `"无事"` or any text. Context verified by
reading the preceding row in each case):

| id   | chat_id             | role      | timestamp (UTC)              | content length | disposition                                                                       |
|------|---------------------|-----------|------------------------------|----------------|-----------------------------------------------------------------------------------|
| 1728 | `__consciousness__` | assistant | 2026-04-16T21:45:05.854740   | 0 chars        | empty reply to tick at 05:44 Friday (`id=1727`, system tick prompt). Next tick at 06:57 (`id=1731`). |
| 1752 | `__consciousness__` | assistant | 2026-04-17T01:07:09.756706   | 0 chars        | empty reply to tick at 09:06 Friday (`id=1751`). Next tick at 09:45 (`id=1754`).   |
| 1878 | `__consciousness__` | assistant | 2026-04-17T12:45:15.960005   | 0 chars        | empty reply to tick at 20:45 Friday (`id=1877`). Next tick at 21:22 (`id=1880`).   |

Discard rule: `content is None or content == ""` (`scripts/migrate_to_trajectory.py:_map_row`).
These would have become `INNER_THOUGHT` rows with empty `text` — semantically
indistinguishable from "no tick output", so dropping rather than storing
an empty row is the safer choice. All three are surrounded by normal
ticks, so losing them doesn't break downstream readers' continuity.

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

grep 'session_id|session_manager|            →  9 hits in src/:
      _session_store|append_to_session|         8 in PromptSnapshotManager
      get_session_messages|…'                     (src/core/prompt_builder.py:32,
                                                  34, 35, 37, 40, 41, 42, 49)
                                                   — unrelated "session" naming,
                                                  see §7.2 reachability proof;
                                                 1 comment:
                                                   src/memory/conversation.py:192
```

The 8 PromptSnapshotManager hits describe an **opaque cache key** named
`session_id` by legacy convention — it is not the ConversationMemory
session concept that Step 2j tore out, and no Step-2 call site passes
the old session_id into it. See §7.2 for the end-to-end proof; cleaned
up as part of Step 3 when StateSerializer replaces PromptSnapshotManager
wholesale.

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

Each item carries **清理时机** (when it's scheduled to be resolved) and
**清理条件** (the concrete criterion that tells the next executor the
debt is paid). Both fields must be satisfied for the item to move off
this list.

---

**Debt #1 — `get_last_interaction` / `get_all_chat_ids` ghost methods.**

- **Location**: `src/api/routes/status_v2.py:48,54` calls; 8 test files
  mock them. **Not defined** anywhere in source.
- **Current behaviour**: production calls raise AttributeError caught
  silently by try/except (last_msg_ts stays None); tests patch the
  methods before invocation.
- **清理时机**: independent, outside v2.0 roadmap. Kevin's product call.
- **清理条件**: either (a) both methods get real implementations that
  satisfy the status_v2 contract (returning datetime or None), with a
  test asserting real values flow through; or (b) the status_v2 call
  sites are deleted and all 8 test-file mocks are removed. Grep for
  `get_last_interaction\|get_all_chat_ids` must return 0 across `src/`
  + `tests/` + no untested try/except swallows.

---

**Debt #2 — `scripts/deploy.sh` silently restarts on `stop`.**

- **Root cause**: the script has no argument parsing; it unconditionally
  runs kill-old → `pkill -f main.py` → `nohup python main.py`. The
  `stop` argument Kevin passed was ignored.
- **Impact**: every Step's "stop → modify → restart" flow is unreliable
  without a manual `kill $(cat data/lapwing.pid)`.
- **清理时机**: independent infrastructure, outside v2.0 roadmap.
- **清理条件**: `bash scripts/deploy.sh stop` exits 0 having sent SIGTERM
  to the running main.py PID, waited for it to exit, removed the PID
  file, and left `data/*.db-wal` / `*.db-shm` cleared — without
  starting a new process. Separately, `bash scripts/deploy.sh start`
  or the current no-arg form must continue to deploy. A smoke test
  that asserts `ps aux | grep main.py` reports 0 matches after `stop`.

---

**Debt #3 — `trajectory_compat` transitional shim (Step 3).**

- File: `src/core/trajectory_compat.py` (92 lines).
- The shim converts `TrajectoryEntry → {role, content}` dicts so
  Step 2 could drop into the pre-existing `_recent_messages` pipeline
  without changing its signature.
- **清理时机**: Step 3 (StateSerializer replaces PromptBuilder).
- **清理条件**: StateSerializer reads TrajectoryEntry directly into
  its prompt serialization and emits final prompt bytes; no call to
  `trajectory_entries_to_legacy_messages` remains in `src/`; the file
  is deleted; `brain._load_history` either no longer exists or no
  longer imports from trajectory_compat.

---

**Debt #4 — Conversations table + FTS5 index still present (Step 3).**

- 1354 pre-migration rows retained in `data/lapwing.db`. New rows are
  never written (Step 2h invariant). FTS5 index still indexes those
  legacy rows and `search_history` / `search_deep_archive` / `get_active`
  still query them.
- **FTS backfill mechanism** (documented for completeness): on every
  `ConversationMemory.init_db()` call, `_migrate_fts` runs a
  `SELECT COUNT(*) FROM conversations_fts`; if 0, it bulk-inserts
  `(rowid, _cjk_tokenize(content))` from `conversations` rows where
  content is non-null, then commits. This is one-shot; subsequent
  boots skip the backfill. Post-2h, `_fts_insert` (the per-write sync)
  is no longer called from any production path — the FTS index is
  frozen on whatever the one-time backfill produced.
- **清理时机**: Step 3 drops the conversations table.
- **清理条件**: `DROP TABLE conversations` executed; `conversations_fts`
  and its shadow tables (`conversations_fts_config`, `…_content`,
  `…_data`, `…_docsize`, `…_idx`) also dropped; `search_history`,
  `search_deep_archive`, `get_active`, `get_messages`,
  `_get_surrounding_messages`, `_migrate_fts`, `_fts_insert`,
  `_load_recent_history`, `_cjk_tokenize` either deleted or moved to
  the trajectory query surface; `src/tools/memory_tools_v2.py:267,317`
  migrated to trajectory-based queries or removed.

---

**Debt #5 — PromptSnapshotManager "session_id" cache key (Step 3).**

- See §7.2. The class (`src/core/prompt_builder.py:23-49`) predates
  Step 2; it caches the frozen system-prompt snapshot keyed by an
  opaque string the callers name "session_id". Step 2 does not wire
  any caller to the `freeze()` / `get()` methods — production only
  calls `invalidate()`. The cache value is thus permanently None at
  runtime.
- **清理时机**: Step 3.
- **清理条件**: either the class is deleted as part of the
  StateSerializer rewrite; or its key argument is renamed to something
  unambiguous (e.g. `cache_key`). No `session` lexeme in
  `src/core/prompt_builder.py` after the change.

---

**Debt #6 — Consciousness path still uses `chat_id="__inner__"` sentinel
(Step 4).**

- The active string was renamed in 2i but dispatch still happens via
  `chat_id` and `_mirror_to_trajectory`'s `is_consciousness = chat_id
  == "__inner__"` branch (`src/memory/conversation.py:526`).
- **清理时机**: Step 4 (main-loop unification).
- **清理条件**: `brain.think_inner(internal_message)` (or equivalent
  dedicated entry) exists and is the sole consciousness caller;
  `brain.think` no longer sees `chat_id="__inner__"`; the
  `is_consciousness` branch in `_mirror_to_trajectory` is deleted;
  `grep '__inner__' src/` returns 0 hits in active code (migration
  data references `__inner__` are in `scripts/migrate_to_trajectory.py`
  and are fine — those comments / literals describe the
  post-migration source_chat_id, not active dispatch).

---

**Debt #7 — Session semantics re-specification (Step 4).**

- Sessions were removed wholesale in 2j. Step 4 re-introduces them
  bound to attention focus (topic continuity) rather than chat_id
  partitioning.
- **清理时机**: Step 4.
- **清理条件**: Step 4 spec + implementation adds a session abstraction
  keyed on AttentionState (e.g. `AttentionState.current_topic_id`),
  backed by Trajectory with no partitioning of the legacy
  `conversations` table. New session implementation has its own test
  suite + doesn't reintroduce any of the 7 methods deleted in 2j.

---

**Debt #8 — SSE desktop event source not mutation-log-driven (Step 4).**

- Scope adjustment from original Step 1 plan (§ "Adjusted"). Dispatcher
  still fires `message.received` / `message.sent` in `brain.py:631,653`
  for in-process desktop SSE consumers; trajectory / mutation_log is a
  parallel, durable stream. Merging them needs a
  dispatcher→mutation_log subscription bridge.
- **清理时机**: Step 4.
- **清理条件**: SSE route subscribes directly to
  StateMutationLog.query_by_window (or a live subscribe API) rather
  than Dispatcher's in-process pub/sub; `dispatcher.submit(
  "message.received"...)` + `"message.sent"...` callsites deleted
  from `brain.py`; SSE event shape documented as derived from
  TRAJECTORY_APPENDED payloads; desktop frontend tested against the
  new source.

---

**Debt #9 — Step 5 evaluation corpus.**

- The 2f + 2g validations captured three pre-existing hallucination
  cases + one ghost task (conv#1907, conv#1910, conv#1915, conv#1917).
  Details in `docs/refactor_v2/step2_data_audit_notes.md`.
- **清理时机**: Step 5 (Commitment Reviewer + post-action honesty hook).
- **清理条件**: Step 5's Reviewer passes a regression test harness that
  replays these specific row pairs and outputs (a) a `commitments`
  row with `status=pending` for conv#1917 ("等我看一下"), (b) an
  iteration-level hallucination flag for conv#1910 ("帮你记了" without
  a remind/todo call), and (c) auto-retract / retry trigger for
  conv#1915 ("帮你设置了" with specific time but no tool call). The
  test data set is captured as a fixture and referenced in the Step 5
  plan.

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
| 2l follow-up (master/step2 parity smoke) | 1038  | +2  |

**Final**: 1038 pass.

### 10.1 Math reconciliation — 118 vs. 116 vs. 118

Per-sub-phase deltas summed naively (positive only, pre-smoke-test):

  21 + 13 + 15 + 25 + 13 + 19 + 0 + 0 + 3 + 9 = **118 additions**

Net across Step 2 (accounting for the 2 session-specific tests deleted
in 2j — `test_append_to_session_records_legacy_session_id` and
`test_search_session_messages`):

  118 − 2 = **116 net delta**  (920 + 116 = 1036 ✓)

Both numbers are correct for the question they answer:
- **118** = total new tests authored through 2l.
- **116** = observable test-count delta from Step 1 baseline to 2l final.

The 2l follow-up commit adds 2 master-vs-step2 parity smoke tests,
bringing the final count to 1038 and the net delta to +118.

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
- [x] 9 end-to-end integration tests + 2 master-vs-step2 parity smoke tests
- [x] Full suite green (1038 pass)
- [x] Cleanup report with debt registry + reachability proofs + concrete
      discard content + 清理时机/清理条件 table
- [x] Desktop compatibility verification (no route changes required)
- [x] Backup conventions documented (`docs/refactor_v2/backup_conventions.md`)

## 12. Next Step Preview (not executed)

**Step 3** per blueprint: StateSerializer replaces PromptBuilder;
`brain.get_context()` deleted; `trajectory_compat` shim deleted;
`conversations` table + FTS index dropped; `search_history` /
`search_deep_archive` / `get_active` legacy APIs either migrated or
removed.

The entire `memory_tools_v2.py` caller set for the legacy search APIs
(`tools/memory_tools_v2.py:267, 317`) becomes a Step 3 decision point.
