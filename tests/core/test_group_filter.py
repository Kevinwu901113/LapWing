"""GroupMessageFilter 单元测试。"""

from unittest.mock import AsyncMock

import pytest

from src.core.group_filter import GroupMessageFilter


@pytest.fixture
def mock_router():
    router = AsyncMock()
    router.simple_completion = AsyncMock(return_value="enter")
    return router


@pytest.fixture
def gf(mock_router):
    return GroupMessageFilter(llm_router=mock_router)


# ── 1. @me always enters ──

async def test_at_me_always_enters(gf: GroupMessageFilter, mock_router):
    result = await gf.filter("随便什么内容", sender_name="Alice", is_at_me=True)
    assert result == "enter"
    # 不应调用 LLM
    mock_router.simple_completion.assert_not_called()


# ── 2. empty message discards ──

async def test_empty_message_discards(gf: GroupMessageFilter, mock_router):
    result = await gf.filter("", sender_name="Bob", is_at_me=False)
    assert result == "discard"
    mock_router.simple_completion.assert_not_called()


# ── 3. emoji only discards ──

async def test_emoji_only_discards(gf: GroupMessageFilter, mock_router):
    result = await gf.filter("😀🎉🔥", sender_name="Carol", is_at_me=False)
    assert result == "discard"
    mock_router.simple_completion.assert_not_called()


# ── 4. short message discards ──

async def test_short_message_discards(gf: GroupMessageFilter, mock_router):
    result = await gf.filter("ok", sender_name="Dave", is_at_me=False)
    assert result == "discard"
    mock_router.simple_completion.assert_not_called()


# ── 5. LLM returns enter ──

async def test_llm_returns_enter(gf: GroupMessageFilter, mock_router):
    mock_router.simple_completion.return_value = "enter"
    result = await gf.filter(
        "有人知道明天天气怎么样吗", sender_name="Eve", is_at_me=False
    )
    assert result == "enter"
    mock_router.simple_completion.assert_called_once()


# ── 6. LLM returns cache ──

async def test_llm_returns_cache(gf: GroupMessageFilter, mock_router):
    mock_router.simple_completion.return_value = "cache"
    result = await gf.filter(
        "今天中午吃什么好呢", sender_name="Frank", is_at_me=False
    )
    assert result == "cache"


# ── 7. LLM returns discard ──

async def test_llm_returns_discard(gf: GroupMessageFilter, mock_router):
    mock_router.simple_completion.return_value = "discard"
    result = await gf.filter(
        "哈哈哈哈哈哈", sender_name="Grace", is_at_me=False
    )
    assert result == "discard"


# ── 8. LLM error defaults to cache ──

async def test_llm_error_defaults_cache(gf: GroupMessageFilter, mock_router):
    mock_router.simple_completion.side_effect = RuntimeError("API 超时")
    result = await gf.filter(
        "这条消息会触发异常", sender_name="Heidi", is_at_me=False
    )
    assert result == "cache"


# ── 9. cache deduplication ──

async def test_cache_deduplication(gf: GroupMessageFilter, mock_router):
    mock_router.simple_completion.return_value = "enter"

    msg = "这是一条需要去重的消息内容"
    sender = "Ivan"

    result1 = await gf.filter(msg, sender_name=sender, is_at_me=False)
    assert result1 == "enter"
    assert mock_router.simple_completion.call_count == 1

    # 相同消息第二次调用，应命中缓存，LLM 不再调用
    result2 = await gf.filter(msg, sender_name=sender, is_at_me=False)
    assert result2 == "enter"
    assert mock_router.simple_completion.call_count == 1  # 仍然是 1 次
