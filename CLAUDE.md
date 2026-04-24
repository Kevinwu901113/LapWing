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

1. **Direct output.** In a conversation, the LLM's bare text reply is the user-visible
   message — no wrapper tool. Tool calls are internal operations (search, notes, etc.).
   When the model has no live conversation context (inner tick, scheduled reminder,
   agent-mode scheduler), it uses the `send_message` tool to proactively push to a
   specific target (`kevin_qq` / `kevin_desktop` / `qq_group:{id}`).
2. **Think-then-speak loop.** Brain → LLM emits tool calls until it stops; the final
   bare-text turn is what Kevin sees. Loop bounded by `TASK_MAX_TOOL_ROUNDS`.
3. **Inner tick is interruptible.** When Lapwing is idle, `InnerTickScheduler` drives her
   own thoughts; an OWNER message pushes a high-priority event onto `EventQueue` and the
   inner tick yields.
4. **StateMutationLog is the single source of truth** for LLM calls, tool calls,
   iterations, promises, and user-visible sends. Anything that mutates durable state
   records a mutation.

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
          → when LLM emits final bare text (no more tool_use) → send_fn delivers it
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
- **Direct output, no wrapper tool.** In a live conversation the LLM's bare text is the
  user-visible message — the contract is structural: the chat path delivers the final
  non-tool-use reply through `send_fn`. `send_message` (in `personal_tools.py`) is used
  only when there is no live conversation context (inner tick, scheduled reminder,
  agent-mode scheduler fallback) to push a message to a specific target.
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
  ambient/           Short-term environment knowledge (working memory, not long-term):
                       ambient_knowledge (SQLite store with TTL)
                       preparation_engine (parses interest profile → prep status)
                       time_context (pure function: now → TimeContext with lunar/season)
                       models (AmbientEntry, Interest, PreparationStatus, TimeContext)
  api/               FastAPI + WebSocket local desktop API
    routes/          auth / agents / browser / chat_ws / identity / life_v2 /
                     events_v2 / models_v2 / notes_v2 / permissions_v2 / status_v2 /
                     system_v2 / tasks_v2
  app/               Application bootstrap (AppContainer = DI root, task_view)
  auth/              OAuth, API sessions, credential resolver
  core/              Core runtime. Key groups:
                       Conversation path: brain, task_runtime, llm_router, llm_protocols,
                         llm_types, llm_exceptions, task_types, task_model, reasoning_tags,
                         output_sanitizer
                       v2.0 single-consumer loop: main_loop, event_queue, events,
                         inner_tick_scheduler, attention, dispatcher, trajectory_store,
                         commitments, durable_scheduler
                       State view assembly: state_view, state_view_builder,
                         state_serializer, identity_file_manager, soul_manager,
                         prompt_loader, phase0 (test harness)
                       Task planning: plan_state (PlanStep/PlanState/transitions)
                       Behavior correction: correction_manager (Kevin corrections +
                         circuit-breaker callback; triggers urgency at threshold)
                       Framework-level user output: system_send (audited non-LLM exit
                         for confirmations, LLM errors, notify-mode reminders, agent
                         scheduler fallbacks)
                       Safety: authority_gate, vital_guard, shell_policy, shell_types,
                         verifier, credential_vault, credential_sanitizer
                       Sandbox: execution_sandbox (three-tier Docker isolation:
                         STRICT/STANDARD/PRIVILEGED; used by shell_executor + code_runner)
                       Network: proxy_router (per-domain adaptive proxy/direct selection;
                         seeded with Chinese domains direct; persists to data/proxy/)
                       Sensing: vitals, maintenance_timer, runtime_profiles,
                         model_config, group_filter, time_utils, trust_tagger,
                         minimax_vlm
                       Auth: codex_oauth_client
                       Channels: channel_manager
                       Browser: browser_manager (dual-context with ProxyRouter)
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
  skills/            Skill Growth Model: skill_store.py (YAML+md CRUD),
                       skill_executor.py (Docker sandbox / host routing)
  tools/             Tool registry + executors:
                       commitments.py (commit / fulfill / abandon)
                       memory_tools_v2.py (recall / write_note / edit_note / …)
                       personal_tools.py (get_time / send_message / send_image /
                         browse / view_image) — send_message is the proactive-push
                         tool for no-conversation contexts (target=kevin_qq /
                         kevin_desktop / qq_group:{id})
                       agent_tools.py (delegate, delegate_to_agent)
                       browser_tools.py (13 browser actions)
                       skill_tools.py (create / run / edit / list / promote / delete /
                         search / install skill — marketplace + local CRUD)
                       ambient_tools.py (prepare_ambient_knowledge /
                         check_ambient_knowledge / manage_interest_profile)
                       correction_tools.py (add_correction — logs Kevin's corrections)
                       plan_tools.py (plan_task / update_plan — multi-step task planning
                         with soft-gate on the final bare-text reply)
                       timezone_tools.py (convert_timezone / get_current_datetime)
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
  proxy/             routing_rules.json (ProxyRouter persisted per-domain rules)
  logs/              lapwing.log + mutations_YYYY-MM-DD.log
  config/            model_routing.json, permissions.json, permission_overrides.json
  lapwing.db         SQLite (trajectory + commitments + reminders_v2 + sqlite_sequence)
  mutation_log.db    SQLite (mutations table; WAL)
  ambient.db         SQLite (ambient_entries; TTL-scoped short-term environment cache)
  vitals.json        Boot/shutdown state for restart awareness
  lapwing.pid        Process lock
desktop-v2/          Tauri v2 + React 19 frontend (active)
scripts/             deploy.sh, diagnose_schedule.py, qq_export.py, setup_browser.sh
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
`data/memory/notes/`, `data/workspace/`, `skill_traces/`, `desktop/`,
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
  `QQ_ENABLED` · `BROWSER_ENABLED` (default false) ·
  `BROWSER_VISION_ENABLED` · `MINIMAX_VLM_ENABLED` (default false) ·
  `SHELL_ENABLED` · `LOOP_DETECTION_ENABLED` · `CHAT_WEB_TOOLS_ENABLED` ·
  `AGENT_TEAM_ENABLED` · `EPISODIC_EXTRACT_ENABLED` · `SEMANTIC_DISTILL_ENABLED` ·
  `DESKTOP_DEFAULT_OWNER` · `SHELL_ALLOW_SUDO` (default false) ·
  `SKILL_SYSTEM_ENABLED` (default false).
- **Proxy settings** (ProxyRouter): `PROXY_SERVER` (upstream proxy URL, empty = disable),
  `PROXY_DEFAULT_STRATEGY` (`proxy` / `direct`), `PROXY_PERSIST_INTERVAL_SECONDS`.
  `SEARCH_PROXY_URL` still applies specifically to search-backend calls.
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

- Persistent context under `data/browser/profile/` (dual-context: proxied + direct, so
  `ProxyRouter` can pick per-domain without tearing down the other session).
- Tab management (`BROWSER_MAX_TABS` default 8), DOM extraction, structured page state.
- Screenshots with retention (`BROWSER_SCREENSHOT_RETAIN_DAYS`).
- Vision pipeline: when the page is image-heavy (above `BROWSER_VISION_IMG_THRESHOLD`), a
  screenshot is sent to the `BROWSER_VISION_SLOT` (via LLMRouter) or to the direct MiniMax
  VLM endpoint if `MINIMAX_VLM_ENABLED=true`.
- BrowserGuard (in-module) vets URLs and flags sensitive actions (purchase, delete).
- Gated by `BROWSER_ENABLED`. 25+ `BROWSER_*` env vars, all declared in
  `config/settings.py`.

### ProxyRouter

`ProxyRouter` (`src/core/proxy_router.py`) does per-domain adaptive routing between the
upstream proxy and direct connection. Seeded with Chinese domains (`*.cn`, `*.baidu.com`,
`*.qq.com`, `*.bilibili.com`, …) as `direct`; everything else starts at
`PROXY_DEFAULT_STRATEGY`. On failure, it flips the domain's strategy and records the
outcome; on sustained success it stabilises. Rules persist to
`data/proxy/routing_rules.json` on a timer (`PROXY_PERSIST_INTERVAL_SECONDS`) and at
shutdown. Consumers: `SmartFetcher` and `BrowserManager` both go through `ProxyRouter`;
search-backend calls still use `SEARCH_PROXY_URL` directly.

### ExecutionSandbox

`ExecutionSandbox` (`src/core/execution_sandbox.py`) is the unified Docker harness used
by `code_runner` and `shell_executor`. Three tiers:

- `STRICT` — 256 MB / 0.5 CPU / no network / workspace read-only (default for
  `code_runner`).
- `STANDARD` — 512 MB / 1.0 CPU / bridge network (`lapwing-sandbox`) / workspace RW.
- `PRIVILEGED` — 1 GB+, host network, mounted secrets (opt-in, shell-side).

Secrets are scrubbed via `credential_sanitizer.sanitize_env` before entering the
container; stdout/stderr are redacted with `redact_secrets` and truncated to 4 000 bytes.
The default image is `lapwing-sandbox:latest`.

### CorrectionManager

`CorrectionManager` (`src/core/correction_manager.py`) is the feedback spine for
behaviour drift:

- `add_correction(rule_key, details)` — logs Kevin's correction; at the 3rd hit on the
  same `rule_key`, invokes the `on_threshold` callback which pushes an urgency event into
  `InnerTickScheduler` so the model sees the pattern on the next tick.
- `on_circuit_break(tool_name, repeat_count)` — wired from `TaskRuntime`'s per-tool loop
  detector. Debounces with a 10-minute cooldown per tool so the model isn't spammed.

Exposed to the LLM as the `add_correction` tool (`src/tools/correction_tools.py`).

### Task planning (PlanState)

For multi-step requests the LLM calls `plan_task` (builds a `PlanState` with ≥2 steps),
then `update_plan` to advance status (`pending` → `in_progress` → `completed` /
`blocked`). `PlanState` lives in `TaskRuntime.context.services["plan_state"]` (lifetime =
single TaskRuntime execution; not persisted across turns). Each tool round re-injects the
rendered plan into the LLM's view, and the final bare-text reply carries a **soft gate**:
if the plan has incomplete steps, a reminder is prepended warning the model to finish
planning before speaking. Gate is advisory, not a hard block.

### Ambient knowledge

`AmbientKnowledgeStore` (`src/ambient/ambient_knowledge.py`, backed by
`data/ambient.db`) is short-term working memory — **not** a substitute for episodic /
semantic memory. It caches "what Lapwing currently knows" (weather, sports, news,
calendar) keyed by topic, with per-category TTL (weather 3 h, news 4 h, sports 6 h,
calendar 12 h, default 6 h) and a 50-entry cap.

- `prepare_ambient_knowledge` — fetches via `ResearchEngine` then writes an entry.
- `check_ambient_knowledge` — reads (honours TTL).
- `manage_interest_profile` — CRUD for Kevin's interest profile (Markdown). The profile
  is parsed by `PreparationEngine` into `Interest` records; `StateViewBuilder` can then
  ask "what should Lapwing have prepared by now?" (`PreparationStatus`) and inject that
  into the prompt.

`TimeContextProvider` (`src/ambient/time_context.py`) is a pure function over `datetime`
that yields weekday / season / lunar date (optional `lunardate`) / time-of-day bucket —
no external calls, used by `StateViewBuilder` to give every turn a dated preamble.

### Framework-level user output (`system_send`)

`src/core/system_send.py` is the **non-LLM** audited exit for user-visible bytes
(`send_system_message`). It covers the four framework-mediated cases that don't run
through the model: confirm responses (`TaskRuntime.resolve_pending_confirmation`),
LLM-call error surfacing, timer-driven `notify`-mode reminders, and agent-mode scheduler
fallbacks. LLM-mediated sends (bare-text conversation replies + `send_message` tool
calls) write to `trajectory_store` + `mutation_log`; `system_send` writes the same
entries with a `source` tag (`confirmation` / `llm_error` / `reminder_notify` /
`reminder_agent_result` / `reminder_agent_fallback`) so the audit trail stays complete.

### Vitals

`src/core/vitals.py` tracks system lifecycle: boot time, uptime, restart detection.
Persists to `data/vitals.json`. Provides `system_snapshot()` (CPU / memory / disk via
psutil) and desktop environment sensing (current app, user state, 10-minute TTL).

### Search providers

`TAVILY_API_KEY` + `BOCHA_API_KEY` configure the two search backends
(Tavily for international, Bocha for domestic Chinese). Both run in
parallel when keys are present; results are merged by the research engine.

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

As of the 2026-04-24 direct-output overhaul, there are no outstanding
invariant-level gaps. Earlier documentation flagged several, all now resolved:

- `tell_user` single-exit — superseded by the 2026-04-24 direct-output
  overhaul. The `tell_user` tool was removed; in a live conversation the
  LLM's bare text reply is the user-visible message (Invariant #1). The
  `send_message` tool (in `personal_tools.py`) handles proactive pushes
  when there is no live conversation context. Audit-trail guarantees are
  preserved: both LLM-mediated sends and `system_send` framework exits
  record into `trajectory_store` + `mutation_log`.
- `MainLoop` not load-bearing — resolved. The handler bodies in
  `src/core/main_loop.py:_handle_message` / `_handle_inner_tick` /
  `_handle_system` are fully implemented, and the QQ adapter (`main.py`)
  + Desktop WebSocket (`src/api/routes/chat_ws.py`) push `MessageEvent`
  into the shared `EventQueue` (the only consumers of
  `brain.think_conversational` in production are the MainLoop handler and
  the scheduler fallback).
- Dormant skill subsystem — resolved by O3 (removed), then
  re-implemented as the Skill Growth Model (2026-04-20). Skills live
  under `src/skills/` (SkillStore + SkillExecutor) with 6 LLM-facing
  tools in `src/tools/skill_tools.py`. Gated by `SKILL_SYSTEM_ENABLED`
  (default false).

If a new invariant-level gap is found, add it back here.
