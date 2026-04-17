# Step 1 Cleanup Report — StateMutationLog + Dead Code Excision

**Branch**: `refactor/step1-mutation-log`
**Baseline tag**: `pre_recast_v2_step1` → `3c1ee40`
**Date range**: 2026-04-17 → 2026-04-18
**Prior event count** (`events_v2.db` before archival): **534** events
**Final test count**: **920** pass (+17 new Step-1 tests, -4 EventLogger-specific; vs. pre-branch 907)

Blueprint v2.0 Step 1 executed under **Option C′** (refined per Kevin's
2026-04-18 decision):

> *Keep Dispatcher as in-memory pub/sub for desktop SSE. Delete EventLogger
> and events_v2.db. StateMutationLog becomes the durable log for state
> mutations (LLM / tool / iteration / system lifecycle).*

---

## 1. Commit Sequence

| Commit  | Step | Summary                                                          |
|---------|------|------------------------------------------------------------------|
| 65ad71d | —    | Commit `tests/baseline_v2/` Step 0 results                       |
| 3fe3c9d | 1a   | Introduce `StateMutationLog` + MutationType enum + unit tests    |
| b396844 | 1b   | Instrument every LLM call in LLMRouter via `_tracked_call`        |
| 5707013 | 1c   | Record TOOL_CALLED/TOOL_RESULT in TaskRuntime (alongside dispatcher) |
| 20ee5a6 | 1d   | Wrap complete_chat with ITERATION_STARTED/ENDED                   |
| 038ec0e | 1e   | Wire StateMutationLog into AppContainer; SYSTEM_STARTED/STOPPED   |
| 90af4d5 | 1f+1g | Delete EventLogger, strip SQLite from Dispatcher, archive events_v2.db |
| 66cfe71 | 1h   | Excise quality_checker / progress_reporter / task_resumption residue |
| c9a73bc | 1i   | Strict chat_tools whitelist; remove get_weather + image_search    |
| 0f3ebfb | 1j   | Temporary hallucination observation patch                         |
| *TBD*   | 1k   | Integration test + this cleanup report                            |

---

## 2. Deleted Files

| Path                                     | Lines | Reason                          |
|------------------------------------------|-------|---------------------------------|
| `src/core/event_logger_v2.py`            | 178   | EventLogger class removed (§4.1)|
| `tests/core/test_event_logger_v2.py`     | ~50   | Tests for the above             |

Inline deletions (within files that remain):

| Location                                        | Description                                |
|-------------------------------------------------|--------------------------------------------|
| `src/core/brain.py` (≈50 lines)                 | `_prepare_think_for_resumption` method     |
| `src/core/brain.py` (≈15 lines)                 | `is_resumption` branch, `resumption_context` threading |
| `src/core/prompt_builder.py` (≈60 lines)        | 3 dead builder functions (`build_progress_prompt`, `build_completion_check_prompt`, `build_resumption_prompt`) |
| `src/core/task_types.py` (2 fields)             | `resumption_context`, `progress_state` on ToolLoopContext |
| `src/core/task_runtime.py` (≈12 lines)          | `resumption_context` param, `progress_state=None` |
| `src/core/authority_gate.py` (2 entries)        | Auth entries for removed `get_weather`/`image_search` |
| `config/.env.example` (3 lines)                 | `QUALITY_CHECK_ENABLED`/`PROGRESS_REPORT_ENABLED`/`TASK_RESUMPTION_ENABLED` |
| `config/.env.test` (3 lines)                    | Same three flags (active=false)            |

---

## 3. Data Archival

- `data/events_v2.db` (430 KB, **534 events** — frozen at pre-Step-1 count)
  moved to `~/lapwing-backups/pre_step1_20260417_234006/archived/events_v2.db`.
  Current `ls data/events_v2.db*` shows the file is **gone from `data/`**.
- Baseline `/tmp/pre_step1_event_count.txt` reads `534`; reopening the archived
  DB shows the same 534 rows. The DB is no longer written to — no code path
  references it.
- Full `data/` snapshot (minus `data/browser/profile/` which contains root-owned
  blob_storage) → `~/lapwing-backups/pre_step1_20260417_234006/data/`
- `git_commit.txt`, `git_branch.txt`, `manifest.txt` in the backup root for
  rollback reference.

---

## 4. Grep Verifications

All active source (`src/`, `tests/`, `main.py`, `config/`), `__pycache__`
excluded. Counts are authoritative (backup path excluded).

| Pattern                                                                | Hits | Notes                                                                 |
|------------------------------------------------------------------------|------|-----------------------------------------------------------------------|
| `class EventLogger` \| `from src.core.event_logger_v2 import` \| `EventLogger(` | **0** | §4.1 satisfied                                                        |
| `events_v2\.db` (active code)                                          | 5    | All inside transitional doc comments explaining the removal — not live references |
| `quality_checker` \| `QualityChecker` \| `ReplyQualityChecker`         | **0** | §4.3 satisfied                                                        |
| `progress_reporter` \| `ProgressReporter` \| `progress_state`          | **0** | §4.3 satisfied                                                        |
| `task_resumption` \| `TaskResumption` \| `resumption_context`          | **0** | §4.3 satisfied                                                        |
| `pending_task` \| `PendingTaskStore`                                   | **0** | §4.3 satisfied                                                        |
| `"get_weather"` in whitelist                                           | 1    | Only a **negative** assertion in `tests/core/test_task_runtime.py` that it is NOT present — desired |
| `"image_search"` in whitelist                                          | 1    | Same negative assertion pattern — desired                              |

`client.messages.create` / `client.chat.completions.create` / `_collect_codex_stream(client` in `src/`: all 12 call sites live inside `lambda:` closures passed to `LLMRouter._tracked_call`. No bypass paths.

---

## 5. Scope Reconciliation (vs. original plan)

The plan described paths that didn't exist. These were reconciled with Kevin
before execution; see `Blueprint v2.0 Step 1 refinement` conversation.

| Original plan claim                                      | Reality                                                                              |
|----------------------------------------------------------|--------------------------------------------------------------------------------------|
| Replace `src/logging/event_logger.py`                    | Actual path was `src/core/event_logger_v2.py`. Old `src/logging/` did not exist.    |
| Option C′ executed                                       | Dispatcher survives as in-memory pub/sub; only EventLogger + SQLite path removed.   |
| `quality_checker` / `progress_reporter` already removed  | Wrong — live references existed (see §2 inline deletions).                           |
| `/api/v2/tasks/{id}/messages` → query mutation_log       | Kevin's directive: leave endpoint code alone, return empty list until Step 6.       |
| Git tag pushed to Gitea                                  | Local-only per Kevin's directive; push deferred to post-merge.                       |

---

## 6. Architectural Changes

### StateMutationLog

- `src/logging/state_mutation_log.py` — new module. Append-only SQLite log
  at `data/mutation_log.db` + daily JSONL mirror at `data/logs/mutations_YYYY-MM-DD.log`.
- Strict `MutationType` enum (16 members). `record()` raises `TypeError` on
  non-enum event types.
- Context vars (`iteration_id`, `chat_id`, `last_llm_request_id`) propagate
  correlation ids implicitly through async calls — no method-signature churn
  for downstream callers.

### Instrumented call sites (Step 1)

| Event type                                | Call site                                                   |
|-------------------------------------------|-------------------------------------------------------------|
| `LLM_REQUEST` / `LLM_RESPONSE`            | `LLMRouter._tracked_call` — wraps all 12 API/stream calls (anthropic + openai + codex_oauth) |
| `TOOL_CALLED` / `TOOL_RESULT`             | `TaskRuntime._execute_tool_call`                            |
| `ITERATION_STARTED` / `ITERATION_ENDED`   | `TaskRuntime.complete_chat` (body extracted to `_complete_chat_body`) |
| `SYSTEM_STARTED` / `SYSTEM_STOPPED`       | `AppContainer.start` / `AppContainer.shutdown`              |
| `LLM_HALLUCINATION_SUSPECTED`             | `hallucination_patch.check_and_record` (temporary, see §7)  |

### Dispatcher (simplified)

- `src/core/dispatcher.py` rewritten: no SQLite persistence, no EventLogger
  dependency. Pure in-memory pub/sub. `Event` dataclass relocated into
  dispatcher.py itself.
- `src/api/routes/events_v2.py` dropped the `Last-Event-ID` replay branch
  (no longer possible without a persistent source).
- `src/api/routes/system_v2.py` `/events` endpoint now reads from
  mutation_log.db. Response shape preserved: `event_id` is a stringified
  autoincrement id, `actor` is the constant `"system"`, `task_id` is
  extracted from payload if present.
- `src/api/routes/tasks_v2.py` `/{task_id}/messages` endpoint returns an
  empty list; Step 6 will rewire to StateMutationLog-derived agent events.

### chat_tools strictness

- `src/tools/registry.py` — `ToolNotRegisteredError` raised when a
  `tool_names` whitelist references a non-registered tool.
- `src/core/task_runtime.chat_tools` — `get_weather` + `image_search`
  removed (never implemented).

---

## 7. Debt Registry — Required Follow-ups

All items below are **intentionally** left in place by Step 1 and must be
addressed by the listed future Step. Each has a grep-greppable marker.

### 7.1 `message.received` / `message.sent` dispatcher events
- **Where**: `src/core/brain.py` (4 `dispatcher.submit("message.*")` sites).
- **Status**: carried through Dispatcher for SSE live stream; not persisted.
- **Why it's still there**: desktop-v2 `useSSEv2.ts` subscribes to them.
- **Clean in Step 2**: emit as derived SSE from `TRAJECTORY_APPENDED` events
  in mutation_log, then delete these `dispatcher.submit` calls.

### 7.2 `system.heartbeat_tick` / `reminder.fired` dispatcher events
- **Where**: `src/core/consciousness.py` (tick), `src/core/durable_scheduler.py` (reminder fired).
- **Status**: Dispatcher broadcasts only; no mutation_log record.
- **Clean in Step 4**: migrate to ITERATION_*-derived SSE once the main
  loop is formalised.

### 7.3 `agent.task_*` dispatcher events + `/api/v2/tasks/{id}/messages`
- **Where**: `src/tools/agent_tools.py` (4 `dispatcher.submit("agent.*")` sites);
  `src/api/routes/tasks_v2.py` (`get_task_messages` returns empty list).
- **Status**: dispatched only when `AGENT_TEAM_ENABLED=true`; endpoint is a stub.
- **Clean in Step 6**: rewire with the Agent Team refactor so agent
  messages come from StateMutationLog-derived events.

### 7.4 Temporary hallucination patch
- **Where**:
  - `src/logging/hallucination_patch.py` (whole module, 125 lines).
  - `src/core/task_runtime.py` — single `_check_hallucination(reply, mutation_log)` call and its import at the top.
  - `src/logging/state_mutation_log.py` — `MutationType.LLM_HALLUCINATION_SUSPECTED` enum member.
  - `tests/logging/test_hallucination_patch.py` — whole file.
- **Status**: observation-only; does NOT intercept user-visible text.
- **Clean in Step 5**: delete everything above once the trajectory +
  commitment pipeline eliminates the structural hallucination vector.

### 7.5 `events_v2.db` mentioned in transitional doc comments
- **Where**: 5 doc-string mentions in `src/api/routes/system_v2.py`,
  `src/app/container.py`, `src/api/routes/events_v2.py`, and two test
  files. These are explanatory notes about the v2.0 Step 1 change.
- **Status**: harmless — no runtime code references.
- **Clean in Step 2 or later**: once the transition is ancient history,
  the notes can be dropped.

---

## 8. Test Results

- **Total**: 920 pass (2 pre-existing asyncio-marker warnings).
- **New tests added**: 36 (+1 integration test).
  - `tests/logging/test_state_mutation_log.py` — 17 tests (CRUD, enum discipline, large-payload no-truncation, iteration correlation, concurrent writes, context vars, uninitialised-state safety).
  - `tests/logging/test_llm_router_tracking.py` — 5 tests (Anthropic success, OpenAI success, exception-path recording, no-mutation-log passthrough, codex tuple mapping).
  - `tests/logging/test_task_runtime_tracking.py` — 2 tests (TOOL_CALLED/RESULT recorded; missing log is a no-op).
  - `tests/logging/test_iteration_boundaries.py` — 2 tests (success pair; error pair with `end_reason=error`).
  - `tests/logging/test_system_lifecycle.py` — 1 test (container start/shutdown writes SYSTEM_STARTED/STOPPED).
  - `tests/logging/test_hallucination_patch.py` — 9 tests (strict phrases, tool-call suppression, soft-phrase disambiguation, missing-context safety).
  - `tests/tools/test_registry.py` — 3 tests (raise on unknown, raise via function_tools, positive control).
  - `tests/integration/test_step1_observability.py` — 1 end-to-end test (full chain with parent_llm_request_id correlation).
- **Deleted tests**: 4 (`tests/core/test_event_logger_v2.py`).

---

## 9. Desktop Compatibility Verification

Every endpoint the desktop-v2 frontend consumes, plus what it looked for:

| Endpoint / stream                     | Desktop usage                                | Step 1 behaviour                          | Status        |
|---------------------------------------|----------------------------------------------|-------------------------------------------|---------------|
| `GET /api/v2/events` (SSE)            | `useSSEv2.ts` live event stream              | Still streams via Dispatcher. **Removed**: `Last-Event-ID` replay (no persistent source). Live events unchanged. | ⚠️ Minor regression: missed events during disconnect can no longer be replayed. Debt §7.1. |
| `GET /api/v2/system/info`             | `getSystemInfo()` in `lib/api-v2.ts`         | Untouched. Same response shape.           | ✅ No change  |
| `GET /api/v2/system/events`           | `getSystemEvents()` in `lib/api-v2.ts`       | Now reads `mutation_log.db`. Field shape preserved: `event_id` (stringified int), `event_type` (MutationType value), `timestamp` (ISO UTC), `actor` (constant `"system"`), `task_id` (extracted from payload, may be None), `payload`. | ✅ Shape compatible. Actor is now constant; old distinctions ("lapwing" vs "team_lead") are gone.  |
| `GET /api/v2/tasks` / `/{id}`         | `getTasks()` / `getTask()` in `lib/api-v2.ts` | Untouched. Reads `task_view_store` as before. | ✅ No change |
| `GET /api/v2/tasks/{id}/messages`     | `getTaskMessages()` in `lib/api-v2.ts`       | **Returns empty list**. Agent history lookup deferred to Step 6. | ⚠️ Visible regression: agent messages panel empty until Step 6. Debt §7.3. |
| Desktop `agent.task_*` SSE dispatch   | `useSSEv2.ts` type-switch                    | Events still emitted by `agent_tools.py` via Dispatcher when `AGENT_TEAM_ENABLED=true`. | ✅ No change (agent team off in practice) |
| Desktop `message.*` / `heartbeat_tick`/`reminder.fired` SSE | various UI hooks     | Events still emitted via Dispatcher. Not durable. | ✅ No change |

Desktop compile will not break; behavioural changes are:
1. On SSE reconnect, any events that fired during the disconnect are lost (no replay). Debt §7.1.
2. The "task messages" panel is empty until Step 6. Debt §7.3.

No other desktop calls were touched.

---

## 10. Outstanding Questions for Kevin

None as of this writing. Execution followed the decisions in
Kevin's 2026-04-18 directive verbatim.
