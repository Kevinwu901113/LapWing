# Lapwing Health Check Report

> **STATUS — read this first**: this report is a historical snapshot from 2026-04-14.
> Several items have since been addressed by Blueprint v2.0 Step 1 (see
> `cleanup_report_step1.md` at the repo root after Step 1 merges). In particular,
> `quality_checker`, `progress_reporter`, and `task_resumption` have been removed;
> `EventLogger` / `events_v2.db` are replaced by `StateMutationLog` /
> `data/mutation_log.db`. Original content retained below for the audit trail.

**Inspection time**: 2026-04-14
**Code version**: `ca4903c` (branch `feature/tool-loop-reform`)
**Inspection scope**: `src/` (143 files, 31,552 lines), `tests/` (107 files, 20,284 lines)

---

## Executive Summary

- :red_circle: Critical: **4**
- :yellow_circle: Medium: **15**
- :green_circle: Suggested improvements: **17**
- :information_source: Info only: **8**
- Test pass rate: **1274/1274 (100%)**
- Syntax errors: **0**
- Import chain: **OK**
- Merge conflict markers: **0**

---

## :red_circle: Must Fix Immediately

### R-01. Live API keys in plaintext in `data/config/model_routing.json`

- **File**: `data/config/model_routing.json:8,33`
- **Problem**: MiniMax API key (`sk-cp-DG3QI_...`) and NVIDIA NIM API key (`nvapi-QnEN0...`) are stored in cleartext. This file is under `data/config/` which is NOT in `.gitignore`. A `git push` would expose both keys.
- **Impact**: Credential leak if repository is ever pushed to a remote.
- **Fix**: Add `data/config/model_routing.json` to `.gitignore`, rotate both keys, and move secrets to `config/.env` or the credential vault.
- **Estimated change**: 3 lines (.gitignore + vault migration)

### R-02. `SCHEDULED_TASKS_PATH` does not exist in `config/settings.py` — runtime ImportError

- **File**: `src/api/routes/system.py:307,316`
- **Problem**: The file imports `SCHEDULED_TASKS_PATH` from `config.settings`, but this symbol was never defined in `settings.py`. Any request to `GET /api/scheduled-tasks` or `DELETE /api/scheduled-tasks/{id}` raises `ImportError` at runtime.
- **Impact**: Two API endpoints are completely broken.
- **Fix**: Add `SCHEDULED_TASKS_PATH: Path = DATA_DIR / "scheduled_tasks.json"` to `config/settings.py`.
- **Estimated change**: 1 line in `settings.py`

### R-03. Path traversal in knowledge API endpoints

- **File**: `src/api/routes/system.py:450-458` (DELETE), `src/api/routes/data.py:191-198` (PUT)
- **Problem**: The `{topic}` path parameter is appended directly to `knowledge_dir` with no containment check. A request like `DELETE /api/knowledge/notes/../../../../data/memory/KEVIN` resolves outside the knowledge directory and can delete or overwrite arbitrary `.md` files, including `prompts/`, `data/identity/`, and `data/memory/` — the very files VitalGuard protects on the shell layer but not at the API layer.
- **Impact**: Authenticated user can delete/overwrite any `.md` file accessible to the process.
- **Fix**: Add `if not path.resolve().is_relative_to(knowledge_dir.resolve()): return 403` before file operations.
- **Estimated change**: 4 lines (2 per endpoint)

### R-04. Auth bypass race condition on first boot

- **File**: `src/api/routes/auth.py:65-87`
- **Problem**: `POST /api/auth/desktop-token` only validates the bootstrap token **if** the token file exists. On a fresh install before `AuthManager` creates the file, any caller can create a persistent desktop token with no authentication.
- **Impact**: Unauthenticated access to all OWNER-level operations during a race window at first startup.
- **Fix**: If the token file doesn't exist, reject the request (return 503 "not ready") instead of skipping validation.
- **Estimated change**: 3 lines

---

## :yellow_circle: Should Fix

### Y-01. Tools described in system prompt but unreachable by LLM

- **File**: `src/tools/registry.py:675`, `src/core/task_runtime.py:303-325`, `src/core/prompt_builder.py:252,432`, `prompts/lapwing_capabilities.md:50,56`
- **Problem**: Four tools are registered and described to the LLM in system prompts but never included in `chat_tools()`'s whitelist:
  - `delegate_task` — also uses `capability="delegation"` which no `RuntimeProfile` includes
  - `image_search` — described in `lapwing_capabilities.md` line 50
  - `report_incident` — described in `lapwing_capabilities.md` line 56
  - `trace_mark` — described in `prompt_builder.py:432`
- **Impact**: The LLM is instructed to use tools it can never see. This causes confusion in the model's reasoning and wasted prompt tokens. `delegate_task` is entirely dead code at runtime.
- **Fix**: Add these tools to `chat_tools()` or remove their descriptions from system prompts.
- **Estimated change**: 8 lines in `task_runtime.py` + remove/update prompt references

### Y-02. Heredoc injection in `write_file_tool`

- **File**: `src/tools/handlers.py:140`
- **Problem**: The shell command `cat > {path} << 'LAPWING_EOF'\n{content}\nLAPWING_EOF` does not escape `content`. If `content` contains the literal string `LAPWING_EOF` on its own line, the heredoc terminates early and the remainder executes as shell code.
- **Impact**: Prompt injection could achieve arbitrary shell execution via this tool (OWNER-only, but the LLM itself could be tricked).
- **Fix**: Use `file_editor.py` path exclusively, or write content via Python `Path.write_text()` instead of shell heredoc.
- **Estimated change**: 5 lines

### Y-03. All API endpoints bypass auth when `auth_manager is None`

- **File**: `src/api/server.py:99-101`
- **Problem**: The auth middleware checks `if auth_manager is None: return await call_next(request)`. If `auth_manager` is somehow absent, every endpoint is open without authentication.
- **Impact**: Complete auth bypass if initialization is incomplete or `auth_manager` is unset.
- **Fix**: Return 503 when `auth_manager is None` instead of passing through.
- **Estimated change**: 2 lines

### Y-04. Silent VitalGuard backup failure

- **File**: `src/core/vital_guard.py:428,437,461,482,490`
- **Problem**: `auto_backup()` catches all exceptions with `pass`. When a backup fails before a VERIFY_FIRST write, the write proceeds with no backup and no notification.
- **Impact**: Data loss on write errors with no recovery path.
- **Fix**: Log at WARNING and optionally block the write if backup fails.
- **Estimated change**: 5 lines

### Y-05. `BROWSE_ENABLED` silently dead when `CONSCIOUSNESS_ENABLED=true`

- **File**: `src/app/container.py:143-145`, `src/app/container.py:426`
- **Problem**: `AutonomousBrowsingAction` (and `AutoMemoryAction`) are only registered in `_build_heartbeat()`, which runs only when `CONSCIOUSNESS_ENABLED=false`. Both `CONSCIOUSNESS_ENABLED` (default true) and `BROWSE_ENABLED` (default true) are on, but autonomous browsing silently does nothing.
- **Impact**: Feature flag appears enabled but has no effect. Operators may believe autonomous browsing is running when it isn't.
- **Fix**: Register browsing/memory actions in the consciousness path too, or document the mutual exclusion.
- **Estimated change**: 10 lines

### Y-06. `on_interim_text` type annotation mismatch

- **File**: `src/core/task_runtime.py:369` (type annotation), `src/core/progress_reporter.py:164` (caller), `src/core/brain.py:922` (implementation)
- **Problem**: `task_runtime.py` types the callback as `Callable[[str], Awaitable[None]]` (one-arg), but `progress_reporter.py` calls it with `bypass_monologue_filter=True`. Works only because the sole concrete implementation in `brain.py` accepts the kwarg. Any future implementation matching the declared type will crash.
- **Fix**: Change type to `Callable[..., Awaitable[None]]` or use a `Protocol`.
- **Estimated change**: 1 line

### Y-07. 5x duplicated JSON code-fence stripping

- **Files**: `src/memory/fact_extractor.py:176`, `src/memory/interest_tracker.py:99`, `src/memory/auto_extractor.py:138`, `src/core/quality_checker.py:128`, `src/core/llm_protocols.py:122`
- **Problem**: Five independent implementations of "strip \`\`\`json fence, then parse JSON". Two use regex (robust), two use `startswith`/`endswith` (fragile with leading whitespace), one returns `{}` for non-dict. The `auto_extractor` and `quality_checker` versions will fail on content with leading space before the fence marker.
- **Fix**: Extract shared `strip_code_fence(text) -> str` utility.
- **Estimated change**: 20 lines (new util) + 10 lines (5 call sites)

### Y-08. `complete_chat` is a 742-line monster method

- **File**: `src/core/task_runtime.py:356-1097`
- **Problem**: One method contains: 4 nested function definitions, tool loop orchestration, loop detection, progress reporting, task completion, message compaction, and event publishing — 6 distinct concerns.
- **Impact**: Extremely difficult to test, maintain, or debug. Any change risks regressions across unrelated concerns.
- **Fix**: Extract nested functions to class methods; split into `_run_tool_loop()`, `_handle_compaction()`, `_finalize_response()`.
- **Estimated change**: 200+ lines of refactoring (no behavior change)

### Y-09. `QUALITY_CHECK_ENABLED` reads wrong env var name

- **File**: `config/settings.py:147`
- **Problem**: Python variable is `QUALITY_CHECK_ENABLED` but reads env var `LAPWING_FLAG_QUALITY_CHECK`. Setting `QUALITY_CHECK_ENABLED=false` in `.env` has no effect. Neither name appears in `.env.example`.
- **Fix**: Rename env var to `QUALITY_CHECK_ENABLED` to match all other flags.
- **Estimated change**: 1 line

### Y-10. Conversations channel default mismatch (schema drift)

- **File**: `src/memory/conversation.py:166` vs actual DB
- **Problem**: Code says `DEFAULT 'qq'` but the live DB column has `DEFAULT 'telegram'` (migration ran when code had different default). On a fresh DB, records without explicit channel default to `'qq'`; on the existing DB, they default to `'telegram'`.
- **Impact**: Inconsistent behavior between fresh install and existing deployment.
- **Fix**: Run `ALTER TABLE conversations ALTER COLUMN channel SET DEFAULT 'qq'` on existing DB, or align code to match.
- **Estimated change**: 1 migration line

### Y-11. 13 feature flags undocumented in `.env.example`

- **File**: `config/.env.example`
- **Problem**: `CONSCIOUSNESS_ENABLED`, `AGENT_TEAM_ENABLED`, `MEMORY_CRUD_ENABLED`, `AUTO_MEMORY_EXTRACT_ENABLED`, `MEMORY_GUARD_ENABLED`, `SELF_SCHEDULE_ENABLED`, `QUALITY_CHECK_ENABLED`, `INCIDENT_ENABLED`, `PROGRESS_REPORT_ENABLED`, `TASK_RESUMPTION_ENABLED`, `MESSAGE_SPLIT_ENABLED`, `SESSION_ENABLED`, `EXPERIENCE_SKILLS_ENABLED` are all defined in `settings.py` with defaults but absent from `.env.example`.
- **Fix**: Add entries with comments to `.env.example`.
- **Estimated change**: 15 lines

### Y-12. Heartbeat actions bypass Brain orchestration

- **Files**: `src/heartbeat/actions/proactive.py`, `interest_proactive.py`, `autonomous_browsing.py`, `self_reflection.py`, `system_health.py`
- **Problem**: 5+ heartbeat action files call `brain.router.complete(...)` directly, bypassing Brain's system prompt, memory injection, and tool loop. Two files self-document this as `# TODO: 架构违反`. Some also directly import tool modules (`web_fetcher`, `web_search`) instead of going through the tool registry.
- **Impact**: Heartbeat replies lack personality anchoring, memory context, and safety guards.
- **Fix**: Route through `brain.think()` or create a dedicated `brain.heartbeat_reply()` method.
- **Estimated change**: 30 lines per action

### Y-13. `task_types.py` (types module) imports from `prompt_builder`

- **File**: `src/core/task_types.py:175`
- **Problem**: A types/data module lazy-imports `src.core.prompt_builder.inject_voice_reminder`. Types should have zero upward dependencies. Similarly, `progress_reporter.py:140` lazy-imports `build_progress_prompt`.
- **Impact**: Layering violation that makes dependency analysis unreliable and could lead to circular imports as the codebase grows.
- **Fix**: Move the voice reminder injection to the caller (Brain) or pass it as a callback.
- **Estimated change**: 10 lines

### Y-14. Independent httpx client construction across 5 files

- **Files**: `src/tools/web_fetcher.py:100`, `src/tools/web_search.py:277`, `src/tools/weather.py:31`, `src/adapters/qq_adapter.py:422`, `src/core/codex_oauth_client.py:242`
- **Problem**: Each constructs `httpx.AsyncClient` independently with bespoke timeouts and proxy settings. Four share the same proxy env var (`SEARCH_PROXY_URL`) and similar timeouts (10-15s) but none share code.
- **Fix**: Create shared `_make_outbound_client(timeout, proxy_key)` factory.
- **Estimated change**: 15 lines (factory) + 5 lines per call site

### Y-15. WebSocket grants full OWNER access with no credentials

- **File**: `src/api/routes/chat_ws.py:67-69`
- **Problem**: When `DESKTOP_DEFAULT_OWNER=true` (default), no token is required. Any localhost process can connect and issue shell commands. HTTP auth middleware does not cover WebSocket upgrades.
- **Impact**: Acceptable for localhost-only binding (`127.0.0.1`), but if `API_HOST` is changed to `0.0.0.0`, this becomes a critical remote code execution vector.
- **Fix**: Add a startup warning if `API_HOST != 127.0.0.1 && DESKTOP_DEFAULT_OWNER=true`.
- **Estimated change**: 5 lines

---

## :green_circle: Suggested Improvements

### G-01. `transcriber.py` is orphaned from production code

- **File**: `src/tools/transcriber.py`
- **Problem**: `transcribe()` has zero references in `src/`. Not registered as a tool, not called by any adapter. Only imported in test files. Likely a legacy voice transcription module from the removed Telegram adapter.
- **Fix**: Delete or wire into tool registry.
- **Estimated change**: 0 (delete) or 15 (register)

### G-02. `images/asian1.jpg` is a fake image (404 HTML)

- **File**: `images/asian1.jpg`
- **Problem**: 29-byte file containing `<html><body>404</body></html>`, not an actual image.
- **Fix**: Delete.
- **Estimated change**: 0

### G-03. 59 src modules have no corresponding test file

- **Key untested modules**: `event_logger.py`, `vitals.py`, `prompt_builder.py`, `session_manager.py`, `conversation.py`, `handlers.py`, all `api/routes/`, 6 heartbeat actions
- **Fix**: Prioritize tests for `event_logger.py` (startup/shutdown critical), `conversation.py` (data layer), and API routes (security-sensitive).
- **Estimated change**: 500+ lines of new tests

### G-04. No end-to-end integration tests

- **Problem**: All existing tests mock LLMRouter. The path `Brain.think_conversational -> TaskRuntime.complete_chat -> ToolRegistry.execute -> real tool handler` is never tested end-to-end.
- **Fix**: Add at least one integration test with a scripted LLM response that exercises the full path.
- **Estimated change**: 50 lines

### G-05. 298 broad `except Exception` catches across src/

- **Worst offenders**: VitalGuard backup (5 `pass`), DB migrations (4 `pass`), consciousness loop (3 `continue` without logging)
- **Fix**: Narrow to specific exceptions (e.g., `sqlite3.OperationalError` for migrations, `OSError` for file ops). Add WARNING-level logging where `pass` swallows errors silently.
- **Estimated change**: 50+ lines

### G-06. Time-period computation duplicated 3x

- **Files**: `src/core/prompt_builder.py:37` (7-period), `src/heartbeat/actions/proactive.py:35` (4-period), `src/heartbeat/actions/interest_proactive.py:68` (identical 4-period)
- **Fix**: Export `get_period_name(hour)` from `vitals.py` and reuse everywhere.
- **Estimated change**: 10 lines

### G-07. `_normalize_list` / `_normalize_string_list` duplicated

- **Files**: `src/core/skills.py:466`, `src/core/experience_skills.py:644`
- **Fix**: Extract to shared utility.
- **Estimated change**: 8 lines

### G-08. Frontmatter parsing duplicated

- **Files**: `src/core/skills.py:415`, `src/core/experience_skills.py:621`
- **Fix**: Share frontmatter scanner with different return-shape adapters.
- **Estimated change**: 15 lines

### G-09. `_format_conversation` duplicated 3x with inconsistent handling

- **Files**: `src/memory/fact_extractor.py:160`, `src/memory/auto_extractor.py:114`, `src/memory/compactor.py:36`
- **Problem**: Three formatters with different speaker labels, content-type handling, and truncation. `fact_extractor`'s version will produce garbage on Anthropic multipart content.
- **Fix**: Extract shared `format_messages_for_llm(messages, options)` utility.
- **Estimated change**: 25 lines

### G-10. Taipei timezone constructed inline in 4 files

- **Files**: `src/tools/handlers.py:151`, `src/tools/schedule_task.py:69,97`, `src/core/heartbeat.py:109,207`
- **Problem**: Each constructs `timezone(timedelta(hours=8))` despite `vitals.py` exporting `now_taipei()` and `_TAIPEI_TZ`.
- **Fix**: Import from `vitals.py`.
- **Estimated change**: 8 lines

### G-11. String truncation functions duplicated 4x

- **Files**: `src/logging/event_logger.py:362`, `src/core/prompt_builder.py:303`, `src/core/skills.py:494`, `src/tools/shell_executor.py:118`
- **Fix**: Extract `truncate_text(text, max_len, ellipsis="...")`.
- **Estimated change**: 10 lines

### G-12. Phantom `hasattr(brain, "__dict__")` guard (dead branch)

- **Files**: `src/heartbeat/actions/proactive.py:75`, `interest_proactive.py:105`, `autonomous_browsing.py:107`
- **Problem**: `brain.__dict__.get("event_bus") if hasattr(brain, "__dict__") else None` — `LapwingBrain` always has `__dict__` (no `__slots__`). The `hasattr` check is always `True`.
- **Fix**: Replace with `getattr(brain, "event_bus", None)`.
- **Estimated change**: 3 lines

### G-13. Tool parameter validation inconsistent across handlers

- **Files**: `src/tools/handlers.py:116,136`, `src/tools/memory_crud.py`, `src/tools/schedule_task.py`
- **Problem**: ~15 sites each construct "missing parameter" errors with different payload shapes. Some include `stdout`/`return_code` (mimicking shell), others just `error`.
- **Fix**: Create `_missing_param_result(name) -> ToolExecutionResult` helper.
- **Estimated change**: 20 lines

### G-14. `build_default_tool_registry()` is 748-line monolithic factory

- **File**: `src/tools/registry.py:130`
- **Fix**: Split into per-capability registration functions (e.g., `_register_shell_tools()`, `_register_memory_tools()`).
- **Estimated change**: 50 lines (structural refactor, no behavior change)

### G-15. `qq.py` misplaced in project root

- **File**: `/home/kevin/lapwing/qq.py`
- **Problem**: Utility script for QQ group history export. Not part of the application. Has hardcoded group IDs.
- **Fix**: Move to `scripts/`.
- **Estimated change**: 1 file move

### G-16. `SKILLS_COMMANDS_ENABLED` is dead configuration

- **File**: `config/settings.py:183`
- **Problem**: Defined in `settings.py` and documented in `.env.example` but never referenced in any `src/` file.
- **Fix**: Remove from both files, or wire it up.
- **Estimated change**: 2 lines

### G-17. Shutdown order asymmetry

- **File**: `src/app/container.py:160-203`
- **Problem**: `start()` initializes channels before API server, but `shutdown()` stops channels before API server. If an in-flight WebSocket handler calls `channel_manager.send_*` during `api_server.shutdown()`, it will fail. The symmetric order would be: stop API server first, then channels.
- **Fix**: Swap shutdown order: API server before channel_manager.
- **Estimated change**: 4 lines

---

## :information_source: Info Only

### I-01. 2 unused `timedelta` imports

- `src/memory/conversation.py:5`, `src/auth/storage.py:6`

### I-02. Stale `.pyc` files from deleted Telegram modules

- `src/app/__pycache__/telegram_app.cpython-312.pyc`, `telegram_delivery.cpython-312.pyc`
- Harmless bytecode. Clean up with `find src -name '*.pyc' -delete`.

### I-03. Lazy stdlib imports inside functions (already at module level)

- `src/core/task_runtime.py:1922` — `import asyncio` inside function (already imported at top)
- `src/core/task_runtime.py:2028` — `import re` inside function (already imported at top)

### I-04. `brain` <-> `consciousness_engine` bidirectional runtime reference

- `src/app/container.py:141` — both objects hold references to each other. Not a circular import, but a runtime graph cycle. By design.

### I-05. `secure=False` on session cookie

- `src/api/routes/auth.py:51` — intentional for localhost HTTP. Would be a problem if `API_HOST` changes to non-localhost.

### I-06. Ghost `event_log` table in `data/lapwing.db`

- Created by old code. Current code writes to `data/events.db`. Orphaned table, never written to. Wastes space.

### I-07. `data/credentials/` is empty (no `vault.enc`)

- The credential vault is uninitialized. `browser_login` tool is non-functional until vault is created.

### I-08. `data/vital_manifest.json` contains hashes for deleted Telegram files

- Will self-heal on next boot when `save_manifest()` runs.

---

## Appendix: Statistics

### File Size Top 20 (src/)

| File | Lines |
|------|-------|
| `src/core/task_runtime.py` | 2,059 |
| `src/core/llm_router.py` | 1,225 |
| `src/core/browser_manager.py` | 1,199 |
| `src/core/brain.py` | 1,058 |
| `src/auth/service.py` | 1,007 |
| `src/tools/file_editor.py` | 889 |
| `src/tools/registry.py` | 878 |
| `src/tools/browser_tools.py` | 808 |
| `src/core/experience_skills.py` | 729 |
| `src/core/shell_policy.py` | 658 |
| `src/memory/conversation.py` | 653 |
| `src/tools/handlers.py` | 604 |
| `src/api/routes/system.py` | 571 |
| `src/adapters/qq_adapter.py` | 565 |
| `src/core/vital_guard.py` | 501 |
| `src/core/consciousness.py` | ~480 |
| `src/core/prompt_builder.py` | ~450 |
| `src/logging/event_logger.py` | ~400 |
| `src/core/codex_oauth_client.py` | ~380 |
| `src/memory/auto_extractor.py` | ~350 |

### Methods Over 100 Lines

| Method | File | Lines |
|--------|------|-------|
| `TaskRuntime.complete_chat` | `task_runtime.py:356-1097` | 742 |
| `TaskRuntime.execute_tool` | `task_runtime.py:1218-1425` | 208 |
| `PageState.to_llm_text` | `browser_manager.py:140-334` | 195 |
| `Brain.think_conversational` | `brain.py:823-1000` | 178 |
| `LLMRouter.complete_with_tools` | `llm_router.py:799-972` | 174 |
| `LLMRouter._with_routing_retry` | `llm_router.py:484-640` | 157 |
| `Brain._prepare_think` | `brain.py:573-704` | 133 |
| `LLMRouter.complete` | `llm_router.py:641-770` | 130 |

### Broad `except` Distribution (top 10 files)

| File | Count |
|------|-------|
| `src/core/codex_oauth_client.py` | 7 |
| `src/core/consciousness.py` | 6 |
| `src/core/incident_manager.py` | 6 |
| `src/api/routes/system.py` | 6 |
| `src/memory/conversation.py` | 6 |
| `src/tools/self_status.py` | 6 |
| `src/core/vital_guard.py` | 5 |
| `src/api/routes/chat_ws.py` | 5 |
| `src/core/prompt_builder.py` | 4 |
| `src/app/container.py` | 3 |
| **Total across src/** | **298** |

### Code Duplication Hotspots

| Pattern | Instances | Severity |
|---------|-----------|----------|
| JSON code-fence strip + parse | 5 | High |
| `_format_conversation` message formatter | 3 | Medium |
| `httpx.AsyncClient` independent construction | 5 | Medium |
| Time-period name computation | 3 | Low |
| `timezone(timedelta(hours=8))` inline | 5 | Low |
| String truncation function | 4 | Low |
| `_normalize_list` for skill metadata | 2 | Low |
| Frontmatter `---` scanning | 2 | Low |

### Feature Flags (26 total)

| Flag | Default | In .env.example | Status |
|------|---------|-----------------|--------|
| `QQ_ENABLED` | false | Yes | OK |
| `HEARTBEAT_ENABLED` | true | Yes | OK |
| `CONSCIOUSNESS_ENABLED` | true | **No** | Undocumented |
| `BROWSE_ENABLED` | true | Yes | Silently dead (Y-05) |
| `BROWSER_ENABLED` | true | Yes (overridden false) | Default mismatch |
| `BROWSER_VISION_ENABLED` | true | Yes | OK |
| `MINIMAX_VLM_ENABLED` | false | Yes | OK |
| `MEMORY_CRUD_ENABLED` | true | **No** | Undocumented |
| `AUTO_MEMORY_EXTRACT_ENABLED` | true | **No** | Undocumented |
| `MEMORY_GUARD_ENABLED` | true | **No** | Undocumented |
| `DELEGATION_ENABLED` | true | Yes | Tool unreachable (Y-01) |
| `AGENT_TEAM_ENABLED` | true | **No** | Undocumented |
| `SELF_SCHEDULE_ENABLED` | true | **No** | Undocumented |
| `QUALITY_CHECK_ENABLED` | true | **No** | Wrong env var name (Y-09) |
| `INCIDENT_ENABLED` | true | **No** | Undocumented |
| `PROGRESS_REPORT_ENABLED` | true | **No** | Undocumented |
| `TASK_RESUMPTION_ENABLED` | true | **No** | Undocumented |
| `MESSAGE_SPLIT_ENABLED` | true | **No** | Undocumented |
| `SESSION_ENABLED` | true | **No** | Undocumented |
| `SHELL_ENABLED` | true | Yes | OK |
| `SKILLS_ENABLED` | true | Yes | OK |
| `SKILLS_COMMANDS_ENABLED` | true | Yes | Dead flag (G-16) |
| `EXPERIENCE_SKILLS_ENABLED` | true | **No** | Undocumented |
| `LOOP_DETECTION_ENABLED` | true | Yes | OK |
| `CHAT_WEB_TOOLS_ENABLED` | true | Yes | OK |
| `DESKTOP_DEFAULT_OWNER` | true | **No** | Security-sensitive (Y-15) |

### Test Coverage Blind Spots

| Module | Lines | Test Exists |
|--------|-------|-------------|
| `src/logging/event_logger.py` | ~400 | No |
| `src/core/vitals.py` | ~200 | No |
| `src/core/prompt_builder.py` | ~450 | No |
| `src/core/session_manager.py` | ~300 | No |
| `src/memory/conversation.py` | 653 | No |
| `src/tools/handlers.py` | 604 | No |
| `src/api/routes/chat_ws.py` | ~200 | No |
| `src/api/routes/data.py` | ~200 | No |
| `src/api/routes/system.py` | 571 | No |
| All heartbeat actions (6 files) | ~600 | Partial |
