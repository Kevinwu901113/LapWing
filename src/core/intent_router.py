"""Runtime profile router for user conversations.

Two-class classifier (post-2026-04-29 refactor): every user turn is
either pure chitchat (no tools needed) or "use tools" (Lapwing's full
self-capability surface, including delegate_to_researcher for any
external info question). No more domain routing — the model picks
delegate_to_researcher itself based on the tool description, and the
Researcher decides which retrieval API to call.

Output names ``zero_tools`` / ``standard`` map onto runtime profiles.
The current-info gate fields on RouteDecision are kept but never
populated after this commit; they're scheduled for deletion in the
gate-removal commit.
"""

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

    profile_name picks the runtime profile (``zero_tools`` /
    ``standard`` / ``task_execution``). The ``requires_current_info``
    fields are deprecated — kept on the dataclass so older consumers
    don't crash, but always default values now. They're removed
    entirely in the gate-cleanup commit.
    """

    profile_name: str
    requires_current_info: bool = False
    current_info_domain: str | None = None
    required_tool_names: tuple[str, ...] = ()


class IntentRouter:
    """Classify a user message into a runtime profile."""

    # Keywords that should always escalate to ``standard`` regardless
    # of any cached chat-only verdict — covers external-info queries,
    # memory/reminder/promise actions, and engineering tasks. The
    # pivot from "今天累死了" to "明天天气" used to slip past the cache
    # because chat→standard is a different profile shape.
    _TASK_INDICATORS: tuple[str, ...] = (
        # engineering / tools
        "跑", "执行", "运行", "shell", "命令", "代码",
        "git", "pytest", "deploy", "部署", "打开浏览器", "browse",
        # external info queries (now go via delegate_to_researcher)
        "天气", "气温", "比分", "赛况", "搜索", "搜一下", "查一下", "查查",
        "新闻", "股价", "汇率", "weather", "score", "price", "news",
        # memory / reminder / promise actions
        "提醒", "到时候", "记一下", "帮我记", "明天再", "晚点",
        "别忘了", "记住", "笔记",
    )

    def __init__(self, llm_router: Any, session_ttl_seconds: int | None = None) -> None:
        self._llm_router = llm_router
        self._ttl = session_ttl_seconds or INTENT_ROUTER_SESSION_TTL_SECONDS
        self._cache: dict[str, tuple[RouteDecision, datetime]] = {}

    async def route(self, chat_id: str, user_message: str) -> RouteDecision:
        cached = self._cache.get(chat_id)
        if cached is not None:
            decision, ts = cached
            if (now() - ts).total_seconds() < self._ttl:
                if (
                    self._is_obvious_task(user_message)
                    and decision.profile_name == "zero_tools"
                ):
                    pass  # fall through — clearly needs tools
                else:
                    return decision

        decision = await self._llm_classify(user_message)
        self._cache[chat_id] = (decision, now())
        logger.info(
            "[intent_router] chat=%s profile=%s msg=%r",
            chat_id,
            decision.profile_name,
            user_message[:60],
        )
        return decision

    def _is_obvious_task(self, msg: str) -> bool:
        lowered = msg.lower()
        return any(item in lowered for item in self._TASK_INDICATORS)

    async def _llm_classify(self, msg: str) -> RouteDecision:
        if self._is_obvious_task(msg):
            # Skip the LLM entirely for obvious task messages — saves a
            # lightweight_judgment call and avoids any classifier quirks.
            return RouteDecision(profile_name="standard")

        prompt = f"""判断这条消息是否需要工具辅助。

消息:{msg[:200]}

判断标准:
- chat:纯闲聊、情感互动、问候、改写、翻译、不需要查资料或记事的对话
- tools:需要做任何事(查信息、记笔记、设提醒、做承诺、委托调研、跑代码、用技能)

只输出一个词:chat / tools

判断不确定时,输出 tools。"""

        try:
            result = await self._llm_router.complete(
                [{"role": "user", "content": prompt}],
                purpose="lightweight_judgment",
                max_tokens=10,
            )
        except Exception as exc:
            logger.warning("[intent_router] LLM call failed: %s", exc)
            return RouteDecision(profile_name="standard")

        decision_token = str(result).strip().lower()
        if decision_token == "chat":
            return RouteDecision(profile_name="zero_tools")
        return RouteDecision(profile_name="standard")
