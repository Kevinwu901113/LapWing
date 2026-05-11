"""ContinuationRegistry — in-memory async-future registry for interrupt suspends.

See docs/architecture/lapwing_v1_blueprint.md §8.3-§8.4.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal


class InterruptCancelled(Exception):
    """Raised on a continuation when its Interrupt is denied/expired/cancelled."""


class ContinuationRegistry:
    """In-memory registry of suspended agent tasks awaiting interrupt resolution.

    continuation_ref → asyncio.Future

    Lifecycle invariant (blueprint §8.4): every `register()` must be paired
    with `cleanup(ref)`. The four terminal Interrupt transitions (resolved /
    denied / expired / cancelled) all trigger cleanup in the worker's
    `finally` block.

    Process-local. Kernel restart with pending interrupts = continuations LOST.
    The persisted Interrupt remains but no worker is awaiting it.
    ActionExecutor.resume() MUST call `has(ref)` BEFORE persisting interrupt
    as 'resolved' (blueprint §4.3, §15.2 I-6).
    """

    _instance: "ContinuationRegistry | None" = None

    @classmethod
    def instance(cls) -> "ContinuationRegistry":
        if cls._instance is None:
            cls._instance = ContinuationRegistry()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Drop the singleton — only safe in test setup."""
        cls._instance = None

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future] = {}
        self._task_refs: dict[str, str | None] = {}

    def register(self, task_ref: str | None = None) -> str:
        ref = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        self._futures[ref] = loop.create_future()
        self._task_refs[ref] = task_ref
        return ref

    def has(self, ref: str) -> bool:
        return ref in self._futures and not self._futures[ref].done()

    def get_status(
        self, ref: str
    ) -> Literal["active", "missing", "done", "cancelled"]:
        if ref not in self._futures:
            return "missing"
        f = self._futures[ref]
        if not f.done():
            return "active"
        if f.cancelled() or (f.exception() is not None):
            return "cancelled"
        return "done"

    async def wait_for_resume(self, ref: str) -> dict[str, Any]:
        if ref not in self._futures:
            raise KeyError(ref)
        return await self._futures[ref]

    def resume(self, ref: str, payload: dict[str, Any]) -> None:
        """Release the suspended awaiter.

        Caller must have verified `has(ref)` at the calling site. If a race
        causes the future to be missing/done here, silently noop — the caller
        already committed to the operation; downstream consistency is theirs.
        """
        if ref not in self._futures:
            return
        future = self._futures[ref]
        if not future.done():
            future.set_result(payload)

    def cancel(self, ref: str, reason: str = "cancelled") -> None:
        if ref in self._futures and not self._futures[ref].done():
            self._futures[ref].set_exception(InterruptCancelled(reason))

    def cleanup(self, ref: str) -> None:
        """Release the in-memory future + task_ref entry.

        MUST be called after one of the terminal transitions
        (resolved / denied / expired / cancelled). Not calling cleanup() =
        memory leak per pending interrupt over process lifetime.
        See §8.4 for the lifecycle invariant.
        """
        self._futures.pop(ref, None)
        self._task_refs.pop(ref, None)
