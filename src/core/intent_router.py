"""Runtime profile router for user conversations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from config.settings import INTENT_ROUTER_SESSION_TTL_SECONDS
from src.core.time_utils import now

logger = logging.getLogger("lapwing.core.intent_router")


@dataclass(frozen=True)
class RouteDecision:
    """Structured result of IntentRouter classification.

    profile_name picks the runtime profile (chat_minimal / chat_extended /
    task_execution). When the message asks for real-time information that
    must come from a live tool call, requires_current_info is True and
    required_tool_names lists the preferred tools to satisfy the gate.
    """

    profile_name: str
    requires_current_info: bool = False
    current_info_domain: str | None = None
    required_tool_names: tuple[str, ...] = ()


# current-info domain → preferred tool names. The gate is satisfied if
# *any* of these were successfully invoked during the tool loop.
#
# IMPORTANT: tool names here MUST exist in the chat_extended profile's
# tool_names. Per Blueprint §10.1, raw `research`/`browse` are no longer
# exposed at the chat tier — research-class queries go through
# `delegate_to_agent` (agent_type=researcher). Listing `research` here
# would tell the runtime to nudge the model toward a tool it can't see,
# leading the model to skip tools entirely and the gate to force-fallback
# every weather/news/price turn (2026-04-29 incident).
_DOMAIN_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "sports": ("get_sports_score", "delegate_to_agent"),
    "weather": ("delegate_to_agent",),
    "news": ("delegate_to_agent",),
    "price": ("delegate_to_agent",),
}


class IntentRouter:
    """Classify a user message into a runtime profile + current-info hint."""

    # Cheap keyword sniff used to break a stale chat cache when the user
    # pivots to a real-time question. Cached chat_minimal/chat_extended
    # decisions don't carry a current_info_domain, so without this a
    # follow-up "道奇今天比赛" would inherit the previous turn's
    # requires_current_info=False and slip past the gate.
    _CURRENT_INFO_HINTS: frozenset[str] = frozenset({
        "今天", "现在", "今晚", "刚才", "最新", "新闻", "天气", "比分",
        "赛况", "比赛", "赢了", "输了", "价格", "股价", "汇率",
        "道奇", "dodgers", "湖人", "lakers", "mlb", "nba", "nfl",
        "weather", "score", "price",
    })

    def __init__(self, llm_router: Any, session_ttl_seconds: int | None = None) -> None:
        self._llm_router = llm_router
        self._ttl = session_ttl_seconds or INTENT_ROUTER_SESSION_TTL_SECONDS
        self._cache: dict[str, tuple[RouteDecision, datetime]] = {}

    async def route(self, chat_id: str, user_message: str) -> RouteDecision:
        cached = self._cache.get(chat_id)
        if cached is not None:
            decision, ts = cached
            if (now() - ts).total_seconds() < self._ttl:
                if self._is_obvious_task(user_message) and decision.profile_name != "task_execution":
                    pass  # fall through to re-classify
                elif self._looks_like_current_info(user_message) and not decision.requires_current_info:
                    pass  # cached decision predates a current-info pivot — re-classify
                else:
                    return decision

        decision = await self._llm_classify(user_message)
        # Only cache non-current-info decisions. Current-info decisions
        # carry required_tool_names that the runtime gate enforces; if a
        # weather decision sticks to an unrelated follow-up turn, the gate
        # forces an honest-fallback on a normal reply (2026-04-28 incident:
        # "明天天气" + "想看电影" → cached weather → next turn "在等结果" →
        # gate fired on it). Re-classifying every current-info turn costs
        # one extra lightweight_judgment LLM call but isolates the gate.
        if not decision.requires_current_info:
            self._cache[chat_id] = (decision, now())
        logger.info(
            "[intent_router] chat=%s profile=%s current_info=%s domain=%s msg=%r",
            chat_id,
            decision.profile_name,
            decision.requires_current_info,
            decision.current_info_domain,
            user_message[:60],
        )
        return decision

    def _is_obvious_task(self, msg: str) -> bool:
        task_indicators = [
            "跑", "执行", "运行", "shell", "命令", "代码",
            "git", "pytest", "deploy", "部署", "打开浏览器", "browse",
        ]
        lowered = msg.lower()
        return any(item in lowered for item in task_indicators)

    def _looks_like_current_info(self, msg: str) -> bool:
        lowered = msg.lower()
        return any(hint in lowered for hint in self._CURRENT_INFO_HINTS)

    async def _llm_classify(self, msg: str) -> RouteDecision:
        prompt = f"""判断这条消息属于哪类需求。

消息：{msg[:200]}

类别：
- chat: 日常聊天、情感互动、问候、闲谈
- chat_extended: 需要查信息（搜索/天气/体育/新闻）、需要记事或设提醒、需要承诺管理
- task: 明确的工程任务（写代码、操作文件、跑命令、浏览器自动化、委派子任务）

是否需要实时信息（current_info）：
- 体育比分/赛程/胜负 → sports
- 天气/气温/降水 → weather
- 新闻/时事/最新消息 → news
- 股价/价格/汇率 → price
- 不需要实时信息 → none

输出格式（严格一行）：
类别 current_info_domain

例如：
chat_extended sports
chat none
task none

判断不确定时，类别输出 chat_extended，domain 输出 none。"""

        try:
            result = await self._llm_router.complete(
                [{"role": "user", "content": prompt}],
                purpose="lightweight_judgment",
                max_tokens=20,
            )
        except Exception as exc:
            logger.warning("[intent_router] LLM call failed: %s", exc)
            return RouteDecision(profile_name="chat_extended")

        return self._parse_decision(str(result).strip())

    def _parse_decision(self, raw: str) -> RouteDecision:
        """Parse the LLM's two-token response into a RouteDecision."""
        parts = raw.lower().split()
        category = parts[0] if parts else "chat_extended"
        domain = parts[1] if len(parts) > 1 else "none"

        if "task" in category:
            profile = "task_execution"
        elif "chat_extended" in category or "extended" in category:
            profile = "chat_extended"
        elif category == "chat":
            profile = "chat_minimal"
        else:
            logger.warning("[intent_router] unparseable category=%r", category)
            profile = "chat_extended"

        if domain in _DOMAIN_TOOL_MAP:
            # Real-time queries always need at least the chat_extended tool
            # surface (research, get_sports_score, ...). chat_minimal would
            # leave the model with no way to satisfy the gate.
            if profile == "chat_minimal":
                profile = "chat_extended"
            return RouteDecision(
                profile_name=profile,
                requires_current_info=True,
                current_info_domain=domain,
                required_tool_names=_DOMAIN_TOOL_MAP[domain],
            )

        return RouteDecision(profile_name=profile)
