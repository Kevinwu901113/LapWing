# cleanup_report_step4.md — MainLoop Unification + OWNER Instant Interrupt

Step 4 of the v2.0 recast. Converts Lapwing's runtime from "consciousness
loop + adapter-driven think_conversational" into a single **MainLoop +
EventQueue**: every input — adapter messages, inner ticks, system signals
— flows through one queue and one consumer. OWNER messages can preempt
any in-flight LLM call mid-stream.

Branch: `refactor/step4-main-loop` from master `c20d1ed`.
Final tag: `recast_v2_step4_complete`.

## §1 — Deletion clipboard

| Path | Status | Replacement |
|------|--------|-------------|
| `src/core/consciousness.py` (476 LoC) | DELETED | `MainLoop` + `InnerTickScheduler` + `MaintenanceTimer` |
| `tests/core/test_consciousness.py` | DELETED | covered by inner-tick + main-loop test suites |
| `tests/core/test_consciousness_v2.py` | DELETED | same |
| `tests/core/test_consciousness_brain_integration.py` | DELETED | brain no longer holds `consciousness_engine` |
| `_refresh_voice_reminder` (in `src/core/task_types.py`) | DELETED | voice is placed at render time by `StateSerializer`; the helper was a silently-swallowed no-op since Step 3 |
| `_refresh_voice_reminder` import + call site in `src/core/task_runtime.py` | DELETED | same |
| `_in_conversation` flag, `_interrupt_flag`, `_save_interrupted_state`, `_log_activity`, `_NEXT_PATTERN`, `_silence_based_interval`, `_adjust_interval_after_tick`, `_think_freely`, `_run_maintenance_if_due` (all on the deleted `ConsciousnessEngine`) | DELETED with the file | logic redistributed per `step4_consciousness_migration.md` |
| `chat_id == "__inner__"` branch in `conversation._mirror_to_trajectory` | DELETED | `is_inner=True` parameter on `memory.append` |
| `ConversationMemory(chat_id="__inner__")` callers | DELETED | `brain.think_inner` uses internal session key `_inner_tick` and writes via `is_inner=True` |
| 3× `dispatcher.submit("message.received"\|"message.sent")` emit points in `brain.py` | DELETED | SSE subscribes to `StateMutationLog` directly (Step 4 M5); no other consumers |
| `ConsciousnessEngine` field on `LapwingBrain` | DELETED | not needed; `inner_tick_scheduler` + `attention_manager` cover the use cases |
| `app_state.consciousness` SSE projection | NULLED OUT | retained as `None` so any forgotten reader sees a clean signal |

## §2 — `grep -rn` verification

```
$ grep -rn '__inner__' src/ --include='*.py'
src/core/trajectory_store.py:126:        thoughts then had to use the ``"__inner__"`` sentinel string.
src/core/trajectory_store.py:176:            "(Step 4 M3 inner-thought writes use NULL instead of '__inner__')"
src/core/trajectory_store.py:321:        rows (where ``source_chat_id`` was the literal ``'__inner__'``)
src/core/brain.py:798:        ``__inner__`` sentinel), and parses ``[NEXT: Xm]`` from the
src/core/brain.py:819:        # adapter chat_ids without re-introducing the ``__inner__``
src/memory/conversation.py:170:        ``source_chat_id = NULL`` instead of the legacy ``'__inner__'``
src/memory/conversation.py:255:        ``source_chat_id = NULL``. The previous ``chat_id == "__inner__"``
```
All remaining hits are docstrings / log messages explaining the migration.
No active code references the sentinel.

```
$ grep -rn 'ConsciousnessEngine\|consciousness_engine' src/ --include='*.py'
(only docstring/comment references in main_loop.py / inner_tick_scheduler.py / maintenance_timer.py)
```

```
$ grep -rn 'message\.sent\|message\.received' src/ --include='*.py'
src/core/brain.py:664:        # Step 4 M5: dispatcher emits for message.received / message.sent
src/core/brain.py:762:            # Step 4 M5: message.sent dispatcher emit removed — SSE now
src/core/brain.py:1019:            # Step 4 M5: message.sent dispatcher emit removed — see M5.c.
```
Only `# removed` markers remain (kept as breadcrumbs for one cleanup
cycle, then deletable).

```
$ grep -rn '_refresh_voice_reminder' src/ tests/ --include='*.py'
(no matches)
```

## §3 — TODO / FIXME inventory

No new TODO/FIXME comments were introduced. The decision-memo file
(`docs/refactor_v2/step4_decisions.md`) explicitly tracks all open
work; in-code TODOs would duplicate it without a clearer cleanup
trigger.

## §4 — Test count delta

| Milestone | Total tests | Notes |
|-----------|-------------|-------|
| Baseline (master before branch) | 1148 | |
| After M1 | 1172 | +24 (events, event_queue, main_loop unit) |
| After M2 | 1176 | +4 (message-event integration) |
| After M3 | 1194 | +18 (inner_tick_scheduler unit + M3 integration) |
| After M4 | 1198 | +4 (owner-interrupt integration) |
| After M5 | 1207 | +9 (SSE format, mutation_log subscribe) |
| After M6+M7 | **1180** | +6 attention session, +2 parity, −∼35 deleted (3 consciousness test files) |

Final: **1180 passed** (full suite, exit code 0). Net **+32** tests
across Step 4 (after netting against deleted legacy tests). No
skipped/xfail introduced.

## §5 — Data migration verification

Schema change: `trajectory.source_chat_id` migrated from `TEXT NOT NULL`
to `TEXT` (nullable). Migration is idempotent and lives in
`TrajectoryStore._migrate_source_chat_id_nullable`:

  * Detects the original constraint by string-matching
    `sqlite_master.sql`. Only runs once per database.
  * Recreate-table dance preserves all existing rows verbatim
    (`INSERT INTO trajectory_new SELECT * FROM trajectory`).
  * All four indexes are recreated post-rename.

Backup taken before branch:
`~/lapwing-backups/pre_step4_20260418_233236/`. Contents:
`lapwing.db`, `consciousness.py.bak`, all adapter `.bak`s, `brain.py.bak`,
`container.py.bak`, `metadata.json` with table row counts and HEAD sha.

Pre-Step-4 row counts (from `metadata.json`): trajectory=1367,
event_log=4145, user_facts=107, discoveries=113, interest_topics=98,
reminders=3, reminders_v2=2, todos=0, commitments=0.

## §6 — Step-4 exit invariants

| # | Invariant | Status |
|---|-----------|--------|
| 1 | `MainLoop` is the sole runtime driver | ✅ container.start launches it; no parallel inner-tick loop |
| 2 | Adapters do not call `brain.think_conversational` directly | ✅ QQ/desktop both enqueue `MessageEvent`; `durable_scheduler._fire_agent` is the only remaining direct caller (D-3 — Step 5 carryover) |
| 3 | Inner ticks fire via `EventQueue` → `InnerTickEvent` | ✅ |
| 4 | OWNER message preempts in-flight LLM call | ✅ verified by `test_step4_m4_owner_interrupt.py::test_scenario1..2` |
| 5 | INTERRUPTED trajectory entry persisted on preempt | ✅ `brain._persist_interrupted` called from `think_conversational` + `think_inner` cancel handlers |
| 6 | SSE subscribes to `StateMutationLog` (not dispatcher) | ✅ `events_v2.py` uses `mutation_log.subscribe`; `mutation_log` adds `subscribe` / `unsubscribe` / `_fanout` |
| 7 | `__inner__` sentinel gone from active code | ✅ docstring mentions only |
| 8 | session is an AttentionState window (not a DB table) | ✅ `current_session_start` / `is_in_session` / `end_session` on `AttentionManager` |
| 9 | `consciousness.py` deleted | ✅ |
| 10 | Voice reminder no-op removed | ✅ |

## §7 — Architecture decisions

All decisions captured in `docs/refactor_v2/step4_decisions.md`.
Summary:

  * **D-1**: Branched from current master, not the step3 tag, because life_v2 features had already merged.
  * **D-2**: `MessageEvent` extended with `typing_fn` / `status_callback` / `done_future` for desktop-WS semantics.
  * **D-3**: `durable_scheduler._fire_agent` keeps calling brain directly (carryover for Step 5).
  * **D-4**: MainLoop start/stop ordering rationale.
  * **D-5**: EventQueue lives in `__init__`, not `prepare()`.
  * **D-6**: trajectory schema migration to nullable source_chat_id.
  * **D-7**: ConsciousnessEngine kept in maintenance-only mode for one milestone, then replaced by MaintenanceTimer in M7.
  * **D-8**: Legacy `chat_id == "__inner__"` branch removed in M7 along with consciousness.py.

Plus the full consciousness migration audit in
`docs/refactor_v2/step4_consciousness_migration.md` — feature-by-feature
ledger of what moved where and what got dropped.

## §8 — Carryover debt registry (for Step 5+)

| Item | Source | Cleanup trigger |
|------|--------|-----------------|
| `durable_scheduler._fire_agent` calls `brain.think_conversational` directly instead of enqueuing | Step 4 D-3 | Step 5+: introduce `ScheduledTaskEvent` (or similar) so the last brain call site goes through MainLoop |
| `# Step 4 M5: dispatcher emit removed` breadcrumb comments in `brain.py` | Step 4 M5.c | Delete after one review cycle (Step 5) once nobody is grep-searching for them |
| `_inner_tick` session-key string magic | Step 4 M3 | Step 5+ when a richer "execution context" type lands; the magic string can become a typed `InnerSession` value |
| Trajectory docstring explanations of `'__inner__'` migration | Step 4 M3 | Delete in Step 6 (event sourcing rationalisation) when no test data references the literal |
| `MaintenanceTimer.SenseContext` shape duplicated from old consciousness | Step 4 M7 | Step 5+ when `SenseContext` is replaced by something StateView-derived |

## §9 — New evaluation corpora

OWNER preempt is the new failure mode worth watching. Add to Step 5
evaluation set:

  * Two-OWNER-back-to-back scenario: the cancellation should not leak a
    half-streamed reply into the second turn's context.
  * Long inner-tick during conversation: scheduler should not fire the
    tick while in_conversation is active (covered by unit test, but
    real-LLM verification belongs in 2g+).
  * Schema-migration boot: a database containing both legacy
    `'__inner__'` rows AND new `NULL` rows should serve queries
    consistently via `relevant_to_chat(include_inner=True)`.

## §10 — Test-counting protocol

Counts in §4 come from `python -m pytest tests/ -x -q`'s tail line.
The test discovery uses `pytest.ini`'s `asyncio_mode=auto`; counts
include both sync and async tests. Skipped/xfail tests are excluded
from the totals because Step 4 didn't introduce any.
