# Step 4 M3.d — ConsciousnessEngine Migration Audit

This memo enumerates every behavioural feature of
`src/core/consciousness.py` and records, for each, where it lives after
M3 and why. Read this alongside `step4_decisions.md` (the broader Step 4
judgement-call log).

The spec was explicit: **do not port the file as-is, audit each feature.**

| # | Feature | Status | Lands at | Rationale |
|---|---------|--------|----------|-----------|
| 1 | Periodic tick at `CONSCIOUSNESS_DEFAULT_INTERVAL` | KEPT | `InnerTickScheduler._run` | Core "wake every N seconds" loop. |
| 2 | Backoff on idle (×1.5, capped at MAX_INTERVAL) | KEPT | `InnerTickScheduler.note_tick_result` (idle branch) | Avoids burning tokens during long quiet periods. |
| 3 | LLM-driven `[NEXT: Xm]` override | KEPT | `parse_next_interval` + `note_tick_result(llm_next_interval=...)` | Lapwing tells us when to wake — preserves agency. |
| 4 | Silence-based interval (post-2h, post-30min) | KEPT | `InnerTickScheduler._silence_based_interval` | Matches user's circadian rhythm; adjusts when offline long. |
| 5 | TickBudget (max_time_seconds=120) | KEPT | `brain.think_inner(timeout_seconds=120)` | Prevents stuck tick from blocking everything. |
| 6 | Pause during conversation (`_in_conversation` flag) | KEPT (different mechanism) | `InnerTickScheduler.note_conversation_start/end` + `EventQueue.has_owner_message` self-yield in handler | Two layers: scheduler doesn't fire while conversing; handler also yields if OWNER message arrives between fire and dispatch. |
| 7 | Urgency queue (DurableScheduler reminder fires) | KEPT | `InnerTickScheduler.push_urgency` + `drain_urgency`; container wires `durable_scheduler.urgency_callback → push_urgency` | DurableScheduler still uses the urgency-callback API; only the destination changed. |
| 8 | `_interrupt_flag` (`asyncio.Event`) | DROPPED | — | Step 4 M4's `MainLoop._interrupt_current` cancels the in-flight task directly via `asyncio.Task.cancel()`; no separate signalling primitive needed. |
| 9 | Working-memory file (`data/consciousness/working_memory.md`) read | KEPT | `build_inner_prompt` reads it inline | Lapwing's continuity across ticks; she writes it through the standard `write_file` tool. |
| 10 | Activity log (`data/consciousness/activity_log.md`) write | DROPPED | — | Trajectory `INNER_THOUGHT` rows already capture every reply; the activity log was a redundant secondary record. The trajectory is now the single source of truth. |
| 11 | `_save_interrupted_state()` (writes "被中断" line on cancel) | DROPPED | — | M4 will write a structured trajectory entry with `entry_type = "interrupted"` (and partial-content payload) on OWNER preemption. The free-text "被中断" note was prose; structured data wins. |
| 12 | `_NEXT_PATTERN` regex parser | KEPT | `parse_next_interval` in `inner_tick_scheduler.py` | Single shared parser. |
| 13 | Hourly maintenance — `SessionReaperAction`, `TaskNotificationAction`, `AutonomousBrowsingAction` | TEMPORARILY KEPT | `ConsciousnessEngine._run_hourly_maintenance` (still alive, with `thinking_disabled=True`) | These actions do non-tick work (DB cleanup, notifications, browsing). M3 keeps them running by leaving `ConsciousnessEngine` alive in **maintenance-only mode**. M7 deletes the engine entirely; before M7 lands, maintenance moves to a dedicated `MaintenanceTimer` (or merges into the scheduler). Logged as carryover D-7. |
| 14 | Daily maintenance — `MemoryConsolidationAction`, `MemoryMaintenanceAction`, `CompactionCheckAction`, `SelfReflectionAction` | TEMPORARILY KEPT | Same as #13 | Same rationale. |
| 15 | Dispatcher emit `system.heartbeat_tick` | DROPPED for inner ticks | — | Step 4 M5 will subscribe SSE to StateMutationLog instead of dispatcher events. The heartbeat-tick event was only consumed by SSE/observability. |
| 16 | `chat_id = "__inner__"` literal | DROPPED | — | `brain.think_inner` uses internal session key `_inner_tick`; trajectory writes `source_chat_id = NULL`. The string survives in conversation.py's backward-compat branch (legacy data and tests) and trajectory_store docstrings (explanatory). |
| 17 | `brain.think("__inner__", ...)` call | DROPPED | `brain.think_inner()` | The new entry point doesn't accept a chat_id argument. |
| 18 | `on_conversation_start/end` (engine-level) | KEPT (engine still exposes) + REPLACED (scheduler is the new authority) | `InnerTickScheduler.note_conversation_start/end` | brain.think_conversational signals both during the M3→M7 transition window. |
| 19 | `push_urgency` public API on engine | KEPT (engine still exposes for legacy callers) + REPLACED | `InnerTickScheduler.push_urgency` | container rewires `DurableScheduler.urgency_callback` to the new scheduler. |

## What the file looks like at end of M3

`consciousness.py` is now a **maintenance-only** subsystem:
- Constructed with `thinking_disabled=True` from `AppContainer`
- `_loop` still runs but skips `_think_freely`; only `_run_maintenance_if_due` and `_drain_urgency → push to inner_tick_scheduler` execute
- Will be deleted entirely in M7 once maintenance lands in its new home

Tests that construct `ConsciousnessEngine` standalone (without
`thinking_disabled=True`) still exercise the legacy `_think_freely`
path, so the existing 27 consciousness tests continue to pass.

## Migration tracker

- [x] Tick scheduling → InnerTickScheduler
- [x] Urgency queue → InnerTickScheduler
- [x] Conversation pause/resume → InnerTickScheduler
- [x] Inner prompt builder → `inner_tick_scheduler.build_inner_prompt`
- [x] [NEXT: Xm] parser → `inner_tick_scheduler.parse_next_interval`
- [x] brain.think_inner with `is_inner=True` trajectory writes → done
- [x] DurableScheduler urgency wired to InnerTickScheduler → done
- [x] consciousness.py thinking disabled in production → done
- [ ] **Carryover (D-7)**: hourly + daily maintenance moved out of consciousness.py — required before M7 deletes the file
- [ ] **Carryover (D-8)**: legacy `chat_id == "__inner__"` branch in `conversation._mirror_to_trajectory` — remove with M7 once consciousness.py deletion lands

These open carryovers are tracked in `step4_decisions.md` D-7 and D-8.
