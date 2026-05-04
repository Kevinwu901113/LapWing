from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.logging.state_mutation_log import MutationType, iteration_context
from src.tools.personal_tools import _send_message
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


class _MutationLog:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def query_by_iteration(self, iteration_id):
        return list(self._rows)


def _tool_result(tool_name="research", *, cache_hit=False, age_seconds=1):
    return SimpleNamespace(
        event_type=MutationType.TOOL_RESULT.value,
        timestamp=time.time() - age_seconds,
        payload={
            "tool_name": tool_name,
            "success": True,
            "payload": {"cache_hit": cache_hit},
            "reason": "confidence=0.9",
        },
    )


def _ctx(*, mutation_log=None):
    qq = MagicMock()
    qq.send_private_message = AsyncMock()
    cm = MagicMock()
    cm.get_adapter = MagicMock(return_value=qq)
    services = {
        "channel_manager": cm,
        "owner_qq_id": "12345",
        "proactive_send_active": True,
    }
    if mutation_log is not None:
        services["mutation_log"] = mutation_log
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
        runtime_profile="inner_tick",
        chat_id="chat1",
    ), qq


async def _run(content: str, *, rows=()):
    ctx, qq = _ctx(mutation_log=_MutationLog(rows))
    req = ToolExecutionRequest(
        name="send_message",
        arguments={"target": "kevin_qq", "content": content},
    )
    with iteration_context("iter1", "chat1"):
        result = await _send_message(req, ctx)
    return result, qq


@pytest.mark.asyncio
async def test_factual_claim_without_research_is_blocked():
    result, qq = await _run("刚搜到 1-0 领先")
    assert not result.success
    assert result.reason == "factual_claim_requires_fresh_search"
    qq.send_private_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_hit_research_is_not_enough():
    result, qq = await _run("刚搜到 1-0 领先", rows=[_tool_result(cache_hit=True)])
    assert not result.success
    qq.send_private_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_research_allows_factual_claim():
    result, qq = await _run("刚搜到 1-0 领先", rows=[_tool_result(cache_hit=False)])
    assert result.success
    qq.send_private_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_softened_cache_wording_allows_claim():
    result, qq = await _run("之前查到的信息显示 1-0 领先")
    assert result.success
    qq.send_private_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_factual_message_allows_send():
    result, qq = await _run("在干嘛")
    assert result.success
    qq.send_private_message.assert_awaited_once()
