# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Lapwing — Claude Code Project Guide

## Overview

Lapwing is a 24/7 autonomous AI companion system. She is a virtual person with her own
personality, memories, and growth trajectory — not a bot or assistant framework.

**Tech stack**: Python backend + Tauri/React/TypeScript desktop frontend.
**LLM providers**: MiniMax M2.7 via Anthropic-compatible API, GLM via OpenAI-compatible API.
**Messaging**: Telegram Bot API, QQ (NapCat WebSocket).
**Infrastructure**: PVE server (Xeon E-2174G, 32GB RAM).

## Directory Structure

```
src/
  adapters/      — Messaging platform adapters (QQ)
  api/           — Desktop API server (FastAPI + WebSocket)
  app/           — Application bootstrap (container, Telegram app)
  auth/          — Auth management (OAuth, API keys)
  core/          — Core logic (brain, LLM router, task runtime, evolution)
  heartbeat/     — Periodic background actions
  memory/        — Conversation memory, facts, interests, vector store
  tools/         — Tool registry + execution handlers
config/          — Settings and environment
prompts/         — Markdown prompt templates (hot-reloadable)
data/            — Runtime data (identity, memory, evolution)
desktop/         — Tauri + React frontend
tests/           — Mirrors src/ structure
```

## Architecture

All user messages flow through one path:

```
User message → Brain._prepare_think()
  → Build system prompt (soul + voice + memory + context)
  → LLM with tool schemas → Model selects tools → Tool loop → Reply
```

There is NO agent dispatch layer. All capabilities (weather, search, file ops,
scheduling, memory) are exposed as tool schemas. The LLM decides which tools to call.

## Key Design Decisions

- **LLM-driven routing**: No regex or keyword matching for intent classification.
  The model decides everything through tool calls.
- **Personality via soul.md + voice.md**: soul.md defines WHO she is. voice.md uses
  ✕/✓ contrasts to set behavioral boundaries. Depth-0 injection prevents drift.
- **Files as source of truth**: Identity, memory, and evolution use markdown files,
  not databases, for transparency and editability.
- **Diff-based evolution**: Personality changes accumulate as diffs, not full rewrites.
- **Constitution protection**: Only Kevin can modify the constitution. ConstitutionGuard
  enforces this during evolution.

## Development Conventions

- **Language**: Python source in Chinese comments. CLAUDE.md and commit messages in English.
- **Testing**: `pytest` with `pytest-asyncio`. Tests mirror `src/` structure.
  Run all: `python -m pytest tests/ -x -q`
  Run one file: `python -m pytest tests/core/test_brain.py -x -q`
  Run one test: `python -m pytest tests/core/test_brain.py::test_name -x -q`
- **Prompts**: Markdown files in `prompts/`. Hot-reloadable via `prompt_loader.py`.
  Prompt changes can be deployed without code changes.
- **Import style**: Absolute imports from project root (e.g., `from src.core.brain import ...`).
- **Config**: All config via environment variables in `config/.env`, loaded in `config/settings.py`.
  Feature flags use `FEATURE_ENABLED` pattern. Wave 1 flags: `MEMORY_CRUD_ENABLED`, `AUTO_MEMORY_EXTRACT_ENABLED`, `SELF_SCHEDULE_ENABLED`.

## Running

```bash
# Deploy/restart (always use this script — never run nohup python main.py directly)
bash scripts/deploy.sh

# Auth management
python main.py auth list
python main.py auth login openai-codex
```

## Deployment Rules

- **NEVER** run `nohup python main.py &` directly. Always use `scripts/deploy.sh`.
- The process uses a PID file lock (`data/lapwing.pid`) to prevent multiple instances.
  If startup fails with "另一个 Lapwing 进程正在运行", kill the old process first.

## AppContainer Lifecycle

`AppContainer` (`src/app/container.py`) is the DI root. Startup order:
1. `prepare()` — init DB, wire brain dependencies (KnowledgeManager, VectorStore, SkillManager, etc.)
2. `start(send_fn)` — launch HeartbeatEngine, ReminderScheduler, ChannelManager, LocalApiServer
3. `shutdown()` — reverse teardown

Brain dependencies injected lazily in `_configure_brain_dependencies()`, not in `__init__`.

## LLM Router & Multi-Model Routing

`LLMRouter` (`src/core/llm_router.py`) routes by *purpose slot*:
- `chat` (main_conversation, persona_expression, self_reflection) → `LLM_CHAT_*` env vars
- `tool` (lightweight_judgment, memory_processing, agent_execution) → `LLM_TOOL_*` env vars
- `heartbeat` (heartbeat_proactive) → `NIM_*` env vars

Falls back to generic `LLM_*` vars if slot-specific vars are absent.

## Heartbeat System

`HeartbeatEngine` (`src/core/heartbeat.py`) runs three beat types:
- `fast` — every `HEARTBEAT_FAST_INTERVAL_MINUTES` (default 60 min)
- `slow` — once per day around `HEARTBEAT_SLOW_HOUR` (default 3 AM); includes memory summary
- `minute` — runs every minute (for `always`-mode actions like ReminderScheduler)

Actions implement `HeartbeatAction` ABC. Register in `AppContainer._build_heartbeat()`.
`selection_mode="always"` runs unconditionally; `"decide"` means the LLM picks from candidates.

## Tool System

Tools are `ToolSpec` instances registered in `build_default_tool_registry()` (`src/tools/registry.py`).
Each has a `capability` tag (shell, web, file, memory, schedule, skill, code, verify) used to filter
which tools are exposed to the LLM in a given context. `visibility="internal"` hides a tool from
the model's function list (used for verify_code_result, verify_workspace).

## Data Directory Layout

```
data/
  identity/     — soul.md, constitution.md (immutable by Lapwing)
  memory/       — KEVIN.md, SELF.md, journal/, conversations/summaries/, sessions/
  evolution/    — rules.md, interests.md, changelog.md (diff-based)
  lapwing.pid   — process lock file
  lapwing.db    — SQLite conversation history
```

## MiniMax-Specific Notes

- MiniMax uses the Anthropic-compatible endpoint (`api.minimaxi.com/anthropic`). The LLM
  router auto-detects this via `/anthropic` in the base_url and uses `AsyncAnthropic`.
- `tool_choice`, `tools`, `thinking` blocks all work natively via the Anthropic SDK.
- Temperature must be in (0.0, 1.0] — do not pass `temperature=0`.
- 529 errors (overload) are treated as rate_limit by `_classify_provider_exception()`.
