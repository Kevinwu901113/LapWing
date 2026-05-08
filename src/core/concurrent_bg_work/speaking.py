from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone


class SpeakingPolicyDenied(RuntimeError):
    pass


class SpeakingArbiter:
    def __init__(self, *, chat_activity_tracker=None) -> None:
        self._chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_speak_at: dict[str, datetime] = {}
        self._chat_activity_tracker = chat_activity_tracker

    @asynccontextmanager
    async def acquire(
        self,
        chat_id: str,
        *,
        purpose: str = "user_reply",
        chat_activity_tracker=None,
    ):
        allowed, reason = self.can_acquire(
            chat_id,
            purpose=purpose,
            chat_activity_tracker=chat_activity_tracker,
        )
        if not allowed:
            raise SpeakingPolicyDenied(reason)
        lock = self._chat_locks[chat_id]
        async with lock:
            try:
                yield
            finally:
                self._last_speak_at[chat_id] = datetime.now(timezone.utc)

    def can_acquire(
        self,
        chat_id: str,
        *,
        purpose: str = "user_reply",
        chat_activity_tracker=None,
    ) -> tuple[bool, str]:
        if purpose in {"user_reply", "direct_reply"}:
            return True, "user_reply_priority"

        tracker = chat_activity_tracker or self._chat_activity_tracker
        if tracker is None:
            return True, "no_activity_tracker"

        snapshot = tracker.snapshot(chat_id)
        if snapshot.has_unanswered_user_message:
            return False, "unanswered_user_message"
        if snapshot.active_turn is not None:
            return False, "active_user_turn"
        if purpose == "heartbeat_diagnostic":
            return False, "heartbeat_diagnostic_not_user_reply"
        return True, "within_policy"

    def last_speak_at(self, chat_id: str) -> datetime | None:
        return self._last_speak_at.get(chat_id)
