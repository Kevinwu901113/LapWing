"""session_search 工具执行器测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.tools.session_search import session_search_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_context(*, memory=None, chat_id: str = "test_chat") -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        memory=memory,
        chat_id=chat_id,
    )


# ── 参数校验 ────────────────────────────────────────────────────────────────────

class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_query(self):
        req = ToolExecutionRequest(name="session_search", arguments={})
        result = await session_search_executor(req, _make_context(memory=MagicMock()))
        assert not result.success
        assert "query" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_empty_query(self):
        req = ToolExecutionRequest(name="session_search", arguments={"query": "  "})
        result = await session_search_executor(req, _make_context(memory=MagicMock()))
        assert not result.success

    @pytest.mark.asyncio
    async def test_no_memory(self):
        req = ToolExecutionRequest(name="session_search", arguments={"query": "test"})
        result = await session_search_executor(req, _make_context(memory=None))
        assert not result.success
        assert "记忆" in (result.reason or "") or "记忆" in str(result.payload)


# ── 搜索结果 ────────────────────────────────────────────────────────────────────

class TestResults:
    @pytest.mark.asyncio
    async def test_empty_results(self):
        memory = MagicMock()
        memory.search_history = AsyncMock(return_value=[])
        req = ToolExecutionRequest(name="session_search", arguments={"query": "不存在"})
        result = await session_search_executor(req, _make_context(memory=memory))
        assert result.success
        assert "未找到" in result.payload["output"]

    @pytest.mark.asyncio
    async def test_formatted_output(self):
        memory = MagicMock()
        memory.search_history = AsyncMock(return_value=[
            {
                "message_id": 1, "chat_id": "c1", "role": "user",
                "content": "明天有什么安排",
                "timestamp": "2026-04-01 10:00:00",
                "session_id": "s1", "context": [],
            },
            {
                "message_id": 2, "chat_id": "c1", "role": "assistant",
                "content": "你明天下午有一个会议",
                "timestamp": "2026-04-01 10:00:30",
                "session_id": "s1", "context": [],
            },
        ])
        req = ToolExecutionRequest(name="session_search", arguments={"query": "安排"})
        result = await session_search_executor(req, _make_context(memory=memory))
        assert result.success
        output = result.payload["output"]
        assert "Kevin" in output          # user → Kevin
        assert "Lapwing" in output        # assistant → Lapwing
        assert "2026-04-01 10:00" in output  # 时间戳截断到 16 字符

    @pytest.mark.asyncio
    async def test_content_truncated_at_200(self):
        long_content = "啊" * 300
        memory = MagicMock()
        memory.search_history = AsyncMock(return_value=[{
            "message_id": 1, "chat_id": "c1", "role": "user",
            "content": long_content,
            "timestamp": "2026-04-01 10:00:00",
            "session_id": "s1", "context": [],
        }])
        req = ToolExecutionRequest(name="session_search", arguments={"query": "啊"})
        result = await session_search_executor(req, _make_context(memory=memory))
        assert result.success
        assert "..." in result.payload["output"]

    @pytest.mark.asyncio
    async def test_context_messages_included(self):
        memory = MagicMock()
        memory.search_history = AsyncMock(return_value=[{
            "message_id": 1, "chat_id": "c1", "role": "user",
            "content": "主消息",
            "timestamp": "2026-04-01 10:00:00",
            "session_id": "s1",
            "context": [
                {"role": "assistant", "content": "上下文回复"},
            ],
        }])
        req = ToolExecutionRequest(name="session_search", arguments={"query": "主消息"})
        result = await session_search_executor(req, _make_context(memory=memory))
        assert "↳" in result.payload["output"]
        assert "Lapwing" in result.payload["output"]


# ── 异常处理 ────────────────────────────────────────────────────────────────────

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_exception(self):
        memory = MagicMock()
        memory.search_history = AsyncMock(side_effect=RuntimeError("db error"))
        req = ToolExecutionRequest(name="session_search", arguments={"query": "test"})
        result = await session_search_executor(req, _make_context(memory=memory))
        assert not result.success
        assert "db error" in str(result.payload)


# ── 参数传递 ────────────────────────────────────────────────────────────────────

class TestParameters:
    @pytest.mark.asyncio
    async def test_days_back_passed_as_int(self):
        memory = MagicMock()
        memory.search_history = AsyncMock(return_value=[])
        req = ToolExecutionRequest(
            name="session_search",
            arguments={"query": "test", "days_back": "7"},
        )
        await session_search_executor(req, _make_context(memory=memory))
        call_kwargs = memory.search_history.call_args
        assert call_kwargs[1]["days_back"] == 7  # str → int 转换

    @pytest.mark.asyncio
    async def test_chat_id_passed_through(self):
        memory = MagicMock()
        memory.search_history = AsyncMock(return_value=[])
        req = ToolExecutionRequest(name="session_search", arguments={"query": "test"})
        await session_search_executor(req, _make_context(memory=memory, chat_id="my_chat"))
        call_kwargs = memory.search_history.call_args
        assert call_kwargs[1]["chat_id"] == "my_chat"
