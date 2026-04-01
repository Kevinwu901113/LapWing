# Lapwing — Claude Code Project Guide

## Overview

Lapwing is a 24/7 autonomous AI companion system. She is a virtual person with her own
personality, memories, and growth trajectory — not a bot or assistant framework.

**Tech stack**: Python backend + Tauri/React/TypeScript desktop frontend.
**LLM providers**: MiniMax M2.7 / GLM via OpenAI-compatible API.
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
  Run: `python -m pytest tests/ -x -q`
- **Prompts**: Markdown files in `prompts/`. Hot-reloadable via `prompt_loader.py`.
  Prompt changes can be deployed without code changes.
- **Import style**: Absolute imports from project root (e.g., `from src.core.brain import ...`).
- **Settings**: All config via environment variables, loaded in `config/settings.py`.
  Feature flags use `FEATURE_ENABLED` pattern.

## Running

```bash
# Start with Telegram (primary)
python main.py

# Auth management
python main.py auth list
python main.py auth login openai-codex
```

## MiniMax-Specific Notes

- `_merge_messages_for_minimax()` must preserve `tool_calls` and `tool_call_id` fields.
- MiniMax pops `tool_choice` from requests. Anti-think injection and `response_format`
  are used as workarounds for structured output.
- 529 errors require separate exponential backoff from 429.
