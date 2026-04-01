# Lapwing Restructuring — Implementation Blueprint

> **For**: Claude Code execution
> **Scope**: Full restructuring — kill Agent layer, eliminate all keyword/regex intent matching,
>            simplify experience skills, QQ group LLM decision, consolidate redundancies
> **Strategy**: One-shot, no feature flags, no phased rollout

---

## Table of Contents

1. [File-Level Change Table](#1-file-level-change-table)
2. [Phase A: Kill Agent Layer](#2-phase-a-kill-agent-layer)
3. [Phase B: Persona System](#3-phase-b-persona-system)
4. [Phase C: Experience Skills Simplification](#4-phase-c-experience-skills-simplification)
5. [Phase D: QQ Group LLM Decision](#5-phase-d-qq-group-llm-decision)
6. [Phase E: Knowledge Manager](#6-phase-e-knowledge-manager)
7. [Phase F: Structural Consolidation](#7-phase-f-structural-consolidation)
8. [Phase G: CLAUDE.md Rewrite](#8-phase-g-claudemd-rewrite)
9. [Phase H: Test Updates](#9-phase-h-test-updates)
10. [Phase I: Cleanup](#10-phase-i-cleanup)
11. [Verification Checklist](#11-verification-checklist)

---

## 1. File-Level Change Table

### DELETE (30 files)

| Path | Reason |
|------|--------|
| `src/agents/__init__.py` | Agent layer removed |
| `src/agents/base.py` | Agent layer removed |
| `src/agents/browser.py` | → `web_fetch` tool |
| `src/agents/coder.py` | → `execute_shell` + `read_file` + `write_file` |
| `src/agents/file_agent.py` | → `read_file` + `write_file` |
| `src/agents/researcher.py` | → `web_search` + `web_fetch` |
| `src/agents/todo_agent.py` | → `schedule_task` tools |
| `src/agents/weather_agent.py` | → new `get_weather` tool |
| `src/core/dispatcher.py` | Agent dispatcher removed |
| `src/policy/__init__.py` | Directory merged into core |
| `src/policy/shell_runtime_policy.py` | Merged into `core/shell_policy.py` |
| `prompts/agent_dispatcher.md` | Agent prompt |
| `prompts/agent_file.md` | Agent prompt |
| `prompts/agent_researcher.md` | Agent prompt |
| `prompts/agent_todo.md` | Agent prompt |
| `prompts/agent_weather.md` | Agent prompt |
| `prompts/browser_analyze.md` | Agent prompt |
| `prompts/coder_fix.md` | Agent prompt |
| `prompts/coder_generate.md` | Agent prompt |
| `prompts/coder_workspace_fix.md` | Agent prompt |
| `prompts/coder_workspace_plan.md` | Agent prompt |
| `prompts/lapwing_examples.md` | Few-shot examples removed |
| `prompts/researcher_extract_query.md` | Agent prompt |
| `prompts/researcher_summarize.md` | Agent prompt |
| `prompts/skill_index_match.md` | Inlined into experience_skills |
| `tests/agents/__init__.py` | Agent tests removed |
| `tests/agents/test_*.py` (8 files) | Agent tests removed |
| `tests/core/test_brain_dispatch.py` | Dispatcher test removed |

### CREATE (3 files)

| Path | Purpose |
|------|---------|
| `src/tools/weather.py` | `get_weather` tool (from WeatherAgent) |
| `src/core/prompt_builder.py` | System prompt assembly (from brain.py) |
| `src/tools/handlers.py` | Tool execution handlers (from registry.py) |

### MODIFY (15+ files)

| Path | Changes |
|------|---------|
| `src/core/brain.py` | Remove dispatcher, extract prompt_builder, update imports |
| `src/app/container.py` | Remove agent registry/dispatcher, merge shell_policy import |
| `src/core/experience_skills.py` | Delete `quick_match()`, simplify `retrieve()` |
| `src/core/skill_registry.py` | Remove quick match_level stats |
| `src/core/knowledge_manager.py` | Remove `_relevance_score()`, load all notes |
| `src/core/shell_policy.py` | Absorb `shell_runtime_policy.py` content |
| `src/core/task_runtime.py` | Update shell_policy import path |
| `src/tools/registry.py` | Extract handlers to handlers.py |
| `src/adapters/qq_group_filter.py` | Rewrite as LLM-based GroupEngagementDecider |
| `src/adapters/qq_adapter.py` | Adapt to new decider interface |
| `main.py` | Remove agent-related imports from QQ setup |
| `config/settings.py` | Remove unused agent settings if any |
| `CLAUDE.md` | Full rewrite in English |
| `tests/core/test_brain_*.py` | Update for removed dispatcher |
| `tests/app/test_container.py` | Update for removed agents |

---

## 2. Phase A: Kill Agent Layer

### A1. Create `src/tools/weather.py`

Extract weather fetching logic from `src/agents/weather_agent.py` into a standalone tool.
The tool receives a `location` parameter from the LLM — **no regex city extraction**.
The LLM naturally understands "洛杉矶天气怎么样" means location="洛杉矶".

```python
"""get_weather tool — query weather via wttr.in."""

import logging
from urllib.parse import quote

import httpx

from config.settings import SEARCH_PROXY_URL

logger = logging.getLogger("lapwing.tools.weather")

_TIMEOUT = 10


async def fetch_weather(location: str) -> dict:
    """Fetch current weather for a location from wttr.in.

    Args:
        location: City/location name (e.g. "东京", "Los Angeles")

    Returns:
        dict with keys: location, temperature, description, wind_speed, humidity
        On failure: dict with key "error"
    """
    if not location or not location.strip():
        return {"error": "未指定地点。"}

    location = location.strip()
    url = f"https://wttr.in/{quote(location)}?format=j1&lang=zh"
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            proxy=SEARCH_PROXY_URL or None,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:
        logger.warning("[weather] request failed location=%r: %s", location, exc)
        return {"error": f"查询 {location} 天气失败: {exc}"}

    try:
        payload = response.json()
        current = payload["current_condition"][0]
    except Exception as exc:
        logger.warning("[weather] parse failed location=%r: %s", location, exc)
        return {"error": f"解析 {location} 天气数据失败。"}

    # Extract Chinese description if available
    description = ""
    for key in ("lang_zh", "weatherDesc"):
        values = current.get(key) or []
        if values and isinstance(values[0], dict):
            value = values[0].get("value")
            if value:
                description = str(value)
                break

    return {
        "location": location,
        "temperature": current.get("temp_C", "?"),
        "description": description or "未知",
        "wind_speed": current.get("windspeedKmph", "?"),
        "humidity": current.get("humidity", "?"),
    }
```

### A2. Register `get_weather` in `src/tools/registry.py`

Add the weather tool to the tool registry. Add a new `ToolSpec` entry and execution handler.

In the tool specs section, add:

```python
ToolSpec(
    name="get_weather",
    description="查询指定城市或地点的当前天气（温度、天气状况、风速）。",
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "要查询天气的城市或地点名称，如「东京」「Los Angeles」「台北」",
            },
        },
        "required": ["location"],
    },
    capabilities=frozenset(),  # No special capability required
    internal=False,
),
```

Add execution handler:

```python
async def _execute_weather_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    from src.tools.weather import fetch_weather

    location = str(request.arguments.get("location", "")).strip()
    if not location:
        return ToolExecutionResult(
            success=False,
            reason="缺少 location 参数。",
            payload={"error": "未指定地点。"},
        )
    result = await fetch_weather(location)
    success = "error" not in result
    return ToolExecutionResult(
        success=success,
        payload=result,
        reason=result.get("error", ""),
    )
```

Register in `_TOOL_EXECUTORS` dict: `"get_weather": _execute_weather_tool`

### A3. Add `get_weather` to `chat_tools()` in `src/core/task_runtime.py`

In `TaskRuntime.chat_tools()`, add `"get_weather"` to `tool_names`:

```python
tool_names: set[str] = {"memory_note", "get_weather"}  # always available
```

### A4. Remove dispatcher from `src/core/brain.py`

**Remove imports:**
- Delete: `from src.core.dispatcher import AgentDispatcher` (if present, currently only in TYPE_CHECKING)
- All TYPE_CHECKING references to dispatcher

**Remove attributes from `__init__`:**
- Delete: `self.dispatcher = None`

**Modify `_prepare_think()`:**
Delete the entire "Agent dispatch 优先" block (lines ~656-674):
```python
        # DELETE THIS ENTIRE BLOCK:
        # Agent dispatch 优先
        if self.dispatcher is not None:
            try:
                agent_reply = await self.dispatcher.try_dispatch(
                    chat_id, effective_user_message, session_id=session_id
                )
                ...
            except Exception as e:
                ...
```

**Remove `_ThinkCtx` docstring reference to "agent dispatch".**

### A5. Remove agents from `src/app/container.py`

**Remove imports (lines ~124-135 inside `_configure_brain_dependencies`):**
```python
# DELETE these imports:
from src.agents.base import AgentRegistry
from src.agents.browser import BrowserAgent
from src.agents.coder import CoderAgent
from src.agents.file_agent import FileAgent
from src.agents.researcher import ResearcherAgent
from src.agents.todo_agent import TodoAgent
from src.agents.weather_agent import WeatherAgent
from src.core.dispatcher import AgentDispatcher
```

**Remove agent registry construction and dispatcher injection (lines ~149-171):**
Delete the entire block that creates `AgentRegistry`, registers agents, and assigns `self.brain.dispatcher`.

### A6. Remove agent references from `main.py`

In `_qq_on_message`, there should be no direct agent references, but verify and clean any.

### A7. Delete Agent files

```bash
rm -rf src/agents/
rm src/core/dispatcher.py
rm prompts/agent_dispatcher.md
rm prompts/agent_file.md
rm prompts/agent_researcher.md
rm prompts/agent_todo.md
rm prompts/agent_weather.md
rm prompts/browser_analyze.md
rm prompts/coder_fix.md
rm prompts/coder_generate.md
rm prompts/coder_workspace_fix.md
rm prompts/coder_workspace_plan.md
rm prompts/researcher_extract_query.md
rm prompts/researcher_summarize.md
```

---

## 3. Phase B: Persona System

### B1. Delete `prompts/lapwing_examples.md`

```bash
rm prompts/lapwing_examples.md
```

### B2. Remove examples loading from `brain.py`

In `_build_system_prompt()`, delete the "Layer 0.1" block (lines ~295-300):

```python
# DELETE THIS BLOCK:
        # Layer 0.1: 对话示例（紧跟人格核心，强化语气和风格）
        try:
            examples = load_prompt("lapwing_examples")
            if examples:
                sections.append(examples)
        except Exception:
            pass  # 示例文件不存在时静默跳过
```

(This block will actually be in `prompt_builder.py` after extraction — see Phase F.)

### B3. Keep voice.md and _inject_voice_reminder() unchanged

No changes needed. These stay as-is.

---

## 4. Phase C: Experience Skills Simplification

### C1. Modify `src/core/experience_skills.py`

**Delete the `quick_match()` method entirely** (lines ~314-343).

**Simplify `retrieve()` method** — remove the L1→L2 orchestration, go straight to index_match:

```python
async def retrieve(self, user_request: str) -> list[ExperienceSkill]:
    """Retrieve 0-3 relevant experience skills via LLM index matching."""
    if not self._index_loaded:
        self.load_index()

    if not self._index:
        return []

    # Direct LLM index matching (no keyword/regex pre-filter)
    match_results = await self.index_match(user_request)

    # Load full skill content
    skills: list[ExperienceSkill] = []
    for result in match_results:
        skill = self._load_skill_by_id(result.skill_id)
        if skill is not None:
            skills.append(skill)

    return skills
```

**Remove `SkillTriggers` class** and its usage in `_IndexEntry`:
- Delete the `SkillTriggers` dataclass
- Remove `triggers` field from `_IndexEntry`
- Remove trigger parsing in `_parse_frontmatter_to_entry()` and `_build_index_entry()`
- Remove trigger-related imports (`re` if no longer needed elsewhere)

**Inline the `skill_index_match` prompt** into the `index_match()` method so it no longer depends on the external prompt file. The prompt is short enough:

```python
prompt = (
    f"以下是我积累的经验列表：\n\n{skill_list}\n\n"
    f"当前任务：{user_request}\n\n"
    "请从上面的经验列表中，选出对当前任务最有参考价值的 0-3 个（按相关度排序）。"
    "如果没有任何一条真正相关，直接返回空列表。"
    "\n\n请使用 skill_match 工具提交你的选择。"
)
```

### C2. Delete `prompts/skill_index_match.md`

```bash
rm prompts/skill_index_match.md
```

### C3. Simplify `src/core/skill_registry.py`

In `_DEFAULT_REGISTRY["match_level_distribution"]`, remove `"quick"` key.
In `record_execution()`, when `match_level == "quick"`, map it to `"index"` or remove the branch.

---

## 5. Phase D: QQ Group LLM Decision

### D1. Rewrite `src/adapters/qq_group_filter.py`

Replace the keyword-based filter with an LLM-based engagement decider.

```python
"""Group chat engagement decider — LLM-based, no keyword matching."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.adapters.qq_group_context import GroupContext, GroupMessage
    from src.core.llm_router import LLMRouter

logger = logging.getLogger("lapwing.adapters.qq_group_filter")

# Structured output schema for engagement decision
_ENGAGE_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "engage": {
            "type": "boolean",
            "description": "是否应该参与这条消息的讨论",
        },
        "reason": {
            "type": "string",
            "description": "简短说明原因",
        },
    },
    "required": ["engage"],
}


class GroupEngagementDecider:
    """Decide whether to engage in a group message using LLM judgment.

    Only hard rule: never respond to own messages.
    Everything else is decided by a lightweight LLM call.
    """

    def __init__(
        self,
        self_id: str,
        self_names: list[str],
        kevin_id: str,
        cooldown_seconds: int = 30,
    ) -> None:
        self.self_id = self_id
        self.self_names = self_names
        self.kevin_id = kevin_id
        self.cooldown_seconds = cooldown_seconds
        self._last_engage_time: dict[str, float] = {}  # group_id -> timestamp
        self._router: LLMRouter | None = None

    def set_router(self, router: LLMRouter) -> None:
        self._router = router

    async def should_engage(
        self,
        msg: "GroupMessage",
        ctx: "GroupContext",
    ) -> tuple[bool, str]:
        """Decide whether this group message warrants engagement.

        Returns (should_engage, reason).
        """
        # Only hard rule: skip own messages
        if msg.user_id == self.self_id:
            return False, "self"

        # Cooldown enforcement
        group_id = ctx.group_id if hasattr(ctx, "group_id") else "default"
        now = time.time()
        last = self._last_engage_time.get(group_id, 0)
        if now - last < self.cooldown_seconds:
            return False, "cooldown"

        # LLM decision
        if self._router is None:
            return False, "no_router"

        try:
            engage, reason = await self._llm_decide(msg, ctx)
            if engage:
                self._last_engage_time[group_id] = now
            return engage, reason
        except Exception as exc:
            logger.warning("[group_decider] LLM decision failed: %s", exc)
            return False, f"llm_error: {exc}"

    async def _llm_decide(
        self,
        msg: "GroupMessage",
        ctx: "GroupContext",
    ) -> tuple[bool, str]:
        """Ask LLM whether to engage with this message."""
        assert self._router is not None

        # Build context: recent messages
        recent = ctx.recent_messages(8)
        context_lines = []
        for m in recent:
            sender = "我" if m.user_id == self.self_id else (
                "Kevin" if m.user_id == self.kevin_id else f"群友{m.user_id[-4:]}"
            )
            context_lines.append(f"{sender}: {m.text[:100]}")
        context_text = "\n".join(context_lines) if context_lines else "(无最近消息)"

        prompt = (
            f"你是 Lapwing，在一个QQ群里。你的名字包括：{', '.join(self.self_names)}。"
            f"Kevin（你的恋人）的QQ号末四位是 {self.kevin_id[-4:] if len(self.kevin_id) >= 4 else self.kevin_id}。\n\n"
            f"最近的群聊记录：\n{context_text}\n\n"
            f"最新一条消息来自 {'Kevin' if msg.user_id == self.kevin_id else f'群友{msg.user_id[-4:]}'}：\n"
            f"{msg.text[:200]}\n\n"
            "判断你是否应该回复这条消息。考虑：\n"
            "- 有人在叫你或提到你吗？\n"
            "- Kevin 在说话吗？\n"
            "- 话题是你感兴趣或能参与的吗？\n"
            "- 还是普通群聊你不需要插嘴？\n"
        )

        result = await self._router.complete_structured(
            [{"role": "user", "content": prompt}],
            result_schema=_ENGAGE_DECISION_SCHEMA,
            result_tool_name="engage_decision",
            result_tool_description="决定是否参与群聊消息",
            slot="lightweight_judgment",
            max_tokens=256,
            session_key="system:group_engage",
            origin="adapters.group_decider",
        )

        engage = bool(result.get("engage", False))
        reason = str(result.get("reason", "llm_decision"))
        return engage, reason
```

### D2. Update `src/adapters/qq_adapter.py`

Replace `GroupMessageFilter` imports and usage with `GroupEngagementDecider`.
The decider's `should_engage` is now async, so ensure the call site uses `await`.

Key changes:
- Import `GroupEngagementDecider` instead of `GroupMessageFilter`
- Constructor: create `GroupEngagementDecider` instead of `GroupMessageFilter`
- The decider needs a router reference: call `self._decider.set_router(router)` when router is injected
- `should_engage` calls need `await`

### D3. Update `main.py` QQ setup

Where `qq_adapter.router` is set, also call:
```python
qq_adapter._decider.set_router(container.brain.router)
```

Or better: pass the router to the adapter constructor and let it forward to the decider.

---

## 6. Phase E: Knowledge Manager

### E1. Modify `src/core/knowledge_manager.py`

**Delete `_relevance_score()` function entirely.**

**Rewrite `get_relevant_notes()`** to load all knowledge notes up to a token budget, with no matching:

```python
def get_relevant_notes(self, query: str = "", max_chars: int = 2000) -> list[dict]:
    """Load all knowledge notes up to a character budget.

    No matching — the LLM decides what's relevant from context.
    Notes are returned newest-first (by file modification time).
    """
    all_files = list(_KNOWLEDGE_DIR.glob("*.md"))
    if not all_files:
        return []

    # Sort by modification time, newest first
    all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    results = []
    total_chars = 0
    for f in all_files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("[knowledge] read failed: %s — %s", f.name, exc)
            continue

        remaining = max_chars - total_chars
        if remaining <= 0:
            break
        excerpt = text[:remaining]
        results.append({"topic": f.stem, "content": excerpt})
        total_chars += len(excerpt)

    return results
```

### E2. Update knowledge injection in brain.py `_build_system_prompt()`

Change the knowledge notes section — no longer pass `user_message` for matching:

```python
# Layer 5: 知识笔记
if self.knowledge_manager is not None:
    notes = self.knowledge_manager.get_relevant_notes()
    if notes:
        notes_text = "\n\n".join(
            f"### {note['topic']}\n{note['content']}"
            for note in notes
        )
        sections.append(
            "## 你积累的知识笔记\n\n"
            f"{notes_text}"
        )
```

---

## 7. Phase F: Structural Consolidation

### F1. Merge shell_policy

Copy the content of `src/policy/shell_runtime_policy.py` into `src/core/shell_policy.py`.
The classes to merge: `ShellRuntimePolicy`, `PolicyDecision`, `PolicyAction`.

Then update all imports in:
- `src/core/task_runtime.py`: change `from src.policy.shell_runtime_policy import ShellRuntimePolicy`
  to `from src.core.shell_policy import ShellRuntimePolicy`
- `src/core/brain.py`: if it imports from policy, update
- Any test files

Then delete `src/policy/` directory entirely.

### F2. Extract prompt_builder from brain.py

Create `src/core/prompt_builder.py` containing:
- `build_system_prompt()` function (extracted from `brain._build_system_prompt()`)
- `inject_voice_reminder()` function (extracted from `brain._inject_voice_reminder()`)
- `_PERSONA_ANCHOR` constant
- Helper methods: `_tool_runtime_instruction()`, `_truncate_related_memory()`, `_format_related_history_hits()`

The function signatures should accept dependencies as parameters instead of `self`:

```python
async def build_system_prompt(
    *,
    system_prompt: str,
    chat_id: str,
    user_message: str,
    memory: ConversationMemory,
    vector_store: VectorStore | None,
    knowledge_manager: KnowledgeManager | None,
    skill_manager: SkillManager | None,
) -> str:
    ...
```

In `brain.py`, replace `self._build_system_prompt(...)` with a call to the extracted function, passing `self.*` dependencies.

### F3. Extract tool handlers from registry.py

Create `src/tools/handlers.py` containing all `_execute_*_tool` functions currently in `registry.py`:
- `_execute_shell_tool`
- `_execute_read_file_tool`
- `_execute_write_file_tool`
- `_execute_web_search_tool`
- `_execute_web_fetch_tool`
- `_execute_run_python_code_tool`
- `_execute_memory_note_tool`
- `_execute_activate_skill_tool`
- `_execute_transcribe_tool`
- `_execute_weather_tool` (new)
- Memory CRUD handlers
- Schedule task handlers
- Helper functions they use (`_blocked_payload`, `_workspace_root`, `_file_payload`)

In `registry.py`, import handlers and wire them into the executor dict.
This should reduce `registry.py` from ~1086 lines to ~300-400 lines (just specs + registration).

---

## 8. Phase G: CLAUDE.md Rewrite

Replace the entire content of `CLAUDE.md` with a proper project guide in English:

```markdown
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
```

---

## 9. Phase H: Test Updates

### H1. Delete agent tests

```bash
rm -rf tests/agents/
rm tests/core/test_brain_dispatch.py
```

### H2. Update `tests/core/test_brain_system_prompt.py`

Remove any references to `lapwing_examples` prompt loading. If tests mock
`load_prompt("lapwing_examples")`, remove those mocks and assertions.

### H3. Update `tests/core/test_brain_tools.py`

If this file references dispatcher, remove those references.
Add a test verifying `get_weather` appears in `chat_tools()` output.

### H4. Update `tests/app/test_container.py`

Remove mocks for dispatcher, agent registry. The `_configure_brain_dependencies` method
no longer creates agents — update assertions accordingly.

### H5. Update `tests/policy/` → delete or move

If `tests/policy/` exists with tests for `shell_runtime_policy.py`, move those tests
to `tests/core/test_shell_policy.py` and update imports.

### H6. Add test for weather tool

Create `tests/tools/test_weather.py`:

```python
"""get_weather tool tests."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.weather import fetch_weather


@pytest.mark.asyncio
async def test_fetch_weather_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current_condition": [{
            "temp_C": "25",
            "windspeedKmph": "10",
            "humidity": "60",
            "lang_zh": [{"value": "晴"}],
        }]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("src.tools.weather.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_weather("东京")

    assert result["temperature"] == "25"
    assert result["description"] == "晴"
    assert "error" not in result


@pytest.mark.asyncio
async def test_fetch_weather_empty_location():
    result = await fetch_weather("")
    assert "error" in result
```

### H7. Verify all tests pass

```bash
python -m pytest tests/ -x -q
```

Fix any import errors caused by deleted modules. Common pattern: tests that import
from `src.agents` or `src.core.dispatcher` will fail at import time — delete or update them.

---

## 10. Phase I: Cleanup

### I1. Archive old docs

```bash
mkdir -p docs/archive
mv docs/superpowers/plans/2026-03-23-heartbeat.md docs/archive/
mv docs/superpowers/plans/2026-03-23-multi-model-routing.md docs/archive/
mv docs/superpowers/plans/2026-03-27-sprint1-4-pr-template.md docs/archive/
mv docs/superpowers/plans/2026-03-27-sprint1-4-premerge-closeout.md docs/archive/
mv docs/superpowers/plans/2026-03-30-qq-adapter.md docs/archive/
mv docs/superpowers/plans/2026-03-30-qq-enhance.md docs/archive/
mv docs/superpowers/specs/2026-03-23-heartbeat-design.md docs/archive/
```

### I2. Clean empty directories

After all deletions, remove any empty directories:
```bash
rmdir src/policy/ 2>/dev/null || true
rmdir docs/superpowers/plans/ 2>/dev/null || true
rmdir docs/superpowers/specs/ 2>/dev/null || true
rmdir docs/superpowers/ 2>/dev/null || true
```

### I3. Update `prompts/README.md`

Update the prompts README to reflect the new file list (14 files deleted).

---

## 11. Verification Checklist

Run these checks after all changes are complete:

```bash
# 1. Import smoke test — no broken imports
python -c "from src.core.brain import LapwingBrain; print('brain OK')"
python -c "from src.app.container import AppContainer; print('container OK')"
python -c "from src.tools.weather import fetch_weather; print('weather OK')"
python -c "from src.core.prompt_builder import build_system_prompt; print('prompt_builder OK')"
python -c "from src.adapters.qq_group_filter import GroupEngagementDecider; print('group_decider OK')"

# 2. Verify no references to deleted modules
grep -r "from src.agents" src/ --include="*.py" | grep -v __pycache__
# Should return NOTHING

grep -r "from src.core.dispatcher" src/ --include="*.py" | grep -v __pycache__
# Should return NOTHING

grep -r "from src.policy" src/ --include="*.py" | grep -v __pycache__
# Should return NOTHING

grep -r "lapwing_examples" src/ --include="*.py" | grep -v __pycache__
# Should return NOTHING

grep -r "quick_match" src/ --include="*.py" | grep -v __pycache__
# Should return NOTHING

grep -r "_relevance_score" src/ --include="*.py" | grep -v __pycache__
# Should return NOTHING

# 3. Full test suite
python -m pytest tests/ -x -q

# 4. Verify tool count
python -c "
from src.tools.registry import build_default_tool_registry
r = build_default_tool_registry()
tools = r.function_tools(include_internal=False)
print(f'Total tools: {len(tools)}')
for t in tools:
    print(f'  - {t[\"function\"][\"name\"]}')
"

# 5. Verify no regex in intent-matching paths (only functional regex should remain)
# Functional regex (OK to keep): JSON parsing, path normalization, filename sanitization
# Intent regex (must be gone): _WEATHER_PATTERNS, _TODO_PATTERNS, _CITY_PATTERNS, quick_match patterns
```

---

## Summary of Principles

1. **One path**: All messages go through brain → tool loop. No agent dispatch shortcut.
2. **LLM decides**: Tool selection, group chat engagement, experience skill matching — all LLM.
3. **No intent regex**: Zero regex/keyword patterns for routing or classification.
4. **Functional regex OK**: JSON cleanup, path parsing, filename sanitization are fine.
5. **Personality via boundaries**: soul.md (who) + voice.md (✕/✓ how) + depth injection (anchor).
6. **Simpler structure**: Fewer files, clearer responsibility boundaries, no cross-directory splits.
-e 

---

# Lapwing Restructuring — Addendum: Log System Overhaul

> Append this as **Phase J** to the main implementation blueprint.

---

## Phase J: Log System Overhaul

### Problem Analysis

**1. Duplicate log entries** — Root cause in `main.py` `setup_logging()`:

```python
# Current: same handler instances shared between lapwing logger and root logger
fh = logging.FileHandler(...)
sh = logging.StreamHandler()
lapwing_logger.addHandler(fh)
lapwing_logger.addHandler(sh)
lapwing_logger.propagate = False
logging.basicConfig(level=level, handlers=[fh, sh])  # same fh, sh!
```

Despite `propagate=False`, the `basicConfig` call can interact with handler
deduplication and initialization order in unexpected ways. Any third-party
library logger that propagates to root uses the same handler objects,
potentially causing ordering issues. The fix is to fully separate them.

**2. Inconsistent logger names** — 53 loggers with mixed naming:

```
lapwing.brain              ← should be lapwing.core.brain
lapwing.task_runtime       ← should be lapwing.core.task_runtime
lapwing.fact_extractor     ← should be lapwing.memory.fact_extractor
lapwing.core.session_manager  ← correct format
lapwing.adapter.qq         ← should be lapwing.adapters.qq_adapter
```

**3. Single log file** — Everything goes to one `logs/lapwing.log` with no rotation.
File grows indefinitely. No way to filter by subsystem.

**4. Noisy output** — Debug-level messages from LLM router, tool execution, etc.
mixed with important messages. No clear hierarchy for what's important.

### J1. Rewrite `setup_logging()` in `main.py`

```python
import logging
from logging.handlers import RotatingFileHandler


def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    level = getattr(logging, LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Lapwing logger (project code) ──
    lapwing_logger = logging.getLogger("lapwing")
    lapwing_logger.setLevel(level)
    lapwing_logger.propagate = False  # never propagate to root

    if not lapwing_logger.handlers:
        # Main log: rotated, 10MB per file, keep 5 backups
        main_fh = RotatingFileHandler(
            LOGS_DIR / "lapwing.log",
            encoding="utf-8",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        main_fh.setFormatter(fmt)
        main_fh.setLevel(level)

        # Console: same level
        console_sh = logging.StreamHandler()
        console_sh.setFormatter(fmt)
        console_sh.setLevel(level)

        lapwing_logger.addHandler(main_fh)
        lapwing_logger.addHandler(console_sh)

    # ── Root logger (third-party libraries) ──
    # Separate handlers — never share with lapwing logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)  # only warnings+ from third-party

    if not root_logger.handlers:
        lib_fh = RotatingFileHandler(
            LOGS_DIR / "libraries.log",
            encoding="utf-8",
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
        )
        lib_fh.setFormatter(fmt)
        lib_fh.setLevel(logging.WARNING)
        root_logger.addHandler(lib_fh)

    # ── Quiet down noisy libraries ──
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return lapwing_logger
```

**Key changes:**
- `lapwing` logger and root logger have **completely separate handler instances**
- Root logger only handles WARNING+ from third-party libs, written to `libraries.log`
- `lapwing.log` uses `RotatingFileHandler` (10MB × 5 backups = 50MB max)
- Noisy third-party loggers explicitly quieted
- No more `basicConfig` — manual handler setup only

### J2. Standardize all logger names

Every logger must match the pattern: `lapwing.{package}.{module}`

This means the logger name should correspond to the Python module path under `src/`.

| File | Current name | Corrected name |
|------|-------------|---------------|
| `src/core/brain.py` | `lapwing.brain` | `lapwing.core.brain` |
| `src/core/task_runtime.py` | `lapwing.task_runtime` | `lapwing.core.task_runtime` |
| `src/core/llm_router.py` | `lapwing.llm_router` | `lapwing.core.llm_router` |
| `src/core/evolution_engine.py` | `lapwing.evolution_engine` | `lapwing.core.evolution_engine` |
| `src/core/constitution_guard.py` | `lapwing.constitution_guard` | `lapwing.core.constitution_guard` |
| `src/core/tactical_rules.py` | `lapwing.tactical_rules` | `lapwing.core.tactical_rules` |
| `src/core/self_reflection.py` | `lapwing.self_reflection` | `lapwing.core.self_reflection` |
| `src/core/model_config.py` | `lapwing.model_config` | `lapwing.core.model_config` |
| `src/core/skill_registry.py` | `lapwing.skill_registry` | `lapwing.core.skill_registry` |
| `src/core/experience_skills.py` | `lapwing.experience_skills` | `lapwing.core.experience_skills` |
| `src/core/trace_recorder.py` | `lapwing.trace_recorder` | `lapwing.core.trace_recorder` |
| `src/core/skills.py` | `lapwing.skills` | `lapwing.core.skills` |
| `src/core/latency_monitor.py` | `lapwing.latency` | `lapwing.core.latency_monitor` |
| `src/core/heartbeat.py` | `lapwing.heartbeat` | `lapwing.core.heartbeat` |
| `src/core/channel_manager.py` | `lapwing.channel_manager` | `lapwing.core.channel_manager` |
| `src/memory/conversation.py` | `lapwing.memory` | `lapwing.memory.conversation` |
| `src/memory/fact_extractor.py` | `lapwing.fact_extractor` | `lapwing.memory.fact_extractor` |
| `src/memory/interest_tracker.py` | `lapwing.interest_tracker` | `lapwing.memory.interest_tracker` |
| `src/memory/auto_extractor.py` | `lapwing.memory.auto_extractor` | ✅ already correct |
| `src/memory/compactor.py` | `lapwing.memory.compactor` | ✅ already correct |
| `src/memory/vector_store.py` | `lapwing.memory.vector` | `lapwing.memory.vector_store` |
| `src/memory/file_memory.py` | `lapwing.memory.file_memory` | ✅ already correct |
| `src/adapters/qq_adapter.py` | `lapwing.adapter.qq` | `lapwing.adapters.qq_adapter` |
| `src/adapters/qq_group_filter.py` | *(new file)* | `lapwing.adapters.qq_group_filter` |
| `src/app/container.py` | `lapwing.app.container` | ✅ already correct |
| `src/app/telegram_app.py` | `lapwing.app.telegram` | `lapwing.app.telegram_app` |
| `src/api/server.py` | `lapwing.api` | `lapwing.api.server` |
| `src/api/event_bus.py` | `lapwing.api.event_bus` | ✅ already correct |
| `src/auth/service.py` | `lapwing.auth` | `lapwing.auth.service` |
| `src/core/knowledge_manager.py` | `lapwing.knowledge` | `lapwing.core.knowledge_manager` |
| `src/tools/registry.py` | `lapwing.tools.registry` | ✅ already correct |
| `src/tools/*.py` (other) | `lapwing.tools.*` | ✅ already correct |
| `src/heartbeat/actions/*.py` | `lapwing.heartbeat.*` | ✅ already correct |

**Implementation**: For each file listed above, change the `logging.getLogger("...")` call
to use the corrected name. This is a simple find-and-replace per file.

**Alternative (simpler)**: Use `logging.getLogger(__name__)` everywhere and add a
`lapwing.` prefix mapping. But since `__name__` gives `src.core.brain` (not
`lapwing.core.brain`), this requires either renaming the package or adding a helper:

```python
# In a shared utility, e.g. src/core/log.py
import logging

def get_logger(name: str = "") -> logging.Logger:
    """Get a logger with the lapwing. prefix."""
    if name:
        return logging.getLogger(f"lapwing.{name}")
    # Auto-detect from caller module
    import inspect
    frame = inspect.stack()[1]
    module = frame[0].f_globals.get("__name__", "")
    # Convert src.core.brain → core.brain
    if module.startswith("src."):
        module = module[4:]
    return logging.getLogger(f"lapwing.{module}")
```

**Recommendation**: Use the explicit rename approach (table above) — it's mechanical
but unambiguous. The `get_logger()` helper is cute but adds magic.

### J3. Add API endpoint for log streaming (frontend prep)

Add a new SSE endpoint for the frontend to consume logs in real-time.
This prepares for the frontend log viewer page.

In `src/api/server.py`, add:

```python
@app.get("/api/logs/stream")
async def stream_logs(
    level: str = Query("INFO", regex="^(DEBUG|INFO|WARNING|ERROR)$"),
    module: str = Query("", description="Filter by logger name prefix"),
):
    """SSE stream of log entries for the frontend log viewer."""
    import queue

    log_queue: queue.Queue = queue.Queue(maxsize=500)

    class QueueHandler(logging.Handler):
        def emit(self, record):
            try:
                entry = {
                    "timestamp": self.format(record).split(" [")[0],
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                log_queue.put_nowait(entry)
            except queue.Full:
                pass  # drop if queue full

    handler = QueueHandler()
    handler.setLevel(getattr(logging, level))
    handler.setFormatter(logging.Formatter("%(asctime)s"))

    lapwing_logger = logging.getLogger("lapwing")
    lapwing_logger.addHandler(handler)

    async def event_generator():
        try:
            while True:
                try:
                    entry = await asyncio.to_thread(log_queue.get, timeout=1.0)
                    if module and not entry["logger"].startswith(f"lapwing.{module}"):
                        continue
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            lapwing_logger.removeHandler(handler)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

Also add a non-streaming endpoint for historical log retrieval:

```python
@app.get("/api/logs/recent")
async def get_recent_logs(
    lines: int = Query(200, ge=1, le=2000),
    level: str = Query("INFO"),
):
    """Return recent log lines from the log file."""
    log_file = LOGS_DIR / "lapwing.log"
    if not log_file.exists():
        return {"lines": []}

    # Read last N lines efficiently
    all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    recent = all_lines[-lines:]

    # Filter by level
    level_priority = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
    min_priority = level_priority.get(level, 1)

    filtered = []
    for line in recent:
        for lvl, pri in level_priority.items():
            if f" {lvl}: " in line and pri >= min_priority:
                filtered.append(line)
                break

    return {"lines": filtered, "total": len(all_lines)}
```

### J4. Add settings-related API endpoints (frontend prep)

These endpoints will be consumed by the frontend settings/configuration pages.
Add to `src/api/server.py`:

```python
# ── Platform Config ──

@app.get("/api/config/platforms")
async def get_platform_config():
    """Return current platform connection settings."""
    from config import settings
    return {
        "telegram": {
            "enabled": bool(settings.TELEGRAM_TOKEN),
            "proxy_url": settings.TELEGRAM_PROXY_URL,
            "kevin_id": settings.TELEGRAM_KEVIN_ID,
            "text_mode": settings.TELEGRAM_TEXT_MODE,
        },
        "qq": {
            "enabled": settings.QQ_ENABLED,
            "ws_url": settings.QQ_WS_URL,
            "self_id": settings.QQ_SELF_ID,
            "kevin_id": settings.QQ_KEVIN_ID,
            "group_ids": settings.QQ_GROUP_IDS,
            "group_cooldown": settings.QQ_GROUP_COOLDOWN,
        },
    }

# ── Feature Flags ──

@app.get("/api/config/features")
async def get_feature_flags():
    """Return current feature flag states."""
    from config import settings
    return {
        "shell_enabled": settings.SHELL_ENABLED,
        "web_tools_enabled": settings.CHAT_WEB_TOOLS_ENABLED,
        "skills_enabled": settings.SKILLS_ENABLED,
        "experience_skills_enabled": settings.EXPERIENCE_SKILLS_ENABLED,
        "session_enabled": settings.SESSION_ENABLED,
        "memory_crud_enabled": settings.MEMORY_CRUD_ENABLED,
        "auto_memory_extract_enabled": settings.AUTO_MEMORY_EXTRACT_ENABLED,
        "self_schedule_enabled": settings.SELF_SCHEDULE_ENABLED,
        "qq_enabled": settings.QQ_ENABLED,
    }

# ── Persona Files ──

@app.get("/api/persona/files")
async def get_persona_files():
    """Return editable persona file contents."""
    from config.settings import SOUL_PATH, IDENTITY_DIR, PROMPTS_DIR
    files = {}
    for name, path in [
        ("soul", SOUL_PATH),
        ("voice", PROMPTS_DIR / "lapwing_voice.md"),
        ("capabilities", PROMPTS_DIR / "lapwing_capabilities.md"),
        ("constitution", IDENTITY_DIR / "constitution.md"),
    ]:
        if path.exists():
            files[name] = {
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            }
    return {"files": files}

@app.post("/api/persona/files/{file_name}")
async def update_persona_file(file_name: str, request: Request):
    """Update a persona file's content."""
    body = await request.json()
    content = body.get("content", "")

    from config.settings import SOUL_PATH, IDENTITY_DIR, PROMPTS_DIR
    path_map = {
        "soul": SOUL_PATH,
        "voice": PROMPTS_DIR / "lapwing_voice.md",
        "capabilities": PROMPTS_DIR / "lapwing_capabilities.md",
        "constitution": IDENTITY_DIR / "constitution.md",
    }
    path = path_map.get(file_name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown persona file: {file_name}")

    path.write_text(content, encoding="utf-8")

    # Reload persona in brain
    brain = _get_brain(request)
    brain.reload_persona()

    return {"success": True, "file": file_name}

# ── Scheduled Tasks ──

@app.get("/api/scheduled-tasks")
async def get_scheduled_tasks():
    """Return all scheduled tasks."""
    from config.settings import SCHEDULED_TASKS_PATH
    if not SCHEDULED_TASKS_PATH.exists():
        return {"tasks": []}
    import json
    data = json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
    return {"tasks": data if isinstance(data, list) else []}

@app.delete("/api/scheduled-tasks/{task_id}")
async def delete_scheduled_task(task_id: str):
    """Cancel a scheduled task."""
    from config.settings import SCHEDULED_TASKS_PATH
    import json
    if not SCHEDULED_TASKS_PATH.exists():
        raise HTTPException(status_code=404, detail="No tasks found")
    data = json.loads(SCHEDULED_TASKS_PATH.read_text(encoding="utf-8"))
    updated = [t for t in data if t.get("id") != task_id]
    if len(updated) == len(data):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    SCHEDULED_TASKS_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "task_id": task_id}
```

**Note**: These endpoints are read-only or simple writes. For platform config changes
(e.g., changing QQ_ENABLED), they should write to a runtime config overlay or `.env` file.
Full implementation of env-write will be specified in the frontend blueprint.

### Summary of Log + API changes

```
MODIFY  main.py                  — Rewrite setup_logging() completely
MODIFY  ~25 source files         — Rename loggers to consistent pattern
MODIFY  src/api/server.py        — Add log streaming + config + persona + task endpoints
CREATE  (none)                   — No new files needed
DELETE  (none)
```

### Verification

```bash
# 1. No duplicate log lines
python -c "
import logging
exec(open('main.py').read().split('def main')[0])  # load setup_logging
logger = setup_logging()
logger.info('TEST: this should appear exactly once')
"
# Verify: "TEST: this should appear exactly once" shows once on console

# 2. All loggers follow naming convention
grep -rn 'getLogger("lapwing\.' src/ --include="*.py" | grep -v __pycache__ | \
  awk -F'"' '{print $2}' | sort | while read name; do
    # Check it matches lapwing.{package}.{module} format
    parts=$(echo "$name" | tr '.' '\n' | wc -l)
    if [ "$parts" -lt 3 ]; then
        echo "WARNING: $name has fewer than 3 segments"
    fi
done

# 3. Log rotation works
ls -la logs/lapwing.log*

# 4. New API endpoints respond
curl -s http://127.0.0.1:8765/api/config/features | python -m json.tool
curl -s http://127.0.0.1:8765/api/config/platforms | python -m json.tool
curl -s http://127.0.0.1:8765/api/persona/files | python -m json.tool
curl -s http://127.0.0.1:8765/api/logs/recent?lines=10 | python -m json.tool
```
-e 

---

# Lapwing Restructuring — Addendum: Desktop Client Backend Prep

> Append this as **Phase K** to the main implementation blueprint.
> These changes prepare the backend for a Tauri Windows exe remote client.

---

## Architecture Decision

```
┌─────────────────────┐              ┌──────────────────────────┐
│  Windows exe (Tauri) │              │  PVE Server (24/7)       │
│                      │   WebSocket  │                          │
│  Chat window    ◄────┼──────/ws/chat┼──► Brain                 │
│  Control panel  ◄────┼──── REST API ┼──► Config / Memory / Log │
│  Presence signal ────┼─────────────►┼──► Heartbeat awareness   │
│                      │   HTTPS      │                          │
│  Tauri + React       │  (via domain │  FastAPI + uvicorn       │
│                      │   + nginx)   │                          │
└─────────────────────┘              └──────────────────────────┘
```

- exe is a **remote client only** — no backend bundled
- Backend runs 24/7 on PVE server, accessible via domain + reverse proxy
- Chat uses WebSocket for real-time bidirectional communication
- Control panel uses REST API (same endpoints as web dashboard)
- Desktop connection/disconnection triggers presence awareness in Lapwing

---

## Phase K: Desktop Client Backend Prep

### K1. WebSocket Chat Endpoint

Add `/ws/chat` to `src/api/server.py`. This is the core real-time channel
for the desktop chat window.

```python
from fastapi import WebSocket, WebSocketDisconnect
import json

# Track active desktop connections
_active_desktop_ws: dict[str, WebSocket] = {}


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """WebSocket endpoint for desktop chat.

    Client → Server messages:
        { "type": "message", "content": "..." }
        { "type": "ping" }

    Server → Client messages:
        { "type": "reply", "content": "...", "final": true }
        { "type": "interim", "content": "..." }           # tool loop intermediate text
        { "type": "status", "phase": "...", "text": "..." } # thinking/executing/etc
        { "type": "typing" }
        { "type": "presence_ack", "status": "connected" }
        { "type": "pong" }
        { "type": "error", "message": "..." }
    """
    # Auth: verify token from query param or first message
    token = ws.query_params.get("token", "")
    # TODO: validate token against API session / bootstrap token
    # For now, accept if DESKTOP_DEFAULT_OWNER is true
    from config.settings import DESKTOP_DEFAULT_OWNER
    if not DESKTOP_DEFAULT_OWNER and not token:
        await ws.close(code=4001, reason="Authentication required")
        return

    await ws.accept()
    connection_id = str(id(ws))
    _active_desktop_ws[connection_id] = ws

    # Notify brain: Kevin is at his computer
    brain = _get_brain_from_app(app)
    _notify_desktop_presence(brain, connected=True)
    await ws.send_json({"type": "presence_ack", "status": "connected"})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if msg_type == "message":
                content = str(msg.get("content", "")).strip()
                if not content:
                    continue

                chat_id = f"desktop:{connection_id}"

                # Define callbacks for conversational mode
                async def send_fn(text: str) -> None:
                    try:
                        await ws.send_json({"type": "interim", "content": text})
                    except Exception:
                        pass

                async def typing_fn() -> None:
                    try:
                        await ws.send_json({"type": "typing"})
                    except Exception:
                        pass

                async def status_callback(cid: str, status_text: str) -> None:
                    try:
                        await ws.send_json({
                            "type": "status",
                            "phase": "executing",
                            "text": status_text,
                        })
                    except Exception:
                        pass

                # Process through brain
                try:
                    await ws.send_json({"type": "status", "phase": "thinking", "text": ""})
                    reply = await brain.think_conversational(
                        chat_id=chat_id,
                        user_message=content,
                        send_fn=send_fn,
                        typing_fn=typing_fn,
                        status_callback=status_callback,
                        adapter="desktop",
                        user_id="owner",
                    )
                    # Send final reply marker
                    await ws.send_json({
                        "type": "reply",
                        "content": reply,
                        "final": True,
                    })
                except Exception as exc:
                    await ws.send_json({
                        "type": "error",
                        "message": f"处理消息失败: {exc}",
                    })

    except WebSocketDisconnect:
        pass
    finally:
        _active_desktop_ws.pop(connection_id, None)
        _notify_desktop_presence(brain, connected=bool(_active_desktop_ws))
```

Helper function for presence:

```python
def _notify_desktop_presence(brain, *, connected: bool) -> None:
    """Notify the system about desktop connection status changes."""
    # Store presence state for heartbeat to reference
    if hasattr(brain, '_desktop_connected'):
        brain._desktop_connected = connected
    else:
        brain._desktop_connected = connected

    logger.info("Desktop presence: %s", "connected" if connected else "disconnected")
```

### K2. Desktop Presence Integration

Add a `desktop_connected` property to `LapwingBrain` in `src/core/brain.py`:

```python
@property
def desktop_connected(self) -> bool:
    """Whether any desktop client is currently connected."""
    return getattr(self, '_desktop_connected', False)
```

Heartbeat actions can check `brain.desktop_connected` to decide behavior:
- When connected: suppress proactive Telegram messages (Kevin is at computer, use desktop)
- When disconnected: resume normal Telegram proactive behavior
- Connection event can trigger a greeting ("你回来了。")

### K3. Remote Auth Enhancement

The current auth system uses `API_SESSION_COOKIE_NAME` with cookie-based sessions.
For WebSocket from a remote Tauri client, cookies may not work reliably.
Add token-based auth as an alternative.

In `src/api/server.py`, add a token verification utility:

```python
async def _verify_desktop_token(token: str) -> bool:
    """Verify a desktop connection token.

    Tokens are generated via /api/auth/session (bootstrap token flow)
    or a new /api/auth/desktop-token endpoint.
    """
    if not token:
        return False
    # Check against stored session tokens
    # Implementation: validate against the existing session store
    from config.settings import DESKTOP_DEFAULT_OWNER
    if DESKTOP_DEFAULT_OWNER:
        return True  # Local dev: accept any token

    # Production: validate against session store
    # TODO: integrate with existing auth session validation
    return False
```

Add a desktop token generation endpoint:

```python
@app.post("/api/auth/desktop-token")
async def create_desktop_token(request: Request):
    """Generate a long-lived token for the desktop client.

    This token is stored in the Tauri app's secure storage
    and sent with every WebSocket connection.
    """
    body = await request.json()
    bootstrap = body.get("bootstrap_token", "")

    # Validate bootstrap token
    from config.settings import API_BOOTSTRAP_TOKEN_PATH
    if API_BOOTSTRAP_TOKEN_PATH.exists():
        expected = API_BOOTSTRAP_TOKEN_PATH.read_text().strip()
        if bootstrap != expected:
            raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    # Generate long-lived desktop token
    import secrets
    token = secrets.token_urlsafe(32)

    # Store token (simple file-based for now)
    token_path = AUTH_DIR / "desktop-tokens.json"
    tokens = []
    if token_path.exists():
        tokens = json.loads(token_path.read_text())
    tokens.append({
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": body.get("label", "desktop"),
    })
    token_path.write_text(json.dumps(tokens, indent=2))

    return {"token": token}
```

### K4. CORS Update for Remote Access

In `config/settings.py`, the `API_ALLOWED_ORIGINS` needs to include the server's
domain for remote access:

```python
# Add to default origins
_API_ALLOWED_ORIGINS_DEFAULT = (
    "http://localhost:1420,http://127.0.0.1:1420,http://127.0.0.1:8765"
    ",tauri://localhost,http://tauri.localhost,https://tauri.localhost"
)
# Kevin's domain will be added via env var:
# API_ALLOWED_ORIGINS=https://lapw1ng.com,...
```

Also ensure the CORS middleware allows WebSocket upgrade:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=API_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### K5. Channel Manager: Register Desktop Channel

The desktop WebSocket should be registered as a proper channel in `ChannelManager`,
similar to how QQ is registered. This enables Lapwing to route proactive messages
to the desktop when Kevin is there.

In `src/adapters/base.py`, add `DESKTOP` to `ChannelType`:

```python
class ChannelType(str, Enum):
    TELEGRAM = "telegram"
    QQ = "qq"
    DESKTOP = "desktop"  # NEW
```

In the WebSocket handler, when sending proactive messages, the heartbeat can
check `channel_manager.has_active(ChannelType.DESKTOP)` to decide where to send.

### K6. API Endpoint Summary for Frontend

After all backend changes (Phase A-K), these are all available API endpoints
the frontend can consume:

```
# ── Chat ──
WS   /ws/chat                          # Real-time chat (WebSocket)

# ── Status ──
GET  /api/status                        # System status + latency
GET  /api/chats                         # List chat sessions

# ── Auth ──
POST /api/auth/session                  # Create session (bootstrap token)
GET  /api/auth/status                   # Auth profiles + bindings
POST /api/auth/desktop-token            # Generate desktop token (NEW)
POST /api/auth/import/codex-cache       # Import Codex auth
POST /api/auth/oauth/openai-codex/start # Start OAuth flow
GET  /api/auth/oauth/sessions/{id}      # OAuth session status

# ── Configuration ──
GET  /api/config/platforms              # Platform connection config (NEW)
GET  /api/config/features               # Feature flags (NEW)

# ── Model Routing ──
GET  /api/model-routing/config          # Provider + slot config
POST /api/model-routing/providers       # Add provider
PUT  /api/model-routing/providers/{id}  # Update provider
DEL  /api/model-routing/providers/{id}  # Remove provider
PUT  /api/model-routing/slots/{id}      # Assign slot
POST /api/model-routing/reload          # Hot reload

# ── Persona ──
GET  /api/persona/files                 # Read persona files (NEW)
POST /api/persona/files/{name}          # Edit persona file (NEW)
POST /api/reload                        # Reload persona
POST /api/evolve                        # Trigger evolution

# ── Memory ──
GET  /api/memory?chat_id=...            # List facts
POST /api/memory/delete                 # Delete fact
GET  /api/interests?chat_id=...         # Interest items
GET  /api/learnings                     # Journal/learning entries

# ── Tasks ──
GET  /api/tasks                         # List tasks
GET  /api/tasks/{id}                    # Task detail
GET  /api/scheduled-tasks               # Scheduled tasks (NEW)
DEL  /api/scheduled-tasks/{id}          # Cancel scheduled task (NEW)

# ── Logs ──
GET  /api/logs/stream                   # SSE log stream (NEW)
GET  /api/logs/recent                   # Historical logs (NEW)

# ── Events ──
GET  /api/events/stream                 # SSE event stream (existing)

# ── Telemetry ──
POST /api/telemetry/latency             # Client latency report
```

### K7. Settings for Desktop Connection

Add to `config/settings.py`:

```python
# Desktop client
DESKTOP_WS_CHAT_ID_PREFIX: str = os.getenv("DESKTOP_WS_CHAT_ID_PREFIX", "desktop")
DESKTOP_AUTH_TOKENS_PATH: Path = AUTH_DIR / "desktop-tokens.json"
```

---

## Summary: What the Backend Blueprint Now Covers

After appending Phase J (Logs) and Phase K (Desktop Prep), the complete blueprint is:

| Phase | Scope | Key Deliverables |
|-------|-------|-----------------|
| A | Kill Agent layer | Delete agents/, dispatcher; add weather tool |
| B | Persona system | Delete examples.md; keep voice.md + depth injection |
| C | Experience skills | Delete quick_match; simplify to LLM-only matching |
| D | QQ group LLM | Rewrite filter as LLM-based decider |
| E | Knowledge manager | Delete keyword matching; load all notes |
| F | Structural consolidation | Merge shell_policy; split brain.py, registry.py |
| G | CLAUDE.md | Rewrite in English |
| H | Test updates | Delete agent tests; ensure all pass |
| I | Cleanup | Archive docs; clean dirs/prompts |
| J | Log system | Fix duplicates; rotation; consistent naming; log API |
| K | Desktop prep | WebSocket chat; presence; remote auth; channel; API endpoints |

**Execution order**: A → B → C → D → E → F → G → J → K → H → I
(Tests and cleanup last, after all functional changes are in place)

**Frontend blueprint**: Separate document, to be created after backend is deployed.
Will cover: 5-page Chinese UI (对话/仪表盘/记忆/人格/任务 + 设置),
AstrBot-style comprehensive configuration, Tauri packaging for Windows exe.
