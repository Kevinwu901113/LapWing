"""Researcher Agent — 联网搜索并整理信息。"""

import json
import logging
import re

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt
from src.tools import web_search

logger = logging.getLogger("lapwing.agents.researcher")


class ResearcherAgent(BaseAgent):
    """联网搜索信息，查找新闻、技术文档、百科知识等。"""

    name = "researcher"
    description = "联网搜索信息，查找新闻、技术文档、百科知识等"
    capabilities = ["搜索网页信息", "查找新闻动态", "检索技术文档", "查询百科知识"]

    def __init__(self, memory) -> None:
        self._memory = memory

    async def execute(self, task: AgentTask, router) -> AgentResult:
        """执行搜索：提取关键词 → 搜索 → 摘要 → 存 discovery。"""
        # 1. 提取搜索关键词
        queries = await self._extract_queries(task.user_message, router)
        if not queries:
            logger.warning("[researcher] 关键词提取失败，降级返回提示")
            return AgentResult(
                content="搜索关键词提取失败，请换一种方式提问。",
                needs_persona_formatting=True,
            )

        # 2. 执行搜索（取第一个关键词，最多 5 条）
        primary_query = queries[0]
        results = await web_search.search(primary_query, max_results=5)

        # 如果第一个没结果，尝试第二个
        if not results and len(queries) > 1:
            results = await web_search.search(queries[1], max_results=5)

        if not results:
            logger.info(f"[researcher] 搜索无结果: {queries}")
            return AgentResult(
                content=f"搜索「{primary_query}」没有找到相关结果，可能是网络问题或关键词需要调整。",
                needs_persona_formatting=True,
            )

        # 3. 用 LLM 整理摘要
        summary = await self._summarize(task.user_message, results, router)

        # 4. 存 discovery（取第一条结果代表这次搜索）
        await self._save_discovery(task.chat_id, primary_query, results, summary)

        sources = [{"title": r["title"], "url": r["url"]} for r in results if r.get("url")]
        return AgentResult(
            content=summary,
            needs_persona_formatting=True,
            metadata={"sources": sources, "queries": queries},
        )

    async def _extract_queries(self, user_message: str, router) -> list[str]:
        """用 LLM 从用户消息中提取搜索关键词。"""
        try:
            prompt = load_prompt("researcher_extract_query").replace(
                "{user_message}", user_message
            )
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=128,
            )
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip().strip("`")
            queries = json.loads(raw)
            if isinstance(queries, list) and queries:
                return [str(q) for q in queries if q]
        except Exception as e:
            logger.warning(f"[researcher] 关键词提取出错: {e}")
        return []

    async def _summarize(
        self, user_message: str, results: list[dict], router
    ) -> str:
        """用 LLM 整理搜索结果为摘要。"""
        search_results_text = "\n\n".join(
            f"[{r['title']}]({r['url']})\n{r['snippet']}"
            for r in results
            if r.get("title") or r.get("snippet")
        )
        prompt = (
            load_prompt("researcher_summarize")
            .replace("{user_message}", user_message)
            .replace("{search_results}", search_results_text)
        )
        try:
            return await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=1024,
            )
        except Exception as e:
            logger.warning(f"[researcher] 摘要生成出错: {e}")
            # 降级：直接把搜索结果标题拼起来
            lines = [f"- [{r['title']}]({r['url']})" for r in results if r.get("title")]
            return "以下是相关搜索结果：\n" + "\n".join(lines)

    async def _save_discovery(
        self,
        chat_id: str,
        query: str,
        results: list[dict],
        summary: str,
    ) -> None:
        """将搜索结果存入 discoveries 表。"""
        if not results:
            return
        first = results[0]
        try:
            await self._memory.add_discovery(
                chat_id=chat_id,
                source="web_search",
                title=first.get("title", query),
                summary=summary[:500],  # 截短，避免过长
                url=first.get("url"),
            )
        except Exception as e:
            logger.warning(f"[researcher] 存 discovery 失败: {e}")
