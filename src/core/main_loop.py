"""MainLoop — single runtime driver for Lapwing.

Blueprint v2.0 Step 4. Replaces the parallel "consciousness loop +
adapter-driven think_conversational" model with one consumer that
serves a single ``EventQueue``. Adapters, the inner-tick scheduler, and
system signals all enqueue ``Event`` instances; MainLoop dispatches
each to the correct handler.

This file ships the skeleton (M1.c). Handlers are stubbed with TODO
markers that point at the milestone that fills them in:

  * ``_handle_message``  → M2 (adapter migration)
  * ``_handle_inner_tick`` → M3 (consciousness migration)
  * ``_handle_system``   → M4+ (interrupt-driven control)

The OWNER-interrupt cancellation machinery hangs off ``_current_task``
and is implemented in M4. For now ``_dispatch`` only logs the event and
calls the stub handler so the loop can be unit-tested for lifecycle
behaviour without depending on brain wiring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.core.events import Event, InnerTickEvent, MessageEvent, SystemEvent

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from src.core.brain import LapwingBrain
    from src.core.event_queue import EventQueue

logger = logging.getLogger("lapwing.main_loop")


class MainLoop:
    """Single consumer for the EventQueue.

    Lifecycle:
      * ``await loop.run()`` blocks until ``stop()`` is called.
      * ``stop()`` flips ``_alive`` to False and cancels any in-flight
        handler so shutdown does not hang waiting on an LLM stream.

    The brain reference is optional in M1 so the skeleton can be
    constructed and exercised without wiring AppContainer.
    """

    def __init__(
        self,
        queue: "EventQueue",
        brain: "LapwingBrain | None" = None,
    ) -> None:
        self._queue = queue
        self._brain = brain
        self._alive = False
        self._current_task: asyncio.Task | None = None
        # M4 will read this to decide whether a cancellation was
        # pre-emptive (set True) versus a normal task completion.
        self._cancel_requested = False

    # ── Lifecycle ────────────────────────────────────────────────────

    async def run(self) -> None:
        """Consume the queue until ``stop()`` is called."""
        self._alive = True
        logger.info("MainLoop started")
        try:
            while self._alive:
                event = await self._queue.get()
                if not self._alive:
                    # stop() raced the queue.get; do not dispatch.
                    break
                await self._dispatch(event)
        finally:
            await self._cancel_in_flight("loop_shutdown")
            logger.info("MainLoop stopped")

    async def stop(self) -> None:
        """Signal the loop to exit and cancel any in-flight handler."""
        self._alive = False
        await self._cancel_in_flight("loop_shutdown")

    # ── Dispatch ─────────────────────────────────────────────────────

    async def _dispatch(self, event: Event) -> None:
        """Route ``event`` to the handler that matches its kind."""
        try:
            if isinstance(event, MessageEvent):
                await self._handle_message(event)
            elif isinstance(event, InnerTickEvent):
                await self._handle_inner_tick(event)
            elif isinstance(event, SystemEvent):
                await self._handle_system(event)
            else:
                logger.warning("Unknown event kind: %s", event.kind)
        except asyncio.CancelledError:
            # M4 will surface partial output here. M1 just logs.
            logger.info("Handler cancelled while dispatching %s", event.kind)
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Handler crashed for %s: %s", event.kind, exc)

    async def _cancel_in_flight(self, reason: str) -> None:
        """Cancel ``_current_task`` if one is running."""
        task = self._current_task
        if task is None or task.done():
            return
        self._cancel_requested = True
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            logger.info("In-flight handler cancelled (reason=%s)", reason)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Error awaiting cancelled task")
        finally:
            self._current_task = None
            self._cancel_requested = False

    # ── Handlers (stubs filled in later milestones) ──────────────────

    async def _handle_message(self, event: MessageEvent) -> None:
        """Drive ``brain.think_conversational`` for ``event``.

        The actual call is wrapped in ``self._current_task`` so M4's
        interrupt path can cancel it. ``done_future`` (when supplied by
        the producer) gets the assistant's full reply or the exception
        the brain raised — this is how the desktop ``/ws/chat`` route
        keeps its synchronous "send reply, then close turn" semantic.
        """
        if self._brain is None:
            logger.warning("MessageEvent received but no brain wired")
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result("")
            return

        async def _drive() -> str:
            return await self._brain.think_conversational(
                chat_id=event.chat_id,
                user_message=event.text,
                send_fn=event.send_fn,
                typing_fn=event.typing_fn,
                status_callback=event.status_callback,
                adapter=event.adapter,
                user_id=event.user_id,
                images=list(event.images) if event.images else None,
            )

        task = asyncio.create_task(_drive(), name=f"think_conv:{event.chat_id}")
        self._current_task = task
        try:
            reply = await task
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_result(reply)
        except asyncio.CancelledError:
            # M4 surfaces partial output here; for now propagate the
            # cancellation to the producer so it can clean up.
            if event.done_future is not None and not event.done_future.done():
                event.done_future.cancel()
            raise
        except Exception as exc:
            logger.exception("think_conversational failed for %s", event.chat_id)
            if event.done_future is not None and not event.done_future.done():
                event.done_future.set_exception(exc)
        finally:
            if self._current_task is task:
                self._current_task = None

    async def _handle_inner_tick(self, event: InnerTickEvent) -> None:
        # M3 fills this in: yield to OWNER messages first, then call
        # brain.think_inner().
        logger.debug("InnerTickEvent stub: reason=%s", event.reason)

    async def _handle_system(self, event: SystemEvent) -> None:
        # Right now only "shutdown" is meaningful — it triggers stop()
        # so the runtime can be torn down via an in-band event.
        logger.debug("SystemEvent stub: action=%s", event.action)
        if event.action == "shutdown":
            await self.stop()
