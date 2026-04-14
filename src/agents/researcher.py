"""ResearcherAgent：信息搜集和调研。

执行流程：分解关键词 → 并行搜索 → 筛选来源 → 深度抓取 → 综合报告。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.agent_base import BaseAgent
from src.core.agent_protocol import (
    AgentCommand,
    AgentEmitState,
    AgentNotify,
    AgentNotifyKind,
    AgentUrgency,
    EmitCallback,
)
from src.tools.types import ToolExecutionRequest

if TYPE_CHECKING:
    from src.core.task_runtime import TaskRuntime

logger = logging.getLogger("lapwing.agents.researcher")

_DECOMPOSE_PROMPT = """你是一个调研助手。用户想要调研以下主题：

{task}

请生成 2-5 个搜索关键词（中文或英文），用于在网上搜索相关信息。
每个关键词一行，不要编号，不要额外解释。"""

_SELECT_SOURCES_PROMPT = """以下是搜索结果。请从中选择最相关的 5-8 个来源进行深度阅读。

搜索结果：
{results}

原始任务：{task}

请返回你选择的 URL 列表，每行一个，不要额外解释。"""

_SYNTHESIZE_PROMPT = """你是一个调研助手。请基于以下来源内容，为用户的调研需求生成一份结构化摘要报告。

用户需求：{task}

来源内容：
{sources}

要求：
- 使用 Markdown 格式
- 包含主要发现、关键观点、值得关注的项目/论文
- 标注每个信息的来源
- 用中文撰写
- 简洁但全面，不超过 2000 字"""

# 每篇文章最大字符数（防止 token 爆炸）
_MAX_CONTENT_CHARS = 3000
# 最多深度抓取的文章数
_MAX_FETCH_COUNT = 6


class ResearcherAgent(BaseAgent):
    """信息搜集和调研 Agent。"""

    def __init__(self):
        super().__init__(
            name="researcher",
            description="信息搜集和调研，能搜索网页、阅读文章、综合整理报告",
        )

    @property
    def capabilities(self) -> list[str]:
        return ["web_search", "web_fetch", "summarize", "multi_source_synthesis"]

    async def _execute_task(
        self,
        command: AgentCommand,
        task_runtime: TaskRuntime,
        emit: EmitCallback,
    ) -> AgentNotify:
        task = command.task_description

        # Step 1: 分解搜索关键词
        emit(AgentEmitState.WORKING, "正在分析调研任务...")
        keywords = await self._decompose_task(task, task_runtime)
        if not keywords:
            return AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.ERROR,
                urgency=AgentUrgency.SOON,
                headline="无法分解调研任务",
                ref_command_id=command.id,
            )

        # Step 2: 搜索每个关键词
        emit(AgentEmitState.WORKING, f"正在搜索 {len(keywords)} 个关键词...", 0.2)
        all_results = await self._search_all(keywords, task_runtime)

        if not all_results:
            return AgentNotify(
                agent_name=self.name,
                kind=AgentNotifyKind.RESULT,
                urgency=AgentUrgency.SOON,
                headline="搜索未找到相关结果",
                detail=f"尝试了关键词：{', '.join(keywords)}",
                ref_command_id=command.id,
            )

        # Step 3: 筛选最相关来源
        emit(AgentEmitState.WORKING, f"找到 {len(all_results)} 条结果，正在筛选...", 0.4)
        selected_urls = await self._select_sources(task, all_results, task_runtime)

        # Step 4: 深度抓取
        urls_to_fetch = selected_urls[:_MAX_FETCH_COUNT]
        emit(AgentEmitState.WORKING, f"正在阅读 {len(urls_to_fetch)} 篇文章...", 0.6)
        source_contents = await self._fetch_all(urls_to_fetch, task_runtime)

        # Step 5: 综合整理
        emit(AgentEmitState.WORKING, "正在整理调研报告...", 0.85)
        report = await self._synthesize(task, source_contents, task_runtime)

        return AgentNotify(
            agent_name=self.name,
            kind=AgentNotifyKind.RESULT,
            urgency=AgentUrgency.SOON,
            headline=f"调研完成：{task[:50]}",
            detail=report,
            payload={"sources_count": len(source_contents), "keywords": keywords},
            ref_command_id=command.id,
        )

    # -- 内部步骤 --

    async def _decompose_task(self, task: str, runtime: TaskRuntime) -> list[str]:
        """用 LLM 分解任务为搜索关键词。"""
        prompt = _DECOMPOSE_PROMPT.format(task=task)
        response = await runtime.llm_router.simple_completion(prompt, purpose="agent_execution")
        if not response:
            return []
        return [line.strip() for line in response.strip().split("\n") if line.strip()][:5]

    async def _search_all(self, keywords: list[str], runtime: TaskRuntime) -> list[dict]:
        """搜索所有关键词，去重合并结果。"""
        context = runtime.create_agent_context(self.name)
        all_results: list[dict] = []
        seen_urls: set[str] = set()

        for kw in keywords:
            if self.is_cancel_requested:
                break
            result = await runtime.tool_registry.execute(
                ToolExecutionRequest(name="web_search", arguments={"query": kw}),
                context=context,
            )
            if not result.success:
                continue
            # web_search 返回 results 列表或 sources 列表
            items = result.payload.get("results", []) or result.payload.get("sources", [])
            for item in items:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(item)

        return all_results

    async def _select_sources(
        self, task: str, results: list[dict], runtime: TaskRuntime,
    ) -> list[str]:
        """用 LLM 从搜索结果中选择最相关来源。"""
        results_text = "\n".join(
            f"- [{r.get('title', 'Unknown')}]({r.get('url', '')}) - {r.get('snippet', '')[:100]}"
            for r in results[:20]
        )
        prompt = _SELECT_SOURCES_PROMPT.format(results=results_text, task=task)
        response = await runtime.llm_router.simple_completion(prompt, purpose="agent_execution")
        if not response:
            return [r.get("url", "") for r in results[:5] if r.get("url")]
        urls = [
            line.strip()
            for line in response.strip().split("\n")
            if line.strip().startswith("http")
        ]
        return urls[:8]

    async def _fetch_all(
        self, urls: list[str], runtime: TaskRuntime,
    ) -> list[dict]:
        """抓取多个 URL 的内容。"""
        context = runtime.create_agent_context(self.name)
        source_contents: list[dict] = []

        for url in urls:
            if self.is_cancel_requested:
                break
            result = await runtime.tool_registry.execute(
                ToolExecutionRequest(name="web_fetch", arguments={"url": url}),
                context=context,
            )
            if result.success:
                text = result.payload.get("text", "") or result.payload.get("answer", "")
                if text:
                    source_contents.append({
                        "url": url,
                        "content": text[:_MAX_CONTENT_CHARS],
                    })

        return source_contents

    async def _synthesize(
        self, task: str, sources: list[dict], runtime: TaskRuntime,
    ) -> str:
        """用 LLM 综合整理报告。"""
        sources_text = "\n\n---\n\n".join(
            f"来源：{s['url']}\n{s['content']}" for s in sources
        )
        prompt = _SYNTHESIZE_PROMPT.format(task=task, sources=sources_text)
        response = await runtime.llm_router.simple_completion(
            prompt, purpose="agent_execution", max_tokens=4096,
        )
        return response or "未能生成报告"
