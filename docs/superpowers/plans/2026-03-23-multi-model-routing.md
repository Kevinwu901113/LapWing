# Multi-Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持在 `.env` 中配置多组 LLM 参数，通过 `LLMRouter` 统一调度，按 `purpose`（`chat` / `tool`）选择模型，兼容只配了一组通用参数的情况。

**Architecture:** 新建 `src/core/llm_router.py`，读取 `settings.py` 中的多模型配置，对外暴露 `router.complete(messages, purpose)` 统一接口。`brain.py` 改用此接口，不再直接持有 `AsyncOpenAI` 实例。启动时打日志说明每种 purpose 实际使用的模型。

**Tech Stack:** Python 3.11+, openai (AsyncOpenAI), aiosqlite (已有), pytest + pytest-asyncio (新增测试依赖)

---

## File Map

| 操作 | 文件 | 职责 |
|------|------|------|
| Modify | `config/settings.py` | 添加 `LLM_CHAT_*` / `LLM_TOOL_*` 配置，兼容回退到通用 `LLM_*` |
| Create | `src/core/llm_router.py` | 持有多个 `AsyncOpenAI` 实例，`complete(messages, purpose)` 统一入口，启动时校验通用配置 |
| Modify | `src/core/brain.py` | 改用 `LLMRouter`，去掉直接 `AsyncOpenAI` 实例 |
| Modify | `main.py` | 简化启动验证，改为校验 `TELEGRAM_TOKEN`（LLM 验证移入 LLMRouter） |
| Modify | `config/.env.example` | 添加多模型配置示例 |
| Modify | `requirements.txt` | 添加 pytest + pytest-asyncio 测试依赖 |
| Create | `tests/core/test_llm_router.py` | 单元测试：路由选择、回退逻辑 |

---

## Task 1: 搭建测试环境

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/core/__init__.py`
- Create: `tests/core/test_llm_router.py`

- [ ] **Step 1: 安装测试依赖并更新 requirements.txt**

```bash
cd /home/kevin/lapwing && source venv/bin/activate && pip install pytest pytest-asyncio
```

然后在 `requirements.txt` 中追加（如文件不存在则创建）：
```
pytest>=8.0
pytest-asyncio>=0.23
```

Expected: 安装成功，无报错

- [ ] **Step 2: 创建 tests 目录结构**

```bash
mkdir -p tests/core
touch tests/__init__.py tests/core/__init__.py
```

- [ ] **Step 3: 写失败测试（LLMRouter 不存在时会 ImportError）**

创建 `tests/core/test_llm_router.py`：

```python
"""LLMRouter 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestLLMRouterInit:
    """测试路由器初始化和配置加载。"""

    def test_chat_purpose_uses_chat_model_when_configured(self):
        """当 CHAT 模型单独配置时，chat purpose 使用专用模型。"""
        with patch.dict("os.environ", {
            "LLM_CHAT_MODEL": "glm-4-plus",
            "LLM_CHAT_BASE_URL": "https://chat.api.com/v1",
            "LLM_CHAT_API_KEY": "chat-key",
            "LLM_TOOL_MODEL": "glm-4-flash",
            "LLM_TOOL_BASE_URL": "https://tool.api.com/v1",
            "LLM_TOOL_API_KEY": "tool-key",
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }):
            import sys
            for mod in list(sys.modules.keys()):
                if "llm_router" in mod or "settings" in mod:
                    del sys.modules[mod]
            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("chat") == "glm-4-plus"
            assert router.model_for("tool") == "glm-4-flash"

    def test_fallback_to_generic_when_chat_not_configured(self):
        """当专用 CHAT 模型未配置时，回退到通用 LLM_MODEL。"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }, clear=True):
            # 清除可能残留的模块缓存
            import importlib
            import sys
            for mod in list(sys.modules.keys()):
                if "llm_router" in mod or "settings" in mod:
                    del sys.modules[mod]

            from src.core.llm_router import LLMRouter
            router = LLMRouter()
            assert router.model_for("chat") == "glm-4-flash"
            assert router.model_for("tool") == "glm-4-flash"


@pytest.mark.asyncio
class TestLLMRouterComplete:
    """测试 complete() 接口。"""

    async def test_complete_calls_correct_client(self):
        """complete() 用正确的 purpose 调用对应的 client。"""
        with patch.dict("os.environ", {
            "LLM_CHAT_MODEL": "glm-4-plus",
            "LLM_CHAT_BASE_URL": "https://chat.api.com/v1",
            "LLM_CHAT_API_KEY": "chat-key",
            "LLM_TOOL_MODEL": "glm-4-flash",
            "LLM_TOOL_BASE_URL": "https://tool.api.com/v1",
            "LLM_TOOL_API_KEY": "tool-key",
            "LLM_API_KEY": "generic-key",
            "LLM_BASE_URL": "https://generic.api.com/v1",
            "LLM_MODEL": "glm-4-flash",
        }):
            import sys
            for mod in list(sys.modules.keys()):
                if "llm_router" in mod or "settings" in mod:
                    del sys.modules[mod]

            from src.core.llm_router import LLMRouter
            router = LLMRouter()

            mock_response = MagicMock()
            mock_response.choices[0].message.content = "测试回复"

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            router._clients["chat"] = mock_client

            messages = [{"role": "user", "content": "你好"}]
            result = await router.complete(messages, purpose="chat")

            assert result == "测试回复"
            mock_client.chat.completions.create.assert_called_once()
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "glm-4-plus"
            assert call_kwargs["messages"] == messages
```

- [ ] **Step 4: 运行测试，确认失败（ImportError）**

```bash
cd /home/kevin/lapwing && source venv/bin/activate && python -m pytest tests/core/test_llm_router.py -v 2>&1 | head -30
```

Expected: `ImportError` 或 `ModuleNotFoundError: No module named 'src.core.llm_router'`

---

## Task 2: 更新 settings.py

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: 添加多模型配置项**

在 `config/settings.py` 的 LLM 配置块后追加：

```python
# 多模型路由配置（可选，不配置时回退到通用 LLM_* 配置）
LLM_CHAT_API_KEY: str = os.getenv("LLM_CHAT_API_KEY", "")
LLM_CHAT_BASE_URL: str = os.getenv("LLM_CHAT_BASE_URL", "")
LLM_CHAT_MODEL: str = os.getenv("LLM_CHAT_MODEL", "")

LLM_TOOL_API_KEY: str = os.getenv("LLM_TOOL_API_KEY", "")
LLM_TOOL_BASE_URL: str = os.getenv("LLM_TOOL_BASE_URL", "")
LLM_TOOL_MODEL: str = os.getenv("LLM_TOOL_MODEL", "")
```

- [ ] **Step 2: 验证 settings 可以正常导入**

```bash
cd /home/kevin/lapwing && source venv/bin/activate && python -c "from config.settings import LLM_CHAT_MODEL; print('ok')"
```

Expected: `ok`

---

## Task 3: 实现 LLMRouter

**Files:**
- Create: `src/core/llm_router.py`

- [ ] **Step 1: 创建 llm_router.py**

```python
"""LLM 路由器 - 按用途（purpose）选择对应的模型和 client。"""

import logging
from openai import AsyncOpenAI
from config.settings import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL,
    LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL,
)

logger = logging.getLogger("lapwing.llm_router")

# purpose -> (api_key, base_url, model) 的映射配置
_PURPOSE_ENV: dict[str, tuple[str, str, str]] = {
    "chat": (LLM_CHAT_API_KEY, LLM_CHAT_BASE_URL, LLM_CHAT_MODEL),
    "tool": (LLM_TOOL_API_KEY, LLM_TOOL_BASE_URL, LLM_TOOL_MODEL),
}


class LLMRouter:
    """按 purpose 路由到对应 LLM client。

    用法：
        router = LLMRouter()
        reply = await router.complete(messages, purpose="chat")
    """

    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI] = {}
        self._models: dict[str, str] = {}
        self._setup_clients()

    def _setup_clients(self) -> None:
        """根据配置初始化各 purpose 的 client，未配置时回退到通用 LLM_*。"""
        # 校验通用配置（所有 purpose 的最终回退）
        if not LLM_API_KEY or not LLM_BASE_URL or not LLM_MODEL:
            raise ValueError(
                "LLM 通用配置不完整，请检查 config/.env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL"
            )

        for purpose, (api_key, base_url, model) in _PURPOSE_ENV.items():
            if api_key and base_url and model:
                self._clients[purpose] = AsyncOpenAI(api_key=api_key, base_url=base_url)
                self._models[purpose] = model
                logger.info(f"[{purpose}] 使用专用模型: {model} ({base_url})")
            else:
                # 回退到通用配置
                if purpose not in self._clients:
                    self._clients[purpose] = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
                self._models[purpose] = LLM_MODEL
                logger.info(f"[{purpose}] 回退到通用模型: {LLM_MODEL} ({LLM_BASE_URL})")

    def model_for(self, purpose: str) -> str:
        """返回指定 purpose 实际使用的模型名。"""
        return self._models.get(purpose, LLM_MODEL)

    async def complete(
        self,
        messages: list[dict],
        purpose: str = "chat",
        max_tokens: int = 1024,
    ) -> str:
        """向对应 purpose 的模型发送请求，返回回复文本。

        Args:
            messages: OpenAI 格式的消息列表
            purpose: 用途标识（"chat" 或 "tool"）
            max_tokens: 最大生成 token 数

        Returns:
            模型回复的文本内容

        Raises:
            Exception: LLM API 调用失败时向上抛出，由调用方处理
        """
        client = self._clients.get(purpose, self._clients["chat"])
        model = self._models.get(purpose, LLM_MODEL)

        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content
```

- [ ] **Step 2: 运行测试，确认通过**

```bash
cd /home/kevin/lapwing && source venv/bin/activate && python -m pytest tests/core/test_llm_router.py -v
```

Expected: 3 tests PASSED

- [ ] **Step 3: 提交**

```bash
cd /home/kevin/lapwing && git add src/core/llm_router.py tests/core/ tests/__init__.py config/settings.py && git commit -m "feat: add LLMRouter with purpose-based model routing"
```

---

## Task 4: 改造 brain.py

**Files:**
- Modify: `src/core/brain.py`

- [ ] **Step 1: 改用 LLMRouter**

将 `brain.py` 改为：

```python
"""Lapwing 的大脑 - LLM 调用与对话管理。"""

import logging
from pathlib import Path

from src.core.prompt_loader import load_prompt
from src.core.llm_router import LLMRouter
from src.memory.conversation import ConversationMemory
from config.settings import MAX_HISTORY_TURNS

logger = logging.getLogger("lapwing.brain")


class LapwingBrain:
    """管理 LLM 调用和对话上下文。"""

    def __init__(self, db_path: Path):
        self.router = LLMRouter()
        self.memory = ConversationMemory(db_path)
        self._system_prompt: str | None = None

    async def init_db(self) -> None:
        """初始化数据库连接和表结构。"""
        await self.memory.init_db()

    @property
    def system_prompt(self) -> str:
        """懒加载 system prompt。"""
        if self._system_prompt is None:
            self._system_prompt = load_prompt("lapwing")
            logger.info("已加载 Lapwing 人格 prompt")
        return self._system_prompt

    def reload_persona(self) -> None:
        """重新加载人格 prompt（修改 prompts/lapwing.md 后调用）。"""
        from src.core.prompt_loader import reload_prompt
        self._system_prompt = reload_prompt("lapwing")
        logger.info("已重新加载 Lapwing 人格 prompt")

    async def think(self, chat_id: str, user_message: str) -> str:
        """处理用户消息，返回 Lapwing 的回复。

        Args:
            chat_id: Telegram 对话 ID
            user_message: 用户发送的消息

        Returns:
            Lapwing 的回复文本
        """
        await self.memory.append(chat_id, "user", user_message)

        history = await self.memory.get(chat_id)
        max_messages = MAX_HISTORY_TURNS * 2
        recent = history[-max_messages:] if len(history) > max_messages else history

        messages = [
            {"role": "system", "content": self.system_prompt},
            *recent,
        ]

        try:
            reply = await self.router.complete(messages, purpose="chat")
            await self.memory.append(chat_id, "assistant", reply)
            logger.debug(f"[{chat_id}] 回复生成成功，长度: {len(reply)}")
            return reply

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self.memory.remove_last(chat_id)
            return "抱歉，我刚才走神了一下。你能再说一次吗？"
```

- [ ] **Step 2: 更新 main.py 的 brain 初始化和启动验证**

`main.py` 顶部导入改为（移除不再需要的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`）：
```python
from config.settings import (
    TELEGRAM_TOKEN,
    MAX_REPLY_LENGTH,
    LOG_LEVEL,
    LOGS_DIR,
    DB_PATH,
)
```

初始化改为：
```python
brain = LapwingBrain(db_path=DB_PATH)
```

启动验证块改为（LLM 配置校验已由 `LLMRouter.__init__` 负责，启动时抛出 ValueError）：
```python
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
    exit(1)
```

- [ ] **Step 3: 验证启动不报错**

```bash
cd /home/kevin/lapwing && source venv/bin/activate && python -c "
from config.settings import DB_PATH
from src.core.brain import LapwingBrain
brain = LapwingBrain(db_path=DB_PATH)
print('brain ok, chat model:', brain.router.model_for('chat'))
print('tool model:', brain.router.model_for('tool'))
"
```

Expected: 打印两行模型信息，无报错

- [ ] **Step 4: 提交**

```bash
cd /home/kevin/lapwing && git add src/core/brain.py main.py && git commit -m "refactor: brain uses LLMRouter instead of direct AsyncOpenAI"
```

---

## Task 5: 更新 .env.example

**Files:**
- Modify: `config/.env.example`

- [ ] **Step 1: 更新模板**

将 `config/.env.example` 改为：

```
# Telegram
TELEGRAM_TOKEN=你的_telegram_bot_token

# LLM 通用配置（所有 purpose 未单独配置时使用此配置）
LLM_API_KEY=你的_api_key
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=glm-4-flash

# LLM 多模型路由（可选，不填则回退到通用配置）
# 对话模型（人格对话，追求质量）
LLM_CHAT_API_KEY=
LLM_CHAT_BASE_URL=
LLM_CHAT_MODEL=

# 工具模型（Agent 任务，追求速度和成本）
LLM_TOOL_API_KEY=
LLM_TOOL_BASE_URL=
LLM_TOOL_MODEL=

# 日志级别
LOG_LEVEL=INFO
```

- [ ] **Step 2: 提交**

```bash
cd /home/kevin/lapwing && git add config/.env.example && git commit -m "docs: update .env.example with multi-model routing config"
```
