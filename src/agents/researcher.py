"""Researcher Agent — 联网搜索并整理信息。"""

import json
import logging
import re
from typing import Any

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt
from src.tools import web_fetcher, web_search

logger = logging.getLogger("lapwing.agents.researcher")

_FETCH_TOP_N = 2
_MAX_QUERY_CANDIDATES = 3
_QUERY_LEADING_PREFIX = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|能不能|可以|想|想要|我想|给我|替我|查一下|查下|查查|搜一下|搜下|搜搜|"
    r"搜索一下|查询一下|查一查|搜一搜|看下|看一下|告诉我)\s*",
    flags=re.IGNORECASE,
)


class ResearcherAgent(BaseAgent):
    """联网搜索信息，查找新闻、技术文档、百科知识等。"""

    name = "researcher"
    description = "联网搜索信息，查找新闻、技术文档、百科知识等"
    capabilities = ["搜索网页信息", "查找新闻动态", "检索技术文档", "查询百科知识"]

    def __init__(self, memory, knowledge_manager=None) -> None:
        self._memory = memory
        self._knowledge_manager = knowledge_manager

    async def execute(self, task: AgentTask, router) -> AgentResult:
        """执行搜索：提取关键词 → 搜索 → 摘要 → 存 discovery。"""
        # 1. 提取搜索关键词
        extracted_queries = await self._extract_queries(task.chat_id, task.user_message, router)
        fallback_queries = self._build_fallback_queries(task.user_message)
        queries = self._normalize_queries([*extracted_queries, *fallback_queries])
        if not queries:
            logger.warning("[researcher] 无法生成搜索关键词，使用原句兜底")
            queries = self._normalize_queries([task.user_message]) or ["最新信息"]

        # 2. 按候选词依次执行搜索（最多 5 条结果）
        primary_query = queries[0]
        results: list[dict[str, Any]] = []
        for query in queries:
            current_results = await web_search.search(query, max_results=5)
            if current_results:
                results = current_results
                primary_query = query
                break

        if not results:
            logger.info(f"[researcher] 搜索无结果: {queries}")
            return AgentResult(
                content=f"搜索「{primary_query}」没有找到相关结果，可能是网络问题或关键词需要调整。",
                needs_persona_formatting=True,
            )

        enriched_results = await self._enrich_results(results)

        # 3. 用 LLM 整理摘要
        summary = await self._summarize(task.chat_id, task.user_message, enriched_results, router)

        # 4. 存 discovery + 知识笔记
        await self._save_discovery(task.chat_id, primary_query, results, summary)
        if self._knowledge_manager is not None and results:
            self._knowledge_manager.save_note(
                topic=primary_query,
                source_url=results[0].get("url", ""),
                content=summary,
            )

        sources = [{"title": r["title"], "url": r["url"]} for r in results if r.get("url")]
        return AgentResult(
            content=summary,
            needs_persona_formatting=True,
            metadata={"sources": sources, "queries": queries},
        )

    async def _extract_queries(self, chat_id: str, user_message: str, router) -> list[str]:
        """用 LLM 从用户消息中提取搜索关键词。"""
        try:
            prompt = load_prompt("researcher_extract_query").replace(
                "{user_message}", user_message
            )
            raw = await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=128,
                session_key=f"chat:{chat_id}",
                origin="agent.researcher.extract_queries",
            )
            parsed_queries = self._parse_query_candidates(raw)
            if parsed_queries:
                return parsed_queries
            logger.warning("[researcher] 关键词提取未得到可用结果，原始输出已忽略")
        except Exception as e:
            logger.warning(f"[researcher] 关键词提取出错: {e}")
        return []

    def _parse_query_candidates(self, raw: Any) -> list[str]:
        """容错解析 LLM 返回的查询词。"""
        text = str(raw or "").strip()
        if not text:
            return []

        # 去掉 markdown 代码块与思考标签，避免 JSON 解析受干扰。
        sanitized = re.sub(r"```[a-z]*\n?", "", text, flags=re.IGNORECASE).strip().strip("`")
        sanitized = re.sub(r"<think>[\s\S]*?</think>", "", sanitized, flags=re.IGNORECASE).strip()
        if not sanitized:
            return []

        # 1) 直接按 JSON 数组解析
        direct = self._parse_json_array(sanitized)
        if direct:
            return direct

        # 2) 提取文本中的 JSON 数组片段解析
        for match in re.finditer(r"\[[\s\S]*?\]", sanitized):
            candidate = self._parse_json_array(match.group(0))
            if candidate:
                return candidate

        # 3) 文本降级：按换行/逗号切词
        plain_chunks = re.split(r"[\n,，;；]+", sanitized)
        return self._normalize_queries(plain_chunks)

    def _parse_json_array(self, value: str) -> list[str]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return self._normalize_queries(parsed)

    def _build_fallback_queries(self, user_message: str) -> list[str]:
        """当 LLM 提取失败时，使用本地规则生成候选搜索词。"""
        original = re.sub(r"https?://\S+", "", user_message or "").strip()
        if not original:
            return []

        compact = original
        for _ in range(3):
            updated = _QUERY_LEADING_PREFIX.sub("", compact).strip()
            if updated == compact:
                break
            compact = updated

        compact = compact.strip(" \t\r\n，,。！？!?：:")
        variants = [compact, original]

        if re.search(r"(今天|今日)", compact):
            variants.append(re.sub(r"(今天|今日)", "", compact).strip())
        if re.search(r"(A股|股市|上证|深证)", compact, flags=re.IGNORECASE):
            variants.append(compact.replace("收盘信息", "收盘").replace("行情信息", "行情"))

        return self._normalize_queries(variants)

    def _normalize_queries(self, candidates: list[Any]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text:
                continue
            text = text.strip("`\"'[]（）()")
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 2:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
            if len(normalized) >= _MAX_QUERY_CANDIDATES:
                break
        return normalized

    async def _enrich_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """尝试抓取前几条搜索结果的正文内容。"""
        enriched = [dict(result) for result in results]

        fetch_count = 0
        for result in enriched:
            url = result.get("url")
            if not url:
                continue
            if fetch_count >= _FETCH_TOP_N:
                break

            fetch_count += 1
            try:
                fetched = await web_fetcher.fetch(url)
            except Exception as e:
                logger.warning(f"[researcher] 网页抓取异常: {url} ({e})")
                continue

            if fetched.success and fetched.text:
                result["page_text"] = fetched.text

        return enriched

    async def _summarize(
        self, chat_id: str, user_message: str, results: list[dict], router
    ) -> str:
        """用 LLM 整理搜索结果为摘要。"""
        search_results_text = self._format_results(results)
        prompt = (
            load_prompt("researcher_summarize")
            .replace("{user_message}", user_message)
            .replace("{search_results}", search_results_text)
        )
        try:
            return await router.complete(
                [{"role": "user", "content": prompt}],
                slot="agent_execution",
                max_tokens=1024,
                session_key=f"chat:{chat_id}",
                origin="agent.researcher.summarize",
            )
        except Exception as e:
            logger.warning(f"[researcher] 摘要生成出错: {e}")
            # 降级：直接把搜索结果标题拼起来
            lines = [f"- [{r['title']}]({r['url']})" for r in results if r.get("title")]
            return "以下是相关搜索结果：\n" + "\n".join(lines)

    def _format_results(self, results: list[dict[str, Any]]) -> str:
        """将搜索结果组织为更适合总结的结构化文本。"""
        blocks: list[str] = []
        for index, result in enumerate(results, start=1):
            if not (result.get("title") or result.get("snippet") or result.get("page_text")):
                continue

            lines = [
                f"结果 {index}",
                f"标题：{result.get('title', '')}",
                f"链接：{result.get('url', '')}",
                f"摘要：{result.get('snippet', '')}",
            ]
            if result.get("page_text"):
                lines.append(f"网页正文：\n{result['page_text']}")
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

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
