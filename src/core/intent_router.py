"""Runtime profile router for user conversations."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from config.settings import INTENT_ROUTER_SESSION_TTL_SECONDS
from src.core.time_utils import now

logger = logging.getLogger("lapwing.core.intent_router")


class IntentRouter:
    """Classify a user message into a runtime profile."""

    def __init__(self, llm_router: Any, session_ttl_seconds: int | None = None) -> None:
        self._llm_router = llm_router
        self._ttl = session_ttl_seconds or INTENT_ROUTER_SESSION_TTL_SECONDS
        self._cache: dict[str, tuple[str, datetime]] = {}

    async def route(self, chat_id: str, user_message: str) -> str:
        cached = self._cache.get(chat_id)
        if cached is not None:
            profile, ts = cached
            if (now() - ts).total_seconds() < self._ttl:
                if self._is_obvious_task(user_message) and profile != "task_execution":
                    pass
                else:
                    return profile

        profile = await self._llm_classify(user_message)
        self._cache[chat_id] = (profile, now())
        logger.info("[intent_router] chat=%s profile=%s msg=%r", chat_id, profile, user_message[:60])
        return profile

    def _is_obvious_task(self, msg: str) -> bool:
        task_indicators = [
            "跑", "执行", "运行", "shell", "命令", "代码",
            "git", "pytest", "deploy", "部署", "打开浏览器", "browse",
        ]
        lowered = msg.lower()
        return any(item in lowered for item in task_indicators)

    async def _llm_classify(self, msg: str) -> str:
        prompt = f"""判断这条消息属于哪类需求。

消息：{msg[:200]}

类别：
- chat: 日常聊天、情感互动、问候、闲谈
- chat_extended: 需要查信息（搜索/天气/体育/新闻）、需要记事或设提醒、需要承诺管理
- task: 明确的工程任务（写代码、操作文件、跑命令、浏览器自动化、委派子任务）

只输出一个词：chat / chat_extended / task

判断不确定时，输出 chat_extended。"""

        try:
            result = await self._llm_router.complete(
                [{"role": "user", "content": prompt}],
                purpose="lightweight_judgment",
                max_tokens=10,
            )
        except Exception as exc:
            logger.warning("[intent_router] LLM call failed: %s", exc)
            return "chat_extended"

        decision = str(result).strip().lower()
        if "task" in decision:
            return "task_execution"
        if "chat_extended" in decision or "extended" in decision:
            return "chat_extended"
        if decision == "chat":
            return "chat_minimal"
        logger.warning("[intent_router] unparseable decision=%r", decision)
        return "chat_extended"
