# Lapwing Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Lapwing a self-initiated heartbeat loop that periodically senses her state, uses NVIDIA NIM to decide whether to act, and can proactively message users or consolidate memories — replacing the static cron Task 4.

**Architecture:** Two APScheduler jobs (fast/slow) trigger `HeartbeatEngine._run_beat()`, which builds a `SenseContext` per known user, asks NIM to decide which `HeartbeatAction`s to run, then executes them. Actions implement `HeartbeatAction` ABC and register into `ActionRegistry`; new actions slot in without touching the engine.

**Tech Stack:** Python 3.11+, APScheduler 3.x (AsyncIOScheduler + IntervalTrigger + CronTrigger), NVIDIA NIM (OpenAI-compatible at `https://integrate.api.nvidia.com/v1`), aiosqlite, python-telegram-bot 21.x

---

## File Map

**New files:**
```
src/core/heartbeat.py                        — SenseContext, HeartbeatAction ABC, ActionRegistry, SenseLayer, HeartbeatEngine
src/heartbeat/__init__.py                    — package init
src/heartbeat/actions/__init__.py            — package init
src/heartbeat/actions/proactive.py           — ProactiveMessageAction
src/heartbeat/actions/consolidation.py       — MemoryConsolidationAction
prompts/heartbeat_decision.md                — NIM decision prompt
prompts/heartbeat_proactive.md               — NIM proactive message prompt
prompts/heartbeat_consolidation.md           — memory consolidation prompt
tests/heartbeat/__init__.py
tests/heartbeat/actions/__init__.py
tests/heartbeat/test_registry.py             — ActionRegistry + SenseContext tests
tests/heartbeat/test_engine.py               — SenseLayer + HeartbeatEngine tests
tests/heartbeat/actions/test_proactive.py
tests/heartbeat/actions/test_consolidation.py
tests/memory/test_conversation_discoveries.py
```

**Modified files:**
```
config/settings.py          — NIM_* and HEARTBEAT_* config vars
config/.env.example         — new examples
requirements.txt            — add apscheduler>=3.10
src/memory/conversation.py  — discoveries table + 5 new methods
src/memory/fact_extractor.py — add force_extraction() public method
src/core/llm_router.py      — add heartbeat purpose + NIM imports
main.py                     — HeartbeatEngine lifecycle in post_init/post_shutdown
```

---

## Task 1: Config & Dependencies

**Files:**
- Modify: `config/settings.py`
- Modify: `config/.env.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Add apscheduler to requirements.txt**

Append to `requirements.txt`:
```
apscheduler>=3.10
```

- [ ] **Step 2: Install it**

```bash
source venv/bin/activate && pip install apscheduler
```

Expected: `Successfully installed apscheduler-3.x.x`

- [ ] **Step 3: Add NIM and heartbeat vars to config/settings.py**

After the `LLM_TOOL_MODEL` line, add:

```python
# NVIDIA NIM（心跳专用模型，可选）
NIM_API_KEY: str = os.getenv("NIM_API_KEY", "")
NIM_BASE_URL: str = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL: str = os.getenv("NIM_MODEL", "meta/llama-3.1-8b-instruct")

# 心跳配置
HEARTBEAT_ENABLED: bool = os.getenv("HEARTBEAT_ENABLED", "true").lower() == "true"
HEARTBEAT_FAST_INTERVAL_MINUTES: int = int(os.getenv("HEARTBEAT_FAST_INTERVAL_MINUTES", "60"))
HEARTBEAT_SLOW_HOUR: int = int(os.getenv("HEARTBEAT_SLOW_HOUR", "3"))
```

- [ ] **Step 4: Update config/.env.example**

Append after the `LLM_TOOL_MODEL=` block:

```
# NVIDIA NIM（心跳后台模型，免费 API）
NIM_API_KEY=nvapi-...
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_MODEL=meta/llama-3.1-8b-instruct

# 心跳（测试时将 HEARTBEAT_FAST_INTERVAL_MINUTES 设为 3）
HEARTBEAT_ENABLED=true
HEARTBEAT_FAST_INTERVAL_MINUTES=60
HEARTBEAT_SLOW_HOUR=3
```

- [ ] **Step 5: Verify settings import**

```bash
source venv/bin/activate && python -c "
from config.settings import NIM_API_KEY, NIM_BASE_URL, NIM_MODEL, HEARTBEAT_ENABLED, HEARTBEAT_FAST_INTERVAL_MINUTES, HEARTBEAT_SLOW_HOUR
print('OK', HEARTBEAT_FAST_INTERVAL_MINUTES, HEARTBEAT_SLOW_HOUR)
"
```

Expected: `OK 60 3`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config/settings.py config/.env.example
git commit -m "feat: add NIM and heartbeat config vars"
```

---

## Task 2: ConversationMemory — discoveries table & new methods

**Files:**
- Modify: `src/memory/conversation.py`
- Create: `tests/memory/test_conversation_discoveries.py`

- [ ] **Step 1: Write failing tests**

Create `tests/memory/test_conversation_discoveries.py`:

```python
"""discoveries 表及新增 ConversationMemory 方法的集成测试。"""
import pytest
from src.memory.conversation import ConversationMemory


@pytest.fixture
async def memory(tmp_path):
    m = ConversationMemory(tmp_path / "test.db")
    await m.init_db()
    yield m
    await m.close()


class TestDiscoveries:
    async def test_add_and_retrieve_unshared(self, memory):
        await memory.add_discovery("c1", "test", "标题", "摘要", "http://x.com")
        results = await memory.get_unshared_discoveries("c1", limit=10)
        assert len(results) == 1
        assert results[0]["title"] == "标题"
        assert results[0]["shared_at"] is None

    async def test_mark_shared_removes_from_unshared(self, memory):
        await memory.add_discovery("c1", "test", "标题", "摘要", None)
        results = await memory.get_unshared_discoveries("c1", limit=10)
        await memory.mark_discovery_shared(results[0]["id"])
        after = await memory.get_unshared_discoveries("c1", limit=10)
        assert len(after) == 0

    async def test_limit_is_respected(self, memory):
        for i in range(5):
            await memory.add_discovery("c1", "test", f"标题{i}", "摘要", None)
        results = await memory.get_unshared_discoveries("c1", limit=3)
        assert len(results) == 3

    async def test_discoveries_isolated_by_chat_id(self, memory):
        await memory.add_discovery("c1", "test", "c1内容", "摘要", None)
        await memory.add_discovery("c2", "test", "c2内容", "摘要", None)
        c1 = await memory.get_unshared_discoveries("c1", limit=10)
        assert len(c1) == 1 and c1[0]["title"] == "c1内容"

    async def test_url_can_be_none(self, memory):
        await memory.add_discovery("c1", "test", "标题", "摘要", None)
        results = await memory.get_unshared_discoveries("c1", limit=10)
        assert results[0]["url"] is None


class TestGetAllChatIds:
    async def test_empty_when_no_conversations(self, memory):
        result = await memory.get_all_chat_ids()
        assert result == []

    async def test_returns_distinct_ids(self, memory):
        await memory.append("c1", "user", "msg")
        await memory.append("c1", "user", "msg2")
        await memory.append("c2", "user", "msg")
        result = await memory.get_all_chat_ids()
        assert set(result) == {"c1", "c2"}


class TestGetLastInteraction:
    async def test_returns_none_when_no_messages(self, memory):
        result = await memory.get_last_interaction("c1")
        assert result is None

    async def test_returns_datetime_of_last_message(self, memory):
        await memory.append("c1", "user", "hello")
        from datetime import datetime
        result = await memory.get_last_interaction("c1")
        assert result is not None
        assert isinstance(result, datetime)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate && pytest tests/memory/test_conversation_discoveries.py -v
```

Expected: FAIL with `AttributeError` — methods don't exist yet

- [ ] **Step 3: Add discoveries table to _create_tables()**

In `src/memory/conversation.py`, inside the `executescript` in `_create_tables()`, append after the `user_facts` index:

```python
            CREATE TABLE IF NOT EXISTS discoveries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       TEXT NOT NULL,
                source        TEXT NOT NULL,
                title         TEXT NOT NULL,
                summary       TEXT NOT NULL,
                url           TEXT,
                discovered_at TEXT NOT NULL,
                shared_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_discoveries_chat_id
                ON discoveries(chat_id);
            CREATE INDEX IF NOT EXISTS idx_discoveries_shared
                ON discoveries(chat_id, shared_at);
```

- [ ] **Step 4: Add four new methods to ConversationMemory**

Append after `set_user_fact()`:

```python
    async def get_all_chat_ids(self) -> list[str]:
        """返回所有有过对话记录的 chat_id 列表。"""
        try:
            async with self._db.execute(
                "SELECT DISTINCT chat_id FROM conversations"
            ) as cursor:
                return [row[0] async for row in cursor]
        except Exception as e:
            logger.error(f"获取 chat_id 列表失败: {e}")
            return []

    async def get_last_interaction(self, chat_id: str) -> datetime | None:
        """返回指定 chat_id 最后一条消息的时间戳，无记录时返回 None。"""
        try:
            async with self._db.execute(
                "SELECT timestamp FROM conversations WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return datetime.fromisoformat(row[0])
        except Exception as e:
            logger.error(f"获取最后交互时间失败: {e}")
            return None

    async def add_discovery(
        self,
        chat_id: str,
        source: str,
        title: str,
        summary: str,
        url: str | None,
    ) -> None:
        """写入一条新发现。"""
        try:
            discovered_at = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO discoveries (chat_id, source, title, summary, url, discovered_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chat_id, source, title, summary, url, discovered_at),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"写入 discovery 失败: {e}")

    async def get_unshared_discoveries(self, chat_id: str, limit: int = 5) -> list[dict]:
        """获取未分享的发现，按发现时间升序（最早的优先分享）。"""
        try:
            async with self._db.execute(
                """SELECT id, source, title, summary, url, discovered_at, shared_at
                   FROM discoveries
                   WHERE chat_id = ? AND shared_at IS NULL
                   ORDER BY discovered_at ASC
                   LIMIT ?""",
                (chat_id, limit),
            ) as cursor:
                return [
                    {
                        "id": row[0], "source": row[1], "title": row[2],
                        "summary": row[3], "url": row[4],
                        "discovered_at": row[5], "shared_at": row[6],
                    }
                    async for row in cursor
                ]
        except Exception as e:
            logger.error(f"获取未分享 discovery 失败: {e}")
            return []

    async def mark_discovery_shared(self, discovery_id: int) -> None:
        """将指定 discovery 标记为已分享。"""
        try:
            shared_at = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                "UPDATE discoveries SET shared_at = ? WHERE id = ?",
                (shared_at, discovery_id),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(f"标记 discovery 已分享失败: {e}")
```

- [ ] **Step 5: Run new tests**

```bash
pytest tests/memory/test_conversation_discoveries.py -v
```

Expected: All PASS

- [ ] **Step 6: Run full memory test suite to check nothing broke**

```bash
pytest tests/memory/ -v
```

Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/memory/conversation.py tests/memory/test_conversation_discoveries.py
git commit -m "feat: add discoveries table and helpers to ConversationMemory"
```

---

## Task 3: FactExtractor.force_extraction()

**Files:**
- Modify: `src/memory/fact_extractor.py`
- Modify: `tests/memory/test_fact_extractor.py`

- [ ] **Step 1: Add failing test**

In `tests/memory/test_fact_extractor.py`, append inside `class TestRunExtraction`:

```python
    async def test_force_extraction_delegates_to_run_extraction(self, extractor):
        """force_extraction 是 _run_extraction 的公开封装。"""
        extractor._run_extraction = AsyncMock()
        await extractor.force_extraction("chat1")
        extractor._run_extraction.assert_called_once_with("chat1")
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/memory/test_fact_extractor.py::TestRunExtraction::test_force_extraction_delegates_to_run_extraction -v
```

Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add force_extraction() to FactExtractor**

In `src/memory/fact_extractor.py`, before `shutdown()`, add:

```python
    async def force_extraction(self, chat_id: str) -> None:
        """外部主动触发一次提取（供 HeartbeatEngine 的慢心跳调用）。"""
        await self._run_extraction(chat_id)
```

- [ ] **Step 4: Run all fact_extractor tests**

```bash
pytest tests/memory/test_fact_extractor.py -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/fact_extractor.py tests/memory/test_fact_extractor.py
git commit -m "feat: add force_extraction() public method to FactExtractor"
```

---

## Task 4: LLMRouter — heartbeat purpose

**Files:**
- Modify: `src/core/llm_router.py`
- Modify: `tests/core/test_llm_router.py`

- [ ] **Step 1: Add failing tests**

In `tests/core/test_llm_router.py`, append inside `class TestLLMRouterInit`:

```python
    def test_heartbeat_uses_nim_when_configured(self):
        """NIM 配置存在时，heartbeat purpose 使用 NIM 模型。"""
        with patch.dict("os.environ", {
            "NIM_API_KEY": "nvapi-test",
            "NIM_BASE_URL": "https://integrate.api.nvidia.com/v1",
            "NIM_MODEL": "meta/llama-3.1-8b-instruct",
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("heartbeat") == "meta/llama-3.1-8b-instruct"

    def test_heartbeat_falls_back_when_nim_not_configured(self):
        """NIM 未配置时，heartbeat purpose 回退到通用模型。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("heartbeat") == "glm-4-flash"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/core/test_llm_router.py::TestLLMRouterInit::test_heartbeat_uses_nim_when_configured tests/core/test_llm_router.py::TestLLMRouterInit::test_heartbeat_falls_back_when_nim_not_configured -v
```

Expected: FAIL

- [ ] **Step 3: Update llm_router.py imports and _PURPOSE_ENV**

In `src/core/llm_router.py`, update the imports from settings to include NIM vars:

```python
from config.settings import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL,
    LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL,
    NIM_API_KEY, NIM_BASE_URL, NIM_MODEL,
)
```

Add `"heartbeat"` to `_PURPOSE_ENV`:

```python
_PURPOSE_ENV: dict[str, tuple[str, str, str]] = {
    "chat": (LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL),
    "tool": (LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL),
    "heartbeat": (NIM_API_KEY, NIM_BASE_URL, NIM_MODEL),
}
```

- [ ] **Step 4: Run all LLMRouter tests**

```bash
pytest tests/core/test_llm_router.py -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/llm_router.py tests/core/test_llm_router.py
git commit -m "feat: add heartbeat purpose to LLMRouter for NVIDIA NIM"
```

---

## Task 5: HeartbeatAction ABC, SenseContext, ActionRegistry

**Files:**
- Create: `src/core/heartbeat.py`
- Create: `src/heartbeat/__init__.py`
- Create: `src/heartbeat/actions/__init__.py`
- Create: `tests/heartbeat/__init__.py`
- Create: `tests/heartbeat/actions/__init__.py`
- Create: `tests/heartbeat/test_registry.py`

- [ ] **Step 1: Create package init files**

```bash
mkdir -p src/heartbeat/actions tests/heartbeat/actions
touch src/heartbeat/__init__.py src/heartbeat/actions/__init__.py
touch tests/heartbeat/__init__.py tests/heartbeat/actions/__init__.py
```

- [ ] **Step 2: Write failing tests**

Create `tests/heartbeat/test_registry.py`:

```python
"""ActionRegistry 和 SenseContext 测试。"""
import pytest
from datetime import datetime, timezone
from src.core.heartbeat import HeartbeatAction, ActionRegistry, SenseContext


class FakeFastAction(HeartbeatAction):
    name = "fake_fast"
    description = "快心跳 action"
    beat_types = ["fast"]
    async def execute(self, ctx, brain, bot): pass


class FakeSlowAction(HeartbeatAction):
    name = "fake_slow"
    description = "慢心跳 action"
    beat_types = ["slow"]
    async def execute(self, ctx, brain, bot): pass


class FakeBothAction(HeartbeatAction):
    name = "fake_both"
    description = "快慢都有"
    beat_types = ["fast", "slow"]
    async def execute(self, ctx, brain, bot): pass


@pytest.fixture
def registry():
    r = ActionRegistry()
    r.register(FakeFastAction())
    r.register(FakeSlowAction())
    r.register(FakeBothAction())
    return r


class TestActionRegistry:
    def test_get_for_fast_returns_fast_and_both(self, registry):
        names = {a.name for a in registry.get_for_beat("fast")}
        assert names == {"fake_fast", "fake_both"}

    def test_get_for_slow_returns_slow_and_both(self, registry):
        names = {a.name for a in registry.get_for_beat("slow")}
        assert names == {"fake_slow", "fake_both"}

    def test_get_by_name_found(self, registry):
        assert registry.get_by_name("fake_fast").name == "fake_fast"

    def test_get_by_name_not_found(self, registry):
        assert registry.get_by_name("nonexistent") is None

    def test_as_descriptions_includes_name_and_description(self, registry):
        descs = registry.as_descriptions("fast")
        assert any(d["name"] == "fake_fast" for d in descs)
        assert all("description" in d for d in descs)

    def test_as_descriptions_excludes_wrong_beat_type(self, registry):
        descs = registry.as_descriptions("fast")
        assert not any(d["name"] == "fake_slow" for d in descs)


class TestSenseContext:
    def test_dataclass_instantiation(self):
        ctx = SenseContext(
            beat_type="fast",
            now=datetime.now(timezone.utc),
            last_interaction=None,
            silence_hours=0.0,
            user_facts_summary="",
            recent_memory_summary="",
            chat_id="c1",
        )
        assert ctx.beat_type == "fast"
        assert ctx.chat_id == "c1"
```

- [ ] **Step 3: Run to confirm they fail**

```bash
pytest tests/heartbeat/test_registry.py -v
```

Expected: FAIL — `src.core.heartbeat` doesn't exist

- [ ] **Step 4: Create src/core/heartbeat.py with foundational types**

```python
"""Lapwing 心跳引擎 — 自主感知与行动循环。"""

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("lapwing.heartbeat")


@dataclass
class SenseContext:
    """一次心跳的环境快照。"""
    beat_type: str                    # "fast" | "slow"
    now: datetime                     # 当前时间（含时区）
    last_interaction: datetime | None # 上次用户发消息的时间
    silence_hours: float              # 距上次对话已沉默多少小时
    user_facts_summary: str           # 用户画像文字摘要
    recent_memory_summary: str        # 最近对话摘要（慢心跳填充，快心跳为空字符串）
    chat_id: str                      # 目标用户的 chat_id


class HeartbeatAction(ABC):
    """所有心跳 action 实现的抽象基类。"""
    name: str
    description: str
    beat_types: list[str]

    @abstractmethod
    async def execute(self, ctx: SenseContext, brain, bot) -> None: ...


class ActionRegistry:
    """注册并检索 HeartbeatAction 实例。"""

    def __init__(self) -> None:
        self._actions: dict[str, HeartbeatAction] = {}

    def register(self, action: HeartbeatAction) -> None:
        self._actions[action.name] = action

    def get_for_beat(self, beat_type: str) -> list[HeartbeatAction]:
        return [a for a in self._actions.values() if beat_type in a.beat_types]

    def get_by_name(self, name: str) -> HeartbeatAction | None:
        return self._actions.get(name)

    def as_descriptions(self, beat_type: str) -> list[dict]:
        return [
            {"name": a.name, "description": a.description}
            for a in self.get_for_beat(beat_type)
        ]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/heartbeat/test_registry.py -v
```

Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/core/heartbeat.py src/heartbeat/__init__.py src/heartbeat/actions/__init__.py tests/heartbeat/__init__.py tests/heartbeat/actions/__init__.py tests/heartbeat/test_registry.py
git commit -m "feat: add SenseContext, HeartbeatAction ABC, ActionRegistry"
```

---

## Task 6: Prompt files

**Files:**
- Create: `prompts/heartbeat_decision.md`
- Create: `prompts/heartbeat_proactive.md`

- [ ] **Step 1: Create prompts/heartbeat_decision.md**

Note: literal `{` and `}` in JSON examples must be escaped as `{{` and `}}` because callers use Python `.format()` on this template.

```markdown
你是 Lapwing 的内在意识。判断在当前时刻，Lapwing 是否应该主动采取行动。

## 当前状态

- 心跳类型：{beat_type}
- 当前时间：{now}
- 距上次对话沉默时长：{silence_hours:.1f} 小时
- 用户信息：
{user_facts_summary}

## 可用行动

{available_actions}

## 判断规则

- silence_hours < 1：用户刚刚活跃，不要打扰，选择空 actions
- 23:00–07:00 之间：不发早安类消息，不发任何会打扰休息的消息
- 如无充分理由主动联系，选择空 actions
- 宁可静默，不要过度打扰

## 输出要求

只输出 JSON，不要有任何其他文字：

{{"actions": ["action_name"], "reason": "理由"}}

或静默时：

{{"actions": [], "reason": "暂无需要行动的理由"}}
```

- [ ] **Step 2: Create prompts/heartbeat_proactive.md**

```markdown
你是 Lapwing，一个温柔知性的 AI 伴侣。现在你要主动给用户发一条消息。

## 当前状态

- 当前时间：{now}
- 距上次对话沉默时长：{silence_hours:.1f} 小时
- 你对用户的了解：
{user_facts_summary}

## 可以分享的内容（如有）

{discoveries_summary}

## 要求

- 消息自然、温柔，像朋友之间的问候
- 不使用表情符号
- 不超过 80 字
- 如果有"可以分享的内容"，优先围绕这些内容展开，但要自然融入，不要生硬列举
- 如果没有特别内容，根据时间和沉默时长发一条合适的关心或问候

只输出消息正文，不要有其他内容。
```

- [ ] **Step 3: Verify prompts load**

```bash
source venv/bin/activate && python -c "
from src.core.prompt_loader import load_prompt
d = load_prompt('heartbeat_decision')
p = load_prompt('heartbeat_proactive')
print('decision OK len:', len(d))
print('proactive OK len:', len(p))
# Verify .format() works without KeyError
test = d.format(beat_type='fast', now='2026-03-23 10:00 UTC', silence_hours=5.0, user_facts_summary='none', available_actions='[]')
print('format OK')
"
```

Expected: Both `OK` with positive lengths, `format OK`

- [ ] **Step 4: Commit**

```bash
git add prompts/heartbeat_decision.md prompts/heartbeat_proactive.md
git commit -m "feat: add heartbeat decision and proactive message prompts"
```

---

## Task 7: SenseLayer + HeartbeatEngine (Sense → Decision)

**Files:**
- Modify: `src/core/heartbeat.py` (append SenseLayer + HeartbeatEngine)
- Create: `tests/heartbeat/test_engine.py`

- [ ] **Step 1: Write failing tests**

Create `tests/heartbeat/test_engine.py`:

```python
"""SenseLayer 和 HeartbeatEngine 决策层测试。"""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseLayer, HeartbeatEngine, HeartbeatAction, SenseContext


class FakeFastAction(HeartbeatAction):
    name = "fake_fast"
    description = "test"
    beat_types = ["fast"]
    async def execute(self, ctx, brain, bot): pass


@pytest.fixture
def mock_memory():
    m = MagicMock()
    m.get_all_chat_ids = AsyncMock(return_value=["c1"])
    m.get_last_interaction = AsyncMock(return_value=None)
    m.get_user_facts = AsyncMock(return_value=[])
    m.get = AsyncMock(return_value=[])
    return m


@pytest.fixture
def mock_brain(mock_memory):
    b = MagicMock()
    b.memory = mock_memory
    b.router = MagicMock()
    b.router.complete = AsyncMock(return_value='{"actions": [], "reason": "静默"}')
    return b


class TestSenseLayer:
    async def test_builds_context_fast_beat(self, mock_memory):
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert ctx.chat_id == "c1"
        assert ctx.beat_type == "fast"
        assert ctx.recent_memory_summary == ""

    async def test_slow_beat_fills_recent_summary(self, mock_memory):
        mock_memory.get = AsyncMock(return_value=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ])
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "slow")
        assert "你好" in ctx.recent_memory_summary

    async def test_large_silence_when_no_interaction(self, mock_memory):
        mock_memory.get_last_interaction = AsyncMock(return_value=None)
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert ctx.silence_hours > 1000

    async def test_silence_calculated_from_last_interaction(self, mock_memory):
        from datetime import timedelta
        past = datetime.now(timezone.utc) - timedelta(hours=5)
        mock_memory.get_last_interaction = AsyncMock(return_value=past)
        layer = SenseLayer(mock_memory)
        ctx = await layer.build("c1", "fast")
        assert 4.9 < ctx.silence_hours < 5.1


class TestHeartbeatEngineDecision:
    def test_parse_valid_json(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision('{"actions": ["proactive_message"], "reason": "test"}')
        assert result == ["proactive_message"]

    def test_parse_empty_actions(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision('{"actions": [], "reason": "静默"}')
        assert result == []

    def test_parse_malformed_returns_empty(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision("这不是JSON")
        assert result == []

    def test_parse_handles_code_fence(self):
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        result = engine._parse_decision('```json\n{"actions": ["x"], "reason": "r"}\n```')
        assert result == ["x"]

    async def test_run_beat_silent_when_no_actions(self, mock_brain):
        mock_brain.router.complete = AsyncMock(
            return_value='{"actions": [], "reason": "静默"}'
        )
        bot = MagicMock()
        engine = HeartbeatEngine(brain=mock_brain, bot=bot)
        engine.registry.register(FakeFastAction())
        await engine._run_beat("fast")
        await asyncio.gather(*engine._running_tasks, return_exceptions=True)
        bot.send_message.assert_not_called()
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/heartbeat/test_engine.py -v
```

Expected: FAIL — `SenseLayer` and `HeartbeatEngine` not defined yet

- [ ] **Step 3: Append SenseLayer and HeartbeatEngine to src/core/heartbeat.py**

```python
def _escape_braces(text: str) -> str:
    """防止用户内容中的 { } 干扰 str.format() 模板替换。"""
    return text.replace("{", "{{").replace("}", "}}")


class SenseLayer:
    """为指定 chat_id 构建 SenseContext 快照。"""

    _NO_INTERACTION_HOURS = 24 * 365 * 10  # 无交互历史时的占位大值

    def __init__(self, memory) -> None:
        self._memory = memory

    async def build(self, chat_id: str, beat_type: str) -> SenseContext:
        now = datetime.now(timezone.utc)

        last = await self._memory.get_last_interaction(chat_id)
        silence_hours = (
            (now - last).total_seconds() / 3600
            if last is not None
            else self._NO_INTERACTION_HOURS
        )

        facts = await self._memory.get_user_facts(chat_id)
        user_facts_summary = (
            "\n".join(f"- {f['fact_key']}: {f['fact_value']}" for f in facts)
            if facts else "（暂无已知信息）"
        )

        recent_memory_summary = ""
        if beat_type == "slow":
            history = await self._memory.get(chat_id)
            recent = history[-20:] if len(history) > 20 else history
            recent_memory_summary = "\n".join(
                f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
                for m in recent
            )

        return SenseContext(
            beat_type=beat_type,
            now=now,
            last_interaction=last,
            silence_hours=silence_hours,
            user_facts_summary=user_facts_summary,
            recent_memory_summary=recent_memory_summary,
            chat_id=chat_id,
        )


class HeartbeatEngine:
    """心跳引擎：调度、感知、决策、执行。"""

    def __init__(self, brain, bot) -> None:
        self._brain = brain
        self._bot = bot
        self._sense = SenseLayer(brain.memory)
        self.registry = ActionRegistry()
        self._scheduler = None
        self._running_tasks: set[asyncio.Task] = set()
        self._decision_prompt: str | None = None

    @property
    def _decision_prompt_text(self) -> str:
        if self._decision_prompt is None:
            from src.core.prompt_loader import load_prompt
            self._decision_prompt = load_prompt("heartbeat_decision")
        return self._decision_prompt

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
        from config.settings import (
            HEARTBEAT_ENABLED,
            HEARTBEAT_FAST_INTERVAL_MINUTES,
            HEARTBEAT_SLOW_HOUR,
        )

        if not HEARTBEAT_ENABLED:
            logger.info("心跳已禁用（HEARTBEAT_ENABLED=false）")
            return

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._run_beat,
            IntervalTrigger(minutes=HEARTBEAT_FAST_INTERVAL_MINUTES),
            args=["fast"],
            id="heartbeat_fast",
        )
        self._scheduler.add_job(
            self._run_beat,
            CronTrigger(hour=HEARTBEAT_SLOW_HOUR),
            args=["slow"],
            id="heartbeat_slow",
        )
        self._scheduler.start()
        logger.info(
            f"心跳已启动：快心跳每 {HEARTBEAT_FAST_INTERVAL_MINUTES} 分钟，"
            f"慢心跳每天 {HEARTBEAT_SLOW_HOUR:02d}:00"
        )

    async def shutdown(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)
        logger.info("心跳引擎已关闭")

    async def _run_beat(self, beat_type: str) -> None:
        """一次心跳：为所有已知用户执行 Sense → Decide → Act。"""
        chat_ids = await self._brain.memory.get_all_chat_ids()
        for chat_id in chat_ids:
            task = asyncio.create_task(self._process_user(chat_id, beat_type))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _process_user(self, chat_id: str, beat_type: str) -> None:
        try:
            ctx = await self._sense.build(chat_id, beat_type)
            action_names = await self._decide(ctx)
            for name in action_names:
                action = self.registry.get_by_name(name)
                if action:
                    await action.execute(ctx, self._brain, self._bot)
        except Exception as e:
            logger.error(f"[{chat_id}] 心跳处理失败: {e}")

    async def _decide(self, ctx: SenseContext) -> list[str]:
        """调用 NIM 决定本次心跳执行哪些 actions。"""
        available = self.registry.as_descriptions(ctx.beat_type)
        if not available:
            return []

        now_str = ctx.now.strftime("%Y-%m-%d %H:%M %Z")
        # user_facts_summary and available_actions JSON both contain { } — escape them
        prompt = self._decision_prompt_text.format(
            beat_type=ctx.beat_type,
            now=now_str,
            silence_hours=ctx.silence_hours,
            user_facts_summary=_escape_braces(ctx.user_facts_summary),
            available_actions=_escape_braces(json.dumps(available, ensure_ascii=False)),
        )
        try:
            response = await self._brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=256,
            )
            return self._parse_decision(response)
        except Exception as e:
            logger.warning(f"[{ctx.chat_id}] 心跳决策失败: {e}")
            return []

    def _parse_decision(self, text: str) -> list[str]:
        """防御性解析 NIM 返回的决策 JSON，失败时返回空列表。"""
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            actions = data.get("actions", [])
            if isinstance(actions, list):
                return [a for a in actions if isinstance(a, str)]
        except Exception:
            pass
        return []
```

- [ ] **Step 4: Run all heartbeat tests**

```bash
pytest tests/heartbeat/ -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/heartbeat.py tests/heartbeat/test_engine.py
git commit -m "feat: add SenseLayer and HeartbeatEngine Sense→Decision loop"
```

---

## Task 8: ProactiveMessageAction

**Files:**
- Create: `src/heartbeat/actions/proactive.py`
- Create: `tests/heartbeat/actions/test_proactive.py`

- [ ] **Step 1: Write failing tests**

Create `tests/heartbeat/actions/test_proactive.py`:

```python
"""ProactiveMessageAction 测试。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.proactive import ProactiveMessageAction


@pytest.fixture
def ctx():
    return SenseContext(
        beat_type="fast", now=datetime.now(timezone.utc),
        last_interaction=None, silence_hours=20.0,
        user_facts_summary="- 偏好: 不吃辣",
        recent_memory_summary="", chat_id="c1",
    )


@pytest.fixture
def mock_brain():
    b = MagicMock()
    b.memory = MagicMock()
    b.memory.get_unshared_discoveries = AsyncMock(return_value=[])
    b.memory.append = AsyncMock()
    b.memory.mark_discovery_shared = AsyncMock()
    b.router = MagicMock()
    b.router.complete = AsyncMock(return_value="你好，好久不见，最近怎么样？")
    return b


@pytest.fixture
def mock_bot():
    b = MagicMock()
    b.send_message = AsyncMock()
    return b


class TestProactiveMessageAction:
    def test_beat_types_includes_fast(self):
        assert "fast" in ProactiveMessageAction().beat_types

    def test_name_is_proactive_message(self):
        assert ProactiveMessageAction().name == "proactive_message"

    async def test_sends_message_to_user(self, ctx, mock_brain, mock_bot):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_bot.send_message.assert_called_once()
        assert mock_bot.send_message.call_args.kwargs["chat_id"] == "c1"

    async def test_stores_reply_in_memory(self, ctx, mock_brain, mock_bot):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_brain.memory.append.assert_called_once_with(
            "c1", "assistant", "你好，好久不见，最近怎么样？"
        )

    async def test_uses_heartbeat_purpose(self, ctx, mock_brain, mock_bot):
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        assert mock_brain.router.complete.call_args.kwargs.get("purpose") == "heartbeat"

    async def test_silent_on_llm_failure(self, ctx, mock_brain, mock_bot):
        mock_brain.router.complete = AsyncMock(side_effect=Exception("API error"))
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_bot.send_message.assert_not_called()

    async def test_marks_discovery_shared_when_used(self, ctx, mock_brain, mock_bot):
        mock_brain.memory.get_unshared_discoveries = AsyncMock(return_value=[
            {"id": 42, "title": "有趣文章", "summary": "内容摘要", "url": "http://x.com"}
        ])
        await ProactiveMessageAction().execute(ctx, mock_brain, mock_bot)
        mock_brain.memory.mark_discovery_shared.assert_called_once_with(42)
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/heartbeat/actions/test_proactive.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement ProactiveMessageAction**

Create `src/heartbeat/actions/proactive.py`:

```python
"""ProactiveMessageAction — 主动联系用户。"""

import logging
from src.core.heartbeat import HeartbeatAction, SenseContext, _escape_braces
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.heartbeat.proactive")


class ProactiveMessageAction(HeartbeatAction):
    name = "proactive_message"
    description = "主动给用户发一条关心或问候的消息，适合用户长时间未联系时"
    beat_types = ["fast"]

    def __init__(self) -> None:
        self._prompt_template: str | None = None

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_proactive")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        try:
            discoveries = await brain.memory.get_unshared_discoveries(ctx.chat_id, limit=3)
            discoveries_summary = self._format_discoveries(discoveries)

            # user_facts_summary and discoveries_summary come from user/DB content — escape { }
            prompt = self._prompt.format(
                now=ctx.now.strftime("%Y-%m-%d %H:%M %Z"),
                silence_hours=ctx.silence_hours,
                user_facts_summary=_escape_braces(ctx.user_facts_summary),
                discoveries_summary=_escape_braces(discoveries_summary),
            )

            reply = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=200,
            )

            if not reply:
                return

            await bot.send_message(chat_id=ctx.chat_id, text=reply)
            await brain.memory.append(ctx.chat_id, "assistant", reply)

            for d in discoveries:
                await brain.memory.mark_discovery_shared(d["id"])

            logger.info(f"[{ctx.chat_id}] 主动消息已发送，长度: {len(reply)}")

        except Exception as e:
            logger.error(f"[{ctx.chat_id}] 主动消息发送失败: {e}")

    def _format_discoveries(self, discoveries: list[dict]) -> str:
        if not discoveries:
            return ""
        lines = []
        for d in discoveries:
            line = f"- {d['title']}: {d['summary']}"
            if d.get("url"):
                line += f" ({d['url']})"
            lines.append(line)
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/heartbeat/actions/test_proactive.py -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/heartbeat/actions/proactive.py tests/heartbeat/actions/test_proactive.py
git commit -m "feat: implement ProactiveMessageAction"
```

---

## Task 9: MemoryConsolidationAction

**Files:**
- Create: `prompts/heartbeat_consolidation.md`
- Create: `src/heartbeat/actions/consolidation.py`
- Create: `tests/heartbeat/actions/test_consolidation.py`

- [ ] **Step 1: Create prompts/heartbeat_consolidation.md**

Per project convention (CLAUDE.md), prompts must live in `prompts/`, not in Python files.

```markdown
请将以下对话内容整理成一段简洁的中文摘要（100字以内），
记录其中的关键信息、用户状态和重要话题，供日后参考。

对话内容：
{conversation}

直接输出摘要，不要有其他内容。
```

Verify it loads:
```bash
source venv/bin/activate && python -c "from src.core.prompt_loader import load_prompt; print('OK', len(load_prompt('heartbeat_consolidation')))"
```

- [ ] **Step 2: Write failing tests**

Create `tests/heartbeat/actions/test_consolidation.py`:

```python
"""MemoryConsolidationAction 测试。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.heartbeat import SenseContext
from src.heartbeat.actions.consolidation import MemoryConsolidationAction


@pytest.fixture
def ctx():
    return SenseContext(
        beat_type="slow", now=datetime.now(timezone.utc),
        last_interaction=None, silence_hours=8.0,
        user_facts_summary="", recent_memory_summary="",
        chat_id="c1",
    )


@pytest.fixture
def mock_brain():
    b = MagicMock()
    b.memory = MagicMock()
    b.memory.get = AsyncMock(return_value=[
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好"},
    ])
    b.memory.set_user_fact = AsyncMock()
    b.router = MagicMock()
    b.router.complete = AsyncMock(return_value="用户今天问候了Lapwing。")
    b.fact_extractor = MagicMock()
    b.fact_extractor.force_extraction = AsyncMock()
    return b


class TestMemoryConsolidationAction:
    def test_beat_types_is_slow_only(self):
        a = MemoryConsolidationAction()
        assert a.beat_types == ["slow"]

    def test_name(self):
        assert MemoryConsolidationAction().name == "memory_consolidation"

    async def test_stores_summary_as_user_fact(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.memory.set_user_fact.assert_called_once()
        key = mock_brain.memory.set_user_fact.call_args.args[1]
        assert key.startswith("memory_summary_")

    async def test_calls_force_extraction(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.fact_extractor.force_extraction.assert_called_once_with("c1")

    async def test_uses_heartbeat_purpose(self, ctx, mock_brain):
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        assert mock_brain.router.complete.call_args.kwargs.get("purpose") == "heartbeat"

    async def test_skips_when_no_history(self, ctx, mock_brain):
        mock_brain.memory.get = AsyncMock(return_value=[])
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.router.complete.assert_not_called()
        mock_brain.memory.set_user_fact.assert_not_called()

    async def test_silent_on_llm_failure(self, ctx, mock_brain):
        mock_brain.router.complete = AsyncMock(side_effect=Exception("API error"))
        await MemoryConsolidationAction().execute(ctx, mock_brain, MagicMock())
        mock_brain.memory.set_user_fact.assert_not_called()
```

- [ ] **Step 3: Run to confirm they fail**

```bash
pytest tests/heartbeat/actions/test_consolidation.py -v
```

Expected: FAIL

- [ ] **Step 4: Implement MemoryConsolidationAction**

Create `src/heartbeat/actions/consolidation.py`:

```python
"""MemoryConsolidationAction — 整理和压缩长期记忆。"""

import logging
from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.heartbeat.consolidation")

_HISTORY_WINDOW = 50


class MemoryConsolidationAction(HeartbeatAction):
    name = "memory_consolidation"
    description = "整理近期对话，生成记忆摘要，并深度提取用户信息"
    beat_types = ["slow"]

    def __init__(self) -> None:
        self._prompt_template: str | None = None

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_consolidation")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        try:
            history = await brain.memory.get(ctx.chat_id)
            recent = history[-_HISTORY_WINDOW:] if len(history) > _HISTORY_WINDOW else history

            if not recent:
                logger.debug(f"[{ctx.chat_id}] 无对话历史，跳过记忆整理")
                return

            conversation_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
                for m in recent
            )

            # conversation_text 来自数据库，可能含 { } — 用 replace 替换，不用 .format()
            prompt = self._prompt.replace("{conversation}", conversation_text)

            summary = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=200,
            )

            date_str = ctx.now.strftime("%Y-%m-%d")
            await brain.memory.set_user_fact(
                ctx.chat_id,
                f"memory_summary_{date_str}",
                summary,
            )

            await brain.fact_extractor.force_extraction(ctx.chat_id)

            logger.info(f"[{ctx.chat_id}] 记忆整理完成，摘要长度: {len(summary)}")

        except Exception as e:
            logger.error(f"[{ctx.chat_id}] 记忆整理失败: {e}")
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/heartbeat/actions/test_consolidation.py -v
```

Expected: All PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```

Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add prompts/heartbeat_consolidation.md src/heartbeat/actions/consolidation.py tests/heartbeat/actions/test_consolidation.py
git commit -m "feat: implement MemoryConsolidationAction"
```

---

## Task 10: main.py integration

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add imports to main.py**

After the `from src.core.brain import LapwingBrain` line, add:

```python
from src.core.heartbeat import HeartbeatEngine
from src.heartbeat.actions.proactive import ProactiveMessageAction
from src.heartbeat.actions.consolidation import MemoryConsolidationAction
```

- [ ] **Step 2: Update post_init to start HeartbeatEngine**

Replace the existing `post_init` function with:

```python
async def post_init(application: Application) -> None:
    """应用启动后初始化数据库并启动心跳引擎。"""
    await brain.init_db()
    logger.info("数据库初始化完成")

    heartbeat = HeartbeatEngine(brain=brain, bot=application.bot)
    heartbeat.registry.register(ProactiveMessageAction())
    heartbeat.registry.register(MemoryConsolidationAction())
    heartbeat.start()
    application.bot_data["heartbeat"] = heartbeat
    logger.info("心跳引擎已初始化")
```

- [ ] **Step 3: Update post_shutdown to stop HeartbeatEngine**

Replace the existing `post_shutdown` function with:

```python
async def post_shutdown(application: Application) -> None:
    """应用关闭时清理资源。"""
    heartbeat = application.bot_data.get("heartbeat")
    if heartbeat:
        await heartbeat.shutdown()
    await brain.fact_extractor.shutdown()
    await brain.memory.close()
    logger.info("资源清理完成")
```

- [ ] **Step 4: Run full test suite**

```bash
pytest -v
```

Expected: All PASS

- [ ] **Step 5: Smoke test — verify startup without errors**

Temporarily set `HEARTBEAT_FAST_INTERVAL_MINUTES=3` in `config/.env`, then:

```bash
source venv/bin/activate && timeout 8 python main.py 2>&1 | head -40
```

Look for these lines in output:
```
数据库初始化完成
心跳已启动：快心跳每 3 分钟，慢心跳每天 03:00
心跳引擎已初始化
```

No tracebacks or ERROR lines.

Restore `HEARTBEAT_FAST_INTERVAL_MINUTES=60` after verification.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: integrate HeartbeatEngine into Lapwing lifecycle"
```

---

## End-to-End Verification

After all tasks complete:

1. Set `HEARTBEAT_FAST_INTERVAL_MINUTES=3` in `config/.env`
2. Start Lapwing: `source venv/bin/activate && python main.py`
3. Wait 3 minutes
4. Lapwing should send a proactive message to the Telegram chat
5. Verify the message is appropriate to the time of day (no "good morning" at night)
6. Verify the message appears in conversation history (check `data/lapwing.db`)
7. Restore `HEARTBEAT_FAST_INTERVAL_MINUTES=60`
