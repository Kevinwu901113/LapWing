"""轻量级群消息过滤器 — 不走 LLM，纯规则判断。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.qq_group_context import GroupContext, GroupMessage


class GroupMessageFilter:
    """Tier-1 filter: pure rules, zero LLM cost."""

    def __init__(
        self,
        self_id: str,
        self_names: list[str],
        kevin_id: str,
        interest_keywords: list[str],
        cooldown_seconds: int = 60,
    ) -> None:
        self.self_id = self_id
        self.self_names = [n.lower() for n in self_names]
        self.kevin_id = kevin_id
        self.interest_keywords = [k.lower() for k in interest_keywords]
        self.cooldown_seconds = cooldown_seconds

    def should_engage(self, msg: GroupMessage, ctx: GroupContext) -> tuple[bool, str]:
        """
        Decide whether this group message warrants Brain evaluation.
        Returns (should_pass, reason).
        """
        if msg.user_id == self.self_id:
            return False, "self"

        if msg.is_at_self:
            return True, "at_self"

        if msg.is_reply_to_self:
            return True, "reply_to_self"

        text_lower = msg.text.lower()
        for name in self.self_names:
            if name in text_lower:
                return True, "name_mention"

        if msg.user_id == self.kevin_id:
            return True, "kevin_speaking"

        for keyword in self.interest_keywords:
            if keyword in text_lower:
                if ctx.seconds_since_last_reply() < self.cooldown_seconds:
                    return False, "keyword_cooldown"
                return True, f"keyword:{keyword}"

        recent = ctx.recent_messages(15)
        if len(recent) >= 10:
            time_span = msg.timestamp - recent[-10].timestamp
            if time_span < 120 and ctx.seconds_since_last_reply() > self.cooldown_seconds * 3:
                return True, "active_chat"

        return False, "no_match"
