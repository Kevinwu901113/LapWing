"""ResearcherAgent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.researcher import ResearcherAgent, _MAX_CONTENT_CHARS
from src.core.agent_protocol import (
    AgentCommand,
    AgentCommandIntent,
    AgentEmit,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
)
from src.tools.types import ToolExecutionResult


# ---------- Fixtures ----------

def _make_command(task: str = "调研 2026 年最新的 RAG 论文", **kw) -> AgentCommand:
    return AgentCommand(
        target_agent="researcher",
        intent=AgentCommandIntent.EXECUTE,
        task_description=task,
        timeout_seconds=kw.get("timeout_seconds", 300),
    )


def _make_runtime(
    *,
    decompose_response: str = "RAG 2026\nretrieval augmented generation latest",
    select_response: str = "https://example.com/a\nhttps://example.com/b",
    synthesize_response: str = "# 调研报告\n\n这是综合报告。",
    search_results: list[dict] | None = None,
    fetch_text: str = "这是网页内容。",
) -> MagicMock:
    """构建 mock TaskRuntime。"""
    runtime = MagicMock()

    # llm_router.simple_completion 按调用顺序返回不同内容
    responses = [decompose_response, select_response, synthesize_response]
    call_count = {"n": 0}

    async def _simple_completion(prompt, purpose="agent_execution", max_tokens=2048):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        return responses[idx]

    runtime.llm_router = MagicMock()
    runtime.llm_router.simple_completion = _simple_completion

    # tool_registry.execute
    if search_results is None:
        search_results = [
            {"title": "Paper A", "url": "https://example.com/a", "snippet": "关于 RAG 的论文"},
            {"title": "Paper B", "url": "https://example.com/b", "snippet": "另一篇 RAG 论文"},
            {"title": "Paper C", "url": "https://example.com/c", "snippet": "第三篇"},
        ]

    async def _execute(request, *, context):
        if request.name == "web_search":
            return ToolExecutionResult(
                success=True,
                payload={"results": search_results, "query": request.arguments.get("query", "")},
            )
        elif request.name == "web_fetch":
            return ToolExecutionResult(
                success=True,
                payload={"text": fetch_text, "url": request.arguments.get("url", "")},
            )
        return ToolExecutionResult(success=False, payload={}, reason="unknown tool")

    runtime.tool_registry = MagicMock()
    runtime.tool_registry.execute = _execute
    runtime.create_agent_context = MagicMock(return_value=MagicMock())

    return runtime


async def _collect_events(agent, command, runtime) -> list[AgentEmit | AgentNotify]:
    events = []
    async for ev in agent.execute(command, runtime):
        events.append(ev)
    return events


# ---------- Tests ----------

async def test_researcher_decompose_task():
    """ResearcherAgent 能将任务分解为搜索关键词。"""
    agent = ResearcherAgent()
    runtime = _make_runtime(decompose_response="RAG 2026\nretrieval augmented generation")
    keywords = await agent._decompose_task("调研 RAG", runtime)
    assert len(keywords) == 2
    assert "RAG 2026" in keywords


async def test_researcher_decompose_limits_to_five():
    """最多返回 5 个关键词。"""
    agent = ResearcherAgent()
    runtime = _make_runtime(decompose_response="a\nb\nc\nd\ne\nf\ng")
    keywords = await agent._decompose_task("test", runtime)
    assert len(keywords) == 5


async def test_researcher_decompose_empty():
    """LLM 返回空字符串时返回空列表。"""
    agent = ResearcherAgent()
    runtime = _make_runtime(decompose_response="")
    keywords = await agent._decompose_task("test", runtime)
    assert keywords == []


async def test_researcher_full_pipeline():
    """完整调研流程：分解→搜索→筛选→抓取→整理。"""
    agent = ResearcherAgent()
    command = _make_command()
    runtime = _make_runtime()

    events = await _collect_events(agent, command, runtime)

    # 应该有 QUEUED、WORKING emit 和最终 RESULT notify
    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    notify = notifies[0]
    assert notify.kind == AgentNotifyKind.RESULT
    assert "调研完成" in notify.headline
    assert notify.detail == "# 调研报告\n\n这是综合报告。"
    assert notify.payload["keywords"] is not None
    assert notify.payload["sources_count"] > 0


async def test_researcher_emits_progress():
    """执行过程中 emit 进度更新。"""
    agent = ResearcherAgent()
    command = _make_command()
    runtime = _make_runtime()

    events = await _collect_events(agent, command, runtime)

    working_emits = [
        e for e in events
        if isinstance(e, AgentEmit) and e.state == AgentEmitState.WORKING
    ]
    # 至少有初始 WORKING + 回调产生的 WORKING
    assert len(working_emits) >= 1
    # 应该有进度信息的 emit
    progress_notes = [e.note for e in working_emits if e.note]
    assert any("搜索" in n or "筛选" in n or "阅读" in n or "整理" in n for n in progress_notes)


async def test_researcher_handles_no_results():
    """搜索无结果时返回适当的 notify。"""
    agent = ResearcherAgent()
    command = _make_command()
    runtime = _make_runtime(search_results=[])

    events = await _collect_events(agent, command, runtime)

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    assert notifies[0].kind == AgentNotifyKind.RESULT
    assert "未找到" in notifies[0].headline


async def test_researcher_handles_decompose_failure():
    """LLM 无法分解任务时返回 ERROR。"""
    agent = ResearcherAgent()
    command = _make_command()
    runtime = _make_runtime(decompose_response="")

    events = await _collect_events(agent, command, runtime)

    notifies = [e for e in events if isinstance(e, AgentNotify)]
    assert len(notifies) == 1
    assert notifies[0].kind == AgentNotifyKind.ERROR
    assert "无法分解" in notifies[0].headline


async def test_researcher_deduplicates_urls():
    """搜索结果中相同 URL 的结果只保留一个。"""
    agent = ResearcherAgent()
    runtime = _make_runtime(search_results=[
        {"title": "A", "url": "https://example.com/dup", "snippet": "first"},
        {"title": "B", "url": "https://example.com/dup", "snippet": "duplicate"},
        {"title": "C", "url": "https://example.com/other", "snippet": "unique"},
    ])
    results = await agent._search_all(["test"], runtime)
    urls = [r["url"] for r in results]
    assert urls.count("https://example.com/dup") == 1
    assert len(results) == 2


async def test_researcher_truncates_content():
    """每篇文章截断到 _MAX_CONTENT_CHARS 字符。"""
    long_text = "x" * (_MAX_CONTENT_CHARS + 5000)
    agent = ResearcherAgent()
    runtime = _make_runtime(fetch_text=long_text)
    sources = await agent._fetch_all(["https://example.com/long"], runtime)
    assert len(sources) == 1
    assert len(sources[0]["content"]) == _MAX_CONTENT_CHARS


async def test_researcher_respects_cancel():
    """取消请求时停止搜索。"""
    agent = ResearcherAgent()
    await agent.cancel()
    runtime = _make_runtime()
    results = await agent._search_all(["a", "b", "c"], runtime)
    # 取消后不应执行任何搜索
    assert results == []
