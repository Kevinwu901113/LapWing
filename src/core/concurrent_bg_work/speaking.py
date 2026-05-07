from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone


class SpeakingArbiter:
    def __init__(self) -> None:
        self._chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_speak_at: dict[str, datetime] = {}

    @asynccontextmanager
    async def acquire(self, chat_id: str):
        lock = self._chat_locks[chat_id]
        async with lock:
            try:
                yield
            finally:
                self._last_speak_at[chat_id] = datetime.now(timezone.utc)

    def last_speak_at(self, chat_id: str) -> datetime | None:
        return self._last_speak_at.get(chat_id)
