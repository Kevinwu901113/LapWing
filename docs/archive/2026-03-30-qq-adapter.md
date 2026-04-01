# QQ Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add QQ as a second messaging channel alongside Telegram, sharing the same Brain/Core, by implementing a BaseAdapter abstraction, refactoring TelegramApp to use it, building a QQAdapter over OneBot v11 WebSocket, and wiring both through a ChannelManager.

**Architecture:** Introduce `src/adapters/` with a `BaseAdapter` ABC. `TelegramAdapter` wraps existing `TelegramApp` + `telegram_delivery`. `QQAdapter` connects to NapCat via WebSocket (OneBot v11). A `ChannelManager` in `src/core/` registers adapters, routes incoming messages to Brain, and provides `send_to_kevin()` for Heartbeat. Heartbeat actions call a `send_fn` callback instead of importing `send_telegram_text_to_chat` directly.

**Tech Stack:** Python 3.11+, websockets>=12.0, aiosqlite, python-telegram-bot, APScheduler

**Scope note:** This plan implements a text-only MVP (CLAUDE.md Phases 1-2 + partial Phase 3-4). The unified `IncomingMessage`/`OutgoingMessage` message models from the blueprint are deferred — `BaseAdapter.send_text(chat_id, text)` is a temporary simplification. Image send/receive, group messages, and the full message model will be added in a follow-up plan.

---

## File Structure

### New Files
- `src/adapters/__init__.py` — package init
- `src/adapters/base.py` — `BaseAdapter` ABC + `ChannelType` enum
- `src/adapters/qq_adapter.py` — `QQAdapter` (OneBot v11 WebSocket client)
- `src/core/channel_manager.py` — `ChannelManager` (multi-adapter orchestrator)
- `tests/adapters/__init__.py` — test package
- `tests/adapters/test_qq_adapter.py` — QQAdapter unit tests
- `tests/core/test_channel_manager.py` — ChannelManager unit tests

### Modified Files
- `src/adapters/telegram_adapter.py` ← rename from `src/app/telegram_app.py` (wrap as BaseAdapter)
- `src/app/telegram_app.py` — keep for Telegram-specific build_application, but delegate send to adapter interface
- `src/core/heartbeat.py` — `HeartbeatAction.execute()` signature: `bot` → `send_fn` callback
- `src/heartbeat/actions/proactive.py` — use `send_fn` instead of `send_telegram_text_to_chat`
- `src/heartbeat/actions/interest_proactive.py` — use `send_fn` instead of `send_telegram_text_to_chat`
- `src/app/container.py` — create ChannelManager, register adapters, wire heartbeat
- `main.py` — launch via ChannelManager
- `config/settings.py` — add QQ config vars
- `config/.env.example` — add QQ config template
- `requirements.txt` — add `websockets>=12.0`

---

### Task 1: Add websockets dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add websockets to requirements.txt**

Add at end of file:
```
websockets>=12.0
```

- [ ] **Step 2: Install the dependency**

Run: `cd /home/kevin/lapwing && pip install websockets>=12.0`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add websockets dependency for QQ adapter"
```

---

### Task 2: Define BaseAdapter ABC and ChannelType enum

**Files:**
- Create: `src/adapters/__init__.py`
- Create: `src/adapters/base.py`

- [ ] **Step 1: Create the adapters package**

`src/adapters/__init__.py`:
```python
```

`src/adapters/base.py`:
```python
"""消息通道适配器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Awaitable, Callable


class ChannelType(Enum):
    TELEGRAM = "telegram"
    QQ = "qq"


class BaseAdapter(ABC):
    """所有消息通道 Adapter 的基类。"""

    channel_type: ChannelType

    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    async def start(self) -> None:
        """启动 Adapter，建立连接，开始监听。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止 Adapter，断开连接。"""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None:
        """发送文本消息到指定 chat_id。"""

    @abstractmethod
    async def is_connected(self) -> bool:
        """检查连接状态。"""
```

- [ ] **Step 2: Commit**

```bash
git add src/adapters/__init__.py src/adapters/base.py
git commit -m "feat: add BaseAdapter ABC and ChannelType enum"
```

---

### Task 3: Create ChannelManager

**Files:**
- Create: `src/core/channel_manager.py`
- Create: `tests/core/test_channel_manager.py`

- [ ] **Step 1: Write the failing test**

`tests/core/test_channel_manager.py`:
```python
"""ChannelManager 单元测试。"""

import pytest

from src.adapters.base import BaseAdapter, ChannelType


class FakeAdapter(BaseAdapter):
    channel_type = ChannelType.TELEGRAM

    def __init__(self):
        super().__init__(config={})
        self.started = False
        self.stopped = False
        self.sent: list[tuple[str, str]] = []
        self._connected = True

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_text(self, chat_id: str, text: str):
        self.sent.append((chat_id, text))

    async def is_connected(self):
        return self._connected


@pytest.mark.asyncio
async def test_register_and_start_all():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.TELEGRAM, adapter)
    await mgr.start_all()
    assert adapter.started


@pytest.mark.asyncio
async def test_stop_all():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.TELEGRAM, adapter)
    await mgr.start_all()
    await mgr.stop_all()
    assert adapter.stopped


@pytest.mark.asyncio
async def test_send_to_channel():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.TELEGRAM, adapter)
    await mgr.send(ChannelType.TELEGRAM, "123", "hello")
    assert adapter.sent == [("123", "hello")]


@pytest.mark.asyncio
async def test_send_to_kevin_uses_last_active():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()

    tg = FakeAdapter()
    tg.channel_type = ChannelType.TELEGRAM
    tg.config = {"kevin_id": "111"}
    mgr.register(ChannelType.TELEGRAM, tg)

    qq = FakeAdapter()
    qq.channel_type = ChannelType.QQ
    qq.config = {"kevin_id": "222"}
    mgr.register(ChannelType.QQ, qq)

    mgr.last_active_channel = ChannelType.QQ
    await mgr.send_to_kevin("hi")
    assert qq.sent == [("222", "hi")]
    assert tg.sent == []


@pytest.mark.asyncio
async def test_send_to_kevin_fallback():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()

    tg = FakeAdapter()
    tg.channel_type = ChannelType.TELEGRAM
    tg.config = {"kevin_id": "111"}
    mgr.register(ChannelType.TELEGRAM, tg)

    # No last_active_channel set — should fallback to first connected
    await mgr.send_to_kevin("hi")
    assert tg.sent == [("111", "hi")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/kevin/lapwing && python -m pytest tests/core/test_channel_manager.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'src.core.channel_manager')

- [ ] **Step 3: Write the implementation**

`src/core/channel_manager.py`:
```python
"""多通道管理器：注册、路由和消息分发。"""

from __future__ import annotations

import logging
from typing import Optional

from src.adapters.base import BaseAdapter, ChannelType

logger = logging.getLogger("lapwing.channel_manager")


class ChannelManager:
    """管理多个消息通道 Adapter 的注册与消息路由。"""

    def __init__(self) -> None:
        self.adapters: dict[ChannelType, BaseAdapter] = {}
        self.last_active_channel: Optional[ChannelType] = None

    def register(self, channel_type: ChannelType, adapter: BaseAdapter) -> None:
        self.adapters[channel_type] = adapter
        logger.info("已注册通道: %s", channel_type.value)

    async def start_all(self) -> None:
        for ch_type, adapter in self.adapters.items():
            await adapter.start()
            logger.info("通道已启动: %s", ch_type.value)

    async def stop_all(self) -> None:
        for ch_type, adapter in self.adapters.items():
            await adapter.stop()
            logger.info("通道已停止: %s", ch_type.value)

    async def send(self, channel: ChannelType, chat_id: str, text: str) -> None:
        adapter = self.adapters.get(channel)
        if adapter and await adapter.is_connected():
            await adapter.send_text(chat_id, text)

    async def send_to_kevin(self, text: str, prefer_channel: Optional[ChannelType] = None) -> None:
        """Heartbeat 主动消息：优先用指定通道，其次用最后活跃通道，最后 fallback。"""
        channel = prefer_channel or self.last_active_channel

        if channel and channel in self.adapters:
            adapter = self.adapters[channel]
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_text(kevin_id, text)
                    return

        for ch_type, adapter in self.adapters.items():
            if await adapter.is_connected():
                kevin_id = adapter.config.get("kevin_id", "")
                if kevin_id:
                    await adapter.send_text(kevin_id, text)
                    return

        logger.warning("所有通道离线，主动消息未发送: %s", text[:50])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/kevin/lapwing && python -m pytest tests/core/test_channel_manager.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/channel_manager.py tests/core/test_channel_manager.py
git commit -m "feat: add ChannelManager for multi-adapter routing"
```

---

### Task 4: Decouple Heartbeat actions from Telegram

The current `HeartbeatAction.execute()` signature is `(ctx, brain, bot)` where `bot` is a Telegram bot object. We need to change `bot` → `send_fn` so heartbeat actions are channel-agnostic. The `send_fn` callback will be `ChannelManager.send_to_kevin`.

**Files:**
- Modify: `src/core/heartbeat.py`
- Modify: `src/heartbeat/actions/proactive.py`
- Modify: `src/heartbeat/actions/interest_proactive.py`
- Modify: `src/heartbeat/actions/autonomous_browsing.py` (update signature)
- Modify: `src/heartbeat/actions/compaction_check.py` (update signature)
- Modify: `src/heartbeat/actions/consolidation.py` (update signature)
- Modify: `src/heartbeat/actions/self_reflection.py` (update signature)
- Modify: `src/heartbeat/actions/prompt_evolution.py` (update signature)

- [ ] **Step 1: Update HeartbeatAction ABC signature**

In `src/core/heartbeat.py`, change the `HeartbeatAction.execute` abstract method signature from:
```python
    @abstractmethod
    async def execute(self, ctx: SenseContext, brain, bot) -> None: ...
```
to:
```python
    @abstractmethod
    async def execute(self, ctx: SenseContext, brain, send_fn) -> None: ...
```

Also update `ProactiveRuntime.__init__` — remove `bot` param, add `send_fn`:
```python
class ProactiveRuntime:
    def __init__(self, brain, send_fn, registry: ActionRegistry, sense: SenseLayer) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._registry = registry
        self._sense = sense
        self._decision_prompt: str | None = None
```

Update `_execute_actions` to pass `self._send_fn` instead of `self._bot`:
```python
    async def _execute_actions(self, actions: list[HeartbeatAction], ctx: SenseContext) -> None:
        for action in actions:
            try:
                await action.execute(ctx, self._brain, self._send_fn)
            except Exception as exc:
                logger.exception(f"[{ctx.chat_id}] action {action.name} 执行失败: {exc}")
```

Update `HeartbeatEngine.__init__` — remove `bot` param, add `send_fn`:
```python
class HeartbeatEngine:
    def __init__(self, brain, send_fn) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._sense = SenseLayer(brain.memory)
        self.registry = ActionRegistry()
        self._runtime = ProactiveRuntime(brain=brain, send_fn=send_fn, registry=self.registry, sense=self._sense)
```

- [ ] **Step 2: Update proactive.py**

In `src/heartbeat/actions/proactive.py`:

Remove the import:
```python
from src.app.telegram_delivery import send_telegram_text_to_chat
```

Update `ProactiveMessageAction.execute`:
```python
    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
```
Replace `await send_telegram_text_to_chat(bot=bot, chat_id=ctx.chat_id, text=reply)` with:
```python
            await send_fn(reply)
```

Update `ReminderDispatchAction.execute`:
```python
    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
```
Replace `await send_telegram_text_to_chat(bot=bot, chat_id=ctx.chat_id, text=message)` with:
```python
                await send_fn(message)
```

- [ ] **Step 3: Update interest_proactive.py**

In `src/heartbeat/actions/interest_proactive.py`:

Remove the import:
```python
from src.app.telegram_delivery import send_telegram_text_to_chat
```

Update signature:
```python
    async def execute(self, ctx: SenseContext, brain, send_fn) -> None:
```
Replace `await send_telegram_text_to_chat(bot=bot, chat_id=ctx.chat_id, text=message)` with:
```python
            await send_fn(message)
```

- [ ] **Step 4: Update all other heartbeat action signatures**

For each of these files, update the `execute` method signature from `(self, ctx, brain, bot)` to `(self, ctx, brain, send_fn)`:
- `src/heartbeat/actions/autonomous_browsing.py`
- `src/heartbeat/actions/compaction_check.py`
- `src/heartbeat/actions/consolidation.py`
- `src/heartbeat/actions/self_reflection.py`
- `src/heartbeat/actions/prompt_evolution.py`

These actions don't use `bot`/`send_fn` but need matching signatures.

- [ ] **Step 5: Update container.py**

In `src/app/container.py`, change `_build_heartbeat(self, bot)` to `_build_heartbeat(self, send_fn)`:
```python
    def _build_heartbeat(self, send_fn) -> HeartbeatEngine:
        heartbeat = HeartbeatEngine(brain=self.brain, send_fn=send_fn)
        # ... rest unchanged
```

And in `start()`:
```python
    async def start(self, *, send_fn=None) -> None:
        ...
        if send_fn is not None:
            self.heartbeat = self._build_heartbeat(send_fn)
            self.heartbeat.start()
```

- [ ] **Step 6: Update TelegramApp._post_init to pass send_fn**

In `src/app/telegram_app.py`, update `_post_init`:
```python
    async def _post_init(self, application) -> None:
        self._bot = application.bot

        async def _telegram_send_to_kevin(text: str) -> None:
            from src.app.telegram_delivery import send_telegram_text_to_chat
            chat_ids = await self._container.brain.memory.get_all_chat_ids()
            for chat_id in chat_ids:
                await send_telegram_text_to_chat(bot=self._bot, chat_id=chat_id, text=text)

        await self._container.start(send_fn=_telegram_send_to_kevin)
```

Note: This is a temporary bridge. In Task 7 (main.py integration), `send_fn` will be replaced with `channel_manager.send_to_kevin`.

- [ ] **Step 7: Run existing tests**

Run: `cd /home/kevin/lapwing && python -m pytest tests/ -x -q`
Expected: All existing tests still pass (signature changes are compatible)

- [ ] **Step 8: Commit**

```bash
git add src/core/heartbeat.py src/heartbeat/actions/ src/app/container.py src/app/telegram_app.py
git commit -m "refactor: decouple heartbeat actions from Telegram — use send_fn callback"
```

---

### Task 5: Add QQ configuration to settings

**Files:**
- Modify: `config/settings.py`
- Modify: `config/.env.example`

- [ ] **Step 1: Add QQ settings**

In `config/settings.py`, after the Telegram section (after line 88), add:
```python
TELEGRAM_KEVIN_ID: str = os.getenv("TELEGRAM_KEVIN_ID", "")

# QQ (NapCat OneBot v11)
QQ_ENABLED: bool = os.getenv("QQ_ENABLED", "false").lower() == "true"
QQ_WS_URL: str = os.getenv("QQ_WS_URL", "ws://127.0.0.1:3001")
QQ_ACCESS_TOKEN: str = os.getenv("QQ_ACCESS_TOKEN", "")
QQ_SELF_ID: str = os.getenv("QQ_SELF_ID", "")
QQ_KEVIN_ID: str = os.getenv("QQ_KEVIN_ID", "")
```

- [ ] **Step 2: Add to .env.example**

Append to `config/.env.example`:
```
TELEGRAM_KEVIN_ID=  # Kevin 的 Telegram user ID（用于 Heartbeat 主动消息路由）

# QQ (NapCat OneBot v11)
QQ_ENABLED=false
QQ_WS_URL=ws://127.0.0.1:3001
QQ_ACCESS_TOKEN=
QQ_SELF_ID=
QQ_KEVIN_ID=
```

- [ ] **Step 3: Commit**

```bash
git add config/settings.py config/.env.example
git commit -m "feat: add QQ channel configuration settings"
```

---

### Task 6: Implement QQAdapter

**Files:**
- Create: `src/adapters/qq_adapter.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/test_qq_adapter.py`

- [ ] **Step 1: Write tests for QQAdapter message parsing**

`tests/adapters/__init__.py`:
```python
```

`tests/adapters/test_qq_adapter.py`:
```python
"""QQAdapter 单元测试。"""

import pytest

from src.adapters.qq_adapter import QQAdapter


def _make_adapter(**overrides) -> QQAdapter:
    config = {
        "ws_url": "ws://127.0.0.1:3001",
        "access_token": "test",
        "self_id": "100",
        "kevin_id": "200",
        **overrides,
    }
    return QQAdapter(config=config)


class TestExtractText:
    def test_string_message(self):
        adapter = _make_adapter()
        event = {"message": "你好"}
        assert adapter._extract_text(event) == "你好"

    def test_array_message(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "text", "data": {"text": "hello "}},
                {"type": "text", "data": {"text": "world"}},
            ]
        }
        assert adapter._extract_text(event) == "hello world"

    def test_array_with_at_segment(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "at", "data": {"qq": "100"}},
                {"type": "text", "data": {"text": "你好"}},
            ]
        }
        assert adapter._extract_text(event) == "你好"

    def test_empty_message(self):
        adapter = _make_adapter()
        assert adapter._extract_text({"message": ""}) == ""
        assert adapter._extract_text({"message": []}) == ""


class TestExtractImage:
    def test_no_image(self):
        adapter = _make_adapter()
        event = {"message": [{"type": "text", "data": {"text": "hi"}}]}
        assert adapter._extract_image(event) is None

    def test_has_image(self):
        adapter = _make_adapter()
        event = {
            "message": [
                {"type": "image", "data": {"url": "https://example.com/img.png"}},
            ]
        }
        assert adapter._extract_image(event) == "https://example.com/img.png"

    def test_string_message_no_image(self):
        adapter = _make_adapter()
        assert adapter._extract_image({"message": "text"}) is None


class TestMarkdownToPlain:
    def test_bold(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("**bold**") == "bold"

    def test_italic(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("*italic*") == "italic"

    def test_code_block(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("```python\nprint(1)\n```") == "print(1)\n"

    def test_inline_code(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("`code`") == "code"

    def test_link(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("[text](url)") == "text (url)"

    def test_plain_text_unchanged(self):
        adapter = _make_adapter()
        assert adapter._markdown_to_plain("hello world") == "hello world"


class TestSplitText:
    def test_short_text(self):
        adapter = _make_adapter()
        assert adapter._split_text("short", 100) == ["short"]

    def test_split_at_newline(self):
        adapter = _make_adapter()
        text = "line1\nline2\nline3"
        chunks = adapter._split_text(text, 10)
        assert all(len(c) <= 10 for c in chunks)
        recombined = "\n".join(chunks)
        assert "line1" in recombined
        assert "line3" in recombined

    def test_split_long_no_newline(self):
        adapter = _make_adapter()
        text = "a" * 20
        chunks = adapter._split_text(text, 8)
        assert all(len(c) <= 8 for c in chunks)
        assert "".join(chunks) == text


class TestBuildMessageSegments:
    def test_text_only(self):
        adapter = _make_adapter()
        segments = adapter._build_message_segments("hello", None)
        assert segments == [{"type": "text", "data": {"text": "hello"}}]

    def test_with_image(self):
        adapter = _make_adapter()
        segments = adapter._build_message_segments("caption", "base64data")
        assert len(segments) == 2
        assert segments[0]["type"] == "text"
        assert segments[1]["type"] == "image"
        assert "base64://" in segments[1]["data"]["file"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/lapwing && python -m pytest tests/adapters/test_qq_adapter.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement QQAdapter**

`src/adapters/qq_adapter.py`:
```python
"""QQ 通道适配器 — 通过 NapCat (OneBot v11) WebSocket 收发消息。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

import websockets

from src.adapters.base import BaseAdapter, ChannelType

logger = logging.getLogger("lapwing.adapter.qq")

MAX_QQ_MSG_LENGTH = 4000


class QQAdapter(BaseAdapter):
    """OneBot v11 WebSocket 客户端适配器。"""

    channel_type = ChannelType.QQ

    def __init__(
        self,
        config: dict,
        on_message: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        super().__init__(config)
        self.on_message = on_message
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_url: str = config.get("ws_url", "ws://127.0.0.1:3001")
        self.access_token: str = config.get("access_token", "")
        self.self_id: str = str(config.get("self_id", ""))
        self.kevin_id: str = str(config.get("kevin_id", ""))
        self._reconnect_delay = 5
        self._max_reconnect_delay = 300
        self._running = False
        self._echo_futures: dict[str, asyncio.Future] = {}
        self._message_dedup: dict[str, float] = {}
        self._connection_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._running = True
        self._connection_task = asyncio.create_task(self._connection_loop())
        logger.info("QQ adapter 启动中，连接 %s", self.ws_url)

    async def stop(self) -> None:
        self._running = False
        if self.ws:
            await self.ws.close()
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        logger.info("QQ adapter 已停止")

    async def is_connected(self) -> bool:
        return self.ws is not None and self.ws.open

    async def send_text(self, chat_id: str, text: str) -> None:
        text = self._markdown_to_plain(text)
        if len(text) <= MAX_QQ_MSG_LENGTH:
            await self._send_private_msg(chat_id, text)
        else:
            chunks = self._split_text(text, MAX_QQ_MSG_LENGTH)
            for chunk in chunks:
                await self._send_private_msg(chat_id, chunk)
                await asyncio.sleep(0.5)

    # ── WebSocket 连接管理 ──────────────────────────────

    async def _connection_loop(self) -> None:
        delay = self._reconnect_delay
        while self._running:
            try:
                headers = {}
                if self.access_token:
                    headers["Authorization"] = f"Bearer {self.access_token}"
                async with websockets.connect(
                    self.ws_url,
                    extra_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.ws = ws
                    delay = self._reconnect_delay
                    logger.info("QQ adapter 已连接到 %s", self.ws_url)
                    await self._listen(ws)
            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as exc:
                self.ws = None
                if self._running:
                    logger.warning("QQ 连接断开 (%s)，%ds 后重连", exc, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)

    async def _listen(self, ws) -> None:
        async for raw_msg in ws:
            try:
                data = json.loads(raw_msg)
                if "echo" in data:
                    echo = data["echo"]
                    future = self._echo_futures.get(echo)
                    if future and not future.done():
                        future.set_result(data)
                elif "post_type" in data:
                    await self._handle_event(data)
            except json.JSONDecodeError:
                logger.warning("QQ 收到无效 JSON: %s", raw_msg[:200])

    # ── 事件处理 ────────────────────────────────────────

    async def _handle_event(self, event: dict) -> None:
        post_type = event.get("post_type")
        if post_type == "meta_event":
            return
        if post_type == "message":
            await self._handle_message_event(event)

    async def _handle_message_event(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        message_id = str(event.get("message_id", ""))

        if user_id == self.self_id:
            return

        # 消息去重
        dedup_key = f"{user_id}:{message_id}"
        now = time.time()
        if dedup_key in self._message_dedup:
            return
        self._message_dedup[dedup_key] = now
        self._message_dedup = {k: v for k, v in self._message_dedup.items() if now - v < 60}

        # 只处理 Kevin 的消息
        if self.kevin_id and user_id != self.kevin_id:
            return

        text = self._extract_text(event)
        if not text:
            return

        if self.on_message:
            await self.on_message(
                chat_id=user_id,
                text=text,
                channel=ChannelType.QQ,
                raw_event=event,
            )

    # ── 消息解析 ────────────────────────────────────────

    def _extract_text(self, event: dict) -> str:
        message = event.get("message", "")
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            for seg in message:
                if seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            return "".join(parts).strip()
        return str(message)

    def _extract_image(self, event: dict) -> Optional[str]:
        message = event.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "image":
                    return seg.get("data", {}).get("url", "")
        return None

    # ── 发送消息 ────────────────────────────────────────

    async def _send_private_msg(self, user_id: str, text: str) -> dict:
        return await self._call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": self._build_message_segments(text, None),
        })

    def _build_message_segments(self, text: str, image_base64: Optional[str] = None) -> list:
        segments = []
        if text:
            segments.append({"type": "text", "data": {"text": text}})
        if image_base64:
            segments.append({"type": "image", "data": {"file": f"base64://{image_base64}"}})
        return segments

    async def _call_api(self, action: str, params: dict, timeout: float = 30.0) -> dict:
        if not self.ws or not self.ws.open:
            return {"status": "failed", "retcode": -1}

        echo = f"{action}_{time.time()}"
        request = {"action": action, "params": params, "echo": echo}
        future = asyncio.get_running_loop().create_future()
        self._echo_futures[echo] = future

        try:
            await self.ws.send(json.dumps(request))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("QQ API 超时: %s", action)
            return {"status": "failed", "retcode": -2}
        finally:
            self._echo_futures.pop(echo, None)

    # ── 格式转换 ────────────────────────────────────────

    def _markdown_to_plain(self, text: str) -> str:
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'```\w*\n?', '', text)
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
        return text

    def _split_text(self, text: str, max_length: int) -> list[str]:
        if len(text) <= max_length:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, max_length)
            if split_at <= 0:
                split_at = max_length
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kevin/lapwing && python -m pytest tests/adapters/test_qq_adapter.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/adapters/qq_adapter.py tests/adapters/__init__.py tests/adapters/test_qq_adapter.py
git commit -m "feat: implement QQAdapter with OneBot v11 WebSocket support"
```

---

### Task 7: Wire everything together in container.py and main.py

**Files:**
- Modify: `src/app/container.py`
- Modify: `src/app/telegram_app.py`
- Modify: `main.py`

- [ ] **Step 1: Update container.py to create ChannelManager**

Add ChannelManager to `AppContainer`:

```python
# Add import at top
from src.core.channel_manager import ChannelManager

# In __init__, add:
        self.channel_manager = ChannelManager()

# Replace start() method:
    async def start(self, *, bot=None, send_fn=None) -> None:
        if self._started:
            return
        await self.prepare()

        # If send_fn provided (from ChannelManager), use it for heartbeat
        effective_send_fn = send_fn
        if effective_send_fn is None and bot is not None:
            # Legacy path: wrap telegram bot as send_fn
            async def _legacy_send(text: str) -> None:
                from src.app.telegram_delivery import send_telegram_text_to_chat
                chat_ids = await self.brain.memory.get_all_chat_ids()
                for chat_id in chat_ids:
                    await send_telegram_text_to_chat(bot=bot, chat_id=chat_id, text=text)
            effective_send_fn = _legacy_send

        if effective_send_fn is not None:
            self.heartbeat = self._build_heartbeat(effective_send_fn)
            self.heartbeat.start()

        await self.api_server.start()
        self._started = True
        logger.info("应用容器启动完成")
```

- [ ] **Step 2: Update TelegramApp._post_init to use ChannelManager**

In `src/app/telegram_app.py`, update `_post_init`:
```python
    async def _post_init(self, application) -> None:
        self._bot = application.bot
        await self._container.start(send_fn=self._container.channel_manager.send_to_kevin)
```

- [ ] **Step 3: Create TelegramAdapter wrapper**

The existing `TelegramApp` handles all the Telegram-specific logic (commands, message buffering, etc). We don't fully rewrite it — instead we make it register itself with ChannelManager by implementing a lightweight `BaseAdapter` wrapper inside `telegram_app.py`.

Add to `src/app/telegram_app.py`:

```python
from src.adapters.base import BaseAdapter, ChannelType

class TelegramChannelAdapter(BaseAdapter):
    """Thin BaseAdapter wrapper around TelegramApp for ChannelManager registration."""

    channel_type = ChannelType.TELEGRAM

    def __init__(self, telegram_app: 'TelegramApp', config: dict) -> None:
        super().__init__(config)
        self._telegram_app = telegram_app

    async def start(self) -> None:
        pass  # TelegramApp lifecycle managed by python-telegram-bot

    async def stop(self) -> None:
        pass  # TelegramApp lifecycle managed by python-telegram-bot

    async def send_text(self, chat_id: str, text: str) -> None:
        from src.app.telegram_delivery import send_telegram_text_to_chat
        bot = self._telegram_app._bot
        if bot is None:
            return
        try:
            numeric_id = int(chat_id)
        except ValueError:
            numeric_id = chat_id
        await send_telegram_text_to_chat(bot=bot, chat_id=numeric_id, text=text)

    async def is_connected(self) -> bool:
        return self._telegram_app._bot is not None
```

Update `_post_init` to register this adapter:
```python
    async def _post_init(self, application) -> None:
        self._bot = application.bot

        from config.settings import TELEGRAM_TOKEN
        tg_adapter = TelegramChannelAdapter(
            telegram_app=self,
            config={"kevin_id": str(list(await self._container.brain.memory.get_all_chat_ids())[0]) if await self._container.brain.memory.get_all_chat_ids() else ""},
        )
        self._container.channel_manager.register(ChannelType.TELEGRAM, tg_adapter)

        await self._container.start(send_fn=self._container.channel_manager.send_to_kevin)
```

Simpler: use `TELEGRAM_KEVIN_ID` from config, passed as `tg_config` from `main.py`:

```python
    async def _post_init(self, application) -> None:
        self._bot = application.bot

        tg_adapter = TelegramChannelAdapter(
            telegram_app=self,
            config=self._tg_config,
        )
        self._container.channel_manager.register(ChannelType.TELEGRAM, tg_adapter)

        await self._container.start(send_fn=self._container.channel_manager.send_to_kevin)
```

Store `_tg_config` in `__init__`:
```python
    def __init__(self, container, tg_config: dict | None = None) -> None:
        self._container = container
        self._tg_config = tg_config or {}
        # ... rest unchanged
```

- [ ] **Step 4: Update main.py to register QQAdapter**

```python
def run_telegram_bot(logger: logging.Logger) -> int:
    from src.app.container import AppContainer
    from src.app.telegram_app import TelegramApp

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
        return 1

    logger.info("Lapwing 正在启动...")
    container = AppContainer(db_path=DB_PATH, data_dir=DATA_DIR)

    from config.settings import TELEGRAM_KEVIN_ID

    # TelegramApp handles its own adapter registration in _post_init
    telegram_app = TelegramApp(container=container, tg_config={"kevin_id": TELEGRAM_KEVIN_ID})
    container.telegram_app = telegram_app

    # Register QQ adapter if enabled
    from config.settings import QQ_ENABLED
    if QQ_ENABLED:
        from config.settings import QQ_WS_URL, QQ_ACCESS_TOKEN, QQ_SELF_ID, QQ_KEVIN_ID
        from src.adapters.base import ChannelType
        from src.adapters.qq_adapter import QQAdapter

        qq_config = {
            "ws_url": QQ_WS_URL,
            "access_token": QQ_ACCESS_TOKEN,
            "self_id": QQ_SELF_ID,
            "kevin_id": QQ_KEVIN_ID,
        }

        async def _qq_on_message(chat_id: str, text: str, channel, raw_event: dict) -> None:
            """QQ 消息进入 Brain 的桥接。"""
            container.channel_manager.last_active_channel = channel
            brain = container.brain

            async def send_fn(reply_text: str) -> None:
                await container.channel_manager.send(ChannelType.QQ, chat_id, reply_text)

            async def typing_fn() -> None:
                pass  # QQ 无 typing indicator

            async def noop_status(cid: str, t: str) -> None:
                pass

            reply = await brain.think_conversational(
                chat_id,
                text,
                send_fn=send_fn,
                typing_fn=typing_fn,
                status_callback=noop_status,
            )

        qq_adapter = QQAdapter(config=qq_config, on_message=_qq_on_message)
        container.channel_manager.register(ChannelType.QQ, qq_adapter)
        logger.info("QQ 通道已注册")

    app = telegram_app.build_application(
        token=TELEGRAM_TOKEN,
        proxy_url=TELEGRAM_PROXY_URL,
    )
    app.run_polling(drop_pending_updates=True)
    logger.info("Lapwing 已关闭")
    return 0
```

- [ ] **Step 5: Handle QQAdapter lifecycle in container**

Add to `container.py` `start()` — after heartbeat setup, start the ChannelManager:
```python
        await self.channel_manager.start_all()
```

Add to `shutdown()`:
```python
        await self.channel_manager.stop_all()
```

- [ ] **Step 6: Run existing tests**

Run: `cd /home/kevin/lapwing && python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add main.py src/app/container.py src/app/telegram_app.py
git commit -m "feat: wire ChannelManager with Telegram and QQ adapters in main startup"
```

---

### Task 8: Add channel field to conversations table

**Files:**
- Modify: `src/memory/conversation.py`

- [ ] **Step 1: Add channel column migration**

In `ConversationMemory._create_tables()`, the existing `CREATE TABLE IF NOT EXISTS` won't alter existing tables. Add a migration after table creation:

```python
    async def _create_tables(self) -> None:
        await self._db.executescript("""...""")  # existing
        # Migration: add channel column if missing
        try:
            await self._db.execute(
                "ALTER TABLE conversations ADD COLUMN channel TEXT DEFAULT 'telegram'"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists
```

- [ ] **Step 2: Update append() to accept channel parameter**

In the `append` method, add an optional `channel` parameter:

Find the `append` method signature and add `channel: str = "telegram"` parameter. Update the INSERT to include the channel column.

- [ ] **Step 3: Run existing tests**

Run: `cd /home/kevin/lapwing && python -m pytest tests/ -x -q`
Expected: All tests pass (channel defaults to "telegram")

- [ ] **Step 4: Commit**

```bash
git add src/memory/conversation.py
git commit -m "feat: add channel column to conversations table"
```

---

### Task 9: Track last_active_channel from Telegram messages

**Files:**
- Modify: `src/app/telegram_app.py`

- [ ] **Step 1: Set last_active_channel on Telegram messages**

In `TelegramApp._think_and_reply()`, before calling brain, set:
```python
        self._container.channel_manager.last_active_channel = ChannelType.TELEGRAM
```

(Import ChannelType at top if not already imported.)

- [ ] **Step 2: Commit**

```bash
git add src/app/telegram_app.py
git commit -m "feat: track last active channel for Telegram messages"
```

---

### Task 10: Integration smoke test

- [ ] **Step 1: Verify Telegram still works**

Run: `cd /home/kevin/lapwing && python -c "from src.app.container import AppContainer; from src.app.telegram_app import TelegramApp; print('import OK')"`

- [ ] **Step 2: Verify QQ adapter imports**

Run: `cd /home/kevin/lapwing && python -c "from src.adapters.qq_adapter import QQAdapter; from src.core.channel_manager import ChannelManager; print('QQ imports OK')"`

- [ ] **Step 3: Run full test suite**

Run: `cd /home/kevin/lapwing && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit any fixes if needed**

---

## Notes

- **TelegramApp is NOT fully rewritten.** It keeps its existing architecture (build_application, run_polling, command handlers). A thin `TelegramChannelAdapter` wrapper registers it with ChannelManager for send_to_kevin routing.
- **QQAdapter.on_message** is a callback that bridges into `brain.think_conversational()` directly — same as TelegramApp does, just without the message buffering and Telegram-specific features.
- **QQ commands** (like /memory, /model) are not implemented in this phase. QQ is text-only for now.
- **Group messages** are not handled — QQAdapter only processes private messages from Kevin.
- **NapCat must be running** for QQAdapter to connect. If NapCat is down, QQAdapter retries with exponential backoff.
