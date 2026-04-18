"""EventQueue — typed wrapper around asyncio.PriorityQueue.

Blueprint v2.0 Step 4 §M1.b. Two responsibilities the bare
``asyncio.PriorityQueue`` does not provide:

  * ``peek_priority`` — handlers running a long-lived LLM call
    periodically check whether a higher-priority event is waiting and
    yield voluntarily.
  * ``has_owner_message`` — fast specialisation for the common
    "should the inner tick abort itself?" check.

Both peeks reach into the underlying heap (``_queue``); we accept that
private-attr coupling because Python ships no public peek for
``asyncio.PriorityQueue``. The alternative — maintaining a parallel
counter — would let the two views drift.
"""

from __future__ import annotations

import asyncio

from src.core.events import PRIORITY_OWNER_MESSAGE, Event


class EventQueue:
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[Event] = asyncio.PriorityQueue()

    async def put(self, event: Event) -> None:
        await self._queue.put(event)

    async def get(self) -> Event:
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def peek_priority(self) -> int | None:
        """Return the priority of the head event without dequeuing it.

        ``None`` when the queue is empty. Reads the heap's first slot
        directly because PriorityQueue stores items at ``_queue._queue``
        (a heapq-managed list).
        """
        heap = self._queue._queue  # type: ignore[attr-defined]
        if not heap:
            return None
        head: Event = heap[0]
        return head.priority

    def has_owner_message(self) -> bool:
        """True when at least one queued event is OWNER-priority.

        Used by inner-tick handler to self-yield when Kevin sends a
        message during a tick. Scans the heap (cost: O(n)); n is bounded
        by adapter throughput in practice.
        """
        heap = self._queue._queue  # type: ignore[attr-defined]
        return any(ev.priority == PRIORITY_OWNER_MESSAGE for ev in heap)
