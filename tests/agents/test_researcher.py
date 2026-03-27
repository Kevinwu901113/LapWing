"""ResearcherAgent 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.base import AgentTask
from src.agents.researcher import ResearcherAgent
from src.tools.web_fetcher import FetchResult


# ---- 辅助 ----

def make_task(user_message: str = "Python 3.13 有什么新特性？") -> AgentTask:
    return AgentTask(
        chat_id="42",
        user_message=user_message,
        history=[],
        user_facts=[],
    )


def make_router(tool_replies: list[str]) -> MagicMock:
    """创建模拟 router，按顺序返回 tool_replies 中的内容。"""
    router = MagicMock()
    router.complete = AsyncMock(side_effect=tool_replies)
    return router


def make_memory() -> MagicMock:
    memory = MagicMock()
    memory.add_discovery = AsyncMock()
    return memory


FAKE_RESULTS = [
    {"title": "Python 3.13 新特性", "url": "https://example.com/py313", "snippet": "新增 JIT 编译器..."},
    {"title": "Python 3.13 发布说明", "url": "https://python.org/313", "snippet": "修复了若干 Bug..."},
]

FAKE_QUERIES_JSON = '["Python 3.13 新特性", "Python 3.13 release notes"]'
FAKE_SUMMARY = "Python 3.13 引入了实验性 JIT 编译器，提升了性能。"


# ---- 测试：关键词提取 ----

class TestExtractQueries:
    @pytest.mark.asyncio
    async def test_extracts_queries_from_user_message(self):
        """正常情况：提取出查询词列表。"""
        with patch("src.agents.researcher.load_prompt", return_value="prompt {user_message}"):
            router = make_router([FAKE_QUERIES_JSON])
            agent = ResearcherAgent(memory=make_memory())
            queries = await agent._extract_queries("42", "Python 3.13 有什么新特性？", router)

        assert queries == ["Python 3.13 新特性", "Python 3.13 release notes"]

    @pytest.mark.asyncio
    async def test_strips_markdown_fences_from_llm_output(self):
        """LLM 返回了 markdown 代码块时，应正确解析。"""
        wrapped = '```json\n["kw1", "kw2"]\n```'
        with patch("src.agents.researcher.load_prompt", return_value="prompt {user_message}"):
            router = make_router([wrapped])
            agent = ResearcherAgent(memory=make_memory())
            queries = await agent._extract_queries("42", "问题", router)

        assert queries == ["kw1", "kw2"]

    @pytest.mark.asyncio
    async def test_extracts_json_array_from_mixed_text(self):
        """LLM 输出混杂文本时，仍能从中提取 JSON 数组。"""
        mixed = "先分析一下\n[\"kw1\", \"kw2\"]\n以上是建议"
        with patch("src.agents.researcher.load_prompt", return_value="prompt {user_message}"):
            router = make_router([mixed])
            agent = ResearcherAgent(memory=make_memory())
            queries = await agent._extract_queries("42", "问题", router)

        assert queries == ["kw1", "kw2"]

    @pytest.mark.asyncio
    async def test_falls_back_to_plain_text_queries_when_invalid_json(self):
        """LLM 返回无效 JSON 时，降级为文本切分结果。"""
        with patch("src.agents.researcher.load_prompt", return_value="prompt {user_message}"):
            router = make_router(["今天a股收盘信息"])
            agent = ResearcherAgent(memory=make_memory())
            queries = await agent._extract_queries("42", "问题", router)

        assert queries
        assert "今天a股收盘信息" in queries[0]

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_router_exception(self):
        """router 抛出异常时，返回空列表。"""
        with patch("src.agents.researcher.load_prompt", return_value="prompt {user_message}"):
            router = MagicMock()
            router.complete = AsyncMock(side_effect=RuntimeError("API error"))
            agent = ResearcherAgent(memory=make_memory())
            queries = await agent._extract_queries("42", "问题", router)

        assert queries == []


# ---- 测试：execute 主流程 ----

class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_happy_path(self):
        """正常路径：提取关键词 → 搜索 → 摘要 → 存 discovery → 返回结果。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])

        with patch("src.agents.researcher.load_prompt", return_value="prompt {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=FAKE_RESULTS) as mock_search, \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://example.com/py313",
                 title="Python 3.13 新特性",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert result.content == FAKE_SUMMARY
        assert result.needs_persona_formatting is True
        assert "sources" in result.metadata
        mock_search.assert_called_once_with("Python 3.13 新特性", max_results=5)
        memory.add_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_second_query_when_first_empty(self):
        """第一个关键词无结果时，自动尝试第二个。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])

        async def mock_search(query, max_results=5):
            if "新特性" in query:
                return []          # 第一个词无结果
            return FAKE_RESULTS    # 第二个词有结果

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", side_effect=mock_search), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://python.org/313",
                 title="Python 3.13 发布说明",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert result.content == FAKE_SUMMARY

    @pytest.mark.asyncio
    async def test_execute_returns_friendly_message_when_no_results(self):
        """所有关键词搜索均无结果时，返回提示而不是崩溃。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON])

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message}"), \
             patch("src.agents.researcher.web_search.search", return_value=[]):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert "没有找到" in result.content
        memory.add_discovery.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_uses_fallback_query_when_query_extraction_fails(self):
        """关键词提取失败时，仍会用兜底词继续搜索。"""
        memory = make_memory()
        router = make_router(["not json", FAKE_SUMMARY])

        async def mock_search(query, max_results=5):
            if "Python 3.13" in query:
                return FAKE_RESULTS
            return []

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", side_effect=mock_search), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://example.com/py313",
                 title="Python 3.13 新特性",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert result.content == FAKE_SUMMARY

    @pytest.mark.asyncio
    async def test_execute_tries_all_query_candidates_until_hit(self):
        """搜索会按候选词顺序尝试，不再只限前两个。"""
        memory = make_memory()
        router = make_router(['["q1", "q2", "q3"]', FAKE_SUMMARY])

        async def mock_search(query, max_results=5):
            if query == "q3":
                return FAKE_RESULTS
            return []

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", side_effect=mock_search), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://example.com/py313",
                 title="Python 3.13 新特性",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert result.content == FAKE_SUMMARY

    @pytest.mark.asyncio
    async def test_discovery_saved_with_truncated_summary(self):
        """discovery 的 summary 最长 500 字符。"""
        memory = make_memory()
        long_summary = "A" * 600
        router = make_router([FAKE_QUERIES_JSON, long_summary])

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=FAKE_RESULTS), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://example.com/py313",
                 title="Python 3.13 新特性",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            await agent.execute(make_task(), router)

        call_kwargs = memory.add_discovery.call_args.kwargs
        assert len(call_kwargs["summary"]) == 500

    @pytest.mark.asyncio
    async def test_discovery_failure_does_not_crash_agent(self):
        """存 discovery 失败时，agent 仍正常返回结果。"""
        memory = make_memory()
        memory.add_discovery = AsyncMock(side_effect=Exception("DB error"))
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=FAKE_RESULTS), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://example.com/py313",
                 title="Python 3.13 新特性",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert result.content == FAKE_SUMMARY  # 正常返回

    @pytest.mark.asyncio
    async def test_summarize_fallback_on_router_exception(self):
        """摘要 LLM 调用失败时，降级返回搜索结果列表。"""
        memory = make_memory()

        # 第一次调用（关键词提取）成功，第二次（摘要）失败
        router = MagicMock()
        router.complete = AsyncMock(side_effect=[FAKE_QUERIES_JSON, RuntimeError("timeout")])

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message} {search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=FAKE_RESULTS), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=FetchResult(
                 url="https://example.com/py313",
                 title="Python 3.13 新特性",
                 text="完整正文",
                 success=True,
                 error="",
             ))):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        assert "搜索结果" in result.content
        assert "Python 3.13 新特性" in result.content  # 降级内容包含标题

    @pytest.mark.asyncio
    async def test_fetch_success_includes_page_text_in_summary_prompt(self):
        """抓取成功时，送给总结模型的 prompt 包含网页正文。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])
        fetched = FetchResult(
            url="https://example.com/py313",
            title="Python 3.13 新特性",
            text="这是抓取到的完整正文",
            success=True,
            error="",
        )

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message}\n{search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=FAKE_RESULTS), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=fetched)):
            agent = ResearcherAgent(memory=memory)
            await agent.execute(make_task(), router)

        prompt_text = router.complete.call_args_list[1].args[0][0]["content"]
        assert "网页正文" in prompt_text
        assert "这是抓取到的完整正文" in prompt_text

    @pytest.mark.asyncio
    async def test_fetch_failure_still_summarizes_snippets(self):
        """抓取失败时，仍用 snippet 正常总结。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])
        fetched = FetchResult(
            url="https://example.com/py313",
            title="",
            text="",
            success=False,
            error="请求超时",
        )

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message}\n{search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=FAKE_RESULTS), \
             patch("src.agents.researcher.web_fetcher.fetch", AsyncMock(return_value=fetched)):
            agent = ResearcherAgent(memory=memory)
            result = await agent.execute(make_task(), router)

        prompt_text = router.complete.call_args_list[1].args[0][0]["content"]
        assert result.content == FAKE_SUMMARY
        assert "新增 JIT 编译器" in prompt_text
        assert "网页正文" not in prompt_text

    @pytest.mark.asyncio
    async def test_only_fetches_first_two_results(self):
        """最多只抓取前两条带 URL 的结果。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])
        results = [
            {"title": "A", "url": "https://a.com", "snippet": "a"},
            {"title": "B", "url": "https://b.com", "snippet": "b"},
            {"title": "C", "url": "https://c.com", "snippet": "c"},
        ]
        mock_fetch = AsyncMock(return_value=FetchResult(
            url="https://a.com",
            title="A",
            text="正文",
            success=True,
            error="",
        ))

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message}\n{search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=results), \
             patch("src.agents.researcher.web_fetcher.fetch", mock_fetch):
            agent = ResearcherAgent(memory=memory)
            await agent.execute(make_task(), router)

        assert mock_fetch.await_count == 2
        fetched_urls = [call.args[0] for call in mock_fetch.await_args_list]
        assert fetched_urls == ["https://a.com", "https://b.com"]

    @pytest.mark.asyncio
    async def test_result_without_url_is_not_fetched(self):
        """没有 URL 的结果不会调用网页抓取。"""
        memory = make_memory()
        router = make_router([FAKE_QUERIES_JSON, FAKE_SUMMARY])
        results = [
            {"title": "A", "url": "", "snippet": "a"},
            {"title": "B", "url": "https://b.com", "snippet": "b"},
        ]
        mock_fetch = AsyncMock(return_value=FetchResult(
            url="https://b.com",
            title="B",
            text="正文",
            success=True,
            error="",
        ))

        with patch("src.agents.researcher.load_prompt", return_value="p {user_message}\n{search_results}"), \
             patch("src.agents.researcher.web_search.search", return_value=results), \
             patch("src.agents.researcher.web_fetcher.fetch", mock_fetch):
            agent = ResearcherAgent(memory=memory)
            await agent.execute(make_task(), router)

        mock_fetch.assert_awaited_once_with("https://b.com")
