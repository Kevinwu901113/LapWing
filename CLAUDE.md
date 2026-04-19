# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Lapwing — Claude Code Project Guide

## Overview

Lapwing is a 24/7 autonomous AI companion. She is a virtual person with her own personality,
memories, and growth trajectory — not a chatbot or assistant framework. Everything she does
is driven by LLM tool calls: there is no rule-based router deciding what she says, feels, or
remembers.

**Tech stack**: Python 3.12 backend + Tauri v2 / React 19 / TypeScript desktop frontend.
**LLM providers**: MiniMax M2.7 via Anthropic-compatible API (primary), OpenAI / Codex via
OAuth, NVIDIA NIM for lightweight background slots.
**Messaging channels**: QQ (NapCat WebSocket, primary), Desktop (local WebSocket). Telegram
was retired; there is no Telegram code path in the current tree.
**Infrastructure**: PVE server (Xeon E-2174G, 32 GB RAM). Watchdog via `watchdog/` + systemd.

### MVP Invariants (do not break without a dedicated refactor)

1. **`tell_user` is the sole user-visible output.** Every token that reaches a human must
   pass through the `tell_user` tool. All other tool calls are internal monologue.
2. **Think-then-speak loop.** Brain → LLM decides whether to call tools or call `tell_user`;
   only `tell_user` produces user-visible bytes. The loop keeps running until the LLM stops
   emitting tool calls.
3. **Inner tick is interruptible.** When Lapwing is idle, `InnerTickScheduler` drives her
   own thoughts; an OWNER message pushes a high-priority event onto `EventQueue` and the
   inner tick yields.
4. **StateMutationLog is the single source of truth** for LLM calls, tool calls, iterations,
   promises, and tells. Anything that mutates durable state should record a mutation.

See *Known gaps* at the end of this document for invariant-level issues not yet addressed.

## Setup

```bash
pip install -r requirements.txt          # Python dependencies
cp config/.env.example config/.env       # Then fill in API keys
```

No linter, type checker, or CI pipeline is configured. Tests are the primary quality gate.

## Commands

```bash
# Tests
python -m pytest tests/ -x -q                              # All tests (~1257 cases, ~5 min)
python -m pytest tests/core/test_brain.py -x -q             # One file
python -m pytest tests/core/test_brain.py::test_name -x -q  # One test

# Deploy (NEVER run nohup python main.py & directly)
bash scripts/deploy.sh

# Auth management (OpenAI / Codex OAuth PKCE flow)
python main.py auth list
python main.py auth login openai-codex

# Desktop frontend (Tauri v2 + React 19)
cd desktop-v2 && npm run dev        # Vite dev server (localhost:1420)
cd desktop-v2 && npm run tauri dev  # Full Tauri v2 app with backend
cd desktop-v2 && npm run build      # Production build

# One-off diagnostics
python scripts/diagnose_schedule.py  # End-to-end check of DurableScheduler
python scripts/qq_export.py          # Dump QQ history for offline analysis
```

The process uses a PID file lock (`data/lapwing.pid`). If startup fails with
"另一个 Lapwing 进程正在运行", kill the old process first.

The legacy Tauri v1 / React 18 frontend (`desktop/`) was retired — `desktop-v2/` is the
only active frontend.

## Architecture

### Request flow (OWNER / TRUSTED / GUEST message → reply)

```
User message (QQ / Desktop adapter)
  → ChannelManager normalizes + tags adapter/actor
  → Brain.think_conversational(chat_id, text, send_fn, adapter, user_id)
      → AuthorityGate.identify(adapter, user_id)          # IGNORE/GUEST/TRUSTED/OWNER
      → ConversationMemory.append() + TrajectoryStore mirror
      → StateViewBuilder.build(chat_id)                    # read soul/voice/rules/trajectory/
                                                           # memory/commitments into StateView
      → StateSerializer.serialize(state_view)              # pure fn → prompt bytes
      → TaskRuntime.complete_chat() (tool loop; bounded by TASK_MAX_TOOL_ROUNDS,
        wrapped by iteration_context → StateMutationLog ITERATION_STARTED/ENDED)
          → LLMRouter._tracked_call                        # LLM_REQUEST / LLM_RESPONSE
          → VitalGuard → AuthorityGate → ToolRegistry.execute()
                                                           # TOOL_CALLED / TOOL_RESULT
          → iff tool == tell_user → bytes flow back to send_fn
```

### Inner tick flow (idle → self-initiated thinking)

```
InnerTickScheduler (event-queue producer, adaptive backoff)
  → EventQueue.push(InnerTickEvent, priority=low)
  → MainLoop consumes from EventQueue (priority-sorted: OWNER > TRUSTED > SYSTEM > INNER)
  → Brain.think_inner(prompt_context)
      → Same StateViewBuilder / StateSerializer / TaskRuntime chain
      → Inner turns record as INNER_THOUGHT in trajectory with source_chat_id = NULL
  → OWNER MessageEvent can preempt: high priority wins the peek; inner loop resumes later
```

### Maintenance flow (durable reminders + daily distillation)

```
DurableScheduler (reminders_v2 table) ──► InnerTickScheduler.push_urgency(reminder)
                                            → handled as a high-priority InnerTickEvent
MaintenanceTimer (3 AM daily)
  → SemanticDistiller.distill_recent()     # consolidates episodic/ into semantic/
```

### Key design decisions

- **LLM-driven everything.** No regex or keyword routing; every decision — whether to speak,
  which tool to call, whether to remember — is the model's. If you find yourself adding a
  rule that "filters" or "gates" a message, stop: that belongs in the prompt, not the code.
- **Personality lives in `prompts/lapwing_soul.md` + `prompts/lapwing_voice.md`.** soul.md
  defines WHO she is; voice.md uses ✕/✓ contrasts to set behavioural boundaries.
  `StateSerializer` injects a `_PERSONA_ANCHOR` reminder at depth-0 (immediately before the
  last user turn) to prevent drift during long tool loops.
- **Files as source of truth for identity and memory.** Soul / constitution live in
  `data/identity/`; episodic and semantic memory live as markdown under `data/memory/`.
  Databases are used for append-only event logs (`trajectory`, `commitments`,
  `reminders_v2`, `mutations`).
- **Single output channel.** `tell_user` is the sole tool that produces user-visible bytes
  (see Known gaps for plumbing that still bypasses this).
- **Four-tier permissions.** `AuthorityGate` classifies callers into
  `IGNORE(0) / GUEST(1) / TRUSTED(2) / OWNER(3)`. `ToolRegistry` enforces the per-tool
  minimum in `src/core/authority_gate.py:OPERATION_AUTH`. Desktop connections default to
  OWNER because the local API binds to localhost.

### Multi-channel architecture

`ChannelManager` routes messages to adapters under `src/adapters/`:

- `QQAdapter` — OneBot v11 over NapCat WebSocket. Primary channel.
- `DesktopAdapter` — local `/ws/chat` connection pool used by `desktop-v2/`.

Each adapter populates `ToolExecutionContext` with `{adapter, user_id, chat_id,
auth_level}`, which `AuthorityGate` inspects per tool call.

### Safety guards

- **`AuthorityGate`** (`src/core/authority_gate.py`) — per-tool minimum tier enforcement.
- **`VitalGuard`** (`src/core/vital_guard.py`) — pre-execution shield for shell/file writes.
  Verdicts: `PASS` / `VERIFY_FIRST` (backup then execute) / `BLOCK`. Protected paths:
  `src/`, `prompts/`, `data/identity/`, `data/memory/`, `config/`, `main.py`.
- **`ShellPolicy`** (`src/core/shell_policy.py`) — cwd / sudo / extension constraints layered
  on top of `execute_shell`.
- **`MemoryGuard`** (`src/guards/memory_guard.py`) — scans memory writes for prompt
  injection, credentials, identity tampering, and invisible unicode.
- **`BrowserGuard`** — URL blacklist/whitelist, internal-network blocking, and sensitive
  action detection (purchase, delete, etc.). Implemented inside
  `src/core/browser_manager.py`, **not** as a standalone file under `src/guards/`.
- **Loop detection** — tool-loop heuristics (generic repeat, ping-pong,
  known-poll-no-progress). Escalates warning → critical → global circuit breaker. Gated by
  `LOOP_DETECTION_ENABLED`.

## Directory structure

```
src/
  adapters/          Messaging adapters: base.py, qq_adapter.py, desktop_adapter.py,
                     qq_group_context.py, qq_group_filter.py
  agents/            Phase-6 agent framework: base.py, coder.py, researcher.py,
                     team_lead.py, registry.py, types.py
  api/               FastAPI + WebSocket local desktop API
    routes/          auth / agents / browser / chat_ws / identity / life_v2 /
                     events_v2 / models_v2 / notes_v2 / permissions_v2 / status_v2 /
                     system_v2 / tasks_v2
  app/               Application bootstrap (AppContainer = DI root, task_view)
  auth/              OAuth, API sessions, credential resolver
  core/              Core runtime. 44 files. Key groups:
                       Conversation path: brain, task_runtime, llm_router, llm_protocols,
                         llm_types, llm_exceptions, task_types, task_model, reasoning_tags,
                         output_sanitizer
                       v2.0 single-consumer loop: main_loop, event_queue, events,
                         inner_tick_scheduler, attention, dispatcher, trajectory_store,
                         commitments, durable_scheduler
                       State view assembly: state_view, state_view_builder,
                         state_serializer, identity_file_manager, soul_manager,
                         prompt_loader, phase0 (test harness)
                       Safety: authority_gate, vital_guard, shell_policy, shell_types,
                         verifier, credential_vault
                       Sensing: vitals, maintenance_timer, runtime_profiles,
                         model_config, group_filter, time_utils, trust_tagger,
                         minimax_vlm
                       Auth: codex_oauth_client
                       Channels: channel_manager
                       Browser: browser_manager
  guards/            memory_guard.py (only)
  logging/           state_mutation_log.py — Blueprint v2.0 §2 single source of truth
  memory/            RAPTOR two-layer memory + cache facade:
                       episodic_store / episodic_extractor (daily event logs)
                       semantic_store / semantic_distiller (kevin / lapwing / world facts)
                       working_set (cross-layer retrieval with MemorySnippets)
                       vector_store (ChromaDB)
                       embedding_worker / note_store / compactor
                       conversation (in-memory cache + trajectory mirror)
  models/            RichMessage shared data model
  research/          ResearchEngine (scope_router → fetcher → refiner → backends)
  tools/             Tool registry + executors:
                       tell_user.py  — SOLE user-visible output
                       commitments.py (commit / fulfill / abandon)
                       memory_tools_v2.py (recall / write_note / edit_note / …)
                       personal_tools.py (get_time / send_message / send_image / browse / view_image)
                       agent_tools.py (delegate, delegate_to_agent)
                       browser_tools.py (13 browser actions)
                       research_tool.py
                       soul_tools.py (read_soul / edit_soul — OWNER only)
                       shell_executor / file_editor / code_runner / workspace_tools /
                       handlers (shell / read_file / write_file / file_* / workspace
                       verifiers) / registry / types
  utils/             Small helpers

config/              settings.py (all values via os.getenv) + .env / .env.example / .env.test
prompts/             11 markdown files, hot-reloadable via prompt_loader.py
data/
  identity/          soul.md, voice.md, constitution.md (immutable by Lapwing)
  memory/
    episodic/        YYYY-MM-DD.md daily event logs (RAPTOR lower layer)
    semantic/        kevin.md / lapwing.md / world.md (RAPTOR upper layer)
    conversations/summaries/   compaction products (read by /api/v2/life/timeline)
  chroma/            ChromaDB vector store (search embeddings)
  chroma_memory/     ChromaDB vector store (memory embeddings; distinct collection)
  agent_workspace/   Coder Agent isolated sandbox
  tasks/             TaskFlow checkpoints
  tool_results/      Large tool output spill-over
  consciousness/     InnerTickScheduler working state
  backups/           VitalGuard auto-backups
  browser/           profile/ (persistent context), screenshots/, state.json
  credentials/       vault.enc (Fernet-encrypted credential store)
  logs/              lapwing.log + mutations_YYYY-MM-DD.log
  config/            model_routing.json, permissions.json, permission_overrides.json
  lapwing.db         SQLite (trajectory + commitments + reminders_v2 + sqlite_sequence)
  mutation_log.db    SQLite (mutations table; WAL)
  vitals.json        Boot/shutdown state for restart awareness
  lapwing.pid        Process lock
desktop-v2/          Tauri v2 + React 19 frontend (active)
scripts/             deploy.sh, diagnose_schedule.py, qq_export.py, setup_browser.sh,
                     migrations/mvp_drop_legacy_tables.py (one-shot; will be archived
                     after the 2026-04-19 cleanup branch merges)
tests/               ~1257 cases (pytest + pytest-asyncio auto mode); mirrors src/ layout
docs/
  archive/           Superseded designs, historical audits, frontend v1 blueprints
  refactor_v2/       Blueprint v2.0 step-by-step execution logs + design memos,
                     plus migrations-archive.md (registry of retired migration scripts)
  superpowers/plans/ Task plans (including this cleanup plan)
watchdog/            sentinel.py + lapwing-sentinel.service (systemd)
```

Deliberately absent (either never-existed or retired during the 2026-04-19 MVP cleanup):
`src/core/evolution.py`, `src/core/delegation.py`, `src/core/session.py`,
`src/core/heartbeat.py`, `src/core/prompt_builder.py`, `src/heartbeat/`,
`src/guards/skill_guard.py`, `src/guards/browser_guard.py`, `data/evolution/`,
`data/memory/notes/`, `data/workspace/`, `skills/`, `skill_traces/`, `desktop/`,
`user_facts` / `interest_topics` / `discoveries` / `todos` / `reminders` SQLite tables.

## Development conventions

- **Language**: Python source uses Chinese comments; CLAUDE.md and commit messages are in
  English.
- **Testing**: `pytest` with `pytest-asyncio` (`asyncio_mode=auto` in `pytest.ini`). No root
  `conftest.py`. Tests mock `LLMRouter`, `ConversationMemory`, etc.; the common pattern is
  to set up mock tool results and assert state-mutation-log entries.
- **Prompts**: Markdown in `prompts/`. Hot-reloadable via `prompt_loader.py`; changes ship
  without code changes.
- **Import style**: Absolute imports from project root (`from src.core.brain import ...`).
- **Config**: All config via env vars in `config/.env`, read in `config/settings.py`.
- **Feature flags** (live; all default true unless noted):
  `QQ_ENABLED` · `BROWSER_ENABLED` (default false) · `BROWSE_ENABLED` ·
  `BROWSER_VISION_ENABLED` · `MINIMAX_VLM_ENABLED` (default false) ·
  `SHELL_ENABLED` · `LOOP_DETECTION_ENABLED` · `CHAT_WEB_TOOLS_ENABLED` ·
  `AGENT_TEAM_ENABLED` · `EPISODIC_EXTRACT_ENABLED` · `SEMANTIC_DISTILL_ENABLED` ·
  `DESKTOP_DEFAULT_OWNER` · `SHELL_ALLOW_SUDO` (default false).
- **Logging**: dual logger setup in `main.py` — `lapwing` project logger + separate root
  library logger. Use `logging.getLogger("lapwing.module_name")`.
- **Type modules**: core types live in dedicated modules — `task_types.py` (task runtime),
  `llm_types.py` (LLM protocol adapters), `shell_types.py` (shell policy). Keep types out
  of logic files.

## Key subsystems

### AppContainer lifecycle

`AppContainer` (`src/app/container.py`) is the DI root.

1. `prepare()` — initialise DB, wire `StateMutationLog`, `TrajectoryStore`,
   `CommitmentStore`, `DurableScheduler`, memory stores, and optional subsystems
   (`BrowserManager`, agent team, etc.).
2. `start(send_fn)` — launch `InnerTickScheduler`, `MaintenanceTimer`, `ChannelManager`,
   `LocalApiServer`. Wire `DurableScheduler.urgency_callback` to push reminder fires into
   `InnerTickScheduler`.
3. `shutdown()` — reverse teardown.

All optional brain dependencies default to `None` and are gated by feature flags.

### LLM Router

`LLMRouter` routes by *purpose slot*:
- `chat` (main conversation, persona expression, self-reflection) → `LLM_CHAT_*` env vars
- `tool` (lightweight judgement, memory processing, agent execution) → `LLM_TOOL_*` env vars
- `heartbeat` (inner-tick / background) → `NIM_*` env vars
- `browser_vision` (page description) → `BROWSER_VISION_*` / `MINIMAX_VLM_*`

Falls back to the generic `LLM_*` set if slot-specific vars are absent. Auto-detects
Anthropic-compatible endpoints via `/anthropic` in `base_url` and switches to
`AsyncAnthropic`.

Runtime model switching is persisted to `data/config/model_routing.json` via
`ModelConfigManager`.

### Tool system

Tools are `ToolSpec` instances registered in `build_default_tool_registry()`
(`src/tools/registry.py`). Each has:

- `capability` — one of `shell` / `web` / `file` / `memory` / `schedule` / `code`
  / `verify` / `general` / `browser` / `personal` / `identity` / `commitment` /
  `delegation` / `communication` / `agent`.
- `visibility` — `"model"` (LLM sees the schema) vs `"internal"` (invoked by framework
  only, hidden from the LLM).
- `risk_level` — `low` / `medium` / `high` (surfaced to `VitalGuard`).

`RuntimeProfile` (`src/core/runtime_profiles.py`) defines which capabilities are exposed
per execution context: `chat_shell` (main conversation), `coder_snippet`,
`coder_workspace`, `file_ops`.

`TaskRuntime` runs the tool loop: LLM picks a tool → executor runs → result feeds back →
repeat, bounded by `TASK_MAX_TOOL_ROUNDS`. Loop detection and circuit breaker are built in.

### Single-consumer MainLoop (Blueprint v2.0 Step 4)

`MainLoop` is the single consumer of `EventQueue`. Events carry a priority
(OWNER > TRUSTED > SYSTEM > INNER); a concurrent watcher cancels the in-flight
handler the moment an OWNER message lands, so inner ticks and reminder-driven
agent runs yield to Kevin immediately. Handlers dispatch by event class:

- `MessageEvent` → `_handle_message` → `brain.think_conversational`. Producers:
  QQ adapter (`main.py`) and Desktop WebSocket (`src/api/routes/chat_ws.py`);
  also `DurableScheduler._fire_agent` for agent-mode reminder fires, so those
  inherit the same preemption rules.
- `InnerTickEvent` → `_handle_inner_tick` → `brain.think_inner`. Producer:
  `InnerTickScheduler` (adaptive backoff, urgency pushed by
  `DurableScheduler` when a reminder fires).
- `SystemEvent` → `_handle_system` (currently handles `shutdown`).

`done_future` on `MessageEvent` lets producers await the handler's reply — the
Desktop WS channel uses this for synchronous request/response turns; the
scheduler uses it to relay the agent-mode final reply through
`send_system_message`.

### RAPTOR two-layer memory

- **Lower layer (episodic)**: `EpisodicExtractor` runs after a conversation window closes
  and writes a YYYY-MM-DD.md file under `data/memory/episodic/`.
- **Upper layer (semantic)**: `SemanticDistiller` runs daily (via `MaintenanceTimer`) and
  consolidates episodic content into `kevin.md` / `lapwing.md` / `world.md` under
  `data/memory/semantic/`.
- **Cross-layer retrieval**: `WorkingSet.build(chat_id, query)` returns
  `MemorySnippets` tagged `[情景]` or `[知识]`; `StateViewBuilder` injects them into the
  prompt.
- **Vectors**: `MemoryVectorStore` (collection `lapwing_memory` under `data/chroma_memory/`)
  ranks by similarity × recency × trust × depth × access.

### Durable reminders

`DurableScheduler` owns the `reminders_v2` table (schema created by the scheduler itself,
not by `ConversationMemory`). When a reminder fires, the callback pushes an urgency event
into `InnerTickScheduler` so the model sees it on the next tick.

### Browser subsystem

`BrowserManager` (`src/core/browser_manager.py`) provides Playwright-based Chromium
automation:

- Persistent context under `data/browser/profile/`.
- Tab management (`BROWSER_MAX_TABS` default 8), DOM extraction, structured page state.
- Screenshots with retention (`BROWSER_SCREENSHOT_RETAIN_DAYS`).
- Vision pipeline: when the page is image-heavy (above `BROWSER_VISION_IMG_THRESHOLD`), a
  screenshot is sent to the `BROWSER_VISION_SLOT` (via LLMRouter) or to the direct MiniMax
  VLM endpoint if `MINIMAX_VLM_ENABLED=true`.
- BrowserGuard (in-module) vets URLs and flags sensitive actions (purchase, delete).
- Gated by `BROWSER_ENABLED`. 25+ `BROWSER_*` env vars, all declared in
  `config/settings.py`.

### Vitals

`src/core/vitals.py` tracks system lifecycle: boot time, uptime, restart detection.
Persists to `data/vitals.json`. Provides `system_snapshot()` (CPU / memory / disk via
psutil) and desktop environment sensing (current app, user state, 10-minute TTL).

### Search providers

`SEARCH_PROVIDER`: `"auto"` (default — Tavily if `TAVILY_API_KEY`, else DDG) /
`"tavily"` / `"ddg"`. `TAVILY_SEARCH_DEPTH` chooses `basic` vs `advanced`.

## Extension patterns

### Adding a new tool

1. Implement `async def my_tool(req: ToolExecutionRequest, ctx: ToolExecutionContext) -> ToolExecutionResult`
   in `src/tools/handlers.py` (or a new file under `src/tools/`).
2. Register in `build_default_tool_registry()` in `src/tools/registry.py`:

   ```python
   registry.register(ToolSpec(
       name="my_tool",
       description="...",
       json_schema={...},
       executor=my_tool,
       capability="my_cap",
       risk_level="low",
   ))
   ```

3. If auth-gated, add an entry in `OPERATION_AUTH` in `src/core/authority_gate.py`.

### Adding a maintenance task

`MaintenanceTimer` only runs semantic distillation at 3 AM. For a periodic task, extend
`_run_daily()`, or push an event via `InnerTickScheduler.push_urgency()` on a trigger.
Heartbeat-style "always/decide" actions have been retired — the inner loop + trajectory
replace them.

### Adding a message channel

1. Inherit `BaseAdapter` (`src/adapters/base.py`), implement
   `start / stop / send_message / is_connected`.
2. In `main.py`, construct the adapter and call
   `container.channel_manager.register(ChannelType.XXX, adapter)`.
3. Route messages via
   `brain.think_conversational(chat_id, text, send_fn, adapter="xxx", user_id="...")`.

## Desktop frontend (`desktop-v2/`)

Tauri v2 + React 19 + TypeScript. Stack: Zustand (state), Tailwind CSS 4, shadcn/ui,
CodeMirror (markdown editing), react-router-dom, Recharts (dashboards), Lucide icons.

Pages (`desktop-v2/src/pages/`):

- `ChatPage.tsx` — main conversation
- `IdentityPage.tsx` — soul / voice / constitution editing with version history
- `NotesPage.tsx` — memory browsing + CRUD
- `SystemPage.tsx` — heartbeat / reminders / resource dashboard
- `StatusDetailPage.tsx` — system metrics detail
- `SettingsPage.tsx` — settings (including runtime model routing)

State: Zustand stores in `src/stores/` (`chat.ts`, `server.ts`). Types in `src/types/`.

## MiniMax-specific notes

- Uses Anthropic-compatible endpoint (`api.minimaxi.com/anthropic`). `LLMRouter`
  auto-detects via `/anthropic` in `base_url` and uses `AsyncAnthropic`.
- Temperature must be in `(0.0, 1.0]` — do **not** pass `temperature=0`.
- 529 errors (overload) are classified as rate_limit by
  `_classify_provider_exception()`.

## Known gaps

As of the 2026-04-19 MVP cleanup + its O1/O2/O3 follow-ups, there are no
outstanding invariant-level gaps. Earlier documentation flagged three, all now
resolved:

- `tell_user` single-exit — resolved by O1. All user-visible bytes
  (LLM-mediated through the `tell_user` tool, and framework-mediated
  through `src/core/system_send.py:send_system_message`) record into
  `trajectory_store` + `mutation_log`. The invariant stays intact: the LLM
  has only one exit (`tell_user`); the framework has a distinct, audited
  exit that carries a `source` tag (`confirmation` / `llm_error` /
  `reminder_notify` / `reminder_agent_result` / `reminder_agent_fallback`).
- `MainLoop` not load-bearing — resolved. The earlier note was wrong: the
  handler bodies in `src/core/main_loop.py:_handle_message` /
  `_handle_inner_tick` / `_handle_system` are fully implemented, and the QQ
  adapter (`main.py`) + Desktop WebSocket (`src/api/routes/chat_ws.py`) push
  `MessageEvent` into the shared `EventQueue` (the only consumers of
  `brain.think_conversational` in production are the MainLoop handler and
  the scheduler fallback).
- Dormant skill subsystem — resolved by O3 (removed).

If a new invariant-level gap is found, add it back here.
