"""BrowserAgent 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import AgentTask
from src.agents.browser import BrowserAgent
from src.tools.web_fetcher import FetchResult


def make_task(user_message: str = "帮我看看这个 https://example.com/article") -> AgentTask:
    return AgentTask(
        chat_id="42",
        user_message=user_message,
        history=[],
        user_facts=[],
    )


def make_memory() -> MagicMock:
    memory = MagicMock()
    memory.add_discovery = AsyncMock()
    return memory


@pytest.mark.asyncio
class TestBrowserAgent:
    async def test_extracts_url_and_fetches(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(return_value="这是摘要")
        fetch_result = FetchResult(
            url="https://example.com/article",
            title="文章标题",
            text="网页正文",
            success=True,
            error="",
        )

        with patch("src.agents.browser.load_prompt", return_value="{user_message}\n{title}\n{url}\n{page_text}"), \
             patch("src.agents.browser.web_fetcher.fetch", AsyncMock(return_value=fetch_result)) as mock_fetch:
            agent = BrowserAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert result.content == "这是摘要"
        assert result.metadata["url"] == "https://example.com/article"
        mock_fetch.assert_awaited_once_with("https://example.com/article")

    async def test_no_url_in_message_returns_hint(self):
        router = MagicMock()
        router.complete = AsyncMock()
        agent = BrowserAgent(memory=make_memory())

        result = await agent.execute(make_task("帮我看看这个页面"), router)

        assert "请提供一个网址" in result.content
        router.complete.assert_not_called()

    async def test_fetch_failure_returns_error(self):
        router = MagicMock()
        router.complete = AsyncMock()
        fetch_result = FetchResult(
            url="https://example.com/article",
            title="",
            text="",
            success=False,
            error="请求超时",
        )

        with patch("src.agents.browser.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            agent = BrowserAgent(memory=make_memory())
            result = await agent.execute(make_task(), router)

        assert "打不开" in result.content
        assert "请求超时" in result.content
        router.complete.assert_not_called()

    async def test_saves_discovery(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(return_value="这是摘要")
        fetch_result = FetchResult(
            url="https://example.com/article",
            title="文章标题",
            text="网页正文",
            success=True,
            error="",
        )

        with patch("src.agents.browser.load_prompt", return_value="{user_message} {title} {url} {page_text}"), \
             patch("src.agents.browser.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            agent = BrowserAgent(memory=memory)
            await agent.execute(make_task(), router)

        memory.add_discovery.assert_awaited_once()
        call_kwargs = memory.add_discovery.call_args.kwargs
        assert call_kwargs["source"] == "browsing"
        assert call_kwargs["title"] == "文章标题"
        assert call_kwargs["url"] == "https://example.com/article"

    async def test_uses_tool_purpose(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(return_value="这是摘要")
        fetch_result = FetchResult(
            url="https://example.com/article",
            title="文章标题",
            text="网页正文",
            success=True,
            error="",
        )

        with patch("src.agents.browser.load_prompt", return_value="{user_message} {title} {url} {page_text}"), \
             patch("src.agents.browser.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            agent = BrowserAgent(memory=memory)
            await agent.execute(make_task(), router)

        assert router.complete.call_args.kwargs["purpose"] == "tool"

    async def test_llm_failure_returns_graceful_error(self):
        memory = make_memory()
        router = MagicMock()
        router.complete = AsyncMock(side_effect=RuntimeError("LLM error"))
        fetch_result = FetchResult(
            url="https://example.com/article",
            title="文章标题",
            text="网页正文",
            success=True,
            error="",
        )

        with patch("src.agents.browser.load_prompt", return_value="{user_message} {title} {url} {page_text}"), \
             patch("src.agents.browser.web_fetcher.fetch", AsyncMock(return_value=fetch_result)):
            agent = BrowserAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert "整理" in result.content
        memory.add_discovery.assert_not_awaited()
