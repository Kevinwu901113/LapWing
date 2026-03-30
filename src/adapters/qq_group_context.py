"""QQ 群聊上下文缓冲区。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class GroupMessage:
    """群聊消息记录。"""

    message_id: str
    user_id: str
    nickname: str
    text: str
    timestamp: float
    is_at_self: bool = False
    is_reply_to_self: bool = False
    replied_by_self: bool = False


@dataclass
class GroupContext:
    """单个群的上下文状态。"""

    group_id: str
    buffer: deque[GroupMessage] = field(default_factory=lambda: deque(maxlen=50))
    last_reply_time: float = 0.0
    my_recent_message_ids: list[str] = field(default_factory=list)

    def add_message(self, msg: GroupMessage) -> None:
        self.buffer.append(msg)

    def recent_messages(self, n: int = 30) -> list[GroupMessage]:
        return list(self.buffer)[-n:]

    def format_for_prompt(self, n: int = 30) -> str:
        """Format recent group chat for Brain prompt."""
        messages = self.recent_messages(n)
        lines: list[str] = []
        for msg in messages:
            prefix = "(我回复过) " if msg.replied_by_self else ""
            lines.append(f"{msg.nickname}: {prefix}{msg.text}")
        return "\n".join(lines)

    def seconds_since_last_reply(self) -> float:
        if self.last_reply_time == 0:
            return float("inf")
        return time.time() - self.last_reply_time

    def record_my_message(self, message_id: str) -> None:
        """Track a message ID sent by Lapwing (for reply-to-self detection)."""
        self.my_recent_message_ids.append(message_id)
        if len(self.my_recent_message_ids) > 20:
            self.my_recent_message_ids = self.my_recent_message_ids[-20:]
