"""Group chat engagement decider — LLM-based, no keyword matching."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.adapters.qq_group_context import GroupContext, GroupMessage
    from src.core.llm_router import LLMRouter

logger = logging.getLogger("lapwing.adapters.qq_group_filter")

# Structured output schema for engagement decision
_ENGAGE_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "engage": {
            "type": "boolean",
            "description": "是否应该参与这条消息的讨论",
        },
        "reason": {
            "type": "string",
            "description": "简短说明原因",
        },
    },
    "required": ["engage"],
}


class GroupEngagementDecider:
    """Decide whether to engage in a group message using LLM judgment.

    Only hard rule: never respond to own messages.
    Everything else is decided by a lightweight LLM call.
    """

    def __init__(
        self,
        self_id: str,
        self_names: list[str],
        kevin_id: str,
        cooldown_seconds: int = 30,
    ) -> None:
        self.self_id = self_id
        self.self_names = self_names
        self.kevin_id = kevin_id
        self.cooldown_seconds = cooldown_seconds
        self._last_engage_time: dict[str, float] = {}  # group_id -> timestamp
        self._router: LLMRouter | None = None

    def set_router(self, router: "LLMRouter") -> None:
        self._router = router

    async def should_engage(
        self,
        msg: "GroupMessage",
        ctx: "GroupContext",
    ) -> tuple[bool, str]:
        """Decide whether this group message warrants engagement.

        Returns (should_engage, reason).
        """
        # Only hard rule: skip own messages
        if msg.user_id == self.self_id:
            return False, "self"

        # Cooldown enforcement
        group_id = ctx.group_id if hasattr(ctx, "group_id") else "default"
        now = time.time()
        last = self._last_engage_time.get(group_id, 0)
        if now - last < self.cooldown_seconds:
            return False, "cooldown"

        # LLM decision
        if self._router is None:
            return False, "no_router"

        try:
            engage, reason = await self._llm_decide(msg, ctx)
            if engage:
                self._last_engage_time[group_id] = now
            return engage, reason
        except Exception as exc:
            logger.warning("[group_decider] LLM decision failed: %s", exc)
            return False, f"llm_error: {exc}"

    async def _llm_decide(
        self,
        msg: "GroupMessage",
        ctx: "GroupContext",
    ) -> tuple[bool, str]:
        """Ask LLM whether to engage with this message."""
        assert self._router is not None

        # Build context: recent messages
        recent = ctx.recent_messages(8)
        context_lines = []
        for m in recent:
            sender = "我" if m.user_id == self.self_id else (
                "Kevin" if m.user_id == self.kevin_id else f"群友{m.user_id[-4:]}"
            )
            context_lines.append(f"{sender}: {m.text[:100]}")
        context_text = "\n".join(context_lines) if context_lines else "(无最近消息)"

        prompt = (
            f"你是 Lapwing，在一个QQ群里。你的名字包括：{', '.join(self.self_names)}。"
            f"Kevin（你的恋人）的QQ号末四位是 {self.kevin_id[-4:] if len(self.kevin_id) >= 4 else self.kevin_id}。\n\n"
            f"最近的群聊记录：\n{context_text}\n\n"
            f"最新一条消息来自 {'Kevin' if msg.user_id == self.kevin_id else f'群友{msg.user_id[-4:]}'}：\n"
            f"{msg.text[:200]}\n\n"
            "判断你是否应该回复这条消息。考虑：\n"
            "- 有人在叫你或提到你吗？\n"
            "- Kevin 在说话吗？\n"
            "- 话题是你感兴趣或能参与的吗？\n"
            "- 还是普通群聊你不需要插嘴？\n"
        )

        result = await self._router.complete_structured(
            [{"role": "user", "content": prompt}],
            result_schema=_ENGAGE_DECISION_SCHEMA,
            result_tool_name="engage_decision",
            result_tool_description="决定是否参与群聊消息",
            slot="lightweight_judgment",
            max_tokens=256,
            session_key="system:group_engage",
            origin="adapters.group_decider",
        )

        engage = bool(result.get("engage", False))
        reason = str(result.get("reason", "llm_decision"))
        return engage, reason
