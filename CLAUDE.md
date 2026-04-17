# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Lapwing — Claude Code Project Guide

## Overview

Lapwing is a 24/7 autonomous AI companion system. She is a virtual person with her own
personality, memories, and growth trajectory — not a bot or assistant framework.

**Tech stack**: Python backend + Tauri v2/React 19/TypeScript desktop frontend.
**LLM providers**: MiniMax M2.7 via Anthropic-compatible API, GLM via OpenAI-compatible API.
**Messaging**: Telegram Bot API, QQ (NapCat WebSocket), Desktop (local WebSocket).
**Infrastructure**: PVE server (Xeon E-2174G, 32GB RAM).

## Setup

```bash
pip install -r requirements.txt          # Python dependencies
cp config/.env.example config/.env       # Then fill in API keys
```

No linter, type checker, or CI pipeline is configured. Tests are the primary quality gate.

## Commands

```bash
# Tests
python -m pytest tests/ -x -q                              # All tests
python -m pytest tests/core/test_brain.py -x -q             # One file
python -m pytest tests/core/test_brain.py::test_name -x -q  # One test

# Deploy (NEVER run nohup python main.py & directly)
bash scripts/deploy.sh

# Auth management
python main.py auth list
python main.py auth login openai-codex

# Desktop frontend (v2 — active development)
cd desktop-v2 && npm run dev        # Vite dev server (localhost:1420)
cd desktop-v2 && npm run tauri dev  # Full Tauri v2 app with backend
cd desktop-v2 && npm run build      # Production build

# Desktop frontend (v1 — legacy)
cd desktop && npm run dev        # Vite dev server
cd desktop && npm run tauri dev  # Full Tauri v1 app
```

The process uses a PID file lock (`data/lapwing.pid`). If startup fails with
"另一个 Lapwing 进程正在运行", kill the old process first.

## Architecture

All user messages flow through one path:

```
User message (any channel)
  → Brain._prepare_think()
      → SessionManager.resolve_session()
      → ConversationMemory.append()
      → PromptBuilder.build_system_prompt() (8 layers: soul → rules → time → memory → facts → vectors → summaries → voice)
      → ExperienceSkillManager.retrieve() (inject relevant past experience)
  → Brain._complete_chat()
      → TaskRuntime.complete_chat() (tool loop, max TASK_MAX_TOOL_ROUNDS rounds;
        wrapped by iteration_context → StateMutationLog ITERATION_STARTED/ENDED)
          → LLMRouter._tracked_call (records LLM_REQUEST/LLM_RESPONSE)
          → VitalGuard → AuthorityGate → ToolRegistry.execute()
            (records TOOL_CALLED/TOOL_RESULT)
```

There is NO agent dispatch layer. All capabilities are tool schemas. The LLM decides
which tools to call.

### Key Design Decisions

- **LLM-driven routing**: No regex or keyword matching. The model decides everything
  through tool calls.
- **Personality via soul.md + voice.md**: soul.md defines WHO she is. voice.md uses
  ✕/✓ contrasts to set behavioral boundaries. PromptBuilder injects a `_PERSONA_ANCHOR`
  reminder at depth-0 (before the last user message) to prevent drift.
- **Files as source of truth**: Identity, memory, and evolution use markdown files,
  not databases, for transparency and editability.
- **Diff-based evolution**: Personality changes accumulate as diffs, not full rewrites.
  ConstitutionGuard ensures only Kevin can modify the constitution.

### Multi-Channel Architecture

`ChannelManager` routes messages to adapters (`ChannelType.TELEGRAM`, `.QQ`, `.DESKTOP`).
Each adapter injects identity info into `ToolExecutionContext`, which flows through
`AuthorityGate` for three-tier permission checks:
- `OWNER` (level 2) — Kevin. Full access. Desktop defaults to OWNER.
- `TRUSTED` (level 1) — Friends. Search, chat, general tools.
- `GUEST` (level 0) — Group members. Chat only.

### Safety Guards

- **VitalGuard** (`src/core/vital_guard.py`) — Pre-execution shield for shell/file writes.
  Verdicts: `PASS`, `VERIFY_FIRST` (backup then execute), `BLOCK`. Protects `src/`,
  `prompts/`, `data/identity/`, `data/memory/`, `config/`, `main.py`.
- **BrowserGuard** (`src/guards/browser_guard.py`) — Pre-execution shield for browser actions.
  URL blacklist/whitelist, internal network blocking, sensitive action detection
  (purchase, delete, etc.).
- **ConstitutionGuard** — Only Kevin can modify constitution. Enforced during evolution.
- **MemoryGuard** (`src/guards/memory_guard.py`) — Validates memory writes.
- **Loop Detection** — Detects tool call loops (generic repeat, ping-pong, poll-no-progress).
  Escalates: warning → critical → global circuit breaker.

## Directory Structure

```
src/
  adapters/      — Messaging platform adapters (QQ, Desktop)
  api/           — Desktop API server (FastAPI + WebSocket)
    routes/      — auth.py, browser.py, chat_ws.py, data.py, system.py
  app/           — Application bootstrap: AppContainer (DI root), TelegramApp
  auth/          — Auth management (OAuth, API keys, desktop tokens)
  core/          — Core logic (brain, llm_router, task_runtime, prompt_builder,
                   heartbeat, evolution, delegation, session, skills, dispatcher,
                   browser_manager, vitals, runtime_profiles, credential_vault)
  logging/       — StateMutationLog (durable append-only log of LLM/tool/iteration
                   mutations; see Blueprint v2.0 §2)
  guards/        — Safety guards (memory_guard, skill_guard, browser_guard)
  heartbeat/     — Periodic background actions (actions/ subdirectory, 12 action files)
  memory/        — Conversation memory, facts, interests, vector store, auto-extraction,
                   discoveries (browser findings), todos, reminders
  models/        — Shared data models (RichMessage)
  tools/         — Tool registry + execution handlers (20 handler files)
config/          — Settings (.env) and settings.py (all config via os.getenv)
prompts/         — Markdown prompt templates (hot-reloadable)
data/
  identity/      — soul.md, constitution.md (immutable by Lapwing)
  memory/        — KEVIN.md, SELF.md, _index.json, journal/, sessions/, conversations/summaries/
  evolution/     — rules.md, interests.md, changelog.md (diff-based)
  config/        — model_routing.json (runtime model config)
  browser/       — profile/ (persistent browser context), screenshots/
  credentials/   — vault.enc (encrypted credential store)
  tasks/         — TaskFlow checkpoints
  chroma/        — ChromaDB vector store
  lapwing.db     — SQLite (conversations, user_facts, reminders, sessions)
  lapwing.pid    — Process lock file
  vitals.json    — Boot/shutdown state for restart awareness
desktop-v2/      — Tauri v2 + React 19 frontend (Zustand, Tailwind, shadcn, CodeMirror)
desktop/         — Legacy Tauri v1 + React 18 frontend
skills/          — Experience skills (Lapwing's learned patterns, early stage)
scripts/         — deploy.sh, diagnostic/migration utilities
tests/           — Mirrors src/ structure (~75 test files)
```

## Development Conventions

- **Language**: Python source uses Chinese comments. CLAUDE.md and commit messages in English.
- **Testing**: `pytest` with `pytest-asyncio` (asyncio_mode=auto in pytest.ini). No root
  conftest.py. Tests mock LLMRouter, ConversationMemory, etc. Pattern: define mock tool
  results, assert state changes.
- **Prompts**: Markdown in `prompts/`. Hot-reloadable via `prompt_loader.py`. Changes
  deploy without code changes.
- **Import style**: Absolute imports from project root (`from src.core.brain import ...`).
- **Config**: All config via env vars in `config/.env`, loaded in `config/settings.py`.
  Feature flags use `FEATURE_ENABLED` pattern (e.g., `MEMORY_CRUD_ENABLED`,
  `DELEGATION_ENABLED`, `SKILLS_ENABLED`, `SHELL_ENABLED`, `SESSION_ENABLED`,
  `LOOP_DETECTION_ENABLED`, `CHAT_WEB_TOOLS_ENABLED`,
  `AUTO_MEMORY_EXTRACT_ENABLED`, `SELF_SCHEDULE_ENABLED`, `MESSAGE_SPLIT_ENABLED`,
  `BROWSER_ENABLED`, `BROWSE_ENABLED`).
- **Logging**: Dual logger setup in `main.py` — `lapwing` project logger + separate root
  library logger. Use `logging.getLogger("lapwing.module_name")`.
- **Type extraction**: Core types live in dedicated modules — `task_types.py` (task runtime
  types), `llm_types.py` (LLM protocol adapters). Keep types separate from logic.

## Key Subsystems

### AppContainer Lifecycle

`AppContainer` (`src/app/container.py`) is the DI root:
1. `prepare()` — init DB, wire brain dependencies via `_configure_brain_dependencies()`
2. `start(send_fn)` — launch HeartbeatEngine, ReminderScheduler, ChannelManager, LocalApiServer
3. `shutdown()` — reverse teardown

All brain dependencies (KnowledgeManager, VectorStore, SkillManager, SessionManager,
BrowserManager, etc.) are optional (`brain.xxx` defaults to `None`), gated by feature flags.

### LLM Router

`LLMRouter` routes by *purpose slot*:
- `chat` (main_conversation, persona_expression, self_reflection) → `LLM_CHAT_*` env vars
- `tool` (lightweight_judgment, memory_processing, agent_execution) → `LLM_TOOL_*` env vars
- `heartbeat` (heartbeat_proactive) → `NIM_*` env vars

Falls back to generic `LLM_*` vars if slot-specific vars are absent. Auto-detects
Anthropic-compatible endpoints via `/anthropic` in the base_url.

Runtime model routing persisted to `data/config/model_routing.json` via `ModelConfigManager`.

### Tool System

Tools are `ToolSpec` instances registered in `build_default_tool_registry()` (`src/tools/registry.py`).
Each has a `capability` tag (shell, web, file, memory, schedule, skill, code, verify, general,
browser) used to filter which tools are exposed. `visibility="internal"` hides a tool from the LLM.

`RuntimeProfile` (`src/core/runtime_profiles.py`) controls which capabilities and tools are
available per execution context: `chat_shell` (main conversation), `coder_snippet`,
`coder_workspace`, `file_ops`.

`TaskRuntime` runs the tool loop: LLM selects tools → execute → feed results back → repeat
until done or max rounds reached. Loop detection and circuit breaker are built in.

### Heartbeat System

Three beat types: `fast` (hourly), `slow` (daily 3 AM), `minute` (every minute).
Actions implement `HeartbeatAction` ABC. `selection_mode="always"` runs unconditionally;
`"decide"` means the LLM picks from candidates.

### Browser Subsystem

`BrowserManager` (`src/core/browser_manager.py`) provides Playwright-based Chromium automation:
- Persistent browser context with user data dir (`data/browser/profile/`)
- Tab management (max 8 default), DOM extraction, structured page state for LLM consumption
- Screenshot capture with retention policy (`data/browser/screenshots/`)
- Vision pipeline: when a page is image-heavy (above `BROWSER_VISION_IMG_THRESHOLD`),
  takes a screenshot and sends to a separate LLM slot (`BROWSER_VISION_SLOT`) for visual
  description. Cached with TTL.
- `BrowserGuard` validates URLs (blacklist/whitelist, internal network blocking) and
  detects sensitive actions (purchase, delete) before execution.
- Gated by `BROWSER_ENABLED`. 25+ `BROWSER_*` env vars in `config/settings.py`.

### Vitals

`src/core/vitals.py` tracks system lifecycle: boot time, uptime, restart detection.
Persists state to `data/vitals.json`. Provides `system_snapshot()` (CPU/memory/disk via
psutil) and desktop environment sensing (current app, user state, 10-min TTL).

### Search Providers

`SEARCH_PROVIDER` env var controls web search: `"auto"` (default) tries Tavily first
then DuckDuckGo, `"tavily"` or `"ddg"` forces one. Needs `TAVILY_API_KEY` for Tavily.

## Extension Patterns

### Adding a New Tool

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
3. If auth-gated, add entry in `src/core/authority_gate.py` tool permission table.

### Adding a Heartbeat Action

1. Create file in `src/heartbeat/actions/`, inherit `HeartbeatAction`.
   Set `name`, `description`, `beat_types`, `selection_mode`.
2. Register in `AppContainer._build_heartbeat()` via `heartbeat.registry.register(MyAction())`.

### Adding a Message Channel

1. Inherit `BaseAdapter` (`src/adapters/base.py`), implement `start/stop/send_message/is_connected`.
2. In `main.py`, construct adapter and call `container.channel_manager.register(ChannelType.XXX, adapter)`.
3. Route messages via `brain.think_conversational(chat_id, text, send_fn, adapter="xxx", user_id="...")`.

## Desktop Frontend (v2)

`desktop-v2/` is the active frontend, replacing the legacy `desktop/` (Tauri v1 + React 18).

**Stack**: Tauri v2, React 19, TypeScript, Zustand (state), Tailwind CSS 4, shadcn/ui,
CodeMirror (markdown editing), react-router-dom, Recharts (dashboards), Lucide icons.

**Pages**: ChatPage, DashboardPage, MemoryPage, ModelRoutingPage, PersonaPage, SensingPage,
SettingsPage, TaskCenterPage. Components organized by domain under `src/components/`.

**State**: Zustand stores in `src/stores/` (chat.ts, server.ts). Types in `src/types/`.

## MiniMax-Specific Notes

- Uses Anthropic-compatible endpoint (`api.minimaxi.com/anthropic`). LLM router auto-detects
  via `/anthropic` in base_url and uses `AsyncAnthropic`.
- Temperature must be in (0.0, 1.0] — do not pass `temperature=0`.
- 529 errors (overload) are treated as rate_limit by `_classify_provider_exception()`.
