"""AutonomousBrowsingAction - 后台自主浏览并沉淀知识。"""

import logging
import random
from datetime import datetime, timedelta

from config.settings import BROWSE_ENABLED, BROWSE_INTERVAL_HOURS, BROWSE_SOURCES
from src.core.heartbeat import HeartbeatAction, SenseContext
from src.core.prompt_loader import load_prompt
from src.tools import web_fetcher, web_search

logger = logging.getLogger("lapwing.heartbeat.autonomous_browsing")

_INTEREST_PROBABILITY = 0.7
_INTEREST_INCREMENT = 0.3
_MAX_FETCH_RESULTS = 3
_MAX_SEARCH_RESULTS = 5

_SOURCE_QUERY_MAP = {
    "hackernews": "Hacker News top stories",
    "reddit/technology": "Reddit r/technology hot posts",
    "reddit/science": "Reddit r/science hot posts",
}


class AutonomousBrowsingAction(HeartbeatAction):
    """周期性自主浏览，写 discovery/knowledge，不直接发消息。"""

    name = "autonomous_browsing"
    description = "后台自主浏览并沉淀发现，供后续主动消息择机分享"
    beat_types = ["fast"]
    selection_mode = "always"

    def __init__(self) -> None:
        self._prompt_template: str | None = None
        self._last_run_at: dict[str, datetime] = {}

    @property
    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt("heartbeat_autonomous_browsing")
        return self._prompt_template

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        if not BROWSE_ENABLED:
            return
        if not self._is_due(ctx.chat_id, ctx.now):
            return

        # 即使本轮失败也进入冷却，避免每次 fast 心跳都重复打外网。
        self._last_run_at[ctx.chat_id] = ctx.now

        try:
            query = await self._select_query(ctx, brain)
            if not query:
                return

            results = await web_search.search(query, max_results=_MAX_SEARCH_RESULTS)
            if not results:
                logger.info(f"[{ctx.chat_id}] 自主浏览无搜索结果: {query!r}")
                return

            selected = await self._fetch_first_readable(results[:_MAX_FETCH_RESULTS])
            if selected is None:
                logger.info(f"[{ctx.chat_id}] 自主浏览抓取失败（前 {_MAX_FETCH_RESULTS} 条）: {query!r}")
                return

            search_item, fetched = selected
            url = str(search_item.get("url", "")).strip() or fetched.url
            title = fetched.title or str(search_item.get("title", "")).strip() or query

            prompt = self._prompt.format(
                query=query,
                title=title,
                url=url,
                page_text=fetched.text,
                user_facts_summary=ctx.user_facts_summary,
                top_interests_summary=ctx.top_interests_summary,
            )
            summary = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=300,
                session_key=f"chat:{ctx.chat_id}",
                origin="heartbeat.autonomous_browsing",
            )
            summary = summary.strip()
            if not summary:
                return

            await brain.memory.add_discovery(
                chat_id=ctx.chat_id,
                source="autonomous_browsing",
                title=title,
                summary=summary[:500],
                url=url,
            )
            if hasattr(brain, "knowledge_manager") and brain.knowledge_manager is not None:
                brain.knowledge_manager.save_note(
                    topic=query,
                    source_url=url,
                    content=summary,
                )
            await brain.memory.bump_interest(ctx.chat_id, query, increment=_INTEREST_INCREMENT)

            event_bus = brain.__dict__.get("event_bus") if hasattr(brain, "__dict__") else None
            if event_bus is not None:
                await event_bus.publish(
                    "autonomous_browsing",
                    {
                        "chat_id": ctx.chat_id,
                        "query": query,
                        "title": title,
                        "url": url,
                    },
                )

            # 显式不发消息：主动分享统一由 proactive_message 链路处理。
            if bot is not None:
                _ = bot

            logger.info(f"[{ctx.chat_id}] 自主浏览完成: {query!r} -> {title!r}")
        except Exception as exc:
            logger.error(f"[{ctx.chat_id}] 自主浏览失败: {exc}")

    def _is_due(self, chat_id: str, now: datetime) -> bool:
        last = self._last_run_at.get(chat_id)
        if last is None:
            return True
        interval_hours = max(int(BROWSE_INTERVAL_HOURS), 1)
        return (now - last) >= timedelta(hours=interval_hours)

    async def _select_query(self, ctx: SenseContext, brain) -> str | None:
        # 新增：从最近对话中提取话题（30% 概率使用）
        if random.random() < 0.3:
            recent_topic = await self._extract_recent_topic(ctx.chat_id, brain)
            if recent_topic:
                return recent_topic

        top_interests = await brain.memory.get_top_interests(ctx.chat_id, limit=5)
        if top_interests and random.random() < _INTEREST_PROBABILITY:
            topic = str(top_interests[0].get("topic", "")).strip()
            if topic:
                return topic

        source_query = self._query_from_source()
        if source_query:
            return source_query

        if top_interests:
            fallback = str(top_interests[0].get("topic", "")).strip()
            if fallback:
                return fallback
        return None

    async def _extract_recent_topic(self, chat_id: str, brain) -> str | None:
        """从最近对话历史中提取一个可用于浏览的话题关键词。"""
        try:
            history = await brain.memory.get(chat_id)
            if not history:
                return None
            # 取最近 10 条用户消息
            user_msgs = [m["content"] for m in history if m.get("role") == "user"][-10:]
            if not user_msgs:
                return None

            combined = "\n".join(user_msgs[-5:])  # 只取最近 5 条
            prompt = (
                "从以下对话中提取一个用户最近关注的具体话题，用于搜索最新动态。\n"
                "只输出一个简短的搜索关键词（2-6个词），不要解释。\n"
                "如果没有明确话题，只输出 null。\n\n"
                f"{combined}"
            )
            result = await brain.router.complete(
                [{"role": "user", "content": prompt}],
                purpose="heartbeat",
                max_tokens=50,
                session_key=f"chat:{chat_id}",
                origin="heartbeat.browsing.recent_topic",
            )
            result = result.strip().strip('"').strip()
            if not result or result.lower() == "null" or len(result) > 30:
                return None
            return result
        except Exception as exc:
            logger.warning(f"[{chat_id}] 提取最近话题失败: {exc}")
            return None

    def _query_from_source(self) -> str | None:
        if not BROWSE_SOURCES:
            return None
        source = random.choice(BROWSE_SOURCES).strip().lower()
        if source in _SOURCE_QUERY_MAP:
            return _SOURCE_QUERY_MAP[source]
        fallback = source.replace("/", " ").replace("_", " ").strip()
        return fallback or None

    async def _fetch_first_readable(
        self,
        results: list[dict],
    ) -> tuple[dict, web_fetcher.FetchResult] | None:
        for item in results:
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            fetched = await web_fetcher.fetch(url)
            if fetched.success and fetched.text:
                return item, fetched
        return None
